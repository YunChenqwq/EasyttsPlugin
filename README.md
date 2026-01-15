# EasyttsPlugin（MaiBot QQ 机器人语音合成插件）

本仓库根目录就是插件目录（克隆下来即可直接作为 MaiBot 插件使用）。

功能：
- 命令：`/eztts` 文本转语音
- Action：自动语音回复（支持两种模式：自由模式=LLM 决定是否用语音；固定模式=逐句语音）
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

### 2.0 模式选择（重要）

在 `[general]` 里设置：
- `tts_mode = "free"`：自由模式（LLM 不会每次都用语音；更自然）
- `tts_mode = "fixed"`：固定模式（每次回复逐句发语音；每句都会调用 LLM 翻译 + 判断情绪）

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
"普通" = "普通"
"开心" = "开心"
"伤心" = "伤心"
"生气" = "生气"
"害怕" = "普通"
"害羞" = "普通"
"惊讶" = "惊讶"
"认真" = "普通"
"疑问" = "疑问"
"痛苦" = "普通"
"百感交集释然" = "百感交集释然"
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

### 3.2 Action 自动触发

本插件的 Action 名称固定为 `unified_tts_action`，但触发方式取决于 `general.tts_mode`：
- `free`：由 LLM 决定是否触发（LLM_JUDGE）；**不会**每次都用语音
- `fixed`：固定触发（ALWAYS）；每次回复会按标点分句，逐句翻译并逐句发送语音（并逐句判断情绪）

自由模式下，为了避免“文字/语音不匹配”，建议在你的 Bot 提示词里约束：
- 如果选择 `unified_tts_action`，就不要再额外选择其他文字回复 action

按情绪回复（Action 场景）：
- 如果你的 LLM/工作流支持给 Action 传参，可以把 `emotion` 一起传入（例如：`开心`/`伤心`/`生气`…），后端会按 `[easytts.emotion_preset_map]` 自动选 preset
- 情绪值建议从 `easytts.emotion_preset_map` 的 key 里选（或在该映射里补齐同义词），否则会回退到默认 preset
- 优先级：当 `voice` 显式指定为 `角色:预设` 时（例如 `mika:普通`），不会被 `emotion` 覆盖

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
