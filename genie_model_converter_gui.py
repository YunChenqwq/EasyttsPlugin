"""
Genie-TTS / GENIE 模型转换 GUI

用途：
  - 把 GPT-SoVITS 的 .pth + .ckpt 转换为 GENIE/Genie-TTS 所需的 ONNX 模型目录。

实现依据：
  - High-Logic/Genie-TTS 项目文档中的转换接口：genie_tts.convert_to_onnx(...)

说明：
  - 这是“转换器 GUI 外壳”，核心转换逻辑来自 genie-tts 包本身。
  - 建议在你运行 MaiBot / EasyTTS 的同一个 Python 环境中运行本脚本，避免依赖不一致。
"""

from __future__ import annotations

import os
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


# 这些文件名来自 Genie-TTS 报错信息/模型规范：
# - v2 Base (all models)
# - v2ProPlus additions (v2pp models)
REQUIRED_V2_BASE = [
    "t2s_encoder_fp32.bin",
    "t2s_encoder_fp32.onnx",
    "t2s_first_stage_decoder_fp32.onnx",
    "t2s_shared_fp16.bin",
    "t2s_stage_decoder_fp32.onnx",
    "vits_fp16.bin",
    "vits_fp32.onnx",
]

OPTIONAL_V2PP = [
    "prompt_encoder_fp16.bin",
    "prompt_encoder_fp32.onnx",
]


