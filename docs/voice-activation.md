# 语音激活

未绑定设备除了拿到 6 位激活码，还会拿到 `activation_type=voice`。设备连上语音服务后，由服务端把手机号播报给用户确认，再用阿里云号码认证短信校验。校验通过后，用超级管理员新建一个智能体，并按激活码绑定设备，同时记下手机号。绑定成功后询问助手昵称；昵称确认后自动做默认技能绑定，再询问是否重启设备。

固件不在本仓库。设备侧对接见 [voice-activation-device.md](voice-activation-device.md)：OTA 返回 `activation_type=voice` 时连接 WebSocket 并对话，不要只把激活码展示成控制面板绑定。`activation_type` 不是 `voice` 时，仍播放「请登录控制面板，输入激活码」。

时序图：[architecture/voice-activation.drawio](architecture/voice-activation.drawio)。

## 范围

| 阶段 | 内容 | 本次 |
| --- | --- | --- |
| 第一阶段 激活 | 确认手机号、发短信、校验验证码、超级管理员建智能体并绑定设备 | 实现 |
| 第二阶段 角色绑定 | 只询问助手昵称，写入智能体 `agentName` | 实现 |
| 第三阶段 技能绑定 | 昵称确认后自动调 portal_lite 默认技能绑定，成功后询问是否重启设备 | 实现 |

控制台原有 `POST /device/bind/{agentId}/{deviceCode}` 保留，给非 voice 路径使用。一个智能体只能绑定一台设备，见下文「一机一体」。

## 流程

1. 设备不存在时，`POST /ota/` 仍生成 6 位激活码（Redis，24 小时），响应增加 `activation_type=voice`。激活码写入 `ota:activation:data:*` 时一并写入 `activation_type`。
2. 设备连接 `xiaozhi-server`。配置接口返回 10042 时，再查语音激活会话。类型为 `voice` 才进入语音状态机，并使用公共配置里的 ASR/TTS 听和说。
3. 请用户说手机号。从识别文本抽出 11 位号码（含中文数字）。不是合法手机号就重问。
4. 逐位播报号码，请用户说「确认」或「重说」。
5. 确认后调用发送接口。成功后请用户说出短信验证码。失败则播报原因，留在确认步骤，再说「确认」会重发。
6. 校验通过后：查找 `super_admin=1` 的用户（取 id 最小的一个），**新建**一个智能体（不按手机号查找已有智能体），用默认模板创建，初始名为「小智」。按激活码写入 `ai_device`，`user_id` 为该管理员，并保存手机号，`onboarding_status=nickname`。同一手机号再绑另一台设备时，再新建一个智能体。
7. 播报绑定成功，重新拉取该设备的私有配置。若 `voice_onboarding=nickname`，请用户说助手昵称，播报确认后写入 `agentName`，状态改为 `skills`。
8. 昵称确认后**不询问用户**，自动请求技能绑定（`POST /device/voice-activation/skills` → portal_lite）。成功后把状态改为 `done`，播报「技能已经绑定好了」，再问是否重启设备。用户确认后通过设备 MCP `reboot` 重启；拒绝则进入日常对话。失败保持 `skills`，播报原因，可说「确认/重试」。
9. 断线重连：已绑定且状态为 `nickname` 时只继续问昵称；为 `skills` 时只继续技能绑定与重启询问，不再走激活。

验证码阶段说「重说」会回到手机号。说「重发」会再发一条短信（仍受频控）。

## 数据

`ai_device` 新增：

| 字段 | 说明 |
| --- | --- |
| `phone` | 绑定手机号，可空。控制台手工绑定的旧设备为空。首页智能体卡片通过列表接口的 `phone` 字段展示，没有号码时显示「未记录」。右上角搜索同时匹配这个号码，支持片段，例如输入 `136` |
| `onboarding_status` | `nickname` 待设置昵称；`skills` 待技能绑定；`done` 已完成。空值视为已完成，避免老设备被追问 |

发送短信时，把已确认的手机号写入该设备的激活 Redis，校验时必须和请求里的手机号一致。

## 接口

