#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Modified: 2026-08-18 20:19 +02:00

"""
第二阶段：整理主 Gallery Organizer 留在原地的媒体。

交互式 TUI（推荐）：
    python organize_leftover_media.py

兼容命令行（默认 DRY RUN）：
    python organize_leftover_media.py "来源目录"

真正执行：
    python organize_leftover_media.py "来源目录" --apply

职责：
- 不替代主 Organizer；只处理主 Organizer 留下来的媒体/sidecar。
- 先恢复媒体组关系（Live Photo、Apple 多格式、XMP/AAE 等），再整组分类。
- 明确截图/录屏 -> 其他图片/截图与录屏。
- 明确网页/聊天/下载来源 -> 其他图片/下载与保存。
- 文件健康、时间正常但无法证明属于主相机图库 -> 其他图片/来源无法确认。
- 只有真正可能影响云端相册导入、时间线、Live Photo/sidecar 完整性或解码的问题才进入待修复媒体。
- 能被当前主 Organizer 完整确认的相机媒体，仍按主 Organizer 的原规则进入配置的正式 Gallery 目标。
- 完全重复先做全局内容预检（正式 Gallery + 本次剩余媒体之间），整组隔离到配置的第二阶段重复目录；不删除、不覆盖、不改名。
- 目标冲突、读取失败、无法安全恢复关系时宁可整组保持原位并报告。
- 默认 DRY RUN；只有明确 A/--apply 才实际移动。
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import html
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import struct
import tempfile
import termios
import tty
import shlex
import sqlite3
import time
import urllib.parse
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple


from gallery_config import (
    BACKUP_ROOT as BACKUP_APPLE_ROOT, GALLERY_ROOT,
    SECOND_STAGE_LIBRARY_ROOT as DESTINATION_ROOT,
    DUPLICATE_ROOT, OTHER_MEDIA_ROOT, REPAIR_MEDIA_ROOT, SCREEN_ROOT,
    SECOND_STAGE_DUPLICATE_ROOT, HASH_CACHE_DB, XATTR_TOOL, SIPS_TOOL,
)
SCRIPT_VERSION = "2026-08-27-centralized-path-config"

TIME_TOLERANCE_SECONDS = 20
ANDROID_VIDEO_LOCAL_UTC_OFFSETS = (0, 1, 2, 8, 9)
HASH_CHUNK_SIZE = 8 * 1024 * 1024
PROGRESS_BAR_WIDTH = 28
SOURCE_PROGRESS_INTERVAL = 20
SIDECAR_PROGRESS_INTERVAL = 20

FINDER_TAG_XATTR = "com.apple.metadata:_kMDItemUserTags"
CUSTOM_FILENAME_FINDER_TAG = "自定义文件名"


# 与主 Organizer 1854 共用：这些缓存只改变取得同一结果的速度，不改变判定规则。
_XATTR_NAME_CACHE: Dict[str, Set[str]] = {}


class Sha256Cache:
    """持久 SHA-256 缓存；文件状态变化时自动失效。"""
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.memory: Dict[Tuple[str, int, int, int], str] = {}
        self._conn: Optional[sqlite3.Connection] = None
        self.hits = 0
        self.misses = 0
        self.errors = 0

    def _connection(self) -> Optional[sqlite3.Connection]:
        if self._conn is not None:
            return self._conn
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), timeout=2.0)
            conn.execute("""CREATE TABLE IF NOT EXISTS sha256_cache (
                path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                ctime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL)""")
            conn.commit(); self._conn = conn; return conn
        except Exception:
            self.errors += 1; return None

    @staticmethod
    def _identity(path: Path) -> Tuple[str, int, int, int]:
        st = path.stat()
        return (str(path.resolve(strict=False)), int(st.st_size), int(st.st_mtime_ns), int(st.st_ctime_ns))

    def get(self, path: Path) -> Optional[str]:
        try: identity = self._identity(path)
        except OSError: return None
        cached = self.memory.get(identity)
        if cached is not None:
            self.hits += 1; return cached
        conn = self._connection()
        if conn is None:
            self.misses += 1; return None
        try:
            row = conn.execute("SELECT size,mtime_ns,ctime_ns,sha256 FROM sha256_cache WHERE path=?", (identity[0],)).fetchone()
        except Exception:
            self.errors += 1; self.misses += 1; return None
        if row is None or tuple(row[:3]) != identity[1:]:
            self.misses += 1; return None
        digest = str(row[3]); self.memory[identity] = digest; self.hits += 1; return digest

    def put(self, path: Path, digest: str) -> None:
        try: identity = self._identity(path)
        except OSError: return
        self.memory[identity] = digest
        conn = self._connection()
        if conn is None: return
        try:
            conn.execute("""INSERT INTO sha256_cache(path,size,mtime_ns,ctime_ns,sha256) VALUES(?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,
                ctime_ns=excluded.ctime_ns,sha256=excluded.sha256""", (*identity, digest))
            conn.commit()
        except Exception: self.errors += 1

    def close(self) -> None:
        if self._conn is not None:
            try: self._conn.close()
            except Exception: pass
            self._conn = None


_SHA256_CACHE = Sha256Cache(HASH_CACHE_DB)
atexit.register(_SHA256_CACHE.close)


PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".jfif",
    ".heic", ".heif", ".hif",
    ".png", ".gif", ".webp", ".avif", ".jxl",
    ".tif", ".tiff",
    ".dng", ".cr2", ".cr3", ".crw",
    ".nef", ".nrw", ".arw", ".sr2", ".srf",
    ".raf", ".rw2", ".orf", ".pef", ".srw", ".raw",
}

VIDEO_EXTENSIONS = {
    ".mov", ".mp4", ".m4v",
    ".3gp", ".3g2", ".avi", ".mkv", ".webm",
    ".mts", ".m2ts", ".mpg", ".mpeg",
    ".wmv", ".asf", ".mod", ".tod", ".ts",
}

SIDECAR_EXTENSIONS = {
    ".xmp", ".aae", ".thm", ".lrv", ".dop", ".pp3",
}

# 这些是明确的系统索引/垃圾目录。隐藏媒体本身不因点号或 UF_HIDDEN 被排除。
SYSTEM_EXCLUDED_DIR_NAMES = {
    ".spotlight-v100",
    ".trashes",
    ".trash",
    ".fseventsd",
    ".documentrevisions-v100",
    ".temporaryitems",
    ".mobilebackups",
    ".com.apple.timemachine.donotpresent",
    ".vol",
    "system volume information",
    "$recycle.bin",
    "lost+found",
}

SYSTEM_EXCLUDED_FILE_NAMES = {
    ".ds_store",
    ".localized",
    "thumbs.db",
    "desktop.ini",
}

# Apple Photos 常见的同一资产多格式导出候选。仅用于寻找关系，绝不凭扩展名直接绑定。
APPLE_VARIANT_PHOTO_EXTENSIONS = {
    ".heic", ".heif", ".hif", ".jpg", ".jpeg", ".dng",
}

VISUAL_SIMILARITY_THRESHOLD = 0.92
VISUAL_DIFFERENT_THRESHOLD = 0.78

APPLE_CAMERA_PHOTO_EXTENSIONS = {
    ".heic", ".heif", ".jpg", ".jpeg", ".dng",
}

APPLE_CAMERA_VIDEO_EXTENSIONS = {
    ".mov", ".mp4", ".m4v",
}

# 这些格式可以作为第二阶段媒体组的“主媒体”（例如 GIF + XMP），
# 但不应仅凭继承的设备/时间 EXIF 自动认定为手机相机原片。
# PNG 主要防截图/转换图误收；GIF 主要防导出/转换后的动画图继承相机 EXIF 后误收。
NON_CAMERA_AUTO_ACCEPT_PHOTO_EXTENSIONS = {
    ".png", ".gif",
}

PHOTO_DATE_FIELDS = (
    "DateTimeOriginal",
    "SubSecDateTimeOriginal",
    "CreateDate",
    "DateCreated",
)

VIDEO_DATE_FIELDS = (
    "DateTimeOriginal",
    "CreateDate",
    "MediaCreateDate",
    "TrackCreateDate",
    "DateCreated",
)

CONTENT_ID_FIELDS = (
    "ContentIdentifier",
    "MediaGroupUUID",
)

SIDECAR_IDENTIFIER_FIELDS = (
    "ContentIdentifier",
    "MediaGroupUUID",
    "DocumentID",
    "InstanceID",
    "OriginalDocumentID",
    "DerivedFromDocumentID",
    "ImageUniqueID",
    "Identifier",
)

SIDECAR_FILENAME_FIELDS = (
    "OriginalFileName",
    "PreservedFileName",
    "SourceFileName",
    "OriginalFilename",
)

UUID_RE = re.compile(
    r"(?i)(?:uuid:|xmp\.did:)?"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)

MAX_SIDECAR_TEXT_BYTES = 16 * 1024 * 1024

# ExifTool 常见格式：
# 2026:08:17 14:35:22
# 2026:08:17 14:35:22+02:00
# 2026:08:17 14:35:22.123
METADATA_TIMESTAMP_RE = re.compile(
    r"^\s*(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})"
)

# 仅用于 Android 相机命名核对，不作为日期来源。
ANDROID_MEDIA_RE = re.compile(
    r"^(MVIMG|IMG|VID|PANO)[_-]?"
    r"(\d{4})(\d{2})(\d{2})"
    r"[_-]?"
    r"(\d{2})(\d{2})(\d{2})",
    re.IGNORECASE,
)

APPLE_IMG_RE = re.compile(r"^IMG_\d{4}$", re.IGNORECASE)
APPLE_EDITED_IMG_RE = re.compile(r"^IMG_E\d{4}$", re.IGNORECASE)
APPLE_ORIGINAL_ADJUSTMENT_AAE_RE = re.compile(r"^IMG_O(\d{4})$", re.IGNORECASE)
APPLE_ORIGINAL_ADJUSTMENT_TIME_TOLERANCE_SECONDS = 2

# 仅用于判断“是否属于常见相机自动命名”，避免把 Pixel / Samsung /
# 常见 DSC 命名误标为“自定义文件名”。这些规则不参与拍摄时间判定。
KNOWN_CAMERA_FILENAME_RES = (
    re.compile(r"^PXL_\d{8}_\d{6}(?:\d{3})?(?:_[A-Z0-9]+)*$", re.IGNORECASE),
    re.compile(r"^\d{8}_\d{6}(?:_\d+)?$", re.IGNORECASE),
    re.compile(r"^DSC[_-]?\d+$", re.IGNORECASE),
    re.compile(r"^DSCF\d+$", re.IGNORECASE),
    re.compile(r"^DSCN\d+$", re.IGNORECASE),
    re.compile(r"^SAM[_-]?\d+$", re.IGNORECASE),
)

SCREENSHOT_NAME_RE = re.compile(
    r"(?:"
    r"(?:^|[_\-\s])(?:screenshot|screen[_\-\s]?shot)(?:[_\-\s(（]|$)|"
    r"(?:屏幕截图|屏幕快照|截屏|截图)|"
    r"^pixpin_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:[_\-].*)?$"
    r")",
    re.IGNORECASE,
)

SCREENSHOT_DIR_NAMES = {
    "screenshot",
    "screenshots",
    "screen shot",
    "screen shots",
    "屏幕截图",
    "屏幕快照",
    "截屏",
    "截图",
}

SCREEN_RECORDING_NAME_RE = re.compile(
    r"(?:"
    r"^rpreplay_final\d+(?:[_\-].*)?$|"
    r"(?:^|[_\-\s])(?:screen[_\-\s]?(?:recording|record)|screenrecord(?:ing|er)?|录屏|屏幕录制)(?:[_\-\s]|$)"
    r")",
    re.IGNORECASE,
)

# metadata 中出现这些明确描述时，负面证据优先于 Make/Model/DateTimeOriginal。
SCREEN_CAPTURE_METADATA_RE = re.compile(
    r"(?:screenshot|screen[_\-\s]?shot|screen[_\-\s]?(?:recording|record)|screenrecord(?:ing)?|"
    r"screen[_\-\s]?capture|截屏|截图|录屏|屏幕录制)",
    re.IGNORECASE,
)

RISKY_SOURCE_DIR_NAMES = {
    "download", "downloads", "下载",
    "wechat", "wechat files", "微信", "微信文件",
    "telegram", "whatsapp", "messages", "imessage",
    "messenger", "discord", "line", "qq", "tencent",
}

STRONG_DOWNLOAD_AGENT_RE = re.compile(
    r"(?:safari|chrome|chromium|firefox|edge|arc|opera|brave|wechat|telegram|whatsapp|discord|messenger)",
    re.IGNORECASE,
)

WHERE_FROMS_XATTR = "com.apple.metadata:kMDItemWhereFroms"
QUARANTINE_XATTR = "com.apple.quarantine"

SOURCE_STATE_CLEAN = "CLEAN"
SOURCE_STATE_AMBIGUOUS = "AMBIGUOUS"
SOURCE_STATE_EXCLUDED = "EXCLUDED"


Timestamp = Tuple[int, int, int, int, int, int]


@dataclass
class MediaInfo:
    path: Path
    kind: str  # photo / video / sidecar
    metadata: dict = field(default_factory=dict)
    content_id: Optional[str] = None
    timestamp: Optional[Timestamp] = None
    date_source: Optional[str] = None
    filename_timestamp: Optional[Timestamp] = None
    filename_timestamp_conflict: bool = False
    trusted_exif: bool = False
    camera_origin: bool = False
    camera_origin_reason: str = ""
    source_state: str = SOURCE_STATE_CLEAN
    source_reason: str = "无来源负面证据"
    source_evidence: List[str] = field(default_factory=list)
    android_video_time_verified: bool = False
    android_video_time_reason: str = ""


@dataclass
class MoveGroup:
    primary: Path
    members: List[Path]
    year: int
    month: int
    day: int
    date_source: str
    description: str


@dataclass
class Problem:
    path: str
    reason: str
    severity: str = "skip"  # skip / failure / warning


@dataclass
class ProblemDisplayEntry:
    primary: Problem
    companions: List[Problem] = field(default_factory=list)


@dataclass
class FinderTagPlanItem:
    source: Path
    target: Path
    original_xattr_exists: bool
    original_xattr_raw: Optional[bytes]
    desired_xattr_raw: bytes
    already_had_tag: bool


@dataclass
class SidecarContentEvidence:
    readable: bool = False
    text: str = ""
    identifiers: Set[str] = field(default_factory=set)
    embedded_strings: Set[str] = field(default_factory=set)
    parse_notes: List[str] = field(default_factory=list)
    aae_editor_bundle_id: Optional[str] = None
    aae_format_identifier: Optional[str] = None
    aae_adjustment_timestamp: Optional[Timestamp] = None


@dataclass
class SidecarResolution:
    status: str  # confirmed / generic / ambiguous / unrelated
    targets: Set[Path] = field(default_factory=set)
    reason: str = ""


@dataclass
class SidecarLookupIndex:
    """1854 风格 sidecar 线性索引；只优化查找，不改变归属证据优先级。"""
    media_by_directory: Dict[Path, List[MediaInfo]] = field(default_factory=dict)
    media_by_name: Dict[Path, Dict[str, MediaInfo]] = field(default_factory=dict)
    media_by_stem: Dict[Path, Dict[str, List[MediaInfo]]] = field(default_factory=dict)
    media_by_identifier: Dict[Path, Dict[str, Set[Path]]] = field(default_factory=dict)
    xmp_explicit_times_by_stem: Dict[Tuple[Path, str], List[Tuple[Timestamp, int, str]]] = field(default_factory=dict)
    filename_matcher_by_directory: Dict[Path, Optional[re.Pattern[str]]] = field(default_factory=dict)



def format_progress_bar(current: int, total: int, width: int = PROGRESS_BAR_WIDTH) -> str:
    """只负责终端显示；不参与任何整理/判定逻辑。"""
    if total <= 0:
        return "[" + ("░" * width) + "] 0/0   0.0%"
    safe_current = max(0, min(current, total))
    ratio = safe_current / total
    filled = int(ratio * width)
    if safe_current >= total:
        filled = width
    bar = ("█" * filled) + ("░" * (width - filled))
    return f"[{bar}] {safe_current}/{total} {ratio * 100:5.1f}%"


def _terminal_text_width(text: str) -> int:
    """Approximate terminal column width without third-party dependencies."""
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _truncate_terminal_text(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if _terminal_text_width(text) <= max_width:
        return text

    ellipsis = "…"
    target = max(0, max_width - _terminal_text_width(ellipsis))
    out: List[str] = []
    used = 0
    for char in text:
        char_width = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in ("W", "F") else 1)
        if used + char_width > target:
            break
        out.append(char)
        used += char_width
    return "".join(out) + ellipsis


class PinnedProgressLine:
    """Reserve the terminal's last row for one in-place progress status line."""

    def __init__(self) -> None:
        self.active = False
        self.rows = 0
        self.columns = 0

    def _tty_supported(self) -> bool:
        return (
            sys.stdout.isatty()
            and os.environ.get("TERM", "").lower() != "dumb"
        )

    def _size(self) -> Tuple[int, int]:
        size = shutil.get_terminal_size(fallback=(100, 30))
        return max(4, size.lines), max(20, size.columns)

    def _setup(self) -> bool:
        if not self._tty_supported():
            return False

        rows, columns = self._size()
        if self.active and (rows, columns) == (self.rows, self.columns):
            return True

        if self.active:
            self.close()

        self.rows = rows
        self.columns = columns
        # Keep ordinary print() output in rows 1..rows-1; reserve the last row.
        sys.stdout.write(f"\033[1;{rows - 1}r\033[{rows - 1};1H")
        sys.stdout.flush()
        self.active = True
        return True

    def update(self, line: str) -> None:
        if not self._setup():
            # Redirected/non-interactive output remains line-oriented and audit-friendly.
            print(line, flush=True)
            return

        rendered = _truncate_terminal_text(line, self.columns - 1)
        # Save the log cursor, repaint the reserved bottom row, then restore it.
        sys.stdout.write(
            f"\0337\033[{self.rows};1H\033[2K{rendered}\0338"
        )
        sys.stdout.flush()

    def close(self) -> None:
        if not self.active:
            return
        try:
            # Clear the pinned row first, then restore the terminal's full scroll region.
            sys.stdout.write(
                f"\0337\033[{self.rows};1H\033[2K\0338\033[r"
            )
            sys.stdout.flush()
        finally:
            self.active = False
            self.rows = 0
            self.columns = 0


_PINNED_PROGRESS = PinnedProgressLine()
atexit.register(_PINNED_PROGRESS.close)


def print_stage_progress(label: str, current: int, total: int, detail: str = "") -> None:
    """交互终端固定底部原地刷新；重定向输出时逐行记录。"""
    suffix = f"  {detail}" if detail else ""
    _PINNED_PROGRESS.update(
        f"[{label}] {format_progress_bar(current, total)}{suffix}"
    )


def finish_stage_progress() -> None:
    """Restore the normal terminal before summaries/prompts are printed."""
    _PINNED_PROGRESS.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "从指定来源目录扫描手机相机媒体，并固定整理到 "
            f"{DESTINATION_ROOT}/YYYY/YYYY-MM/；与目标字节完全相同的来源副本 "
            f"移入 {DUPLICATE_ROOT}/。"
        )
    )
    parser.add_argument(
        "folder",
        nargs="?",
        help=(
            "来源目录；必须位于 "
            f"{BACKUP_APPLE_ROOT} 内。省略时进入交互式 TUI。目标目录固定为 {DESTINATION_ROOT}；"
            f"完全重复副本隔离到 {DUPLICATE_ROOT}。"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正移动文件；不加此参数时只预览。",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=250,
        help="每批交给 ExifTool 的文件数（默认 250）。",
    )
    return parser.parse_args()


def require_exiftool() -> str:
    exe = shutil.which("exiftool")
    if exe:
        return exe

    print("错误：没有找到 ExifTool。", file=sys.stderr)
    print("安装：brew install exiftool", file=sys.stderr)
    raise SystemExit(2)


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in PHOTO_EXTENSIONS:
        return "photo"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in SIDECAR_EXTENSIONS:
        return "sidecar"
    return "other"


def same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve(strict=False) == b.resolve(strict=False)
    except OSError:
        return os.path.abspath(str(a)) == os.path.abspath(str(b))


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def iter_candidate_files(
    source_root: Path,
    destination_root: Path,
    duplicate_root: Path,
) -> Iterator[Path]:
    """
    递归扫描来源目录，包括：
    - 点号开头的隐藏文件/目录；
    - Finder/chflags hidden 项（os.walk 本来就能看到，不能据此跳过）。

    只排除明确的系统索引/垃圾目录、AppleDouble `._*` 元数据文件、
    符号链接，以及来源树内的固定 Gallery 目标子树。
    """
    supported = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS | SIDECAR_EXTENSIONS

    skip_destination_subtree = (
        not same_path(source_root, destination_root)
        and path_is_within(destination_root, source_root)
    )
    skip_duplicate_subtree = (
        not same_path(source_root, duplicate_root)
        and path_is_within(duplicate_root, source_root)
    )

    for current_dir, dirnames, filenames in os.walk(
        source_root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_dir)

        kept_dirs: List[str] = []
        for name in dirnames:
            child = current / name
            folded = name.casefold()

            if folded in SYSTEM_EXCLUDED_DIR_NAMES:
                continue
            if child.is_symlink():
                continue
            if skip_destination_subtree and same_path(child, destination_root):
                continue
            if skip_duplicate_subtree and same_path(child, duplicate_root):
                continue

            kept_dirs.append(name)

        dirnames[:] = kept_dirs

        for name in filenames:
            folded = name.casefold()

            # `._IMG_1234.JPG` 可能带照片扩展名，但只是 AppleDouble 元数据，绝不能当照片。
            if name.startswith("._"):
                continue
            if folded in SYSTEM_EXCLUDED_FILE_NAMES:
                continue

            path = current / name

            if path.is_symlink() or not path.is_file():
                continue

            if path.suffix.lower() in supported:
                yield path


def chunks(items: Sequence[Path], size: int) -> Iterator[List[Path]]:
    if size <= 0:
        size = 250

    for i in range(0, len(items), size):
        yield list(items[i:i + size])


def read_metadata_batch(
    exiftool: str,
    files: Sequence[Path],
) -> Dict[str, dict]:
    if not files:
        return {}

    cmd = [
        exiftool,
        "-j",
        "-G1",
        "-charset", "filename=UTF8",

        "-DateTimeOriginal",
        "-SubSecDateTimeOriginal",
        "-CreateDate",
        "-MediaCreateDate",
        "-TrackCreateDate",
        "-DateCreated",
        "-OffsetTime",
        "-OffsetTimeOriginal",
        "-OffsetTimeDigitized",

        "-ContentIdentifier",
        "-MediaGroupUUID",
        "-DocumentID",
        "-InstanceID",
        "-OriginalDocumentID",
        "-DerivedFromDocumentID",
        "-ImageUniqueID",
        "-Identifier",
        "-OriginalFileName",
        "-PreservedFileName",
        "-SourceFileName",
        "-OriginalFilename",

        "-Make",
        "-Model",
        "-LensMake",
        "-LensModel",
        "-DeviceManufacturer",
        "-DeviceModelName",

        "-ImageWidth",
        "-ImageHeight",
        "-ExifImageWidth",
        "-ExifImageHeight",
        "-Orientation",
        "-Duration",

        # 来源/截图/屏幕录制辅助证据。只读，不修改媒体。
        "-Software",
        "-CreatorTool",
        "-Encoder",
        "-HandlerDescription",
        "-Comment",
        "-UserComment",
        "-ImageDescription",
        "-Description",
        "-Title",
        "-SourceURL",
        "-URL",
        "-OriginatingProgram",

        *[str(path) for path in files],
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # ExifTool 有时因 warning 返回 1，但 JSON 仍然有效。
    if result.returncode not in (0, 1):
        raise RuntimeError(
            result.stderr.strip()
            or f"ExifTool failed ({result.returncode})"
        )

    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"无法解析 ExifTool JSON：{exc}") from exc

    result_map: Dict[str, dict] = {}

    for row in rows:
        source = row.get("SourceFile")
        if source:
            result_map[str(Path(source))] = row

    return result_map


def plain_tag_name(key: str) -> str:
    return key.split(":")[-1]


def metadata_values_for(
    metadata: dict,
    wanted_name: str,
) -> List[str]:
    values: List[str] = []

    for key, value in metadata.items():
        if (
            plain_tag_name(key) == wanted_name
            and isinstance(value, str)
            and value.strip()
        ):
            values.append(value.strip())

    return values


def metadata_scalar_values_for(
    metadata: dict,
    wanted_name: str,
) -> List[object]:
    values: List[object] = []
    for key, value in metadata.items():
        if plain_tag_name(key) == wanted_name and value not in (None, ""):
            values.append(value)
    return values


def metadata_values_for_device_identity(
    metadata: dict,
    wanted_name: str,
) -> List[str]:
    """读取真正可作为相机/设备身份的字段，明确排除 ICC 色彩配置来源。"""
    values: List[str] = []

    for key, value in metadata.items():
        if plain_tag_name(key) != wanted_name:
            continue
        if not isinstance(value, str) or not value.strip():
            continue

        # ExifTool -G1 会产生例如 ICC-header:DeviceManufacturer。
        # 该字段描述 ICC profile 的设备/厂商，不代表拍摄设备，不能用作相机来源证据。
        group_name = key.rsplit(":", 1)[0] if ":" in key else ""
        if "icc" in group_name.casefold():
            continue

        values.append(value.strip())

    return values


def first_device_metadata_value(
    metadata: dict,
    names: Sequence[str],
) -> Optional[str]:
    for name in names:
        values = metadata_values_for_device_identity(metadata, name)
        if values:
            return values[0]

    return None


def flatten_metadata_value(value: object) -> Iterator[object]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from flatten_metadata_value(key)
            yield from flatten_metadata_value(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from flatten_metadata_value(item)
    else:
        yield value


def normalize_identifier(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().casefold()
    if not text:
        return None
    text = re.sub(r"^(?:uuid:|xmp\.did:)", "", text, flags=re.IGNORECASE)
    return text


def metadata_identifiers(metadata: dict) -> Set[str]:
    result: Set[str] = set()
    for field_name in SIDECAR_IDENTIFIER_FIELDS:
        for value in metadata_scalar_values_for(metadata, field_name):
            for scalar in flatten_metadata_value(value):
                normalized = normalize_identifier(scalar)
                if normalized:
                    result.add(normalized)
    return result


def metadata_filename_hints(metadata: dict) -> Set[str]:
    hints: Set[str] = set()
    for field_name in SIDECAR_FILENAME_FIELDS:
        for value in metadata_scalar_values_for(metadata, field_name):
            for scalar in flatten_metadata_value(value):
                text = str(scalar).strip()
                if not text:
                    continue
                decoded = html.unescape(urllib.parse.unquote(text)).replace("\\", "/")
                basename = decoded.rsplit("/", 1)[-1].strip()
                if basename:
                    hints.add(basename.casefold())
    return hints


def parse_utc_offset_minutes(value: object) -> Optional[int]:
    """Parse Z / +08:00 / -0530 style offsets into signed minutes."""
    text = str(value).strip()
    if not text:
        return None
    if text.upper() == "Z":
        return 0
    match = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", text)
    if not match:
        return None
    hours = int(match.group(2))
    minutes = int(match.group(3))
    if hours > 23 or minutes > 59:
        return None
    total = hours * 60 + minutes
    return total if match.group(1) == "+" else -total


def timestamp_explicit_utc_offset_minutes(value: str) -> Optional[int]:
    """Return an explicit timezone suffix from an ExifTool timestamp, if present."""
    text = value.strip()
    match = re.search(r"(Z|[+-]\d{2}:?\d{2})\s*$", text, re.IGNORECASE)
    if not match:
        return None
    return parse_utc_offset_minutes(match.group(1))


def timestamp_as_utc_datetime(
    timestamp: Optional[Timestamp],
    utc_offset_minutes: Optional[int],
) -> Optional[datetime]:
    if timestamp is None or utc_offset_minutes is None:
        return None
    try:
        local = datetime(*timestamp)
    except ValueError:
        return None
    return local - timedelta(minutes=utc_offset_minutes)


def utc_timestamp_difference_seconds(
    utc_timestamp: Optional[Timestamp],
    local_timestamp: Optional[Timestamp],
    local_utc_offset_minutes: Optional[int],
) -> Optional[int]:
    """Compare a UTC wall-clock tuple with a local wall-clock tuple carrying an offset."""
    if utc_timestamp is None:
        return None
    try:
        utc_dt = datetime(*utc_timestamp)
    except ValueError:
        return None
    local_as_utc = timestamp_as_utc_datetime(local_timestamp, local_utc_offset_minutes)
    if local_as_utc is None:
        return None
    return int(abs((utc_dt - local_as_utc).total_seconds()))


def metadata_offsets_for_time_field(metadata: dict, field_name: str) -> List[Tuple[int, str]]:
    """Return only explicit EXIF timezone offsets that semantically match a time field."""
    if field_name == "DateTimeOriginal":
        offset_fields = ("OffsetTimeOriginal", "OffsetTime")
    elif field_name == "CreateDate":
        offset_fields = ("OffsetTimeDigitized", "OffsetTime")
    else:
        offset_fields = ("OffsetTime",)

    result: List[Tuple[int, str]] = []
    seen: Set[int] = set()
    for offset_field in offset_fields:
        for value in metadata_scalar_values_for(metadata, offset_field):
            parsed = parse_utc_offset_minutes(value)
            if parsed is None or parsed in seen:
                continue
            seen.add(parsed)
            result.append((parsed, f"{offset_field}={value}"))
    return result


def same_stem_xmp_explicit_times(
    candidate: MediaInfo,
    infos: Dict[Path, MediaInfo],
) -> List[Tuple[Timestamp, int, str]]:
    """Use a same-stem XMP only as explicit timezone evidence, never as a guessed offset."""
    result: List[Tuple[Timestamp, int, str]] = []
    stem = candidate.path.stem.casefold()
    for info in infos.values():
        if info.path.parent != candidate.path.parent:
            continue
        if info.path.suffix.lower() != ".xmp":
            continue
        if info.path.stem.casefold() != stem:
            continue
        for field_name in ("DateCreated", "DateTimeOriginal", "CreateDate"):
            for value in metadata_values_for(info.metadata, field_name):
                parsed = parse_metadata_timestamp(value)
                offset = timestamp_explicit_utc_offset_minutes(value)
                if parsed is None or offset is None:
                    continue
                result.append((parsed, offset, f"{info.path.name}:{field_name}={value}"))
    return result


def metadata_duration_seconds(metadata: dict) -> Optional[float]:
    for value in metadata_scalar_values_for(metadata, "Duration"):
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            continue
        try:
            return float(text)
        except ValueError:
            pass
        match = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)", text)
        if match:
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2))
            seconds = float(match.group(3))
            return hours * 3600 + minutes * 60 + seconds
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*s(?:ec(?:onds?)?)?\b", text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def first_metadata_value(
    metadata: dict,
    names: Sequence[str],
) -> Optional[str]:
    for name in names:
        values = metadata_values_for(metadata, name)
        if values:
            return values[0]

    return None


