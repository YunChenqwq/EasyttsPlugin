# EasyttsPlugin（MaiBot QQ 机器人语音合成插件）

本仓库根目录就是插件目录（克隆下来即可直接作为 MaiBot 插件使用）。

功能：
- 命令：`/eztts` 文本转语音
- Action：关键词触发自动语音回复（可调用 LLM 生成/润色回复，再转语音）
- 云端仓库池：配置多个 `endpoints`，根据队列忙碌度/失败自动切换
- 情绪预设：`emotion -> preset` 映射（用于“按情绪回复”）

## 1) 安装

把本仓库克隆到 MaiBot 的插件目录（与其它插件同级）。示例：

```bash
cd <你的MaiBot项目>/plugins
git clone https://github.com/YunChenqwq/EasyttsPlugin.git
```

克隆完成后应满足：
- `<你的MaiBot项目>/plugins/EasyttsPlugin/plugin.py` 存在

依赖：
- Python 包：`aiohttp`

## 2) 配置（必须）

编辑 `config.toml`。

### 2.1 endpoints（云端仓库池）

至少填 1 个：
- `base_url`: 你的 WebUI 部署域名（例如 `https://xxx.ms.show`）
- `studio_token`: 浏览器抓包拿到（敏感信息，不要泄露）
- `fn_index` / `trigger_id`: 对应 Gradio 按钮函数索引（你之前抓包是 `fn_index=3`、`trigger_id=19`）

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

### 2.2 角色与情绪预设（按情绪回复）

- `easytts.characters`: 维护“角色(模型) -> 可用预设(preset)”列表
- `[easytts.emotion_preset_map]`: 把 `emotion` 映射到你的云端 `preset` 名称

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
- 如果你只写 `-v mika`（或不写 `-v`）并且传了 `-e 伤心`，会自动选 preset=伤心。

## 3) 使用

### 3.1 命令（只保留 /eztts）

- `/eztts 你好世界`
- `/eztts 今天天气不错 -v mika:普通`
- `/eztts 我有点难过 -v mika -e 伤心`

参数：
- `-v`: `角色:预设` 或仅 `角色`
- `-e`: 情绪（会映射为 preset）

### 3.2 Action 自动触发（关键词）

当用户消息包含 “语音/朗读/说出来/tts/voice/speak ……” 等关键词时会触发 Action：
- 插件可调用 `generator_api.generate_reply(...)` 生成/润色回复，再转语音发送
- 若回复文本超长，会降级为文字回复

## 4) 如何抓 studio_token / fn_index / trigger_id

在浏览器打开你的 ms.show WebUI：
1. F12 -> Network
2. 触发一次合成
3. 找请求：`/gradio_api/queue/join`
4. 请求里能看到：
   - `studio_token`
   - body 里有 `fn_index` / `trigger_id`

## 5) 排错

- 合成超时：改 `easytts.sse_timeout`（秒）
- Windows 代理：如果你启用了系统代理，git/网络请求可能需要显式走代理（视运行环境而定）

## 6) 许可证（重要）

本插件参考并改造自 `xuqian13/tts_voice_plugin`，按 **AGPL-3.0** 分发：
- 详见 `LICENSE` 与 `NOTICE.md`

