"""Reject unsupported systems before loading macOS-only script dependencies."""
from configparser import ConfigParser
from pathlib import Path
import sys


def require_macos(script_path: str) -> None:
    if sys.platform == "darwin":
        return
    settings = ConfigParser(interpolation=None)
    settings.read(Path(script_path).with_name("config.ini"), encoding="utf-8")
    language = settings.get("general", "language", fallback="zh").strip().lower()
    if language == "en":
        raise SystemExit("This script supports macOS only; it will not run on Windows or Linux.")
    raise SystemExit("此脚本仅支持 macOS；Windows 和 Linux 上不会运行。")
