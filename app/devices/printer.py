from __future__ import annotations
from typing import List, Optional
from app.logs.logger import get_logger
from app.storage.config import ConfigRepo

class Printer:
    """
    占位打印设备接口：
    - 通过配置 printing.enabled 控制启用（默认关闭）
    - print_labels(items, columns=2)：启用时将内容写入日志/控制台作为占位
    不依赖任何外部驱动，便于后续扩展真实打印机适配。
    """
    def __init__(self, enabled: bool = False, columns: int = 2, name: str = "printer") -> None:
        self.enabled = bool(enabled)
        self.columns = int(columns) if columns and columns > 0 else 2
        self.logger = get_logger(name)
        # 读取打印列分隔符（默认回退 " | "）
        try:
            cfg = ConfigRepo().load()
            pr = cfg.get("printing", {})
            sep = pr.get("column_separator", " | ")
        except Exception:
            sep = " | "
        self._sep = str(sep)

    def print_labels(self, items: List[str], columns: Optional[int] = None) -> None:
        cols = int(columns or self.columns)
        if cols <= 0:
            cols = 1
        if not self.enabled:
            # 默认关闭，保持现有流程不受影响
            self.logger.debug(f"printing disabled, skip labels count={len(items)}")
            return
        # 占位打印：按列写入日志
        self.logger.info(f"PRINT labels cols={cols}, count={len(items)}")
        row: List[str] = []
        for i, item in enumerate(items, start=1):
            row.append(str(item))
            if i % cols == 0:
                self.logger.info(self._sep.join(row))
                row = []
        if row:
            self.logger.info(self._sep.join(row))