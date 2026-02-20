from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict

from app.storage.config import ConfigRepo

_LEVELS: Dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

def _level_of(name: Any) -> int:
    try:
        key = str(name).upper()
        return _LEVELS.get(key, logging.INFO)
    except Exception:
        return logging.INFO

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    cfg = ConfigRepo().load()
    log_cfg: Dict[str, Any] = cfg.get("logging", {})

    logger.setLevel(_level_of(log_cfg.get("level", "INFO")))

    # 文件路径与目录
    file_path = Path(str(log_cfg.get("file", "logs/app.log")))
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # 格式化
    fmt_str = str(log_cfg.get("format", "%(asctime)s %(levelname)s %(name)s - %(message)s"))
    formatter = logging.Formatter(fmt_str)

    # 轮转配置（按大小）
    rot = log_cfg.get("rotate", {}) or {}
    if bool(rot.get("enabled", True)):
        max_bytes = int(rot.get("max_bytes", 1048576))
        backup_count = int(rot.get("backup_count", 3))
        fh: logging.Handler = RotatingFileHandler(file_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    else:
        fh = logging.FileHandler(file_path, encoding="utf-8")
    fh.setFormatter(formatter)

    # 控制台输出（保持现有行为）
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)

    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.propagate = False
    return logger

def hex_dump(data: bytes, limit: int | None = None) -> str:
    if not isinstance(data, (bytes, bytearray)):
        return ""
    view = data if limit is None else data[: max(0, int(limit))]
    return " ".join(f"{b:02X}" for b in view)

def get_device_info_logger(name: str = "device-info") -> logging.Logger:
    """
    专用“设备信息”日志（非协议字节，以 ASCII 解码）：
    - 文件路径：logging.device_info_file（默认 logs/device-info.log）
    - 独立 FileHandler 与轮转参数复用 logging.rotate.*
    - 日志格式：'%(asctime)s %(message)s'
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    cfg = ConfigRepo().load()
    log_cfg: Dict[str, Any] = cfg.get("logging", {}) or {}
    rot = log_cfg.get("rotate", {}) or {}

    file_path = Path(str(log_cfg.get("device_info_file", "logs/device-info.log")))
    file_path.parent.mkdir(parents=True, exist_ok=True)

    fmt_str = str(log_cfg.get("device_info_format", "%(asctime)s %(message)s"))
    formatter = logging.Formatter(fmt_str)

    if bool(rot.get("enabled", True)):
        max_bytes = int(rot.get("max_bytes", 1048576))
        backup_count = int(rot.get("backup_count", 3))
        fh: logging.Handler = RotatingFileHandler(file_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    else:
        fh = logging.FileHandler(file_path, encoding="utf-8")
    fh.setFormatter(formatter)

    logger.setLevel(logging.INFO)
    logger.addHandler(fh)
    logger.propagate = False
    return logger