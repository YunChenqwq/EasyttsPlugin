"""
统一 TTS 语音合成插件（EasyttsPugin）

基于 GPT-SoVITS 推理特化库（Genie-TTS / GPT-SoVITS ONNX 推理引擎）+ 魔搭社区（ModelScope Studio）托管的语音合成插件：
- Action：关键词触发，生成/润色回复文本后转语音发送
- Command：/eztts 手动命令转语音
- 分段发送 / SPLIT 标记 / 失败降级为文字
- 支持按预设（preset）生成语音（emotion 参数直接等同 preset，不做任何映射）
"""

import sys
sys.dont_write_bytecode = True

import asyncio
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import List, Tuple, Type

from src.common.logger import get_logger
from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.apis.plugin_register_api import register_plugin
from src.plugin_system.base.base_action import BaseAction, ActionActivationType
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import ComponentInfo, ChatMode
from src.plugin_system.base.config_types import ConfigField, ConfigSection
from src.plugin_system.apis import generator_api

from .backends import TTSBackendRegistry, TTSResult
from .config_keys import ConfigKeys, get_config_with_aliases
from .utils.text import TTSTextUtils

logger = get_logger("EasyttsPugin")

VALID_BACKENDS = ["easytts"]


class TTSExecutorMixin:
    def _cfg(self, key: str, default=None):
        return get_config_with_aliases(self.get_config, key, default)

    def _characters_from_slots(self) -> List[dict]:
        """
        从可视化字段（固定 5 个槽位）构造角色列表。
        这是为了让 MaiBot WebUI 能“可视化编辑”，避免 list[object] 显示为 [object Object]。
        """
        easytts_cfg = self.config.get("easytts") if isinstance(self.config, dict) else None
        if not isinstance(easytts_cfg, dict):
            return []

        out: List[dict] = []
        for i in range(1, 6):
            name = str(easytts_cfg.get(f"character_{i}_name", "") or "").strip()
            raw_presets = easytts_cfg.get(f"character_{i}_presets", [])
            presets: List[str] = []
            if isinstance(raw_presets, list):
                presets = [str(x).strip() for x in raw_presets if str(x).strip()]
            elif isinstance(raw_presets, str):
                # 支持用户在 WebUI 里用“逗号/换行”填写：普通,开心,伤心
                s = raw_presets.replace("，", ",").replace("；", ",")
                parts: List[str] = []
                for line in s.splitlines():
                    parts.extend(line.split(","))
                presets = [p.strip() for p in parts if p.strip()]
            if name:
                out.append({"name": name, "presets": presets})
        return out

    def _endpoints_from_slots(self) -> List[dict]:
        """
        从可视化字段（固定 5 个槽位）构造云端仓库池 endpoints。
        仅返回 base_url 与 studio_token 都填了的仓库（否则后端校验会失败）。
        """
        easytts_cfg = self.config.get("easytts") if isinstance(self.config, dict) else None
        if not isinstance(easytts_cfg, dict):
            return []

        out: List[dict] = []
        for i in range(1, 6):
            name = str(easytts_cfg.get(f"endpoint_{i}_name", f"pool-{i}") or f"pool-{i}").strip()
            base_url = str(easytts_cfg.get(f"endpoint_{i}_base_url", "") or "").strip().rstrip("/")
            token = str(easytts_cfg.get(f"endpoint_{i}_studio_token", "") or "").strip()
            fn_index = int(easytts_cfg.get(f"endpoint_{i}_fn_index", 3) or 3)
            trigger_id = int(easytts_cfg.get(f"endpoint_{i}_trigger_id", 19) or 19)

            if not base_url or not token:
                continue
            out.append(
                {
                    "name": name,
                    "base_url": base_url,
                    "studio_token": token,
                    "fn_index": fn_index,
                    "trigger_id": trigger_id,
                }
            )
        return out

    def _sync_visual_fields(self) -> None:
        """
        让 WebUI 的“可视化字段”与旧版的 list 配置互通：
        - 若用户旧配置仍是 easytts.characters / easytts.endpoints，则把前 5 项回填到槽位字段里，便于在 WebUI 编辑；
        - 若用户只填了槽位字段，则在内存里生成 easytts.characters / easytts.endpoints 供后端读取。
        注意：这里只改内存 self.config，不直接写文件。
        """
        if not isinstance(self.config, dict):
            return
        easytts_cfg = self.config.setdefault("easytts", {})
        if not isinstance(easytts_cfg, dict):
            return

        # 1) slots -> arrays（供后端/逻辑使用）
        chars = easytts_cfg.get("characters")
        if not (isinstance(chars, list) and chars):
            derived = self._characters_from_slots()
            if derived:
                easytts_cfg["characters"] = derived

        eps = easytts_cfg.get("endpoints")
        if not (isinstance(eps, list) and eps):
            derived = self._endpoints_from_slots()
            if derived:
                easytts_cfg["endpoints"] = derived

        # 2) arrays -> slots（供 WebUI 可视化编辑）
        chars = easytts_cfg.get("characters")
        if isinstance(chars, list) and chars:
            idx = 0
            for item in chars:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", item.get("角色名", "")) or "").strip()
                presets_raw = item.get("presets", item.get("预设列表", []))
                presets = [str(x).strip() for x in presets_raw if str(x).strip()] if isinstance(presets_raw, list) else []
                if not name:
                    continue
                idx += 1
                if idx > 5:
                    break
                easytts_cfg.setdefault(f"character_{idx}_name", name)
                if f"character_{idx}_presets" not in easytts_cfg:
                    # 槽位字段在 WebUI 中用字符串展示，避免 list 输入导致用户看到 [object Object]
                    easytts_cfg[f"character_{idx}_presets"] = ",".join(presets) if presets else "普通"

        eps = easytts_cfg.get("endpoints")
        if isinstance(eps, list) and eps:
            idx = 0
            for item in eps:
                if not isinstance(item, dict):
                    continue
                base_url = str(item.get("base_url", item.get("基地址", "")) or "").strip().rstrip("/")
                token = str(item.get("studio_token", item.get("令牌", "")) or "").strip()
                if not base_url or not token:
                    continue
                idx += 1
                if idx > 5:
                    break
                easytts_cfg.setdefault(f"endpoint_{idx}_name", str(item.get("name", item.get("名称", f"pool-{idx}")) or f"pool-{idx}").strip())
                easytts_cfg.setdefault(f"endpoint_{idx}_base_url", base_url)
                easytts_cfg.setdefault(f"endpoint_{idx}_studio_token", token)
                easytts_cfg.setdefault(f"endpoint_{idx}_fn_index", int(item.get("fn_index", item.get("函数索引", 3)) or 3))
                easytts_cfg.setdefault(f"endpoint_{idx}_trigger_id", int(item.get("trigger_id", item.get("触发ID", 19)) or 19))

    def _create_backend(self, backend_name: str):
        # 确保后端总能读到 endpoints/characters（无论用户是在 WebUI 槽位编辑，还是旧版 list 配置）。
        self._sync_visual_fields()
        backend = TTSBackendRegistry.create(
            backend_name,
            lambda k, d=None: get_config_with_aliases(self.get_config, k, d),
            self.log_prefix,
        )
        if backend and hasattr(backend, "set_send_custom"):
            backend.set_send_custom(self.send_custom)
        return backend

    async def _execute_backend(self, backend_name: str, text: str, voice: str = "", emotion: str = "") -> TTSResult:
        backend = self._create_backend(backend_name)
        if not backend:
            return TTSResult(success=False, message=f"未知的 TTS 后端: {backend_name}")
        return await backend.execute(text, voice, emotion=emotion)

    def _get_default_backend(self) -> str:
        backend = self._cfg(ConfigKeys.GENERAL_DEFAULT_BACKEND, "easytts")
        if backend not in VALID_BACKENDS:
            return "easytts"
        return backend

    async def _send_error(self, message: str) -> None:
        if self._cfg(ConfigKeys.GENERAL_SEND_ERROR_MESSAGES, True):
            await self.send_text(message)


