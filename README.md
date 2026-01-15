# EasyttsPlugin

MaiBot 插件：使用 easytts（ModelScope Studio / Gradio）进行语音合成，并支持云端仓库池自动切换与按情绪选择预设。

本仓库中的插件目录是 `EasyttsPugin/`（历史命名保留）。

## 安装
把 `EasyttsPugin/` 复制到你的 MaiBot 插件目录（与其他插件同级），然后编辑 `EasyttsPugin/config.toml`。

## 命令
- `/eztts <文本> [-v 角色:预设] [-e 情绪]`

## 许可证
本插件参考并改造自 `xuqian13/tts_voice_plugin`，因此按 AGPL-3.0 分发，详见 `LICENSE` 与 `EasyttsPugin/NOTICE.md`。
