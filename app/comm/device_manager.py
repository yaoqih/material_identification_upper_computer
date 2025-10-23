from __future__ import annotations
from typing import Dict, Callable, Optional, Tuple, List

from app.comm.session import SerialSession
from app.comm.serial_port import SerialPortBase

RequestHandler = Callable[[], Tuple[List[int], Optional[List[int]]]]

class DeviceManager:
    def __init__(self) -> None:
        self.sessions: Dict[str, SerialSession] = {}

    def attach(self, name: str, port: SerialPortBase, request_handler: Optional[RequestHandler] = None) -> SerialSession:
        sess = SerialSession(port, request_handler, name=f"session:{name}")
        self.sessions[name] = sess
        return sess

    def broadcast_heartbeat(self) -> Dict[str, bool]:
        results: Dict[str, bool] = {}
        for name, sess in self.sessions.items():
            results[name] = sess.send_heartbeat()
        return results