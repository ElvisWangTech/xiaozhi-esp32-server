#!/usr/bin/env python3
"""
Qwen 系列语音合成 A/B 基准测试：对比首包延迟、合成速度与音质。

只依赖 aiohttp，不加载项目配置，可脱离 xiaozhi-server 直接运行：

    export DASHSCOPE_API_KEY=sk-xxxx
    python tests/bench_qwen_tts.py                 # 默认 3 组模型，各 3 轮
    python tests/bench_qwen_tts.py --rounds 5
    python tests/bench_qwen_tts.py --text "更长的文本，用来测长句吞吐。"

指标说明：
    TTFB     首包延迟（发出请求 -> 收到第一段音频），决定「开口前等待」的主观感受
    总耗时   一句话全部合成完成的时间
    音频时长 合成出的语音实际长度
    RTF      实时率 = 总耗时 / 音频时长，越小越快（<1 表示比实时播放快）
    字符/秒  每秒合成的文本字符数，衡量吞吐

合成结果会写到 /tmp/qwen_tts_bench/ 下的 wav 文件，建议实际听一遍再定音色。
"""

import argparse
import asyncio
import base64
import json
import os
import time
import wave

import aiohttp

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
SPEECH_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"

DEFAULT_TEXT = "今天天气不错，我们下午三点在楼下的咖啡馆聊一下新方案，顺便把上周的进度对一遍。"

CASES = [
    {
        "name": "Qwen3-TTS-Flash",
        "url": DASHSCOPE_URL,
        "model": "qwen3-tts-flash",
        "voice": "Cherry",
        "sample_rate": 24000,
    },
    {
        "name": "Qwen-Audio-3.1-TTS-Flash",
        "url": SPEECH_URL,
        "model": "qwen-audio-3.1-tts-flash",
        "voice": "longanhuan_v3.1",
        "sample_rate": 24000,
    },
    {
        "name": "Qwen-Audio-3.0-TTS-Flash",
        "url": SPEECH_URL,
        "model": "qwen-audio-3.0-tts-flash",
        "voice": "longanhuan_v3.6",
        "sample_rate": 24000,
    },
]

OUT_DIR = "/tmp/qwen_tts_bench"


def build_payload(case, text):
    if case["url"] == DASHSCOPE_URL:
        return {
            "model": case["model"],
            "input": {"text": text, "voice": case["voice"]},
        }
    return {
        "model": case["model"],
        "input": {
            "text": text,
            "voice": case["voice"],
            "format": "pcm",
            "sample_rate": case["sample_rate"],
        },
    }


async def synth_once(session, case, text, timeout=60):
    headers = {
        "Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}",
        "Content-Type": "application/json",
        "X-DashScope-SSE": "enable",
    }
    payload = build_payload(case, text)

    started = time.perf_counter()
    ttfb = None
    pcm = bytearray()
    errors = []

    async with session.post(
        case["url"], headers=headers, data=json.dumps(payload), timeout=timeout
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"HTTP {resp.status}: {body[:200]}")
        buffer = b""
        async for raw in resp.content.iter_any():
            buffer += raw
            while True:
                index = buffer.find(b"\n\n")
                if index < 0:
                    break
                event, buffer = buffer[:index], buffer[index + 2 :]
                for line in event.split(b"\n"):
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == b"[DONE]":
                        continue
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    code = data.get("code")
                    if code:
                        errors.append(f"{code}: {data.get('message')}")
                        continue
                    audio = (data.get("output") or {}).get("audio") or {}
                    if audio.get("data"):
                        pcm.extend(base64.b64decode(audio["data"]))
                        if ttfb is None:
                            ttfb = time.perf_counter() - started
                    if (data.get("output") or {}).get("finish_reason") == "stop":
                        break

    total = time.perf_counter() - started
    if errors:
        raise RuntimeError("; ".join(errors[:2]))
    if ttfb is None:
        raise RuntimeError("未收到音频数据")
    return ttfb, total, bytes(pcm)


def save_wav(path, pcm, sample_rate):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(pcm)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3, help="每个模型重复次数")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="待合成文本")
    args = parser.parse_args()

    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise SystemExit("请先设置环境变量 DASHSCOPE_API_KEY")

    text = args.text
    print(f"文本长度：{len(text)} 字，每个模型跑 {args.rounds} 轮\n")

    results = []
    async with aiohttp.ClientSession() as session:
        for case in CASES:
            ttfb_list, total_list, pcm = [], [], b""
            try:
                for _ in range(args.rounds):
                    ttfb, total, pcm = await synth_once(session, case, text)
                    ttfb_list.append(ttfb)
                    total_list.append(total)
                    await asyncio.sleep(0.3)
            except Exception as e:  # noqa: BLE001
                print(f"{case['name']:<28} 失败：{e}")
                continue

            duration = len(pcm) / (case["sample_rate"] * 2)
            ttfb_avg = sum(ttfb_list) / len(ttfb_list)
            total_avg = sum(total_list) / len(total_list)
            results.append(
                {
                    "name": case["name"],
                    "ttfb": ttfb_avg,
                    "total": total_avg,
                    "duration": duration,
                    "rtf": total_avg / duration if duration else 0,
                    "cps": len(text) / total_avg if total_avg else 0,
                }
            )
            out_file = os.path.join(OUT_DIR, f"{case['model']}.wav")
            save_wav(out_file, pcm, case["sample_rate"])
            print(
                f"{case['name']:<28} TTFB {ttfb_avg * 1000:6.0f} ms | "
                f"总耗时 {total_avg:5.2f}s | 音频 {duration:5.2f}s | "
                f"RTF {total_avg / duration:4.2f} | {len(text) / total_avg:5.1f} 字/秒"
            )

    if results:
        print(f"\n试听文件已写入 {OUT_DIR}/")
        fastest = min(results, key=lambda r: r["ttfb"])
        print(f"首包最快：{fastest['name']}（{fastest['ttfb'] * 1000:.0f} ms）")
        fastest_total = min(results, key=lambda r: r["total"])
        print(f"整体最快：{fastest_total['name']}（{fastest_total['total']:.2f}s）")


if __name__ == "__main__":
    asyncio.run(main())
