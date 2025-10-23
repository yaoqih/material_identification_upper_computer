from __future__ import annotations
import time
import threading
from enum import Enum
from typing import Callable, Optional, Tuple, List, Dict, Set, Union

from app.comm.protocol import (
    FrameType,
    ProtocolFrame,
    encode_frame,
    decode_stream,
    build_af,
    build_a1,
    build_a0,
    AckCode,
)
from app.logs.logger import get_logger, hex_dump, get_device_info_logger
from app.storage.config import ConfigRepo

RequestHandler = Callable[[], Union[Tuple[List[int], Optional[List[int]]], Tuple[List[int], Optional[List[int]], Optional[List[int]]]]]

class SessionState(Enum):
    """会话状态机"""
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    OFFLINE = "OFFLINE"

class SerialSession:
    """
    最小会话实现（基线：单串口、A1仅index、无任务空清单）：
    - B1 请求：AF(00) → request_handler() → A1（分包） → 等待BF
    - B0 心跳：回 AF(00)
    - A0 心跳发送：send_heartbeat()
    - 重复B1（同SEQ）：仅重发AF，避免重复下发A1
    """
    def __init__(
        self,
        port,
        request_handler: Optional[RequestHandler] = None,
        name: str = "session",
        ack_timeout_ms: int = 1000,
        cmd_timeout_ms: int = 2000,
        bytes_per_frame: int = 512,
        inter_frame_gap_ms: int = 10,
    ) -> None:
        self.port = port
        self.port.set_rx_callback(self._on_bytes)
        self.logger = get_logger(name)
        self._rx_buf = bytearray()
        self._awaiting: Set[int] = set()
        self._acked: Dict[int, int] = {}  # seq -> code
        self._seq = 0
        self._request_handler: RequestHandler = request_handler or (lambda: ([], None))
        self.last_remote_seq: Optional[int] = None

        # 基线参数
        self.ack_timeout_ms = int(ack_timeout_ms)
        self.cmd_timeout_ms = int(cmd_timeout_ms)
        self.bytes_per_frame = int(bytes_per_frame)
        self.inter_frame_gap_ms = int(inter_frame_gap_ms)

        # 幂等控制：记录最近一次B1序号
        self._last_b1_seq: Optional[int] = None

        # 状态机与心跳
        self.state: SessionState = SessionState.DISCONNECTED
        self._offline_failures: int = 0
        cfg = ConfigRepo().load()
        comm_cfg = cfg.get("comm", {})
        # 周期和阈值以配置驱动，避免硬编码；仅使用 comm.*（参见 [docs/通信约定.md](docs/通信约定.md:68)）
        self.heartbeat_interval_sec: float = float(comm_cfg.get("heartbeat_interval_seconds", 10))
        self.offline_threshold: int = int(comm_cfg.get("offline_failure_threshold", 10))
        # ACK/BF 重试策略（项目约定配置驱动；未见于文档，故仅在注释中说明）
        rty = comm_cfg.get("retry", {})
        self.retry_enabled: bool = bool(rty.get("enabled", True))
        self.retry_ack_timeout_ms: int = int(rty.get("ack_timeout_ms", 300))
        self.retry_max_attempts: int = int(rty.get("max_attempts", 3))
        self.retry_backoff_ms: int = int(rty.get("backoff_ms", 50))
        # 若调用方未显式缩短构造参数 ack_timeout_ms（仍为默认 1000），则允许用配置覆盖默认超时
        try:
            if int(ack_timeout_ms) == 1000 and self.ack_timeout_ms == 1000 and "ack_timeout_ms" in rty:
                self.ack_timeout_ms = int(rty["ack_timeout_ms"])
        except Exception:
            pass

        # 兼容开关：重复B1应答模式
        self._duplicate_ack_mode = str(comm_cfg.get("duplicate_ack_mode", "duplicate_code"))

        # 运行时可调参数（配置优先，覆盖构造形参值；默认由 ConfigRepo._apply_defaults() 保底）
        try:
            self.bytes_per_frame = int(comm_cfg.get("bytes_per_frame", self.bytes_per_frame))
            self.inter_frame_gap_ms = int(comm_cfg.get("inter_frame_gap_ms", self.inter_frame_gap_ms))
            self.cmd_timeout_ms = int(comm_cfg.get("cmd_timeout_ms", self.cmd_timeout_ms))
            # 非重试场景的通用 ACK 等待超时（当前未在 send_and_wait_ack 中使用）
            self._ack_timeout_ms = int(comm_cfg.get("ack_timeout_ms", 1000))
        except Exception:
            # 保守兜底，不影响主流程
            pass

        # 最近一次 B1 的 AF code（用于 echo_last）
        self._last_b1_ack_code: int = int(AckCode.OK)

        # HEX 捕获配置
        log_cfg = cfg.get("logging", {})
        _hx = (log_cfg.get("hex") or {})
        self._hex_capture = bool(_hx.get("capture", False))
        self._hex_incoming = bool(_hx.get("incoming", True))
        self._hex_outgoing = bool(_hx.get("outgoing", True))
        try:
            self._hex_max_bytes = int(_hx.get("max_bytes", 1024))
        except Exception:
            self._hex_max_bytes = 1024

        self._hb_stop = threading.Event()
        self._hb_thread: Optional[threading.Thread] = None
        # 默认按配置启用心跳调度（幂等防重复）
        if bool(comm_cfg.get("enable_heartbeat", True)):
            try:
                self.start_heartbeat(self.heartbeat_interval_sec)
            except Exception as e:
                self.logger.debug(f"auto start heartbeat failed: {e}")

        # 乱序处理
        self.expected_remote_seq: Optional[int] = None

        # 可选派发结果钩子（成功派发后归档用）
        self.on_a1_result: Optional[Callable[[bool], None]] = None

    def _set_state(self, new_state: "SessionState") -> None:
        if new_state != self.state:
            self.logger.info(f"Session state {self.state.value} -> {new_state.value}")
            self.state = new_state

    def next_seq(self) -> int:
        s = self._seq
        self._seq = (self._seq + 1) & 0xFFFF
        return s

    def _send_frame(self, frame: ProtocolFrame) -> None:
        blob = encode_frame(frame)
        # HEX 捕获（发送）
        if getattr(self, "_hex_capture", False) and getattr(self, "_hex_outgoing", True):
            try:
                _hx = hex_dump(blob, limit=getattr(self, "_hex_max_bytes", 1024))
                self.logger.debug(f"TX HEX: {_hx}")
            except Exception:
                pass
        self.port.write_bytes(blob)
        try:
            tname = FrameType(frame.type).name  # type: ignore[arg-type]
        except Exception:
            tname = f"0x{int(frame.type):02X}"
        self.logger.debug(f"TX {tname} seq={frame.seq} len={len(frame.val)}")

    def send_and_wait_ack(self, frame: ProtocolFrame, timeout_ms: Optional[int] = None) -> bool:
        """
        实现 ACK 等待重试（由 comm.retry.* 配置驱动）：
        - 参考 [docs/通信约定.md](docs/通信约定.md:64) 的在线判定：收到任何ACK视为在线，失败计数清零；
        - 重试策略为项目约定：可配置 enabled/ack_timeout_ms/max_attempts/backoff_ms。
        - 超时优先级：调用方参数 > 会话属性(self.ack_timeout_ms)。配置对 ack_timeout_ms 的影响在 __init__ 已处理，避免破坏既有语义。
        - 心跳调度线程中按单次尝试执行，不引入重试，保持心跳节奏与离线判定的一致性；显式调用 send_heartbeat() 仍按重试策略执行。
        """
        use_retry = getattr(self, "retry_enabled", True)
        attempts = max(1, int(getattr(self, "retry_max_attempts", 3))) if use_retry else 1
        backoff_ms = int(getattr(self, "retry_backoff_ms", 50)) if use_retry else 0
        # 单次尝试的等待时长
        per_try_timeout = int(timeout_ms if timeout_ms is not None else getattr(self, "ack_timeout_ms", 1000))

        # 心跳线程内关闭重试（仅用于周期性心跳；不影响显式调用 send_heartbeat() 的重试用例）
        try:
            if frame.type == FrameType.A0 and self._hb_thread is not None and threading.current_thread() == self._hb_thread:
                attempts = 1
                backoff_ms = 0
        except Exception:
            # 保守兜底，不影响主流程
            pass

        exp = frame.seq
        for i in range(attempts):
            self._awaiting.add(exp)
            self._send_frame(frame)
            deadline = time.monotonic() + (per_try_timeout / 1000.0)
            while time.monotonic() < deadline:
                if exp in self._acked:
                    code = self._acked.pop(exp)
                    self._awaiting.discard(exp)
                    # 收到任何ACK视为在线，失败计数清零（参见文档在线判定）
                    self._offline_failures = 0
                    if self.state != SessionState.CONNECTED:
                        self._set_state(SessionState.CONNECTED)
                    return code == int(AckCode.OK)
                time.sleep(0.001)
            # 本次尝试超时
            self._awaiting.discard(exp)
            if i < (attempts - 1):
                # backoff 间隔：确保不阻塞串口接收线程
                if backoff_ms > 0:
                    time.sleep(backoff_ms / 1000.0)
                continue
            # 最后一次仍超时：计入失败并按阈值置 OFFLINE
            self._offline_failures += 1
            if self._offline_failures >= self.offline_threshold:
                self._set_state(SessionState.OFFLINE)
            return False

    def send_heartbeat(self, timeout_ms: Optional[int] = None) -> bool:
        f = build_a0()
        return self.send_and_wait_ack(f, timeout_ms=timeout_ms)

    def start_heartbeat(self, interval_sec: Optional[float] = None) -> None:
        """启动心跳后台线程；默认由配置自动启用（参见 [docs/通信约定.md](docs/通信约定.md:64)）"""
        itv = float(interval_sec if interval_sec is not None else self.heartbeat_interval_sec)
        if self._hb_thread and self._hb_thread.is_alive():
            return
        self._hb_stop.clear()

        def _runner() -> None:
            # 失败计数与在线判定由 send_and_wait_ack() 统一处理，避免重复累计
            # 心跳调度遵循固定周期（参见 [docs/通信约定.md](docs/通信约定.md:64)）：
            # 若一次发送耗时超过周期，则不额外等待，避免“耗时+周期”叠加导致节奏过慢。
            next_time = time.monotonic()
            while not self._hb_stop.is_set():
                self.send_heartbeat()
                next_time += itv
                now = time.monotonic()
                delay = next_time - now
                if delay > 0:
                    time.sleep(delay)
                else:
                    # 抢占下一周期，修正时间漂移
                    next_time = now

        self._hb_thread = threading.Thread(target=_runner, name=f"{self.logger.name}-hb", daemon=True)
        self._hb_thread.start()
        self.logger.info(f"Heartbeat scheduler started interval={itv}s")

    def stop_heartbeat(self) -> None:
        """停止心跳后台线程"""
        if self._hb_thread:
            self._hb_stop.set()
            self.logger.info("Heartbeat scheduler stopping")
            # 不阻断等待，避免影响串口线程
            self._hb_thread = None

    def _on_bytes(self, data: bytes) -> None:
        self._rx_buf.extend(data)
        # HEX 捕获（接收）
        if getattr(self, "_hex_capture", False) and getattr(self, "_hex_incoming", True):
            try:
                _hx = hex_dump(data, limit=getattr(self, "_hex_max_bytes", 1024))
                self.logger.debug(f"RX HEX: {_hx}")
            except Exception:
                pass

        # 协议层错误回调：未知TYPE/CHECK失败/长度/VAL 错误时回 AF
        def _on_err(code: AckCode, seq: int) -> None:
            try:
                self.logger.debug(f"decode error -> AF code=0x{int(code):02X} seq={seq}")
                self._send_frame(build_af(seq=seq, code=code))
            except Exception as e:
                self.logger.debug(f"send AF on decode error failed: {e}")

        # 设备信息（非协议）日志：按 ASCII 写入独立日志文件
        def _on_g(chunk: bytes) -> None:
            try:
                msg = chunk.decode("ascii", errors="replace")
            except Exception:
                msg = repr(chunk)
            try:
                get_device_info_logger().info(msg)
            except Exception as e:
                self.logger.debug(f"device-info log failed: {e}")

        frames = decode_stream(
            self._rx_buf,
            on_error=_on_err,
            on_garbage=_on_g,
        )
        for fr in frames:
            self._handle_frame(fr)

    def _handle_frame(self, fr: ProtocolFrame) -> None:
        # 记录对端SEQ
        self.last_remote_seq = fr.seq

        if fr.type == FrameType.BF:
            # 收到对端应答，记录结果码并视为在线
            self._acked[fr.seq] = fr.val[0] if fr.val else 0
            self._offline_failures = 0
            if self.state != SessionState.CONNECTED:
                self._set_state(SessionState.CONNECTED)
            return

        if fr.type == FrameType.B0:
            # 设备心跳，回 AF
            self._send_frame(build_af(seq=fr.seq, code=AckCode.OK))
            self._offline_failures = 0
            if self.state != SessionState.CONNECTED:
                self._set_state(SessionState.CONNECTED)
            return

        if fr.type == FrameType.B1:
            # 重复B1（同seq）幂等：仅重发AF，避免重复下发A1
            if self._last_b1_seq is not None and fr.seq == self._last_b1_seq:
                if getattr(self, "_duplicate_ack_mode", "duplicate_code") == "echo_last":
                    self._send_frame(build_af(seq=fr.seq, code=self._last_b1_ack_code))
                    self.logger.debug(f"dup B1 seq={fr.seq} echo_last code=0x{int(self._last_b1_ack_code):02X}")
                else:
                    self._send_frame(build_af(seq=fr.seq, code=AckCode.DUPLICATE))
                    self.logger.debug(f"dup B1 seq={fr.seq} code=DUPLICATE(0x{int(AckCode.DUPLICATE):02X})")
                return

            # 乱序检测：非预期下一值 → 按大小关系返回
            if self._last_b1_seq is not None:
                expected = (self._last_b1_seq + 1) & 0xFFFF
                if fr.seq != expected:
                    code = AckCode.SEQ_TOO_SMALL if fr.seq < expected else AckCode.SEQ_TOO_LARGE
                    self._send_frame(build_af(seq=fr.seq, code=code))
                    self._last_b1_ack_code = int(code)
                    self.logger.debug(f"ooB1 seq={fr.seq} expected={expected} code=0x{int(code):02X}")
                    return

            # 正常顺序：更新并执行业务
            self._last_b1_seq = fr.seq
            self.expected_remote_seq = (fr.seq + 1) & 0xFFFF
            self._send_frame(build_af(seq=fr.seq, code=AckCode.OK))
            self._last_b1_ack_code = int(AckCode.OK)
            # 若上层提供 attrs，按配置决定是否承载；否则保持纯 index 模式
            result = self._request_handler()
            colors: Optional[List[int]] = None
            if isinstance(result, tuple) and len(result) == 3:
                indices, attrs, colors = result
            else:
                indices, attrs = result  # type: ignore
            self._send_a1_payload(indices, attrs=attrs, colors=colors)
            return

        # 其他 TYPE 最小实现暂不处理

    def _send_a1_payload(self, indices: List[int], attrs: Optional[List[int]] = None, colors: Optional[List[int]] = None) -> None:
        """
        发送 A1（2B/项位域）：
        - 每项 2 字节：bit15=闪烁；bit14..13=颜色(00红/01绿/10蓝/11预留)；bit12..0=ID(13位)
        - attrs bit0→闪烁；colors 0/1/2 对应 R/G/B；分片时 attrs/colors 与 indices 对齐
        - 空清单：仍发送一帧 A1 并等待 BF（保持统一时序）
        """
        per_item_bytes = 2
        max_val_bytes = max(1, int(self.bytes_per_frame))
        chunk_size = max(1, max_val_bytes // per_item_bytes)
    
        all_ok = True
        # 空清单也发送一帧（用于清屏或维持时序）
        if not indices:
            seq = self.next_seq()
            frame = build_a1(indices=[], seq=seq, attrs=None, colors=None)
            ok = self.send_and_wait_ack(frame, timeout_ms=self.cmd_timeout_ms)
            all_ok = all_ok and ok
            if self.on_a1_result:
                try:
                    self.on_a1_result(all_ok)
                except Exception as e:
                    self.logger.debug(f"on_a1_result callback error: {e}")
            return
    
        i = 0
        n = len(indices)
        while i < n:
            chunk = indices[i : i + chunk_size]
            # 对齐 attrs/colors 片段
            attr_chunk: Optional[List[int]] = None
            color_chunk: Optional[List[int]] = None
            if attrs is not None:
                attr_chunk = attrs[i : i + len(chunk)]
            if colors is not None:
                color_chunk = colors[i : i + len(chunk)]
            seq = self.next_seq()
            frame = build_a1(indices=chunk, seq=seq, attrs=attr_chunk, colors=color_chunk)
            ok = self.send_and_wait_ack(frame, timeout_ms=self.cmd_timeout_ms)
            all_ok = all_ok and ok
            i += len(chunk)
            if i < n and self.inter_frame_gap_ms > 0:
                time.sleep(self.inter_frame_gap_ms / 1000.0)
    
        if self.on_a1_result:
            try:
                self.on_a1_result(all_ok)
            except Exception as e:
                self.logger.debug(f"on_a1_result callback error: {e}")
