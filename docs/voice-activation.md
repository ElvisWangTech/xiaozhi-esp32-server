# 语音激活

未绑定设备除了拿到 6 位激活码，还会拿到 `activation_type=voice`。设备连上语音服务后，由服务端把手机号播报给用户确认，再用阿里云号码认证短信校验。校验通过后，用超级管理员新建一个智能体，并按激活码绑定设备，同时记下手机号。绑定成功后询问助手昵称；昵称确认后询问怎么称呼主人，再让用户选一个性格模板并补充设定，然后自动做默认技能绑定，再询问是否重启设备。

固件不在本仓库。设备侧对接见 [voice-activation-device.md](voice-activation-device.md)：OTA 返回 `activation_type=voice` 时连接 WebSocket 并对话，不要只把激活码展示成控制面板绑定。`activation_type` 不是 `voice` 时，仍播放「请登录控制面板，输入激活码」。

时序图：[architecture/voice-activation.drawio](architecture/voice-activation.drawio)。

## 范围

| 阶段 | 内容 | 本次 |
| --- | --- | --- |
| 第一阶段 激活 | 确认手机号、发短信、校验验证码、超级管理员建智能体并绑定设备 | 实现 |
| 第二阶段 角色绑定 | 助手昵称、主人称呼、性格模板和补充设定 | 实现 |
| 第三阶段 技能绑定 | 昵称确认后自动通用绑定，默认开通瑞幸→滴滴（仅验证码时打断；失败口头提示），最后询问是否重启 | 实现 |

控制台原有 `POST /device/bind/{agentId}/{deviceCode}` 保留，给非 voice 路径使用。一个智能体只能绑定一台设备，见下文「一机一体」。

## 流程

1. 设备不存在时，`POST /ota/` 仍生成 6 位激活码（Redis，24 小时），响应增加 `activation_type=voice`。激活码写入 `ota:activation:data:*` 时一并写入 `activation_type`。
2. 设备连接 `xiaozhi-server`。配置接口返回 10042 时，再查语音激活会话。类型为 `voice` 才进入语音状态机，并使用公共配置里的 ASR/TTS 听和说。
3. 请用户说手机号。从识别文本抽出 11 位号码（含中文数字）。不是合法手机号就重问。
4. 逐位播报号码，请用户说「确认」或「重说」。
5. 确认后调用发送接口。成功后请用户说出短信验证码。失败则播报原因，留在确认步骤，再说「确认」会重发。
6. 校验通过后：查找 `super_admin=1` 的用户（取 id 最小的一个），**新建**一个智能体（不按手机号查找已有智能体），用默认模板创建，初始名为「小智」。按激活码写入 `ai_device`，`user_id` 为该管理员，并保存手机号，`onboarding_status=nickname`。同一手机号再绑另一台设备时，再新建一个智能体。
7. 播报绑定成功，重新拉取该设备的私有配置。若 `voice_onboarding=nickname`，请用户说助手昵称。可以说「小智」，也可以说「你的昵称是小智」。播报确认后写入 `agentName`，状态改为 `role`。
8. 认主：问「我该怎么称呼您呢？」。用户可以直接说「老板」，也可以说「叫我老板」。确认后进入性格选择。
9. 性格：按排序播报 `ai_agent_template` 的名称，用户说序号或名字。确认后问还有没有补充。说「没有」就结束；说了补充则追加到角色设定。然后把所选模板的 `systemPrompt`、主人称呼和补充写入智能体，状态改为 `skills`。
10. 角色设定结束后**不询问用户**，自动做通用技能绑定（`POST /skills` → portal_lite `/api/device/bind` + `/api/portal/invite`）。成功后**默认**依次开通瑞幸咖啡、滴滴出行（先瑞幸：滴滴常需实名/免密支付）。`/skills/auth` → portal automation；仅 `waiting_code` 时要短信验证码；说「不要/跳过」则 cancel 该项。单项失败会当场播报原因，全部结束后再汇总失败项并提示可说「绑定技能」重试，然后问是否重启。麦当劳暂不支持。通用绑定失败保持 `skills`。
11. 断线重连：状态为 `nickname` 时只继续问昵称；为 `role` 时从「我该怎么称呼您呢？」重新开始；为 `skills` 时继续通用绑定与默认授权流程，不再走激活。