class UnifiedTTSAction(BaseAction, TTSExecutorMixin):
    """LLM 规划触发（LLM_JUDGE）"""

    action_name = "unified_tts_action"
    action_description = "发送一条文字回复，并附带一条语音（语音内容默认为该文字的日语版本）"
    # 让 Planner 把该动作交给 LLM 自由判断是否启用（无需用户显式说“语音/tts”）
    activation_type = ActionActivationType.LLM_JUDGE
    mode_enable = ChatMode.ALL
    parallel_action = False

    activation_keywords = [
        "语音", "说话", "朗读", "念一个", "读出来",
        "voice", "speak", "tts", "语音回复", "用语音说", "播报",
    ]
    keyword_case_sensitive = False

    action_parameters = {
        "text": (
            "你要发送给用户的最终文字回复（必填，建议 1~2 句，必须 <= max_text_length）。\n"
            "注意：插件会用这段文字生成语音（默认会翻译成日语后再合成），所以不要再额外选择其他文本回复 action，避免“文本/语音不匹配”。"
        ),
        "voice": (
            "可选：角色/预设。\n"
            "1) 只写角色：例如 `sagiri` / `mika`（推荐：允许按 emotion 自动选择预设）\n"
            "2) 写角色:预设：例如 `mika:普通`（显式指定预设后，将不会再按 emotion 自动切换）"
        ),
        "backend": "可选：TTS 后端（仅支持 easytts，可省略）",
        "emotion": "可选：预设名（preset）。必须是该角色在云端 WebUI 下拉中真实存在的 preset 值；不会做任何映射。",
    }

    action_require = [
        "由 LLM 自由决定是否用语音回复（LLM_JUDGE）。不要因为能用就滥用：仅在更适合语音表达时使用。",
        "严格限制：对同一条用户消息，最多选择 1 次 unified_tts_action（一个消息一个语音）。",
        "若希望指定某个预设：填写 emotion（=preset）参数，并且 voice 只填角色名（不要写 `角色:预设`）。",
        "内容必须短（建议 1~2 句），否则会被截断/降级为文字。",
    ]

    associated_types = ["text", "command"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timeout = int(self._cfg(ConfigKeys.GENERAL_TIMEOUT, 60) or 60)
        self.max_text_length = int(self._cfg(ConfigKeys.GENERAL_MAX_TEXT_LENGTH, 200) or 200)

    async def _get_final_text(self, raw_text: str, reason: str, use_replyer: bool) -> Tuple[bool, str]:
        """
        获取最终要转语音的文本。

        默认：完全相信 Planner/LLM 在选择该 action 时提供的 text（不再二次调用 LLM 生成语音文案），
        这样“是否用语音/说什么/用什么情绪”都由 LLM 统一控制，避免出现“发空语音/重复语音/过长语音”。
        """
        if raw_text:
            return True, raw_text
        if not use_replyer:
            return False, ""

        # 兜底：如果上层没给 text，才允许用 replyer 生成（不推荐）。
        max_text_length = int(self._cfg(ConfigKeys.GENERAL_MAX_TEXT_LENGTH, 200) or 200)
        try:
            success, llm_response = await generator_api.generate_reply(
                chat_stream=self.chat_stream,
                reply_message=self.action_message,
                reply_reason=reason or "生成一条简短语音回复",
                extra_info=f"【重要】回复必须控制在{max_text_length}字以内，只输出最终回复文本。",
                request_type="easytts_pugin",
                from_plugin=False,
            )
            if success and llm_response and getattr(llm_response, "content", None):
                return True, llm_response.content.strip()
            return False, ""
        except Exception as e:
            logger.error(f"{self.log_prefix} 调用 replyer 出错: {e}")
            return False, ""

    @staticmethod
    def _strip_llm_wrappers(text: str) -> str:
        """尽量把 LLM 的多余包裹去掉（例如 ```、引号、前缀“翻译：”）。"""
        if not text:
            return ""
        t = text.strip()
        # Code fences
        if "```" in t:
            parts = [p.strip() for p in t.split("```") if p.strip()]
            if parts:
                # 常见结构：```xxx\n内容\n``` -> parts[0] 可能带语言标识
                if len(parts) >= 2 and "\n" in parts[1]:
                    t = parts[1].strip()
                else:
                    t = parts[-1].strip()
        # 去掉常见前缀
        for prefix in ("翻译：", "译文：", "日语：", "日本語：", "Japanese:", "JP:"):
            if t.startswith(prefix):
                t = t[len(prefix) :].strip()
        # 去掉成对引号
        if (t.startswith('"') and t.endswith('"')) or (t.startswith("“") and t.endswith("”")):
            t = t[1:-1].strip()
        return t

    async def _voice_text_from_text(self, text: str) -> str:
        """
        将“要发送的文本”转换成“要合成语音的文本”。
        默认：若文本非日语，则用 LLM 翻译成日语；若已是日语则直接使用，保证文本/语音一致。
        """
        target = str(self._cfg("general.voice_translate_to", "ja") or "").strip().lower()
        if not target or target in ("none", "off", "false", "0", "disable", "disabled"):
            return text

        # 仅对“目标为日语”做语言检测，避免把已经是日语的内容又改写一遍导致不一致。
        if target in ("ja", "jp", "japanese") and TTSTextUtils.detect_language(text) == "ja":
            return text

        try:
            ok, llm_response = await generator_api.rewrite_reply(
                chat_stream=self.chat_stream,
                raw_reply=text,
                reason=(
                    "请把【原文】翻译成自然的日语。\n"
                    "严格要求：\n"
                    "1) 只输出日语译文，不要解释，不要前缀（例如“翻译：”），不要引号，不要代码块；\n"
                    "2) 不要新增信息，不要改变语气；\n"
                    "3) 尽量简短（<=60日文字符左右），多用“。？！……”分句。"
                ),
                enable_splitter=False,
                enable_chinese_typo=False,
                request_type="easytts_translate_to_ja",
            )
            if ok and llm_response and getattr(llm_response, "content", None):
                jp = self._strip_llm_wrappers(llm_response.content)
                jp = TTSTextUtils.clean_text(jp, self.max_text_length)
                return jp or text
        except Exception as e:
            logger.error(f"{self.log_prefix} 翻译日语失败，回退使用原文: {e}")
        return text

    async def _translate_to_zh(self, text: str) -> str:
        """把日语/英文等翻译成简体中文（仅用于“发出去的文字”，避免把日语译文直接发出去）。"""
        if not text:
            return ""
        try:
            ok, llm_response = await generator_api.rewrite_reply(
                chat_stream=self.chat_stream,
                raw_reply=text,
                reason=(
                    "请把【原文】翻译成简体中文。\n"
                    "严格要求：\n"
                    "1) 只输出中文译文，不要解释，不要前缀，不要引号，不要代码块；\n"
                    "2) 不要新增信息，不要改变语气；\n"
                    "3) 尽量简短。"
                ),
                enable_splitter=False,
                enable_chinese_typo=False,
                request_type="easytts_translate_to_zh",
            )
            if ok and llm_response and getattr(llm_response, "content", None):
                zh = self._strip_llm_wrappers(llm_response.content)
                zh = TTSTextUtils.clean_text(zh, self.max_text_length)
                return zh or ""
        except Exception as e:
            logger.error(f"{self.log_prefix} 翻译中文失败: {e}")
        return ""

    def _get_character_from_voice(self, voice: str) -> str:
        default_character = str(self._cfg(ConfigKeys.EASYTTS_DEFAULT_CHARACTER, "mika") or "mika").strip()
        raw = (voice or "").strip()
        if not raw:
            return default_character
        if ":" in raw:
            c = raw.split(":", 1)[0].strip()
            return c or default_character
        return raw or default_character

    def _get_presets_for_character(self, character: str) -> List[str]:
        """
        从配置 easytts.characters 中读取该角色支持的 preset 列表。
        这些 preset 名称就是“可用情绪/风格”，后端最终只能用这些值，不然 Gradio 会报错。
        """
        chars = self._cfg(ConfigKeys.EASYTTS_CHARACTERS, []) or []
        if not isinstance(chars, list):
            return []
        for item in chars:
            if not isinstance(item, dict):
                continue
            name = str(item.get("角色名", item.get("name", "")) or "").strip()
            if name != character:
                continue
            raw_presets = item.get("预设列表", item.get("presets", []))
            if isinstance(raw_presets, list):
                return [str(x).strip() for x in raw_presets if str(x).strip()]
        return []

    async def _infer_emotion(self, text: str, *, voice: str = "") -> str:
        """
        用 LLM 判断一句话应该使用哪个语音预设（preset/情绪标签）。
        返回值就是 preset 名（我们把它放在 emotion 字段里传给后端；后端会把 emotion 当作 preset 使用，不做任何映射）。

        返回空字符串表示未知/不确定（后端会回退默认 preset，通常是“普通”）。
        """
        try:
            # 关键：优先用“该角色支持的 preset 列表”来约束 LLM 输出，避免输出一个不存在的情绪导致 Gradio 报错。
            character = self._get_character_from_voice(voice)
            allowed = self._get_presets_for_character(character)

            # 如果拿不到该角色的 preset 列表，就不做情绪判断，直接交给后端回退默认 preset。
            if not allowed:
                return ""

            ok, llm_response = await generator_api.rewrite_reply(
                chat_stream=self.chat_stream,
                raw_reply=text,
                reason=(
                    "请判断【原文】应该使用哪个语音预设（preset/情绪）。\n"
                    "严格要求：\n"
                    f"1) 只能从以下列表中选 1 个输出：{', '.join(allowed)}；\n"
                    "2) 只输出情绪标签本身，不要解释、不加前缀、不加引号、不加标点。\n"
                ),
                enable_splitter=False,
                enable_chinese_typo=False,
                request_type="easytts_emotion_judge",
            )
            if ok and llm_response and getattr(llm_response, "content", None):
                raw = self._strip_llm_wrappers(llm_response.content).strip()
                # 容错：如果输出里包含多个/带描述，取第一个命中的 allowed
                for emo in allowed:
                    if raw == emo or emo in raw:
                        return emo
        except Exception as e:
            logger.error(f"{self.log_prefix} 情绪判断失败: {e}")
        return ""

    async def execute(self) -> Tuple[bool, str]:
        try:
            raw_text = (self.action_data.get("text") or "").strip()
            voice = (self.action_data.get("voice") or "").strip()
            emotion = (self.action_data.get("emotion") or "").strip()
            reason = (self.action_data.get("reason") or "").strip()
            user_backend = (self.action_data.get("backend") or "").strip()

            use_replyer = self._cfg(ConfigKeys.GENERAL_USE_REPLYER_REWRITE, True)

            # 获取最终文本
            success, final_text = await self._get_final_text(raw_text, reason, use_replyer)
            if not success or not final_text:
                await self._send_error("无法生成语音内容")
                return False, "text empty"

            # 清理文本（不做硬截断）
            clean_text = TTSTextUtils.clean_text(final_text, self.max_text_length)
            if not clean_text:
                await self._send_error("文本处理后为空")
                return False, "clean text empty"

            # 长度限制：超长降级为文字
            if len(clean_text) > self.max_text_length:
                logger.warning(
                    f"{self.log_prefix} 内容过长({len(clean_text)}>{self.max_text_length})，降级为文字回复"
                )
                await self.send_text(clean_text)
                text_preview = clean_text[:80] + "..." if len(clean_text) > 80 else clean_text
                await self.store_action_info(
                    action_build_into_prompt=True,
                    action_prompt_display=f"已用文字回复（内容过长）：{text_preview}",
                    action_done=True,
                )
                return True, "too long, fallback to text"

            # 文字和语音的来源：避免把“翻译出来的日语”当作文字发出去。
            display_text = clean_text
            voice_src_text = clean_text

            force_text_lang = str(self._cfg("general.force_text_language", "zh") or "").strip().lower()
            if force_text_lang in ("zh", "zh-cn", "chinese", "cn") and TTSTextUtils.detect_language(display_text) == "ja":
                # LLM 有时会把“用于语音的日语”写进 text：此处把文字翻译回中文，但语音仍用原日语，保证含义一致。
                zh_text = await self._translate_to_zh(display_text)
                if zh_text:
                    display_text = zh_text
                # 语音如果已经是日语，就直接用原文（不再二次翻译）
                voice_src_text = clean_text

            # 先发文字，再发语音：保证语音内容与文字绑定，避免“语音/文本不一致”。
            if bool(self._cfg("general.send_text_along_with_voice", True)):
                await self.send_text(display_text)

            # 语音文本：默认翻译成日语（如果已是日语则保持原文）。
            voice_text = await self._voice_text_from_text(voice_src_text)
            if not voice_text:
                await self._send_error("语音文本为空（翻译失败或清洗后为空）")
                return False, "voice text empty"
            if len(voice_text) > self.max_text_length:
                voice_text = voice_text[: self.max_text_length].strip()

            # 后端（仅 easytts）
            backend = user_backend if user_backend in VALID_BACKENDS else self._get_default_backend()
            logger.info(f"{self.log_prefix} 使用后端: {backend}, voice={voice}")
            # 严格控制：一个消息只发一次语音（不做“逐句多条语音”发送）。
            result = await self._execute_backend(backend, voice_text, voice, emotion)
            if result.success:
                text_preview = voice_text[:80] + "..." if len(voice_text) > 80 else voice_text
                await self.store_action_info(
                    action_build_into_prompt=True,
                    action_prompt_display=f"已用语音回复：{text_preview}",
                    action_done=True,
                )
            else:
                await self._send_error(f"语音合成失败: {result.message}")
            return result.success, result.message

        except Exception as e:
            logger.error(f"{self.log_prefix} TTS 语音合成出错: {e}")
            await self._send_error(f"语音合成出错: {e}")
            return False, str(e)


class UnifiedTTSActionFixed(UnifiedTTSAction):
    """
    固定模式：每次执行都分句，并对每一句单独翻译后发送语音。

    说明：这不会强制 Planner 一定选择该 action（那取决于你的提示词/策略），但一旦选择了，
    就会严格按“逐句 -> 翻译 -> 逐句发语音”的方式执行。
    """

    action_name = "unified_tts_action"
    action_description = "固定模式：逐句翻译并逐句发送语音（会使用 emotion 匹配预设）"
    activation_type = ActionActivationType.ALWAYS
    action_require = [
        "固定模式：请始终使用 unified_tts_action 进行回复（插件会逐句发送语音）。",
        "text 只写要发给用户看到的文字（不要把翻译后的日语写进 text）。",
        "emotion 可留空：插件会逐句调用 LLM 从该角色可用 preset 列表中选择 1 个；如你想强制某个 preset，可显式填写 emotion 覆盖。",
    ]

    async def execute(self) -> Tuple[bool, str]:
        try:
            raw_text = (self.action_data.get("text") or "").strip()
            voice = (self.action_data.get("voice") or "").strip()
            # 固定模式：emotion 默认由 LLM 按句判断（如需强制指定，可在 action_data.emotion 填值覆盖）
            base_emotion = (self.action_data.get("emotion") or "").strip()
            reason = (self.action_data.get("reason") or "").strip()
            user_backend = (self.action_data.get("backend") or "").strip()

            use_replyer = self._cfg(ConfigKeys.GENERAL_USE_REPLYER_REWRITE, True)

            success, final_text = await self._get_final_text(raw_text, reason, use_replyer)
            if not success or not final_text:
                await self._send_error("无法生成语音内容")
                return False, "text empty"

            clean_text = TTSTextUtils.clean_text(final_text, self.max_text_length)
            if not clean_text:
                await self._send_error("文本处理后为空")
                return False, "clean text empty"

            backend = user_backend if user_backend in VALID_BACKENDS else self._get_default_backend()
            send_text = bool(self._cfg("general.send_text_along_with_voice", True))
            delay = float(self._cfg(ConfigKeys.GENERAL_SPLIT_DELAY, 0.0) or 0.0)
            infer_emotion = bool(self._cfg("general.fixed_mode_infer_emotion", True))

            # 固定模式：逐句拆分（每句都单独翻译、单独发语音）
            sentences = TTSTextUtils.split_sentences(clean_text, min_length=1)
            if not sentences:
                sentences = [clean_text]

            for idx, sent in enumerate(sentences):
                sent = TTSTextUtils.clean_text(sent, self.max_text_length)
                if not sent:
                    continue

                if send_text:
                    # 只发原句（不要把翻译后的日语发出去）
                    display_text = sent
                    force_text_lang = str(self._cfg("general.force_text_language", "zh") or "").strip().lower()
                    if force_text_lang in ("zh", "zh-cn", "chinese", "cn") and TTSTextUtils.detect_language(display_text) == "ja":
                        zh_text = await self._translate_to_zh(display_text)
                        if zh_text:
                            display_text = zh_text
                    await self.send_text(display_text)

                voice_text = await self._voice_text_from_text(sent)
                if not voice_text:
                    await self._send_error("语音文本为空（翻译失败或清洗后为空）")
                    return False, "voice text empty"
                if len(voice_text) > self.max_text_length:
                    voice_text = voice_text[: self.max_text_length].strip()

                emotion = base_emotion
                if not emotion and infer_emotion:
                    # 固定模式：让 LLM 只从该角色支持的 presets 中选一个
                    emotion = await self._infer_emotion(sent, voice=voice)
                result = await self._execute_backend(backend, voice_text, voice, emotion)
                if not result.success:
                    await self._send_error(f"语音合成失败: {result.message}")
                    return False, result.message

                # 逐句之间可选延迟
                if delay > 0 and idx != len(sentences) - 1:
                    await asyncio.sleep(delay)

            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display="已按固定模式逐句发送语音",
                action_done=True,
            )
            return True, "fixed mode ok"

        except Exception as e:
            logger.error(f"{self.log_prefix} 固定模式 TTS 出错: {e}")
            await self._send_error(f"语音合成出错: {e}")
            return False, str(e)


