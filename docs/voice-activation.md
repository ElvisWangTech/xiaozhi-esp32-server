# 语音激活

未绑定设备除了拿到 6 位激活码，还会拿到 `activation_type=voice`。设备连上语音服务后，由服务端把手机号播报给用户确认，再发一条含 4 位验证码的短信，验证码在 manager-api 本地校验。校验通过后，用超级管理员新建一个智能体，并按激活码绑定设备，同时记下手机号。校验通过后，用超级管理员新建一个智能体，并按激活码绑定设备，同时记下手机号。绑定成功后询问助手昵称；昵称确认后询问怎么称呼主人，再让用户选一个性格模板并补充设定，然后自动做默认技能绑定，最后直接重启设备。

固件不在本仓库。设备侧对接见 [voice-activation-device.md](voice-activation-device.md)：OTA 返回 `activation_type=voice` 时连接 WebSocket 并对话，不要只把激活码展示成控制面板绑定。`activation_type` 不是 `voice` 时，仍播放「请登录控制面板，输入激活码」。

时序图：[architecture/voice-activation.drawio](architecture/voice-activation.drawio)。

## 范围

| 阶段 | 内容 | 本次 |
| --- | --- | --- |
| 第一阶段 激活 | 确认手机号、发短信、校验验证码、超级管理员建智能体并绑定设备 | 实现 |
| 第二阶段 角色绑定 | 助手昵称、主人称呼、性格模板和补充设定 | 实现 |
| 第三阶段 技能绑定 | 昵称确认后自动通用绑定，默认开通瑞幸→滴滴→特斯拉（瑞幸/滴滴仅验证码时打断；特斯拉授权短链发短信；失败口头提示），最后直接重启 | 实现 |

控制台原有 `POST /device/bind/{agentId}/{deviceCode}` 保留，给非 voice 路径使用。一个智能体只能绑定一台设备，见下文「一机一体」。

## 流程

1. 设备不存在时，`POST /ota/` 仍生成 6 位激活码（Redis，24 小时），响应增加 `activation_type=voice`。激活码写入 `ota:activation:data:*` 时一并写入 `activation_type`。
2. 设备连接 `xiaozhi-server`。配置接口返回 10042 时，再查语音激活会话。类型为 `voice` 才进入语音状态机，并使用公共配置里的 ASR/TTS 听和说。
3. 开场先打招呼：「你好，我是 X-Atom，新一代物理AI智能体，让AI走进现实。第一次见面，我该怎么称呼您呢？」用户说出称呼（例如「叫我老王」）就复述确认，提前记下作为主人称呼；没听出、跳过或答非所问都不卡住，直接进入报手机号，称呼留到第 9 步再问。
4. 请用户说手机号。从识别文本抽出 11 位号码（含中文数字）。不是合法手机号就重问。
5. 逐位播报号码，请用户说「确认」或「重说」。
6. 确认后调用发送接口。成功后请用户说出短信验证码。失败则播报原因，留在确认步骤，再说「确认」会重发。
7. 校验通过后：查找 `super_admin=1` 的用户（取 id 最小的一个），**新建**一个智能体（不按手机号查找已有智能体），用默认模板创建，初始名为「小智」。按激活码写入 `ai_device`，`user_id` 为该管理员，并保存手机号，`onboarding_status=nickname`。同一手机号再绑另一台设备时，再新建一个智能体。
8. 播报绑定成功，重新拉取该设备的私有配置。若 `voice_onboarding=nickname`，请用户说助手昵称。可以说「小智」，也可以说「你的昵称是小智」。播报确认后写入 `agentName`，状态改为 `role`。
9. 认主：开场已经确认过称呼时不再重复提问，直接播报「好的，我是{昵称}」进入性格选择；否则问「我该怎么称呼您呢？」，用户可以直接说「老板」，也可以说「叫我老板」，确认后进入性格选择。
10. 性格：按排序播报 `ai_agent_template` 的名称，第一项就是当前默认性格（可爱女生），说「不用换」「保持现在的」也等于选它。确认后问还有没有补充。说「没有」就结束；说了补充则追加到角色设定。然后把所选模板的 `systemPrompt`、主人称呼和补充写入智能体，状态改为 `skills`。
11. 角色设定结束后**不询问用户**，自动做通用技能绑定（`POST /skills` → portal_lite `/api/device/bind` + `/api/portal/invite`）。**只有这一次**会接着自动开通瑞幸咖啡、滴滴出行、特斯拉（先瑞幸：滴滴常需实名/免密支付；特斯拉放最后，授权短链发到绑定手机号）。第一次调用 `/skills/auth` 后，状态从 `skills` 改为 `skills_manual`，重连和唤醒都不再自动开通。之后用户要自己说「绑定技能」。瑞幸/滴滴走 portal automation，仅 `waiting_code` 时要短信验证码；特斯拉走 `/api/tesla/oauth/start`，返回 `waiting_oauth`，短信发 portal 短链（`/t/{code}`，打开后 302 到 Tesla 授权页），不要把 `auth.tesla` 长链塞进短信，设备上不念链接。说「不要/跳过」则 cancel 该项。单项失败会当场播报原因（短信没发出去也算失败，不改口头引导），全部结束后再汇总失败项并提示可说「绑定技能」重试，然后播报「配置完毕，设备即将重启」并直接下发重启（不询问用户；不重启设备 MCP 工具加载不到）。麦当劳暂不支持。通用绑定失败保持 `skills`。
12. 断线重连：状态为 `nickname` 时只继续问昵称；为 `role` 时从「我该怎么称呼您呢？」重新开始；为 `skills` 时继续通用绑定，并仍会自动授权（这一次还没发起过）。为 `skills_quiet` 时只静默重试通用绑定，成功后结束引导，并告诉用户可以说「绑定技能」再开通瑞幸、滴滴或特斯拉。`skills_manual` 和 `done` 不再自动拉起；用户说「绑定技能」才会再开通。

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

