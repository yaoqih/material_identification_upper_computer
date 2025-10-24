"""
协议帧格式：HEADER(4)+TYPE(1)+LEN(2)+SEQ(2)+VAL(N)+CHECK(1)
- HEADER 固定：F2 F8 F1 F2
- LEN 表示 (SEQ + VAL + CHECK) 的长度（小端）
- CHECK 为 TYPE、LEN、SEQ、VAL 字节求和的低8位（不含HEADER与自身）
- 字节序：小端
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional, Callable

HEADER = b"\xF2\xF8\xF1\xF2"
MIN_FRAME_LEN = 10  # 4(HDR)+1(TYPE)+2(LEN)+2(SEQ)+1(CHECK)

class FrameType(IntEnum):
    A0 = 0xA0  # 上位机空包
    A1 = 0xA1  # 上位机指令包
    AF = 0xAF  # 上位机应答
    B0 = 0xB0  # 下位机空包
    B1 = 0xB1  # 下位机请求
    BF = 0xBF  # 下位机应答

class AckCode(IntEnum):
    """
    ACK 结果码枚举
    依据文档 [docs/通信约定.md](docs/通信约定.md:47)：
    00=OK, 01=UNKNOWN_TYPE, 02=LEN_ERROR, 03=SEQ_TOO_SMALL, 04=SEQ_TOO_LARGE, 05=VAL_ERROR, 06=CHECKSUM_ERROR。
    兼容说明：
    - 为保持现有测试契约，DUPLICATE 仍使用 0x02（重复 B1 内部占位，不对外发送于协议层）。
    - 同时补齐 LEN_ERROR(0x02) 与 VAL_ERROR(0x05) 枚举，按需由协议层/会话层回 AF。
    """
    OK = 0x00
    UNKNOWN_TYPE = 0x01
    DUPLICATE = 0x02            # 项目兼容：重复 B1 使用 0x02（tests 依赖）
    LEN_ERROR = 0x02            # 文档值，作为别名共用 0x02
    SEQ_TOO_SMALL = 0x03
    SEQ_TOO_LARGE = 0x04
    VAL_ERROR = 0x05
    CHECKSUM_ERROR = 0x06

@dataclass
class ProtocolFrame:
    type: FrameType
    seq: int
    val: bytes

def _calc_check(ftype: int, length: int, seq: int, val: bytes) -> int:
    data = bytes([ftype]) + length.to_bytes(2, "little") + seq.to_bytes(2, "little") + (val or b"")
    return sum(data) & 0xFF

def encode_frame(frame: ProtocolFrame) -> bytes:
    val = frame.val or b""
    length = 2 + len(val) + 1  # SEQ(2) + VAL(N) + CHECK(1)
    chk = _calc_check(int(frame.type), length, frame.seq, val)
    return (
        HEADER
        + bytes([int(frame.type)])
        + length.to_bytes(2, "little")
        + frame.seq.to_bytes(2, "little")
        + val
        + bytes([chk])
    )

def decode_stream(
    buf: bytearray,
    on_error: Optional[Callable[[AckCode, int], None]] = None,
    on_garbage: Optional[Callable[[bytes], None]] = None,
) -> List[ProtocolFrame]:
    """就地解析：从流缓冲区提取尽可能多的完整帧，余量保留在 buf 中。
    错误处理（当提供 on_error 时）参见文档。
    on_garbage：遇到非协议前缀字节（噪声/设备信息）时，将其切片回调给上层。
    A1 的 VAL 语义仅允许 2B/项位域：
      - 位域：bit15=闪烁；bit14..13=颜色：00红/01绿/10蓝/11预留；bit12..0=ID(13位)
    其他长度触发 VAL_ERROR。
    """
    frames: List[ProtocolFrame] = []
    while True:
        idx = buf.find(HEADER)
        if idx == -1:
            # 缓冲中无 HEADER，认为全部为“杂散字节”
            if on_garbage and len(buf) > 0:
                try:
                    on_garbage(bytes(buf))
                except Exception:
                    pass
            buf.clear()
            break
        if idx > 0:
            # 丢弃 HEADER 之前的杂散字节
            if on_garbage:
                try:
                    on_garbage(bytes(buf[:idx]))
                except Exception:
                    pass
            del buf[:idx]
        if len(buf) < 7:  # 4(HDR)+1(TYPE)+2(LEN)
            break
        ftype = buf[4]
        length = int.from_bytes(buf[5:7], "little")
        # 语义长度校验：最小为 3（SEQ2 + CHECK1）
        if length < 3:
            seq = int.from_bytes(buf[7:9], "little") if len(buf) >= 9 else 0
            if on_error is not None:
                try:
                    on_error(AckCode.LEN_ERROR, seq)
                except Exception:
                    pass
            total_bad = 4 + 1 + 2 + length
            if len(buf) >= total_bad:
                del buf[:total_bad]
            else:
                # 若长度不足以跳过，丢弃 header 以避免死循环
                del buf[:4]
            continue

        total = 4 + 1 + 2 + length
        if len(buf) < total:
            break
        seq = int.from_bytes(buf[7:9], "little")
        val_len = max(0, length - 3)  # 减去 SEQ(2) 与 CHECK(1)
        vstart = 9
        vend = vstart + val_len
        val = bytes(buf[vstart:vend])
        chk = buf[vend]
        calc = _calc_check(ftype, length, seq, val)
        if chk == calc:
            try:
                ft = FrameType(ftype)
                # VAL 语义长度校验
                if ft in (FrameType.A0, FrameType.B0, FrameType.B1):
                    if val_len != 0:
                        if on_error is not None:
                            try:
                                on_error(AckCode.VAL_ERROR, seq)
                            except Exception:
                                pass
                    else:
                        frames.append(ProtocolFrame(ft, seq, val))
                elif ft in (FrameType.AF, FrameType.BF):
                    if val_len != 1:
                        if on_error is not None:
                            try:
                                on_error(AckCode.VAL_ERROR, seq)
                            except Exception:
                                pass
                    else:
                        frames.append(ProtocolFrame(ft, seq, val))
                elif ft == FrameType.A1:
                    # 仅允许 2 字节/项位域
                    if (val_len % 2) != 0:
                        if on_error is not None:
                            try:
                                on_error(AckCode.VAL_ERROR, seq)
                            except Exception:
                                pass
                    else:
                        frames.append(ProtocolFrame(ft, seq, val))
                else:
                    frames.append(ProtocolFrame(ft, seq, val))
            except ValueError:
                # 未知 TYPE：可回调错误
                if on_error is not None:
                    try:
                        on_error(AckCode.UNKNOWN_TYPE, seq)
                    except Exception:
                        pass
        else:
            # CHECK 校验失败：可回调错误
            if on_error is not None:
                try:
                    on_error(AckCode.CHECKSUM_ERROR, seq)
                except Exception:
                    pass
        # 无论校验是否通过，都跳过该段，继续向后解析
        del buf[:total]
    return frames

def build_a0(seq: int = 0xFFFF) -> ProtocolFrame:
    return ProtocolFrame(FrameType.A0, seq, b"")

def build_a1(
    indices: List[int],
    seq: int = 0,
    attrs: Optional[List[int]] = None,
    colors: Optional[List[int]] = None,
) -> ProtocolFrame:
    """
    构造 A1 指令帧（2 字节/项位域）：
    - 位域：bit15=闪烁；bit14..13=颜色(00红/01绿/10蓝/11预留)；bit12..0=ID(13位)
    - attrs 提供时，bit0 映射到位域的 bit15；colors 提供时，按 0=红、1=绿、2=蓝、3=预留；未提供则默认 0(红)
    """
    m = len(indices)
    items: List[bytes] = []
    for i in range(m):
        idx13 = int(indices[i]) & 0x1FFF  # 仅保留低13位
        blink = 0
        if attrs is not None and i < len(attrs):
            if (int(attrs[i]) & 0x01) != 0:
                blink = 1
        color2 = 0
        if colors is not None and i < len(colors):
            color2 = int(colors[i]) & 0x03  # 0=红 1=绿 2=蓝 3=预留
        packed = (blink << 15) | (color2 << 13) | idx13
        items.append(packed.to_bytes(2, "little", signed=False))
    val = b"".join(items)
    return ProtocolFrame(FrameType.A1, seq, val)

def build_af(seq: int, code: int | AckCode = 0) -> ProtocolFrame:
    """构造 AF 应答帧。
    VAL 字段为 1 字节结果码（参见 [docs/通信约定.md](docs/通信约定.md:47)）：
    00：处理成功；01：未知TYPE；03：SEQ过小；04：SEQ过大；06：CHECK错误。
    仅填充 VAL（不要误放到 CHECK 字段）。
    """
    return ProtocolFrame(FrameType.AF, seq, bytes([int(code) & 0xFF]))