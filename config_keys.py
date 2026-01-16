"""
配置键常量定义（集中管理，避免硬编码）。
"""


class ConfigKeys:
    # Plugin
    PLUGIN_ENABLED = "plugin.enabled"
    PLUGIN_CONFIG_VERSION = "plugin.config_version"

    # General
    # 注意：TOML 的中文 key 需要写成 `"中文" = ...`（必须加引号）
    GENERAL_DEFAULT_BACKEND = "general.默认后端"
    GENERAL_TIMEOUT = "general.超时"
    GENERAL_MAX_TEXT_LENGTH = "general.最大文本长度"
    GENERAL_USE_REPLYER_REWRITE = "general.使用LLM润色"
    GENERAL_AUDIO_OUTPUT_DIR = "general.音频输出目录"
    GENERAL_USE_BASE64_AUDIO = "general.使用Base64语音"
    GENERAL_SPLIT_SENTENCES = "general.分句发送"
    GENERAL_SPLIT_DELAY = "general.分句间隔"
    GENERAL_SEND_ERROR_MESSAGES = "general.发送错误提示"

    # Components
    COMPONENTS_ACTION_ENABLED = "components.启用Action"
    COMPONENTS_COMMAND_ENABLED = "components.启用命令"

    # EasyTTS
    EASYTTS_ENDPOINTS = "easytts.云端仓库池"
    EASYTTS_DEFAULT_CHARACTER = "easytts.默认角色"
    EASYTTS_DEFAULT_PRESET = "easytts.默认预设"
    # “模型/角色”列表（用于 UI/LLM 提示与后端校验）
    EASYTTS_CHARACTERS = "easytts.角色列表"
    EASYTTS_REMOTE_SPLIT_SENTENCE = "easytts.远端分句合成"
    EASYTTS_PREFER_IDLE_ENDPOINT = "easytts.优先空闲仓库"
    EASYTTS_BUSY_QUEUE_THRESHOLD = "easytts.繁忙阈值"
    EASYTTS_STATUS_TIMEOUT = "easytts.状态超时"
    EASYTTS_JOIN_TIMEOUT = "easytts.加入队列超时"
    EASYTTS_SSE_TIMEOUT = "easytts.SSE超时"
    EASYTTS_DOWNLOAD_TIMEOUT = "easytts.下载超时"
    EASYTTS_TRUST_ENV = "easytts.继承系统代理"


# 兼容旧英文 key（老配置不用改也能跑）
CONFIG_KEY_ALIASES = {
    # General
    ConfigKeys.GENERAL_DEFAULT_BACKEND: "general.default_backend",
    ConfigKeys.GENERAL_TIMEOUT: "general.timeout",
    ConfigKeys.GENERAL_MAX_TEXT_LENGTH: "general.max_text_length",
    ConfigKeys.GENERAL_USE_REPLYER_REWRITE: "general.use_replyer_rewrite",
    ConfigKeys.GENERAL_AUDIO_OUTPUT_DIR: "general.audio_output_dir",
    ConfigKeys.GENERAL_USE_BASE64_AUDIO: "general.use_base64_audio",
    ConfigKeys.GENERAL_SPLIT_SENTENCES: "general.split_sentences",
    ConfigKeys.GENERAL_SPLIT_DELAY: "general.split_delay",
    ConfigKeys.GENERAL_SEND_ERROR_MESSAGES: "general.send_error_messages",
    # Components
    ConfigKeys.COMPONENTS_ACTION_ENABLED: "components.action_enabled",
    ConfigKeys.COMPONENTS_COMMAND_ENABLED: "components.command_enabled",
    # EasyTTS
    ConfigKeys.EASYTTS_ENDPOINTS: "easytts.endpoints",
    ConfigKeys.EASYTTS_DEFAULT_CHARACTER: "easytts.default_character",
    ConfigKeys.EASYTTS_DEFAULT_PRESET: "easytts.default_preset",
    ConfigKeys.EASYTTS_CHARACTERS: "easytts.characters",
    ConfigKeys.EASYTTS_REMOTE_SPLIT_SENTENCE: "easytts.remote_split_sentence",
    ConfigKeys.EASYTTS_PREFER_IDLE_ENDPOINT: "easytts.prefer_idle_endpoint",
    ConfigKeys.EASYTTS_BUSY_QUEUE_THRESHOLD: "easytts.busy_queue_threshold",
    ConfigKeys.EASYTTS_STATUS_TIMEOUT: "easytts.status_timeout",
    ConfigKeys.EASYTTS_JOIN_TIMEOUT: "easytts.join_timeout",
    ConfigKeys.EASYTTS_SSE_TIMEOUT: "easytts.sse_timeout",
    ConfigKeys.EASYTTS_DOWNLOAD_TIMEOUT: "easytts.download_timeout",
    ConfigKeys.EASYTTS_TRUST_ENV: "easytts.trust_env",
}


_MISSING = object()


def get_config_with_aliases(getter, key: str, default=None):
    """
    从配置读取 key；若新中文 key 取不到，则回退读取旧英文 key。
    getter 形如：getter(key, default) -> value
    """
    v = getter(key, _MISSING)
    if v is not _MISSING:
        return v
    alt = CONFIG_KEY_ALIASES.get(key)
    if alt:
        v2 = getter(alt, _MISSING)
        if v2 is not _MISSING:
            return v2
    return default
