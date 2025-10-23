from __future__ import annotations
from typing import Callable, Optional, List

class SerialPortBase:
    """
    轻量串口抽象：设置回调并写入字节。
    真实实现可在后续替换为 QtSerialPort 适配层。
    """
    def __init__(self) -> None:
        self._rx_cb: Optional[Callable[[bytes], None]] = None

    def set_rx_callback(self, cb: Callable[[bytes], None]) -> None:
        self._rx_cb = cb

    def open(self, port: str | None = None, baud: int = 115200) -> None:
        # 占位：Fake 实现不需要
        pass

    def close(self) -> None:
        # 占位：Fake 实现不需要
        pass

    def write_bytes(self, data: bytes) -> None:
        raise NotImplementedError("write_bytes must be implemented")

class FakeSerialPort(SerialPortBase):
    """
    内存中双端口，便于测试。使用 connect_peer 连接两端。
    write_bytes 会将数据直接注入 peer._deliver，从而触发对端回调。
    """
    def __init__(self) -> None:
        super().__init__()
        self._peer: Optional["FakeSerialPort"] = None
        self.tx_log: List[bytes] = []
        self.rx_log: List[bytes] = []
        self.is_open: bool = True

    def connect_peer(self, peer: "FakeSerialPort") -> None:
        self._peer = peer
        peer._peer = self

    def write_bytes(self, data: bytes) -> None:
        self.tx_log.append(data)
        if self._peer and self._peer.is_open:
            self._peer._deliver(data)

    def _deliver(self, data: bytes) -> None:
        self.rx_log.append(data)
        cb = self._rx_cb
        if cb:
            cb(data)

    def open(self, port: str | None = None, baud: int = 115200) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False