以下接口使用与 `/config/**` 相同的服务端密钥（`Authorization: Bearer {server.secret}`），供 `xiaozhi-server` 调用。

`POST /device/voice-activation/session`

请求：`{ "macAddress": "..." }`

- 未绑定且激活码仍在：`{ "bound": false, "activationType": "voice" }`。没有 `activation_type` 的旧缓存视为 `code`。
- 已绑定：`{ "bound": true, "onboardingStatus": "nickname" | "skills" }`，没有引导任务时 `onboardingStatus` 为空。

`POST /device/voice-activation/sms`

请求：`{ "macAddress": "...", "phone": "13800138000" }`

设备必须尚未绑定，激活码必须有效，手机号为 `1` 开头的 11 位。

`POST /device/voice-activation/bind`

请求：`{ "macAddress": "...", "phone": "13800138000", "code": "123456" }`

校验短信后完成绑定。返回 `deviceId`、`agentId`。

`POST /device/voice-activation/nickname`

请求：`{ "macAddress": "...", "nickname": "小助" }`

仅当 `onboarding_status=nickname` 时更新智能体名称，然后把状态改为 `skills`。

`POST /device/voice-activation/skills`

请求：`{ "macAddress": "..." }`

仅当 `onboarding_status=skills` 时，由 manager-api 代理调用 portal_lite 默认技能绑定。成功后把状态改为 `done`。失败保持 `skills`，供重试或重连。

已绑定设备的 `POST /config/agent-models`：需要昵称时返回 `voice_onboarding=nickname`；需要技能绑定时返回 `voice_onboarding=skills`。

## 短信

登录注册继续用现有短信服务（`dysmsapi`）。语音激活单独用阿里云号码认证（`dypnsapi`）：

- `SendSmsVerifyCode`，`returnVerifyCode=false`，验证码由阿里云生成。
- `CheckSmsVerifyCode`，结果必须是 `PASS`。

智控台参数：

| 参数 | 说明 |
| --- | --- |
| `aliyun.dypns.access_key_id` | 为空时回退 `aliyun.sms.access_key_id` |
| `aliyun.dypns.access_key_secret` | 为空时回退 `aliyun.sms.access_key_secret` |
| `aliyun.dypns.sign_name` | 短信签名 |
| `aliyun.dypns.template_code` | 短信模板。模板变量使用 `##code##` |
| `aliyun.dypns.scheme_name` | 号码认证方案名，可空 |
| `aliyun.dypns.mock` | `true` 时不调用阿里云，验证码写在服务端日志里，供开发联调 |
| `portal_lite.base_url` | portal_lite 服务地址，例如 `http://127.0.0.1:5005` |
| `portal_lite.bind_token` | 对应 `X-Bind-Token` / `LITE_BIND_TOKEN` |

频控：同一手机号 60 秒内不可重发，每天最多 10 条，验证失败最多 5 次。未配置密钥且未开启 mock 时，接口返回「短信服务暂不可用」。

## 一机一体

每个智能体只绑定一台设备。同一个手机号绑定多台设备时，每台设备各有一个智能体。

语音激活的 `bind` 每次都调用 `createAgentForUser`，不按手机号复用智能体。设备侧不需要再改。

控制台 `POST /device/bind/{agentId}/{deviceCode}` 和手动添加设备走同一条限制：该智能体下已经有设备时拒绝，提示「该智能体已绑定设备，请新建智能体后再绑定」。已经绑了多台的历史数据不拆开，卡片上的手机号会用顿号拼在一起。

## 默认音色

未绑定设备的公共配置固定 `TTS_MimoTTS`。已绑定设备用智能体自己的音色。

2026-10-21 起，TTS 默认模型、智能体模板，以及仍指向 `TTS_HuoshanDoubleStreamTTS` 的已有智能体，都改为小米 MiMo 语音合成，音色为「冰糖」（`TTS_MimoTTS_0001`）。其它音色模型不受影响。变更在 `db/changelog/202610211150.sql`，随 manager-api 启动执行。

## 变更记录

### 2026-10-21

