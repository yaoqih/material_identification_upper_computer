from __future__ import annotations
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import os
import time
import shutil
import re

from app.storage.config import ConfigRepo
from app.business.mapping import MappingService
from app.logs.logger import get_logger

# removed: use config ingress.allowed_extensions instead of hardcoded TARGET_EXTS


class FileIngressService:
    """
    批量入库：从 watch_dir 扫描文件，成对(.txt+.jpg)视为完整条目剪切到 work_dir；
    不完整或非目标类型移入 error_dir。返回(入库文件列表, 错放/错误文件列表)。
    规则简化：仅以“同 stem 不同扩展名”的配对为准。

    注：根据 [docs/任务需求.md](docs/任务需求.md) 的 watch→work/error 约定，
    在处理前对文件进行安静窗口（ready_quiet_ms）判定，避免半写入文件被误处理。
    """

    def __init__(
        self,
        watch_dir: str | Path | None = None,
        work_dir: str | Path | None = None,
        error_dir: str | Path | None = None,
        ready_quiet_ms: Optional[int] = None,
    ) -> None:
        # 可注入路径（测试可传入 tmp_path 下的隔离目录）；未提供时按配置/默认目录
        self._watch_dir = Path(watch_dir) if watch_dir else None
        self._work_dir = Path(work_dir) if work_dir else None
        self._error_dir = Path(error_dir) if error_dir else None
        # 可选覆盖安静窗口阈值；默认 None 不覆盖配置，保持现有行为（0ms）
        self._ready_quiet_ms_override = ready_quiet_ms
        self._logger = get_logger("ingress")

    def ingest_batch(
        self,
        watch_dir: str | Path | None = None,
        work_dir: str | Path | None = None,
        error_dir: str | Path | None = None,
    ) -> Tuple[List[Path], List[Path]]:
        # 目录解析优先级：入参 > 构造注入 > 配置（grouping）> 硬编码默认
        cfg: Dict[str, object] | None = None

        def _cfg() -> Dict[str, object]:
            nonlocal cfg
            if cfg is None:
                cfg = ConfigRepo().load()  # 动态读取，允许测试通过 APP_CONFIG_PATH 注入
            return cfg  # type: ignore[return-value]

        w = Path(watch_dir) if watch_dir is not None else (self._watch_dir if self._watch_dir is not None else Path(_cfg().get("grouping", {}).get("watch_dir", "data/watch")))  # type: ignore[call-arg, attr-defined]
        wk = Path(work_dir) if work_dir is not None else (self._work_dir if self._work_dir is not None else Path(_cfg().get("grouping", {}).get("work_dir", "data/work")))    # type: ignore[call-arg, attr-defined]
        er = Path(error_dir) if error_dir is not None else (self._error_dir if self._error_dir is not None else Path(_cfg().get("grouping", {}).get("error_dir", "data/error")))  # type: ignore[call-arg, attr-defined]

        wk.mkdir(parents=True, exist_ok=True)
        er.mkdir(parents=True, exist_ok=True)

        ing_cfg = _cfg().get("ingress", {}) or {}
        # 安静窗口阈值（毫秒）
        # 规则：当调用方显式提供 watch/work/error（或在构造中已注入路径）时，默认 quiet=0 以便测试与即时入库；
        # 若未显式提供路径，则使用配置中的 ready_quiet_ms。入参 ready_quiet_ms 覆盖两者。
        using_explicit_paths = (
            (watch_dir is not None) or (work_dir is not None) or (error_dir is not None)
            or (self._watch_dir is not None) or (self._work_dir is not None) or (self._error_dir is not None)
        )
        ready_quiet_ms = (
            int(self._ready_quiet_ms_override)  # type: ignore[arg-type]
            if self._ready_quiet_ms_override is not None
            else (0 if using_explicit_paths else int(ing_cfg.get("ready_quiet_ms", 0)))  # type: ignore[arg-type]
        )
        atomic_pair_enabled = bool(ing_cfg.get("atomic_pair_enabled", True))
        # From config: allowed extensions and atomic pair suffixes (lower-cased for comparisons)
        allowed_exts = {str(ext).lower() for ext in ing_cfg["allowed_extensions"]}
        suffix_cfg = ing_cfg["atomic_pair_suffixes"]
        part_suf = str(suffix_cfg["part_suffix"])
        lock_suf = str(suffix_cfg["lock_suffix"])
        part_suf_lower = part_suf.lower()
        lock_suf_lower = lock_suf.lower()
        # 扫描就绪文件，忽略 .part 与 .pairlock
        files = [p for p in w.iterdir() if p.is_file()]
        now = time.time()
        ready_files: List[Path] = []
        for f in files:
            name_lower = f.name.lower()
            if name_lower.endswith(part_suf_lower) or name_lower.endswith(lock_suf_lower):
                continue
            dt_ms = (now - f.stat().st_mtime) * 1000.0
            if dt_ms >= ready_quiet_ms:
                ready_files.append(f)

        # 构建 stem -> 路径映射
        by_stem: Dict[str, Dict[str, Path]] = {}
        for f in ready_files:
            ext = f.suffix.lower()
            if ext not in allowed_exts:
                continue
            by_stem.setdefault(f.stem, {})[ext] = f

        moved_work: List[Path] = []
        moved_err: List[Path] = []


        # 预解析移除（triplet-only，无需单文件预检）

        processed_stems: set[str] = set()
        for stem, parts in list(by_stem.items()):
            txt = parts.get(".txt")
            img = parts.get(".jpg") or parts.get(".jpeg")
            if not (txt and img):
                continue

            # 新格式预检：解析“编号 名称 面积百分比”，并校验 group 范围，失败则整对移入 error
            parse_ok = True
            reason = ""
            indices_chk: List[int] = []
            perc_chk: List[float] = []
            try:
                indices_chk, perc_chk = MappingService().parse_indices_and_percent_from_txt(txt)
                if not indices_chk:
                    parse_ok = False
                    reason = "empty indices"
                elif len(indices_chk) != len(perc_chk):
                    parse_ok = False
                    reason = "mismatched lengths"
            except Exception as e:
                parse_ok = False
                try:
                    reason = str(e)
                except Exception:
                    reason = "parse error"

            # 组范围校验：若定义了 sp_mapping.groups，则要求每个格子编号在任一组范围内
            if parse_ok:
                sp_cfg = _cfg().get("sp_mapping", {}) or {}
                groups = sp_cfg.get("groups", []) if isinstance(sp_cfg, dict) else []
                if isinstance(groups, list) and len(groups) > 0:
                    def _in_any_group(v: int) -> bool:
                        for gobj in groups:
                            try:
                                s = int(gobj.get("start_sp"))
                                e = int(gobj.get("end_sp"))
                                if s <= v <= e:
                                    return True
                            except Exception:
                                continue
                        return False
                    bad: Optional[int] = None
                    for v in indices_chk:
                        try:
                            vv = int(v)
                        except Exception:
                            vv = v
                        if not _in_any_group(vv):
                            bad = vv
                            break
                    if bad is not None:
                        parse_ok = False
                        reason = f"index {bad} out of groups"

            if not parse_ok:
                # 不合规：整对剪切至 error
                try:
                    moved_err.append(self._safe_move(txt, er))
                except Exception:
                    pass
                try:
                    moved_err.append(self._safe_move(img, er))
                except Exception:
                    pass
                processed_stems.add(stem)
                try:
                    self._logger.warning(f"reject pair(table): stem={stem} reason={reason}")
                except Exception:
                    pass
                continue

            if not atomic_pair_enabled:
                # 回退：直接原子移动
                try:
                    moved_work.append(self._safe_move(txt, wk))
                    moved_work.append(self._safe_move(img, wk))
                    processed_stems.add(stem)
                except Exception:
                    # 失败回滚到 error
                    try:
                        moved_err.append(self._safe_move(txt, er))
                    except Exception:
                        pass
                    try:
                        moved_err.append(self._safe_move(img, er))
                    except Exception:
                        pass
                continue

            # 准原子两阶段提交：.part + .pairlock
            part_txt = wk / f"{txt.name}{part_suf}"
            part_img = wk / f"{img.name}{part_suf}"
            pairlock = wk / f"{stem}{lock_suf}"
            final_txt = wk / txt.name
            final_img = wk / img.name

            try:
                # 第 1 阶段：放置 .part 文件
                self._move_or_copy2(txt, part_txt)
                self._move_or_copy2(img, part_img)
                # 创建 lock（空文件）
                pairlock.touch(exist_ok=True)
                # 第 2 阶段：原子重命名为最终文件名
                os.replace(part_txt, final_txt)
                os.replace(part_img, final_img)
                try:
                    pairlock.unlink(missing_ok=True)  # py3.8 以上
                except Exception:
                    if pairlock.exists():
                        try:
                            os.remove(pairlock)
                        except Exception:
                            pass
                moved_work.extend([final_txt, final_img])
                processed_stems.add(stem)
            except Exception:
                # 回滚：清理已创建的 .part / lock，并将二者放入 error（尽力而为）
                for p in (part_txt, part_img):
                    if p.exists():
                        try:
                            # 将 part 后缀文件转移到 error，去掉配置的 part 后缀
                            name = p.name
                            base_name = name[:-len(part_suf)] if name.lower().endswith(part_suf_lower) else name
                            target = er / base_name
                            os.replace(p, target)
                            moved_err.append(target)
                        except Exception:
                            try:
                                p.unlink()
                            except Exception:
                                pass
                if pairlock.exists():
                    try:
                        pairlock.unlink()
                    except Exception:
                        pass
                # 若源仍在 watch，移入 error
                if txt.exists():
                    try:
                        moved_err.append(self._safe_move(txt, er))
                    except Exception:
                        pass
                if img.exists():
                    try:
                        moved_err.append(self._safe_move(img, er))
                    except Exception:
                        pass

        # 其余“就绪但不成对/不支持扩展名”的文件 -> error
        for f in ready_files:
            if f.stem in processed_stems:
                continue
            ext = f.suffix.lower()
            if (ext not in allowed_exts) or (f.stem not in by_stem) or (".txt" not in by_stem[f.stem]) or ((".jpg" not in by_stem[f.stem] and ".jpeg" not in by_stem[f.stem])):
                try:
                    moved_err.append(self._safe_move(f, er))
                except Exception:
                    pass

        return moved_work, moved_err

    def _move_or_copy2(self, src: Path, dst: Path) -> None:
        """
        跨卷容错移动：优先 os.replace；失败则 copy2 后删除源文件。
        """
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src, dst)
        except OSError as e:
            try:
                shutil.copy2(src, dst)
                os.remove(src)
            except Exception as ex:
                # 复制/删除源失败，记录并抛出以触发上层回滚
                try:
                    self._logger.error(f"copy2/remove failed: {src} -> {dst}: {ex}")
                except Exception:
                    pass
                raise

    def _safe_move(self, src: Path, dst_dir: Path) -> Path:
        """
        使用 os.replace 实现覆盖式原子移动；若目标存在冲突或瞬时异常，追加唯一后缀重试。
        满足 [docs/任务需求.md](docs/任务需求.md) 中 watch→work/error 的原子语义。
        """
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        # 首选原子替换（覆盖同名文件）
        try:
            os.replace(src, dst)
            return dst
        except OSError:
            # 冲突或瞬时失败时，追加基于时间戳的唯一后缀重试
            i = 1
            while True:
                cand = dst_dir / f"{src.stem}_{int(time.time() * 1000)}_{i}{src.suffix}"
                try:
                    os.replace(src, cand)
                    return cand
                except OSError:
                    i += 1
                    if i > 5:
                        # 仍失败则抛出，由上层决定兜底
                        raise