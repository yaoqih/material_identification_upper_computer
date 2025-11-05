from __future__ import annotations
from pathlib import Path
from typing import List, Optional, Tuple

from app.business.grouping import GroupingService, GroupTriplet
from app.business.mapping import MappingService
from app.business.file_ingress import FileIngressService
from app.logs.logger import get_logger
from app.storage.config import ConfigRepo

RequestPayload = Tuple[List[int], Optional[List[int]], Optional[List[int]]]

class Dispatcher:
    """
    派发器（仅支持 triplet 三色编组）：
    - 从 work_dir 聚合完整三色组（顺序由配置 color_order 决定，默认 R→G→B）
    - 请求时返回 indices（2B/项，小端）；attrs 由上层决定是否承载
    """
    def __init__(self, work_dir: str | Path, grouping: GroupingService | None = None, mapping: MappingService | None = None) -> None:
        self.work_dir = Path(work_dir)
        self.grouping = grouping or GroupingService()
        self.mapping = mapping or MappingService()
        self.logger = get_logger("dispatcher")
        cfg = ConfigRepo().load()
        gp = cfg.get("grouping", {})
        # 颜色顺序：优先 dispatcher.color_order；回退 grouping.color_order；再回退 ["R","G","B"]
        disp = cfg.get("dispatcher", {})
        order = disp.get("color_order") or gp.get("color_order") or ["R", "G", "B"]
        self._color_order = list(order)

        self.done_dir = Path(gp.get("done_dir", self.work_dir.parent / "done"))
        self.error_dir = Path(gp.get("error_dir", self.work_dir.parent / "error"))
        self._queue_triplets: List[GroupTriplet] = []
        self._last_dispatched: Optional[GroupTriplet] = None
        self.reload()

    def reload(self) -> None:
        self._queue_triplets = self.grouping.group(self.work_dir)

    def request_next_payload(self) -> RequestPayload:
        if not self._queue_triplets:
            return [], None, None
        g = self._queue_triplets.pop(0)
        self._last_dispatched = g
        indices, attrs, colors = self.mapping.compose_indices_attrs_and_colors_for_group(g)
        return indices, attrs, colors

    def archive_group(self, group: GroupTriplet, success: bool = True) -> None:
        """
        归档最近一次任务（仅 triplet）。
        """
        dst = self.done_dir if success else self.error_dir
        fis = FileIngressService()
        for color, (txt, jpg) in group.files.items():
            try:
                fis._safe_move(txt, dst)
                fis._safe_move(jpg, dst)
                self.logger.info(f"archive {txt} color={color} -> {dst}")
            except Exception as e:
                self.logger.error(f"archive failed {txt} color={color}: {e}")

    def archive_pending(self, success: bool = True) -> None:
        g = self._last_dispatched
        if not g:
            self.logger.debug("archive_pending: no task to archive")
            return
        self.archive_group(g, success=success)
        self._last_dispatched = None