验证码阶段说「重说」会回到手机号。说「重发」会再发一条短信（仍受频控）。

## 数据

`ai_device` 新增：

| 字段 | 说明 |
| --- | --- |
| `phone` | 绑定手机号，可空。控制台手工绑定的旧设备为空。首页智能体卡片通过列表接口的 `phone` 字段展示，没有号码时显示「未记录」。右上角搜索同时匹配这个号码，支持片段，例如输入 `136` |
| `onboarding_status` | `nickname` 待设置昵称；`role` 待认主和性格；`skills` 待技能绑定；`done` 已完成。空值视为已完成，避免老设备被追问 |

发送短信时，把已确认的手机号写入该设备的激活 Redis，校验时必须和请求里的手机号一致。

## 接口

以下接口使用与 `/config/**` 相同的服务端密钥（`Authorization: Bearer {server.secret}`），供 `xiaozhi-server` 调用。

`POST /device/voice-activation/session`

请求：`{ "macAddress": "..." }`

- 未绑定且激活码仍在：`{ "bound": false, "activationType": "voice" }`。没有 `activation_type` 的旧缓存视为 `code`。
- 已绑定：`{ "bound": true, "onboardingStatus": "nickname" | "role" | "skills" }`，没有引导任务时 `onboardingStatus` 为空。

`POST /device/voice-activation/sms`

请求：`{ "macAddress": "...", "phone": "13800138000" }`

设备必须尚未绑定，激活码必须有效，手机号为 `1` 开头的 11 位。

`POST /device/voice-activation/bind`

请求：`{ "macAddress": "...", "phone": "13800138000", "code": "123456" }`

校验短信后完成绑定。返回 `deviceId`、`agentId`。

`POST /device/voice-activation/nickname`

请求：`{ "macAddress": "...", "nickname": "小助" }`

仅当 `onboarding_status=nickname` 时更新智能体名称，然后把状态改为 `role`。

`POST /device/voice-activation/role/templates`

请求：`{ "macAddress": "..." }`

仅当 `onboarding_status=role` 时，按排序返回仍在使用的性格模板。每项包含名称和豆包音色编码，例如 `[{"name":"湾湾小何","voice":"zh_female_xiaohe_uranus_bigtts"},{"name":"星际游子","voice":"zh_male_m191_uranus_bigtts"}]`。`sort < 0` 的模板不返回。

`POST /device/voice-activation/role`

请求：`{ "macAddress": "...", "ownerTitle": "老板", "templateName": "英语老师", "supplement": "说话温柔一点" }`

`supplement` 可空。仅当状态为 `role` 时，把模板的 `systemPrompt`、TTS 模型和音色抄到智能体，并追加主人称呼和补充，然后把状态改为 `skills`。`{{assistant_name}}` 仍留给配置下发时替换成昵称。

`POST /device/voice-activation/skills`

请求：`{ "macAddress": "..." }`

仅当 `onboarding_status=skills` 时：调用 portal_lite 通用绑定，用返回的 `device_id` 签发邀请令牌（`X-Admin-Token`），写入 Redis。成功后**不**改为 `done`，由语音侧默认启动授权自动化。

`POST /device/voice-activation/skills/auth`

请求：`{ "macAddress": "...", "service": "didi" }`

`service` 仅 `didi` / `luckin`。启动 portal `/api/automation/start`，立即返回 `{ status, service, taskId, message }`。`status`：`pending` / `waiting_code` / `done` / `failed` / `skipped`。

`POST /device/voice-activation/skills/auth/status`

请求：`{ "macAddress": "...", "taskId": "..." }`

查询自动化状态，返回同上。

`POST /device/voice-activation/skills/auth/code`

请求：`{ "macAddress": "...", "taskId": "...", "code": "123456" }`

提交短信验证码到 `/api/automation/code`。