仅当 `onboarding_status=role` 时，按排序返回仍在使用的性格模板。每项包含名称和豆包音色编码，例如 `[{"name":"可爱女生","voice":"saturn_zh_female_keainvsheng_tob"},{"name":"甜美小源","voice":"zh_female_tianmeixiaoyuan_uranus_bigtts"},{"name":"温婉珊珊","voice":"saturn_zh_female_wenwanshanshan_cs_tob"}]`。`sort < 0` 的模板不返回。

`POST /device/voice-activation/role`

请求：`{ "macAddress": "...", "ownerTitle": "老板", "templateName": "英语老师", "supplement": "说话温柔一点" }`

`supplement` 可空。仅当状态为 `role` 时，把模板的 `systemPrompt`、TTS 模型和音色抄到智能体，并追加主人称呼和补充，然后把状态改为 `skills`。`{{assistant_name}}` 仍留给配置下发时替换成昵称。

`POST /device/voice-activation/skills`

请求：`{ "macAddress": "..." }`

仅当 `onboarding_status=skills` 时：调用 portal_lite 通用绑定，用返回的 `device_id` 签发邀请令牌（`X-Admin-Token`），写入 Redis。成功后**不**改为 `done`，由语音侧默认启动授权自动化。

`POST /device/voice-activation/skills/auth`

请求：`{ "macAddress": "...", "service": "didi" }`

`service` 为 `didi` / `luckin` / `tesla`。瑞幸、滴滴启动 portal `/api/automation/start`，立即返回 `{ status, service, taskId, message }`。特斯拉启动 `/api/tesla/oauth/start`，成功后把返回的 `sms_url`（portal 短链）用通知短信发到设备绑定手机号，返回 `{ status: "waiting_oauth", service: "tesla", authUrl, message }`，无 `taskId`。`authUrl` 也是这条短链。没有 `sms_url` 时返回 `failed` / `OAUTH_START_FAILED`，不要改发 Tesla 长链。无手机号或短信发送失败返回 `failed` / `SMS_SEND_FAILED`。`status`：`pending` / `waiting_code` / `waiting_oauth` / `done` / `failed` / `skipped` / `already_bound`。

