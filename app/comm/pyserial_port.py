from __future__ import annotations
import threading
import time
from typing import Callable, Optional

try:
    import serial  # pyserial
except Exception:  # defer ImportError until open() is called
    serial = None  # type: ignore

from app.comm.serial_port import SerialPortBase
from app.logs.logger import get_logger


class PySerialPort(SerialPortBase):
    """
    真实串口适配（pyserial），继承 SerialPortBase:
    - open(port, baud, timeout_ms): 打开串口并启动接收线程
    - close(): 停止接收线程并关闭串口
    - write_bytes(data): 写入字节流，异常记录日志
    接收线程使用 in_waiting 低延迟轮询，将读到的数据直接回调上层（会话层负责协议解析和“设备信息”日志）。
    """

    def __init__(self) -> None:
        super().__init__()
        self._ser: Optional["serial.Serial"] = None  # type: ignore[name-defined]
        self._rx_th: Optional[threading.Thread] = None
        self._rx_stop = threading.Event()
        self._logger = get_logger("pyserial")

    def set_rx_callback(self, cb: Callable[[bytes], None]) -> None:
        super().set_rx_callback(cb)

    def open(self, port: str | None = None, baud: int = 115200, timeout_ms: int = 100) -> None:
        """
        打开串口并启动接收线程。timeout_ms 为读超时（秒=ms/1000）。
        """
        if port is None:
            raise ValueError("PySerialPort.open requires a 'port' string (e.g., 'COM3')")

        if serial is None:
            raise ImportError("pyserial is not installed. Please install 'pyserial>=3.5'")

        try:
            self._ser = serial.Serial(  # type: ignore[attr-defined]
                port=port,
                baudrate=int(baud),
                timeout=float(timeout_ms) / 1000.0,
            )
            self._rx_stop.clear()
            self._rx_th = threading.Thread(target=self._rx_loop, name=f"pyserial-{port}-rx", daemon=True)
            self._rx_th.start()
            self._logger.info(f"Serial opened: port={port} baud={baud} timeout_ms={timeout_ms}")
        except Exception as e:
            self._logger.error(f"Serial open failed: {e}")
            raise

    def close(self) -> None:
        """
        停止接收线程并关闭串口。
        """
        try:
            self._rx_stop.set()
            if self._rx_th and self._rx_th.is_alive():
                # 不阻塞太久，接收线程会在下一个轮询周期退出
                self._rx_th = None
            if self._ser:
                try:
                    if getattr(self._ser, "is_open", False):
                        self._ser.close()
                except Exception:
                    pass
                self._ser = None
            self._logger.info("Serial closed")
        except Exception as e:
            self._logger.error(f"Serial close failed: {e}")

    def write_bytes(self, data: bytes) -> None:
        """
        写入字节流；异常时记录日志但不抛出致命错误。
        """
        try:
            if not isinstance(data, (bytes, bytearray)):
                raise TypeError("write_bytes expects bytes or bytearray")
            ser = self._ser
            if ser is None or not getattr(ser, "is_open", False):
                raise RuntimeError("Serial port is not open")
            ser.write(data)
        except Exception as e:
            self._logger.error(f"Serial write failed: {e}")

    def _rx_loop(self) -> None:
        """
        轮询读取串口数据：
        - 优先读取 in_waiting 的全部字节；
        - 若无数据，短暂 sleep 以降低 CPU 占用；
        - 将读取到的数据原样上送回调（上层负责协议解析与设备信息日志）。
        """
        ser = self._ser
        if ser is None:
            return
        while not self._rx_stop.is_set():
            try:
                waiting = int(getattr(ser, "in_waiting", 0))
                if waiting > 0:
                    data = ser.read(waiting)
                    if data and  self._rx_cb:
                        try:
                             self._rx_cb(data)
                        except Exception as e:
                            self._logger.error(f"RX callback error: {e}")
                else:
                    # 适度休眠，避免忙等
                    time.sleep(0.001)
            except Exception as e:
                # 读异常：记录并继续尝试（串口可能短暂不可用）
                self._logger.error(f"Serial read failed: {e}")
                time.sleep(0.010)
        # 退出前不做额外处理；close() 会负责资源清理