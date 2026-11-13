"""配置加载。所有可调项见 config.default.json，用户覆盖见 ~/.config/video-extractor/config.json。"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SKILL_ROOT / "config.default.json"
USER_CONFIG = Path.home() / ".config" / "video-extractor" / "config.json"
CACHE_ROOT = Path.home() / ".cache" / "video-extractor"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(config_path: str | None = None) -> dict[str, Any]:
    cfg = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    for p in (USER_CONFIG, Path(config_path) if config_path else None):
        if p and Path(p).exists():
            cfg = _merge(cfg, json.loads(Path(p).read_text(encoding="utf-8")))
    # 新名优先，旧名（VIDEO_QUIZER_*）继续兼容，避免已有脚本静默失效
    for new, old, key in (
        ("VIDEO_EXTRACTOR_PROXY", "VIDEO_QUIZER_PROXY", "proxy"),
        ("VIDEO_EXTRACTOR_HF_ENDPOINT", "VIDEO_QUIZER_HF_ENDPOINT", "hf_endpoint"),
        ("VIDEO_EXTRACTOR_VAULT", "VIDEO_QUIZER_VAULT", "vault_path"),
    ):
        val = os.environ.get(new) or os.environ.get(old)
        if val:
            cfg[key] = val
    return cfg


def job_dir(source_id: str) -> Path:
    d = CACHE_ROOT / "jobs" / source_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def runs_dir() -> Path:
    d = CACHE_ROOT / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d
