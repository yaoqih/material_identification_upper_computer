from __future__ import annotations
import time
import threading
from pathlib import Path
from typing import Optional

from app.storage.config import ConfigRepo
from app.logs.logger import get_logger
from app.comm.pyserial_port import PySerialPort
from app.comm.session import SerialSession
from app.business.dispatcher import Dispatcher
from app.business.file_ingress import FileIngressService


def main() -> int:
    logger = get_logger("app")
    cfg = ConfigRepo().load()
    logger.info("Material Upper Computer started [production-only]")

    # Ensure required directories exist
    grouping_cfg = (cfg.get("grouping", {}) or {})
    watch_dir = Path(grouping_cfg.get("watch_dir", "data/watch"))
    work_dir = Path(grouping_cfg.get("work_dir", "data/work"))
    error_dir = Path(grouping_cfg.get("error_dir", "data/error"))
    done_dir = Path(grouping_cfg.get("done_dir", "data/done"))
    for d in (watch_dir, work_dir, error_dir, done_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Dispatcher and ingress pipeline
    dispatcher = Dispatcher(work_dir=work_dir)
    ingress = FileIngressService()

    stop_evt = threading.Event()

    def _ingress_runner() -> None:
        while not stop_evt.is_set():
            try:
                ingress.ingest_batch()
                dispatcher.reload()
            except Exception as ex:
                logger.error(f"ingress/reload failed: {ex}")
            time.sleep(1.0)

    threading.Thread(target=_ingress_runner, name="ingress-runner", daemon=True).start()

    # Open real serial port with retries (no simulation/fallback)
    def _open_port_with_retry() -> Optional[PySerialPort]:
        backoff = 5.0  # seconds, will increase up to 30s
        while not stop_evt.is_set():
            try:
                c = ConfigRepo().load()
                sc = (c.get("serial", {}) or {})
                ports = sc.get("ports") or []
                baud = int(sc.get("baud", 115200))
                if not ports:
                    logger.warning("serial.ports is empty; configure a port list. Retry in 10s.")
                    time.sleep(10.0)
                    continue
                for name in ports:
                    try:
                        p = PySerialPort()
                        p.open(str(name), baud=baud)
                        logger.info(f"Serial opened on {name} baud={baud}")
                        return p
                    except Exception as e:
                        logger.error(f"Open serial {name} failed: {e}")
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)
            except Exception as e:
                logger.error(f"Unexpected error when opening serial: {e}")
                time.sleep(10.0)
        return None
    dispatcher.request_next_payload()
    port = _open_port_with_retry()
    if port is None:
        logger.info("Shutdown requested before serial opened.")
        return 0

    # Session: heartbeat behavior is driven by config (comm.enable_heartbeat, etc.)
    sess = SerialSession(port, request_handler=dispatcher.request_next_payload, name="session")

    def _on_result(ok: bool) -> None:
        try:
            dispatcher.archive_pending(success=ok)
        except Exception:
            pass

    sess.on_a1_result = _on_result

    logger.info("Production flow started. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Stopping production...")
    finally:
        stop_evt.set()
        try:
            sess.stop_heartbeat()
        except Exception:
            pass
        try:
            port.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())