@dataclass
class ConvertArgs:
    pth_path: str
    ckpt_path: str
    output_dir: str


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("GENIE / Genie-TTS 模型转换器 (GUI)")
        self.geometry("900x560")
        self.minsize(860, 520)

        self._log_q: "queue.Queue[str]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

        self._pth_var = tk.StringVar()
        self._ckpt_var = tk.StringVar()
        self._out_var = tk.StringVar()

        self._build_ui()
        self.after(80, self._drain_logs)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, **pad)

        # Inputs
        row = 0
        ttk.Label(frm, text=".pth (SoVITS) 模型文件：").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self._pth_var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="选择...", command=self._pick_pth).grid(row=row, column=2, sticky="ew", **pad)

        row += 1
        ttk.Label(frm, text=".ckpt (GPT) 权重文件：").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self._ckpt_var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="选择...", command=self._pick_ckpt).grid(row=row, column=2, sticky="ew", **pad)

        row += 1
        ttk.Label(frm, text="输出目录（ONNX 模型文件夹）：").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self._out_var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="选择...", command=self._pick_outdir).grid(row=row, column=2, sticky="ew", **pad)

        row += 1
        btnbar = ttk.Frame(frm)
        btnbar.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        btnbar.columnconfigure(0, weight=1)

        self._run_btn = ttk.Button(btnbar, text="开始转换", command=self._on_run)
        self._run_btn.pack(side="left")
        self._validate_btn = ttk.Button(btnbar, text="校验输出目录", command=self._on_validate)
        self._validate_btn.pack(side="left", padx=(10, 0))
        ttk.Button(btnbar, text="清空日志", command=self._clear_log).pack(side="left", padx=(10, 0))

        # Tips
        row += 1
        tips = (
            "依赖说明：\n"
            "  - 需要安装 genie-tts 与 torch\n"
            "  - 建议：pip install genie-tts torch\n\n"
            "输出校验：\n"
            "  - 转换完成后，脚本会检查输出目录是否包含 GENIE 所需的 base 文件；\n"
            "  - 如果你是 v2ProPlus，还会检查 prompt_encoder_* 两个附加文件。"
        )
        ttk.Label(frm, text=tips, justify="left").grid(row=row, column=0, columnspan=3, sticky="w", **pad)

        # Log
        row += 1
        ttk.Label(frm, text="日志：").grid(row=row, column=0, sticky="w", **pad)

        row += 1
        self._log_text = tk.Text(frm, height=16, wrap="word")
        self._log_text.grid(row=row, column=0, columnspan=3, sticky="nsew", **pad)
        self._log_text.configure(state="disabled")

        scr = ttk.Scrollbar(frm, command=self._log_text.yview)
        scr.grid(row=row, column=3, sticky="ns", pady=6)
        self._log_text["yscrollcommand"] = scr.set

        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(row, weight=1)

    def _pick_pth(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 .pth 文件",
            filetypes=[("PyTorch .pth", "*.pth"), ("All files", "*.*")],
        )
        if path:
            self._pth_var.set(path)

    def _pick_ckpt(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 .ckpt 文件",
            filetypes=[("Checkpoint .ckpt", "*.ckpt"), ("All files", "*.*")],
        )
        if path:
            self._ckpt_var.set(path)

    def _pick_outdir(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录（会写入多种 .onnx/.bin 文件）")
        if path:
            self._out_var.set(path)

    def _clear_log(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log_q.put(f"[{ts}] {msg}")

    def _drain_logs(self) -> None:
        try:
            lines = []
            while True:
                lines.append(self._log_q.get_nowait())
        except queue.Empty:
            pass

        if lines:
            self._log_text.configure(state="normal")
            for line in lines:
                self._log_text.insert("end", line + "\n")
            self._log_text.see("end")
            self._log_text.configure(state="disabled")

        self.after(80, self._drain_logs)

    def _get_args(self) -> Optional[ConvertArgs]:
        pth = self._pth_var.get().strip().strip('"')
        ckpt = self._ckpt_var.get().strip().strip('"')
        out_dir = self._out_var.get().strip().strip('"')

        if not pth or not Path(pth).is_file():
            messagebox.showerror("参数错误", "请选择有效的 .pth 文件。")
            return None
        if not ckpt or not Path(ckpt).is_file():
            messagebox.showerror("参数错误", "请选择有效的 .ckpt 文件。")
            return None
        if not out_dir:
            messagebox.showerror("参数错误", "请选择输出目录。")
            return None
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return ConvertArgs(pth_path=pth, ckpt_path=ckpt, output_dir=out_dir)

    def _on_validate(self) -> None:
        out_dir = self._out_var.get().strip().strip('"')
        if not out_dir:
            messagebox.showerror("参数错误", "请先选择输出目录。")
            return
        ok, report = validate_output_dir(out_dir)
        self._log(report)
        if ok:
            messagebox.showinfo("校验通过", report)
        else:
            messagebox.showwarning("校验未通过", report)

    def _on_run(self) -> None:
        args = self._get_args()
        if not args:
            return
        if self._worker and self._worker.is_alive():
            messagebox.showwarning("正在运行", "当前已有转换任务在运行。")
            return

        self._run_btn.configure(state="disabled")
        self._validate_btn.configure(state="disabled")
        self._stop_flag.clear()

        self._worker = threading.Thread(target=self._run_worker, args=(args,), daemon=True)
        self._worker.start()

    def _run_worker(self, args: ConvertArgs) -> None:
        try:
            self._log("开始转换...")
            self._log(f"pth:  {args.pth_path}")
            self._log(f"ckpt: {args.ckpt_path}")
            self._log(f"out:  {args.output_dir}")

            # 确保依赖就绪
            try:
                import genie_tts as genie  # type: ignore
            except Exception as e:
                self._log("导入 genie_tts 失败。你可能还没安装依赖：pip install genie-tts torch")
                self._log(f"ImportError: {e}")
                return

            # 执行转换（核心逻辑由 genie-tts 提供）
            try:
                genie.convert_to_onnx(
                    torch_pth_path=os.path.abspath(args.pth_path),
                    torch_ckpt_path=os.path.abspath(args.ckpt_path),
                    output_dir=os.path.abspath(args.output_dir),
                )
            except Exception as e:
                self._log("转换失败（genie.convert_to_onnx 抛异常）：")
                self._log(str(e))
                self._log(traceback.format_exc())
                return

            self._log("转换完成。开始校验输出目录...")
            ok, report = validate_output_dir(args.output_dir)
            self._log(report)
            if ok:
                self._log("✅ 校验通过。你可以把输出目录作为模型包上传/使用。")
            else:
                self._log("⚠️ 校验未通过：输出目录缺少文件。请检查是否选择了正确的 .pth/.ckpt（需 V2/V2ProPlus）。")
        finally:
            self.after(0, lambda: self._run_btn.configure(state="normal"))
            self.after(0, lambda: self._validate_btn.configure(state="normal"))


def validate_output_dir(out_dir: str) -> tuple[bool, str]:
    p = Path(out_dir)
    if not p.exists():
        return False, f"输出目录不存在：{out_dir}"
    if not p.is_dir():
        return False, f"输出路径不是目录：{out_dir}"

    missing_base = [f for f in REQUIRED_V2_BASE if not (p / f).exists()]
    missing_v2pp = [f for f in OPTIONAL_V2PP if not (p / f).exists()]

    lines = [f"输出目录：{p}"]
    if not missing_base:
        lines.append("Base 文件：✅ 完整")
    else:
        lines.append("Base 文件：❌ 缺失")
        lines.extend([f"  - {x}" for x in missing_base])

    # v2pp 不强制（因为有些模型是 v2 base），但给出提示
    if not missing_v2pp:
        lines.append("v2ProPlus 附加文件：✅ 检测到（看起来是 v2ProPlus）")
    else:
        lines.append("v2ProPlus 附加文件：未检测到（如果你的模型是 v2ProPlus，需要这两项）")
        lines.extend([f"  - {x}" for x in missing_v2pp])

    ok = not missing_base
    return ok, "\n".join(lines)


def main() -> None:
    # Windows 下 Tk 默认字体可能较小，略微放大一点
    app = App()
    try:
        app.tk.call("tk", "scaling", 1.15)
    except Exception:
        pass
    app.mainloop()


if __name__ == "__main__":
    main()