`POST /device/voice-activation/skills/auth/status`

请求：`{ "macAddress": "...", "taskId": "..." }`；特斯拉可省略 `taskId`，改传 `service=tesla`。

查询自动化状态，或查 portal `auth-status.tesla`。返回同上。

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

登录注册继续用现有短信服务（`dysmsapi`）。语音激活改走通用短信平台，平台只有发送接口，没有校验接口：

- 发送：`POST {sms.gateway.base_url}/api/notification/send/sms`，请求体 `template_type`、`phone_number`、`message`，请求头必带 `Client-Id`（取 `sms.gateway.client_id`）。
- 验证码由 manager-api 本地生成：4 位数字，5 分钟有效，校验通过后立即失效，只存 SHA-256 摘要。
- 平台返回 HTTP 2xx 且没有明确写 `success=false` / `ok=false` 视为发送成功；发送失败不占用频控额度。

智控台参数（系统设置 → 参数管理，搜索 `sms.gateway`）：

| 参数 | 说明 |
| --- | --- |
| `sms.gateway.base_url` | 短信平台根地址，例如 `https://sms.example.com`，不带路径 |
| `sms.gateway.client_id` | 客户端标识，必填。请求时以 `Client-Id` 请求头携带；为空时接口报「短信服务暂不可用」 |
| `sms.gateway.template_type` | 短信类型：0 通知，1 营销，2 验证码。默认 2，非法值按 2 处理 |
| `sms.gateway.message_template` | 短信文案模板，`{{code}}` 替换为验证码。默认 `您的验证码为{{code}}，五分钟内有效，请勿泄露给他人`；模板里没有占位符时回落默认文案 |
| `sms.gateway.tesla_auth_template` | 特斯拉授权链接短信模板，`{{url}}` 替换为 portal 短链（`https://{portal}/t/{code}`）。默认 `请打开以下链接完成特斯拉授权：{{url}}`；`template_type` 固定为 0 通知，不占用验证码模板和日限额。短链打开后 302 到 Tesla 授权页，避免运营商改写 `auth.tesla` 长链 |
| `sms.gateway.mock` | `true` 时不调用平台，验证码和特斯拉授权链接都只写在服务端日志里，供开发联调 |
| `portal_lite.base_url` | portal_lite 服务地址，例如 `http://127.0.0.1:5005` |
| `portal_lite.bind_token` | 对应 `X-Bind-Token` / `LITE_BIND_TOKEN` |
| `portal_lite.admin_token` | 对应 `X-Admin-Token` / `LITE_ADMIN_TOKEN`，签发邀请令牌；为空时回退 `bind_token` |

频控：同一手机号 60 秒内不可重发，每天最多 10 条，验证失败最多 5 次。未配置 `base_url` 且未开启 mock 时，接口返回「短信服务暂不可用」。

旧的 `aliyun.dypns.*` 参数已经不再读取，确认无需回滚后可自行删除。登录注册短信仍用 `aliyun.sms.*`。

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
| TTS | 豆包语音合成2.0(流式)，`TTS_HSDSTTS_V2`，音色「可爱女生」（`TTS_HSDSTTS_V2_0003`） |

已绑定设备用智能体自己的模型。语音激活 `bind` 走 `createAgentForUser`，从默认模板复制这三项。

激活话术的音色跟着**默认模板**走：`/config/server-base` 把默认模板绑定的音色写成 `private_voice` 下发，不再沿用 `ai_model_config` 里 TTS 模型自身的 `speaker`（那个值可能还是旧音色）。缓存里存的音色和当前默认模板对不上时会丢掉缓存重建，所以换模板音色不需要手工清 Redis。

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
- 授权技能（瑞幸、滴滴）只在第一次进入 `skills` 时自动开通。发起后状态改为 `skills_manual`，重连和唤醒不再自动开通；用户说「绑定技能」才会再绑。`skills_quiet` 只重试通用绑定。

