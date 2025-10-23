from __future__ import annotations
from pathlib import Path
from typing import List, Optional, Tuple
import re

from app.business.grouping import GroupTriplet
from app.storage.config import ConfigRepo
from app.logs.logger import get_logger

class MappingService:
    """
    映射引擎：
    - parse_indices_and_percent_from_txt: 从 .txt 解析 SP 与百分比
    - compose_indices_and_attrs_for_group: 直接按 group.files(R/G/B) 合成 indices，并按阈值生成 attrs(bit0=blink)
    """

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or ConfigRepo().load()
        # 缓存解析与蛇形/LED 映射配置，及颜色顺序回退链
        cfg_parsing = (self._config.get("parsing", {}) or {})
        self._row_pattern = cfg_parsing.get("row_pattern")
        self._alt_row_pattern = cfg_parsing.get("alt_row_pattern")
        self._header_keywords = list(cfg_parsing.get("header_keywords", []))

        cfg_spm = (self._config.get("sp_mapping", {}) or {})
        try:
            self._leds_per_slot = int(cfg_spm.get("leds_per_slot", 3))
        except Exception:
            self._leds_per_slot = 3
        self._start_corner = str(cfg_spm.get("start_corner", "TL")).upper()
        self._row_dir_even = str(cfg_spm.get("row_direction_even", "LR")).upper()
        self._row_dir_odd = str(cfg_spm.get("row_direction_odd", "RL")).upper()

        order = (self._config.get("dispatcher", {}) or {}).get("color_order")
        if not order:
            order = (self._config.get("grouping", {}) or {}).get("color_order")
        if not order:
            order = ["R", "G", "B"]
        self._color_order = [str(c).upper() for c in order]
    
    def parse_indices_and_percent_from_txt(self, txt_path: str | Path) -> Tuple[List[int], List[float]]:
        """
        解析“编号 名称 面积百分比”表格：
        - 名称列格式：<prefix><digits>，真正格子编号为末尾数字部分；
        - 百分比列：形如 '19.97%'，输出 float 百分比值（单位：百分比）；
        - 跳过首行表头；遇到非法行抛出 ValueError 并包含行号与原因；
        - 拒绝重复格子编号、负值或>100 的百分比。
        """
        p = Path(txt_path)
        if not p.exists():
            return [], []
        indices: List[int] = []
        percents: List[float] = []
        seen: set[int] = set()
        # 行解析正则按配置驱动，大小写不敏感；允许缺省为 None
        row_pat = self._row_pattern
        alt_pat = self._alt_row_pattern
        row_re = re.compile(row_pat, re.IGNORECASE) if row_pat else None
        alt_re = re.compile(alt_pat, re.IGNORECASE) if alt_pat else None
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for lineno, raw in enumerate(f, start=1):
                line = raw.strip()
                if not line:
                    continue
                m = row_re.match(line) if row_re else None
                if not m:
                    # 首行表头关键词跳过（大小写不敏感）
                    if lineno == 1 and any(kw.lower() in line.lower() for kw in self._header_keywords):
                        continue
                    if alt_re:
                        m = alt_re.match(line)
                    if not m:
                        raise ValueError(f"Invalid row at {txt_path}:{lineno}: '{line}'")
                prefix, digits, pct_s = m.groups()
                if prefix not in self._config['parsing']['allowed_code_prefix']:
                    continue
                try:
                    idx = int(digits)
                except Exception:
                    raise ValueError(f"Bad name code at {txt_path}:{lineno}: '{line}'")
                if idx in seen:
                    raise ValueError(f"Duplicate index {idx} at {txt_path}:{lineno}")
                try:
                    pct = float(pct_s)
                except Exception:
                    raise ValueError(f"Bad percent at {txt_path}:{lineno}: '{line}'")
                if pct < 0 or pct > 100:
                    raise ValueError(f"Out-of-range percent {pct} at {txt_path}:{lineno}")
                indices.append(idx)
                percents.append(pct)
                seen.add(idx)
        return indices, percents
    

    def find_sp_group(self, sp: int) -> Optional[dict]:
        """
        在配置 sp_mapping.groups 中查找包含该 SP 的组对象。
        """
        spm = (self._config.get("sp_mapping", {}) or {})
        groups = spm.get("groups", [])
        if not isinstance(groups, list):
            return None
        for g in groups:
            try:
                s = int(g.get("start_sp"))
                e = int(g.get("end_sp"))
                if s <= sp <= e:
                    return g
            except Exception:
                continue
        return None

    def remap_sp_to_block(self, sp: int, group: dict) -> int:
        """
        SP → new_sp（块偏移）：new_sp = (group_id-1)*block_size + (sp - start_sp + 1)
        """
        spm = (self._config.get("sp_mapping", {}) or {})
        block_size = int(spm.get("block_size", 100))
        gid = int(group.get("id"))
        start_sp = int(group.get("start_sp"))
        return (gid - 1) * block_size + (sp - start_sp + 1)

    def _serpentine_pos_in_block(self, pos: int, cols_per_row: int) -> Tuple[int, bool]:
        """
        计算组内蛇形位置（1 基），行长=cols_per_row。
        方向由配置 sp_mapping.row_direction_even/row_direction_odd 控制；
        起始角 sp_mapping.start_corner 影响行内左右方向（TR/BR 视为水平翻转）。
        返回 (组内蛇形位置, 是否反向RL)。
        """
        cols = max(1, int(cols_per_row))
        r = (pos - 1) // cols
        c = (pos - 1) % cols

        dir_str = self._row_dir_even if (r % 2 == 0) else self._row_dir_odd
        # 基于起始角水平翻转（TR/BR）
        if self._start_corner in ("TR", "BR"):
            dir_str = "RL" if dir_str == "LR" else "LR"

        if dir_str == "LR":
            pos_val = r * cols + c + 1
            reverse = False
        else:
            pos_val = r * cols + (cols - 1 - c) + 1
            reverse = True
        return pos_val, reverse

    def compute_led_ids_for_sp(self, sp: int, group: dict) -> Tuple[int, int, int]:
        """
        由 SP 与组定义计算全局 LED1/2/3：
        - 先求组内位置 pos = sp - start_sp + 1
        - 组内蛇形 serp_in_block = serp(pos, cols_per_row)
        - 全局蛇形 serp_global = (group_id-1)*block_size + serp_in_block
        - LED 基址 base = (serp_global-1)*3 + 1
        """
        spm = (self._config.get("sp_mapping", {}) or {})
        block_size = int(spm.get("block_size", 100))
        gid = int(group.get("id"))
        start_sp = int(group.get("start_sp"))
        cols = int(group.get("cols_per_row"))
        pos_in_group = sp - start_sp + 1
        serp_in_block, reverse = self._serpentine_pos_in_block(pos_in_group, cols)
        serp_global = (gid - 1) * block_size + serp_in_block
        base = (serp_global - 1) * self._leds_per_slot + 1
        if reverse:
            return base, base + 1, base + 2
        else:
            return base+2, base + 1, base

    # removed: compose_indices_with_msb_for_file（旧 MSB/percent 逻辑已移除）
    def compose_indices_and_attrs_for_group(self, group: GroupTriplet, color_order: List[str] | None = None) -> Tuple[List[int], Optional[List[int]]]:
        """
        新 SP 映射（含 percent/attrs 闪烁，按订单逐灯判定）：
        - 直接按 group.files 的颜色键(R/G/B)遍历；
        - 使用 parse_indices_and_percent_from_txt() 解析每色的 SP 与百分比；
        - 对每个 SP 计算 LED1/2/3，按颜色选择通道合并为 indices；
        - 对每个订单独立判定：若该订单的 percent>=阈值，则仅该订单对应的灯闪（bit0=1），否则不闪（0）；
        - 返回 (indices, attrs 或 None)。
        """
        log = get_logger("mapping")
    
        order = [c.upper() for c in (color_order or self._color_order)]
    
        display_cfg = self._config.get("display", {}) or {}
        blink_enabled = bool(display_cfg.get("blink_enabled", False))
        try:
            blink_threshold = float(display_cfg.get("blink_threshold_percent", 100))
        except Exception:
            blink_threshold = 100.0
    
        # 收集输出顺序中的条目：(sp, color, led_id, pct)
        items: List[Tuple[int, str, int, float]] = []
        counts = {"R": 0, "G": 0, "B": 0}
    
        for color in order:
            pair = group.files.get(color)
            if not pair:
                continue
            txt_path, _jpg = pair
            sp_list, percent_list = self.parse_indices_and_percent_from_txt(txt_path)
            for sp, pct in zip(sp_list, percent_list):
                gobj = self.find_sp_group(sp)
                if not gobj:
                    continue  # 入库已校验，这里稳健跳过
                led1, led2, led3 = self.compute_led_ids_for_sp(sp, gobj)
                if color == "R":
                    items.append((sp, color, led1, pct))
                    counts["R"] += 1
                elif color == "G":
                    items.append((sp, color, led2, pct))
                    counts["G"] += 1
                else:
                    items.append((sp, color, led3, pct))
                    counts["B"] += 1
    
        # indices 按收集顺序
        indices: List[int] = [led for (_sp, _color, led, _pct) in items]
    
        # 生成与 indices 对齐的 attrs：按订单独立阈值判定
        attrs: List[int] = []
        blink_count = 0
        if blink_enabled:
            for (_sp, _color, _led, pct) in items:
                if pct >= blink_threshold:
                    attrs.append(1)
                    blink_count += 1
                else:
                    attrs.append(0)
    
        # 日志输出
        try:
            base = f"compose sp→LED key={group.key} R={counts['R']} G={counts['G']} B={counts['B']} total={len(indices)}"
            if blink_enabled:
                base += f" blink={blink_count}"
            log.info(base)
            if indices:
                log.debug(f"sample first={indices[:5]}")
        except Exception:
            pass
    
        ret_attrs: Optional[List[int]] = None
        if blink_enabled and blink_count > 0:
            ret_attrs = attrs
        return indices, ret_attrs
    
    def compose_indices_attrs_and_colors_for_group(self, group: GroupTriplet, color_order: List[str] | None = None) -> Tuple[List[int], Optional[List[int]], Optional[List[int]]]:
        """
        返回 indices、attrs(可选) 与 colors(与 indices 对齐；0=红/1=绿/2=蓝)。
        兼容旧方法 compose_indices_and_attrs_for_group 的行为，但额外提供 colors。
        """
        log = get_logger("mapping")
        order = [c.upper() for c in (color_order or self._color_order)]
    
        display_cfg = self._config.get("display", {}) or {}
        blink_enabled = bool(display_cfg.get("blink_enabled", False))
        try:
            blink_threshold = float(display_cfg.get("blink_threshold_percent", 100))
        except Exception:
            blink_threshold = 100.0
    
        items: List[Tuple[int, str, int, float]] = []
        counts = {"R": 0, "G": 0, "B": 0}
    
        for color in order:
            pair = group.files.get(color)
            if not pair:
                continue
            txt_path, _jpg = pair
            sp_list, percent_list = self.parse_indices_and_percent_from_txt(txt_path)
            for sp, pct in zip(sp_list, percent_list):
                gobj = self.find_sp_group(sp)
                if not gobj:
                    continue
                led1, led2, led3 = self.compute_led_ids_for_sp(sp, gobj)
                if color == "R":
                    items.append((sp, color, led1, pct))
                    counts["R"] += 1
                elif color == "G":
                    items.append((sp, color, led2, pct))
                    counts["G"] += 1
                else:
                    items.append((sp, color, led3, pct))
                    counts["B"] += 1
    
        indices: List[int] = [led for (_sp, _color, led, _pct) in items]
        colors: List[int] = [0 if c == "R" else (1 if c == "G" else 2) for (_sp, c, _led, _pct) in items]
    
        attrs: List[int] = []
        blink_count = 0
        if blink_enabled:
            for (_sp, _color, _led, pct) in items:
                if pct >= blink_threshold:
                    attrs.append(1)
                    blink_count += 1
                else:
                    attrs.append(0)
    
        try:
            base = f"compose(sp→LED+colors) key={group.key} R={counts['R']} G={counts['G']} B={counts['B']} total={len(indices)}"
            if blink_enabled:
                base += f" blink={blink_count}"
            log.info(base)
        except Exception:
            pass
    
        ret_attrs: Optional[List[int]] = None
        if blink_enabled and blink_count > 0:
            ret_attrs = attrs
        ret_colors: Optional[List[int]] = colors if len(colors) > 0 else None
        return indices, ret_attrs, ret_colors