- 首页智能体卡片增加「绑定手机号」，数据来自该智能体已绑定设备的 `ai_device.phone`。右上角搜索同时按这个号码过滤，名称、标签、MAC 仍然有效。
- 核对一机一体：语音激活本来就是一台设备新建一个智能体，同一手机号不会合并。控制台绑定和手动添加补上「已有设备则拒绝」。
- 默认音色从火山引擎（流式）改为小米 MiMo 语音合成。模板和仍使用火山双流式的已有智能体一起切换，否则卡片仍显示火山。

### 2026-10-23

- 实现第三阶段技能绑定：昵称确认后 `onboarding_status=skills`，自动调 portal_lite `POST /api/device/bind`，成功后询问是否重启设备（设备 MCP `reboot`）。智控台参数 `portal_lite.base_url`、`portal_lite.bind_token` 见 `db/changelog/202610231600.sql`。

## 后续操作（本次不实现）

升级、更换手机号、解绑也走语音：先播报当前绑定手机号请用户确认，确认后发送短信，验证码通过后再执行变更。设备上的 `phone` 和号码认证校验就是为这些操作留的。本次只保存手机号并完成激活校验，不提供上述三条对话。

## 第三阶段

激活和昵称完成之前，不询问技能。昵称确认后的默认技能绑定为自动动作，**不需要和用户确认**。绑定成功后再询问是否重启设备；用户确认后重启。

### 对话顺序

| 步骤 | `onboarding_status` | 对话 |
| --- | --- | --- |
| 设备绑定成功 | `nickname` | 询问助手昵称 |
| 昵称确认写入 | `skills` | 自动请求技能绑定，不询问用户 |
| 技能绑定成功 | `done` | 播报「技能已经绑定好了。是否重启设备？」 |
| 用户确认重启 | `done` | 调用设备 MCP 重启工具（本固件为 `self.qiniu.custom.restart`；也匹配含 `reboot` / `restart` 的工具） |
| 用户拒绝重启 | `done` | 「好的，我们可以开始聊天了」，清引导 handler |

断线重连：`voice_onboarding=skills` 时只继续技能绑定与重启询问，不再问手机号或昵称。技能绑定失败时保持 `skills`，播报原因；用户可说「确认」重试，或「稍后再说」先聊天，之后再说「绑定技能 / 配置技能」重新进入。已是 `done` 的老设备不回填技能绑定。

部署注意：`portal_lite.base_url` 指向的服务必须包含 `POST /api/device/bind`。若只部署了旧版 portal_lite（有 `/api/health` 但无 bind 路由），会返回 404 HTML，语音侧会提示「技能服务接口不存在，请升级 portal_lite」。

### 外部接口（portal_lite）

技能服务在仓库外：`mcd_mcp` 的 `portal_lite`。manager-api 代理调用：

`POST {portal_lite.base_url}/api/device/bind`

鉴权：请求头 `X-Bind-Token: {portal_lite.bind_token}`。

请求体：

```json
{
  "device_name": "小助",
  "mac_address": "aa:bb:cc:dd:ee:ff",
  "agent_name": "小助",
  "endpoint_url": "ws://.../mcp/?token=...",
  "phone": "13800138000"
}
```

| 字段 | 来源 |
| --- | --- |
| `mac_address` | 设备 MAC |
| `phone` | `ai_device.phone` |
| `agent_name` / `device_name` | 刚写入的智能体昵称 |
| `endpoint_url` | 该智能体的 MCP 接入点地址（`AgentMcpAccessPointService.getAgentMcpAccessAddress`），供 mcd_mcp broker 建通道 |

成功响应含 `ok`、`device_id`、`agent_id`、`skill_id` 等。同 MAC 重复调用幂等。该接口创建空技能壳（`skill_<agent_id>`），不预填滴滴/瑞幸等凭证；凭证授权仍走门户配对，不在本阶段调用。

未配置 `portal_lite.base_url` / `portal_lite.bind_token`，或智能体 MCP 接入点为空时，接口返回错误，状态保持 `skills`。

控制台手工绑定路径不强制调 portal_lite。不新增 WebSocket 消息类型。
