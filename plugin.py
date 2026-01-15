"""
EasyttsPlugin - 语音合成插件

基于 GPT-SoVITS 推理特化库（Genie-TTS / GPT-SoVITS ONNX 推理引擎）+ 魔搭社区（ModelScope Studio）免费托管：
- 支持按情绪（emotion -> preset 映射）生成语音
- 支持云端仓库池：当一个仓库忙时自动切换到其他仓库

两种模式（general.tts_mode）：
- free：自由模式（LLM_JUDGE），由 LLM 决定是否使用语音；语音与文本绑定，默认“中文文字 + 日语语音”
- fixed：固定模式（ALWAYS），逐句翻译/逐句判断情绪/逐句发送语音
"""

import sys

sys.dont_write_bytecode = True

import asyncio
from pathlib import Path
from typing import List, Tuple, Type

from src.common.logger import get_logger
from src.plugin_system.apis import generator_api
from src.plugin_system.apis.plugin_register_api import register_plugin
from src.plugin_system.base.base_action import BaseAction, ActionActivationType
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.base.component_types import ChatMode, ComponentInfo
from src.plugin_system.base.config_types import ConfigField

from backends import TTSBackendRegistry, TTSResult
from config_keys import ConfigKeys
from utils.text import TTSTextUtils

logger = get_logger("EasyttsPlugin")

VALID_BACKENDS = ["easytts"]


class TTSExecutorMixin:
    def _create_backend(self, backend_name: str):
        backend = TTSBackendRegistry.create(backend_name, self.get_config, self.log_prefix)
        if backend and hasattr(backend, "set_send_custom"):
            backend.set_send_custom(self.send_custom)
        return backend

    async def _execute_backend(self, backend_name: str, text: str, voice: str = "", emotion: str = "") -> TTSResult:
        backend = self._create_backend(backend_name)
        if not backend:
            return TTSResult(success=False, message=f"未知的 TTS 后端: {backend_name}")
        return await backend.execute(text, voice, emotion=emotion)

    def _get_default_backend(self) -> str:
        backend = self.get_config(ConfigKeys.GENERAL_DEFAULT_BACKEND, "easytts")
        if backend not in VALID_BACKENDS:
            return "easytts"
        return backend

    async def _send_error(self, message: str) -> None:
        if self.get_config(ConfigKeys.GENERAL_SEND_ERROR_MESSAGES, True):
            await self.send_text(message)