### 2026-11-03

- 配置完毕后不再询问是否重启：汇总结果后直接播报「配置完毕，设备即将重启」，等这句播完再调设备 MCP `self.qiniu.custom.restart`。原因是不重启时设备 MCP 工具加载不到，技能配了也用不了。原 `ask_reboot` 状态与确认/拒绝分支删除，改为终态 `rebooting`，期间用户输入不再处理。
- 授权等待收紧：提交验证码后查 `/skills/auth/status` 的时间从 90 秒改为 15 秒（`AUTH_CODE_WAIT_SECONDS=15`，间隔 1.5 秒，10 次）。没有验证码环节的自动化登录仍按 90 秒等（`AUTH_POLL_WAIT_SECONDS=90`）。超时、`failed`、`skipped`、`already_bound`、启动异常等在切到下一项或汇总前都先 `_wait_spoken()`，避免「瑞幸咖啡开通失败」被「正在开通滴滴出行」盖掉。

### 2026-11-04

- 语音激活开场先打招呼：「你好，我是 X-Atom，新一代物理AI智能体，让AI走进现实。第一次见面，我该怎么称呼您呢？」用户说出称呼就复述确认并提前记下，角色绑定时不再重复问；没说出来就继续报手机号，留到认主那一步再问。新增状态 `greet`、`confirm_greet`。
- 默认模板从「湾湾小何」换成「甜美小源」，定位为 x-atom 专业客服（中英双语、耐心解答、偶尔给情绪价值），音色 `TTS_HSDSTTS_V2_0021`。
- 性格模板「湾湾小何」改名为「温婉珊珊」，提示词整段替换，音色换成 `TTS_HSDSTTS_V2_0030`。排序：甜美小源 1（默认）、温婉珊珊 2、星际游子 3、英语老师 4。变更在 `db/changelog/202611041600.sql`。
- 性格选择第一项就是当前默认性格，语音里会说明「也就是现在的我，不换也可以」；说「不用换」「保持现在的」「随便」都按选它处理，确认话术改成「性格就保持甜美小源，不换了」。
- 修激活话术仍用「湾湾小何」音色的问题：`ConfigServiceImpl.getConfig` 原来给未绑定设备传 `voice=null`，TTS 回落到 `ai_model_config` 里 `TTS_HSDSTTS_V2` 的 `speaker`（还是 `zh_female_xiaohe_uranus_bigtts`）。改为取默认模板绑定的音色写成 `private_voice`，并在命中 Redis 缓存时比对音色，不一致就重建缓存。

### 2026-11-05

- 默认模板从「甜美小源」换成「可爱女生」：温柔、体贴、爱撒娇、偶尔闹点小脾气，但答应的事情一定做到。音色换成豆包语音合成2.0 的「可爱女生」（`TTS_HSDSTTS_V2_0003`，`saturn_zh_female_keainvsheng_tob`）。变更在 `db/changelog/202611051700.sql`。
- 「甜美小源」保留为可选模板（提示词、音色不变），排序调整为：可爱女生 1（默认）、甜美小源 2、温婉珊珊 3、星际游子 4、英语老师 5。
- 语音里的确认话术随之变为「性格就保持可爱女生，不换了，对吗？」，`persona_menu` 的第一项也变成「一，可爱女生，也就是现在的我，不换也可以」。Python 侧无硬编码，模板顺序全部来自 `ai_agent_template.sort`。
- 默认昵称从「助手」改为「牛小七」：用户说「算了」「不想设置」跳过昵称时，`_NICKNAME_DEFAULT` 兜底为牛小七，写进 `agentName` 后提示词里的 `{{assistant_name}}` 也是牛小七。改动在 `core/handle/voice_activation.py`，`_begin_owner` 的 `self.nickname or _NICKNAME_DEFAULT` 同步复用同一常量，不再各写一份。

### 2026-11-06

