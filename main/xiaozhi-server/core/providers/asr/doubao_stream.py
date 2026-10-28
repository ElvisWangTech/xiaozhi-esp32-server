import json
import gzip
import time
import uuid
import asyncio
import websockets
from core.providers.asr.base import ASRProviderBase
from config.logger import setup_logging
from core.providers.asr.dto.dto import InterfaceType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()
# 60ms 一帧。没开口时留约 1.8 秒，避免字头被裁掉；开口后最多留约 6 秒。
_IDLE_PREROLL_FRAMES = 30
_SPEECH_BUFFER_FRAMES = 100


def stash_unsent(buffer, frame, speaking, idle_limit=_IDLE_PREROLL_FRAMES, speech_limit=_SPEECH_BUFFER_FRAMES):
    """连接还没就绪时先把音频留下。静音只留一小段，说话时把这一句都留下。"""
    buffer.append(frame)
    limit = speech_limit if speaking else idle_limit
    if len(buffer) > limit:
        del buffer[:-limit]
    return buffer


def merge_replay(recent, unsent, limit=_SPEECH_BUFFER_FRAMES):
    """连接中途断开时，把已经发出去但还没识别完的音频和新到的音频接在一起。"""
    if not recent:
        return list(unsent)
    merged = list(recent) + list(unsent)
    if len(merged) > limit:
        return merged[-limit:]
    return merged


# 服务端发完音频后，设备缓冲区里往往还剩约 1 秒。这段时间麦会收到扬声器。
_PLAYBACK_TAIL_SECONDS = 1.0
# 约 60ms 一帧。连续几帧都像人声，才当成用户在打断，避免扬声器余音误触发。
_BARGE_IN_FRAMES = 5


def should_commit_definite(text, manual, voice_stop, frame_count):
    """云端已经给出确定文本时就交给对话。短句（如「对」）本地往往凑不满 15 帧。"""
    content = (text or "").strip()
    if not content:
        return False
    if manual:
        return bool(voice_stop) and frame_count > 0
    return True


def playback_blocks_upload(speaking, aec, manual, now, resume_at, hold_tail, tail_seconds=_PLAYBACK_TAIL_SECONDS):
    """没有回声消除时，播放期间不上送麦克风。聊天再多留一小段，避开设备里还没播完的声音。"""
    if aec or manual:
        return False, resume_at
    if speaking:
        if hold_tail:
            return True, now + tail_seconds
        return True, now
    if now < resume_at:
        return True, resume_at
    return False, resume_at


