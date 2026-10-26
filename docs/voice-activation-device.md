# 设备端语音激活对接

给固件用。设备只对接已有的 OTA 和 WebSocket，不调用短信、绑定、昵称接口。这些接口由 `xiaozhi-server` 用服务端密钥调用，见 [voice-activation.md](voice-activation.md)。

时序图：[architecture/voice-activation.drawio](architecture/voice-activation.drawio)。

## 设备要做的事

未绑定设备上报版本后，看响应里的 `activation_type`。

| `activation_type` | 设备行为 |
| --- | --- |
| `voice` | 用返回的 `websocket.url` 建连，按平时对话收发音频。服务端会问手机号、验证码、助手昵称，并自动绑定技能后询问是否重启 |
| 没有该字段，或不是 `voice` | 保持原逻辑：展示 `activation.code`，提示用户到控制面板输入 |

`voice` 时仍然可以在屏幕上显示 6 位激活码，作为语音失败时的手工绑定入口。不要只展示激活码、不连 WebSocket。

绑定成功后同一条 WebSocket 会继续问昵称、自动绑定技能并询问是否重启，设备不用主动断开重连。用户确认重启后，服务端通过设备已有 MCP `reboot` 工具下发重启，不新增消息类型。

## 1. 上报版本

`POST /xiaozhi/ota/`

请求头：

| Header | 说明 |
| --- | --- |
| `Device-Id` | 设备 MAC，与 WebSocket 的 `device-id` 相同 |
| `Client-Id` | 客户端标识，可空 |
| `Content-Type` | `application/json` |

请求体与现在一致，例如：

```json
{
  "application": {
    "version": "1.0.1",
    "elf_sha256": "1"
  },
  "board": {
    "mac": "11:22:33:44:55:66"
  }
}
```

未绑定且走语音激活时，响应在原有字段上多一个 `activation_type`：

```json
{
  "server_time": {
    "timestamp": 1757567894012,
    "timeZone": "Asia/Shanghai",
    "timezone_offset": 480
  },
  "activation": {
    "code": "460609",
    "message": "http://xiaozhi.server.com\n460609",
    "challenge": "11:22:33:44:55:66"
  },
  "activation_type": "voice",
  "firmware": {
    "version": "1.0.1",
    "url": "http://xiaozhi.server.com:8002/xiaozhi/otaMag/download/NOT_ACTIVATED_FIRMWARE_THIS_IS_A_INVALID_URL"
  },
  "websocket": {
    "url": "ws://192.168.4.23:8000/xiaozhi/v1/"
  }
}
```

已绑定设备没有 `activation`，也没有 `activation_type`。空字段不会出现在 JSON 里。

激活码有效期 24 小时。过期后再次 `POST /ota/` 会拿到新的 6 位码，`activation_type` 仍是 `voice`。

可选：继续轮询 `POST /xiaozhi/ota/activate`，请求头带 `Device-Id`。未绑定返回 HTTP 202，绑定成功返回 HTTP 200，正文 `success`。语音流程不依赖这次轮询，连着 WebSocket 就能完成绑定。

## 2. 连接 WebSocket

地址用 OTA 返回的 `websocket.url`。连接头：

| Header | 说明 |
| --- | --- |
| `device-id` | 与 OTA 的 `Device-Id` 相同的 MAC |
| `client-id` | 与 OTA 的 `Client-Id` 相同，可空 |
| `Authorization` | 若现有固件已带 token，保持原样 |

连上后发送现有 `hello`，例如：

```json
{
  "type": "hello",
  "version": 1,
  "transport": "websocket",
  "audio_params": {
    "format": "opus",
    "sample_rate": 16000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

服务端回 `hello`（含 `session_id`）。之后和已绑定设备相同：

- 文本：`{"type":"listen","state":"start","mode":"auto"}` 开始拾音，`{"type":"listen","state":"stop"}` 结束一段拾音。
- 二进制：Opus 音频帧。上行是用户说话，下行是服务端 TTS。
- 服务端还会下发 `stt`、`tts`（`start` / `sentence_start` / `stop`）文本消息。设备按现有半双工逻辑播放和拾音即可。

不要为语音激活增加新的消息类型，也不要在设备上采集手机号或验证码再走 HTTP。用户说的内容由服务端 ASR 识别。

## 3. 用户会听到什么

服务端用 TTS 驱动对话。设备只负责播放和把麦克风音频送上去。

| 顺序 | 服务端播报 | 用户怎么说 |
| --- | --- | --- |
| 1 | 请说手机号 | 11 位手机号，可以说中文数字 |
| 2 | 逐位重复号码，请说确认或重说 | 「确认」或「重说」。重说回到第 1 步 |
| 3 | 验证码已发送，请说短信里的验证码 | 6 位数字。没收到可以说「重发」 |
| 4 | 绑定成功，请说助手昵称 | 1 到 12 个字 |
| 5 | 重复昵称，请说确认或重说 | 「确认」后自动绑定技能，无需再确认 |
| 6 | 通用技能已绑定。正在开通瑞幸咖啡 | 默认开通，无需确认。若提示请说短信验证码，则说出验证码；「不要/跳过」取消该项 |
| 7 | （可能）请说滴滴出行验证码 | 同上，瑞幸完成后自动开滴滴 |
| 8 | 汇总开通结果，是否重启设备 | 失败会说明哪项未开通；「确认」/「重启」则重启；「不要」则进入日常对话 |

号码或验证码不对时，服务端会说明原因并停留在当前步骤，设备不用改状态。

确认说法包括：对、是、好、嗯、行、确认、对的、是的、没错、正确、可以、好的。

否定说法包括：不对、不是、不行、错、重说、重来、重新、不要。

验证码阶段说「重发」「再发」「没收到」「重新发送」会再发一条短信。同一手机号 60 秒内不能重发，每天最多 10 条。

## 4. 断线

| 情况 | 设备 |
| --- | --- |
| 绑定完成前断开 | 再次 OTA。激活码未过期则沿用，过期则换新码。重新连接 WebSocket，从手机号重新开始 |
| 已绑定、昵称还没设 | 按已绑定设备连接。服务端会继续问昵称，不会再要手机号 |
| 已绑定、技能还没绑 | 按已绑定设备连接。服务端会自动重试技能绑定，并询问是否重启 |
| 引导已完成 | 普通对话，没有激活播报 |

## 设备不要做的事

- 不要调用 `POST /device/voice-activation/session`、`/sms`、`/bind`、`/nickname`。
- 不要自己请求阿里云短信。
- 不要在 `activation_type=voice` 时只播放「请到控制面板输入激活码」并丢掉上行音频。