`POST /device/voice-activation/skills/auth/cancel`

请求：`{ "macAddress": "...", "taskId": "..." }`

取消当前授权任务。

`POST /device/voice-activation/skills/complete`

请求：`{ "macAddress": "..." }`

授权流程结束后调用：可选 `portal/finish`，把 `onboarding_status` 改为 `done`，清掉邀请缓存。

已绑定设备的 `POST /config/agent-models`：需要昵称时返回 `voice_onboarding=nickname`；需要认主和性格时返回 `voice_onboarding=role`；需要技能绑定时返回 `voice_onboarding=skills`。

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
| `portal_lite.admin_token` | 对应 `X-Admin-Token` / `LITE_ADMIN_TOKEN`，签发邀请令牌；为空时回退 `bind_token` |

频控：同一手机号 60 秒内不可重发，每天最多 10 条，验证失败最多 5 次。未配置密钥且未开启 mock 时，接口返回「短信服务暂不可用」。

## 一机一体

每个智能体只绑定一台设备。同一个手机号绑定多台设备时，每台设备各有一个智能体。

语音激活的 `bind` 每次都调用 `createAgentForUser`，不按手机号复用智能体。设备侧不需要再改。

控制台 `POST /device/bind/{agentId}/{deviceCode}` 和手动添加设备走同一条限制：该智能体下已经有设备时拒绝，提示「该智能体已绑定设备，请新建智能体后再绑定」。已经绑了多台的历史数据不拆开，卡片上的手机号会用顿号拼在一起。

## 默认模型

未绑定设备的公共配置固定：

| 模块 | 模型 |
| --- | --- |
| ASR | 豆包语音识别2.0(流式)，`ASR_DoubaoStreamASRV2` |
| LLM | deepseek-chat，`LLM_DeepSeekLLM` |
| TTS | 豆包语音合成2.0(流式)，`TTS_HSDSTTS_V2`，音色「湾湾小何」（`TTS_HSDSTTS_V2_0001`） |

已绑定设备用智能体自己的模型。语音激活 `bind` 走 `createAgentForUser`，从默认模板复制这三项。

2026-10-27 起，上述三项的默认模型、仍指向小米 MiMo 的智能体模板和已有智能体，都改为这一组。其它模型不受影响。变更在 `db/changelog/202610271430.sql`，随 manager-api 启动执行。

## 变更记录

### 2026-10-21

- 首页智能体卡片增加「绑定手机号」，数据来自该智能体已绑定设备的 `ai_device.phone`。右上角搜索同时按这个号码过滤，名称、标签、MAC 仍然有效。
- 核对一机一体：语音激活本来就是一台设备新建一个智能体，同一手机号不会合并。控制台绑定和手动添加补上「已有设备则拒绝」。
- 默认音色从火山引擎（流式）改为小米 MiMo 语音合成。模板和仍使用火山双流式的已有智能体一起切换，否则卡片仍显示火山。

### 2026-10-23

- 实现第三阶段技能绑定：昵称确认后 `onboarding_status=skills`，自动调 portal_lite `POST /api/device/bind`，成功后询问是否重启设备（设备 MCP `reboot`）。智控台参数 `portal_lite.base_url`、`portal_lite.bind_token` 见 `db/changelog/202610231600.sql`。

### 2026-10-26

- 技能绑定拆成通用绑定 + 授权技能询问（方案 A）：通用绑定后依次询问滴滴/瑞幸，确认则写 `auth_bind` 元数据。

### 2026-10-26（方案 B）

- 重构为默认开通：通用绑定后签发 invite，自动跑瑞幸→滴滴 automation；仅 `waiting_code` 时要短信验证码；拒绝则 cancel 该项；失败当场与结束时口头提示。新增 `portal_lite.admin_token` 与 `/skills/auth/{status,code,cancel}`。

### 2026-10-27