- 语音激活短信从阿里云号码认证（`dypnsapi`）换成通用短信平台：只调发送接口 `POST {base_url}/api/notification/send/sms`，参数 `template_type`、`phone_number`、`message`。平台没有校验接口，验证码改由 `VoiceSmsCodeService` 本地生成（4 位）、`Redis` 存摘要、5 分钟有效、校验通过即失效。
- 新增智控台参数 `sms.gateway.base_url`、`sms.gateway.template_type`（默认 2 验证码）、`sms.gateway.message_template`（默认 `验证码：{{code}}`）、`sms.gateway.mock`，变更在 `db/changelog/202611061600.sql`。`DypnsSmsSender`、`DypnsSmsCodeService` 与 `alibabacloud-dypnsapi20170525` 依赖一并删除；登录注册短信仍走 `dysmsapi`。
- 平台要求每个请求携带 `Client-Id` 请求头，新增必填参数 `sms.gateway.client_id`（`db/changelog/202611061700.sql`），为空时不发请求、接口报「短信服务暂不可用」。
- 默认文案模板改为 `您的验证码为{{code}}，五分钟内有效，请勿泄露给他人`（`db/changelog/202611061800.sql`，只覆盖仍是旧默认值的行，用户自定义模板不动）。
- 语音提示补上「是四位数字」，避免用户报错位数。

### 2026-11-12

- 语音授权队列在瑞幸、滴滴之后增加特斯拉。特斯拉走网页 PKCE OAuth，不进 Playwright 短信自动化；授权短链用通知短信发到设备绑定手机号，设备上不念链接。
- 新增参数 `sms.gateway.tesla_auth_template`（`db/changelog/202611121600.sql`），`{{url}}` 替换为 portal 短链，`template_type` 固定为 0 通知，不占用验证码模板和日限额。无手机号或发送失败返回 `SMS_SEND_FAILED`，该项失败，不改口头引导。
- Tesla 地区由 portal-lite 环境变量 `TESLA_REGION`（CN / NA / EU）决定，语音侧不强制中国区。
- 运营商会改写 `auth.tesla.cn` / `auth.tesla.com` 长链（`https` 变成 `*`、域名点号变空格）。短信改发 portal `GET /t/{code}`，打开后 302 到真实 Tesla authorize URL。`TESLA_REDIRECT_URI` 不要改。

## 后续操作（本次不实现）

升级、更换手机号、解绑也走语音：先播报当前绑定手机号请用户确认，确认后发送短信，验证码通过后再执行变更。设备上的 `phone` 和本地验证码校验就是为这些操作留的。本次只保存手机号并完成激活校验，不提供上述三条对话。

## 第三阶段（方案 B）

激活、昵称、认主和性格完成之前，不询问技能。角色设定写入后：先自动做**通用技能绑定**，再**仅这一次**默认跑瑞幸→滴滴→特斯拉（不问「要开通吗」）。瑞幸/滴滴仅短信验证码阶段打断用户；特斯拉把 portal 授权短链发到绑定手机号，用户打开短信完成网页 OAuth。失败会提示原因，最后直接重启。授权一旦发起，状态变为 `skills_manual`，之后只能用户说「绑定技能」再开通。每个授权技能开通前都会查是否已有凭证：瑞幸看 `luckin_token`，滴滴看 `didi_key`，特斯拉看 `extra.tesla.refresh_token`。已经开通的跳过。用户说「重新绑定」才会再开通。Tesla 地区由 portal-lite 环境变量 `TESLA_REGION`（CN / NA / EU）决定，不在语音侧强制中国区。

真实凭证由 portal_lite Playwright 自动化写入 skills；本阶段不再只写 `auth_bind` 元数据。

### 对话顺序

