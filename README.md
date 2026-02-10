# EasyPlugin（MaiBot 插件）

基于 **GPT-SoVITS 推理特化库（Genie-TTS / GPT-SoVITS ONNX 推理引擎）** 的 WebUI（ModelScope Studio / Gradio，ms.show 免费托管）实现语音合成，并提供 **云端仓库池自动切换** 与 **按情绪/预设生成语音** 的 MaiBot 插件。

推荐先看使用教程视频（含安装/配置/演示）：  
https://www.bilibili.com/video/BV1tqFPzCEfm/

## 整合包下载 / 答疑

- 答疑QQ群：`653488716`
- 转换模型一键上传整合包：进群下载（约 200MB）
- 插件一键上传整合包（百度网盘）：`https://pan.baidu.com/s/1DWkFq2qlqPLyUkLUrdSh0g?pwd=u59y`
- 没有度盘会员：进群下载（约 200MB）

## 魔搭社区（ModelScope）地址（推荐用魔搭社区 WebUI/仓库上传模型）

- 模板 Space（推荐复制）：`https://www.modelscope.cn/studios/YunChenqwq/easytts-template/summary`
- easytts Space：`https://www.modelscope.cn/studios/YunChenqwq/easytts/summary`

## 功能概览

- 自动语音回复（Action）：动作在 Planner 中始终可选，由规划器/LLM 决定是否调用语音
- 手动命令：`/eztts`
- 诊断命令：`/test`（发送插件目录自带 `test.wav`）
- 云端仓库池：配置多个 `endpoints`，优先选更空闲的仓库；失败自动切换
- 按情绪/预设：**emotion 参数直接等同 preset 名**（不做任何映射/同义词转换）
- 语音文本翻译：可把“要发出去的文字”翻译成日语后再合成语音，并强制避免把日语译文发到聊天里（防止“文本/语音不一致”）

---

## 1. 安装（Windows / MaiBotOneKey）

### 方式 A：MaiBot WebUI 插件市场安装（推荐）

1) 打开 MaiBot WebUI → **插件市场**
2) 搜索：`tts`
3) 找到本插件：**EasyPlugin / yunchenqwq.easy_plugin**
4) 点击安装 → 等待完成
5) 重启 MaiBot（或在 WebUI 里重载插件）

### 方式 B：手动安装（离线/无法访问插件市场）

1) 下载/克隆本仓库  
2) 把整个目录放到你的 MaiBot 插件目录，例如：

```
E:\bot\MaiBotOneKey\modules\MaiBot\plugins\EasyPlugin\
```

提示：
- 推荐把插件目录命名为 `EasyPlugin`（之前旧版本叫 `EasyttsPugin`，是拼写错误；旧目录名仍可用，但建议改掉）。
- 插件依赖在 `requirements.txt` 中，MaiBot 加载插件时通常会自动安装；如果没装上请手动 `pip install -r requirements.txt`。

3) 重启 MaiBot（或在 WebUI 里重载插件）

---

## 2. 配置（config.toml）

配置文件：`EasyPlugin/config.toml`

推荐方式（更省事）：在 MaiBot WebUI 里可视化编辑配置（本插件已做成表单，不需要写 JSON）。

你也可以直接编辑 `config.toml`（高级用法），但注意：WebUI 保存配置时可能会重排格式/移除 TOML 注释，这属于 WebUI 的正常行为。

### 2.0 推荐配置流程（给小白）

1) 先只填写仓库池 1（`endpoint_1_base_url` + `endpoint_1_studio_token` + `fn_index/trigger_id`）
2) 重启 MaiBot（或重载插件）
3) 插件启动后会自动抓取云端 WebUI 的角色/情绪下拉，并**回写**到你的 `config.toml`（角色槽位与默认角色/默认预设会自动填好）

### 2.1 云端仓库池（必填，推荐用 WebUI 填）

仓库池是“固定 5 个槽位”的表单字段（建议至少填 2 个，方便繁忙时自动切换）：

```toml
endpoint_1_name = "pool-1"
endpoint_1_base_url = "https://xxx.ms.show"
endpoint_1_studio_token = "你的studio_token"
endpoint_1_fn_index = 4
endpoint_1_trigger_id = 25

endpoint_2_name = "pool-2"
endpoint_2_base_url = "https://yyy.ms.show"
endpoint_2_studio_token = "你的studio_token"
endpoint_2_fn_index = 3
endpoint_2_trigger_id = 19
```

如何获取 `studio_token`（二选一即可）：
- 浏览器打开你的 ms.show 页面 → 按 F12 → Network → 找到 `gradio_api/queue/join` 请求 → 请求头里有 `x-studio-token` 或 Cookie 里有 `studio_token`
- 或者 Application/Storage → Cookies → 找到 `studio_token`