class UnifiedTTSAction(BaseAction, TTSExecutorMixin):
    """
    自由模式：LLM_JUDGE 决定是否触发。

    约定：
    - text：发给用户看到的文字（默认建议中文短句）
    - 语音：默认把 text 翻译成日语后合成（避免“语音内容与文字不一致”）
    """

    action_name = "unified_tts_action"
    action_description = "自由模式：发送一条文字回复，并附带一条语音（语音内容默认为该文字的日语版本）"
    activation_type = ActionActivationType.LLM_JUDGE
    mode_enable = ChatMode.ALL
    parallel_action = False

    action_parameters = {
        "text": (
            "你要发送给用户的最终文字回复（必填，建议 1~2 句，必须 <= max_text_length）。\n"
            "注意：插件会用这段文字生成语音（默认会翻译成日语后再合成），所以不要再额外选择其他文本回复 action，避免“文本/语音不匹配”。"
        ),
        "voice": (
            "可选：角色/预设。\n"
            "1) 只写角色：例如 `sagiri` / `mika`（推荐：允许按 emotion 自动选择预设）\n"
            "2) 写 角色:预设：例如 `mika:普通`（显式指定预设后，不再按 emotion 自动切换）"
        ),
        "backend": "可选：TTS 后端（仅支持 easytts，可省略）",
        "emotion": (
            "可选：情绪（用于自动选择 preset，映射见 config.toml 的 [easytts.emotion_preset_map]）。\n"
            "推荐：普通/开心/伤心/生气/害怕/害羞/惊讶/认真/疑问/痛苦/百感交集释然"
        ),
    }

    action_require = [
        "由 LLM 自由决定是否用语音回复（LLM_JUDGE）。不要因为能用就滥用：仅在更适合语音表达时使用。",
        "严格限制：对同一条用户消息，最多选择 1 次 unified_tts_action（一个消息一个语音）。",
        "text 必须短（建议 1~2 句），否则会被截断/降级为文字。",
        "不要把“翻译后的日语”写进 text；语音需要日语时插件会自动翻译。",
    ]

    associated_types = ["text", "command"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timeout = int(self.get_config(ConfigKeys.GENERAL_TIMEOUT, 60) or 60)
        self.max_text_length = int(self.get_config(ConfigKeys.GENERAL_MAX_TEXT_LENGTH, 120) or 120)

    async def _get_final_text(self, raw_text: str, reason: str, use_replyer: bool) -> Tuple[bool, str]:
        if raw_text:
            return True, raw_text
        if not use_replyer:
            return False, ""

        # 兜底：只有上层没给 text 时才调用 replyer（不推荐）
        max_text_length = int(self.get_config(ConfigKeys.GENERAL_MAX_TEXT_LENGTH, 120) or 120)
        try:
            success, llm_response = await generator_api.generate_reply(
                chat_stream=self.chat_stream,
                reply_message=self.action_message,
                reply_reason=reason or "生成一条简短语音回复",
                extra_info=f"【重要】只输出最终回复文本；控制在 {max_text_length} 字以内。",
                request_type="easytts_plugin",
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
        if not text:
            return ""
        t = text.strip()
        if "```" in t:
            parts = [p.strip() for p in t.split("```") if p.strip()]
            if parts:
                if len(parts) >= 2 and "\n" in parts[1]:
                    t = parts[1].strip()
                else:
                    t = parts[-1].strip()
        for prefix in ("翻译：", "译文：", "日语：", "日本語：", "Japanese:", "JP:"):
            if t.startswith(prefix):
                t = t[len(prefix) :].strip()
        if (t.startswith('"') and t.endswith('"')) or (t.startswith("“") and t.endswith("”")):
            t = t[1:-1].strip()
        return t

    async def _voice_text_from_text(self, text: str) -> str:
        """
        将“要发送的文本”转换成“要合成语音的文本”。
        默认：若文本非日语，则用 LLM 翻译成日语；若已是日语则直接使用。
        """
        target = str(self.get_config(ConfigKeys.GENERAL_VOICE_TRANSLATE_TO, "ja") or "").strip().lower()
        if not target or target in ("none", "off", "false", "0", "disable", "disabled"):
            return text

        if target in ("ja", "jp", "japanese") and TTSTextUtils.detect_language(text) == "ja":
            return text

        try:
            ok, llm_response = await generator_api.rewrite_reply(
                chat_stream=self.chat_stream,
                raw_reply=text,
                reason=(
                    "请把【原文】翻译成自然的日语。\n"
                    "严格要求：\n"
                    "1) 只输出日语译文，不要解释，不要前缀，不要引号，不要代码块；\n"
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

    async def _infer_emotion(self, text: str) -> str:
        """用 LLM 判断一句话的情绪标签（用于 emotion->preset 映射）。"""
        try:
            emotion_map = self.get_config(ConfigKeys.EASYTTS_EMOTION_PRESET_MAP, {}) or {}
            allowed = list(emotion_map.keys()) if isinstance(emotion_map, dict) else []
            if not allowed:
                allowed = [
                    "普通",
                    "开心",
                    "伤心",
                    "生气",
                    "害怕",
                    "害羞",
                    "惊讶",
                    "认真",
                    "疑问",
                    "痛苦",
                    "百感交集释然",
                ]

            ok, llm_response = await generator_api.rewrite_reply(
                chat_stream=self.chat_stream,
                raw_reply=text,
                reason=(
                    "请判断【原文】的情绪标签。\n"
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

            use_replyer = bool(self.get_config(ConfigKeys.GENERAL_USE_REPLYER_REWRITE, False))

            success, final_text = await self._get_final_text(raw_text, reason, use_replyer)
            if not success or not final_text:
                await self._send_error("无法生成语音内容")
                return False, "text empty"

            clean_text = TTSTextUtils.clean_text(final_text, self.max_text_length)
            if not clean_text:
                await self._send_error("文本处理后为空")
                return False, "clean text empty"

            if len(clean_text) > self.max_text_length:
                await self.send_text(clean_text[: self.max_text_length])
                return True, "too long, fallback to text"

            # 修复：避免把“翻译出来的日语”当作文字发出去
            display_text = clean_text
            voice_src_text = clean_text
            force_text_lang = str(self.get_config(ConfigKeys.GENERAL_FORCE_TEXT_LANGUAGE, "zh") or "").strip().lower()
            if force_text_lang in ("zh", "zh-cn", "chinese", "cn") and TTSTextUtils.detect_language(display_text) == "ja":
                zh = await self._translate_to_zh(display_text)
                if zh:
                    display_text = zh
                voice_src_text = clean_text  # 语音保持原日语/原文

            if bool(self.get_config(ConfigKeys.GENERAL_SEND_TEXT_ALONG_WITH_VOICE, True)):
                await self.send_text(display_text)

            voice_text = await self._voice_text_from_text(voice_src_text)
            if not voice_text:
                await self._send_error("语音文本为空")
                return False, "voice text empty"
            if len(voice_text) > self.max_text_length:
                voice_text = voice_text[: self.max_text_length].strip()

            backend = user_backend if user_backend in VALID_BACKENDS else self._get_default_backend()
            result = await self._execute_backend(backend, voice_text, voice, emotion)
            if not result.success:
                await self._send_error(f"语音合成失败: {result.message}")
            return result.success, result.message

        except Exception as e:
            logger.error(f"{self.log_prefix} TTS 语音合成出错: {e}")
            await self._send_error(f"语音合成出错: {e}")
            return False, str(e)


class UnifiedTTSActionFixed(UnifiedTTSAction):
    """
    固定模式：逐句翻译 + 逐句情绪判断 + 逐句发送语音。
    """

    action_name = "unified_tts_action"
    action_description = "固定模式：逐句翻译并逐句发送语音（会使用 emotion 匹配预设）"
    activation_type = ActionActivationType.ALWAYS
    action_require = [
        "固定模式：请始终使用 unified_tts_action 进行回复（插件会逐句发送语音）。",
        "text 只写要发给用户看到的文字（不要把翻译后的日语写进 text）。",
        "emotion 可留空：插件会逐句调用 LLM 判断情绪并映射到 preset；如你想强制某个情绪，可显式填写 emotion 覆盖。",
    ]

    async def execute(self) -> Tuple[bool, str]:
        try:
            raw_text = (self.action_data.get("text") or "").strip()
            voice = (self.action_data.get("voice") or "").strip()
            base_emotion = (self.action_data.get("emotion") or "").strip()
            reason = (self.action_data.get("reason") or "").strip()
            user_backend = (self.action_data.get("backend") or "").strip()

            use_replyer = bool(self.get_config(ConfigKeys.GENERAL_USE_REPLYER_REWRITE, False))
            success, final_text = await self._get_final_text(raw_text, reason, use_replyer)
            if not success or not final_text:
                await self._send_error("无法生成语音内容")
                return False, "text empty"

            clean_text = TTSTextUtils.clean_text(final_text, self.max_text_length)
            if not clean_text:
                await self._send_error("文本处理后为空")
                return False, "clean text empty"

            backend = user_backend if user_backend in VALID_BACKENDS else self._get_default_backend()
            send_text = bool(self.get_config(ConfigKeys.GENERAL_SEND_TEXT_ALONG_WITH_VOICE, True))
            delay = float(self.get_config(ConfigKeys.GENERAL_SPLIT_DELAY, 0.0) or 0.0)
            infer_emotion = bool(self.get_config(ConfigKeys.GENERAL_FIXED_MODE_INFER_EMOTION, True))

            sentences = TTSTextUtils.split_sentences(clean_text, min_length=1) or [clean_text]
            for idx, sent in enumerate(sentences):
                sent = TTSTextUtils.clean_text(sent, self.max_text_length)
                if not sent:
                    continue

                if send_text:
                    display_text = sent
                    force_text_lang = str(self.get_config(ConfigKeys.GENERAL_FORCE_TEXT_LANGUAGE, "zh") or "").strip().lower()
                    if force_text_lang in ("zh", "zh-cn", "chinese", "cn") and TTSTextUtils.detect_language(display_text) == "ja":
                        zh = await self._translate_to_zh(display_text)
                        if zh:
                            display_text = zh
                    await self.send_text(display_text)

                voice_text = await self._voice_text_from_text(sent)
                if not voice_text:
                    await self._send_error("语音文本为空")
                    return False, "voice text empty"
                if len(voice_text) > self.max_text_length:
                    voice_text = voice_text[: self.max_text_length].strip()

                emotion = base_emotion
                if not emotion and infer_emotion:
                    emotion = await self._infer_emotion(sent)

                result = await self._execute_backend(backend, voice_text, voice, emotion)
                if not result.success:
                    await self._send_error(f"语音合成失败: {result.message}")
                    return False, result.message

                if delay > 0 and idx != len(sentences) - 1:
                    await asyncio.sleep(delay)

            return True, "fixed mode ok"

        except Exception as e:
            logger.error(f"{self.log_prefix} 固定模式 TTS 出错: {e}")
            await self._send_error(f"语音合成出错: {e}")
            return False, str(e)


class UnifiedTTSCommand(BaseCommand, TTSExecutorMixin):
    """手动命令触发：/eztts"""

    command_name = "unified_tts_command"
    command_description = "将文本转换为语音（easytts 云端仓库池）"
    command_pattern = r"^/eztts\s+(?P<text>.+?)(?:\s+-v\s+(?P<voice>\S+))?(?:\s+-e\s+(?P<emotion>\S+))?$"
    command_help = "用法：/eztts <文本> [-v 角色:预设] [-e 情绪]"
    intercept_message = True

    async def execute(self) -> Tuple[bool, str, bool]:
        try:
            text = (self.matched_groups.get("text") or "").strip()
            voice = (self.matched_groups.get("voice") or "").strip()
            emotion = (self.matched_groups.get("emotion") or "").strip()

            if not text:
                await self._send_error("请输入要转换为语音的文本")
                return False, "missing text", True

            max_length = int(self.get_config(ConfigKeys.GENERAL_MAX_TEXT_LENGTH, 120) or 120)
            clean_text = TTSTextUtils.clean_text(text, max_length)
            if not clean_text:
                await self._send_error("文本处理后为空")
                return False, "clean text empty", True
            if len(clean_text) > max_length:
                await self.send_text(clean_text[:max_length])
                return True, "too long, fallback to text", True

            backend = self._get_default_backend()
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
    command_description = "发送 test.wav（诊断 NapCat/MaiBot 语音发送链路）"
    command_pattern = r"^/test$"
    command_help = "用法：/test"
    intercept_message = True

    async def execute(self) -> Tuple[bool, str, bool]:
        try:
            plugin_dir = Path(__file__).resolve().parent
            wav_path = plugin_dir / "test.wav"
            if not wav_path.exists():
                await self.send_text("插件目录下未找到 test.wav")
                return False, "missing test.wav", True

            # 方式1：voiceurl（本地路径）
            try:
                ok = await self.send_custom(message_type="voiceurl", content=str(wav_path))
                if ok:
                    return True, "sent voiceurl test.wav", True
            except Exception as e:
                logger.error(f"{self.log_prefix} /test send voiceurl failed: {e}")

            # 方式2：voice(base64)
            try:
                import base64

                encoded = base64.b64encode(wav_path.read_bytes()).decode("ascii")
                ok2 = await self.send_custom(message_type="voice", content=encoded)
                return ok2, "sent voice(base64) test.wav" if ok2 else "send voice(base64) failed", True
            except Exception as e:
                logger.error(f"{self.log_prefix} /test send voice(base64) failed: {e}")
                return False, f"/test failed: {e}", True

        except Exception as e:
            await self.send_text(f"/test 失败: {e}")
            return False, str(e), True


@register_plugin
class EasyttsPluginPlugin(BasePlugin):
    plugin_name = "EasyttsPlugin"
    plugin_description = "GPT-SoVITS 推理特化库 + 魔搭社区免费托管的语音合成插件（支持按情绪生成语音）"
    plugin_version = "0.2.0"
    plugin_author = "yunchenqwq"
    enable_plugin = True
    config_file_name = "config.toml"
    dependencies = []
    python_dependencies = ["aiohttp"]

    config_section_descriptions = {
        "plugin": {
            "title": "插件基本配置",
            "description": "提示：请不要在 bot 自己的 WebUI 中编辑配置文件；请直接编辑 config.toml（WebUI 不会显示 TOML 注释，容易改错）。",
            "order": -100,
        },
        "general": "通用设置",
        "components": "组件启用控制",
        "probability": "概率控制（自由模式才有意义）",
        "easytts": "EasyTTS（ModelScope Studio / Gradio）与云端仓库池",
    }

    config_schema = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="0.2.0", description="配置版本"),
            "tips": ConfigField(
                type=str,
                default="请不要在 bot 自己的 WebUI 中编辑配置文件，请打开文件编辑。",
                description="提示（建议只读）",
                input_type="textarea",
                rows=4,
                disabled=True,
                order=-999,
            ),
        },
        "general": {
            "tts_mode": ConfigField(
                type=str,
                default="free",
                choices=["free", "fixed"],
                description="TTS 模式：free=自由模式（LLM 决定是否用语音）；fixed=固定模式（逐句翻译并逐句发送语音）。",
            ),
            "default_backend": ConfigField(type=str, default="easytts", description="默认后端（仅 easytts）"),
            "timeout": ConfigField(type=int, default=60, description="请求超时（秒）"),
            "max_text_length": ConfigField(type=int, default=120, description="最大文本长度（语音建议更短）"),
            "use_replyer_rewrite": ConfigField(
                type=bool,
                default=False,
                description="是否允许在缺少 text 时调用 replyer 兜底生成语音文案（默认关闭）",
            ),
            "audio_output_dir": ConfigField(type=str, default="", description="音频输出目录（留空使用项目根目录）"),
            "use_base64_audio": ConfigField(type=bool, default=False, description="是否使用 base64 方式发送音频"),
            "split_delay": ConfigField(type=float, default=0.0, description="固定模式逐句发送间隔（秒）"),
            "send_error_messages": ConfigField(type=bool, default=True, description="失败时是否发送错误提示"),
            "send_text_along_with_voice": ConfigField(
                type=bool,
                default=True,
                description="是否先发送文字再发送语音（推荐开启：避免文本/语音不一致）",
            ),
            "voice_translate_to": ConfigField(
                type=str,
                default="ja",
                description="语音合成前是否把文字翻译到指定语言（默认 ja=日语；留空/none/off 表示不翻译）",
            ),
            "force_text_language": ConfigField(
                type=str,
                default="zh",
                choices=["zh", ""],
                description="强制“发出去的文字”语言：zh=始终发中文（避免把日语译文直接发出去）；留空=不强制",
            ),
            "fixed_mode_infer_emotion": ConfigField(
                type=bool,
                default=True,
                description="固定模式下是否逐句调用 LLM 判断情绪（用于 emotion->preset 映射）",
            ),
        },
        "components": {
            "action_enabled": ConfigField(type=bool, default=True, description="是否启用 Action"),
            "command_enabled": ConfigField(type=bool, default=True, description="是否启用 Command"),
        },
        "probability": {
            "enabled": ConfigField(type=bool, default=False, description="是否启用概率控制"),
            "base_probability": ConfigField(type=float, default=1.0, description="触发概率（0~1）"),
            "keyword_force_trigger": ConfigField(type=bool, default=True, description="关键词强制触发"),
            "force_keywords": ConfigField(type=list, default=["一定要用语音", "必须语音", "语音回复"], description="强制触发关键词"),
        },
        "easytts": {
            "default_character": ConfigField(type=str, default="sagiri", description="默认角色（character）"),
            "default_preset": ConfigField(type=str, default="普通", description="默认预设（preset）"),
            "characters": ConfigField(
                type=list,
                default=[
                    {
                        "name": "mika",
                        "presets": ["普通", "开心", "伤心", "生气", "害怕", "害羞", "惊讶", "认真", "疑问", "痛苦", "百感交集释然"],
                    },
                    {
                        "name": "sagiri",
                        "presets": ["普通", "开心", "伤心", "生气", "疑问", "惊讶", "百感交集的释然"],
                    },
                ],
                description="可用角色列表（用于提示与校验）",
                input_type="json",
            ),
            "emotion_preset_map": ConfigField(
                type=dict,
                default={
                    "普通": "普通",
                    "开心": "开心",
                    "伤心": "伤心",
                    "生气": "生气",
                    "害怕": "普通",
                    "害羞": "普通",
                    "惊讶": "惊讶",
                    "认真": "普通",
                    "疑问": "疑问",
                    "痛苦": "普通",
                    "百感交集释然": "百感交集的释然",
                },
                description="全局情绪 -> 预设名映射（用于按情绪回复）",
                input_type="json",
            ),
            "remote_split_sentence": ConfigField(type=bool, default=True, description="是否让远端也进行分句合成"),
            "prefer_idle_endpoint": ConfigField(type=bool, default=True, description="优先选择空闲仓库（queue_size 低）"),
            "busy_queue_threshold": ConfigField(type=int, default=0, description="队列繁忙阈值（>此值视为忙）"),
            "status_timeout": ConfigField(type=int, default=3, description="queue/status 超时（秒）"),
            "join_timeout": ConfigField(type=int, default=30, description="queue/join 超时（秒）"),
            "sse_timeout": ConfigField(type=int, default=120, description="queue/data SSE 超时（秒）"),
            "download_timeout": ConfigField(type=int, default=120, description="音频下载超时（秒）"),
            "trust_env": ConfigField(type=bool, default=False, description="aiohttp 是否继承系统代理"),
            "endpoints": ConfigField(
                type=list,
                default=[{"name": "default", "base_url": "", "studio_token": "", "fn_index": 3, "trigger_id": 19}],
                description="云端仓库池（多个 endpoints 自动切换）",
                input_type="json",
            ),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        components: List[Tuple[ComponentInfo, Type]] = []

        if self.get_config(ConfigKeys.COMPONENTS_ACTION_ENABLED, True):
            mode = str(self.get_config(ConfigKeys.GENERAL_TTS_MODE, "free") or "free").strip().lower()
            action_cls = UnifiedTTSActionFixed if mode == "fixed" else UnifiedTTSAction
            components.append((action_cls.get_action_info(), action_cls))

        if self.get_config(ConfigKeys.COMPONENTS_COMMAND_ENABLED, True):
            components.append((UnifiedTTSCommand.get_command_info(), UnifiedTTSCommand))
            components.append((EasyttsTestCommand.get_command_info(), EasyttsTestCommand))

        return components