| 步骤 | `onboarding_status` | 对话 |
| --- | --- | --- |
| 设备绑定成功 | `nickname` | 询问助手昵称。可说「你的昵称是小智」 |
| 昵称确认写入 | `role` | 问「我该怎么称呼您呢？」，确认主人称呼 |
| 称呼确认 | `role` | 播报性格模板，用户说序号或名字，再问补充 |
| 性格和补充写入 | `skills` | 自动通用技能绑定，不询问用户 |
| 通用绑定成功，且还没自动授权过 | `skills` → `skills_manual` | 「正在开通瑞幸咖啡」——只这一次默认启动 automation |
| 瑞幸 `waiting_code` | `skills_manual` | 「请说出瑞幸短信验证码」；「不要/跳过」则 cancel。同一次对话里继续，不再重新自动开始 |
| 瑞幸结束 | `skills_manual` | 同一次对话里继续开通滴滴出行 |
| 滴滴结束 | `skills_manual` | 同一次对话里继续开通特斯拉；portal 短链发短信，「打开短信完成登录」 |
| 特斯拉 `waiting_oauth` | `skills_manual` | 用户打开短信里的授权链接完成登录；说「已经授权」后继续查结果；「不要/跳过」则跳过 |
| 授权做完 | `done` | 汇总成功/失败（失败含原因；滴滴失败可提示实名/免密；特斯拉短信失败提示链接没发出去），`complete` 后直接重启 |
| 重启下发 | `done` | 播报「配置完毕，设备即将重启」，等播完再调设备 MCP `self.qiniu.custom.restart` |

断线重连：`voice_onboarding=role` 时从认主重新开始；`voice_onboarding=skills` 时继续通用绑定，并在还没发起过授权时自动开通。`skills_quiet` 只重试通用绑定。`skills_manual` 和 `done` 不自动开通，用户说「绑定技能」才进入授权。通用绑定失败可说「确认」重试，或「稍后再说」先聊天。已是 `done` 的老设备不回填，仍可以说「绑定技能」。

部署注意：`portal_lite` 须提供 `/api/device/bind`、`/api/portal/invite`、`/api/automation/{start,status,code,cancel}`、`/api/tesla/oauth/start`（额外返回 `sms_url`）、`GET /t/{code}` 短跳转与 `auth-status.tesla`；并配置 `admin_token`（或与 bind_token 相同）。Tesla Fleet App 地区看 portal-lite `TESLA_REGION`。短链公网根地址从 `TESLA_REDIRECT_URI` 推导，不要改 Tesla `redirect_uri`。

### 外部接口（portal_lite）

技能服务在仓库外：`mcd_mcp` 的 `portal_lite`。

| 步骤 | 方法 | 鉴权 |
| --- | --- | --- |
| 通用绑定 | `POST /api/device/bind` | `X-Bind-Token` |
| 签发邀请 | `POST /api/portal/invite` `{device_id}` | `X-Admin-Token` |
| 启动授权 | `POST /api/automation/start` `{token, service, phone}` | invite `token` |
| 启动特斯拉 OAuth | `POST /api/tesla/oauth/start` `{token}`，返回 `auth_url` + `sms_url` | invite `token` |
| 特斯拉短信短链 | `GET /t/{code}` 302 到 Tesla authorize | 短码未过期可重复打开 |
| 查状态 | `GET /api/automation/status?task_id=` | — |
| 查特斯拉凭证 | portal `auth-status.tesla` | invite `token` |
| 交验证码 | `POST /api/automation/code` `{task_id, code}` | — |
| 取消 | `POST /api/automation/cancel` `{task_id}` | — |
| 结束邀请 | `POST /api/portal/finish` `{token}` | invite `token` |

**通用绑定** body：`device_name`、`mac_address`、`agent_name`、`endpoint_url`、`phone`。返回含 `device_id`。

自动化状态：`queued` → `starting` → `waiting_code` → `submitting` → `done` / `error` / `canceled`。语音侧处理短信类服务（didi/luckin）和特斯拉网页 OAuth（`waiting_oauth`）；`waiting_scan`（麦当劳）暂不支持。

未配置 `portal_lite.*` 或 MCP 接入点为空时返回错误，状态保持 `skills`。控制台手工绑定不强制调 portal_lite。不新增 WebSocket 消息类型。