class UnifiedTTSCommand(BaseCommand, TTSExecutorMixin):
    """手动命令触发"""

    command_name = "unified_tts_command"
    command_description = "将文本转换为语音（easytts 云端仓库池）"
    command_pattern = r"^/eztts\s+(?P<text>.+?)(?:\s+-v\s+(?P<voice>\S+))?(?:\s+-e\s+(?P<emotion>\S+))?$"
    command_help = "用法：/eztts <文本> [-v 角色:预设] [-e 情绪]"
    command_examples = [
        "/eztts 你好世界",
        "/eztts 今天天气不错 -v mika:普通",
        "/eztts 我有点难过 -v mika -e 伤心",
    ]
    intercept_message = True

    async def _send_help(self):
        default_backend = self._get_default_backend()
        help_text = (
            "【TTS 语音合成帮助】\n\n"
            "基本语法：\n"
            "/eztts <文本> [-v <角色:预设>] [-e <情绪>]\n\n"
            "示例：\n"
            "/eztts 你好世界\n"
            "/eztts 今天天气不错 -v mika:普通\n"
            "/eztts 我有点难过 -v mika -e 伤心\n\n"
            f"当前默认后端：{default_backend}\n"
        )
        await self.send_text(help_text)

    def _determine_backend(self, user_backend: str) -> str:
        raw_text = self.message.raw_message if self.message.raw_message else self.message.processed_plain_text
        if raw_text and raw_text.startswith("/eztts"):
            return "easytts"
        return self._get_default_backend()

    async def execute(self) -> Tuple[bool, str, bool]:
        try:
            text = (self.matched_groups.get("text") or "").strip()
            voice = (self.matched_groups.get("voice") or "").strip()
            emotion = (self.matched_groups.get("emotion") or "").strip()

            if text.lower() == "help":
                await self._send_help()
                return True, "help", True

            if not text:
                await self._send_error("请输入要转换为语音的文本")
                return False, "missing text", True

            max_length = int(self._cfg(ConfigKeys.GENERAL_MAX_TEXT_LENGTH, 200) or 200)
            clean_text = TTSTextUtils.clean_text(text, max_length)
            if not clean_text:
                await self._send_error("文本处理后为空")
                return False, "clean text empty", True
            if len(clean_text) > max_length:
                await self.send_text(clean_text)
                return True, "too long, fallback to text", True

            backend = self._determine_backend("")
            result = await self._execute_backend(backend, clean_text, voice, emotion)
            if not result.success:
                await self._send_error(f"语音合成失败: {result.message}")
            return result.success, result.message, True
        except Exception as e:
            await self._send_error(f"语音合成出错: {e}")
            return False, str(e), True