`fn_index` / `trigger_id`：
- 通常可以在 `gradio_api/queue/join` 的请求体里看到（并且会随着 WebUI 版本/组件改动而变化）。
- 如果你使用我提供的 `easytts-template` 模板 Space：目前常见是 `fn_index=4`、`trigger_id=25`，但仍以你实际抓到的为准。

### 2.2 角色与预设（情绪/预设列表，推荐用 WebUI 填）

角色/预设同样是“固定 5 个槽位”的表单字段。

推荐做法：**留空**，让插件启动后自动抓取并回写到 `config.toml`。

你也可以手动填（高级/离线模式）。示例：

```toml
character_1_name = "mika"
character_1_presets = "普通,开心,伤心"
```

规则（很重要）：
- 本插件 **不做任何映射**：`emotion` 的值会被当作 **preset 名** 直接使用。
- 所以你传入的 `emotion` 必须是该角色真实存在的 preset（否则会回退到 `easytts.default_preset`）。

自动抓取（推荐开启）：
- `easytts.auto_fetch_gradio_schema = true` 时，插件启动会从你的 endpoints 自动抓取 “角色下拉 + 每个角色的 preset 下拉”。
- 抓取成功后会同时：
  - 写入 `_gradio_schema_cache.json` 缓存
  - **回写到 `config.toml` 的 `character_1_name/character_1_presets ...` 槽位字段**（让小白在 WebUI 里能直接看到最新角色/情绪，不需要手动填）
- 如果你更新了云端模型/预设，但插件没更新：删除插件目录下的 `_gradio_schema_cache.json` 后重启即可强制刷新。

### 2.3 两种模式：free / fixed（推荐分别设置群聊/私聊）

推荐使用（可分别设置群聊/私聊）：

- `general.tts_mode_group`：群聊模式（推荐 `free`）
- `general.tts_mode_private`：私聊模式（推荐 `free`）

说明：

- `free`（推荐/默认）：更自然。是否用语音由 LLM 决定；一条用户消息最多调用一次 action（一个消息一个语音）。并且会自动从该角色可用 preset 中选择更合适的语气（尽量避免一直“普通”）。
- `fixed`：更密集。一旦触发，会把回复分句，并对 **每句** 单独生成语音并发送（适合你想“每句都发语音”的场景）。

旧字段 `general.tts_mode` 仍可用，但在配置了 `tts_mode_group/tts_mode_private` 时会被覆盖。

### 2.4 让“文字/语音一致”的关键设置

默认逻辑：
- “发出去的文字”：来自 action 的 `text`
- “合成语音的文本”：可由插件把 `text` 翻译成日语后再合成（避免中文模型读日语/日语模型读中文的问题）

相关配置：
- `general.voice_translate_to`：默认 `auto`（推荐中文模型）  
  - `auto` / `off`：不翻译，直接用原文合成（推荐中文模型/中文角色）  
  - `ja`：把文字翻译成日语再合成语音（适合日语模型/日语角色）  
  - `zh` / `en`：把文字翻译成中文/英文后再合成语音（按你的模型需求选择）  
- `general.force_text_language`：默认 `zh`  
  - 用于避免 LLM 把日语译文发到聊天里（如果检测到 text 是日语，会再翻译回中文作为“发出去的文字”）

另外：
- `general.send_error_messages`：默认 `false`（仅写入日志，不在聊天里刷屏）。如需把“合成失败/网络错误”等提示直接发给用户，再改为 `true`。

### 2.5 语音发送方式（NapCat 兼容）

- `general.use_base64_audio = false`（默认）：生成 wav 文件后，用本地路径发送（通常更稳定/占用更小）
- 如果你环境不接受本地路径：把它改成 `true`，插件会用 base64 发送音频

---

## 3. 使用方法

### 3.1 手动命令：/eztts

语法：

```
/eztts <文本> [-v <角色:预设>] [-e <情绪/预设>]
```

示例：
- `/eztts 你好世界`
- `/eztts 今天天气不错 -v mika:普通`
- `/eztts 我有点难过 -v mika -e 伤心`

参数细节（避免踩坑）：
- `-v mika:普通`：显式指定 preset，此时 `-e` 会被忽略
- `-v mika -e 伤心`：只指定角色 + 用 `-e` 指定 preset（推荐这种写法，语义更清晰）
- `-e` 的值必须在该角色 `presets` 里（或能被自动抓取到的 preset 下拉中找到）

### 3.2 诊断命令：/test

`/test` 会发送插件目录下的 `test.wav`，用于排查：
- NapCat / OneBot 适配器是否能正常发送语音
- 如果 `/test` 都发不出去，优先检查底层（适配器/权限/消息段支持），而不是 TTS 合成逻辑