- 昵称可以听「你的昵称是小智」这类整句。确认后先认主、再选性格模板并接受补充，写完角色设定才进入 `skills`。新增状态 `role`，以及 `/role/templates`、`/role`。
- 默认 ASR / LLM / TTS 从小米 MiMo 改为豆包语音识别2.0(流式)、deepseek-chat、豆包语音合成2.0(流式)。未绑定设备的公共配置、智能体模板，以及仍使用 MiMo 的已有智能体一起切换，语音激活新建的智能体因此也用这一组。

## 后续操作（本次不实现）

升级、更换手机号、解绑也走语音：先播报当前绑定手机号请用户确认，确认后发送短信，验证码通过后再执行变更。设备上的 `phone` 和号码认证校验就是为这些操作留的。本次只保存手机号并完成激活校验，不提供上述三条对话。

## 第三阶段（方案 B）

激活、昵称、认主和性格完成之前，不询问技能。角色设定写入后：先自动做**通用技能绑定**，再**默认**跑瑞幸→滴滴授权自动化（不问「要开通吗」），仅短信验证码阶段打断用户；失败会提示原因，最后询问是否重启。

真实凭证由 portal_lite Playwright 自动化写入 skills；本阶段不再只写 `auth_bind` 元数据。

### 对话顺序

| 步骤 | `onboarding_status` | 对话 |
| --- | --- | --- |
| 设备绑定成功 | `nickname` | 询问助手昵称。可说「你的昵称是小智」 |
| 昵称确认写入 | `role` | 问「我该怎么称呼您呢？」，确认主人称呼 |
| 称呼确认 | `role` | 播报性格模板，用户说序号或名字，再问补充 |
| 性格和补充写入 | `skills` | 自动通用技能绑定，不询问用户 |
| 通用绑定成功 | `skills` | 「正在开通瑞幸咖啡」——默认启动 automation |
| 瑞幸 `waiting_code` | `skills` | 「请说出瑞幸短信验证码」；「不要/跳过」则 cancel |
| 瑞幸结束 | `skills` | 自动开通滴滴出行（同上） |
| 授权做完 | `done` | 汇总成功/失败（失败含原因；滴滴失败可提示实名/免密），`complete` 后问是否重启 |
| 用户确认重启 | `done` | 设备 MCP `self.qiniu.custom.restart` |
| 用户拒绝重启 | `done` | 「好的，我们可以开始聊天了」 |

断线重连：`voice_onboarding=role` 时从认主重新开始；`voice_onboarding=skills` 时继续通用绑定与默认授权。通用绑定失败可说「确认」重试，或「稍后再说」先聊天，之后再说「绑定技能」。已是 `done` 的老设备不回填。

部署注意：`portal_lite` 须提供 `/api/device/bind`、`/api/portal/invite`、`/api/automation/{start,status,code,cancel}`；并配置 `admin_token`（或与 bind_token 相同）。

### 外部接口（portal_lite）

技能服务在仓库外：`mcd_mcp` 的 `portal_lite`。

| 步骤 | 方法 | 鉴权 |
| --- | --- | --- |
| 通用绑定 | `POST /api/device/bind` | `X-Bind-Token` |
| 签发邀请 | `POST /api/portal/invite` `{device_id}` | `X-Admin-Token` |
| 启动授权 | `POST /api/automation/start` `{token, service, phone}` | invite `token` |
| 查状态 | `GET /api/automation/status?task_id=` | — |
| 交验证码 | `POST /api/automation/code` `{task_id, code}` | — |
| 取消 | `POST /api/automation/cancel` `{task_id}` | — |
| 结束邀请 | `POST /api/portal/finish` `{token}` | invite `token` |

**通用绑定** body：`device_name`、`mac_address`、`agent_name`、`endpoint_url`、`phone`。返回含 `device_id`。

自动化状态：`queued` → `starting` → `waiting_code` → `submitting` → `done` / `error` / `canceled`。语音侧只处理短信类服务（didi/luckin）；`waiting_scan`（麦当劳）暂不支持。

未配置 `portal_lite.*` 或 MCP 接入点为空时返回错误，状态保持 `skills`。控制台手工绑定不强制调 portal_lite。不新增 WebSocket 消息类型。