class ASRProvider(ASRProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__()
        self.interface_type = InterfaceType.STREAM
        self.config = config
        self.text = ""
        self.asr_ws = None
        self.forward_task = None
        self.is_processing = False  # 添加处理状态标志
        self._is_stopping = False  # 添加停止标志，防止竞态条件
        self._connect_lock = asyncio.Lock()
        # 提示音收尾时才建连。连上之后一直听，不在每句结束时断开。
        # 满 6 秒没有任何包时补一包静音，避开火山 8 秒等包超时（45000081，服务端固定，不能加长）。
        self._awaiting_listen = False
        self._ignore_results = False
        self._last_audio_at = 0.0
        self._keepalive_task = None
        self._silence_keepalive_logged = False
        self._resume_upload_at = 0.0
        self._playback_drop_logged = False
        self._drop_replay = False
        self._barge_frames = 0
        self._barge_in = False
        self._barge_abort_sent = False
        # 播放期间先攒着的人声。凑够连续几帧再整段送上，避免只补发触发那一帧。
        self._barge_preroll = []
        # 还没送到当前连接上的 PCM。换连接时整段补发，不依赖只剩 10 帧的 asr_audio。
        self._unsent = []
        self._recent = []
        self._hold_replay = False
        # 建连和发包都在后台做。麦克风线程若在这里等网络，后续帧会堆在队列里，听起来像卡住。
        self._upload_task = None
        self._want_connect = False

        # 配置参数
        self.appid = str(config.get("appid"))
        self.access_token = config.get("access_token")
        # 资源ID，用于区分不同的ASR模型（默认1.0模型小时版，v2版本使用seed-asr）
        self.resource_id = config.get("resource_id", "volc.bigasr.sauc.duration")

        self.boosting_table_name = config.get("boosting_table_name", "")
        self.correct_table_name = config.get("correct_table_name", "")
        self.output_dir = config.get("output_dir", "tmp/")
        self.delete_audio_file = delete_audio_file

        # 火山引擎ASR配置
        enable_multilingual = config.get("enable_multilingual", False)
        self.enable_multilingual = (
            False if str(enable_multilingual).lower() == "false" else True
        )
        if self.enable_multilingual:
            self.ws_url = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
        else:
            self.ws_url = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
        self.uid = config.get("uid", "streaming_asr_service")
        self.workflow = config.get(
            "workflow", "audio_in,resample,partition,vad,fe,decode,itn,nlu_punctuate"
        )
        self.result_type = config.get("result_type", "single")
        self.format = config.get("format", "pcm")
        self.codec = config.get("codec", "pcm")
        self.rate = config.get("sample_rate", 16000)
        # language参数仅在多语种模式(bigmodel_nostream)下有效
        self.language = config.get("language") if self.enable_multilingual else None
        self.bits = config.get("bits", 16)
        self.channel = config.get("channel", 1)
        self.auth_method = config.get("auth_method", "token")
        self.secret = config.get("secret", "access_secret")
        end_window_size = config.get("end_window_size")
        self.end_window_size = int(end_window_size) if end_window_size else 200

    async def open_audio_channels(self, conn):
        await super().open_audio_channels(conn)

    def _session_alive(self):
        return self.asr_ws is not None and self.is_processing and not self._is_stopping

    async def ensure_connected(self, conn: "ConnectionHandler"):
        """提示音快结束时建连。之后只上传真实音频，用户开口时不再握手。"""
        self._ignore_results = True
        self._awaiting_listen = True
        if self._session_alive():
            logger.bind(tag=TAG).info("ASR服务已连接，询问手机号前无需重建")
            return
        await self._open_session(conn, flush_cache=True)

    def finish_prompt_guard(self):
        """提示音播完，之后的识别结果才当作手机号。"""
        self._ignore_results = False

    async def _open_session(self, conn: "ConnectionHandler", flush_cache=True):
        async with self._connect_lock:
            if self._session_alive():
                return True
            opened = None
            try:
                self.is_processing = True
                headers = self.token_auth() if self.auth_method == "token" else None
                logger.bind(tag=TAG).info("正在连接ASR服务")

                opened = await websockets.connect(
                    self.ws_url,
                    additional_headers=headers,
                    max_size=1000000000,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=10,
                )

                request_params = self.construct_request(str(uuid.uuid4()))
                payload_bytes = gzip.compress(str.encode(json.dumps(request_params)))
                full_client_request = self.generate_header()
                full_client_request.extend((len(payload_bytes)).to_bytes(4, "big"))
                full_client_request.extend(payload_bytes)

                logger.bind(tag=TAG).info(f"发送初始化请求: {request_params}")
                await opened.send(full_client_request)

                init_res = await opened.recv()
                result = self.parse_response(init_res)
                logger.bind(tag=TAG).info(f"收到初始化响应: {result}")

                if "code" in result and result["code"] != 1000:
                    error_msg = f"ASR服务初始化失败: {result.get('payload_msg', {}).get('error', '未知错误')}"
                    logger.bind(tag=TAG).error(error_msg)
                    raise Exception(error_msg)

                self.asr_ws = opened
                self._is_stopping = False
                self._last_audio_at = time.monotonic()
                self._silence_keepalive_logged = False
                self._cancel_keepalive()
                self._keepalive_task = asyncio.create_task(
                    self._silence_before_packet_timeout(opened)
                )
                self.forward_task = asyncio.create_task(self._forward_asr_results(conn, opened))

                if flush_cache:
                    await self._flush_unsent(opened)
                return True
            except Exception as e:
                logger.bind(tag=TAG).error(f"建立ASR连接失败: {str(e)}")
                if hasattr(e, "__cause__") and e.__cause__:
                    logger.bind(tag=TAG).error(f"错误原因: {str(e.__cause__)}")
                if opened is not None:
                    try:
                        await opened.close()
                    except Exception:
                        pass
                if self.asr_ws is opened:
                    self.asr_ws = None
                    self.is_processing = False
                elif self.asr_ws is None:
                    self.is_processing = False
                return False

    def _stash(self, conn, pcm_frame, audio_have_voice):
        speaking = bool(
            audio_have_voice
            or getattr(conn, "client_have_voice", False)
            or self._awaiting_listen
            or self._hold_replay
        )
        stash_unsent(self._unsent, pcm_frame, speaking)

    def _preserve_recent_for_replay(self):
        """这次连接没正常收完，下一轮连上后把开头补回去。"""
        if not self._recent:
            return
        self._unsent = merge_replay(self._recent, self._unsent)
        self._recent = []
        self._hold_replay = True

    async def _flush_unsent(self, ws):
        frames = self._unsent
        if not frames:
            return
        self._unsent = []
        self._hold_replay = False
        logger.bind(tag=TAG).info(f"ASR已连接，补发缓冲音频 {len(frames)} 帧")
        for index, pcm in enumerate(frames):
            try:
                await self._send_pcm(ws, pcm)
            except Exception as exc:
                self._unsent = frames[index:]
                self._hold_replay = True
                logger.bind(tag=TAG).info(f"补发缓冲音频失败: {exc}")
                raise

    def _kick_uploader(self, conn, connect=False):
        """把积压的音频交给后台任务。调用方不要 await，否则麦克风线程会停在发包上。"""
        if connect:
            self._want_connect = True
        task = self._upload_task
        if task is not None and not task.done():
            return
        self._upload_task = asyncio.create_task(self._upload_pending(conn))

    async def _upload_pending(self, conn):
        try:
            while self._unsent and not conn.stop_event.is_set() and not self._is_stopping:
                if not self._session_alive():
                    if not self._want_connect:
                        return
                    pending = len(self._unsent)
                    opened = await self._open_session(conn, flush_cache=False)
                    if not opened or not self._session_alive():
                        self._want_connect = False
                        return
                    self._awaiting_listen = False
                    if pending:
                        logger.bind(tag=TAG).info(f"ASR已连接，补发缓冲音频 {pending} 帧")
                    continue
                frame = self._unsent.pop(0)
                try:
                    await self._send_pcm(self.asr_ws, frame)
                except asyncio.CancelledError:
                    if not self._drop_replay and not self._is_stopping:
                        self._unsent.insert(0, frame)
                    raise
                except Exception as exc:
                    self._unsent.insert(0, frame)
                    self._hold_replay = True
                    self._want_connect = True
                    logger.bind(tag=TAG).info(f"发送音频数据时发生错误: {exc}")
                    return
        finally:
            current = asyncio.current_task()
            if self._upload_task is current:
                self._upload_task = None

    def _cancel_uploader(self):
        self._want_connect = False
        task = self._upload_task
        self._upload_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _send_pcm(self, ws, pcm_frame, remember=True):
        payload = gzip.compress(pcm_frame)
        audio_request = bytearray(self.generate_audio_default_header())
        audio_request.extend(len(payload).to_bytes(4, "big"))
        audio_request.extend(payload)
        await ws.send(audio_request)
        self._last_audio_at = time.monotonic()
        if remember:
            self._recent.append(pcm_frame)
            if len(self._recent) > _SPEECH_BUFFER_FRAMES:
                del self._recent[:-_SPEECH_BUFFER_FRAMES]

    def _cancel_keepalive(self, ws=None):
        task = self._keepalive_task
        if task is None:
            return
        if ws is not None and self.asr_ws is not None and self.asr_ws is not ws:
            return
        self._keepalive_task = None
        if not task.done():
            task.cancel()

    async def _silence_before_packet_timeout(self, ws):
        """距上次发包满 6 秒就补 200ms 静音。只在断流时发送，不铺满整段等待。"""
        frame = b"\x00" * int(self.rate * 2 * 0.2)
        try:
            while self.asr_ws is ws and not self._is_stopping:
                await asyncio.sleep(1)
                if self.asr_ws is not ws or self._is_stopping:
                    return
                if time.monotonic() - self._last_audio_at < 6:
                    continue
                try:
                    await self._send_pcm(ws, frame, remember=False)
                except Exception as exc:
                    logger.bind(tag=TAG).info(f"发送静音保活失败: {exc}")
                    return
                if not self._silence_keepalive_logged:
                    self._silence_keepalive_logged = True
                    logger.bind(tag=TAG).info("ASR超过6秒没有音频，已发送静音包以免等包超时")
        except asyncio.CancelledError:
            return

    def _playback_guard(self, conn):
        """正在播，或设备缓冲区那一小段尾巴。这段时间麦里多半是扬声器。"""
        manual = getattr(conn, "client_listen_mode", "auto") == "manual"
        if manual or getattr(conn, "client_aec", False):
            return False
        if bool(getattr(conn, "client_is_speaking", False)):
            return True
        return time.monotonic() < self._resume_upload_at

    def _barge_in_allowed(self, conn):
        """收集手机号和验证码时不把提示音当成用户在打断。"""
        handler = getattr(conn, "voice_activation_handler", None)
        if handler is None:
            return True
        allow = getattr(handler, "_allow_barge_in", None)
        if not callable(allow):
            return True
        try:
            return bool(allow())
        except Exception:
            return True

    def _push_preroll(self, frame):
        self._barge_preroll.append(frame)
        if len(self._barge_preroll) > _BARGE_IN_FRAMES:
            del self._barge_preroll[:-_BARGE_IN_FRAMES]

    def _commit_preroll(self, conn):
        frames = self._barge_preroll
        self._barge_preroll = []
        if not frames:
            return
        logger.bind(tag=TAG).info(f"用户打断，上送缓冲音频 {len(frames)} 帧")
        for frame in frames:
            self._stash(conn, frame, True)

    def _note_barge_in(self, conn, audio_have_voice):
        """播放中用户持续出声：开始上送，并打断当前这句。手动模式和回声消除不走这里。"""
        if not self._playback_guard(conn) or not self._barge_in_allowed(conn):
            self._barge_frames = 0
            return False
        if not audio_have_voice:
            self._barge_frames = 0
            return False
        self._barge_frames += 1
        if self._barge_frames < _BARGE_IN_FRAMES:
            return False
        self._barge_in = True
        return self._barge_frames == _BARGE_IN_FRAMES

    def _schedule_abort(self, conn):
        if self._barge_abort_sent:
            return
        self._barge_abort_sent = True
        asyncio.create_task(self._run_abort(conn))

    async def _run_abort(self, conn):
        from core.handle.abortHandle import handleAbortMessage

        try:
            await handleAbortMessage(conn)
        except Exception as exc:
            logger.bind(tag=TAG).info(f"打断播放失败: {exc}")

    def _suppress_playback(self, conn):
        """播放回答时麦里主要是扬声器。没有回声消除就先不上送，连接保持。用户打断后继续上送。"""
        if self._barge_in:
            self._playback_drop_logged = False
            return False
        hold_tail = getattr(conn, "voice_activation_handler", None) is None
        blocked, self._resume_upload_at = playback_blocks_upload(
            bool(getattr(conn, "client_is_speaking", False)),
            bool(getattr(conn, "client_aec", False)),
            getattr(conn, "client_listen_mode", "auto") == "manual",
            time.monotonic(),
            self._resume_upload_at,
            hold_tail,
        )
        if blocked and not self._playback_drop_logged:
            self._playback_drop_logged = True
            logger.bind(tag=TAG).info("播放中不上送麦克风，ASR连接保持")
        if not blocked:
            self._playback_drop_logged = False
        return blocked

    async def receive_audio(self, conn: "ConnectionHandler", pcm_frame, audio_have_voice):
        # 先调用父类方法处理基础逻辑
        await super().receive_audio(conn, pcm_frame, audio_have_voice)

        # 麦克风线程会一直等到这个协程返回。建连、补发、打断都放到后台，
        # 否则「ASR已连接，补发缓冲音频」会把后面的人声堵在队列里。
        if self._suppress_playback(conn):
            if audio_have_voice and self._barge_in_allowed(conn):
                self._push_preroll(pcm_frame)
                if self._note_barge_in(conn, True):
                    self._commit_preroll(conn)
                    self._schedule_abort(conn)
                    self._kick_uploader(conn, connect=not self._session_alive())
                elif self._barge_frames == 0:
                    self._barge_preroll.clear()
                return
            self._barge_preroll.clear()
            self._barge_frames = 0
            conn.client_have_voice = False
            if len(conn.asr_audio) > 10:
                del conn.asr_audio[:-10]
            return

        self._barge_preroll.clear()
        self._stash(conn, pcm_frame, audio_have_voice)
        connect = bool(
            audio_have_voice
            or self._awaiting_listen
            or self._hold_replay
            or self._barge_in
        )
        if self._session_alive():
            self._awaiting_listen = False
        if self._session_alive() or connect:
            self._kick_uploader(conn, connect=connect and not self._session_alive())

    async def _forward_asr_results(self, conn: "ConnectionHandler", ws):
        try:
            while ws and not conn.stop_event.is_set():
                # 获取当前连接的音频数据
                audio_data = conn.asr_audio
                try:
                    response = await ws.recv()
                    result = self.parse_response(response)
                    logger.bind(tag=TAG).debug(f"收到ASR结果: {result}")
                    # 提示音尾音可能被麦收进去。播完之前不把结果当成手机号，也不因此关掉连接。
                    if self._ignore_results:
                        payload_msg = result.get("payload_msg")
                        if isinstance(payload_msg, dict) and "error" in payload_msg:
                            error_msg = payload_msg.get("error", "未知错误")
                            logger.bind(tag=TAG).error(f"ASR服务返回错误: {error_msg}")
                            if self.asr_ws is ws:
                                self.is_processing = False
                            break
                        continue

                    if "payload_msg" in result:
                        payload = result["payload_msg"]
                        # 检查是否是错误码1013（无有效语音）
                        if "code" in payload and payload["code"] == 1013:
                            # 静默处理，不记录错误日志
                            continue

                        if "result" in payload:
                            utterances = payload["result"].get("utterances", [])
                            # 检查duration和空文本的情况
                            if (
                                not self.enable_multilingual  # 注意：多语种模式不返回中间结果，需要等待最终结果
                                and payload.get("audio_info", {}).get("duration", 0)
                                > 2000
                                and not utterances
                                and not payload["result"].get("text")
                                and conn.client_listen_mode != "manual"
                            ):
                                # 连接一直开着时，两句之间的空结果是正常的，不能因此关掉。
                                continue

                            # 专门处理没有文本的识别结果（手动模式下可能已经识别完成但是没松按键）
                            elif not payload["result"].get("text") and not utterances:
                                # 多语种模式会持续返回空文本，直到最后返回完整结果，所以需要排除
                                if self.enable_multilingual:
                                    continue

                                if conn.client_listen_mode == "manual" and conn.client_voice_stop and len(audio_data) > 15:
                                    logger.bind(tag=TAG).debug("消息结束收到停止信号，触发处理")
                                    await self.handle_voice_stop(conn, audio_data)
                                    break

                            for utterance in utterances:
                                if utterance.get("definite", False):
                                    current_text = utterance["text"]
                                    logger.bind(tag=TAG).info(
                                        f"识别到文本: {current_text}"
                                    )

                                    # 手动模式下累积识别结果
                                    if conn.client_listen_mode == "manual":
                                        if self.text:
                                            self.text += current_text
                                        else:
                                            self.text = current_text

                                        # 在接收消息中途时收到停止信号
                                        if conn.client_voice_stop and len(audio_data) > 0:
                                            logger.bind(tag=TAG).debug("消息中途收到停止信号，触发处理")
                                            await self.handle_voice_stop(conn, audio_data)
                                        break
                                    else:
                                        # 自动模式下直接覆盖。这句交给对话后连接继续听下一句。
                                        # 云端已经断句，不再要求本地缓冲超过 15 帧，否则「对」这类短句会被丢掉。
                                        self.text = current_text
                                        if should_commit_definite(
                                            current_text,
                                            False,
                                            False,
                                            len(audio_data),
                                        ):
                                            await self.handle_voice_stop(
                                                conn, list(audio_data)
                                            )
                                            conn.reset_audio_states()
                                    break
                        elif "error" in payload:
                            error_msg = payload.get("error", "未知错误")
                            logger.bind(tag=TAG).error(f"ASR服务返回错误: {error_msg}")
                            break

                except websockets.ConnectionClosed:
                    logger.bind(tag=TAG).info("ASR服务连接已关闭")
                    if self.asr_ws is ws:
                        self.is_processing = False
                    break
                except Exception as e:
                    logger.bind(tag=TAG).error(f"处理ASR结果时发生错误: {str(e)}")
                    if hasattr(e, "__cause__") and e.__cause__:
                        logger.bind(tag=TAG).error(f"错误原因: {str(e.__cause__)}")
                    if self.asr_ws is ws:
                        self.is_processing = False
                    break

        except Exception as e:
            logger.bind(tag=TAG).error(f"ASR结果转发任务发生错误: {str(e)}")
            if hasattr(e, "__cause__") and e.__cause__:
                logger.bind(tag=TAG).error(f"错误原因: {str(e.__cause__)}")
        finally:
            # 询问手机号前可能已经换上新连接，旧任务不能把新连接关掉。
            if self.asr_ws is not None and self.asr_ws is not ws:
                return
            self._cancel_keepalive(ws)
            if self._drop_replay:
                self._recent = []
                self._unsent = []
                self._hold_replay = False
                self._drop_replay = False
            else:
                self._preserve_recent_for_replay()
            if self.asr_ws is ws:
                try:
                    await ws.close()
                except Exception:
                    pass
                self.asr_ws = None
            self.is_processing = False
            self._is_stopping = False
            # 重置所有音频相关状态
            conn.reset_audio_states()

    def stop_ws_connection(self):
        """一句已经识别完。连接留着听下一句，避免每次说话都重新握手。"""
        self._recent = []
        self._unsent = []
        self._hold_replay = False
        self._barge_frames = 0
        self._barge_in = False
        self._barge_abort_sent = False
        self._barge_preroll = []
        if self._session_alive():
            logger.bind(tag=TAG).info("本句已识别，ASR连接保持")

    def disconnect(self):
        """真正关掉计费连接。长时间无回应或设备断开时用。"""
        self._drop_replay = True
        self._cancel_keepalive()
        self._cancel_uploader()
        self._recent = []
        self._unsent = []
        self._barge_preroll = []
        self._hold_replay = False
        if self.asr_ws:
            asyncio.create_task(self.asr_ws.close())
            self.asr_ws = None
        self.is_processing = False
        self._is_stopping = False

    async def _send_stop_request(self):
        """发送最后一个音频帧以通知服务器结束"""
        self._is_stopping = True  # 先标记为停止状态，阻止后续音频发送
        if self.asr_ws:
            try:
                # 发送结束标记的音频帧（gzip压缩的空数据）
                empty_payload = gzip.compress(b"")
                last_audio_request = bytearray(
                    self.generate_last_audio_default_header()
                )
                last_audio_request.extend(len(empty_payload).to_bytes(4, "big"))
                last_audio_request.extend(empty_payload)
                await self.asr_ws.send(last_audio_request)
                logger.bind(tag=TAG).debug("已发送结束音频帧")
            except Exception as e:
                logger.bind(tag=TAG).debug(f"发送结束音频帧时出错: {e}")

    def construct_request(self, reqid):
        req = {
            "app": {
                "appid": self.appid,
                "token": self.access_token,
            },
            "user": {"uid": self.uid},
            "request": {
                "reqid": reqid,
                "workflow": self.workflow,
                "show_utterances": True,
                "result_type": self.result_type,
                "sequence": 1,
                "end_window_size": self.end_window_size,
                "corpus": {
                    "boosting_table_name": self.boosting_table_name,
                    "correct_table_name": self.correct_table_name,
                }
            },
            "audio": {
                "format": self.format,
                "codec": self.codec,
                "rate": self.rate,
                "bits": self.bits,
                "channel": self.channel,
                "sample_rate": self.rate,
            },
        }

        # language参数仅在多语种模式下添加
        if self.enable_multilingual and self.language:
            req["audio"]["language"] = self.language

        logger.bind(tag=TAG).debug(
            f"构造请求参数: {json.dumps(req, ensure_ascii=False)}"
        )
        return req

    def token_auth(self):
        return {
            "X-Api-App-Key": self.appid,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }

    def generate_header(
        self,
        version=0x01,
        message_type=0x01,
        message_type_specific_flags=0x00,
        serial_method=0x01,
        compression_type=0x01,
        reserved_data=0x00,
        extension_header: bytes = b"",
    ):
        header = bytearray()
        header_size = int(len(extension_header) / 4) + 1
        header.append((version << 4) | header_size)
        header.append((message_type << 4) | message_type_specific_flags)
        header.append((serial_method << 4) | compression_type)
        header.append(reserved_data)
        header.extend(extension_header)
        return header

    def generate_audio_default_header(self):
        return self.generate_header(
            version=0x01,
            message_type=0x02,
            message_type_specific_flags=0x00,
            serial_method=0x01,
            compression_type=0x01,
        )

    def generate_last_audio_default_header(self):
        return self.generate_header(
            version=0x01,
            message_type=0x02,
            message_type_specific_flags=0x02,
            serial_method=0x01,
            compression_type=0x01,
        )

    def parse_response(self, res: bytes) -> dict:
        try:
            # 检查响应长度
            if len(res) < 4:
                logger.bind(tag=TAG).error(f"响应数据长度不足: {len(res)}")
                return {"error": "响应数据长度不足"}

            # 获取消息头
            header = res[:4]
            message_type = header[1] >> 4

            # 如果是错误响应
            if message_type == 0x0F:  # SERVER_ERROR_RESPONSE
                code = int.from_bytes(res[4:8], "big", signed=False)
                msg_length = int.from_bytes(res[8:12], "big", signed=False)
                error_msg = json.loads(res[12:].decode("utf-8"))
                return {
                    "code": code,
                    "msg_length": msg_length,
                    "payload_msg": error_msg,
                }

            # 获取JSON数据
            try:
                # 检查字节8-11是否为有效的JSON长度字段
                # 格式：4字节头 + 4字节序列号 + 4字节长度 + JSON数据
                length = int.from_bytes(res[8:12], "big")
                if length > 0 and length <= len(res) - 12:
                    # 有长度字段，从字节12开始读取指定长度的JSON
                    json_data = res[12:12 + length].decode("utf-8")
                else:
                    # 无长度字段或长度无效，尝试直接解析
                    json_data = res[8:].decode("utf-8")
                result = json.loads(json_data)
                logger.bind(tag=TAG).debug(f"成功解析JSON响应: {result}")
                return {"payload_msg": result}
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                logger.bind(tag=TAG).error(f"JSON解析失败: {str(e)}")
                logger.bind(tag=TAG).error(f"原始数据: {res}")
                raise

        except Exception as e:
            logger.bind(tag=TAG).error(f"解析响应失败: {str(e)}")
            logger.bind(tag=TAG).error(f"原始响应数据: {res.hex()}")
            raise

    async def speech_to_text(self, opus_data, session_id, artifacts=None):
        result = self.text
        self.text = ""  # 清空text
        return result, None

    async def close(self):
        """资源清理方法"""
        self._drop_replay = True
        self._cancel_keepalive()
        self._cancel_uploader()
        self._recent = []
        self._unsent = []
        self._barge_preroll = []
        self._hold_replay = False
        self._is_stopping = True
        ws = self.asr_ws
        self.asr_ws = None
        self.is_processing = False
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        if self.forward_task:
            self.forward_task.cancel()
            try:
                await self.forward_task
            except asyncio.CancelledError:
                pass
            self.forward_task = None
        self.is_processing = False