def parse_metadata_timestamp(value: str) -> Optional[Timestamp]:
    match = METADATA_TIMESTAMP_RE.match(value)
    if not match:
        return None

    y, month, day, hour, minute, second = map(int, match.groups())

    if not (1900 <= y <= 2200):
        return None
    if not (1 <= month <= 12):
        return None
    if not (1 <= day <= 31):
        return None
    if not (0 <= hour <= 23):
        return None
    if not (0 <= minute <= 59):
        return None
    if not (0 <= second <= 60):
        return None

    return y, month, day, hour, minute, second


def choose_embedded_timestamp(
    metadata: dict,
    fields: Sequence[str],
) -> Tuple[Optional[Timestamp], Optional[str]]:
    for field in fields:
        for value in metadata_values_for(metadata, field):
            parsed = parse_metadata_timestamp(value)

            if parsed is not None:
                return parsed, f"metadata:{field}"

    return None, None


STRONG_ANDROID_VIDEO_VERIFY_FIELDS = (
    "DateTimeOriginal",
    "MediaCreateDate",
    "CreateDate",
    "DateCreated",
)

# 部分 Android/QuickTime 文件的 MediaCreateDate/CreateDate 可能更接近录像结束/封装完成时间。
# 仅这两个字段允许结合 Duration 反推录像开始时间；DateTimeOriginal/DateCreated 不做减时长处理。
ANDROID_VIDEO_DURATION_END_TIME_FIELDS = {"MediaCreateDate", "CreateDate"}
ANDROID_VIDEO_MAX_DURATION_FALLBACK_SECONDS = 24 * 60 * 60


def shift_timestamp_hours(timestamp: Timestamp, hours: int) -> Timestamp:
    """仅在内存中解释时间；绝不写回媒体 metadata。"""
    value = datetime(*timestamp) + timedelta(hours=hours)
    return (
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
    )


def verify_android_video_time_against_filename(
    metadata: dict,
    filename_timestamp: Timestamp,
) -> Tuple[bool, Optional[Timestamp], Optional[str], str]:
    """
    用标准 Android VID/MVIMG 文件名中的“当地墙上时间”交叉验证视频 metadata。

    - 检查全部强时间字段，而不是只取第一个；
    - 对每个候选尝试：原值、UTC+1、UTC+2、UTC+8、UTC+9；
    - 第一条路径：转换后的强时间字段直接与文件名 <= TIME_TOLERANCE_SECONDS；
    - 第二条路径：仅 MediaCreateDate/CreateDate 可在存在合法 Duration 时，
      用“转换后的时间 - Duration”推算录像开始时间，再与文件名比较；
    - Duration 必须 >0 且 <= 24 小时，防止异常时长参与自动确认；
    - TrackCreateDate 可用于诊断，但不能单独放行；
    - 成功后返回 filename_timestamp 作为已经 metadata 验证的当地拍摄开始时间；
    - 此函数只读 metadata，绝不修复/写入文件。
    """
    # candidate: difference, mode_rank(0=direct, 1=minus-duration), field_rank,
    #            offset_rank, field_name, raw_timestamp, duration_or_none
    matches: List[Tuple[int, int, int, int, str, Timestamp, Optional[float]]] = []
    closest: Optional[
        Tuple[int, int, int, int, str, Timestamp, Optional[float]]
    ] = None

    duration = metadata_duration_seconds(metadata)
    duration_is_usable = (
        duration is not None
        and duration > 0
        and duration <= ANDROID_VIDEO_MAX_DURATION_FALLBACK_SECONDS
    )

    def consider(
        difference: Optional[int],
        mode_rank: int,
        field_rank: int,
        offset_rank: int,
        field_name: str,
        raw_timestamp: Timestamp,
        candidate_duration: Optional[float],
    ) -> None:
        nonlocal closest
        if difference is None:
            return
        candidate = (
            difference,
            mode_rank,
            field_rank,
            offset_rank,
            field_name,
            raw_timestamp,
            candidate_duration,
        )
        if closest is None or candidate[:4] < closest[:4]:
            closest = candidate
        if difference <= TIME_TOLERANCE_SECONDS:
            matches.append(candidate)

    for field_rank, field_name in enumerate(STRONG_ANDROID_VIDEO_VERIFY_FIELDS):
        for value in metadata_values_for(metadata, field_name):
            parsed = parse_metadata_timestamp(value)
            if parsed is None:
                continue

            for offset_rank, offset_hours in enumerate(ANDROID_VIDEO_LOCAL_UTC_OFFSETS):
                adjusted = shift_timestamp_hours(parsed, offset_hours)

                # 路径 A：强时间字段本身就是录像开始/拍摄时间。
                direct_difference = timestamp_difference_seconds(
                    filename_timestamp,
                    adjusted,
                )
                consider(
                    direct_difference,
                    0,
                    field_rank,
                    offset_rank,
                    field_name,
                    parsed,
                    None,
                )

                # 路径 B：部分 Android 视频把 MediaCreateDate/CreateDate 写在
                # 录像结束/封装完成附近。用 Duration 反推开始时间，再与 VID 文件名核对。
                if (
                    duration_is_usable
                    and field_name in ANDROID_VIDEO_DURATION_END_TIME_FIELDS
                ):
                    adjusted_value = datetime(*adjusted) - timedelta(seconds=duration)
                    inferred_start: Timestamp = (
                        adjusted_value.year,
                        adjusted_value.month,
                        adjusted_value.day,
                        adjusted_value.hour,
                        adjusted_value.minute,
                        adjusted_value.second,
                    )
                    duration_difference = timestamp_difference_seconds(
                        filename_timestamp,
                        inferred_start,
                    )
                    consider(
                        duration_difference,
                        1,
                        field_rank,
                        offset_rank,
                        field_name,
                        parsed,
                        duration,
                    )

    if matches:
        (
            difference,
            mode_rank,
            _field_rank,
            offset_rank,
            field_name,
            raw_timestamp,
            matched_duration,
        ) = min(matches, key=lambda item: item[:4])
        offset_hours = ANDROID_VIDEO_LOCAL_UTC_OFFSETS[offset_rank]
        if offset_hours == 0:
            interpretation = "metadata 原值已是当地时间"
        else:
            interpretation = f"按 UTC+{offset_hours} 转为当地时间"

        if mode_rank == 0:
            reason = (
                f"{field_name}={format_timestamp(raw_timestamp)}；{interpretation}；"
                f"与文件名当地时间相差 {difference} 秒"
            )
            source = f"metadata:{field_name}"
        else:
            assert matched_duration is not None
            adjusted = shift_timestamp_hours(raw_timestamp, offset_hours)
            adjusted_value = datetime(*adjusted) - timedelta(seconds=matched_duration)
            inferred_start = (
                adjusted_value.year,
                adjusted_value.month,
                adjusted_value.day,
                adjusted_value.hour,
                adjusted_value.minute,
                adjusted_value.second,
            )
            reason = (
                f"{field_name}={format_timestamp(raw_timestamp)}；{interpretation}；"
                f"减去 Duration={matched_duration:.3f} 秒推算录像开始时间 "
                f"{format_timestamp(inferred_start)}；"
                f"与文件名当地时间相差 {difference} 秒"
            )
            source = f"metadata:{field_name}-Duration"

        return (
            True,
            filename_timestamp,
            source,
            reason,
        )

    # TrackCreateDate 只用于解释“为什么看起来很接近却仍不放行”。
    track_best: Optional[Tuple[int, int, Timestamp]] = None
    for value in metadata_values_for(metadata, "TrackCreateDate"):
        parsed = parse_metadata_timestamp(value)
        if parsed is None:
            continue
        for offset_rank, offset_hours in enumerate(ANDROID_VIDEO_LOCAL_UTC_OFFSETS):
            adjusted = shift_timestamp_hours(parsed, offset_hours)
            difference = timestamp_difference_seconds(filename_timestamp, adjusted)
            if difference is None:
                continue
            candidate = (difference, offset_rank, parsed)
            if track_best is None or candidate[:2] < track_best[:2]:
                track_best = candidate

    if track_best is not None and track_best[0] <= TIME_TOLERANCE_SECONDS:
        difference, offset_rank, raw_timestamp = track_best
        offset_hours = ANDROID_VIDEO_LOCAL_UTC_OFFSETS[offset_rank]
        offset_text = "原值" if offset_hours == 0 else f"UTC+{offset_hours}"
        return (
            False,
            None,
            None,
            "仅 TrackCreateDate 可匹配："
            f"{format_timestamp(raw_timestamp)} 经 {offset_text} 后与文件名相差 {difference} 秒；"
            "TrackCreateDate 单独不足以确认相机拍摄时间",
        )

    if closest is not None:
        (
            difference,
            mode_rank,
            _field_rank,
            offset_rank,
            field_name,
            raw_timestamp,
            closest_duration,
        ) = closest
        offset_hours = ANDROID_VIDEO_LOCAL_UTC_OFFSETS[offset_rank]
        offset_text = "原值" if offset_hours == 0 else f"UTC+{offset_hours}"
        if mode_rank == 1 and closest_duration is not None:
            return (
                False,
                None,
                None,
                "强视频时间字段没有通过验证；最接近的是 "
                f"{field_name}={format_timestamp(raw_timestamp)} 经 {offset_text} 后再减 "
                f"Duration={closest_duration:.3f} 秒，仍相差 {difference} 秒",
            )
        return (
            False,
            None,
            None,
            "强视频时间字段没有通过验证；最接近的是 "
            f"{field_name}={format_timestamp(raw_timestamp)} 经 {offset_text} 后仍相差 {difference} 秒",
        )

    return (
        False,
        None,
        None,
        "没有可用于校验的强视频时间字段（DateTimeOriginal/MediaCreateDate/CreateDate/DateCreated）",
    )


def choose_content_identifier(metadata: dict) -> Optional[str]:
    for field in CONTENT_ID_FIELDS:
        value = first_metadata_value(metadata, (field,))

        if value:
            return value

    return None


def android_filename_prefix(path: Path) -> Optional[str]:
    match = ANDROID_MEDIA_RE.match(path.stem)
    if not match:
        return None
    return match.group(1).upper()


def is_known_camera_auto_filename(info: MediaInfo) -> bool:
    """
    仅判断文件名是否属于常见的相机/手机自动命名。

    这不会参与“是否是相机实拍”的真实性判断，也不会提供日期来源；
    只用于决定是否需要 Finder 标签“自定义文件名”。
    """
    if info.kind not in ("photo", "video"):
        return False

    stem = info.path.stem

    if APPLE_IMG_RE.fullmatch(stem) or APPLE_EDITED_IMG_RE.fullmatch(stem):
        return True

    filename_timestamp = parse_android_filename_timestamp(info.path)
    prefix = android_filename_prefix(info.path)
    if filename_timestamp is not None and prefix is not None:
        expected = ("IMG", "MVIMG", "PANO") if info.kind == "photo" else ("VID", "MVIMG")
        if prefix in expected:
            return True

    return any(pattern.fullmatch(stem) for pattern in KNOWN_CAMERA_FILENAME_RES)


def should_add_custom_filename_tag(info: MediaInfo) -> bool:
    """
    已被纳入媒体组的照片/视频，如果文件名不是已知相机自动命名，
    就加 Finder 标签“自定义文件名”。

    是否“纳入媒体组”由上层可信 EXIF / ContentIdentifier / 保守配对规则决定；
    这里绝不会因为文件名本身把一个原本不可信的媒体加入整理。
    """
    return (
        info.kind in ("photo", "video")
        and not is_known_camera_auto_filename(info)
    )


