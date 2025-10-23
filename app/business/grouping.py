from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re
from app.logs.logger import get_logger
from app.storage.config import ConfigRepo

# removed: legacy hardcoded NAME_TAG_RE and N_TO_COLOR; now loaded from config

@dataclass(frozen=True)
class GroupTriplet:
    """
    一组三幅画：key 为组前缀（优先来自 -N1/-N2/-N3 前缀的 A[-B]）；
    files 映射颜色→(txt_path, jpg_path)
    """
    key: str
    files: Dict[str, Tuple[Path, Path]]

# removed: TaskPair (pair mode)

class GroupingService:
    """
    简化版分组三色（全局按文件名排序，3个一组，颜色按 R→G→B，末组可不满）：
    - 不再依赖 -N1/N2/N3 标签与 A-B 键；仅基于同名 .txt/.jpg 成对的文件名整体排序；
    - 仅输出 GroupTriplet 列表，key 以首项的“主干名”派生（去掉末尾 -N 标签，如果存在），仅用于日志；
    - 该策略与 Dispatcher.reload() 的“全量重建队列”配合时，自然满足“之前未满的最后一组在新一轮补齐”的需求。
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        # 读取配置并本地化可调变量（保持默认行为）
        cfg = ConfigRepo().load() if config is None else config
        g = cfg.get("grouping", {})
        self._name_tag_regex = re.compile(
            g.get("name_tag_regex", r"^(?P<a>[^-]+)(?:-(?P<b>[^-]+))?-(?P<tag>N[0-9]+)$"),
            re.IGNORECASE,
        )
        # 目前算法按顺序配色，不用 tag→color；为未来回归“按标签配色”保留配置影子
        self._n_to_color = dict(g.get("n_to_color", {"N1": "R", "N2": "G", "N3": "B"}))
        self._color_order = list(g.get("color_order", ["R", "G", "B"]))

    def group(self, work_dir: str | Path) -> List[GroupTriplet]:
        wk = Path(work_dir)
        # 收集成对 .txt+.jpg 的 stem
        txts = {p.stem: p for p in wk.glob("*.txt") if p.is_file()}
        jpgs = {p.stem: p for p in list(wk.glob("*.jpg")) + list(wk.glob("*.jpeg")) if p.is_file()}
        locks = {p.stem for p in wk.glob("*.pairlock") if p.is_file()}
        stems = sorted((set(txts.keys()) & set(jpgs.keys())) - locks)

        logger = get_logger("grouping")

        def derive_key_from_stem(stem: str) -> str:
            m = self._name_tag_regex.match(stem)
            if m:
                a = m.group("a")
                b = m.group("b")
                return f"{a}-{b}" if b else a
            # 无标签：去掉最后一段（若存在），否则返回原 stem
            parts = stem.split("-")
            return "-".join(parts[:-1]) if len(parts) > 1 else stem

        groups: List[GroupTriplet] = []
        i = 0
        while i < len(stems):
            chunk = stems[i:i+3]  # 最后一组可能不足 3 个
            i += 3
            if not chunk:
                continue

            # 颜色顺序来自配置 grouping.color_order（默认 R→G→B），按 chunk 顺位赋色
            colors: List[str] = self._color_order
            ordered: Dict[str, Tuple[Path, Path]] = {}
            for idx, stem in enumerate(chunk):
                c = colors[idx]
                try:
                    ordered[c] = (txts[stem], jpgs[stem])
                    try:
                        logger.info(f"assign via=simple stem={stem} color={c}")
                    except Exception:
                        pass
                except KeyError:
                    # 成对检查已做，理论不应触发；稳健起见跳过
                    continue

            # key 仅做日志展示
            key = derive_key_from_stem(chunk[0])
            if ordered:
                groups.append(GroupTriplet(key=key, files=ordered))

        return groups

# removed: group_pairs (pair mode)