### 3.3 自动语音回复（Action）

1) 打开 `config.toml`，确保：
```toml
[components]
action_enabled = true
```

2) 选择模式：
```toml
[general]
tts_mode_group = "free"   # 或 "fixed"
tts_mode_private = "free" # 或 "fixed"
```

3) 让 LLM 会用（很重要）：  
插件提供的 action 名为 `unified_tts_action`。你需要在 bot 的提示词/规划器说明中明确：
- 什么时候应该用语音（例如用户明确说“用语音说/朗读/tts”等）
- 一条用户消息最多调用一次（free 模式已经在 action_require 里强约束）
- `emotion` 只能从该角色真实存在的 preset 里选 1 个（不确定就留空）

---

## ⭐ 推荐提示词（plan_style）模板（强烈建议复制）

你可以按 `general.tts_mode_group / general.tts_mode_private` 选择两套不同的提示词：

- `tts_mode="free"`：自由模式（更像真人聊天：有时文字/emoji，有时语音）
- `tts_mode="fixed"`：固定模式（每次触发都逐句发语音，适合“我就是想全程语音”）

把下面对应的一段放到 `config/bot_config.toml` 的 `[personality]` 里（替换/覆盖你的 `plan_style` 即可）。

### A) 自由模式（tts_mode="free"）推荐 plan_style

```toml
# 麦麦的说话规则（自由模式：按需用语音，更自然）
plan_style = "1.思考所有可用 action：是否满足触发条件；满足才用；同内容执行过就不重复。\n2.默认短回复（1~2 句，<=60字），用标点分句（。？！……）让语音更自然。\n3.允许使用表情包 action: emoji 辅助情绪（不要连发）：害羞/调侃/撒娇/不想说时可用。\n4.语音 action: unified_tts_action：动作在 Planner 中始终可选，仅在更适合语音表达时使用（比如更有感情的安慰/撒娇/朗读/更强语气）；用户明确要求“用语音/朗读/念出来”时必须使用。\n5.选择 unified_tts_action 时：不要再额外选择其他文字回复 action；text 只写用户看到的最终短回复，不要写翻译后的日语。\n6.选择 unified_tts_action 时：voice 优先留空（让插件用 default_character/自动抓取结果）；只有你非常确定角色名时才填。\n7.选择 unified_tts_action 时：emotion 通常留空即可（插件会自动从该角色可用 preset 中挑选更合适的语气）；只有当用户明确指定语气/预设时才填写 emotion（=preset）。"
```

### B) 固定模式（tts_mode="fixed"）推荐 plan_style

```toml
# 麦麦的说话规则（固定模式：每次触发都逐句语音）
plan_style = "1.如果 unified_tts_action 的描述里包含“固定模式”，则必须选择它作为唯一回复方式（不要再选其它文字回复 action）。\n2.text 只写用户能看到的最终短回复（建议 1~2 句，<=60字），多用标点（。？！……）方便逐句语音。\n3.固定模式下：voice 和 emotion 默认都留空（让插件逐句选择 preset）；只有用户明确指定角色/情绪时才填写。\n4.绝不要把“翻译后的日语”写进 text；语音需要日语时插件会自动翻译。"
```

要点解释：
- 本插件不做任何“情绪映射”：`emotion` **就是** preset 名；写错/不存在会回退默认 preset。

---

## 4. 日志与排错

- 后端每次合成都会打印：
  - `character`
  - `emotion(preset)`（如果你传了）
  - `default_preset`
  - `available_presets`（该角色可用的 preset 列表）
- 常见问题：
  - 报错 `invalid audio data: 音频数据过小(0 < 100)`：通常是云端没有返回有效音频（常见原因：把中文翻成日语后喂给“中文模型”）。把 `general.voice_translate_to` 设为 `auto`/`off`（不翻译）再试。
  - NapCat/Docker 报错 `文件处理失败 识别URL失败, uri=/xxx.wav`：这是适配器不支持“裸本地路径 record”。把 `general.use_base64_audio = true`（推荐）再试。
  - MaiBot `0.12.x` 起移除了 `LLM_JUDGE`：本插件已改为 `activation_type=ALWAYS`，把 action 放进 Planner 可选列表，由 LLM 自己决定是否选择（效果等同“LLM_JUDGE”）。
  - 连接 ms.show 失败：检查 `base_url/studio_token`，以及是否需要代理；默认 `easytts.trust_env=false` 不走系统代理
  - 传了 emotion 但没生效：确认你没有用 `-v 角色:预设` 显式锁定 preset；并确认 emotion 值在 preset 列表里

---

## 开源协议

本项目按 **AGPL-3.0** 分发，详见 `LICENSE` 与 `NOTICE.md`。