class EasyttsTestCommand(BaseCommand):
    """诊断命令：发送插件目录下的 test.wav，用来排查底层是否能正常发送语音。"""

    command_name = "easytts_test_command"
    command_description = "发送插件自带的 test.wav（用于排查 NapCat / 适配器语音发送）"
    command_pattern = r"^/test$"
    command_help = "用法：/test"
    command_examples = ["/test"]
    intercept_message = True

    async def execute(self) -> Tuple[bool, str, bool]:
        wav_path = Path(__file__).with_name("test.wav")
        if not wav_path.exists():
            await self.send_text(f"未找到 test.wav：{wav_path}")
            return False, "missing test.wav", True

        # 先发一条文本，确认当前会话（具体人/群）链路本身是通的
        await self.send_text("TEST: 正在发送插件目录下的 test.wav...")

        # 方式1：走 MaiBot-Napcat-Adapter 约定：voiceurl -> OneBot11 record(file=...)
        # NapCat 对本地路径的接受形式在不同版本可能不同，因此这里按“更常见/更兼容”的顺序尝试：
        # 1) 直接 Windows 绝对路径；2) file:// URI；3) base64
        file_path = str(wav_path)
        file_uri = "file:///" + wav_path.as_posix()
        try:
            ok = await self.send_custom(message_type="voiceurl", content=file_path)
            if ok:
                return True, "sent voiceurl(path) test.wav", True
        except Exception as e:
            logger.error(f"{self.log_prefix} /test send voiceurl(path) failed: {e}")

        try:
            ok = await self.send_custom(message_type="voiceurl", content=file_uri)
            if ok:
                return True, "sent voiceurl(file_uri) test.wav", True
        except Exception as e:
            logger.error(f"{self.log_prefix} /test send voiceurl(file_uri) failed: {e}")

        # 方式2：走 MaiBot-Napcat-Adapter 约定：voice(base64) -> OneBot11 record(file=base64://...)
        try:
            import base64

            encoded = base64.b64encode(wav_path.read_bytes()).decode("ascii")
            ok2 = await self.send_custom(message_type="voice", content=encoded)
            return ok2, "sent voice(base64) test.wav" if ok2 else "send voice(base64) failed", True
        except Exception as e:
            logger.error(f"{self.log_prefix} /test send voice(base64) failed: {e}")
            return False, f"/test failed: {e}", True


