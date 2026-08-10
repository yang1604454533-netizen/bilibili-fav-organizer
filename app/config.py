"""B站收藏夹整理助手 - 配置持久化

使用 JSON 保存 AI 配置与分类列表，存于可执行文件同目录的 config.json（打包后即 exe 所在目录）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .ai_classifier import DEFAULT_CATEGORIES


def app_dir() -> Path:
    """配置目录：优先 %APPDATA%/BilibiliFavOrganizer（打包后 exe 目录可能只读）。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            d = Path(base) / "BilibiliFavOrganizer"
            try:
                d.mkdir(parents=True, exist_ok=True)
                return d
            except OSError:
                pass
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.cwd()


CONFIG_PATH = app_dir() / "config.json"

DEFAULTS = {
    "model": "deepseek-chat",
    "api_key": "",
    "base_url": "https://api.deepseek.com/v1",
    "temperature": 0.2,
    "max_tokens": 4000,
    "categories": list(DEFAULT_CATEGORIES),
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k in cfg:
                if k in saved:
                    cfg[k] = saved[k]
    except (OSError, ValueError) as exc:
        print(f"[config] 读取配置失败，使用默认值：{exc}")
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        # 保存失败不阻断主流程，仅提示
        print(f"[config] 保存配置失败：{exc}")