def _run_xattr(args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """调用 macOS 自带 xattr；兼容未暴露 Python xattr API 的解释器。"""
    try:
        return subprocess.run(
            [XATTR_TOOL, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"找不到 macOS xattr 工具：{XATTR_TOOL}") from exc


def _xattr_error_text(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stderr.decode("utf-8", errors="replace").strip() or f"退出码 {result.returncode}"


def _list_xattr_names(path: Path, *, refresh: bool = False) -> Set[str]:
    """每个路径在一次 run 中最多执行一次 `xattr file`。"""
    cache_key = str(path.resolve(strict=False))
    if not refresh and cache_key in _XATTR_NAME_CACHE:
        return set(_XATTR_NAME_CACHE[cache_key])
    result = _run_xattr([str(path)])
    if result.returncode != 0:
        raise OSError(f"无法列出扩展属性：{path}：{_xattr_error_text(result)}")
    names = {line.strip() for line in result.stdout.decode("utf-8", errors="replace").splitlines() if line.strip()}
    _XATTR_NAME_CACHE[cache_key] = set(names)
    return names


def _read_xattr_bytes(path: Path, name: str) -> Optional[bytes]:
    """
    使用 xattr -px 以十六进制读取原始字节，避免 Finder 二进制 plist
    被终端编码或换行破坏。属性不存在时返回 None。
    """
    result = _run_xattr(["-px", name, str(path)])
    if result.returncode == 0:
        try:
            return bytes.fromhex(result.stdout.decode("ascii", errors="strict"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise OSError(f"xattr 十六进制输出无法解析：{path} [{name}]") from exc

    # 不依赖 stderr 的语言/文案；列出属性名来确认是否只是“属性不存在”。
    names = _list_xattr_names(path)
    if name not in names:
        return None
    raise OSError(f"读取扩展属性失败：{path} [{name}]：{_xattr_error_text(result)}")


def _write_xattr_bytes(path: Path, name: str, value: bytes) -> None:
    """使用 xattr -wx 写入原始字节。"""
    result = _run_xattr(["-wx", name, value.hex(), str(path)])
    if result.returncode != 0:
        raise OSError(f"写入扩展属性失败：{path} [{name}]：{_xattr_error_text(result)}")
    cache_key = str(path.resolve(strict=False))
    if cache_key in _XATTR_NAME_CACHE:
        _XATTR_NAME_CACHE[cache_key].add(name)


def _remove_xattr_if_present(path: Path, name: str) -> None:
    names = _list_xattr_names(path)
    if name not in names:
        return
    result = _run_xattr(["-d", name, str(path)])
    if result.returncode != 0:
        raise OSError(f"删除扩展属性失败：{path} [{name}]：{_xattr_error_text(result)}")
    cache_key = str(path.resolve(strict=False))
    if cache_key in _XATTR_NAME_CACHE:
        _XATTR_NAME_CACHE[cache_key].discard(name)


def _finder_tag_base_name(value: str) -> str:
    # Finder 颜色标签常编码为 "标签名\n6"；比较名称时忽略颜色后缀。
    return value.split("\n", 1)[0]


def _read_finder_tag_xattr(path: Path) -> Tuple[bool, Optional[bytes]]:
    raw = _read_xattr_bytes(path, FINDER_TAG_XATTR)
    return raw is not None, raw

def _decode_finder_tags(raw: Optional[bytes]) -> List[str]:
    if raw is None:
        return []

    try:
        value = plistlib.loads(raw)
    except Exception as exc:
        raise ValueError(f"Finder 标签 xattr 不是有效 plist：{exc}") from exc

    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("Finder 标签 xattr 不是字符串数组")

    return list(value)


def prepare_finder_tag_plan_item(
    source: Path,
    target: Path,
    tag_name: str = CUSTOM_FILENAME_FINDER_TAG,
) -> FinderTagPlanItem:
    """
    在移动前读取并验证原 Finder 标签；只生成计划，不修改文件。
    原标签原样保留，只在不存在同名标签时追加无颜色标签。
    """
    existed, raw = _read_finder_tag_xattr(source)
    tags = _decode_finder_tags(raw)
    already_had = any(_finder_tag_base_name(item) == tag_name for item in tags)

    if already_had:
        # 精确保留原始二进制 plist，包括已有颜色编码和顺序。
        desired_raw = raw if raw is not None else plistlib.dumps(tags, fmt=plistlib.FMT_BINARY)
    else:
        updated = list(tags)
        # Finder 的颜色索引 0 表示无颜色；Finder 中显示名称仍是 tag_name。
        updated.append(f"{tag_name}\n0")
        desired_raw = plistlib.dumps(updated, fmt=plistlib.FMT_BINARY)

    return FinderTagPlanItem(
        source=source,
        target=target,
        original_xattr_exists=existed,
        original_xattr_raw=raw,
        desired_xattr_raw=desired_raw,
        already_had_tag=already_had,
    )


def build_custom_filename_tag_plan(
    mappings: Sequence[Tuple[Path, Path]],
    infos: Dict[Path, MediaInfo],
) -> List[FinderTagPlanItem]:
    plan: List[FinderTagPlanItem] = []

    for source, target in mappings:
        # 重复副本隔离目录只负责保存已确认重复的源文件；不在隔离动作中新增 Finder 标签。
        if path_is_within(target, DUPLICATE_ROOT.resolve(strict=False)):
            continue

        info = infos.get(source)
        if info is None or not should_add_custom_filename_tag(info):
            continue

        plan.append(prepare_finder_tag_plan_item(source, target))

    return plan


def _verify_finder_tag(path: Path, tag_name: str = CUSTOM_FILENAME_FINDER_TAG) -> None:
    existed, raw = _read_finder_tag_xattr(path)
    if not existed:
        raise RuntimeError(f"Finder 标签写入后 xattr 不存在：{path}")

    tags = _decode_finder_tags(raw)
    if not any(_finder_tag_base_name(item) == tag_name for item in tags):
        raise RuntimeError(f"Finder 标签写入后验证失败：{path}")


def _restore_finder_tag_state(path: Path, item: FinderTagPlanItem) -> None:
    if item.original_xattr_exists:
        if item.original_xattr_raw is None:
            raise RuntimeError("原 Finder 标签状态异常：标记存在但原始值为空")
        _write_xattr_bytes(path, FINDER_TAG_XATTR, item.original_xattr_raw)
        return

    _remove_xattr_if_present(path, FINDER_TAG_XATTR)


def apply_finder_tag_plan(
    plan: Sequence[FinderTagPlanItem],
) -> Tuple[bool, Optional[str], int]:
    """应用并逐个验证 Finder 标签。失败时先恢复当前路径上的原 xattr。"""
    if not plan:
        return True, None, 0

    added = 0
    try:
        for item in plan:
            if not item.target.exists():
                raise FileNotFoundError(f"Finder 标签目标不存在：{item.target}")

            _write_xattr_bytes(item.target, FINDER_TAG_XATTR, item.desired_xattr_raw)
            _verify_finder_tag(item.target)
            if not item.already_had_tag:
                added += 1

        return True, None, added

    except Exception as exc:
        restore_errors: List[str] = []
        for item in reversed(plan):
            current = item.target if item.target.exists() else item.source
            if not current.exists():
                continue
            try:
                _restore_finder_tag_state(current, item)
            except Exception as restore_exc:
                restore_errors.append(f"{current}: {restore_exc}")

        message = str(exc)
        if restore_errors:
            message += "；Finder 标签恢复失败：" + " | ".join(restore_errors)
        return False, message, 0


def rollback_completed_move(
    moved: Sequence[Tuple[Path, Path]],
) -> Optional[str]:
    errors: List[str] = []

    for original, moved_to in reversed(moved):
        try:
            if moved_to.exists() and not original.exists():
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(moved_to), str(original))
            elif moved_to.exists() and original.exists():
                errors.append(f"原路径与目标同时存在，无法安全回滚：{moved_to} -> {original}")
        except Exception as exc:
            errors.append(f"{moved_to} -> {original}: {exc}")

    return " | ".join(errors) if errors else None


def restore_tag_states_at_sources(
    plan: Sequence[FinderTagPlanItem],
) -> Optional[str]:
    """回滚移动后再次恢复原路径标签，确保事务失败不留下新标签。"""
    errors: List[str] = []
    for item in plan:
        if not item.source.exists():
            continue
        try:
            _restore_finder_tag_state(item.source, item)
        except Exception as exc:
            errors.append(f"{item.source}: {exc}")
    return " | ".join(errors) if errors else None


def metadata_int_for(metadata: dict, names: Sequence[str]) -> Optional[int]:
    for name in names:
        for key, value in metadata.items():
            if plain_tag_name(key) != name:
                continue
            if isinstance(value, int):
                return value if value > 0 else None
            if isinstance(value, str):
                match = re.search(r"\d+", value)
                if match:
                    parsed = int(match.group(0))
                    return parsed if parsed > 0 else None
    return None


def image_dimensions(metadata: dict) -> Optional[Tuple[int, int]]:
    width = metadata_int_for(metadata, ("ImageWidth", "ExifImageWidth"))
    height = metadata_int_for(metadata, ("ImageHeight", "ExifImageHeight"))
    if width and height:
        return width, height
    return None


def device_signature(metadata: dict) -> Optional[str]:
    make = first_device_metadata_value(metadata, ("Make", "DeviceManufacturer"))
    model = first_device_metadata_value(metadata, ("Model", "DeviceModelName"))
    if not make and not model:
        return None
    return "|".join((make or "", model or "")).strip("|").casefold()


def dimensions_compatible(
    a: Optional[Tuple[int, int]],
    b: Optional[Tuple[int, int]],
) -> bool:
    if not a or not b:
        return True

    def ratio(size: Tuple[int, int]) -> float:
        w, h = size
        long_side = max(w, h)
        short_side = min(w, h)
        return long_side / short_side if short_side else 0.0

    ra = ratio(a)
    rb = ratio(b)
    if ra == 0 or rb == 0:
        return True
    return abs(ra - rb) / max(ra, rb) <= 0.03


def parse_android_filename_timestamp(path: Path) -> Optional[Timestamp]:
    match = ANDROID_MEDIA_RE.match(path.stem)
    if not match:
        return None

    _, y, month, day, hour, minute, second = match.groups()

    timestamp: Timestamp = (
        int(y),
        int(month),
        int(day),
        int(hour),
        int(minute),
        int(second),
    )

    yy, mm, dd, hh, mi, ss = timestamp

    if not (1900 <= yy <= 2200):
        return None
    if not (1 <= mm <= 12):
        return None
    if not (1 <= dd <= 31):
        return None
    if not (0 <= hh <= 23):
        return None
    if not (0 <= mi <= 59):
        return None
    if not (0 <= ss <= 59):
        return None

    return timestamp


def timestamp_difference_seconds(
    a: Optional[Timestamp],
    b: Optional[Timestamp],
) -> Optional[int]:
    if a is None or b is None:
        return None

    try:
        da = datetime(*a)
        db = datetime(*b)
    except ValueError:
        return None

    return int(abs((da - db).total_seconds()))


def timestamps_within_tolerance(
    a: Optional[Timestamp],
    b: Optional[Timestamp],
) -> bool:
    difference = timestamp_difference_seconds(a, b)

    return (
        difference is not None
        and difference <= TIME_TOLERANCE_SECONDS
    )


def format_timestamp(timestamp: Optional[Timestamp]) -> str:
    if timestamp is None:
        return "(无)"

    y, month, day, hour, minute, second = timestamp

    return (
        f"{y:04d}-{month:02d}-{day:02d} "
        f"{hour:02d}:{minute:02d}:{second:02d}"
    )


def is_apple_device_metadata(metadata: dict) -> bool:
    values = [
        first_device_metadata_value(metadata, ("Make",)),
        first_device_metadata_value(metadata, ("Model",)),
        first_device_metadata_value(metadata, ("DeviceManufacturer",)),
        first_device_metadata_value(metadata, ("DeviceModelName",)),
    ]

    joined = " ".join(
        value
        for value in values
        if value
    ).casefold()

    return (
        "apple" in joined
        or "iphone" in joined
        or "ipad" in joined
    )


def has_device_identity(metadata: dict) -> bool:
    fields = (
        "Make",
        "Model",
        "LensMake",
        "LensModel",
        "DeviceManufacturer",
        "DeviceModelName",
    )

    return any(
        first_device_metadata_value(metadata, (field,))
        for field in fields
    )


def is_likely_screenshot(
    path: Path,
    source_root: Path,
) -> bool:
    if SCREENSHOT_NAME_RE.search(path.stem):
        return True

    screenshot_dirs = {
        name.casefold()
        for name in SCREENSHOT_DIR_NAMES
    }

    try:
        relative = path.relative_to(source_root)
        parents = relative.parents
    except ValueError:
        parents = path.parents

    for parent in parents:
        if parent.name.strip().casefold() in screenshot_dirs:
            return True

    return False



def _read_optional_xattr(path: Path, name: str) -> Optional[bytes]:
    # 来源检测与 Finder 标签共用同一套 /usr/bin/xattr 兼容层。
    return _read_xattr_bytes(path, name)

def read_source_xattrs(path: Path) -> Tuple[List[str], Optional[str], Optional[str]]:
    """返回 (WhereFroms 列表, quarantine 文本, 读取错误)；普通文件只列一次 xattr。"""
    where_froms: List[str] = []
    quarantine: Optional[str] = None
    errors: List[str] = []
    try:
        names = _list_xattr_names(path)
    except OSError as exc:
        return where_froms, quarantine, f"xattr 列表读取失败：{exc}"
    if WHERE_FROMS_XATTR in names:
        try:
            raw = _read_optional_xattr(path, WHERE_FROMS_XATTR)
            if raw:
                try:
                    value = plistlib.loads(raw)
                    if isinstance(value, (list, tuple)):
                        where_froms = [str(item).strip() for item in value if str(item).strip()]
                    elif isinstance(value, str) and value.strip(): where_froms = [value.strip()]
                except Exception:
                    decoded = raw.decode("utf-8", errors="ignore").strip()
                    if decoded: where_froms = [decoded]
        except OSError as exc: errors.append(f"WhereFroms xattr 读取失败：{exc}")
    if QUARANTINE_XATTR in names:
        try:
            raw = _read_optional_xattr(path, QUARANTINE_XATTR)
            if raw: quarantine = raw.decode("utf-8", errors="replace").strip() or None
        except OSError as exc: errors.append(f"quarantine xattr 读取失败：{exc}")
    return where_froms, quarantine, "；".join(errors) if errors else None


def metadata_text_for_source_detection(metadata: dict) -> str:
    fields = {
        "Software", "CreatorTool", "Encoder", "HandlerDescription",
        "Comment", "UserComment", "ImageDescription", "Description", "Title",
        "SourceURL", "URL", "OriginatingProgram",
    }
    values: List[str] = []
    for key, value in metadata.items():
        if plain_tag_name(key) not in fields:
            continue
        if isinstance(value, (str, int, float)):
            values.append(str(value))
        elif isinstance(value, (list, tuple)):
            values.extend(str(item) for item in value)
    return "\n".join(values)


def source_directory_evidence(path: Path, source_root: Path) -> List[str]:
    try:
        relative = path.relative_to(source_root)
        parents = relative.parents
    except ValueError:
        parents = path.parents

    matches: List[str] = []
    for parent in parents:
        name = parent.name.strip().casefold()
        if name in RISKY_SOURCE_DIR_NAMES:
            matches.append(parent.name)
    return matches


def metadata_source_urls(metadata: dict) -> List[str]:
    # 只把明确的 SourceURL 当来源证据。通用 URL 字段可能只是版权/作者网页，
    # 不能据此把真实相机原片误判为网络下载。
    urls: List[str] = []
    for value in metadata_values_for(metadata, "SourceURL"):
        candidate = str(value).strip()
        if candidate:
            urls.append(candidate)
    return urls


def classify_source_evidence(info: MediaInfo, source_root: Path) -> Tuple[str, str, List[str]]:
    """
    来源证据三态：
      CLEAN      无明确来源负面证据，可继续相机来源判定。
      AMBIGUOUS  中等负面证据，自动整理暂停，保持原位。
      EXCLUDED   明确截图/屏幕录制/网络下载等强负面证据，保持原位。

    注意：这里只决定“是否允许自动整理”，不删除、不移动文件。
    """
    path = info.path
    evidence: List[str] = []

    # 1) 明确截图 / 屏幕录制名称或目录。
    if info.kind == "photo" and is_likely_screenshot(path, source_root):
        return SOURCE_STATE_EXCLUDED, "明确截图文件名/目录", ["截图名称或截图目录"]

    if info.kind == "video" and SCREEN_RECORDING_NAME_RE.search(path.stem):
        return SOURCE_STATE_EXCLUDED, "明确屏幕录制文件名", [f"文件名：{path.name}"]

    # 2) metadata 明确写出截图/屏幕录制。
    metadata_text = metadata_text_for_source_detection(info.metadata)
    if metadata_text and SCREEN_CAPTURE_METADATA_RE.search(metadata_text):
        return SOURCE_STATE_EXCLUDED, "metadata 明确表明截图/屏幕录制", ["metadata: screen capture/recording"]

    # 3) macOS 下载来源 xattr。
    where_froms, quarantine, xattr_error = read_source_xattrs(path)
    web_where_froms = [
        value for value in where_froms
        if value.casefold().startswith(("http://", "https://"))
    ]
    if web_where_froms:
        shown = web_where_froms[0]
        if len(shown) > 180:
            shown = shown[:177] + "..."
        return SOURCE_STATE_EXCLUDED, "WhereFroms 表明来自网络下载", [f"WhereFroms={shown}"]

    # Exif/XMP 明确 SourceURL 也视作网络来源；普通版权网页字段不会因为 URL 字样自动命中。
    metadata_urls = [
        value for value in metadata_source_urls(info.metadata)
        if value.casefold().startswith(("http://", "https://"))
    ]
    if metadata_urls:
        shown = metadata_urls[0]
        if len(shown) > 180:
            shown = shown[:177] + "..."
        return SOURCE_STATE_EXCLUDED, "metadata SourceURL 表明来自网络来源", [f"SourceURL={shown}"]

    risky_dirs = source_directory_evidence(path, source_root)

    quarantine_agent = ""
    if quarantine:
        # 常见格式 flags;timestamp;agent;uuid，第三段通常是来源 agent。
        parts = quarantine.split(";")
        if len(parts) >= 3:
            quarantine_agent = parts[2].strip()
        if quarantine_agent and STRONG_DOWNLOAD_AGENT_RE.search(quarantine_agent):
            return (
                SOURCE_STATE_EXCLUDED,
                "quarantine 表明来自浏览器/聊天应用",
                [f"quarantine agent={quarantine_agent}"],
            )

    # quarantine 本身不是下载证据。尤其 Apple Photos 导出/保存的媒体可能带
    # agent=Photos；这必须视为中性，不能因此阻止相机原片整理。
    # 未知 agent 也只作诊断，不单独造成 AMBIGUOUS。
    if risky_dirs:
        evidence.append(f"可疑来源目录={risky_dirs[0]}")
    if xattr_error:
        evidence.append(xattr_error)

    if evidence:
        return SOURCE_STATE_AMBIGUOUS, "存在中等来源负面证据，需人工确认", evidence

    if quarantine_agent.casefold() in {"photos", "photos.app", "com.apple.photos"}:
        return SOURCE_STATE_CLEAN, "Apple Photos quarantine 为中性来源证据", [f"quarantine agent={quarantine_agent}"]

    if quarantine:
        suffix = f" agent={quarantine_agent}" if quarantine_agent else ""
        return SOURCE_STATE_CLEAN, "存在 quarantine，但无浏览器/聊天/网络来源证据", ["quarantine（中性）" + suffix]

    return SOURCE_STATE_CLEAN, "无来源负面证据", []

def has_trusted_exif(info: MediaInfo) -> bool:
    """
    可信照片 EXIF：
    - 必须是照片；
    - 必须有设备/相机身份信息（Make / Model / Lens / Device 字段之一）；
    - 必须有可靠内嵌拍摄时间；
    - 时间必须来自 metadata，而不是文件名。

    满足后，EXIF 时间优先于 IMG_YYYYMMDD_HHMMSS 文件名时间。
    即使文件名时间相差超过 TIME_TOLERANCE_SECONDS，也不否决整理。
    """
    if info.kind != "photo":
        return False

    # PNG 不是 iPhone/手机相机的常规原始拍摄格式，而且截图、编辑导出或转换图
    # 可能继承设备身份与 DateTimeOriginal。为避免这类文件仅凭继承 metadata 被误收，
    # PNG 不参与通用 trusted EXIF 自动放行。明确截图仍由来源负面证据规则优先说明。
    if info.path.suffix.lower() == ".png":
        return False

    if not has_device_identity(info.metadata):
        return False

    if info.timestamp is None or info.date_source is None:
        return False

    return info.date_source.startswith("metadata:")


TRUSTED_STANDALONE_VIDEO_DATE_SOURCES = {
    "metadata:DateTimeOriginal",
    "metadata:CreateDate",
    "metadata:MediaCreateDate",
    "metadata:DateCreated",
}


def has_trusted_video_metadata(info: MediaInfo) -> bool:
    """
    可信的独立视频相机元数据。

    用于解决用户把相机视频改成自定义文字文件名后的归类问题。
    文件名本身不提供任何相机来源证据，必须同时满足：
    - 必须是视频；
    - metadata 中仍有设备/相机身份（Make / Model / Lens / Device 字段之一）；
    - 必须有可靠的内嵌时间；
    - 时间来源必须是较强的拍摄/媒体创建字段。

    TrackCreateDate 很容易在转码、封装或导出时被重置，因此它可以参与
    Live/Motion Photo / sidecar 的交叉验证，但不能单独让一个自定义文件名视频
    获得“相机实拍”资格。
    """
    if info.kind != "video":
        return False

    if not has_device_identity(info.metadata):
        return False

    if info.timestamp is None or info.date_source is None:
        return False

    return info.date_source in TRUSTED_STANDALONE_VIDEO_DATE_SOURCES


def has_filename_verified_datetimeoriginal(info: MediaInfo) -> bool:
    """
    针对设备字段已丢失、但仍保留 DateTimeOriginal 的照片：

    - 必须是照片；
    - 文件名必须能解析出 IMG/MVIMG/PANO_YYYYMMDD_HHMMSS... 完整时间；
    - 选中的内嵌时间必须明确来自 DateTimeOriginal；
    - 文件名时间与 DateTimeOriginal 相差 <= TIME_TOLERANCE_SECONDS。

    满足后，即使 Make / Model 已丢失，也允许按 DateTimeOriginal 归类。
    文件名只用于验证，绝不作为归类日期来源。
    """
    if info.kind != "photo":
        return False

    prefix = android_filename_prefix(info.path)
    if prefix not in ("IMG", "MVIMG", "PANO"):
        return False

    if info.filename_timestamp is None:
        return False

    if info.timestamp is None or info.date_source != "metadata:DateTimeOriginal":
        return False

    return timestamps_within_tolerance(
        info.filename_timestamp,
        info.timestamp,
    )


def determine_camera_origin(info: MediaInfo) -> Tuple[bool, str]:
    """
    相机来源确认规则：

    1. 照片存在可信 EXIF：
       设备/相机信息 + 可靠内嵌拍摄时间
       -> 直接信任 EXIF，按 EXIF 时间整理。
       -> 文件名时间差只作为诊断，不再否决。

    2. Apple IMG_####：
       Apple/iPhone/iPad 设备元数据 + 常见相机格式 + 内嵌时间。

    3. Android IMG_/VID_/MVIMG_/PANO_：
       若没有上面的可信照片 EXIF，则继续使用保守核对规则。
       文件名时间绝不作为实际归类日期来源。
    """
    path = info.path
    ext = path.suffix.lower()

    # PNG/GIF 可以作为第二阶段媒体组主媒体，但默认不作为可自动确认的相机原片。
    # 即使它们继承设备字段、DateTimeOriginal 或 Android 风格文件名，也不能仅凭
    # 这些继承 metadata 进入正式 Gallery。明确截图仍由来源强负面证据优先处理。
    if info.kind == "photo" and ext in NON_CAMERA_AUTO_ACCEPT_PHOTO_EXTENSIONS:
        label = ext.lstrip(".").upper()
        return False, f"{label} 不自动认定为手机相机原片；避免截图/导出/转换图继承相机 metadata 后误收"

    if info.trusted_exif:
        return (
            True,
            "可信 EXIF：设备/相机信息 + 内嵌拍摄时间；按 EXIF 时间整理",
        )

    # 与可信照片 EXIF 对称：自定义/非标准文件名的视频若仍保留
    # 设备/相机身份 + 强内嵌拍摄/媒体创建时间，也允许独立归类。
    # 但标准 VID/MVIMG 必须优先通过文件名当地时间与全部 metadata 候选的验证，
    # 避免把 QuickTime UTC 时间直接当成本地时间。
    standard_android_video = (
        info.kind == "video"
        and info.filename_timestamp is not None
        and android_filename_prefix(info.path) in ("VID", "MVIMG")
    )
    if has_trusted_video_metadata(info) and not standard_android_video:
        return (
            True,
            "可信视频 metadata：设备/相机信息 + 强内嵌拍摄/创建时间；"
            "文件名无需符合 VID/IMG/MVIMG 格式",
        )

    # 设备字段可能在编辑/重编码时丢失，但 DateTimeOriginal 仍保留。
    # 若 IMG 文件名完整时间与 DateTimeOriginal 在 20 秒内一致，
    # 则允许按 DateTimeOriginal 整理；文件名只做验证。
    if has_filename_verified_datetimeoriginal(info):
        difference = timestamp_difference_seconds(
            info.filename_timestamp,
            info.timestamp,
        )
        return (
            True,
            "DateTimeOriginal + IMG/MVIMG/PANO 文件名时间验证通过："
            f"相差 {difference} 秒；按 DateTimeOriginal 整理",
        )

    if APPLE_IMG_RE.fullmatch(path.stem):
        if (
            info.kind == "photo"
            and ext not in APPLE_CAMERA_PHOTO_EXTENSIONS
        ):
            return False, "IMG_#### 但不是允许的 iPhone 相机照片格式"

        if (
            info.kind == "video"
            and ext not in APPLE_CAMERA_VIDEO_EXTENSIONS
        ):
            return False, "IMG_#### 但不是允许的 iPhone 相机视频格式"

        if not is_apple_device_metadata(info.metadata):
            return False, "IMG_#### 但缺少 Apple/iPhone/iPad 设备元数据"

        if info.timestamp is None:
            return False, "Apple 媒体缺少可靠内嵌拍摄/创建时间"

        return True, "Apple IMG_#### + Apple device metadata"

    if info.filename_timestamp is not None:
        prefix = android_filename_prefix(path)
        expected_prefixes = ("IMG", "MVIMG", "PANO") if info.kind == "photo" else ("VID", "MVIMG")

        if prefix not in expected_prefixes:
            expected_text = "/".join(expected_prefixes)
            return False, f"文件名不是预期的 {expected_text}_... 相机格式"

        if info.kind == "video" and prefix in ("VID", "MVIMG"):
            if info.android_video_time_verified:
                return (
                    True,
                    "标准 Android VID/MVIMG 时间验证通过："
                    + info.android_video_time_reason
                    + "；仅在内存中解释 UTC/当地时间，不修改媒体 metadata",
                )
            return (
                False,
                "标准 Android VID/MVIMG 时间验证失败："
                + (info.android_video_time_reason or "没有可用验证结果"),
            )

        if info.timestamp is None:
            return False, "安卓相机格式文件名存在，但没有可靠内嵌完整时间"

        difference = timestamp_difference_seconds(
            info.filename_timestamp,
            info.timestamp,
        )

        if not timestamps_within_tolerance(
            info.filename_timestamp,
            info.timestamp,
        ):
            return (
                False,
                f"安卓文件名完整时间与内嵌元数据时间相差 "
                f"{difference} 秒，超过允许的 {TIME_TOLERANCE_SECONDS} 秒；"
                "且未满足更强的可信 metadata 规则",
            )

        if has_device_identity(info.metadata):
            return (
                True,
                f"Android filename + device metadata + "
                f"timestamp difference <= {TIME_TOLERANCE_SECONDS}s",
            )

        return False, "安卓相机格式文件名存在，但缺少设备/相机元数据，且没有达到无设备字段 fallback 条件"

    if info.kind == "video" and info.timestamp is not None:
        if not has_device_identity(info.metadata):
            return False, "自定义/非标准视频有内嵌时间，但缺少设备/相机身份 metadata"
        if info.date_source == "metadata:TrackCreateDate":
            return False, "自定义/非标准视频仅有 TrackCreateDate；该字段可能被转码/封装重置，不能单独确认相机实拍"
        return False, f"自定义/非标准视频的时间来源 {info.date_source} 未达到独立归类的可信门槛"

    return False, "无法强确认是手机相机实际拍摄媒体"

def build_media_info(
    paths: Sequence[Path],
    metadata_map: Dict[str, dict],
    source_root: Path,
    *,
    show_progress: bool = False,
) -> Dict[Path, MediaInfo]:
    infos: Dict[Path, MediaInfo] = {}
    total = len(paths)

    for item_index, path in enumerate(paths, start=1):
        kind = classify(path)
        metadata = metadata_map.get(str(path), {})

        info = MediaInfo(
            path=path,
            kind=kind,
            metadata=metadata,
        )

        if kind in ("photo", "video", "sidecar"):
            info.content_id = choose_content_identifier(metadata)

            if kind == "photo":
                fields = PHOTO_DATE_FIELDS
            elif kind == "video":
                fields = VIDEO_DATE_FIELDS
            else:
                # XMP/THM/LRV 等 sidecar 可能保留拍摄时间；仅用于归属判断。
                fields = tuple(dict.fromkeys(PHOTO_DATE_FIELDS + VIDEO_DATE_FIELDS))

            info.timestamp, info.date_source = choose_embedded_timestamp(
                metadata,
                fields,
            )

        if kind in ("photo", "video"):
            info.filename_timestamp = parse_android_filename_timestamp(path)

            if (
                kind == "video"
                and info.filename_timestamp is not None
                and android_filename_prefix(path) in ("VID", "MVIMG")
            ):
                (
                    info.android_video_time_verified,
                    verified_local_time,
                    verified_metadata_source,
                    info.android_video_time_reason,
                ) = verify_android_video_time_against_filename(
                    metadata,
                    info.filename_timestamp,
                )
                if info.android_video_time_verified and verified_local_time is not None:
                    # 标准 Android 文件名中的当地时间只有在强 metadata 验证成功后
                    # 才成为本脚本的整理时间。这里仅改变内存中的解释，不写媒体文件。
                    info.timestamp = verified_local_time
                    info.date_source = (
                        "verified:AndroidFilenameLocalTime<-"
                        + (verified_metadata_source or "metadata")
                    )

            if (
                info.filename_timestamp is not None
                and info.timestamp is not None
                and not timestamps_within_tolerance(
                    info.filename_timestamp,
                    info.timestamp,
                )
            ):
                info.filename_timestamp_conflict = True

            # 可信 EXIF 优先；文件名时间冲突只保留作诊断。
            info.trusted_exif = has_trusted_exif(info)

            (
                info.camera_origin,
                info.camera_origin_reason,
            ) = determine_camera_origin(info)

            (
                info.source_state,
                info.source_reason,
                info.source_evidence,
            ) = classify_source_evidence(info, source_root)

        infos[path] = info

        if show_progress and (item_index == total or item_index == 1 or item_index % SOURCE_PROGRESS_INTERVAL == 0):
            print_stage_progress("来源/媒体分析", item_index, total, path.name)

    return infos


def custom_named_pair_is_trustworthy(
    still_info: MediaInfo,
    video_info: MediaInfo,
) -> Tuple[bool, str]:
    """
    非标准/用户自定义文件名的静态图 + 视频 fallback。

    文件名本身不提供“相机来源”证据，只允许用来寻找同 stem 候选。
    真正确认必须同时满足：
    - 静态图有可信 EXIF（设备/相机身份 + 可靠 metadata 拍摄时间）；
    - 视频也有可靠 metadata 时间；
    - 两边时间相差 <= TIME_TOLERANCE_SECONDS；
    - 若双方都保留 ContentIdentifier/MediaGroupUUID，则不得互相冲突；
    - 若双方都保留设备签名，则不得互相冲突。

    这样像 `海边.heic + 海边.mov` 可以安全配对，但仅仅“同名”绝不够。
    """
    if still_info.kind != "photo" or video_info.kind != "video":
        return False, "不是照片 + 视频"

    if not still_info.trusted_exif:
        return False, "自定义文件名静态图缺少可信 EXIF"

    if still_info.timestamp is None or video_info.timestamp is None:
        return False, "自定义文件名配对缺少双方可靠内嵌时间"

    if not timestamps_within_tolerance(still_info.timestamp, video_info.timestamp):
        difference = timestamp_difference_seconds(
            still_info.timestamp,
            video_info.timestamp,
        )
        return False, f"自定义文件名配对时间相差 {difference} 秒"

    still_id = normalize_identifier(still_info.content_id)
    video_id = normalize_identifier(video_info.content_id)
    if still_id and video_id and still_id != video_id:
        return False, "ContentIdentifier/MediaGroupUUID 冲突"

    still_sig = device_signature(still_info.metadata)
    video_sig = device_signature(video_info.metadata)
    if still_sig and video_sig and still_sig != video_sig:
        return False, "设备 metadata 冲突"

    difference = timestamp_difference_seconds(
        still_info.timestamp,
        video_info.timestamp,
    )
    return (
        True,
        "自定义文件名同 stem + 可信静态图 EXIF + "
        f"双方内嵌时间一致（相差 {difference} 秒）",
    )


def pair_live_photos(
    infos: Dict[Path, MediaInfo],
) -> Tuple[Dict[Path, Path], Dict[Path, Path]]:
    """
    优先 ContentIdentifier / MediaGroupUUID（文件名可完全不同）；
    无法读取时，再用同目录 + 同 basename 保守配对。

    basename fallback 分两类：
    - 0113/1039 原有 Apple IMG_#### / Android MVIMG 标准命名；
    - 非标准/用户自定义文件名：必须通过 custom_named_pair_is_trustworthy()
      的强 metadata 条件，不能仅凭同 stem 配对。
    """
    photos = [
        info
        for info in infos.values()
        if info.kind == "photo"
    ]

    videos = [
        info
        for info in infos.values()
        if info.kind == "video"
    ]

    still_to_video: Dict[Path, Path] = {}
    video_to_still: Dict[Path, Path] = {}

    photos_by_id: Dict[str, List[Path]] = defaultdict(list)
    videos_by_id: Dict[str, List[Path]] = defaultdict(list)

    for info in photos:
        if info.content_id:
            photos_by_id[info.content_id].append(info.path)

    for info in videos:
        if info.content_id:
            videos_by_id[info.content_id].append(info.path)

    # 强配对必须唯一。
    for content_id in set(photos_by_id) & set(videos_by_id):
        photo_paths = photos_by_id[content_id]
        video_paths = videos_by_id[content_id]

        if len(photo_paths) == 1 and len(video_paths) == 1:
            still = photo_paths[0]
            video = video_paths[0]

            still_to_video[still] = video
            video_to_still[video] = still

    # basename fallback。
    video_lookup: Dict[Tuple[Path, str], List[Path]] = defaultdict(list)

    for info in videos:
        if info.path in video_to_still:
            continue

        key = (
            info.path.parent,
            info.path.stem.casefold(),
        )

        video_lookup[key].append(info.path)

    for info in photos:
        still = info.path

        if still in still_to_video:
            continue

        key = (
            still.parent,
            still.stem.casefold(),
        )

        candidates = video_lookup.get(key, [])
        movs = [
            video
            for video in candidates
            if video.suffix.lower() == ".mov"
        ]

        chosen: Optional[Path] = None

        if len(movs) == 1:
            chosen = movs[0]
        elif len(candidates) == 1:
            chosen = candidates[0]

        if chosen is not None and chosen not in video_to_still:
            still_info = infos[still]
            video_info = infos[chosen]
            stem = still.stem
            prefix = android_filename_prefix(still)

            standard_family_name = (
                APPLE_IMG_RE.fullmatch(stem) is not None
                or prefix == "MVIMG"
            )

            if standard_family_name:
                # 标准命名仍保留 0113/1039 的 fallback；若两边都有内嵌时间且明显不同，不配对。
                if (
                    still_info.timestamp is not None
                    and video_info.timestamp is not None
                    and not timestamps_within_tolerance(
                        still_info.timestamp,
                        video_info.timestamp,
                    )
                ):
                    continue
            else:
                custom_ok, _custom_reason = custom_named_pair_is_trustworthy(
                    still_info,
                    video_info,
                )
                if not custom_ok:
                    continue

            still_to_video[still] = chosen
            video_to_still[chosen] = still

    return still_to_video, video_to_still


def _read_bmp_grayscale(path: Path) -> Optional[List[List[int]]]:
    """读取 sips 生成的 24/32-bit、BI_RGB BMP。仅供小缩略图 dHash。"""
    try:
        data = path.read_bytes()
    except OSError:
        return None

    if len(data) < 54 or data[:2] != b"BM":
        return None

    try:
        pixel_offset = struct.unpack_from("<I", data, 10)[0]
        dib_size = struct.unpack_from("<I", data, 14)[0]
        width = struct.unpack_from("<i", data, 18)[0]
        height_raw = struct.unpack_from("<i", data, 22)[0]
        planes = struct.unpack_from("<H", data, 26)[0]
        bpp = struct.unpack_from("<H", data, 28)[0]
        compression = struct.unpack_from("<I", data, 30)[0]
    except struct.error:
        return None

    if dib_size < 40 or planes != 1 or compression != 0 or bpp not in (24, 32):
        return None
    if width <= 0 or height_raw == 0:
        return None

    height = abs(height_raw)
    bytes_per_pixel = bpp // 8
    row_stride = ((width * bpp + 31) // 32) * 4
    required = pixel_offset + row_stride * height
    if required > len(data):
        return None

    rows: List[List[int]] = []
    for display_y in range(height):
        storage_y = (height - 1 - display_y) if height_raw > 0 else display_y
        row_start = pixel_offset + storage_y * row_stride
        row: List[int] = []
        for x in range(width):
            offset = row_start + x * bytes_per_pixel
            b, g, r = data[offset:offset + 3]
            gray = (299 * r + 587 * g + 114 * b) // 1000
            row.append(gray)
        rows.append(row)

    return rows


VisualFingerprint = Tuple[int, int]


def visual_fingerprint(
    path: Path,
    cache: Dict[Path, Optional[VisualFingerprint]],
) -> Optional[VisualFingerprint]:
    """
    使用 macOS sips 将图片归一化成 9x8 BMP，计算 64-bit dHash + 平均亮度。
    平均亮度用于避免纯色/低纹理图片出现相同 dHash 的假阳性。
    失败时返回 None；图像相似度只是辅助证据。
    """
    if path in cache:
        return cache[path]

    sips = Path(SIPS_TOOL)
    if not sips.exists():
        cache[path] = None
        return None

    try:
        with tempfile.TemporaryDirectory(prefix="gallery-organizer-sim-") as temp_dir:
            output = Path(temp_dir) / "thumb.bmp"
            result = subprocess.run(
                [
                    str(sips),
                    "-z", "8", "9",
                    "-s", "format", "bmp",
                    str(path),
                    "--out", str(output),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            if result.returncode != 0 or not output.exists():
                cache[path] = None
                return None

            rows = _read_bmp_grayscale(output)
            if rows is None or len(rows) != 8 or any(len(row) != 9 for row in rows):
                cache[path] = None
                return None

            value = 0
            pixels: List[int] = []
            for row in rows:
                pixels.extend(row)
                for x in range(8):
                    value <<= 1
                    if row[x] > row[x + 1]:
                        value |= 1

            mean_luma = int(round(sum(pixels) / len(pixels)))
            fingerprint = (value, mean_luma)
            cache[path] = fingerprint
            return fingerprint
    except (OSError, subprocess.SubprocessError):
        cache[path] = None
        return None


def visual_similarity(
    a: Path,
    b: Path,
    cache: Dict[Path, Optional[VisualFingerprint]],
) -> Optional[float]:
    fa = visual_fingerprint(a, cache)
    fb = visual_fingerprint(b, cache)
    if fa is None or fb is None:
        return None

    hash_a, luma_a = fa
    hash_b, luma_b = fb
    hash_similarity = 1.0 - (((hash_a ^ hash_b).bit_count()) / 64.0)
    luma_similarity = 1.0 - (abs(luma_a - luma_b) / 255.0)

    # dHash 是主体，亮度只作为假阳性保护。
    return min(hash_similarity, 0.85 * hash_similarity + 0.15 * luma_similarity)


def evaluate_photo_variant_relation(
    a: MediaInfo,
    b: MediaInfo,
    visual_cache: Dict[Path, Optional[VisualFingerprint]],
) -> Tuple[str, str]:
    """
    返回 confirmed / collision / ambiguous。

    重要：同 stem 只用于寻找候选，绝不是关联证据。
    图像相似度也只是辅助证据，不能单独触发删除、覆盖或改名。
    """
    if a.kind != "photo" or b.kind != "photo":
        return "ambiguous", "不是照片对"
    if a.path.parent != b.path.parent or a.path.stem.casefold() != b.path.stem.casefold():
        return "ambiguous", "目录或 stem 不同"
    if a.path.suffix.lower() == b.path.suffix.lower():
        return "collision", "相同扩展名，不视为多格式导出"
    if (
        a.path.suffix.lower() not in APPLE_VARIANT_PHOTO_EXTENSIONS
        or b.path.suffix.lower() not in APPLE_VARIANT_PHOTO_EXTENSIONS
    ):
        return "ambiguous", "不属于 Apple 多格式候选扩展名"

    stem_is_apple = APPLE_IMG_RE.fullmatch(a.path.stem) is not None
    apple_evidence = stem_is_apple or is_apple_device_metadata(a.metadata) or is_apple_device_metadata(b.metadata)
    if not apple_evidence:
        return "ambiguous", "没有 Apple 文件名/设备证据"

    diff = timestamp_difference_seconds(a.timestamp, b.timestamp)
    sig_a = device_signature(a.metadata)
    sig_b = device_signature(b.metadata)
    device_conflict = bool(sig_a and sig_b and sig_a != sig_b)
    dims_ok = dimensions_compatible(image_dimensions(a.metadata), image_dimensions(b.metadata))

    # 两边都明确是 DateTimeOriginal 且差得很远：无需解码图像，直接判定编号撞号。
    both_original_time = (
        a.date_source == "metadata:DateTimeOriginal"
        and b.date_source == "metadata:DateTimeOriginal"
    )
    if both_original_time and diff is not None and diff > TIME_TOLERANCE_SECONDS:
        return "collision", f"两个 DateTimeOriginal 相差 {diff} 秒"

    similarity = visual_similarity(a.path, b.path, visual_cache)
    sim_text = "不可用" if similarity is None else f"{similarity:.3f}"

    if similarity is not None and similarity < VISUAL_DIFFERENT_THRESHOLD:
        return "collision", f"图像明显不同（dHash 相似度 {similarity:.3f}）"

    # 强视觉一致：允许补足某次导出造成的设备/日期字段缺失或 CreateDate 变化。
    if similarity is not None and similarity >= VISUAL_SIMILARITY_THRESHOLD:
        if not device_conflict:
            if diff is None or diff <= TIME_TOLERANCE_SECONDS:
                return "confirmed", f"图像高度相似 {similarity:.3f}，时间无冲突"
            if not both_original_time and similarity >= 0.96:
                return "confirmed", f"图像高度相似 {similarity:.3f}；至少一边不是 DateTimeOriginal，允许导出时间变化"

    # 图像相似度不可用时，才退回很强的元数据组合；
    # 若图像已经成功比较但不到确认阈值，不让元数据覆盖这个视觉疑点。
    if similarity is None and diff is not None and diff <= 2 and not device_conflict and dims_ok:
        if both_original_time or (sig_a and sig_b and sig_a == sig_b):
            return "confirmed", f"拍摄时间相差 {diff} 秒，设备/尺寸证据一致；图像相似度 {sim_text}"

    if device_conflict and (diff is None or diff > 2):
        return "collision", f"设备信息冲突；图像相似度 {sim_text}"

    return "ambiguous", f"证据不足；时间差 {diff} 秒；图像相似度 {sim_text}"


def build_photo_variant_families(
    infos: Dict[Path, MediaInfo],
) -> Tuple[Dict[Path, Set[Path]], List[Problem], int, int]:
    """确认 Apple 同 stem 多格式照片关系；不确定关系绝不强绑。"""
    photos_by_key: Dict[Tuple[Path, str], List[Path]] = defaultdict(list)
    for info in infos.values():
        if info.kind == "photo":
            photos_by_key[(info.path.parent, info.path.stem.casefold())].append(info.path)

    parent: Dict[Path, Path] = {}
    for paths in photos_by_key.values():
        for path in paths:
            parent[path] = path

    def find(x: Path) -> Path:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: Path, b: Path) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    visual_cache: Dict[Path, Optional[VisualFingerprint]] = {}
    problems: List[Problem] = []
    confirmed_pairs = 0
    ambiguous_pairs = 0

    for (_, stem), paths in photos_by_key.items():
        if len(paths) < 2:
            continue
        ordered = sorted(paths, key=lambda p: p.name.casefold())
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                a, b = ordered[i], ordered[j]
                if a.suffix.lower() == b.suffix.lower():
                    continue
                # 只有双方都属于 Apple 多格式照片候选扩展名时才进入关系判断。
                # PNG/WEBP/GIF 等同 stem 文件不应产生“Apple 多格式关系不确定”假警告。
                if (
                    a.suffix.lower() not in APPLE_VARIANT_PHOTO_EXTENSIONS
                    or b.suffix.lower() not in APPLE_VARIANT_PHOTO_EXTENSIONS
                ):
                    continue
                relation, reason = evaluate_photo_variant_relation(
                    infos[a], infos[b], visual_cache
                )
                if relation == "confirmed":
                    union(a, b)
                    confirmed_pairs += 1
                elif relation == "ambiguous":
                    ambiguous_pairs += 1
                    problems.append(
                        Problem(
                            str(a),
                            f"同 stem 多格式照片关系不确定：{a.name} / {b.name}；{reason}",
                            "warning",
                        )
                    )

    components: Dict[Path, Set[Path]] = defaultdict(set)
    for path in parent:
        components[find(path)].add(path)

    family_map: Dict[Path, Set[Path]] = {}
    for members in components.values():
        if len(members) < 2:
            continue
        frozen = set(members)
        for member in members:
            family_map[member] = frozen

    return family_map, problems, confirmed_pairs, ambiguous_pairs


def choose_family_primary(
    paths: Sequence[Path],
    infos: Dict[Path, MediaInfo],
) -> Path:
    format_rank = {
        ".dng": 0,
        ".heic": 1,
        ".heif": 1,
        ".hif": 1,
        ".jpg": 2,
        ".jpeg": 2,
    }

    def rank(path: Path) -> Tuple[int, int, str]:
        info = infos[path]
        if info.trusted_exif and info.date_source == "metadata:DateTimeOriginal":
            evidence = 0
        elif info.date_source == "metadata:DateTimeOriginal" and info.camera_origin:
            evidence = 1
        elif info.trusted_exif:
            evidence = 2
        elif info.camera_origin and info.timestamp is not None:
            evidence = 3
        elif info.timestamp is not None:
            evidence = 4
        else:
            evidence = 5
        return evidence, format_rank.get(path.suffix.lower(), 9), path.name.casefold()

    return min(paths, key=rank)


def _collect_plist_strings(value: object, output: Set[str], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(value, str):
        if value.strip():
            output.add(value.strip())
        return
    if isinstance(value, bytes):
        if len(value) <= MAX_SIDECAR_TEXT_BYTES:
            decoded = value.decode("utf-8", errors="ignore").strip()
            if decoded:
                output.add(decoded)
            if value.startswith((b"bplist00", b"<?xml")):
                try:
                    nested = plistlib.loads(value)
                except Exception:
                    return
                _collect_plist_strings(nested, output, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _collect_plist_strings(key, output, depth + 1)
            _collect_plist_strings(item, output, depth + 1)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_plist_strings(item, output, depth + 1)


def load_sidecar_content_evidence(
    path: Path,
    cache: Dict[Path, SidecarContentEvidence],
) -> SidecarContentEvidence:
    if path in cache:
        return cache[path]

    evidence = SidecarContentEvidence()
    cache[path] = evidence

    # THM/LRV 本质可能是 JPEG/视频代理，内容不当文本读取；它们用 ExifTool metadata 判断。
    if path.suffix.lower() not in {".xmp", ".aae", ".dop", ".pp3"}:
        return evidence

    try:
        size = path.stat().st_size
        if size > MAX_SIDECAR_TEXT_BYTES:
            evidence.parse_notes.append(
                f"sidecar 超过 {MAX_SIDECAR_TEXT_BYTES // (1024 * 1024)} MiB，跳过全文解析"
            )
            return evidence
        data = path.read_bytes()
    except OSError as exc:
        evidence.parse_notes.append(f"读取失败：{exc}")
        return evidence

    strings: Set[str] = set()
    ext = path.suffix.lower()

    if ext == ".aae":
        try:
            plist = plistlib.loads(data)
            _collect_plist_strings(plist, strings)
            evidence.parse_notes.append("AAE plist 解析成功")

            if isinstance(plist, dict):
                editor = plist.get("adjustmentEditorBundleID")
                fmt = plist.get("adjustmentFormatIdentifier")
                stamp = plist.get("adjustmentTimestamp")

                if isinstance(editor, str) and editor.strip():
                    evidence.aae_editor_bundle_id = editor.strip()
                if isinstance(fmt, str) and fmt.strip():
                    evidence.aae_format_identifier = fmt.strip()

                if isinstance(stamp, datetime):
                    evidence.aae_adjustment_timestamp = (
                        stamp.year, stamp.month, stamp.day,
                        stamp.hour, stamp.minute, stamp.second,
                    )
                elif isinstance(stamp, str):
                    # plist XML 中日期通常由 plistlib 解为 datetime；兼容少量字符串形式。
                    normalized_stamp = stamp.strip().replace("T", " ").replace("Z", "")
                    match = re.match(
                        r"^(\d{4})-(\d{2})-(\d{2})[ ](\d{2}):(\d{2}):(\d{2})",
                        normalized_stamp,
                    )
                    if match:
                        evidence.aae_adjustment_timestamp = tuple(map(int, match.groups()))  # type: ignore[assignment]
        except Exception:
            # AAE 也可能不是 plist，仍继续文本/UUID 扫描。
            pass

    decoded = ""
    for encoding in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            decoded = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not decoded:
        decoded = data.decode("utf-8", errors="ignore")

    normalized_text = html.unescape(urllib.parse.unquote(decoded)).replace("\\", "/")
    if normalized_text.strip():
        evidence.readable = True
        evidence.text = normalized_text.casefold()
        strings.add(normalized_text)

    normalized_strings: Set[str] = set()
    for value in strings:
        normalized = html.unescape(urllib.parse.unquote(value)).replace("\\", "/").strip()
        if normalized:
            normalized_strings.add(normalized.casefold())
    evidence.embedded_strings = normalized_strings

    identifier_source = "\n".join(normalized_strings)
    for match in UUID_RE.finditer(identifier_source):
        normalized = normalize_identifier(match.group(1))
        if normalized:
            evidence.identifiers.add(normalized)

    return evidence


def text_mentions_exact_filename(text: str, filename: str) -> bool:
    if not text:
        return False
    # 避免把 IMG_1234.HEIC.xmp 中的 IMG_1234.HEIC 错当成明确引用。
    pattern = re.compile(
        rf"(?<![a-z0-9._-]){re.escape(filename.casefold())}(?![a-z0-9._-])",
        re.IGNORECASE,
    )
    return pattern.search(text) is not None


def sidecar_metadata_score(sidecar: MediaInfo, media: MediaInfo) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []
    ext = sidecar.path.suffix.lower()

    time_diff = timestamp_difference_seconds(sidecar.timestamp, media.timestamp)
    if time_diff is not None:
        if time_diff <= 2:
            score += 4
            reasons.append(f"时间差 {time_diff}s")
        elif time_diff <= TIME_TOLERANCE_SECONDS:
            score += 2
            reasons.append(f"时间差 {time_diff}s")
        elif (
            sidecar.date_source == "metadata:DateTimeOriginal"
            and media.date_source == "metadata:DateTimeOriginal"
        ):
            score -= 6
            reasons.append(f"DateTimeOriginal 冲突 {time_diff}s")

    side_sig = device_signature(sidecar.metadata)
    media_sig = device_signature(media.metadata)
    if side_sig and media_sig:
        if side_sig == media_sig:
            score += 3
            reasons.append("设备一致")
        else:
            score -= 5
            reasons.append("设备冲突")

    side_dims = image_dimensions(sidecar.metadata)
    media_dims = image_dimensions(media.metadata)
    if ext not in {".thm", ".lrv"} and side_dims and media_dims:
        if dimensions_compatible(side_dims, media_dims):
            score += 1
            reasons.append("尺寸/比例兼容")
        else:
            score -= 2
            reasons.append("尺寸/比例冲突")

    side_duration = metadata_duration_seconds(sidecar.metadata)
    media_duration = metadata_duration_seconds(media.metadata)
    if ext == ".lrv":
        if media.kind != "video":
            score -= 8
            reasons.append("LRV 候选不是视频")
        elif side_duration is not None and media_duration is not None:
            duration_diff = abs(side_duration - media_duration)
            if duration_diff <= 1.0:
                score += 4
                reasons.append(f"时长差 {duration_diff:.2f}s")
            elif duration_diff <= 3.0:
                score += 2
                reasons.append(f"时长差 {duration_diff:.2f}s")
            elif duration_diff > 5.0:
                score -= 4
                reasons.append(f"时长冲突 {duration_diff:.2f}s")

    if ext == ".aae" and media.kind == "photo":
        score += 1
        reasons.append("AAE 照片候选")
    elif ext == ".thm" and media.kind == "video":
        score += 1
        reasons.append("THM 视频候选")

    return score, reasons


def build_sidecar_lookup_index(infos: Dict[Path, MediaInfo]) -> SidecarLookupIndex:
    """预建目录/name/stem/identifier/同 stem XMP 时区及目录级完整文件名匹配器。"""
    by_directory: Dict[Path, List[MediaInfo]] = defaultdict(list)
    by_name: Dict[Path, Dict[str, MediaInfo]] = defaultdict(dict)
    by_stem: Dict[Path, Dict[str, List[MediaInfo]]] = defaultdict(lambda: defaultdict(list))
    by_identifier: Dict[Path, Dict[str, Set[Path]]] = defaultdict(lambda: defaultdict(set))
    xmp_times: Dict[Tuple[Path, str], List[Tuple[Timestamp, int, str]]] = defaultdict(list)

    for info in infos.values():
        if info.kind in ("photo", "video"):
            directory = info.path.parent
            by_directory[directory].append(info)
            by_name[directory][info.path.name.casefold()] = info
            by_stem[directory][info.path.stem.casefold()].append(info)
            for identifier in metadata_identifiers(info.metadata):
                by_identifier[directory][identifier].add(info.path)
        elif info.kind == "sidecar" and info.path.suffix.lower() == ".xmp":
            key = (info.path.parent, info.path.stem.casefold())
            for field_name in ("DateCreated", "DateTimeOriginal", "CreateDate"):
                for value in metadata_values_for(info.metadata, field_name):
                    parsed = parse_metadata_timestamp(value)
                    offset = timestamp_explicit_utc_offset_minutes(value)
                    if parsed is not None and offset is not None:
                        xmp_times[key].append((parsed, offset, f"{info.path.name}:{field_name}={value}"))

    matchers: Dict[Path, Optional[re.Pattern[str]]] = {}
    for directory, name_map in by_name.items():
        names = sorted(name_map.keys(), key=len, reverse=True)
        if not names:
            matchers[directory] = None
            continue
        try:
            body = "|".join(re.escape(name) for name in names)
            matchers[directory] = re.compile(
                rf"(?<![a-z0-9._-])(?:{body})(?![a-z0-9._-])", re.IGNORECASE
            )
        except re.error:
            matchers[directory] = None

    return SidecarLookupIndex(
        media_by_directory={k: list(v) for k, v in by_directory.items()},
        media_by_name={k: dict(v) for k, v in by_name.items()},
        media_by_stem={k: {stem: list(items) for stem, items in v.items()} for k, v in by_stem.items()},
        media_by_identifier={k: {ident: set(paths) for ident, paths in v.items()} for k, v in by_identifier.items()},
        xmp_explicit_times_by_stem={k: list(v) for k, v in xmp_times.items()},
        filename_matcher_by_directory=matchers,
    )


def _indexed_content_filename_targets(
    evidence: SidecarContentEvidence,
    directory: Path,
    lookup_index: SidecarLookupIndex,
) -> Set[Path]:
    """等价于旧逐媒体文件名扫描，但优先使用目录级联合匹配器。"""
    name_map = lookup_index.media_by_name.get(directory, {})
    result: Set[Path] = set()
    for embedded in evidence.embedded_strings:
        basename = embedded.rsplit("/", 1)[-1].strip().casefold()
        info = name_map.get(basename)
        if info is not None:
            result.add(info.path)
    if evidence.text:
        matcher = lookup_index.filename_matcher_by_directory.get(directory)
        if matcher is not None:
            for match in matcher.finditer(evidence.text):
                info = name_map.get(match.group(0).casefold())
                if info is not None:
                    result.add(info.path)
        else:
            for info in lookup_index.media_by_directory.get(directory, []):
                if text_mentions_exact_filename(evidence.text, info.path.name):
                    result.add(info.path)
    return result


def build_sidecar_resolution_index(
    infos: Dict[Path, MediaInfo],
    content_cache: Dict[Path, SidecarContentEvidence],
    *,
    lookup_index: Optional[SidecarLookupIndex] = None,
    show_progress: bool = False,
) -> Tuple[Dict[Path, SidecarResolution], Dict[Path, Set[Path]], SidecarLookupIndex]:
    """每个 sidecar 只解析/判定一次，并建立 target -> sidecar 反向索引。"""
    lookup = lookup_index or build_sidecar_lookup_index(infos)
    resolutions: Dict[Path, SidecarResolution] = {}
    by_target: Dict[Path, Set[Path]] = defaultdict(set)
    sidecars = sorted(
        (info.path for info in infos.values() if info.kind == "sidecar"),
        key=lambda p: (str(p.parent).casefold(), p.name.casefold()),
    )
    total = len(sidecars)
    if show_progress and total:
        print_stage_progress("Sidecar索引", 0, total, "预建归属索引")
    for index, candidate in enumerate(sidecars, start=1):
        resolution = resolve_sidecar_targets(candidate, infos, content_cache, lookup)
        resolutions[candidate] = resolution
        for target in resolution.targets:
            by_target[target].add(candidate)
        if show_progress and (index == total or index == 1 or index % SIDECAR_PROGRESS_INTERVAL == 0):
            print_stage_progress("Sidecar索引", index, total, candidate.name)
    return resolutions, {k: set(v) for k, v in by_target.items()}, lookup


def sidecars_for_media_group_indexed(
    media_paths: Sequence[Path],
    resolutions: Dict[Path, SidecarResolution],
    sidecars_by_target: Dict[Path, Set[Path]],
) -> Tuple[List[Path], List[str]]:
    media_set = set(media_paths)
    candidates: Set[Path] = set()
    for target in media_set:
        candidates.update(sidecars_by_target.get(target, set()))
    result: Set[Path] = set()
    ambiguous: List[str] = []
    for candidate in sorted(candidates, key=lambda p: (str(p.parent).casefold(), p.name.casefold())):
        resolution = resolutions[candidate]
        targets = resolution.targets
        if resolution.status == "unrelated" or not targets or not (targets & media_set):
            continue
        if targets.issubset(media_set):
            result.add(candidate)
            continue
        outsiders = sorted((p.name for p in targets - media_set), key=str.casefold)
        ambiguous.append(
            f"{candidate.name} 归属跨越当前组：{resolution.reason}；组外候选：" + ", ".join(outsiders)
        )
    return sorted(result, key=lambda p: (str(p.parent).casefold(), p.name.casefold())), ambiguous


def build_unknown_companion_index(infos: Dict[Path, MediaInfo]) -> Dict[Tuple[Path, str], Set[Path]]:
    """每个包含媒体的目录只扫描一次，避免每组重复 iterdir。"""
    directories = {info.path.parent for info in infos.values() if info.kind in ("photo", "video")}
    index: Dict[Tuple[Path, str], Set[Path]] = defaultdict(set)
    for directory in directories:
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for candidate in entries:
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
            except OSError:
                continue
            if candidate.name.startswith("._") or candidate.name.casefold() in SYSTEM_EXCLUDED_FILE_NAMES:
                continue
            if classify(candidate) in ("photo", "video", "sidecar"):
                continue
            index[(directory, candidate.stem.casefold())].add(candidate)
    return index


def unknown_companions_for_media_group_indexed(
    media_paths: Sequence[Path],
    unknown_index: Dict[Tuple[Path, str], Set[Path]],
) -> List[Path]:
    suspicious: Set[Path] = set()
    for media in media_paths:
        suspicious.update(unknown_index.get((media.parent, media.stem.casefold()), set()))
        suspicious.update(unknown_index.get((media.parent, media.name.casefold()), set()))
    return sorted(suspicious, key=lambda p: str(p).casefold())


def resolve_sidecar_targets(
    sidecar_path: Path,
    infos: Dict[Path, MediaInfo],
    content_cache: Dict[Path, SidecarContentEvidence],
    lookup_index: Optional[SidecarLookupIndex] = None,
) -> SidecarResolution:
    sidecar = infos[sidecar_path]
    lookup = lookup_index or build_sidecar_lookup_index(infos)
    directory_media = lookup.media_by_directory.get(sidecar_path.parent, [])
    if not directory_media:
        return SidecarResolution("unrelated", reason="同目录没有主媒体")

    by_name = lookup.media_by_name.get(sidecar_path.parent, {})
    by_stem = lookup.media_by_stem.get(sidecar_path.parent, {})
    strong_sets: List[Tuple[Set[Path], str]] = []

    # 1) 双扩展名：IMG_1234.HEIC.xmp -> IMG_1234.HEIC
    explicit_name = sidecar_path.stem.casefold()
    explicit = by_name.get(explicit_name)
    if explicit is not None:
        strong_sets.append(({explicit.path}, "sidecar 文件名明确包含主媒体完整文件名"))

    evidence = load_sidecar_content_evidence(sidecar_path, content_cache)

    # Apple Photos/Camera 导出的 IMG_O####.AAE：
    # 自动查同目录 IMG_#### 主媒体，但绝不只凭数字绑定。必须同时满足：
    # 1) AAE plist 明确来自 com.apple.camera / com.apple.photo；
    # 2) adjustmentTimestamp 与候选媒体的强内嵌时间字段 <=2 秒。
    # 若多个同 stem 主媒体都通过，保留全部候选；只有它们已处于同一媒体组时才会一起移动。
    img_o_match = APPLE_ORIGINAL_ADJUSTMENT_AAE_RE.fullmatch(sidecar_path.stem)
    if sidecar_path.suffix.lower() == ".aae" and img_o_match:
        base_stem = f"IMG_{img_o_match.group(1)}".casefold()
        base_candidates = list(by_stem.get(base_stem, []))

        apple_adjustment = (
            (evidence.aae_editor_bundle_id or "").casefold() == "com.apple.camera"
            and (evidence.aae_format_identifier or "").casefold() == "com.apple.photo"
            and evidence.aae_adjustment_timestamp is not None
        )

        verified_targets: Set[Path] = set()
        verified_reasons: List[str] = []
        if apple_adjustment:
            for candidate in base_candidates:
                # plist adjustmentTimestamp 是 UTC。候选媒体时间可能也是 UTC（例如某些 MOV），
                # 也可能是本地 wall time（例如 HEIC）。只使用文件自身明确携带的时区证据：
                # 1) 时间值自己的 Z/+HH:MM；2) EXIF OffsetTime*；3) 同 stem XMP 的显式时区。
                # 绝不因为当前机器位置或年份直接猜 +08/+02 等偏移。
                best: Optional[Tuple[int, str]] = None
                sibling_xmp_times = lookup.xmp_explicit_times_by_stem.get(
                    (candidate.path.parent, candidate.path.stem.casefold()), []
                )

                for field_name in (
                    "DateTimeOriginal",
                    "CreateDate",
                    "MediaCreateDate",
                    "TrackCreateDate",
                    "DateCreated",
                ):
                    for value in metadata_values_for(candidate.metadata, field_name):
                        parsed = parse_metadata_timestamp(value)
                        if parsed is None:
                            continue

                        # 兼容此前已验证的 Apple MOV：其 QuickTime CreateDate 常以 UTC wall time 保存。
                        direct_difference = timestamp_difference_seconds(
                            evidence.aae_adjustment_timestamp,
                            parsed,
                        )
                        if direct_difference is not None:
                            description = f"{field_name}={format_timestamp(parsed)}（直接比较）"
                            if best is None or direct_difference < best[0]:
                                best = (direct_difference, description)

                        explicit_offset = timestamp_explicit_utc_offset_minutes(value)
                        if explicit_offset is not None:
                            difference = utc_timestamp_difference_seconds(
                                evidence.aae_adjustment_timestamp,
                                parsed,
                                explicit_offset,
                            )
                            if difference is not None:
                                description = f"{field_name}={value}（显式时区归一化）"
                                if best is None or difference < best[0]:
                                    best = (difference, description)

                        for offset, offset_reason in metadata_offsets_for_time_field(
                            candidate.metadata, field_name
                        ):
                            difference = utc_timestamp_difference_seconds(
                                evidence.aae_adjustment_timestamp,
                                parsed,
                                offset,
                            )
                            if difference is None:
                                continue
                            description = (
                                f"{field_name}={format_timestamp(parsed)} + {offset_reason}（时区归一化）"
                            )
                            if best is None or difference < best[0]:
                                best = (difference, description)

                        # 同 stem XMP 只提供显式 timezone；先要求其 wall time 与候选媒体字段 <=2 秒，
                        # 再用 XMP offset 把候选时间归一化到 UTC。
                        for xmp_timestamp, xmp_offset, xmp_reason in sibling_xmp_times:
                            wall_difference = timestamp_difference_seconds(parsed, xmp_timestamp)
                            if (
                                wall_difference is None
                                or wall_difference > APPLE_ORIGINAL_ADJUSTMENT_TIME_TOLERANCE_SECONDS
                            ):
                                continue
                            difference = utc_timestamp_difference_seconds(
                                evidence.aae_adjustment_timestamp,
                                parsed,
                                xmp_offset,
                            )
                            if difference is None:
                                continue
                            description = (
                                f"{field_name}={format_timestamp(parsed)}，由 {xmp_reason} 提供显式时区"
                            )
                            if best is None or difference < best[0]:
                                best = (difference, description)

                if best is not None and best[0] <= APPLE_ORIGINAL_ADJUSTMENT_TIME_TOLERANCE_SECONDS:
                    verified_targets.add(candidate.path)
                    verified_reasons.append(
                        f"{candidate.path.name}: adjustmentTimestamp(UTC)="
                        f"{format_timestamp(evidence.aae_adjustment_timestamp)} 与 {best[1]}相差 {best[0]} 秒"
                    )

        if verified_targets:
            strong_sets.append((
                verified_targets,
                "IMG_O####.AAE Apple adjustment 关系验证通过（"
                + "; ".join(verified_reasons)
                + "）",
            ))

    # 2) ExifTool/sidecar metadata 中的 OriginalFileName / PreservedFileName 等。
    metadata_hints = metadata_filename_hints(sidecar.metadata)
    metadata_targets = {
        info.path for hint in metadata_hints if (info := by_name.get(hint)) is not None
    }
    if metadata_targets:
        strong_sets.append((metadata_targets, "sidecar metadata 明确记录原文件名"))

    # 3) XMP/AAE/DOP/PP3 内容中明确出现完整媒体文件名。
    content_targets = _indexed_content_filename_targets(evidence, sidecar_path.parent, lookup)
    if content_targets:
        strong_sets.append((content_targets, "sidecar 内容明确引用媒体完整文件名"))

    # 4) ContentIdentifier / MediaGroupUUID / XMP UUID 等标识符交集。
    side_ids = metadata_identifiers(sidecar.metadata) | evidence.identifiers
    id_targets: Set[Path] = set()
    identifier_index = lookup.media_by_identifier.get(sidecar_path.parent, {})
    for identifier in side_ids:
        id_targets.update(identifier_index.get(identifier, set()))
    if id_targets:
        strong_sets.append((id_targets, "sidecar 与媒体存在相同唯一标识符"))

    if strong_sets:
        reasons: List[str] = []
        target_sets: List[Set[Path]] = []
        for targets, reason in strong_sets:
            target_sets.append(set(targets))
            reasons.append(reason + "：" + ", ".join(sorted(p.name for p in targets)))

        common = set(target_sets[0])
        for targets in target_sets[1:]:
            common.intersection_update(targets)

        if len(common) == 1:
            # 多种强证据共同指向同一文件，优先于某一证据单独列出多个候选。
            return SidecarResolution("confirmed", common, " | ".join(reasons))
        if len(common) > 1:
            return SidecarResolution("ambiguous", common, " | ".join(reasons))

        combined: Set[Path] = set().union(*target_sets)
        return SidecarResolution(
            "ambiguous",
            combined,
            "强证据互相冲突；" + " | ".join(reasons),
        )

    # 没有强内容证据时，仅在同 stem 候选中做 metadata 辅助。
    stem = sidecar_path.stem.casefold()
    same_stem = list(by_stem.get(stem, []))
    if not same_stem:
        return SidecarResolution("unrelated", reason="没有同 stem 主媒体，也没有内容引用")

    ext = sidecar_path.suffix.lower()
    if ext == ".lrv":
        videos = [info for info in same_stem if info.kind == "video"]
        if videos:
            same_stem = videos
    elif ext == ".aae":
        photos = [info for info in same_stem if info.kind == "photo"]
        if photos:
            same_stem = photos

    if len(same_stem) == 1:
        return SidecarResolution(
            "generic",
            {same_stem[0].path},
            "同目录只有一个合理的同 stem 主媒体",
        )

    scored: List[Tuple[int, MediaInfo, List[str]]] = []
    for candidate in same_stem:
        score, reasons = sidecar_metadata_score(sidecar, candidate)
        scored.append((score, candidate, reasons))
    scored.sort(key=lambda item: item[0], reverse=True)

    if scored:
        best_score, best_info, best_reasons = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else -999
        if best_score >= 5 and best_score - second_score >= 2:
            return SidecarResolution(
                "confirmed",
                {best_info.path},
                f"sidecar metadata 唯一匹配得分 {best_score}：" + ", ".join(best_reasons),
            )

    score_text = "; ".join(
        f"{info.path.name}={score}({', '.join(reasons) or '无有效 metadata'})"
        for score, info, reasons in scored
    )
    return SidecarResolution(
        "ambiguous",
        {info.path for info in same_stem},
        "同 stem 有多个候选且内容/metadata 无法唯一确认；" + score_text,
    )


def sidecars_for_media_group(
    media_paths: Sequence[Path],
    all_paths: Set[Path],
    infos: Dict[Path, MediaInfo],
    content_cache: Dict[Path, SidecarContentEvidence],
) -> Tuple[List[Path], List[str]]:
    """
    sidecar 归属优先级：
    1. IMG_1234.HEIC.xmp 这类双扩展名明确绑定；
    2. IMG_O####.AAE：解析 Apple Camera/Photos adjustment，并用 adjustmentTimestamp 与 IMG_#### 媒体时间严格核对；
    3. XMP/AAE/DOP/PP3 内容中的原文件名或 UUID/ContentIdentifier；
    4. sidecar 自身 metadata 与主媒体时间/设备/尺寸/时长；
    5. 最后才使用唯一同 stem。

    如果一个 sidecar 的所有可能目标都已属于当前媒体组，移动它是安全的；
    如果可能目标跨越当前组和组外媒体，则整组保持原位。
    """
    media_set = set(media_paths)
    result: Set[Path] = set()
    ambiguous: List[str] = []
    directories = {path.parent for path in media_paths}

    for directory in directories:
        sidecars_here = sorted(
            (
                path for path in all_paths
                if path.parent == directory and classify(path) == "sidecar"
            ),
            key=lambda p: p.name.casefold(),
        )

        for candidate in sidecars_here:
            resolution = resolve_sidecar_targets(candidate, infos, content_cache)
            targets = resolution.targets

            if resolution.status == "unrelated" or not targets:
                continue

            intersects = bool(targets & media_set)
            if not intersects:
                # 内容已经证明它属于别的媒体，不应阻碍当前组。
                continue

            if targets.issubset(media_set):
                # 即使 sidecar 同时可能属于本组内 HEIC+JPG/HEIC+MOV，整组移动也不会拆散。
                result.add(candidate)
                continue

            outsiders = sorted((p.name for p in targets - media_set), key=str.casefold)
            ambiguous.append(
                f"{candidate.name} 归属跨越当前组：{resolution.reason}；组外候选："
                + ", ".join(outsiders)
            )

    return sorted(result, key=lambda p: (str(p.parent).casefold(), p.name.casefold())), ambiguous

def unknown_companions_for_media_group(
    media_paths: Sequence[Path],
    known_members: Set[Path],
) -> List[Path]:
    """发现明显同 stem、但脚本不认识的伴随文件；宁可不移动，也不把附件留散。"""
    suspicious: Set[Path] = set()

    for media in media_paths:
        try:
            entries = list(media.parent.iterdir())
        except OSError:
            continue

        stem = media.stem.casefold()
        full_name = media.name.casefold()

        for candidate in entries:
            if candidate in known_members or candidate.is_symlink() or not candidate.is_file():
                continue
            if candidate.name.startswith("._") or candidate.name.casefold() in SYSTEM_EXCLUDED_FILE_NAMES:
                continue

            candidate_stem = candidate.stem.casefold()
            if candidate_stem == stem or candidate_stem == full_name:
                # 已知主媒体扩展名属于“同 stem 媒体候选”，不是未知附件；关系由 family/live 逻辑负责。
                if classify(candidate) in ("photo", "video", "sidecar"):
                    continue
                suspicious.add(candidate)

    return sorted(suspicious, key=lambda p: str(p).casefold())


def build_groups(
    infos: Dict[Path, MediaInfo],
    still_to_video: Dict[Path, Path],
    source_root: Path,
    variant_family_map: Dict[Path, Set[Path]],
    *,
    precomputed_sidecar_lookup: Optional[SidecarLookupIndex] = None,
    precomputed_sidecar_resolutions: Optional[Dict[Path, SidecarResolution]] = None,
    precomputed_sidecars_by_target: Optional[Dict[Path, Set[Path]]] = None,
) -> Tuple[List[MoveGroup], List[Problem]]:
    groups: List[MoveGroup] = []
    problems: List[Problem] = []

    consumed: Set[Path] = set()
    sidecar_content_cache: Dict[Path, SidecarContentEvidence] = {}
    sidecar_lookup = precomputed_sidecar_lookup or build_sidecar_lookup_index(infos)
    if precomputed_sidecar_resolutions is not None and precomputed_sidecars_by_target is not None:
        sidecar_resolutions = precomputed_sidecar_resolutions
        sidecars_by_target = precomputed_sidecars_by_target
    else:
        sidecar_resolutions, sidecars_by_target, _ = build_sidecar_resolution_index(
            infos, sidecar_content_cache, lookup_index=sidecar_lookup, show_progress=True
        )
        finish_stage_progress()
    unknown_companion_index = build_unknown_companion_index(infos)

    def variant_family(path: Path) -> Set[Path]:
        return set(variant_family_map.get(path, {path}))

    def related_videos_for_photos(photo_paths: Set[Path]) -> Set[Path]:
        videos: Set[Path] = set()
        for photo in photo_paths:
            video = still_to_video.get(photo)
            if video is not None:
                videos.add(video)
        return videos

    def finish_group(
        primary_path: Path,
        media_members: Sequence[Path],
        description: str,
    ) -> bool:
        primary = infos[primary_path]
        media_set = set(media_members)

        excluded_members = [
            infos[path]
            for path in media_set
            if infos[path].source_state == SOURCE_STATE_EXCLUDED
        ]
        if excluded_members:
            details = " | ".join(
                f"{item.path.name}: {item.source_reason}"
                + (f"（{'; '.join(item.source_evidence)}）" if item.source_evidence else "")
                for item in excluded_members
            )
            problems.append(
                Problem(
                    str(primary_path),
                    f"来源强负面证据优先于 EXIF，{description} 整组保持原位：{details}",
                )
            )
            consumed.update(media_set)
            return False

        ambiguous_members = [
            infos[path]
            for path in media_set
            if infos[path].source_state == SOURCE_STATE_AMBIGUOUS
        ]
        if ambiguous_members:
            details = " | ".join(
                f"{item.path.name}: {item.source_reason}"
                + (f"（{'; '.join(item.source_evidence)}）" if item.source_evidence else "")
                for item in ambiguous_members
            )
            problems.append(
                Problem(
                    str(primary_path),
                    f"来源不确定，为避免误收下载/聊天媒体，{description} 整组保持原位：{details}",
                    "warning",
                )
            )
            consumed.update(media_set)
            return False

        if not primary.camera_origin:
            problems.append(
                Problem(
                    str(primary_path),
                    "无法强确认主媒体是相机实拍："
                    f"{primary.camera_origin_reason}；{description} 整组保持原位",
                )
            )
            consumed.update(media_set)
            return False

        if primary.timestamp is None or primary.date_source is None:
            problems.append(
                Problem(str(primary_path), f"{description} 主媒体没有可靠内嵌时间，整组保持原位")
            )
            consumed.update(media_set)
            return False

        sidecars, ambiguous_sidecars = sidecars_for_media_group_indexed(
            list(media_set), sidecar_resolutions, sidecars_by_target
        )
        if ambiguous_sidecars:
            problems.append(
                Problem(
                    str(primary_path),
                    "sidecar 归属不明确，为防止附件和主媒体拆散，整组保持原位："
                    + " | ".join(ambiguous_sidecars),
                    "failure",
                )
            )
            consumed.update(media_set)
            return False

        known_members = media_set | set(sidecars)
        unknown = unknown_companions_for_media_group_indexed(list(media_set), unknown_companion_index)
        if unknown:
            problems.append(
                Problem(
                    str(primary_path),
                    "发现脚本未识别但名称明显相关的伴随文件，为防止遗留附件，整组保持原位："
                    + ", ".join(path.name for path in unknown),
                    "failure",
                )
            )
            consumed.update(media_set)
            return False

        unique_members = sorted(
            known_members,
            key=lambda path: (str(path.parent).casefold(), path.name.casefold()),
        )
        year, month, day, *_ = primary.timestamp

        groups.append(
            MoveGroup(
                primary=primary_path,
                members=unique_members,
                year=year,
                month=month,
                day=day,
                date_source=primary.date_source,
                description=description,
            )
        )
        consumed.update(unique_members)
        return True

    # 先处理有静态图 + 视频关系的组；同时吸收已确认的 HEIC/JPG/DNG 同资产变体。
    for still_path, video_path in sorted(
        still_to_video.items(),
        key=lambda item: str(item[0]).casefold(),
    ):
        if still_path in consumed or video_path in consumed:
            continue

        photos = variant_family(still_path)
        videos = related_videos_for_photos(photos)
        videos.add(video_path)
        media_members = photos | videos

        primary_path = choose_family_primary(sorted(photos), infos)
        primary = infos[primary_path]

        # 非可信 EXIF 情况保留 0113 的文件名冲突保护。
        if not primary.trusted_exif:
            conflict_parts: List[str] = []
            for component_path in media_members:
                component = infos[component_path]
                if component.filename_timestamp_conflict:
                    difference = timestamp_difference_seconds(
                        component.filename_timestamp, component.timestamp
                    )
                    conflict_parts.append(
                        f"{component.path.name} 文件名时间 "
                        f"{format_timestamp(component.filename_timestamp)}，"
                        f"元数据时间 {format_timestamp(component.timestamp)}，"
                        f"相差 {difference} 秒"
                    )
            if conflict_parts:
                problems.append(
                    Problem(
                        str(primary_path),
                        "；".join(conflict_parts)
                        + f"；超过允许的 {TIME_TOLERANCE_SECONDS} 秒，整组保持原位",
                    )
                )
                consumed.update(media_members)
                continue

        is_motion = any(android_filename_prefix(path) == "MVIMG" for path in media_members)
        label = "Motion Photo" if is_motion else "Live Photo"
        media_names = " + ".join(sorted((p.name for p in media_members), key=str.casefold))
        finish_group(primary_path, list(media_members), f"{label} ({media_names})")

    # 独立照片/视频；照片若已确认属于 Apple 多格式同资产，整组处理。
    for info in sorted(infos.values(), key=lambda item: str(item.path).casefold()):
        if info.kind not in ("photo", "video") or info.path in consumed:
            continue

        if info.kind == "photo":
            photos = variant_family(info.path)
            videos = related_videos_for_photos(photos)
            media_members = photos | videos
            primary_path = choose_family_primary(sorted(photos), infos)

            if len(photos) > 1:
                names = " + ".join(sorted((p.name for p in photos), key=str.casefold))
                description = f"Photo variants ({names})"
            else:
                description = f"Photo ({info.path.name})"
        else:
            media_members = {info.path}
            primary_path = info.path
            description = f"Video ({info.path.name})"

        finish_group(primary_path, list(media_members), description)

    # sidecar 绝不单独移动。
    for info in sorted(infos.values(), key=lambda item: str(item.path).casefold()):
        if info.kind == "sidecar" and info.path not in consumed:
            problems.append(
                Problem(str(info.path), "找不到已确认且归属明确的主媒体，sidecar 保持原位")
            )

    return groups, problems


def target_dir(
    year: int,
    month: int,
    day: Optional[int] = None,
) -> Path:
    # 关键要求：目标永远固定，绝不能使用来源目录。
    month_dir = (
        DESTINATION_ROOT
        / f"{year:04d}"
        / f"{year:04d}-{month:02d}"
    )
    if day is None:
        return month_dir
    return month_dir / f"{year:04d}-{month:02d}-{day:02d}"



def sha256_file(path: Path) -> str:
    """完整 SHA-256 + 1854 同款持久缓存；最终重复标准仍是完整哈希。"""
    cached = _SHA256_CACHE.get(path)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(HASH_CHUNK_SIZE)
            if not chunk: break
            digest.update(chunk)
    result = digest.hexdigest()
    _SHA256_CACHE.put(path, result)
    return result


def compare_existing_destination_content(
    source: Path,
    destination: Path,
) -> Tuple[Optional[bool], str]:
    """
    比较同名来源与既有目标内容。

    返回：
    - (True, detail): 字节内容可强确认完全一致；
    - (False, detail): 可强确认不同；
    - (None, detail): 无法安全完成比较，调用方必须保守保持原位。

    先比较大小；只有大小相同才读取文件计算 SHA-256。
    """
    try:
        if source.is_symlink():
            return None, f"来源是符号链接，拒绝判重：{source}"

        if destination.is_symlink():
            return False, f"目标同名项是符号链接，不按重复文件处理：{destination}"

        if not source.is_file():
            return None, f"来源不是普通文件，无法安全判重：{source}"

        if not destination.is_file():
            return False, f"目标同名项不是普通文件：{destination}"

        source_size = source.stat().st_size
        destination_size = destination.stat().st_size

        if source_size != destination_size:
            return (
                False,
                f"大小不同（来源 {source_size} bytes；目标 {destination_size} bytes）",
            )

        source_hash = sha256_file(source)
        destination_hash = sha256_file(destination)

        if source_hash == destination_hash:
            return (
                True,
                f"大小相同且 SHA-256 完全一致（{source_hash}）",
            )

        return (
            False,
            "大小相同但 SHA-256 不同"
            f"（来源 {source_hash}；目标 {destination_hash}）",
        )

    except Exception as exc:
        return None, f"内容判重失败：{source} <-> {destination}: {exc}"

def duplicate_target_for_source(source: Path) -> Path:
    """
    重复副本统一移入 DUPLICATE_ROOT，并镜像其在 gallery_config.py 的 BACKUP_ROOT 下的原相对路径。
    这样不需要改名，也能避免不同来源目录的同名文件互相覆盖。
    """
    duplicate_root = DUPLICATE_ROOT.resolve(strict=False)
    source_resolved = source.resolve(strict=False)

    if path_is_within(source_resolved, duplicate_root):
        return source

    backup_root = BACKUP_APPLE_ROOT.resolve(strict=False)
    try:
        relative = source_resolved.relative_to(backup_root)
    except ValueError as exc:
        raise ValueError(f"来源不在 Backup Apple 内，无法生成重复副本路径：{source}") from exc

    return duplicate_root / relative


def is_duplicate_destination(path: Path) -> bool:
    return path_is_within(path.resolve(strict=False), DUPLICATE_ROOT.resolve(strict=False))


def preflight_group(
    group: MoveGroup,
    reserved_destinations: Set[str],
) -> Tuple[
    bool,
    List[Tuple[Path, Path]],
    Optional[str],
    Optional[str],
]:
    """
    目标策略：
    1. 正常放 YYYY/YYYY-MM/，文件名保持原样；
    2. 若正常目标已存在完整同名项：
       - 先比较大小；大小相同再比较 SHA-256；
       - 内容完全相同：该来源副本移入 DUPLICATE_ROOT，保留其原来源相对路径；
       - 同组其他目标缺失成员（例如 XMP/AAE）仍补入正常 Gallery；
       - 内容不同：若该组没有“已确认重复成员”，整组可继续尝试 YYYY-MM-DD 日目录；
       - 若同一候选目录同时出现“完全重复成员 + 同名不同内容冲突”，整组保守停止，不做部分移动；
       - 判重本身失败：整组保持原位并要求人工检查。
    3. 日期目录执行同样的判重/补齐逻辑。
    4. 重复隔离目录本身也禁止覆盖、不改名；若其镜像位置已存在文件，则保守停止。

    dry-run 也用 reserved_destinations 记住本次计划目标，避免两个来源组在预览时
    同时宣称会占用同一个目标名。
    """
    month_dir = target_dir(group.year, group.month)
    day_dir = target_dir(group.year, group.month, group.day)

    def mappings_for(directory: Path) -> List[Tuple[Path, Path]]:
        return [(source, directory / source.name) for source in group.members]

    def reservation_key(path: Path) -> str:
        return str(path.resolve(strict=False)).casefold()

    def evaluate(
        directory: Path,
    ) -> Tuple[str, List[Tuple[Path, Path]], Optional[str]]:
        """返回 ok / ok-with-duplicates / conflict / mixed-conflict / error。"""
        canonical_mappings = mappings_for(directory)
        names_in_group: Dict[str, Path] = {}
        final_mappings: List[Tuple[Path, Path]] = []
        duplicate_details: List[str] = []
        conflict_details: List[str] = []

        for source, canonical_destination in canonical_mappings:
            key = canonical_destination.name.casefold()
            previous = names_in_group.get(key)
            if previous is not None and previous != source:
                return (
                    "conflict",
                    canonical_mappings,
                    "组内两个文件会映射到同一个目标名称："
                    f"{previous.name} / {source.name}",
                )
            names_in_group[key] = source

            if same_path(source, canonical_destination):
                final_mappings.append((source, canonical_destination))
                continue

            if reservation_key(canonical_destination) in reserved_destinations:
                conflict_details.append(
                    f"本次运行已有另一组计划占用目标：{canonical_destination}"
                )
                continue

            if not canonical_destination.exists():
                final_mappings.append((source, canonical_destination))
                continue

            identical, detail = compare_existing_destination_content(
                source, canonical_destination
            )

            if identical is None:
                return (
                    "error",
                    canonical_mappings,
                    f"无法安全判断同名目标是否为重复文件：{detail}",
                )

            if not identical:
                conflict_details.append(
                    f"目标已存在同名但内容不同：{canonical_destination}；{detail}"
                )
                continue

            # 正常 Gallery 已有字节完全相同文件：源副本不删除，而是移入统一隔离目录。
            # 先记录“Gallery 已确认重复”这一事实；后续若隔离路径本身发生冲突，
            # evaluate 会返回 mixed-conflict，绝不能误走日期目录兜底。
            duplicate_details.append(
                f"{source.name} 与 Gallery 目标 {canonical_destination} 完全一致：{detail}"
            )
            duplicate_destination = duplicate_target_for_source(source)

            if same_path(source, duplicate_destination):
                final_mappings.append((source, source))
                continue

            duplicate_key = reservation_key(duplicate_destination)
            if duplicate_key in reserved_destinations:
                conflict_details.append(
                    f"本次运行已有另一组计划占用重复副本隔离路径：{duplicate_destination}"
                )
                continue

            if duplicate_destination.exists():
                duplicate_identical, duplicate_detail = compare_existing_destination_content(
                    source, duplicate_destination
                )
                if duplicate_identical is None:
                    return (
                        "error",
                        canonical_mappings,
                        "无法安全判断重复副本隔离目录中的同名文件："
                        f"{duplicate_detail}",
                    )

                if duplicate_identical:
                    conflict_details.append(
                        "重复副本隔离路径已经存在完全相同文件；脚本不删除来源、也不覆盖："
                        f"{duplicate_destination}；{duplicate_detail}"
                    )
                else:
                    conflict_details.append(
                        "重复副本隔离路径已存在同名但不同内容文件；不改名、不覆盖："
                        f"{duplicate_destination}；{duplicate_detail}"
                    )
                continue

            final_mappings.append((source, duplicate_destination))

        if conflict_details and duplicate_details:
            return (
                "mixed-conflict",
                final_mappings,
                "同一媒体组同时存在已确认重复成员和冲突成员；为避免拆组或错误补齐，整组保持原位。"
                + " 重复：" + " | ".join(duplicate_details)
                + "；冲突：" + " | ".join(conflict_details),
            )

        if conflict_details:
            return "conflict", canonical_mappings, " | ".join(conflict_details)

        if duplicate_details:
            normal_moves = sum(
                1
                for source, destination in final_mappings
                if not same_path(source, destination) and not is_duplicate_destination(destination)
            )
            duplicate_moves = sum(
                1
                for source, destination in final_mappings
                if not same_path(source, destination) and is_duplicate_destination(destination)
            )
            detail = (
                f"确认 {duplicate_moves} 个来源文件与 Gallery 目标字节完全相同，将移入 {DUPLICATE_ROOT}；"
                f"同组另有 {normal_moves} 个缺失成员补入正常 Gallery。"
                + " | ".join(duplicate_details)
            )
            return "ok-with-duplicates", final_mappings, detail

        return "ok", final_mappings, None

    # 已经处于合法日期兜底目录的组不要再搬回月目录。
    day_mappings = mappings_for(day_dir)
    if all(same_path(source, destination) for source, destination in day_mappings):
        return True, day_mappings, None, "day-existing"

    month_status, month_mappings, month_detail = evaluate(month_dir)
    if month_status == "ok":
        return True, month_mappings, None, "month"

    if month_status == "ok-with-duplicates":
        return True, month_mappings, month_detail, "month-with-duplicates"

    if month_status == "error":
        return False, month_mappings, month_detail, "duplicate-check-error"

    if month_status == "mixed-conflict":
        return False, month_mappings, month_detail, "duplicate-mixed-conflict"

    # 只有“纯同名不同内容冲突”才进入日期目录兜底；若已经夹杂确认重复，不做跨目录拆组。
    day_status, day_mappings, day_detail = evaluate(day_dir)
    if day_status == "ok":
        return (
            True,
            day_mappings,
            f"月目录发生同名不同内容冲突（{month_detail}），整组改放日期子目录",
            "day-fallback",
        )

    if day_status == "ok-with-duplicates":
        return (
            True,
            day_mappings,
            f"月目录发生同名不同内容冲突（{month_detail}）；日期目录验证后：{day_detail}",
            "day-with-duplicates",
        )

    if day_status == "error":
        return False, day_mappings, day_detail, "duplicate-check-error"

    if day_status == "mixed-conflict":
        return False, day_mappings, day_detail, "duplicate-mixed-conflict"

    return (
        False,
        day_mappings,
        "月目录和日期子目录都存在同名不同内容冲突；不改名、不覆盖，整组保持原位。"
        f" 月目录：{month_detail}；日期目录：{day_detail}",
        None,
    )

def move_group_with_rollback(
    mappings: Sequence[Tuple[Path, Path]],
) -> Tuple[
    bool,
    Optional[str],
    List[Tuple[Path, Path]],
]:
    moved: List[Tuple[Path, Path]] = []

    try:
        # 先建立目标目录。
        for source, destination in mappings:
            if same_path(source, destination):
                continue

            destination.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        # 真正移动前再次检查，防止并发出现同名文件。
        for source, destination in mappings:
            if same_path(source, destination):
                continue

            if destination.exists():
                raise FileExistsError(
                    f"目标突然已存在：{destination}"
                )

        for source, destination in mappings:
            if same_path(source, destination):
                continue

            shutil.move(
                str(source),
                str(destination),
            )

            moved.append(
                (source, destination)
            )

        return True, None, moved

    except Exception as exc:
        rollback_errors: List[str] = []

        for original, moved_to in reversed(moved):
            try:
                if (
                    moved_to.exists()
                    and not original.exists()
                ):
                    original.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                    shutil.move(
                        str(moved_to),
                        str(original),
                    )

            except Exception as rollback_exc:
                rollback_errors.append(
                    f"{moved_to} -> {original}: {rollback_exc}"
                )

        message = str(exc)

        if rollback_errors:
            message += (
                "；回滚失败："
                + " | ".join(rollback_errors)
            )

        return False, message, moved


def combine_skip_problems_for_display(skips: Sequence[Problem]) -> List[ProblemDisplayEntry]:
    """仅优化日志显示，不改变任何整理/移动决策。

    将“主媒体保持原位”与随后产生的、同目录同 stem 且归属唯一的孤立 sidecar
    合并成一条显示。若同 stem 有多个主媒体候选，sidecar 继续单独显示，避免在日志中
    暗示一个未经确认的对应关系。
    """
    if not skips:
        return []

    main_by_key: Dict[Tuple[str, str], List[Problem]] = defaultdict(list)
    orphan_sidecars: List[Problem] = []
    ordinary: List[Problem] = []

    for problem in skips:
        path = Path(problem.path)
        kind = classify(path)
        if (
            kind == "sidecar"
            and problem.reason.startswith("找不到已确认且归属明确的主媒体")
        ):
            orphan_sidecars.append(problem)
            continue

        ordinary.append(problem)
        if kind in ("photo", "video"):
            key = (str(path.parent).casefold(), path.stem.casefold())
            main_by_key[key].append(problem)

    sidecars_by_main_id: Dict[int, List[Problem]] = defaultdict(list)
    unpaired_sidecars: List[Problem] = []

    for sidecar_problem in orphan_sidecars:
        sidecar_path = Path(sidecar_problem.path)
        key = (str(sidecar_path.parent).casefold(), sidecar_path.stem.casefold())
        candidates = main_by_key.get(key, [])
        if len(candidates) == 1:
            sidecars_by_main_id[id(candidates[0])].append(sidecar_problem)
        else:
            unpaired_sidecars.append(sidecar_problem)

    entries: List[ProblemDisplayEntry] = []
    for problem in ordinary:
        companions = sorted(
            sidecars_by_main_id.get(id(problem), []),
            key=lambda item: item.path.casefold(),
        )
        entries.append(ProblemDisplayEntry(problem, companions))

    entries.extend(ProblemDisplayEntry(problem) for problem in unpaired_sidecars)
    return entries


def print_skip_problem_entries(skips: Sequence[Problem]) -> None:
    entries = combine_skip_problems_for_display(skips)
    grouped_file_records = sum(1 + len(entry.companions) for entry in entries)

    print()
    print(
        "保持原位 / 跳过（合并显示）："
        f"{len(entries)} 组/项"
        f"（{grouped_file_records} 个文件记录）"
    )

    if not entries:
        print("  无")
        return

    for i, entry in enumerate(entries, start=1):
        print(f"  {i}. {entry.primary.path}")
        for companion in entry.companions:
            print(f"     └─ sidecar：{companion.path}")
        print(f"     原因：{entry.primary.reason}")




# -----------------------------------------------------------------------------
# 第二阶段收口整理逻辑（上方复用主 Organizer 已验证的 metadata / 媒体组辅助函数）
# -----------------------------------------------------------------------------

CATEGORY_SCREEN = "截图与录屏"
CATEGORY_DOWNLOAD = "下载与保存"
CATEGORY_UNKNOWN = "来源无法确认"
CATEGORY_TIME = "拍摄时间缺失或异常"
CATEGORY_TIMEZONE = "时区无法确认"
CATEGORY_LIVE = "Live Photo或关联关系异常"
CATEGORY_SIDECAR = "Sidecar关联异常"
CATEGORY_COMPAT = "云端格式兼容性问题"
CATEGORY_CORRUPT = "媒体可能损坏"
CATEGORY_GALLERY = "可确认相机媒体"

WHATSAPP_MEDIA_RE = re.compile(r"^(?:IMG|VID)-\d{8}-WA\d+", re.IGNORECASE)

# 第二阶段专用：只有文件名本身足够明确指向某个聊天/社交应用时才使用。
# 这些规则是白名单式强证据；不会把 image0.jpg、received_123.jpg、video.mp4
# 这类跨应用通用名称单凭文件名归到“下载与保存”。
STRONG_SOCIAL_MEDIA_FILENAME_RULES: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    (
        "WhatsApp",
        re.compile(
            r"^(?:(?:IMG|VID)-\d{8}-WA\d+|WhatsApp[_ \-]+(?:Image|Video)[_ \-]+\d{4}[-_]\d{2}[-_]\d{2}.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "WeChat/微信",
        re.compile(
            r"^(?:mmexport\d{10,17}|wx_camera_[0-9][0-9_\-]*|WeChat[_ \-].*|微信(?:图片|视频|文件)[_ \-]?.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "Telegram",
        re.compile(
            r"^(?:TG-\d{4}-\d{2}-\d{2}-\d{6,}|Telegram[_ \-].*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "QQ",
        re.compile(
            r"^(?:QQ(?:图片|视频|空间图片|空间视频)[_ \-]?\d{8,}.*|QQ[_ \-](?:Image|Photo|Video)[_ \-].*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "LINE",
        re.compile(r"^LINE[_ \-]\d{8}[_ \-]\d{6}.*$", re.IGNORECASE),
    ),
    (
        "Snapchat",
        re.compile(r"^Snapchat[-_]\d+.*$", re.IGNORECASE),
    ),
    (
        "Instagram",
        re.compile(r"^Instagram[_ \-].*$", re.IGNORECASE),
    ),
    (
        "TikTok/抖音",
        re.compile(r"^(?:TikTok|Douyin|抖音)[_ \-].*$", re.IGNORECASE),
    ),
    (
        "Discord",
        re.compile(r"^Discord[_ \-].*$", re.IGNORECASE),
    ),
    (
        "Messenger",
        re.compile(r"^(?:Facebook[_ \-]?Messenger|Messenger)[_ \-].*$", re.IGNORECASE),
    ),
    (
        "ChatGPT Image",
        re.compile(r"^ChatGPT[ _\-]+Image(?:[ _\-].*)?$", re.IGNORECASE),
    ),
    (
        "DALL·E",
        re.compile(r"^DALL(?:·|•|[-_ ]?)E(?:[ _\-].*)?$", re.IGNORECASE),
    ),
)

def strong_social_media_filename_source(path: Path) -> Optional[str]:
    stem = path.stem
    for source_name, pattern in STRONG_SOCIAL_MEDIA_FILENAME_RULES:
        if pattern.match(stem):
            return source_name
    return None

SEVERE_METADATA_RE = re.compile(
    r"(?:corrupt|corrupted|truncated|invalid data|damaged|unexpected end|read error|解码失败|损坏|截断)",
    re.IGNORECASE,
)


@dataclass
class SecondaryGroup:
    primary: Path
    members: List[Path]
    media_members: List[Path]
    sidecar_members: List[Path]
    relation_ambiguous: bool = False
    relation_notes: List[str] = field(default_factory=list)
    organizer_group: Optional[MoveGroup] = None


@dataclass
class SecondaryDecision:
    key: str
    label: str
    reason: str
    timestamp: Optional[Timestamp]
    use_date: bool
    organizer_group: Optional[MoveGroup] = None


@dataclass
class SecondaryPlan:
    group: SecondaryGroup
    decision: SecondaryDecision
    mappings: List[Tuple[Path, Path]]
    placement: str
    note: Optional[str] = None


@dataclass
class GlobalDuplicateMatch:
    reason: str
    signature: Tuple[Tuple[str, int, str], ...]
    matched_gallery: bool = False
    matched_source_group: Optional[Path] = None


def secondary_parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="整理主 Organizer 留在原地的媒体；默认 DRY RUN。"
    )
    parser.add_argument("folder", nargs="?", help="要处理的来源目录")
    parser.add_argument("--apply", action="store_true", help="真正移动文件")
    parser.add_argument("--batch-size", type=int, default=250, help="ExifTool 批次大小（默认 250）")
    return parser.parse_args()


def iter_secondary_candidate_files(source_root: Path) -> Iterator[Path]:
    excluded_roots = [
        OTHER_MEDIA_ROOT.resolve(strict=False),
        REPAIR_MEDIA_ROOT.resolve(strict=False),
        DESTINATION_ROOT.resolve(strict=False),
        DUPLICATE_ROOT.resolve(strict=False),
        SCREEN_ROOT.resolve(strict=False),
    ]

    def excluded(path: Path) -> bool:
        resolved = path.resolve(strict=False)
        return any(path_is_within(resolved, root) for root in excluded_roots)

    stack = [source_root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue

        for path in entries:
            try:
                if path.is_symlink():
                    continue
                if excluded(path):
                    continue
                if path.is_dir():
                    if path.name.casefold() in SYSTEM_EXCLUDED_DIR_NAMES:
                        continue
                    stack.append(path)
                    continue
                if not path.is_file():
                    continue
                if path.name.startswith("._") or path.name.casefold() in SYSTEM_EXCLUDED_FILE_NAMES:
                    continue
                if classify(path) in ("photo", "video", "sidecar"):
                    yield path
            except OSError:
                continue


def _component_timestamp(media: Sequence[MediaInfo]) -> Optional[Timestamp]:
    timestamps = [info.timestamp for info in media if info.timestamp is not None]
    if not timestamps:
        return None
    anchor = timestamps[0]
    # 同组日期如果相差超过一天，就不要用它来建立统一年月目录。
    for other in timestamps[1:]:
        diff = timestamp_difference_seconds(anchor, other)
        if diff is None or diff > 86400:
            return None
    return anchor


def _metadata_error_text(metadata: dict) -> str:
    values: List[str] = []
    for key, value in metadata.items():
        if plain_tag_name(key) not in {"Error", "Warning"}:
            continue
        if isinstance(value, (str, int, float)):
            values.append(str(value))
        elif isinstance(value, (list, tuple)):
            values.extend(str(item) for item in value)
    return "\n".join(values)


def _group_has_strong_screen_evidence(group: SecondaryGroup, infos: Dict[Path, MediaInfo], source_root: Path) -> Tuple[bool, str]:
    reasons: List[str] = []
    for path in group.media_members:
        info = infos[path]
        if info.kind == "photo" and is_likely_screenshot(path, source_root):
            reasons.append(f"{path.name}: 截图文件名/目录")
            continue
        if info.kind == "video" and SCREEN_RECORDING_NAME_RE.search(path.stem):
            reasons.append(f"{path.name}: 屏幕录制文件名")
            continue
        text = metadata_text_for_source_detection(info.metadata)
        if text and SCREEN_CAPTURE_METADATA_RE.search(text):
            reasons.append(f"{path.name}: metadata 明确写有 Screenshot/Screen Recording")
    return bool(reasons), "；".join(reasons)


def _group_has_strong_download_evidence(group: SecondaryGroup, infos: Dict[Path, MediaInfo]) -> Tuple[bool, str]:
    """
    第二阶段的“下载与保存”只接受强来源证据。

    重要：Apple Photos / 未知 quarantine 在主 Organizer 中被定义为中性，
    不能因为 evidence 中存在“quarantine agent=Photos”就被降级成下载媒体。
    """
    reasons: List[str] = []
    for path in group.media_members:
        info = infos[path]

        # 文件名本身明确指向聊天/社交应用时，可作为第二阶段强来源证据。
        # 这里只使用严格白名单，不接受 image0 / received / video 等通用命名。
        social_source = strong_social_media_filename_source(path)
        if social_source is not None:
            reasons.append(f"{path.name}: {social_source} 明确媒体文件名")
            continue

        # 其他下载/网页/聊天来源必须已经被主来源判定标为强负面 EXCLUDED。
        # CLEAN（包括 Apple Photos quarantine）和仅目录可疑的 AMBIGUOUS 都不能
        # 单独进入“下载与保存”。
        if info.source_state != SOURCE_STATE_EXCLUDED:
            continue

        reason_text = " ".join([info.source_reason] + info.source_evidence).casefold()
        if any(token in reason_text for token in (
            "wherefroms",
            "网络下载",
            "网络来源",
            "sourceurl",
            "浏览器/聊天",
            "浏览器",
            "聊天应用",
        )):
            reasons.append(f"{path.name}: {info.source_reason}")

    return bool(reasons), "；".join(reasons)


def _group_has_cloud_format_problem(group: SecondaryGroup, infos: Dict[Path, MediaInfo]) -> Tuple[bool, str]:
    reasons: List[str] = []
    for path in group.media_members:
        info = infos[path]
        metadata = info.metadata
        mime_values = metadata_scalar_values_for(metadata, "MIMEType")
        ext_values = metadata_scalar_values_for(metadata, "FileTypeExtension")
        mime = mime_values[0].casefold() if mime_values else ""
        reported_ext = ext_values[0].casefold().lstrip(".") if ext_values else ""
        actual_ext = path.suffix.casefold().lstrip(".")

        if info.kind == "photo" and mime and not mime.startswith("image/"):
            reasons.append(f"{path.name}: 扩展名是图片但 ExifTool MIMEType={mime}")
        elif info.kind == "video" and mime and not (mime.startswith("video/") or "quicktime" in mime):
            reasons.append(f"{path.name}: 扩展名是视频但 ExifTool MIMEType={mime}")

        aliases = {
            ("jpg", "jpeg"), ("jpeg", "jpg"),
            ("heic", "heif"), ("heif", "heic"),
            ("tif", "tiff"), ("tiff", "tif"),
            ("mov", "qt"),
        }
        if reported_ext and actual_ext and reported_ext != actual_ext and (actual_ext, reported_ext) not in aliases:
            reasons.append(f"{path.name}: 扩展名 .{actual_ext} 与 ExifTool FileTypeExtension=.{reported_ext} 不一致")
    return bool(reasons), "；".join(reasons)


def _group_has_corruption_evidence(group: SecondaryGroup, infos: Dict[Path, MediaInfo]) -> Tuple[bool, str]:
    reasons: List[str] = []
    for path in group.members:
        text = _metadata_error_text(infos[path].metadata)
        if text and SEVERE_METADATA_RE.search(text):
            reasons.append(f"{path.name}: {text}")
    return bool(reasons), "；".join(reasons)


def build_secondary_groups(
    infos: Dict[Path, MediaInfo],
    still_to_video: Dict[Path, Path],
    variant_family_map: Dict[Path, Set[Path]],
    organizer_groups: Sequence[MoveGroup],
    *,
    sidecar_lookup: Optional[SidecarLookupIndex] = None,
    sidecar_resolutions: Optional[Dict[Path, SidecarResolution]] = None,
) -> List[SecondaryGroup]:
    paths = list(infos)
    parent: Dict[Path, Path] = {path: path for path in paths}

    def find(path: Path) -> Path:
        root = path
        while parent[root] != root:
            root = parent[root]
        while parent[path] != path:
            nxt = parent[path]
            parent[path] = root
            path = nxt
        return root

    def union(a: Path, b: Path) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 强 Live/Motion Photo 关系。
    for still, video in still_to_video.items():
        if still in parent and video in parent:
            union(still, video)

    # Apple 多格式照片关系。
    seen_families: Set[frozenset[Path]] = set()
    for family in variant_family_map.values():
        actual = frozenset(path for path in family if path in parent)
        if len(actual) < 2 or actual in seen_families:
            continue
        seen_families.add(actual)
        items = list(actual)
        for other in items[1:]:
            union(items[0], other)

    # 第二阶段对“疑似但未确认”的媒体关系更保守：不拆散，整组送入关系异常。
    ambiguous_media_notes: Dict[Path, str] = {}

    photo_stem_groups: Dict[Tuple[Path, str], List[Path]] = defaultdict(list)
    mixed_stem_groups: Dict[Tuple[Path, str], List[Path]] = defaultdict(list)
    for path, info in infos.items():
        if info.kind == "photo" and path.suffix.casefold() in APPLE_VARIANT_PHOTO_EXTENSIONS:
            photo_stem_groups[(path.parent, path.stem.casefold())].append(path)
        if info.kind in ("photo", "video"):
            mixed_stem_groups[(path.parent, path.stem.casefold())].append(path)

    for candidates in photo_stem_groups.values():
        if len(candidates) < 2:
            continue
        roots = {find(path) for path in candidates}
        if len(roots) == 1:
            continue
        note = "Apple 同 stem 多格式照片未能通过强关系确认"
        anchor = candidates[0]
        for other in candidates[1:]:
            union(anchor, other)
        for path in candidates:
            ambiguous_media_notes[path] = note

    confirmed_live_pairs = {frozenset((still, video)) for still, video in still_to_video.items()}
    for candidates in mixed_stem_groups.values():
        photos = [p for p in candidates if infos[p].kind == "photo"]
        videos = [p for p in candidates if infos[p].kind == "video"]
        if not photos or not videos:
            continue
        if len(photos) == 1 and len(videos) == 1 and frozenset((photos[0], videos[0])) in confirmed_live_pairs:
            continue
        roots = {find(path) for path in candidates}
        if len(roots) == 1:
            continue
        note = "同 stem 照片+视频未能通过 Live/Motion Photo 强关系确认"
        anchor = candidates[0]
        for other in candidates[1:]:
            union(anchor, other)
        for path in candidates:
            ambiguous_media_notes[path] = note

    content_cache: Dict[Path, SidecarContentEvidence] = {}
    ambiguous_sidecars: Dict[Path, str] = {}
    lookup = sidecar_lookup or build_sidecar_lookup_index(infos)
    resolutions = sidecar_resolutions
    if resolutions is None:
        resolutions, _by_target, _ = build_sidecar_resolution_index(
            infos, content_cache, lookup_index=lookup, show_progress=False
        )

    for path, info in infos.items():
        if info.kind != "sidecar":
            continue
        resolution = resolutions[path]
        targets = [target for target in resolution.targets if target in parent]
        if targets:
            union(path, targets[0])
            for target in targets[1:]:
                union(targets[0], target)
        if resolution.status == "ambiguous":
            ambiguous_sidecars[path] = resolution.reason

    components: Dict[Path, List[Path]] = defaultdict(list)
    for path in paths:
        components[find(path)].append(path)

    organizer_by_members: Dict[frozenset[Path], MoveGroup] = {
        frozenset(group.members): group for group in organizer_groups
    }

    result: List[SecondaryGroup] = []
    for members in components.values():
        members = sorted(members, key=lambda p: (str(p.parent).casefold(), p.name.casefold()))
        media = [p for p in members if infos[p].kind in ("photo", "video")]
        sidecars = [p for p in members if infos[p].kind == "sidecar"]
        primary = media[0] if media else members[0]
        notes = [ambiguous_sidecars[p] for p in sidecars if p in ambiguous_sidecars]
        notes.extend(
            note for p, note in ambiguous_media_notes.items()
            if p in members and note not in notes
        )
        exact = organizer_by_members.get(frozenset(members))
        result.append(
            SecondaryGroup(
                primary=primary,
                members=members,
                media_members=media,
                sidecar_members=sidecars,
                relation_ambiguous=bool(notes),
                relation_notes=notes,
                organizer_group=exact,
            )
        )

    return sorted(result, key=lambda g: str(g.primary).casefold())



def _camera_like_for_repair(info: MediaInfo) -> bool:
    """比主 Organizer 的自动放行更宽一层，只用于“待修复”归类，不用于直接进 Gallery。"""
    if info.kind not in ("photo", "video"):
        return False
    if info.source_state == SOURCE_STATE_EXCLUDED:
        return False
    if info.camera_origin:
        return True
    if info.kind == "photo" and info.path.suffix.casefold() in NON_CAMERA_AUTO_ACCEPT_PHOTO_EXTENSIONS:
        return False

    if APPLE_IMG_RE.fullmatch(info.path.stem):
        if is_apple_device_metadata(info.metadata):
            return True

    prefix = android_filename_prefix(info.path)
    if info.filename_timestamp is not None:
        if info.kind == "photo" and prefix in ("IMG", "MVIMG", "PANO"):
            return True
        if info.kind == "video" and prefix in ("VID", "MVIMG"):
            return True

    # 自定义名称但仍保留真实相机设备字段，也视作“可能是相机生成”，
    # 只是缺时间/时间异常时进入待修复，而不是直接放行。
    if has_device_identity(info.metadata):
        return True
    return False


def decide_secondary_category(
    group: SecondaryGroup,
    infos: Dict[Path, MediaInfo],
    source_root: Path,
) -> SecondaryDecision:
    media_infos = [infos[p] for p in group.media_members]
    timestamp = _component_timestamp(media_infos)

    screen, screen_reason = _group_has_strong_screen_evidence(group, infos, source_root)
    if screen:
        return SecondaryDecision("screen", CATEGORY_SCREEN, screen_reason, timestamp, timestamp is not None)

    download, download_reason = _group_has_strong_download_evidence(group, infos)
    if download:
        return SecondaryDecision("download", CATEGORY_DOWNLOAD, download_reason, timestamp, timestamp is not None)

    corrupt, corrupt_reason = _group_has_corruption_evidence(group, infos)
    if corrupt:
        return SecondaryDecision("corrupt", CATEGORY_CORRUPT, corrupt_reason, timestamp, timestamp is not None)

    if not group.media_members:
        return SecondaryDecision(
            "sidecar", CATEGORY_SIDECAR,
            "没有任何可确认主媒体；作为真正孤立 sidecar 整组收口",
            timestamp, False,
        )

    if group.relation_ambiguous:
        has_photo = any(infos[p].kind == "photo" for p in group.media_members)
        has_video = any(infos[p].kind == "video" for p in group.media_members)
        has_apple_aae = any(
            p.suffix.casefold() == ".aae" and APPLE_ORIGINAL_ADJUSTMENT_AAE_RE.fullmatch(p.stem)
            for p in group.sidecar_members
        )
        has_apple_relation_note = any("Apple" in note or "Live/Motion" in note for note in group.relation_notes)
        if (has_photo and has_video) or has_apple_aae or has_apple_relation_note:
            return SecondaryDecision(
                "live", CATEGORY_LIVE,
                "媒体/sidecar 关系无法唯一确认：" + " | ".join(group.relation_notes),
                timestamp, timestamp is not None,
            )
        return SecondaryDecision(
            "sidecar", CATEGORY_SIDECAR,
            "sidecar 归属无法唯一确认：" + " | ".join(group.relation_notes),
            timestamp, timestamp is not None,
        )

    compat, compat_reason = _group_has_cloud_format_problem(group, infos)
    if compat:
        return SecondaryDecision("compat", CATEGORY_COMPAT, compat_reason, timestamp, timestamp is not None)

    # 若当前主 Organizer 已经能完整确认这一整组，则不把它降级成“其他图片”，直接按主图库规则处理。
    if group.organizer_group is not None:
        return SecondaryDecision(
            "gallery", CATEGORY_GALLERY,
            "当前主 Organizer 可以完整确认该媒体组；按主图库原规则处理",
            timestamp, True,
            organizer_group=group.organizer_group,
        )

    camera_evidence = any(_camera_like_for_repair(info) for info in media_infos)
    if camera_evidence:
        # Android 标准视频已有文件名本地时间，但强 metadata 验证失败，优先视为时区/容器时间问题。
        android_failed = [
            info for info in media_infos
            if info.kind == "video"
            and info.filename_timestamp is not None
            and android_filename_prefix(info.path) in ("VID", "MVIMG")
            and not info.android_video_time_verified
        ]
        if android_failed and any(info.timestamp is not None for info in android_failed):
            details = "；".join(
                f"{info.path.name}: {info.android_video_time_reason or 'Android 视频强时间验证失败'}"
                for info in android_failed
            )
            return SecondaryDecision("timezone", CATEGORY_TIMEZONE, details, None, False)

        missing_time = [info.path.name for info in media_infos if info.timestamp is None]
        conflict_time = [info.path.name for info in media_infos if info.filename_timestamp_conflict]
        if missing_time or conflict_time:
            details: List[str] = []
            if missing_time:
                details.append("缺可靠内嵌拍摄时间：" + ", ".join(missing_time))
            if conflict_time:
                details.append("文件名时间与内嵌时间冲突：" + ", ".join(conflict_time))
            return SecondaryDecision("time", CATEGORY_TIME, "；".join(details), None, False)

    # 其余媒体本身没有确认到会影响云端相册导入的技术问题，只是来源/是否属于本人相机图库无法证明。
    reason_parts: List[str] = []
    for info in media_infos:
        if info.camera_origin_reason:
            reason_parts.append(f"{info.path.name}: {info.camera_origin_reason}")
        if info.source_state == SOURCE_STATE_AMBIGUOUS:
            reason_parts.append(f"{info.path.name}: 来源证据中等不确定（{info.source_reason}）")
    return SecondaryDecision(
        "unknown", CATEGORY_UNKNOWN,
        "；".join(reason_parts) or "媒体可读取，未发现明确云端兼容问题，但无法证明属于主相机图库",
        timestamp, timestamp is not None,
    )


def secondary_category_root(decision: SecondaryDecision) -> Path:
    if decision.key == "screen":
        return SCREEN_ROOT
    if decision.key == "download":
        return OTHER_MEDIA_ROOT / CATEGORY_DOWNLOAD
    if decision.key == "unknown":
        return OTHER_MEDIA_ROOT / CATEGORY_UNKNOWN
    if decision.key == "time":
        return REPAIR_MEDIA_ROOT / CATEGORY_TIME
    if decision.key == "timezone":
        return REPAIR_MEDIA_ROOT / CATEGORY_TIMEZONE
    if decision.key == "live":
        return REPAIR_MEDIA_ROOT / CATEGORY_LIVE
    if decision.key == "sidecar":
        return REPAIR_MEDIA_ROOT / CATEGORY_SIDECAR
    if decision.key == "compat":
        return REPAIR_MEDIA_ROOT / CATEGORY_COMPAT
    if decision.key == "corrupt":
        return REPAIR_MEDIA_ROOT / CATEGORY_CORRUPT
    raise ValueError(f"未知第二阶段类别：{decision.key}")


def _secondary_duplicate_anchor_members(group: SecondaryGroup) -> List[Path]:
    """
    全局重复判断遵循“组”的概念：
    - 有主媒体：只用全部主媒体内容构成重复签名，sidecar 不参与“是否重复”的判定，
      但一旦确认该媒体组重复，sidecar 会与整组一起隔离，避免丢失独有编辑信息。
    - 纯 sidecar 孤立组：用全部 sidecar 内容构成签名。
    """
    return list(group.media_members or group.members)


def _iter_canonical_duplicate_candidates() -> Iterator[Path]:
    """扫描正式 Gallery + 已规范分类区；绝不进入第二阶段重复目录。"""
    roots = [
        DESTINATION_ROOT.resolve(strict=False),
        OTHER_MEDIA_ROOT.resolve(strict=False),
        REPAIR_MEDIA_ROOT.resolve(strict=False),
    ]
    stack = [root for root in roots if root.exists()]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if path.is_symlink():
                    continue
                if path.is_dir():
                    if path.name.casefold() in SYSTEM_EXCLUDED_DIR_NAMES:
                        continue
                    stack.append(path)
                    continue
                if not path.is_file():
                    continue
                if path.name.startswith("._") or path.name.casefold() in SYSTEM_EXCLUDED_FILE_NAMES:
                    continue
                if classify(path) in ("photo", "video", "sidecar"):
                    yield path
            except OSError:
                continue


def _safe_file_size(path: Path) -> Optional[int]:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _secondary_duplicate_class(path: Path) -> str:
    return "sidecar" if classify(path) == "sidecar" else "media"


def build_secondary_global_duplicate_index(
    groups: Sequence[SecondaryGroup],
) -> Tuple[Dict[int, GlobalDuplicateMatch], List[str]]:
    """
    建立第二阶段全局内容重复索引。

    为避免大量无意义 SHA-256：
    1) 先只读取文件大小；
    2) 只有“本次来源中同类型同大小出现多次”或“正式 Gallery/已规范分类区存在同类型同大小文件”时才 hash 来源；
    3) canonical 区也只 hash 来源实际需要比较的大小桶。

    重复规则：
    - 有主媒体的组：全部主媒体的 (类型, size, SHA-256) 多重集合必须整体匹配；
    - 纯 sidecar 组：全部成员整体匹配；
    - 如果正式 Gallery / 其他图片 / 待修复媒体已覆盖完整签名，整组视为重复；
    - 否则，本次扫描中后出现的完整相同签名组视为重复，路径排序最前的首组作为 canonical 组。
    """
    warnings: List[str] = []
    matches: Dict[int, GlobalDuplicateMatch] = {}

    anchors_by_group: Dict[int, List[Path]] = {
        id(group): _secondary_duplicate_anchor_members(group) for group in groups
    }
    source_sizes: Dict[Path, int] = {}
    source_bucket_counts: Counter[Tuple[str, int]] = Counter()
    for anchors in anchors_by_group.values():
        for path in anchors:
            size = _safe_file_size(path)
            if size is None:
                warnings.append(f"无法读取文件大小，跳过全局重复预检：{path}")
                continue
            source_sizes[path] = size
            source_bucket_counts[(_secondary_duplicate_class(path), size)] += 1

    canonical_by_bucket: Dict[Tuple[str, int], List[Path]] = defaultdict(list)
    for path in _iter_canonical_duplicate_candidates():
        size = _safe_file_size(path)
        if size is None:
            continue
        bucket = (_secondary_duplicate_class(path), size)
        if bucket in source_bucket_counts:
            canonical_by_bucket[bucket].append(path)

    source_hashes: Dict[Path, str] = {}
    source_hash_candidates = [
        path for path, size in source_sizes.items()
        if source_bucket_counts[(_secondary_duplicate_class(path), size)] > 1
        or bool(canonical_by_bucket.get((_secondary_duplicate_class(path), size)))
    ]
    source_hash_candidates.sort(key=lambda p: str(p).casefold())
    total_source_hash = len(source_hash_candidates)
    if total_source_hash:
        print_stage_progress("重复预检", 0, total_source_hash, "计算来源候选 SHA-256")
    for index, path in enumerate(source_hash_candidates, start=1):
        try:
            source_hashes[path] = sha256_file(path)
        except Exception as exc:
            warnings.append(f"来源 SHA-256 失败，保持保守：{path}：{exc}")
        print_stage_progress("重复预检", index, total_source_hash, path.name)
    finish_stage_progress()

    needed_buckets = {
        (_secondary_duplicate_class(path), source_sizes[path]) for path in source_hashes
    }
    canonical_hash_counts: Counter[Tuple[str, int, str]] = Counter()
    canonical_hash_candidates = [
        path for bucket in sorted(needed_buckets)
        for path in sorted(canonical_by_bucket.get(bucket, []), key=lambda p: str(p).casefold())
    ]
    total_canonical_hash = len(canonical_hash_candidates)
    if total_canonical_hash:
        print_stage_progress("Canonical 判重", 0, total_canonical_hash, "计算 Gallery/规范分类区候选 SHA-256")
    for index, path in enumerate(canonical_hash_candidates, start=1):
        size = _safe_file_size(path)
        if size is None:
            warnings.append(f"Canonical 文件大小读取失败，跳过：{path}")
        else:
            try:
                canonical_hash_counts[
                    (_secondary_duplicate_class(path), size, sha256_file(path))
                ] += 1
            except Exception as exc:
                warnings.append(f"Canonical SHA-256 失败，跳过：{path}：{exc}")
        print_stage_progress("Canonical 判重", index, total_canonical_hash, path.name)
    finish_stage_progress()

    seen_source_signatures: Dict[Tuple[Tuple[str, int, str], ...], SecondaryGroup] = {}

    # 路径排序固定 canonical 选择，保证 DRY RUN / APPLY 结果稳定。
    ordered_groups = sorted(groups, key=lambda g: str(g.primary).casefold())
    for group in ordered_groups:
        anchors = anchors_by_group[id(group)]
        if not anchors:
            continue
        signature_parts: List[Tuple[str, int, str]] = []
        complete = True
        for path in anchors:
            size = source_sizes.get(path)
            digest = source_hashes.get(path)
            if size is None or digest is None:
                complete = False
                break
            signature_parts.append((_secondary_duplicate_class(path), size, digest))
        if not complete:
            continue
        signature = tuple(sorted(signature_parts))

        # 必须按多重集合完整覆盖整个媒体签名，不能只因其中一个 Live Photo 成员重复就拆组。
        required = Counter(signature)
        canonical_covered = all(canonical_hash_counts[key] >= count for key, count in required.items())
        if canonical_covered:
            matches[id(group)] = GlobalDuplicateMatch(
                reason=(
                    "该媒体组全部主媒体内容已在正式 Gallery 或已规范分类区中存在"
                    "（类型 + 大小 + SHA-256 完整匹配）"
                ),
                signature=signature,
                matched_gallery=True,
            )
            seen_source_signatures.setdefault(signature, group)
            continue

        canonical = seen_source_signatures.get(signature)
        if canonical is not None:
            matches[id(group)] = GlobalDuplicateMatch(
                reason=(
                    "与本次扫描中另一媒体组的全部主媒体内容完全一致（类型 + 大小 + SHA-256）；"
                    f"canonical 组：{canonical.primary}"
                ),
                signature=signature,
                matched_source_group=canonical.primary,
            )
        else:
            seen_source_signatures[signature] = group

    return matches, warnings



def _group_bundle_hash8(group: SecondaryGroup) -> str:
    """Duplicate/Other 目录用：包含组内所有成员内容，保留 sidecar 差异。"""
    digest = hashlib.sha256()
    for path in sorted(group.members, key=lambda p: (p.name.casefold(), str(p).casefold())):
        digest.update(path.name.casefold().encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()[:8]


def _global_duplicate_base_directory(group: SecondaryGroup, decision: SecondaryDecision) -> Path:
    root = SECOND_STAGE_DUPLICATE_ROOT / decision.label
    try:
        hash_part = f"SHA256-{_group_bundle_hash8(group)}"
    except Exception:
        # 这里不会覆盖/删除；哈希失败交给调用方作为规划失败处理。
        raise
    if decision.timestamp is not None and decision.use_date:
        year, month, day = decision.timestamp[0], decision.timestamp[1], decision.timestamp[2]
        return root / f"{year:04d}" / f"{year:04d}-{month:02d}" / f"{year:04d}-{month:02d}-{day:02d}" / hash_part
    return root / "日期未知" / hash_part


def plan_global_duplicate_group(
    group: SecondaryGroup,
    decision: SecondaryDecision,
    reserved: Set[str],
) -> Tuple[bool, List[Tuple[Path, Path]], str]:
    """
    将整个已确认重复媒体组隔离到第二阶段重复目录。
    不改文件名、不覆盖；如果同一 hash 目录已有一份，则使用 COPY-2/COPY-3... 目录保存额外副本。
    """
    try:
        base = _global_duplicate_base_directory(group, decision)
    except Exception as exc:
        return False, [], f"重复组完整内容哈希失败：{exc}"

    def key(path: Path) -> str:
        return str(path.resolve(strict=False)).casefold()

    seen_names: Set[str] = set()
    for member in group.members:
        nk = member.name.casefold()
        if nk in seen_names:
            return False, [], f"同一媒体组存在大小写等价的重复文件名：{member.name}"
        seen_names.add(nk)

    # 不限制一个合理的小次数；极端情况下 10,000 份相同副本也不会覆盖。
    for copy_no in range(1, 10001):
        directory = base if copy_no == 1 else base / f"COPY-{copy_no}"
        mappings = [(source, directory / source.name) for source in group.members]
        blocked = False
        for source, destination in mappings:
            if key(destination) in reserved or destination.exists():
                blocked = True
                break
        if blocked:
            continue
        return True, mappings, "duplicate-other" if copy_no == 1 else f"duplicate-other-copy-{copy_no}"

    return False, [], "重复隔离目录已存在过多同名副本，停止以避免覆盖"


def _group_hash8(group: SecondaryGroup) -> str:
    # 使用主文件内容哈希作为最终撞名目录标识；不改文件名。
    candidates = group.media_members or group.members
    digest = hashlib.sha256()
    for path in sorted(candidates, key=lambda p: p.name.casefold()):
        digest.update(path.name.casefold().encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()[:8]


def _standard_category_directories(group: SecondaryGroup, decision: SecondaryDecision) -> List[Tuple[str, Path]]:
    # 这里只生成无需读取文件内容的候选目录；SHA-256 兜底在真正发生撞名后再延迟计算，
    # 避免大量正常文件为了“可能永远用不到的 hash 目录”提前整文件哈希。
    root = secondary_category_root(decision)
    ts = decision.timestamp if decision.use_date else None
    if ts is None:
        return [("unknown-date", root / "日期未知")]

    year, month, day = ts[0], ts[1], ts[2]
    month_dir = root / f"{year:04d}" / f"{year:04d}-{month:02d}"
    day_dir = month_dir / f"{year:04d}-{month:02d}-{day:02d}"
    return [("month", month_dir), ("day", day_dir)]


def _second_stage_duplicate_destination(source: Path, decision: SecondaryDecision, group: SecondaryGroup) -> Path:
    ts = decision.timestamp if decision.use_date else None
    root = SECOND_STAGE_DUPLICATE_ROOT / decision.label
    hash_part = f"SHA256-{_group_hash8(group)}"
    if ts is None:
        directory = root / "日期未知" / hash_part
    else:
        year, month, day = ts[0], ts[1], ts[2]
        directory = (
            root / f"{year:04d}" / f"{year:04d}-{month:02d}"
            / f"{year:04d}-{month:02d}-{day:02d}" / hash_part
        )
    return directory / source.name


def plan_secondary_category_group(
    group: SecondaryGroup,
    decision: SecondaryDecision,
    reserved: Set[str],
) -> Tuple[bool, List[Tuple[Path, Path]], str, Optional[str]]:
    def reservation_key(path: Path) -> str:
        return str(path.resolve(strict=False)).casefold()

    def evaluate(directory: Path) -> Tuple[str, List[Tuple[Path, Path]], Optional[str]]:
        mappings: List[Tuple[Path, Path]] = []
        duplicate_notes: List[str] = []
        conflicts: List[str] = []
        seen_names: Set[str] = set()

        for source in group.members:
            destination = directory / source.name
            name_key = source.name.casefold()
            if name_key in seen_names:
                return "error", [], f"同一媒体组存在大小写等价的重复文件名：{source.name}"
            seen_names.add(name_key)

            if reservation_key(destination) in reserved:
                conflicts.append(f"本次运行已有其他组占用：{destination}")
                continue

            if not destination.exists():
                mappings.append((source, destination))
                continue

            identical, detail = compare_existing_destination_content(source, destination)
            if identical is None:
                return "error", [], detail
            if not identical:
                conflicts.append(f"目标同名但内容不同：{destination}；{detail}")
                continue

            duplicate_destination = _second_stage_duplicate_destination(source, decision, group)
            if reservation_key(duplicate_destination) in reserved:
                return "error", [], f"重复隔离路径已被本次运行占用：{duplicate_destination}"
            if duplicate_destination.exists():
                dup_identical, dup_detail = compare_existing_destination_content(source, duplicate_destination)
                if dup_identical is None:
                    return "error", [], dup_detail
                return "error", [], (
                    "重复隔离路径已经存在文件；本脚本不删除、不覆盖："
                    f"{duplicate_destination}；{dup_detail}"
                )
            mappings.append((source, duplicate_destination))
            duplicate_notes.append(f"{source.name}: 分类目标已有完全相同内容，隔离至 {SECOND_STAGE_DUPLICATE_ROOT}")

        if conflicts and duplicate_notes:
            return "error", [], "同组同时存在完全重复成员和同名不同内容冲突；整组停止。" + " | ".join(conflicts + duplicate_notes)
        if conflicts:
            return "conflict", [], " | ".join(conflicts)
        return "ok", mappings, " | ".join(duplicate_notes) if duplicate_notes else None

    last_conflict: Optional[str] = None
    candidates = _standard_category_directories(group, decision)
    for placement, directory in candidates:
        status, mappings, note = evaluate(directory)
        if status == "ok":
            return True, mappings, placement, note
        if status == "error":
            return False, mappings, placement, note
        last_conflict = note

    # 只有月份/日期（或日期未知根目录）确实发生同名不同内容冲突，才计算整组 SHA-256。
    # 这是最后一级“保持原文件名但避免撞名”的标准化目录。
    base_dir = candidates[-1][1] if candidates else secondary_category_root(decision) / "日期未知"
    try:
        hash_dir = base_dir / f"SHA256-{_group_hash8(group)}"
    except Exception as exc:
        return False, [], "hash-error", f"最终撞名兜底 SHA-256 计算失败：{exc}"
    status, mappings, note = evaluate(hash_dir)
    if status == "ok":
        return True, mappings, "hash", note
    if status == "error":
        return False, mappings, "hash", note
    return False, [], "conflict", note or last_conflict or "所有标准冲突兜底目录均不可用"


def run_secondary(args: argparse.Namespace) -> int:
    source_root = Path(args.folder).expanduser().resolve()
    backup_root = BACKUP_APPLE_ROOT.resolve(strict=False)
    run_started = time.perf_counter()
    stage_timings: Dict[str, float] = {}
    _XATTR_NAME_CACHE.clear()
    _SHA256_CACHE.hits = 0
    _SHA256_CACHE.misses = 0
    _SHA256_CACHE.errors = 0
    if not source_root.exists() or not source_root.is_dir():
        print(f"错误：来源目录不存在或不是目录：{source_root}", file=sys.stderr)
        return 2
    if not path_is_within(source_root, backup_root):
        print(f"错误：来源目录必须位于 {BACKUP_APPLE_ROOT} 内。", file=sys.stderr)
        return 2

    exiftool = require_exiftool()
    print("=" * 78)
    print("Leftover Media Organizer · 第二阶段收口")
    print(f"脚本版本：{SCRIPT_VERSION}")
    print(f"来源目录：{source_root}")
    print(f"其他图片：{OTHER_MEDIA_ROOT}")
    print(f"待修复媒体：{REPAIR_MEDIA_ROOT}")
    print(f"重复隔离：{SECOND_STAGE_DUPLICATE_ROOT}")
    print("模式：" + ("实际移动 --apply" if args.apply else "DRY RUN（只预览，不移动）"))
    print("原则：先恢复媒体组，再穷尽式分类；不改名、不覆盖；真正安全失败才允许原地残留。")
    print("性能：采用主 Organizer 1854 同款 sidecar 线性索引；不按媒体组重复扫描 sidecar/目录。")
    print(f"SHA-256：完整哈希判重；持久缓存 {HASH_CACHE_DB}（文件状态变化自动失效）")
    print("xattr：每个文件先单次列属性名，仅在实际存在 WhereFroms/quarantine 时读取。")
    print("=" * 78)
    print()

    stage_started = time.perf_counter()
    paths = list(iter_secondary_candidate_files(source_root))
    stage_timings["扫描文件"] = time.perf_counter() - stage_started
    media_paths = [p for p in paths if classify(p) in ("photo", "video")]
    sidecar_paths = [p for p in paths if classify(p) == "sidecar"]
    print(f"发现主媒体：{len(media_paths)}")
    print(f"发现 sidecar：{len(sidecar_paths)}")

    metadata_paths = media_paths + sidecar_paths
    stage_started = time.perf_counter()
    metadata_map: Dict[str, dict] = {}
    metadata_batches = list(chunks(metadata_paths, args.batch_size))
    done = 0
    if metadata_paths:
        print_stage_progress("元数据", 0, len(metadata_paths), "准备读取 ExifTool metadata")
    for batch_no, batch in enumerate(metadata_batches, start=1):
        try:
            batch_map = read_metadata_batch(exiftool, batch)
            metadata_map.update(batch_map)
            done += len(batch)
            print_stage_progress("元数据", done, len(metadata_paths), f"第 {batch_no}/{len(metadata_batches)} 批")
        except Exception as exc:
            finish_stage_progress()
            print(f"[失败] ExifTool 批次读取失败：{exc}", file=sys.stderr)
            return 3
    finish_stage_progress()
    stage_timings["ExifTool metadata"] = time.perf_counter() - stage_started

    stage_started = time.perf_counter()
    infos = build_media_info(metadata_paths, metadata_map, source_root, show_progress=True)
    finish_stage_progress()
    stage_timings["来源/xattr与媒体分析"] = time.perf_counter() - stage_started

    stage_started = time.perf_counter()
    still_to_video, _video_to_still = pair_live_photos(infos)
    variant_family_map, variant_warnings, _variant_confirmed, _variant_ambiguous = build_photo_variant_families(infos)
    shared_sidecar_cache: Dict[Path, SidecarContentEvidence] = {}
    shared_sidecar_lookup = build_sidecar_lookup_index(infos)
    shared_sidecar_resolutions, shared_sidecars_by_target, _ = build_sidecar_resolution_index(
        infos, shared_sidecar_cache, lookup_index=shared_sidecar_lookup, show_progress=True
    )
    finish_stage_progress()
    organizer_groups, organizer_problems = build_groups(
        infos, still_to_video, source_root, variant_family_map,
        precomputed_sidecar_lookup=shared_sidecar_lookup,
        precomputed_sidecar_resolutions=shared_sidecar_resolutions,
        precomputed_sidecars_by_target=shared_sidecars_by_target,
    )
    groups = build_secondary_groups(
        infos, still_to_video, variant_family_map, organizer_groups,
        sidecar_lookup=shared_sidecar_lookup,
        sidecar_resolutions=shared_sidecar_resolutions,
    )
    stage_timings["媒体组恢复/关联"] = time.perf_counter() - stage_started

    stage_started = time.perf_counter()
    global_duplicate_matches, duplicate_warnings = build_secondary_global_duplicate_index(groups)
    stage_timings["全局重复预检"] = time.perf_counter() - stage_started

    print(f"恢复媒体组/独立项：{len(groups)}")
    print(f"全局确认重复媒体组/项：{len(global_duplicate_matches)}")
    if duplicate_warnings:
        print(f"重复预检保守跳过/警告：{len(duplicate_warnings)}")
        for warning in duplicate_warnings[:10]:
            print(f"  - {warning}")
        if len(duplicate_warnings) > 10:
            print(f"  ... 其余 {len(duplicate_warnings) - 10} 条省略")
    if variant_warnings:
        print(f"Apple 多格式关系不确定提示：{len(variant_warnings)}")
    print()

    stats: Dict[str, int] = defaultdict(int)
    reserved: Set[str] = set()
    failures: List[Problem] = []
    category_counts: Dict[str, int] = defaultdict(int)
    moved_groups = 0
    moved_files = 0
    global_duplicate_groups = 0

    total = len(groups)
    stage_started = time.perf_counter()
    for index, group in enumerate(groups, start=1):
        decision = decide_secondary_category(group, infos, source_root)
        category_counts[decision.label] += 1
        print_stage_progress("收口进度", index, total, f"{decision.label} · {group.primary.name}")

        print("-" * 78)
        print(f"[{index}/{total}] {decision.label} · {group.primary.name}")
        for member in group.members:
            prefix = "主媒体" if member in group.media_members else "sidecar"
            print(f"  {prefix}: {member}")
        print(f"  原因：{decision.reason}")

        global_duplicate = global_duplicate_matches.get(id(group))
        if global_duplicate is not None:
            print(f"  [全局重复] {global_duplicate.reason}")
            ok, mappings, duplicate_placement = plan_global_duplicate_group(group, decision, reserved)
            if not ok:
                reason = duplicate_placement or "全局重复隔离规划失败"
                print(f"  [保持原位] {reason}")
                failures.append(Problem(str(group.primary), reason, "failure"))
                continue
            for _, destination in mappings:
                reserved.add(str(destination.resolve(strict=False)).casefold())
            global_duplicate_groups += 1
            if not args.apply:
                for source, destination in mappings:
                    print(f"  [全局重复副本预览] {source}\n      -> {destination}")
                stats["planned"] += 1
                continue
            success, move_error, moved = move_group_with_rollback(mappings)
            if not success:
                reason = move_error or "未知重复隔离移动失败"
                failures.append(Problem(str(group.primary), reason, "failure"))
                print(f"  [失败] {reason}")
                continue
            moved_groups += 1
            moved_files += len(moved)
            for source, destination in moved:
                print(f"  [全局重复副本已隔离] {source}\n      -> {destination}")
            continue

        if decision.key == "gallery" and decision.organizer_group is not None:
            ok, mappings, note, placement = preflight_group(decision.organizer_group, reserved)
            if not ok:
                reason = note or "主图库目标预检失败"
                print(f"  [保持原位] {reason}")
                failures.append(Problem(str(group.primary), reason, "failure"))
                continue
            # 保留主 Organizer 的 Finder 自定义文件名标签事务语义。
            try:
                tag_plan = build_custom_filename_tag_plan(mappings, infos)
            except Exception as exc:
                reason = f"Finder 标签预检失败：{exc}"
                print(f"  [保持原位] {reason}")
                failures.append(Problem(str(group.primary), reason, "failure"))
                continue
            for _, destination in mappings:
                reserved.add(str(destination.resolve(strict=False)).casefold())
            if not args.apply:
                for source, destination in mappings:
                    if same_path(source, destination):
                        print(f"  [已正确] {source}")
                    elif is_duplicate_destination(destination):
                        print(f"  [重复副本预览] {source}\n      -> {destination}")
                    else:
                        print(f"  [主图库预览] {source}\n      -> {destination}")
                stats["planned"] += 1
                continue
            success, move_error, moved = move_group_with_rollback(mappings)
            if not success:
                reason = move_error or "未知移动失败"
                failures.append(Problem(str(group.primary), reason, "failure"))
                print(f"  [失败] {reason}")
                continue
            tag_ok, tag_error, added_count = apply_finder_tag_plan(tag_plan)
            if not tag_ok:
                rollback_error = rollback_completed_move(moved)
                restore_error = restore_tag_states_at_sources(tag_plan)
                reason = tag_error or "Finder 标签失败"
                if rollback_error:
                    reason += f"；回滚失败：{rollback_error}"
                if restore_error:
                    reason += f"；标签恢复失败：{restore_error}"
                failures.append(Problem(str(group.primary), reason, "failure"))
                print(f"  [事务失败并回滚] {reason}")
                continue
            moved_groups += 1
            moved_files += len(moved)
            for source, destination in moved:
                print(f"  [成功] {source}\n      -> {destination}")
            continue

        try:
            ok, mappings, placement, note = plan_secondary_category_group(group, decision, reserved)
        except Exception as exc:
            ok, mappings, placement, note = False, [], "plan-error", str(exc)

        if not ok:
            reason = note or "目标规划失败"
            print(f"  [保持原位] {reason}")
            failures.append(Problem(str(group.primary), reason, "failure"))
            continue

        for _, destination in mappings:
            reserved.add(str(destination.resolve(strict=False)).casefold())

        if note:
            print(f"  [提示] {note}")
        if not args.apply:
            for source, destination in mappings:
                label = "重复副本预览" if is_duplicate_destination(destination) else "预览"
                print(f"  [{label}] {source}\n      -> {destination}")
            stats["planned"] += 1
            continue

        success, move_error, moved = move_group_with_rollback(mappings)
        if not success:
            reason = move_error or "未知移动失败"
            failures.append(Problem(str(group.primary), reason, "failure"))
            print(f"  [失败] {reason}")
            continue
        moved_groups += 1
        moved_files += len(moved)
        for source, destination in moved:
            label = "重复副本已隔离" if is_duplicate_destination(destination) else "成功"
            print(f"  [{label}] {source}\n      -> {destination}")

    finish_stage_progress()
    stage_timings["分类规划/移动"] = time.perf_counter() - stage_started
    stage_timings["总耗时"] = time.perf_counter() - run_started
    print()
    print("=" * 78)
    print("第二阶段运行结束")
    print("=" * 78)
    print(f"来源目录：{source_root}")
    print(f"媒体组/独立项：{len(groups)}")
    print(f"全局确认重复媒体组/项：{len(global_duplicate_matches)}")
    print(f"其中计划/已隔离到第二阶段重复目录：{global_duplicate_groups}")
    print(f"计划处理组：{stats['planned'] if not args.apply else moved_groups + len(failures)}")
    if args.apply:
        print(f"成功移动组：{moved_groups}")
        print(f"成功移动文件：{moved_files}")
    print()
    print("阶段耗时：")
    for label in (
        "扫描文件", "ExifTool metadata", "来源/xattr与媒体分析",
        "媒体组恢复/关联", "全局重复预检", "分类规划/移动", "总耗时",
    ):
        if label in stage_timings:
            print(f"  {label:<22} {stage_timings[label]:8.2f} s")
    print(
        "  SHA-256 缓存             "
        f"命中 {_SHA256_CACHE.hits} / 未命中 {_SHA256_CACHE.misses}"
        + (f" / 缓存错误 {_SHA256_CACHE.errors}" if _SHA256_CACHE.errors else "")
    )
    print("分类统计：")
    for label in (
        CATEGORY_GALLERY, CATEGORY_SCREEN, CATEGORY_DOWNLOAD, CATEGORY_UNKNOWN,
        CATEGORY_TIME, CATEGORY_TIMEZONE, CATEGORY_LIVE, CATEGORY_SIDECAR,
        CATEGORY_COMPAT, CATEGORY_CORRUPT,
    ):
        if category_counts.get(label):
            print(f"  {label}: {category_counts[label]}")
    print(f"未能安全移动 / 需要人工检查：{len(failures)}")
    if failures:
        for i, item in enumerate(failures, start=1):
            print(f"  {i}. {item.path}")
            print(f"     原因：{item.reason}")
    else:
        print("  无")
    if not args.apply:
        print("\n当前是 DRY RUN，没有移动任何文件。")
    return 0 if not failures else 1



def clear_terminal() -> None:
    """Clear the interactive terminal without invoking an external command."""
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="", flush=True)


def normalize_tui_path_input(value: str) -> str:
    """Accept plain, quoted, or shell-escaped paths pasted/dragged into Terminal."""
    raw = value.strip()
    if not raw:
        return ""

    try:
        parts = shlex.split(raw)
    except ValueError:
        return raw.strip("\"'")

    if len(parts) == 1:
        return parts[0]
    return raw.strip("\"'")


def read_tui_key() -> str:
    """Read one key immediately on a TTY; fall back to a line outside a TTY."""
    if not sys.stdin.isatty():
        value = input()
        return value[:1] if value else "\n"

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        data = os.read(fd, 1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)

    if data == b"\x03":
        raise KeyboardInterrupt

    key = data.decode("utf-8", errors="ignore")
    print()
    return key


def secondary_clear_terminal() -> None:
    clear_terminal()


def secondary_validate_tui_source(value: str) -> Tuple[Optional[Path], Optional[str]]:
    normalized = normalize_tui_path_input(value)
    if not normalized:
        return None, "目录不能为空。"
    path = Path(normalized).expanduser().resolve(strict=False)
    if not path.exists() or not path.is_dir():
        return None, f"目录不存在或不是目录：{path}"
    if not path_is_within(path, BACKUP_APPLE_ROOT.resolve(strict=False)):
        return None, f"目录必须位于 {BACKUP_APPLE_ROOT} 内。"
    return path, None


def secondary_print_tui_header() -> None:
    print("=" * 72)
    print("Leftover Media Organizer · 第二阶段 TUI")
    print("=" * 72)
    print("默认 DRY RUN；只有明确按 A 才 APPLY。")
    print()


def secondary_tui_main(batch_size: int) -> int:
    while True:
        secondary_clear_terminal()
        secondary_print_tui_header()
        try:
            raw = input("请输入主 Organizer 留下媒体所在目录（可拖入 Terminal）：\n> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        source, error = secondary_validate_tui_source(raw)
        if error:
            print(f"\n错误：{error}")
            input("按回车返回……")
            continue
        assert source is not None

        print("\n运行模式：")
        print("  [A] APPLY（实际移动）")
        print("  [其他任意键] DRY RUN（默认、安全）")
        print("  [Q] 退出")
        key = read_tui_key().casefold()
        if key == "q":
            return 0
        apply_now = key == "a"

        run_args = argparse.Namespace(folder=str(source), apply=apply_now, batch_size=batch_size)
        code = run_secondary(run_args)

        if apply_now:
            print("\nAPPLY 已结束。")
            print("  [Q] 退出")
            print("  [其他任意键] 返回初始界面")
            if read_tui_key().casefold() == "q":
                return code
            continue

        print("\nDRY RUN 已结束，没有移动文件。")
        print("  [A] 对同一目录重新扫描并执行 APPLY")
        print("  [Q] 退出")
        print("  [其他任意键] 返回初始界面")
        after = read_tui_key().casefold()
        if after == "q":
            return code
        if after == "a":
            apply_args = argparse.Namespace(folder=str(source), apply=True, batch_size=batch_size)
            code = run_secondary(apply_args)
            print("\nAPPLY 已结束。")
            print("  [Q] 退出")
            print("  [其他任意键] 返回初始界面")
            if read_tui_key().casefold() == "q":
                return code


def main() -> int:
    args = secondary_parse_args()
    if args.batch_size <= 0:
        print("错误：--batch-size 必须大于 0。", file=sys.stderr)
        return 2
    if args.folder is None:
        return secondary_tui_main(args.batch_size)
    return run_secondary(args)


if __name__ == "__main__":
    raise SystemExit(main())
