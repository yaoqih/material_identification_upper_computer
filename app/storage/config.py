from __future__ import annotations
from pathlib import Path
from typing import Any, Dict
import json
import os

class ConfigRepo:
    def __init__(self, default_path: str | Path = "configs/default.json") -> None:
        # 允许通过环境变量注入测试配置路径（便于缩短心跳周期等）
        env_path = os.environ.get("APP_CONFIG_PATH")
        self.default_path = Path(env_path) if env_path else Path(default_path)

    def load(self, path: str | Path | None = None) -> Dict[str, Any]:
        # 动态读取环境变量（允许测试在运行中注入配置路径）
        env_path_now = os.environ.get("APP_CONFIG_PATH")
        p = Path(path) if path else (Path(env_path_now) if env_path_now else self.default_path)
        with p.open("r", encoding="utf-8") as f:
            cfg: Dict[str, Any] = json.load(f)
        self._apply_defaults(cfg)
        return cfg

    def _apply_defaults(self, cfg: Dict[str, Any]) -> None:

        # comm 默认（依据文档心跳约定，参见 [docs/通信约定.md](docs/通信约定.md:64)）
        comm = cfg.setdefault("comm", {})
        comm.setdefault("enable_heartbeat", True)
        comm.setdefault("heartbeat_interval_seconds", 10)
        comm.setdefault("offline_failure_threshold", 10)
        # 兼容门控与负载参数
        comm.setdefault("duplicate_ack_mode", "duplicate_code")  # 可选：echo_last
        # 运行时可调参数（与现有代码行为对齐）
        comm.setdefault("bytes_per_frame", 512)
        comm.setdefault("ack_timeout_ms", 1000)
        comm.setdefault("cmd_timeout_ms", 2000)
        comm.setdefault("inter_frame_gap_ms", 10)

        # comm.retry 默认（重试策略为项目约定配置驱动）
        retry = comm.setdefault("retry", {})
        retry.setdefault("enabled", True)
        retry.setdefault("ack_timeout_ms", 300)
        retry.setdefault("max_attempts", 3)
        retry.setdefault("backoff_ms", 50)

        # dispatcher 默认
        cfg.setdefault("dispatcher", {}).setdefault("color_order", ["R", "G", "B"])

        # mapping 默认（兼容旧 snake 键 + leds_per_slot/offset）
        m = cfg.setdefault("mapping", {})
        m.setdefault("serpentine_enabled", m.get("snake", True))
        m.setdefault("cols", int(m.get("cols", 0)))
        m.setdefault("leds_per_slot", int(m.get("leds_per_slot", 3)))
        m.setdefault("offset", int(m.get("offset", 0)))

        # printing 默认
        pr = cfg.setdefault("printing", {})
        pr.setdefault("columns", 2)
        pr.setdefault("enabled", False)
        pr.setdefault("column_separator", " | ")

        # grouping 目录与模式默认（以防外部未配置）
        g = cfg.setdefault("grouping", {})
        g.setdefault("done_dir", "data/done")
        g.setdefault("error_dir", "data/error")
        g.setdefault("mode", g.get("mode", "triplet"))
        g.setdefault("n_to_color", {"N1": "R", "N2": "G", "N3": "B"})
        g.setdefault("color_order", ["R", "G", "B"])
        g.setdefault("name_tag_regex", r"^(?P<a>[^-]+)(?:-(?P<b>[^-]+))?-(?P<tag>N[0-9]+)$")

        # ingress 默认（就绪安静窗口，默认 0ms 不改变现有行为）
        ing = cfg.setdefault("ingress", {})
        ing.setdefault("ready_quiet_ms", 0)
        ing.setdefault("atomic_pair_enabled", True)
        ing.setdefault("allowed_extensions", [".txt", ".jpg", ".jpeg"])
        suff = ing.setdefault("atomic_pair_suffixes", {})
        suff.setdefault("part_suffix", ".part")
        suff.setdefault("lock_suffix", ".pairlock")

        # display 默认与旧键迁移（参见 [docs/任务需求.md](docs/任务需求.md) “百分比闪烁”条款）
        disp = cfg.setdefault("display", {})
        # 最小改动：默认启用闪烁承载；A1 长度由上层决定（attrs 或 MSB）
        disp.setdefault("blink_enabled", True)
        if "blink_threshold_percent" not in disp:
            thr = cfg.get("thresholds", {}).get("percent_blink")
            if isinstance(thr, (int, float)):
                try:
                    disp["blink_threshold_percent"] = int(round(float(thr) * 100))
                except Exception:
                    disp["blink_threshold_percent"] = 10
            else:
                disp["blink_threshold_percent"] = 10

        # parsing 默认
        parsing = cfg.setdefault("parsing", {})
        parsing.setdefault("deduplicate_indices", True)
        parsing.setdefault("allowed_code_prefix", ["SP", "X"])
        parsing.setdefault("row_pattern", r"^\s*(?:\d+)\s+([A-Za-z]+)(\d+)\s+([\d]+(?:\.\d+)?)\s*%\s*$")
        parsing.setdefault("alt_row_pattern", r"^\s*([A-Za-z]+)(\d+)\s+([\d]+(?:\.\d+)?)\s*%\s*$")
        parsing.setdefault("header_keywords", ["编号", "名称", "百分"])

        # sp_mapping 默认与校验
        default_groups = [
            {"id": 1, "start_sp": 1, "end_sp": 70, "cols_per_row": 5},
            {"id": 2, "start_sp": 71, "end_sp": 151, "cols_per_row": 5},
            {"id": 3, "start_sp": 152, "end_sp": 210, "cols_per_row": 5},
            {"id": 4, "start_sp": 211, "end_sp": 280, "cols_per_row": 5},
            {"id": 5, "start_sp": 281, "end_sp": 350, "cols_per_row": 5},
            {"id": 6, "start_sp": 351, "end_sp": 420, "cols_per_row": 5},
            {"id": 7, "start_sp": 421, "end_sp": 490, "cols_per_row": 5},
            {"id": 8, "start_sp": 491, "end_sp": 610, "cols_per_row": 5},
            {"id": 9, "start_sp": 611, "end_sp": 670, "cols_per_row": 5},
            {"id": 10, "start_sp": 671, "end_sp": 730, "cols_per_row": 5},
            {"id": 11, "start_sp": 731, "end_sp": 790, "cols_per_row": 5},
            {"id": 12, "start_sp": 791, "end_sp": 850, "cols_per_row": 5},
            {"id": 13, "start_sp": 851, "end_sp": 910, "cols_per_row": 5},
            {"id": 14, "start_sp": 911, "end_sp": 950, "cols_per_row": 4},
            {"id": 15, "start_sp": 1001, "end_sp": 1034, "cols_per_row": 3},
            {"id": 16, "start_sp": 1051, "end_sp": 1099, "cols_per_row": 6},
        ]
        default_spm = {"block_size": 100, "groups": default_groups}

        spm = cfg.get("sp_mapping")
        if not isinstance(spm, dict):
            cfg["sp_mapping"] = default_spm
            spm = cfg["sp_mapping"]
        # 补全缺省项
        spm.setdefault("block_size", 100)
        groups = spm.get("groups")
        if not isinstance(groups, list) or not groups:
            spm["groups"] = default_groups
        else:
            validated: list[dict] = []
            for gobj in groups:
                try:
                    gid = int(gobj.get("id"))
                    s = int(gobj.get("start_sp"))
                    e = int(gobj.get("end_sp"))
                    c = int(gobj.get("cols_per_row"))
                    if s <= e and c >= 1:
                        validated.append({"id": gid, "start_sp": s, "end_sp": e, "cols_per_row": c})
                except Exception:
                    continue
            spm["groups"] = validated if validated else default_groups
            # sp_mapping 运行时可调参数默认
            spm.setdefault("leds_per_slot", 3)
            spm.setdefault("start_corner", "TL")
            spm.setdefault("row_direction_even", "LR")
            spm.setdefault("row_direction_odd", "RL")

        # logging 默认与旧键迁移（最小改动且向后兼容）
        log = cfg.setdefault("logging", {})
        # 嵌套结构
        rot = log.setdefault("rotate", {})
        hexv = log.setdefault("hex", log.get("hex", {}))

        # 迁移旧键：capture_hex -> hex.capture；keep_days -> rotate.backup_count
        if "capture_hex" in log and "capture" not in hexv:
            try:
                hexv["capture"] = bool(log.get("capture_hex"))
            except Exception:
                hexv["capture"] = False

        # 顶层默认值
        log.setdefault("level", "INFO")
        log.setdefault("file", "logs/app.log")
        log.setdefault("format", "%(asctime)s %(levelname)s %(name)s - %(message)s")
        log.setdefault("device_info_file", "logs/device-info.log")
        log.setdefault("device_info_format", "%(asctime)s %(message)s")

        # 轮转默认（按大小）
        rot.setdefault("enabled", True)
        rot.setdefault("max_bytes", 1048576)
        if "backup_count" not in rot:
            try:
                rot["backup_count"] = int(log.get("keep_days", 3))
            except Exception:
                rot["backup_count"] = 3

        # HEX 捕获默认
        hexv.setdefault("capture", hexv.get("capture", False))
        hexv.setdefault("incoming", True)
        hexv.setdefault("outgoing", True)
        try:
            # 若外部提供为字符串，强转为 int；失败则回退
            hexv["max_bytes"] = int(hexv.get("max_bytes", 1024))
        except Exception:
            hexv["max_bytes"] = 1024