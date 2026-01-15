# EasyttsPlugin

这是一个 MaiBot 插件，用于调用你部署在 ModelScope Studio / Gradio 上的 easytts（Genie-TTS 推理）来做文本转语音，并支持：
- 云端仓库池（多 endpoint 自动切换/失败切换）
- 按情绪选择预设（emotion -> preset 映射）

仓库目录结构：
- `EasyttsPlugin/`：插件本体（放到 MaiBot 的 plugins 目录）

## 1. 安装到 QQ 机器人（MaiBot）

1) 复制插件目录
- 把本仓库里的 `EasyttsPlugin/` 整个目录复制到你的 MaiBot 插件目录（与其他插件同级）。
- 例如：`<MaiBot>/plugins/EasyttsPlugin/` 下面应当存在 `plugin.py`。

2) 安装依赖
- 本插件依赖 `aiohttp`。

3) 重启 MaiBot
- 重启后插件会被自动加载（以 MaiBot 的插件机制为准）。

## 2. 配置（必看）

编辑 `EasyttsPlugin/config.toml`。

### 2.1 配置云端仓库池 endpoints
你需要至少配置一个 endpoint：
- `base_url`：你的 ms.show 域名（例如 `https://xxx.ms.show`）
- `studio_token`：从浏览器抓包得到的 token
- `fn_index` / `trigger_id`：对应 WebUI 的“生成语音”按钮函数索引（你之前抓包得到的是 `fn_index=3`、`trigger_id=19`）

示例：
```toml
[[easytts.endpoints]]
name = "pool-1"
base_url = "https://xxx.ms.show"
studio_token = "..."
fn_index = 3
trigger_id = 19

[[easytts.endpoints]]
name = "pool-2"
base_url = "https://yyy.ms.show"
studio_token = "..."
fn_index = 3
trigger_id = 19
```

说明：
- 插件会优先选择 `queue_size` 更小的仓库；当某个仓库繁忙/失败时自动切换到下一个。

### 2.2 配置角色与情绪预设（按情绪回复）

- `easytts.characters` 用于维护“有哪些角色/模型”和“该角色有哪些预设”。
- `[easytts.emotion_preset_map]` 用于把 emotion 映射为你的云端 preset 名称。

示例（mika）：
```toml
[[easytts.characters]]
name = "mika"
presets = ["普通","开心","伤心","生气","害怕","害羞","惊讶","认真","疑问","痛苦","百感交集释然"]

[easytts.emotion_preset_map]
普通 = "普通"
开心 = "开心"
伤心 = "伤心"
生气 = "生气"
害怕 = "害怕"
害羞 = "害羞"
惊讶 = "惊讶"
认真 = "认真"
疑问 = "疑问"
痛苦 = "痛苦"
百感交集释然 = "百感交集释然"
```

规则：
- 如果你在命令里显式写了 `-v mika:普通`，则不会被 `-e` 覆盖。
- 如果你只写 `-v mika` 或不写 `-v`，并且传了 `-e 伤心`，则会自动把 preset 选成 `伤心`。

## 3. 使用方法

### 3.1 命令（只保留 /eztts）
- `/eztts 你好世界`
- `/eztts 今天天气不错 -v mika:普通`
- `/eztts 我有点难过 -v mika -e 伤心`

参数说明：
- `-v`：`角色:预设`，或只写 `角色`
- `-e`：情绪（会映射为 preset）

### 3.2 Action 自动触发
- 当用户消息包含“语音/朗读/说出来/tts/voice/speak”等关键词时，Action 会被触发。
- 插件会调用 LLM（`generator_api.generate_reply`）生成/润色回复文本，再转语音发送。
- 如果回复文本超长，会降级为文字回复。

## 4. 如何获取 studio_token / fn_index / trigger_id

你可以在浏览器打开你的 ms.show WebUI，按 F12 -> Network：
- 找 `/gradio_api/queue/join` 请求
- query 或 header 里能看到 `studio_token`
- request body 里能看到 `fn_index` / `trigger_id`

## 5. 排错

- 合成超时：调整 `easytts.sse_timeout`（默认 120 秒）
- Git/网络：如果你机器开了系统代理，git 可能需要显式设置 `http.proxy/https.proxy`。

## 6. 开源协议（重要）

本插件参考并改造自 `xuqian13/tts_voice_plugin`，因此本仓库按 **AGPL-3.0** 分发。
- 详见：`LICENSE` 与 `EasyttsPlugin/NOTICE.md`
