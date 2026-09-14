# 小米 MiMo 语音识别 / 语音合成接入

MiMo 的 ASR、TTS **不是** OpenAI Whisper `/v1/audio/transcriptions` 或 `/v1/audio/speech`。必须使用本仓库新增的 `MimoASR`、`MimoTTS`，不要在「OpenAI语音识别」里填 `https://api.xiaomimimo.com/v1`。

官方文档：

- [语音识别（MiMo-V2.5-ASR）](https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/audio/Speech-Recognition)
- [语音合成（MiMo-V2.5-TTS）](https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/audio/speech-synthesis-v2.5)
- [模型列表](https://mimo.mi.com/docs/zh-CN/quick-start/summary/model)

大模型（文本）可以走现有 OpenAI 兼容接口；语音必须走专用 Provider。

---

## 1. 智控台配置（全模块 / Docker）

重启 `xiaozhi-esp32-server-web` 让 Liquibase 写入新模型。然后：

### 语音识别

1. **模型配置 → 语音识别**，找到 **小米MiMo语音识别**（不要改原来的 OpenAI语音识别）
2. 填写：

| 字段 | 值 |
|------|-----|
| 基础URL | `https://api.xiaomimimo.com/v1` |
| 模型名称 | `mimo-v2.5-asr` |
| API密钥 | 控制台申请的 Key |
| 语种 | `zh`（或 `auto` / `en`） |

3. 智能体里把 ASR 选成该模型。

### 语音合成

1. **模型配置 → 语音合成**，找到 **小米MiMo语音合成**
2. 填写：

| 字段 | 值 |
|------|-----|
| 基础URL | `https://api.xiaomimimo.com/v1` |
| 模型 | `mimo-v2.5-tts` |
| API密钥 | 同上 |
| 音色 | `冰糖`（或茉莉、苏打、白桦、Mia 等） |
| 音频格式 | `wav` |

3. 智能体里把 TTS 选成该模型，并选音色。

### 大模型（可选）

供应器选 **OpenAI接口**，`base_url` 填 `https://api.xiaomimimo.com/v1`，模型名 `mimo-v2.5` 或 `mimo-v2.5-pro`。

改完后重启语音引擎：

```bash
docker restart xiaozhi-esp32-server
```

---

## 2. 仅源码 / `.config.yaml`

```yaml
selected_module:
  ASR: MimoASR
  LLM: MimoLLM
  TTS: MimoTTS

ASR:
  MimoASR:
    type: mimo
    api_key: 你的Key
    base_url: https://api.xiaomimimo.com/v1
    model_name: mimo-v2.5-asr
    language: zh
    output_dir: tmp/

LLM:
  MimoLLM:
    type: openai
    base_url: https://api.xiaomimimo.com/v1
    model_name: mimo-v2.5
    api_key: 你的Key

TTS:
  MimoTTS:
    type: mimo
    api_key: 你的Key
    base_url: https://api.xiaomimimo.com/v1
    model: mimo-v2.5-tts
    voice: 冰糖
    format: wav
    output_dir: tmp/
```

---

## 3. 容器 DNS 失败

若日志类似：

```text
Failed to resolve 'api.xiaomimimo.com' ([Errno -3] Temporary failure in name resolution)
```

这是 Docker 里解析不了域名，和接口协议无关。先测：

```bash
nslookup api.xiaomimimo.com
docker exec xiaozhi-esp32-server getent hosts api.xiaomimimo.com
```

容器失败时，在 `docker-compose.local.yml` 对应服务下增加：

```yaml
    dns:
      - 223.5.5.5
      - 114.114.114.114
```

然后：

```bash
docker compose -f docker-compose.local.yml up -d
```

---

## 4. 预置音色

`mimo-v2.5-tts` 可用：`冰糖`、`茉莉`、`苏打`、`白桦`、`mimo_default`、`Mia`、`Chloe`、`Milo`、`Dean`。中国集群默认 `冰糖`。

音色设计 / 克隆对应 `mimo-v2.5-tts-voicedesign`、`mimo-v2.5-tts-voiceclone`，当前 Provider 按预置音色实现，克隆需另接音频样本参数。