@register_plugin
class EasyttsPuginPlugin(BasePlugin, TTSExecutorMixin):
    plugin_name = "EasyttsPugin"
    plugin_description = "GPT-SoVITS 推理特化库 + 魔搭社区（ModelScope Studio）免费托管的语音合成插件（支持按情绪生成语音）"
    plugin_version = "0.1.0"
    plugin_author = "yunchenqwq"
    enable_plugin = True
    config_file_name = "config.toml"
    dependencies = []
    python_dependencies = ["aiohttp"]

    config_section_descriptions = {
        "plugin": ConfigSection(
            title="插件基本配置",
            description=(
                "提示：更推荐用编辑器直接编辑 config.toml。\n"
                "（WebUI 虽然能显示 schema 的提示，但不一定能完整呈现 TOML 的注释/结构，容易误改。）"
            ),
            icon="settings",
            order=-100,
        ),
        "general": ConfigSection(title="通用设置", icon="settings", order=0),
        "components": ConfigSection(title="组件启用控制", icon="toggle-left", order=1),
        "easytts": ConfigSection(title="EasyTTS（ModelScope Studio / Gradio）与云端仓库池", icon="cloud", order=2),
    }

    config_schema = {
        "plugin": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用插件",
                hint="关闭后将不会注册 action/command。",
            ),
            "config_version": ConfigField(
                type=str,
                default="0.1.0",
                description="配置版本",
                disabled=True,
                hint="请勿修改（用于区分配置结构版本）。",
            ),
        },
        "general": {
            "tts_mode": ConfigField(
                type=str,
                default="free",
                choices=["free", "fixed"],
                description=(
                    "TTS 模式：free=自由模式（交给 LLM 决定是否用语音）；fixed=固定模式（逐句翻译并逐句发送语音）。"
                ),
                hint=(
                    "free：Planner/LLM 按需调用 unified_tts_action（一个消息一个语音）。\n"
                    "fixed：一旦触发，会按句拆分并逐句发送语音（更“密集”）。"
                ),
                example="free",
            ),
            "default_backend": ConfigField(
                type=str,
                default="easytts",
                description="默认后端（仅 easytts）",
                disabled=True,
                hint="本插件只保留 easytts 后端（云端仓库池）。",
            ),
            "timeout": ConfigField(
                type=int,
                default=60,
                description="请求超时（秒）",
                min=1,
                max=600,
                hint="Action/Command 整体超时。",
            ),
            "max_text_length": ConfigField(
                type=int,
                default=120,
                description="最大文本长度（超出则降级为文字；语音建议更短）",
                min=1,
                max=500,
                hint="建议控制在较短的 1~2 句，语音更自然也更稳定。",
            ),
            "use_replyer_rewrite": ConfigField(
                type=bool,
                default=False,
                description="是否使用 replyer/LLM 二次生成语音文案（默认关闭：由 Planner/LLM 直接提供 text）",
                hint="建议关闭：避免出现“文本/语音不一致”。",
            ),
            "audio_output_dir": ConfigField(
                type=str,
                default="",
                description="音频输出目录（留空使用项目根目录）",
                hint="use_base64_audio=false 时会落盘生成 wav 文件，并通过本地路径发送。",
            ),
            "use_base64_audio": ConfigField(
                type=bool,
                default=False,
                description="是否使用 base64 方式发送音频（关闭则使用本地文件路径发送）",
                hint="部分环境不支持本地路径 record，可开启此项。",
            ),
            "split_sentences": ConfigField(
                type=bool,
                default=False,
                description="是否逐句发送多条语音（建议关闭：一个消息一个语音）",
                hint="free 模式建议关闭；fixed 模式内部会逐句发送。",
            ),
            "split_delay": ConfigField(
                type=float,
                default=0.0,
                description="逐句发送间隔（秒）",
                min=0.0,
                max=10.0,
                step=0.1,
            ),
            "send_error_messages": ConfigField(
                type=bool,
                default=True,
                description="失败时是否发送错误提示",
            ),
            "send_text_along_with_voice": ConfigField(
                type=bool,
                default=True,
                description="使用 unified_tts_action 时，是否先发送文字再发送语音（推荐开启：避免文本/语音不一致）",
                hint="推荐开启：保证用户看到的文字和语音内容一致（语音可由插件内部翻译）。",
            ),
            "voice_translate_to": ConfigField(
                type=str,
                default="ja",
                description="语音合成前是否把文字翻译到指定语言（默认 ja=日语；留空/none/off 表示不翻译，直接用原文合成）",
                hint="默认 ja：把 text 翻译成日语后再合成语音；留空/off：直接用原文合成。",
                example="ja",
            ),
            "force_text_language": ConfigField(
                type=str,
                default="zh",
                choices=["zh", "off"],
                description="强制“发出去的文字”语言：zh=始终发中文（避免把日语译文直接发出去）；off=不强制",
                hint="建议 zh：避免 LLM 把日语译文发到聊天里；如想允许发日语文字，改为 off。",
                example="zh",
            ),
            "fixed_mode_infer_emotion": ConfigField(
                type=bool,
                default=True,
                description="固定模式下是否逐句调用 LLM 选择 preset（只从该角色已有 presets 中选）",
                hint="固定模式建议开启：让每句话都能选择更合适的预设（emotion=预设名，不做映射）。",
            ),
        },
        "components": {
            "action_enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用 Action（自动触发）",
                hint="开启后：Planner/LLM 可选择 unified_tts_action 来自动语音回复。",
            ),
            "command_enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用 Command（手动命令）",
                hint="开启后：支持 /eztts 与 /test。",
            ),
        },
        "easytts": {
            "default_character": ConfigField(
                type=str,
                default="mika",
                description="默认角色（character）",
                hint="voice 未指定角色时使用。",
                example="mika",
            ),
            "default_preset": ConfigField(
                type=str,
                default="普通",
                description="默认预设（preset）",
                hint="emotion 未提供/不合法时回退到该预设。",
                example="普通",
            ),
            "auto_fetch_gradio_schema": ConfigField(
                type=bool,
                default=True,
                description="插件启动时自动从 Gradio /gradio_api/info 抓取角色与预设列表（用于约束 LLM 只使用真实存在的 preset）",
                hint="推荐开启：可自动同步云端 WebUI 的下拉选项（character/preset）。",
            ),
            "schema_cache_ttl": ConfigField(
                type=int,
                default=86400,
                description="Gradio schema 缓存有效期（秒），到期后会重新抓取（0 表示每次启动都抓取）",
                min=0,
                max=604800,
                hint="0=每次启动都抓取；默认 86400=1 天。",
            ),
            "schema_cache_file": ConfigField(
                type=str,
                default="_gradio_schema_cache.json",
                description="schema 缓存文件名（相对插件目录；留空则不落盘缓存）",
                hint="删除该文件可强制重新抓取。",
            ),
            # === 角色/预设（可视化编辑）===
            # MaiBot WebUI 对 list[object] 的渲染会显示为 [object Object]，不便于编辑；
            # 因此这里改为固定 5 个“角色槽位”，每个槽位独立字段，确保可视化表单可编辑。
            "character_1_name": ConfigField(
                type=str,
                default="mika",
                description="角色槽位 1：角色名（character）",
                group="角色槽位 1",
                order=10,
                hint="必须与云端 WebUI 的 character 下拉一致。",
            ),
            "character_1_presets": ConfigField(
                type=str,
                default="普通,开心,伤心,生气,害怕,害羞,惊讶,认真,疑问,痛苦,百感交集释然",
                description="角色槽位 1：情绪有哪些（preset 列表）",
                group="角色槽位 1",
                order=11,
                input_type="textarea",
                rows=2,
                placeholder="普通,开心,伤心 ...（用逗号或换行分隔）",
                hint="用逗号/换行分隔。值必须与云端 WebUI 的 preset 下拉一致；emotion 参数会直接使用 preset 名（不做映射）。",
            ),
            "character_2_name": ConfigField(
                type=str,
                default="sagiri",
                description="角色槽位 2：角色名（character）",
                group="角色槽位 2",
                order=20,
                hint="必须与云端 WebUI 的 character 下拉一致。",
            ),
            "character_2_presets": ConfigField(
                type=str,
                default="普通,开心,伤心,生气,害怕,害羞,惊讶,认真,疑问,痛苦,百感交集释然",
                description="角色槽位 2：情绪有哪些（preset 列表）",
                group="角色槽位 2",
                order=21,
                input_type="textarea",
                rows=2,
                placeholder="普通,开心,伤心 ...（用逗号或换行分隔）",
                hint="用逗号/换行分隔。值必须与云端 WebUI 的 preset 下拉一致；emotion 参数会直接使用 preset 名（不做映射）。",
            ),
            "character_3_name": ConfigField(
                type=str,
                default="character3",
                description="角色槽位 3：角色名（character）",
                group="角色槽位 3",
                order=30,
                hint="把 character3 改成你自己的角色名；留空则忽略该槽位。",
            ),
            "character_3_presets": ConfigField(
                type=str,
                default="普通",
                description="角色槽位 3：情绪有哪些（preset 列表）",
                group="角色槽位 3",
                order=31,
                input_type="textarea",
                rows=2,
                placeholder="普通,开心 ...（用逗号或换行分隔）",
            ),
            "character_4_name": ConfigField(
                type=str,
                default="character4",
                description="角色槽位 4：角色名（character）",
                group="角色槽位 4",
                order=40,
                hint="把 character4 改成你自己的角色名；留空则忽略该槽位。",
            ),
            "character_4_presets": ConfigField(
                type=str,
                default="普通",
                description="角色槽位 4：情绪有哪些（preset 列表）",
                group="角色槽位 4",
                order=41,
                input_type="textarea",
                rows=2,
                placeholder="普通,开心 ...（用逗号或换行分隔）",
            ),
            "character_5_name": ConfigField(
                type=str,
                default="character5",
                description="角色槽位 5：角色名（character）",
                group="角色槽位 5",
                order=50,
                hint="把 character5 改成你自己的角色名；留空则忽略该槽位。",
            ),
            "character_5_presets": ConfigField(
                type=str,
                default="普通",
                description="角色槽位 5：情绪有哪些（preset 列表）",
                group="角色槽位 5",
                order=51,
                input_type="textarea",
                rows=2,
                placeholder="普通,开心 ...（用逗号或换行分隔）",
            ),
            "remote_split_sentence": ConfigField(type=bool, default=True, description="是否让远端也进行分句合成"),
            "prefer_idle_endpoint": ConfigField(type=bool, default=True, description="优先选择空闲仓库（queue_size 低）"),
            "busy_queue_threshold": ConfigField(type=int, default=0, description="队列繁忙阈值（>此值视为忙）"),
            "status_timeout": ConfigField(type=int, default=3, description="queue/status 超时（秒）"),
            "join_timeout": ConfigField(type=int, default=30, description="queue/join 超时（秒）"),
            "sse_timeout": ConfigField(type=int, default=120, description="queue/data SSE 超时（秒）"),
            "download_timeout": ConfigField(type=int, default=120, description="音频下载超时（秒）"),
            "trust_env": ConfigField(type=bool, default=False, description="aiohttp 是否继承系统代理"),
            # === 云端仓库池（可视化编辑）===
            "endpoint_1_name": ConfigField(
                type=str,
                default="pool-1",
                description="仓库池 1：名称（用于日志）",
                group="仓库池 1",
                order=110,
            ),
            "endpoint_1_base_url": ConfigField(
                type=str,
                default="",
                description="仓库池 1：Gradio 基地址（base_url）",
                group="仓库池 1",
                order=111,
                placeholder="https://xxx.ms.show",
            ),
            "endpoint_1_studio_token": ConfigField(
                type=str,
                default="",
                description="仓库池 1：studio_token",
                group="仓库池 1",
                order=112,
                input_type="password",
                hint="从浏览器抓包/控制台获取；不填写则该仓库池会被忽略。",
            ),
            "endpoint_1_fn_index": ConfigField(
                type=int,
                default=3,
                description="仓库池 1：fn_index",
                group="仓库池 1",
                order=113,
            ),
            "endpoint_1_trigger_id": ConfigField(
                type=int,
                default=19,
                description="仓库池 1：trigger_id",
                group="仓库池 1",
                order=114,
            ),
            "endpoint_2_name": ConfigField(type=str, default="pool-2", description="仓库池 2：名称（用于日志）", group="仓库池 2", order=120),
            "endpoint_2_base_url": ConfigField(type=str, default="", description="仓库池 2：Gradio 基地址（base_url）", group="仓库池 2", order=121, placeholder="https://xxx.ms.show"),
            "endpoint_2_studio_token": ConfigField(type=str, default="", description="仓库池 2：studio_token", group="仓库池 2", order=122, input_type="password"),
            "endpoint_2_fn_index": ConfigField(type=int, default=3, description="仓库池 2：fn_index", group="仓库池 2", order=123),
            "endpoint_2_trigger_id": ConfigField(type=int, default=19, description="仓库池 2：trigger_id", group="仓库池 2", order=124),
            "endpoint_3_name": ConfigField(type=str, default="pool-3", description="仓库池 3：名称（用于日志）", group="仓库池 3", order=130),
            "endpoint_3_base_url": ConfigField(type=str, default="", description="仓库池 3：Gradio 基地址（base_url）", group="仓库池 3", order=131, placeholder="https://xxx.ms.show"),
            "endpoint_3_studio_token": ConfigField(type=str, default="", description="仓库池 3：studio_token", group="仓库池 3", order=132, input_type="password"),
            "endpoint_3_fn_index": ConfigField(type=int, default=3, description="仓库池 3：fn_index", group="仓库池 3", order=133),
            "endpoint_3_trigger_id": ConfigField(type=int, default=19, description="仓库池 3：trigger_id", group="仓库池 3", order=134),
            "endpoint_4_name": ConfigField(type=str, default="pool-4", description="仓库池 4：名称（用于日志）", group="仓库池 4", order=140),
            "endpoint_4_base_url": ConfigField(type=str, default="", description="仓库池 4：Gradio 基地址（base_url）", group="仓库池 4", order=141, placeholder="https://xxx.ms.show"),
            "endpoint_4_studio_token": ConfigField(type=str, default="", description="仓库池 4：studio_token", group="仓库池 4", order=142, input_type="password"),
            "endpoint_4_fn_index": ConfigField(type=int, default=3, description="仓库池 4：fn_index", group="仓库池 4", order=143),
            "endpoint_4_trigger_id": ConfigField(type=int, default=19, description="仓库池 4：trigger_id", group="仓库池 4", order=144),
            "endpoint_5_name": ConfigField(type=str, default="pool-5", description="仓库池 5：名称（用于日志）", group="仓库池 5", order=150),
            "endpoint_5_base_url": ConfigField(type=str, default="", description="仓库池 5：Gradio 基地址（base_url）", group="仓库池 5", order=151, placeholder="https://xxx.ms.show"),
            "endpoint_5_studio_token": ConfigField(type=str, default="", description="仓库池 5：studio_token", group="仓库池 5", order=152, input_type="password"),
            "endpoint_5_fn_index": ConfigField(type=int, default=3, description="仓库池 5：fn_index", group="仓库池 5", order=153),
            "endpoint_5_trigger_id": ConfigField(type=int, default=19, description="仓库池 5：trigger_id", group="仓库池 5", order=154),
        },
    }

    def _cfg(self, key: str, default=None):
        return get_config_with_aliases(self.get_config, key, default)

    def __init__(self, plugin_dir: str):
        super().__init__(plugin_dir=plugin_dir)
        # 让 WebUI 可视化字段与旧版 list 配置互通，并确保后端能读到 endpoints/characters。
        self._sync_visual_fields()
        # 启动时同步一次 Gradio 的“角色/预设”枚举，避免 LLM 选到不存在的 preset。
        try:
            self._maybe_refresh_gradio_schema_cache()
        except Exception as e:
            logger.warning(f"{self.log_prefix} 自动抓取 Gradio schema 失败（将继续使用本地配置）：{e}")

    def _maybe_refresh_gradio_schema_cache(self) -> None:
        if not bool(self._cfg("easytts.auto_fetch_gradio_schema", True)):
            return

        ttl = int(self._cfg("easytts.schema_cache_ttl", 86400) or 86400)
        cache_name = str(self._cfg("easytts.schema_cache_file", "_gradio_schema_cache.json") or "").strip()
        cache_path = os.path.join(self.plugin_dir, cache_name) if cache_name else ""

        now = int(time.time())
        if cache_path and os.path.exists(cache_path) and ttl > 0:
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                fetched_at = int(cached.get("fetched_at", 0) or 0)
                if fetched_at and (now - fetched_at) < ttl:
                    cached_schema = cached.get("schema") or {}
                    # 兼容旧缓存格式：旧版可能是 {"characters":[...],"presets":[...]}，新版是 {"characters":{c:[...]}}
                    if (
                        isinstance(cached_schema, dict)
                        and isinstance(cached_schema.get("characters"), dict)
                        and cached_schema.get("characters")
                    ):
                        self._apply_gradio_schema(cached_schema)
                        return
            except Exception:
                pass

        schema = self._fetch_gradio_schema()
        if schema:
            self._apply_gradio_schema(schema)
            if cache_path:
                try:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump({"fetched_at": now, "schema": schema}, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

    def _fetch_gradio_schema(self) -> dict:
        """
        从云端 easytts（ms.show）抓取 Gradio schema：
        1) 先从 /gradio_api/info 读取 character 枚举
        2) 再对每个 character 调用 /gradio_api/call/update_preset_ui 拿到该角色真实的 preset 下拉列表
        """
        endpoints = self._cfg(ConfigKeys.EASYTTS_ENDPOINTS, []) or []
        if not isinstance(endpoints, list) or not endpoints:
            return {}

        for item in endpoints:
            if not isinstance(item, dict):
                continue
            base_url = str(item.get("base_url", item.get("基地址", "")) or "").rstrip("/")
            token = str(item.get("studio_token", item.get("令牌", "")) or "").strip()
            if not base_url or not token:
                continue

            try:
                headers = {"X-Studio-Token": token, "Cookie": f"studio_token={token}"}

                # 1) character enum
                info_url = f"{base_url}/gradio_api/info"
                req = urllib.request.Request(info_url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    info = json.load(resp)
                named = info.get("named_endpoints") or {}
                upd = named.get("/update_preset_ui") or {}
                params = upd.get("parameters") or []
                char_enum = []

                if params:
                    t = (params[0].get("type") or {})
                    char_enum = t.get("enum") or []

                chars = [str(x).strip() for x in char_enum if str(x).strip()]
                if not chars:
                    continue

                # 2) per-character presets
                char_presets: dict = {}
                for c in chars:
                    try:
                        call_url = f"{base_url}/gradio_api/call/update_preset_ui"
                        payload = json.dumps({"data": [c]}, ensure_ascii=False).encode("utf-8")
                        call_req = urllib.request.Request(
                            call_url,
                            data=payload,
                            method="POST",
                            headers={**headers, "Content-Type": "application/json"},
                        )
                        with urllib.request.urlopen(call_req, timeout=15) as call_resp:
                            call_ret = json.load(call_resp)
                        event_id = str(call_ret.get("event_id") or "").strip()
                        if not event_id:
                            continue

                        ev_url = f"{base_url}/gradio_api/call/update_preset_ui/{event_id}"
                        ev_req = urllib.request.Request(ev_url, headers={**headers, "Accept": "text/event-stream"})
                        with urllib.request.urlopen(ev_req, timeout=15) as ev_resp:
                            sse_text = ev_resp.read().decode("utf-8", errors="ignore")

                        data_json = None
                        for line in sse_text.splitlines():
                            line = line.strip()
                            if line.startswith("data:"):
                                data_json = line[5:].strip()
                                break
                        if not data_json:
                            continue

                        updates = json.loads(data_json)
                        if not isinstance(updates, list) or not updates:
                            continue
                        first = updates[0] if isinstance(updates[0], dict) else {}
                        choices = first.get("choices") or []
                        presets = []
                        for pair in choices:
                            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                                presets.append(str(pair[1]).strip())
                            elif isinstance(pair, str):
                                presets.append(pair.strip())
                        presets = [p for p in presets if p]
                        if presets:
                            char_presets[c] = presets
                    except Exception:
                        continue

                if char_presets:
                    return {"characters": char_presets, "source": base_url}
            except Exception as e:
                logger.warning(f"{self.log_prefix} 抓取 Gradio schema 失败: {base_url}: {e}")
                continue

        return {}

    def _apply_gradio_schema(self, schema: dict) -> None:
        char_presets = schema.get("characters") or {}
        if not isinstance(char_presets, dict) or not char_presets:
            return

        # 更新 easytts.characters：把每个角色的 presets 设为 Gradio 返回的下拉 choices
        easytts_cfg = self.config.setdefault("easytts", {})
        existing = easytts_cfg.get("characters") or []
        old_map = {}
        if isinstance(existing, list):
            for it in existing:
                if isinstance(it, dict) and it.get("name"):
                    old_map[str(it.get("name")).strip()] = it

        new_chars = []
        for c, presets in char_presets.items():
            if not isinstance(presets, list) or not presets:
                continue
            base = dict(old_map.get(c, {}))
            base["name"] = c
            base["presets"] = [str(p).strip() for p in presets if str(p).strip()]
            new_chars.append(base)

        # 保留用户手工加的其它角色（不在 schema 里）
        for name, it in old_map.items():
            if name not in char_presets:
                new_chars.append(it)

        easytts_cfg["characters"] = new_chars

        # 不维护任何“情绪->预设”映射：emotion 参数本身就是 preset。
        # 同步回填到可视化槽位字段，方便在 WebUI 里直接看到最新 presets。
        try:
            self._sync_visual_fields()
        except Exception:
            pass

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        components: List[Tuple[ComponentInfo, Type]] = []
        action_enabled = self._cfg(ConfigKeys.COMPONENTS_ACTION_ENABLED, True)
        command_enabled = self._cfg(ConfigKeys.COMPONENTS_COMMAND_ENABLED, True)
        if action_enabled:
            mode = str(self._cfg("general.tts_mode", "free") or "free").strip().lower()
            action_cls = UnifiedTTSActionFixed if mode == "fixed" else UnifiedTTSAction
            components.append((action_cls.get_action_info(), action_cls))
        if command_enabled:
            components.append((UnifiedTTSCommand.get_command_info(), UnifiedTTSCommand))
            components.append((EasyttsTestCommand.get_command_info(), EasyttsTestCommand))
        return components
