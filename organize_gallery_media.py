#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Modified: 2026-08-18 18:54 +02:00

"""
安全整理手机相机照片/视频。

交互式 TUI（推荐）：
    python organize_gallery_media.py

兼容原命令行用法（默认只预览，不移动）：
    python organize_gallery_media.py "来源目录"

真正执行：
    python organize_gallery_media.py "来源目录" --apply

固定目标：
    gallery_config.py 的 MAIN_LIBRARY_ROOT/YYYY/YYYY-MM/

完全重复副本隔离根目录：
    gallery_config.py 的 DUPLICATE_ROOT/

规则：
- 命令参数只代表“来源目录”。
- 来源目录必须位于 gallery_config.py 的 BACKUP_ROOT 内。
- 整理目标永远固定为 gallery_config.py 的 MAIN_LIBRARY_ROOT。
- 不重命名，不覆盖。
- 默认 dry-run；只有 --apply 才移动。
- 只整理能够较强确认是手机相机实拍的媒体。
- 照片没有可靠内嵌拍摄时间：不动。
- 视频没有可靠内嵌拍摄/创建时间：不动。
- 有可信照片 EXIF（设备/相机信息 + 可靠内嵌拍摄时间）时：
  直接按 EXIF 时间归类；文件名是否标准、是否为用户自定义文字、以及文件名时间差都不再否决。
  但 PNG 不通过此通用可信 EXIF 分支自动获得“相机实拍”资格；PNG 默认保持原位，避免截图/转换图继承设备 metadata 后误收。
- 自定义/非标准文件名的视频若有可信相机元数据（设备/相机身份 + 强内嵌拍摄/创建时间），
  也可独立按内嵌时间归类；仅 TrackCreateDate 不足以单独放行。
- 若 Make/Model 已丢失，但照片仍有 DateTimeOriginal，
  且 IMG_YYYYMMDD_HHMMSS... / MVIMG_YYYYMMDD_HHMMSS... / PANO_YYYYMMDD_HHMMSS...
  文件名时间与 DateTimeOriginal 相差 <= 20 秒：按 DateTimeOriginal 归类；文件名只用于验证。
- Android IMG_YYYYMMDD_HHMMSS / VID_YYYYMMDD_HHMMSS / MVIMG_YYYYMMDD_HHMMSS / PANO_YYYYMMDD_HHMMSS：
  MVIMG Motion Photo 原文件不拆分、不转码、不重写。
  标准 VID/MVIMG 视频会读取全部强视频时间字段，并分别尝试原值、UTC+1、UTC+2、UTC+8、UTC+9；
  第一条验证：转换后直接与文件名本地时间 <=20 秒一致；
  第二条验证：对 MediaCreateDate/CreateDate，若存在合法 Duration，则用“转换后的时间 - Duration”推算录像开始时间，
  推算结果与文件名本地时间 <=20 秒也可通过。通过后按已经 metadata 验证的文件名本地时间归类。
  该逻辑只解释已有 metadata，不写入、不修复、不重写 EXIF/QuickTime metadata。
- PANO 缺少可靠内嵌时间时仍保持原位；metadata 修复不属于本脚本职责。
- Apple IMG_####：
  需要 Apple/iPhone/iPad 设备元数据；PNG 等非典型相机照片不自动整理。
- 来源负面证据优先于可信 EXIF：明确截图/屏幕录制（含 UserComment / ImageDescription）、网络 WhereFroms、浏览器/聊天下载痕迹等不自动整理；
  Downloads/聊天目录属于中等负面证据；quarantine 本身不构成负面证据，Apple Photos 写入的 quarantine 明确视为中性。
- xattr 兼容：WhereFroms/quarantine 与 Finder 标签统一通过 macOS /usr/bin/xattr 处理。
- Apple Live Photo 静态图 + MOV 整组移动；非标准/自定义文件名也可在“同 stem + 可信静态图 EXIF + 双方可靠内嵌时间一致 + 元数据无冲突”时保守配对。
- XMP / AAE / THM / LRV / DOP / PP3 sidecar 跟随主媒体移动；优先读取 sidecar 内容、标识符与元数据判断归属；仍不明确时宁可整组不动。
- Apple IMG_O####.AAE 会自动解析 plist，并查找同目录 IMG_#### 主媒体；Apple adjustmentTimestamp 按 UTC 解释，并优先使用候选媒体自身显式时区/OffsetTime 或同 stem XMP 的显式时区归一化后核对；仅在时间 <= 2 秒时确认关联。
- 点号隐藏项和 Finder hidden 项也扫描；仅排除明确系统索引/垃圾目录与 AppleDouble 元数据。
- Apple 同 stem HEIC/JPG/DNG 不因文件名相同就自动绑定；用 metadata、导出伴生证据与图像相似度确认。确认同图后，相机原图进入 Gallery，其他格式进入 Old Duplicate/同图不同格式。
- 月目录出现完整同名项时，先比较文件大小；大小相同再做 SHA-256。内容完全相同时，比较双方 mtime 与可信拍摄时间的距离：更接近的一份进入/留在 Gallery，另一份移入 Old Duplicate 并保留来源相对路径；组内其他目标缺失成员仍可补入正常 Gallery。
- JPEG 同名但完整 SHA-256 不同且“去除全部 metadata 后的 JPEG 主图数据 SHA-256 完全一致”时，只认定为同图 metadata variant；脚本不能判断哪个版本的 Orientation/GPS/日期等 metadata 才正确，因此两份都保持原位、不替换、不进入日期子目录，并列出关键 metadata 差异供人工确认。其他内容不同才按普通重号冲突规则处理。
- 被可信识别并纳入分类、但文件名不是常见相机自动命名的照片/视频，会保留原有 Finder 标签并新增“自定义文件名”标签。
- Finder 标签写入属于同组事务：若标签失败，会恢复原标签并回滚该组移动。
- 实际移动中若中途失败，会尽量回滚已移动的同组文件。
"""

from __future__ import annotations

import sys

if sys.platform != "darwin":
    raise SystemExit("此脚本仅支持 macOS；Windows 和 Linux 上不会运行。")

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
import struct
import tempfile
import termios
import tty
import shlex
import sqlite3
import time
import urllib.parse
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple


from gallery_config import (
    BACKUP_ROOT as BACKUP_APPLE_ROOT, GALLERY_ROOT,
    MAIN_LIBRARY_ROOT as DESTINATION_ROOT, DUPLICATE_ROOT,
    FORMAT_VARIANT_DUPLICATE_ROOT, SOURCE_STAGE0_ROOT, COMPLETED_SOURCE_ROOT,
    HASH_CACHE_DB, XATTR_TOOL, SIPS_TOOL, FFMPEG_TOOL,
)
SCRIPT_VERSION = "2026-08-27-duplicate-best-mtime-transactional-swap"

TIME_TOLERANCE_SECONDS = 20
ANDROID_VIDEO_LOCAL_UTC_OFFSETS = (0, 1, 2, 8, 9)
HASH_CHUNK_SIZE = 8 * 1024 * 1024
PROGRESS_BAR_WIDTH = 28
SOURCE_PROGRESS_INTERVAL = 20

FINDER_TAG_XATTR = "com.apple.metadata:_kMDItemUserTags"
CUSTOM_FILENAME_FINDER_TAG = "自定义文件名"


# 性能缓存只影响“如何更快得到相同结果”，不改变任何媒体判定。
_XATTR_NAME_CACHE: Dict[str, Set[str]] = {}


class Sha256Cache:
    """
    持久 SHA-256 缓存。

    只有路径、大小、mtime_ns、ctime_ns 全部一致时才复用；否则重新读取
    整个文件计算 SHA-256。缓存不可用时自动退化为普通完整哈希，不影响安全性。
    """

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sha256_cache (
                    path TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    ctime_ns INTEGER NOT NULL,
                    sha256 TEXT NOT NULL
                )
                """
            )
            conn.commit()
            self._conn = conn
            return conn
        except Exception:
            self.errors += 1
            return None

    @staticmethod
    def _identity(path: Path) -> Tuple[str, int, int, int]:
        stat = path.stat()
        return (
            str(path.resolve(strict=False)),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            int(stat.st_ctime_ns),
        )

    def get(self, path: Path) -> Optional[str]:
        try:
            identity = self._identity(path)
        except OSError:
            return None

        cached = self.memory.get(identity)
        if cached is not None:
            self.hits += 1
            return cached

        conn = self._connection()
        if conn is None:
            self.misses += 1
            return None

        try:
            row = conn.execute(
                "SELECT size, mtime_ns, ctime_ns, sha256 FROM sha256_cache WHERE path = ?",
                (identity[0],),
            ).fetchone()
        except Exception:
            self.errors += 1
            self.misses += 1
            return None

        if row is None or tuple(row[:3]) != identity[1:]:
            self.misses += 1
            return None

        digest = str(row[3])
        self.memory[identity] = digest
        self.hits += 1
        return digest

    def put(self, path: Path, digest: str) -> None:
        try:
            identity = self._identity(path)
        except OSError:
            return
        self.memory[identity] = digest

        conn = self._connection()
        if conn is None:
            return
        try:
            conn.execute(
                """
                INSERT INTO sha256_cache(path, size, mtime_ns, ctime_ns, sha256)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    size=excluded.size,
                    mtime_ns=excluded.mtime_ns,
                    ctime_ns=excluded.ctime_ns,
                    sha256=excluded.sha256
                """,
                (*identity, digest),
            )
            conn.commit()
        except Exception:
            self.errors += 1

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


_SHA256_CACHE = Sha256Cache(HASH_CACHE_DB)
atexit.register(_SHA256_CACHE.close)


PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".jfif",
    ".heic", ".heif", ".hif",
    ".png", ".webp", ".avif", ".jxl",
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
    r"(?:^|[_\-\s])(?:screenshot|screen[_\-\s]?shot|截屏|截图)(?:[_\-\s]|$)",
    re.IGNORECASE,
)

SCREENSHOT_DIR_NAMES = {
    "screenshot",
    "screenshots",
    "screen shot",
    "screen shots",
    "截屏",
    "截图",
}

SCREEN_RECORDING_NAME_RE = re.compile(
    r"(?:^|[_\-\s])(?:screen[_\-\s]?(?:recording|record)|screenrecord(?:ing)?|录屏|屏幕录制)(?:[_\-\s]|$)",
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
    capture_timestamp: Timestamp
    format_variant_duplicates: List[Path]
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
    media_by_directory: Dict[Path, List[MediaInfo]] = field(default_factory=dict)
    media_by_name: Dict[Path, Dict[str, MediaInfo]] = field(default_factory=dict)
    media_by_stem: Dict[Path, Dict[str, List[MediaInfo]]] = field(default_factory=dict)
    media_by_identifier: Dict[Path, Dict[str, Set[Path]]] = field(default_factory=dict)
    xmp_by_stem: Dict[Path, Dict[str, List[MediaInfo]]] = field(default_factory=dict)
    filename_mention_regex: Dict[Path, Optional[re.Pattern[str]]] = field(default_factory=dict)


def _compile_media_filename_mention_regex(names: Sequence[str]) -> Optional[re.Pattern[str]]:
    """
    为一个目录一次性编译“sidecar 文本明确引用完整媒体文件名”的匹配器。

    边界规则与 text_mentions_exact_filename() 完全一致，但避免每个
    sidecar × 每个媒体反复 re.compile()。
    """
    normalized = sorted(
        {name.casefold() for name in names if name},
        key=lambda value: (-len(value), value),
    )
    if not normalized:
        return None
    try:
        alternatives = "|".join(re.escape(name) for name in normalized)
        return re.compile(
            rf"(?<![a-z0-9._-])(?:{alternatives})(?![a-z0-9._-])",
            re.IGNORECASE,
        )
    except re.error:
        # 极端超长/异常文件名目录退回旧逐项匹配，不改变安全判定。
        return None


def build_sidecar_lookup_index(infos: Dict[Path, MediaInfo]) -> SidecarLookupIndex:
    """
    预建 sidecar 归属所需索引。

    除目录/name/stem 外，把媒体 identifier、同 stem XMP、以及“文本中完整文件名”
    匹配器都只计算一次，避免大目录中 sidecar × media 的重复工作。
    """
    by_directory: Dict[Path, List[MediaInfo]] = defaultdict(list)
    by_name: Dict[Path, Dict[str, MediaInfo]] = defaultdict(dict)
    by_stem: Dict[Path, Dict[str, List[MediaInfo]]] = defaultdict(lambda: defaultdict(list))
    by_identifier: Dict[Path, Dict[str, Set[Path]]] = defaultdict(lambda: defaultdict(set))
    xmp_by_stem: Dict[Path, Dict[str, List[MediaInfo]]] = defaultdict(lambda: defaultdict(list))

    for info in infos.values():
        directory = info.path.parent
        if info.kind in ("photo", "video"):
            by_directory[directory].append(info)
            by_name[directory][info.path.name.casefold()] = info
            by_stem[directory][info.path.stem.casefold()].append(info)
            for identifier in metadata_identifiers(info.metadata):
                by_identifier[directory][identifier].add(info.path)
        elif info.kind == "sidecar" and info.path.suffix.lower() == ".xmp":
            xmp_by_stem[directory][info.path.stem.casefold()].append(info)

    plain_by_name = {key: dict(value) for key, value in by_name.items()}
    mention_regex = {
        directory: _compile_media_filename_mention_regex(list(names))
        for directory, names in (
            (directory, name_map.keys())
            for directory, name_map in plain_by_name.items()
        )
    }

    # 转为普通 dict，避免后续查询意外创建空项。
    return SidecarLookupIndex(
        media_by_directory={key: list(value) for key, value in by_directory.items()},
        media_by_name=plain_by_name,
        media_by_stem={
            key: {stem: list(items) for stem, items in value.items()}
            for key, value in by_stem.items()
        },
        media_by_identifier={
            key: {identifier: set(paths) for identifier, paths in value.items()}
            for key, value in by_identifier.items()
        },
        xmp_by_stem={
            key: {stem: list(items) for stem, items in value.items()}
            for key, value in xmp_by_stem.items()
        },
        filename_mention_regex=mention_regex,
    )



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
            f"按 mtime 与拍摄时间择优，另一份移入 {DUPLICATE_ROOT}/。"
        )
    )
    parser.add_argument(
        "folder",
        nargs="?",
        help=(
            "来源目录；必须位于 "
            f"{BACKUP_APPLE_ROOT} 内。省略时进入交互式 TUI。目标目录固定为 {DESTINATION_ROOT}；"
            f"完全重复副本按 mtime 与拍摄时间择优，另一份隔离到 {DUPLICATE_ROOT}。"
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
    lookup_index: Optional[SidecarLookupIndex] = None,
) -> List[Tuple[Timestamp, int, str]]:
    """Use a same-stem XMP only as explicit timezone evidence, never as a guessed offset."""
    result: List[Tuple[Timestamp, int, str]] = []
    stem = candidate.path.stem.casefold()
    if lookup_index is not None:
        candidates = lookup_index.xmp_by_stem.get(candidate.path.parent, {}).get(stem, [])
    else:
        candidates = [
            info for info in infos.values()
            if info.path.parent == candidate.path.parent
            and info.path.suffix.lower() == ".xmp"
            and info.path.stem.casefold() == stem
        ]

    for info in candidates:
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
    """每个路径在一次运行中最多调用一次 `xattr file`，除非明确 refresh。"""
    cache_key = str(path.resolve(strict=False))
    if not refresh and cache_key in _XATTR_NAME_CACHE:
        return set(_XATTR_NAME_CACHE[cache_key])

    result = _run_xattr([str(path)])
    if result.returncode != 0:
        raise OSError(f"无法列出扩展属性：{path}：{_xattr_error_text(result)}")
    names = {
        line.strip()
        for line in result.stdout.decode("utf-8", errors="replace").splitlines()
        if line.strip()
    }
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


def _move_mappings_transactionally(
    mappings: Sequence[Tuple[Path, Path]],
) -> List[Tuple[Path, Path]]:
    """两阶段暂存后提交，支持普通移动与 A↔B 交换；失败时恢复原路径。"""
    active = [(s, d) for s, d in mappings if not same_path(s, d)]
    if not active:
        return []

    source_keys = {str(s.resolve(strict=False)).casefold() for s, _ in active}
    destination_keys: Set[str] = set()
    for source, destination in active:
        if not source.exists():
            raise FileNotFoundError(f"来源不存在：{source}")
        key = str(destination.resolve(strict=False)).casefold()
        if key in destination_keys:
            raise FileExistsError(f"同组多个 mapping 目标重复：{destination}")
        destination_keys.add(key)
        if destination.exists() and key not in source_keys:
            raise FileExistsError(f"目标已存在且不属于本次交换来源：{destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)

    staged: List[Tuple[Path, Path, Path]] = []
    committed: List[Tuple[Path, Path, Path]] = []
    try:
        for index, (source, destination) in enumerate(active):
            temporary = source.parent / f".{source.name}.gallery-move-{os.getpid()}-{index}.tmp"
            if temporary.exists():
                raise FileExistsError(f"事务临时路径已存在：{temporary}")
            shutil.move(str(source), str(temporary))
            staged.append((source, destination, temporary))

        for source, destination, temporary in staged:
            if destination.exists():
                raise FileExistsError(f"暂存后目标仍存在：{destination}")
            shutil.move(str(temporary), str(destination))
            committed.append((source, destination, temporary))

        return active
    except Exception as exc:
        rollback_errors: List[str] = []
        # 已提交项目先退回各自临时路径，此时所有原路径仍为空，可安全解除交换环。
        for source, destination, temporary in reversed(committed):
            try:
                if destination.exists() and not temporary.exists():
                    shutil.move(str(destination), str(temporary))
            except Exception as rollback_exc:
                rollback_errors.append(f"{destination} -> {temporary}: {rollback_exc}")
        for source, destination, temporary in reversed(staged):
            try:
                if temporary.exists() and not source.exists():
                    shutil.move(str(temporary), str(source))
            except Exception as rollback_exc:
                rollback_errors.append(f"{temporary} -> {source}: {rollback_exc}")
        message = str(exc)
        if rollback_errors:
            message += "；回滚失败：" + " | ".join(rollback_errors)
        raise RuntimeError(message) from exc


def rollback_completed_move(
    moved: Sequence[Tuple[Path, Path]],
) -> Optional[str]:
    try:
        _move_mappings_transactionally([(destination, source) for source, destination in moved])
        return None
    except Exception as exc:
        return str(exc)


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
    """
    返回 (WhereFroms 列表, quarantine 文本, 读取错误)。

    性能关键点：先列一次 xattr 名称；不存在的属性不再分别启动 `xattr -px`
    再失败。普通无 provenance 的媒体从最多约 3~4 次外部调用降为 1 次。
    """
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
                    elif isinstance(value, str) and value.strip():
                        where_froms = [value.strip()]
                except Exception:
                    decoded = raw.decode("utf-8", errors="ignore").strip()
                    if decoded:
                        where_froms = [decoded]
        except OSError as exc:
            errors.append(f"WhereFroms xattr 读取失败：{exc}")

    if QUARANTINE_XATTR in names:
        try:
            raw = _read_optional_xattr(path, QUARANTINE_XATTR)
            if raw:
                quarantine = raw.decode("utf-8", errors="replace").strip() or None
        except OSError as exc:
            errors.append(f"quarantine xattr 读取失败：{exc}")

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

    # PNG 默认不作为可自动确认的相机原片。即使它继承了设备字段、
    # DateTimeOriginal 或 Android 风格文件名，也保持原位；明确截图仍会
    # 在 finish_group 中被来源强负面证据优先报告。
    if info.kind == "photo" and ext == ".png":
        return False, "PNG 不自动认定为手机相机原片；避免截图/转换图继承相机 metadata 后误收"

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

        if show_progress and (
            item_index == total
            or item_index == 1
            or item_index % SOURCE_PROGRESS_INTERVAL == 0
        ):
            print_stage_progress(
                "来源/媒体分析",
                item_index,
                total,
                path.name,
            )

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
    使用 FFmpeg 将图片按显示方向归一化成 9x8 BMP，计算 64-bit dHash + 平均亮度。
    平均亮度用于避免纯色/低纹理图片出现相同 dHash 的假阳性。
    失败时返回 None；图像相似度只是辅助证据。
    """
    if path in cache:
        return cache[path]

    ffmpeg = Path(FFMPEG_TOOL)
    if not ffmpeg.exists():
        cache[path] = None
        return None

    try:
        with tempfile.TemporaryDirectory(prefix="gallery-organizer-sim-") as temp_dir:
            output = Path(temp_dir) / "thumb.bmp"
            result = subprocess.run(
                [
                    str(ffmpeg),
                    "-v", "error",
                    "-i",
                    str(path),
                    "-filter_complex",
                    "[0:v]scale=9:8:flags=area,format=bgr24[out]",
                    "-map", "[out]",
                    "-frames:v", "1",
                    str(output),
                    "-y",
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

    # 常见 iPhone 导出：HEIC 保留完整相机 metadata，旁边的 JPG 被剥离 metadata，
    # 但两者同 stem、尺寸比例一致且文件系统 mtime 几乎同时。这个组合比低分辨率
    # 感知哈希更可靠（JPG 可能经过缩放/轻微裁切/色调映射）。
    suffixes = {a.path.suffix.lower(), b.path.suffix.lower()}
    heic_jpeg_pair = bool(
        suffixes & {".heic", ".heif", ".hif"}
        and suffixes & {".jpg", ".jpeg"}
    )
    one_has_original = (
        a.date_source == "metadata:DateTimeOriginal"
        or b.date_source == "metadata:DateTimeOriginal"
    )
    one_lacks_embedded_time = a.timestamp is None or b.timestamp is None
    try:
        filesystem_time_diff = abs(a.path.stat().st_mtime - b.path.stat().st_mtime)
    except OSError:
        filesystem_time_diff = float("inf")
    if (
        heic_jpeg_pair
        and one_has_original
        and one_lacks_embedded_time
        and not device_conflict
        and dims_ok
        and filesystem_time_diff <= 5
    ):
        return (
            "confirmed",
            "HEIC/JPEG 导出伴生证据一致：一份保留 DateTimeOriginal，另一份 metadata 缺失；"
            f"尺寸比例一致，filesystem mtime 相差 {filesystem_time_diff:.3f}s",
        )

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


def resolve_sidecar_targets(
    sidecar_path: Path,
    infos: Dict[Path, MediaInfo],
    content_cache: Dict[Path, SidecarContentEvidence],
    lookup_index: Optional[SidecarLookupIndex] = None,
) -> SidecarResolution:
    sidecar = infos[sidecar_path]
    if lookup_index is None:
        directory_media = [
            info
            for info in infos.values()
            if info.kind in ("photo", "video") and info.path.parent == sidecar_path.parent
        ]
        by_name = {info.path.name.casefold(): info for info in directory_media}
        by_stem = defaultdict(list)
        for info in directory_media:
            by_stem[info.path.stem.casefold()].append(info)
    else:
        directory_media = lookup_index.media_by_directory.get(sidecar_path.parent, [])
        by_name = lookup_index.media_by_name.get(sidecar_path.parent, {})
        by_stem = lookup_index.media_by_stem.get(sidecar_path.parent, {})

    if not directory_media:
        return SidecarResolution("unrelated", reason="同目录没有主媒体")
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
        base_candidates = [
            info for info in directory_media
            if info.path.stem.casefold() == base_stem
        ]

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
                sibling_xmp_times = same_stem_xmp_explicit_times(candidate, infos, lookup_index)

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
    metadata_targets: Set[Path] = set()
    if metadata_hints:
        if lookup_index is not None:
            for hint in metadata_hints:
                target = by_name.get(hint)
                if target is not None:
                    metadata_targets.add(target.path)
        else:
            metadata_targets = {
                info.path for info in directory_media if info.path.name.casefold() in metadata_hints
            }
    if metadata_targets:
        strong_sets.append((metadata_targets, "sidecar metadata 明确记录原文件名"))

    # 3) XMP/AAE/DOP/PP3 内容中明确出现完整媒体文件名。
    content_targets: Set[Path] = set()
    if evidence.text or evidence.embedded_strings:
        if lookup_index is not None:
            # embedded_strings 已经是解析后的独立字符串，可直接 basename -> name 索引。
            for embedded in evidence.embedded_strings:
                basename = embedded.rsplit("/", 1)[-1].strip().casefold()
                target = by_name.get(basename)
                if target is not None:
                    content_targets.add(target.path)

            # 自由文本仍严格保留旧边界规则，但一个目录只编译一个联合正则。
            mention_re = lookup_index.filename_mention_regex.get(sidecar_path.parent)
            if evidence.text and mention_re is not None:
                for match in mention_re.finditer(evidence.text):
                    target = by_name.get(match.group(0).casefold())
                    if target is not None:
                        content_targets.add(target.path)
            elif evidence.text:
                # 联合正则构建失败时退回旧算法，安全规则不变。
                for info in directory_media:
                    if text_mentions_exact_filename(evidence.text, info.path.name):
                        content_targets.add(info.path)
        else:
            for info in directory_media:
                if text_mentions_exact_filename(evidence.text, info.path.name):
                    content_targets.add(info.path)
                    continue
                for embedded in evidence.embedded_strings:
                    basename = embedded.rsplit("/", 1)[-1].strip().casefold()
                    if basename == info.path.name.casefold():
                        content_targets.add(info.path)
                        break
    if content_targets:
        strong_sets.append((content_targets, "sidecar 内容明确引用媒体完整文件名"))

    # 4) ContentIdentifier / MediaGroupUUID / XMP UUID 等标识符交集。
    side_ids = metadata_identifiers(sidecar.metadata) | evidence.identifiers
    id_targets: Set[Path] = set()
    if side_ids:
        if lookup_index is not None:
            identifier_index = lookup_index.media_by_identifier.get(sidecar_path.parent, {})
            for identifier in side_ids:
                id_targets.update(identifier_index.get(identifier, set()))
        else:
            for info in directory_media:
                media_ids = metadata_identifiers(info.metadata)
                if media_ids & side_ids:
                    id_targets.add(info.path)
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


def build_sidecar_resolution_index(
    infos: Dict[Path, MediaInfo],
    content_cache: Dict[Path, SidecarContentEvidence],
    *,
    show_progress: bool = False,
) -> Tuple[Dict[Path, SidecarResolution], Dict[Path, Set[Path]]]:
    """
    每个 sidecar 只做一次内容/metadata 归属解析，并建立 target -> sidecar 索引。

    旧实现是在每一个媒体组处理中重新遍历同目录全部 sidecar，并反复调用
    resolve_sidecar_targets()。当一个目录里约 1000 主媒体 + 1000 XMP 时会造成
    极大的重复计算。这个索引只缓存同一套判定结果，不改变任何归属规则。
    """
    resolutions: Dict[Path, SidecarResolution] = {}
    by_target: Dict[Path, Set[Path]] = defaultdict(set)
    lookup_index = build_sidecar_lookup_index(infos)
    sidecars = sorted(
        (info.path for info in infos.values() if info.kind == "sidecar"),
        key=lambda p: str(p).casefold(),
    )
    total = len(sidecars)

    for index, candidate in enumerate(sidecars, start=1):
        resolution = resolve_sidecar_targets(
            candidate,
            infos,
            content_cache,
            lookup_index,
        )
        resolutions[candidate] = resolution
        if resolution.status != "unrelated":
            for target in resolution.targets:
                by_target[target].add(candidate)

        if show_progress and (
            index == 1
            or index == total
            or index % SOURCE_PROGRESS_INTERVAL == 0
        ):
            print_stage_progress(
                "Sidecar索引",
                index,
                total,
                candidate.name,
            )

    return resolutions, by_target


def sidecars_for_media_group_indexed(
    media_paths: Sequence[Path],
    resolutions: Dict[Path, SidecarResolution],
    by_target: Dict[Path, Set[Path]],
) -> Tuple[List[Path], List[str]]:
    """使用预计算的 sidecar 归属索引，结果与旧逐组解析逻辑一致。"""
    media_set = set(media_paths)
    candidates: Set[Path] = set()
    for media_path in media_set:
        candidates.update(by_target.get(media_path, set()))

    result: Set[Path] = set()
    ambiguous: List[str] = []

    for candidate in sorted(candidates, key=lambda p: str(p).casefold()):
        resolution = resolutions[candidate]
        targets = resolution.targets
        if resolution.status == "unrelated" or not targets:
            continue
        if targets.issubset(media_set):
            result.add(candidate)
            continue

        outsiders = sorted((p.name for p in targets - media_set), key=str.casefold)
        ambiguous.append(
            f"{candidate.name} 归属跨越当前组：{resolution.reason}；组外候选："
            + ", ".join(outsiders)
        )

    return sorted(result, key=lambda p: (str(p.parent).casefold(), p.name.casefold())), ambiguous


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

def build_unknown_companion_index(infos: Dict[Path, MediaInfo]) -> Dict[Tuple[Path, str], Set[Path]]:
    """
    每个包含媒体的目录只扫描一次，把脚本不认识的普通伴随文件按 stem 建索引。
    只优化原 unknown_companions_for_media_group() 的重复目录 I/O，不改变判定条件。
    """
    directories = {
        info.path.parent
        for info in infos.values()
        if info.kind in ("photo", "video")
    }
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
) -> Tuple[List[MoveGroup], List[Problem]]:
    groups: List[MoveGroup] = []
    problems: List[Problem] = []

    consumed: Set[Path] = set()
    all_paths = set(infos)
    sidecar_content_cache: Dict[Path, SidecarContentEvidence] = {}
    # 性能关键：sidecar 的内容/metadata 归属只解析一次。
    # 之后所有媒体组都通过 target -> sidecar 索引查询，避免 O(groups × sidecars × media)。
    sidecar_resolutions, sidecars_by_target = build_sidecar_resolution_index(
        infos,
        sidecar_content_cache,
        show_progress=True,
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
        format_variant_duplicates: Sequence[Path] = (),
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
            list(media_set),
            sidecar_resolutions,
            sidecars_by_target,
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
        unknown = unknown_companions_for_media_group_indexed(
            list(media_set),
            unknown_companion_index,
        )
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
                capture_timestamp=primary.timestamp,
                format_variant_duplicates=sorted(
                    set(format_variant_duplicates), key=lambda path: str(path).casefold()
                ),
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
        finish_group(
            primary_path,
            list(media_members),
            f"{label} ({media_names})",
            format_variant_duplicates=photos - {primary_path},
        )

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

        finish_group(
            primary_path,
            list(media_members),
            description,
            format_variant_duplicates=(photos - {primary_path}) if info.kind == "photo" else (),
        )

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
    """
    分块计算完整 SHA-256，并持久缓存结果。

    缓存命中只发生在 path/size/mtime_ns/ctime_ns 全部未变化时；
    判重最终标准仍然是完整 SHA-256。缓存故障时透明退化为重新完整读取。
    """
    cached = _SHA256_CACHE.get(path)
    if cached is not None:
        return cached

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    result = digest.hexdigest()
    _SHA256_CACHE.put(path, result)
    return result


XMP_SEMANTIC_TAGS = (
    "-XMP-iptcExt:PersonInImage",
    "-XMP-mwg-rs:all",
    "-XMP-MP:all",
    "-XMP-dc:Subject",
    "-XMP-photoshop:SupplementalCategories",
    "-XMP-xmp:Rating",
)


def read_xmp_annotations(path: Path) -> Optional[dict]:
    """结构化读取会影响人物、区域、标签和评分的 XMP 字段。"""
    exiftool = shutil.which("exiftool")
    if not exiftool:
        return None
    try:
        result = subprocess.run(
            [exiftool, "-j", "-struct", *XMP_SEMANTIC_TAGS, str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        values = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    if result.returncode != 0 or len(values) != 1:
        return None
    values[0].pop("SourceFile", None)
    return values[0]


def xmp_annotations_equivalent(first: Path, second: Path) -> Optional[bool]:
    """忽略 RDF 数组顺序，比较 XMP 的人物、人脸区域、标签和评分。"""
    left = read_xmp_annotations(first)
    right = read_xmp_annotations(second)
    if left is None or right is None:
        return None

    def normalized(value: object) -> object:
        if isinstance(value, dict):
            return {key: normalized(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            items = [normalized(item) for item in value]
            return sorted(
                items,
                key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
            )
        return value

    return normalized(left) == normalized(right)


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

        # Apple AAE 是 plist；文件整体可能因 render type / timestamp 不同而不同，
        # 但 adjustmentData 相同表示实际编辑参数相同，按语义视为重复。
        if source.suffix.lower() == ".aae" and destination.suffix.lower() == ".aae":
            try:
                source_plist = plistlib.loads(source.read_bytes())
                destination_plist = plistlib.loads(destination.read_bytes())
            except Exception as exc:
                return None, f"AAE plist 解析失败，无法安全判重：{exc}"
            source_adjustment = source_plist.get("adjustmentData")
            destination_adjustment = destination_plist.get("adjustmentData")
            if (
                isinstance(source_adjustment, bytes)
                and isinstance(destination_adjustment, bytes)
                and source_adjustment == destination_adjustment
            ):
                return (
                    True,
                    "AAE adjustmentData 完全一致；实际编辑参数相同，忽略 adjustmentRenderTypes/adjustmentTimestamp",
                )

        # osxphotos / ExifTool 可能把等价 XMP 写成不同 XML，并重复写入照片内已有
        # 的 GPS。人物、脸部区域、标签和评分语义相同即可视为重复；保留版本仍由
        # 下游 mtime 与原始拍摄时间的距离决定。
        if source.suffix.lower() == ".xmp" and destination.suffix.lower() == ".xmp":
            equivalent = xmp_annotations_equivalent(source, destination)
            if equivalent is None:
                return None, "XMP 无法结构化读取，无法安全判重"
            if equivalent:
                return (
                    True,
                    "XMP 人物、人脸区域、标签和评分语义相同；忽略 XML 格式、数组顺序及重复 GPS metadata",
                )

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


JPEG_METADATA_VARIANT_EXTENSIONS = {".jpg", ".jpeg", ".jfif"}


def _sha256_uncached(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def compare_jpeg_main_image_without_metadata(
    source: Path,
    destination: Path,
) -> Tuple[Optional[bool], str]:
    """
    对同名 JPEG 的“主图内容”做第二层判重。

    仅当双方都是 JPEG/JFIF 时执行。使用 ExifTool 在临时目录生成 -all= 副本，
    不改写原文件；若去除 metadata 后 SHA-256 完全一致，则强确认主 JPEG 图像数据相同，
    差异仅来自 EXIF/JFIF/XMP/ICC/thumbnail 等 metadata/container 信息。
    """
    if source.suffix.lower() not in JPEG_METADATA_VARIANT_EXTENSIONS:
        return False, "来源不是 JPEG/JFIF，不执行 metadata-variant 主图判重"
    if destination.suffix.lower() not in JPEG_METADATA_VARIANT_EXTENSIONS:
        return False, "目标不是 JPEG/JFIF，不执行 metadata-variant 主图判重"
    if source.is_symlink() or destination.is_symlink():
        return False, "来源或目标是符号链接，不执行 metadata-variant 判定"
    if not source.is_file() or not destination.is_file():
        return False, "来源或目标不是普通文件，不执行 metadata-variant 判定"

    exiftool = shutil.which("exiftool")
    if not exiftool:
        return None, "找不到 exiftool，无法安全执行 JPEG metadata-variant 主图判重"

    try:
        with tempfile.TemporaryDirectory(prefix="gallery-organizer-jpeg-variant-") as temp_dir:
            temp_root = Path(temp_dir)
            stripped_source = temp_root / "source.jpg"
            stripped_destination = temp_root / "destination.jpg"

            for original, stripped in (
                (source, stripped_source),
                (destination, stripped_destination),
            ):
                result = subprocess.run(
                    [
                        exiftool,
                        "-q", "-q",
                        "-all=",
                        "-o", str(stripped),
                        str(original),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                )
                if result.returncode != 0 or not stripped.is_file():
                    detail = result.stderr.strip() or result.stdout.strip() or f"exit={result.returncode}"
                    return None, f"ExifTool 去 metadata 失败：{original}：{detail}"

            source_hash = _sha256_uncached(stripped_source)
            destination_hash = _sha256_uncached(stripped_destination)
            if source_hash == destination_hash:
                return (
                    True,
                    "去除全部 metadata 后 JPEG 主图 SHA-256 完全一致"
                    f"（{source_hash}）",
                )
            return (
                False,
                "去除全部 metadata 后 JPEG 主图仍不同"
                f"（来源 {source_hash}；目标 {destination_hash}）",
            )
    except Exception as exc:
        return None, f"JPEG metadata-variant 主图判重失败：{source} <-> {destination}: {exc}"


def _first_metadata_value(metadata: dict, field_name: str) -> Optional[str]:
    for key, value in metadata.items():
        if key.split(":")[-1] != field_name:
            continue
        if isinstance(value, list):
            if not value:
                continue
            value = value[0]
        if value is not None:
            return str(value)
    return None


def jpeg_metadata_variant_summary(source: Path, destination: Path) -> str:
    """只读提取少量关键 metadata，用于 Dry Run/审计日志，不参与“谁更正确”的自动推断。"""
    exiftool = shutil.which("exiftool")
    if not exiftool:
        return "关键 metadata 摘要不可用（找不到 exiftool）"
    try:
        result = subprocess.run(
            [
                exiftool, "-j", "-G1", "-a", "-s",
                "-Orientation", "-DateTimeOriginal", "-Make", "-Model", "-Software",
                str(source), str(destination),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if result.returncode not in (0, 1):
            return f"关键 metadata 摘要读取失败：{result.stderr.strip()}"
        rows = json.loads(result.stdout)
        if len(rows) < 2:
            return "关键 metadata 摘要读取不完整"

        def describe(row: dict) -> str:
            fields = []
            for field in ("Orientation", "DateTimeOriginal", "Make", "Model", "Software"):
                value = _first_metadata_value(row, field)
                if value:
                    fields.append(f"{field}={value}")
            return ", ".join(fields) if fields else "无关键字段"

        return f"来源[{describe(rows[0])}]；Gallery旧版[{describe(rows[1])}]"
    except Exception as exc:
        return f"关键 metadata 摘要读取失败：{exc}"


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


def format_variant_duplicate_target_for_source(source: Path) -> Path:
    """同图非首选格式进入专用目录，并保留其在 Backup Apple 下的相对路径。"""
    root = FORMAT_VARIANT_DUPLICATE_ROOT.resolve(strict=False)
    source_resolved = source.resolve(strict=False)
    if path_is_within(source_resolved, root):
        return source
    try:
        relative = source_resolved.relative_to(BACKUP_APPLE_ROOT.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"来源不在 Backup Apple 内，无法生成格式副本路径：{source}") from exc
    return root / relative


def is_duplicate_destination(path: Path) -> bool:
    return path_is_within(path.resolve(strict=False), DUPLICATE_ROOT.resolve(strict=False))


def is_format_variant_destination(path: Path) -> bool:
    return path_is_within(
        path.resolve(strict=False), FORMAT_VARIANT_DUPLICATE_ROOT.resolve(strict=False)
    )


def mtime_distance_from_capture(path: Path, capture: Timestamp) -> float:
    """返回文件 mtime 与可信拍摄时间（本地墙钟时间）的绝对秒差。"""
    capture_dt = datetime(*capture)
    mtime_dt = datetime.fromtimestamp(path.stat().st_mtime)
    return abs((mtime_dt - capture_dt).total_seconds())


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
       - 内容完全相同：比较双方 mtime 与可信拍摄时间，更接近的一份进入/留在 Gallery，另一份移入 DUPLICATE_ROOT；
       - 同组其他目标缺失成员（例如 XMP/AAE）仍补入正常 Gallery；
       - JPEG 完整内容不同但去除 metadata 后主图 SHA-256 完全一致：视为同图 metadata variant；不自动判断哪个 metadata 版本正确，两份都保持原位并要求人工确认；不得进入 YYYY-MM-DD 日目录；
       - 其他内容不同：若该组没有已确认重复成员，整组可继续尝试 YYYY-MM-DD 日目录；
       - 若同一候选目录同时出现“完全重复成员 + 其他同名不同内容冲突”，整组保守停止，不做部分移动；
       - 判重本身失败：整组保持原位并要求人工检查。
    3. 日期目录执行同样的判重/补齐逻辑。
    4. 重复隔离目录本身也禁止覆盖、不改名；若其镜像位置已存在文件，则保守停止。

    dry-run 也用 reserved_destinations 记住本次计划目标，避免两个来源组在预览时
    同时宣称会占用同一个目标名。
    """
    month_dir = target_dir(group.year, group.month)
    day_dir = target_dir(group.year, group.month, group.day)

    def mappings_for(directory: Path) -> List[Tuple[Path, Path]]:
        format_duplicates = set(group.format_variant_duplicates)
        return [
            (
                source,
                format_variant_duplicate_target_for_source(source)
                if source in format_duplicates
                else directory / source.name,
            )
            for source in group.members
        ]

    def reservation_key(path: Path) -> str:
        return str(path.resolve(strict=False)).casefold()

    def evaluate(
        directory: Path,
    ) -> Tuple[str, List[Tuple[Path, Path]], Optional[str]]:
        """返回 ok / ok-with-duplicates / metadata-variant-review / conflict / mixed-conflict / error。"""
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
                # 第二层 JPEG 判重：完整文件不同，但去除 metadata 后主 JPEG 图像数据完全一致。
                # 这里只能证明“同一主图、metadata 不同”，不能自动判断哪个 Orientation/GPS/日期
                # 才是正确版本。因此一旦确认 metadata variant，立即停止整组：两份都不动，
                # 也绝不改走 YYYY-MM-DD 日期目录。
                same_main_image, variant_detail = compare_jpeg_main_image_without_metadata(
                    source, canonical_destination
                )
                if same_main_image is None:
                    return (
                        "error",
                        canonical_mappings,
                        "无法安全判断同名 JPEG 是否为 metadata variant："
                        f"{variant_detail}",
                    )

                if same_main_image:
                    return (
                        "metadata-variant-review",
                        canonical_mappings,
                        f"{source.name} 与 Gallery 同名文件的 JPEG 主图完全相同，但 metadata 不同；"
                        "脚本无法判断哪个版本的 Orientation/GPS/日期等 metadata 才正确，"
                        "因此来源与 Gallery 两份都保持原位，不替换、不备份迁移、不进入日期子目录。"
                        f" {variant_detail}；{jpeg_metadata_variant_summary(source, canonical_destination)}",
                    )

                conflict_details.append(
                    f"目标已存在同名但内容不同：{canonical_destination}；{detail}；{variant_detail}"
                )
                continue

            # 正常 Gallery 已有字节完全相同文件：比较双方 mtime 与可信拍摄时间的距离。
            # 更接近拍摄时间的一份进入/留在 Gallery，另一份进入 Old Duplicate。
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

            source_distance = mtime_distance_from_capture(
                source, group.capture_timestamp
            )
            gallery_distance = mtime_distance_from_capture(
                canonical_destination, group.capture_timestamp
            )

            if source_distance < gallery_distance:
                duplicate_details.append(
                    f"{source.name} 与 Gallery 目标字节完全一致；来源 mtime 更接近拍摄时间"
                    f"（来源差 {source_distance:.0f}s；Gallery 差 {gallery_distance:.0f}s），"
                    "将来源放入 Gallery、原 Gallery 副本移入 Old Duplicate："
                    f"{detail}"
                )
                # 两阶段事务移动器支持该交换映射：先暂存两边，再提交到目标位置。
                final_mappings.append((canonical_destination, duplicate_destination))
                final_mappings.append((source, canonical_destination))
            else:
                duplicate_details.append(
                    f"{source.name} 与 Gallery 目标字节完全一致；Gallery mtime 更接近或相等"
                    f"（来源差 {source_distance:.0f}s；Gallery 差 {gallery_distance:.0f}s），"
                    "保留 Gallery 版本："
                    f"{detail}"
                )
                final_mappings.append((source, duplicate_destination))

        if conflict_details and duplicate_details:
            return (
                "mixed-conflict",
                final_mappings,
                "同一媒体组同时存在已确认重复和其他冲突成员；"
                "为避免拆组或错误补齐，整组保持原位。 "
                + "重复：" + " | ".join(duplicate_details)
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
                f"确认完全重复并按 mtime 与拍摄时间择优；{duplicate_moves} 个文件将移入 {DUPLICATE_ROOT}；"
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

    if month_status == "metadata-variant-review":
        return False, month_mappings, month_detail, "metadata-variant-review"

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

    if day_status == "metadata-variant-review":
        return False, day_mappings, day_detail, "metadata-variant-review"

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
    try:
        moved = _move_mappings_transactionally(mappings)
        return True, None, moved
    except Exception as exc:
        return False, str(exc), []


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


def archive_completed_source_directory(source_root: Path) -> Path:
    """将已成功 apply 的阶段0直接子目录整体移入阶段1，禁止覆盖。"""
    stage0 = SOURCE_STAGE0_ROOT.resolve(strict=False)
    completed = COMPLETED_SOURCE_ROOT.resolve(strict=False)
    source = source_root.resolve(strict=True)

    if source.parent != stage0:
        raise ValueError(
            "完成目录归档只允许整理阶段0的直接子目录："
            f"{source}；预期父目录：{stage0}"
        )

    destination = completed / source.name
    if destination.exists():
        raise FileExistsError(f"整理阶段1目标已存在，拒绝覆盖：{destination}")

    completed.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    if source.exists() or not destination.is_dir():
        raise RuntimeError(f"来源目录归档后验证失败：{source} -> {destination}")
    return destination


def run_organizer(args: argparse.Namespace) -> int:
    source_root = Path(args.folder).expanduser().resolve()
    backup_root = BACKUP_APPLE_ROOT.resolve(strict=False)
    destination_root = DESTINATION_ROOT.resolve(strict=False)
    duplicate_root = DUPLICATE_ROOT.resolve(strict=False)
    run_started = time.perf_counter()
    stage_timings: Dict[str, float] = {}

    # TUI 中 Dry Run -> Apply 会重新扫描；每次运行都重新读取当前 xattr 名称，
    # 但 SHA-256 内容缓存可跨运行复用（文件状态变化会自动失效）。
    _XATTR_NAME_CACHE.clear()
    _SHA256_CACHE.hits = 0
    _SHA256_CACHE.misses = 0
    _SHA256_CACHE.errors = 0

    if not source_root.exists():
        print(
            f"错误：来源目录不存在：{source_root}",
            file=sys.stderr,
        )
        return 2

    if not source_root.is_dir():
        print(
            f"错误：来源路径不是目录：{source_root}",
            file=sys.stderr,
        )
        return 2

    if not path_is_within(source_root, backup_root):
        print(
            f"错误：来源目录必须位于 {BACKUP_APPLE_ROOT} 内。\n"
            f"当前来源：{source_root}",
            file=sys.stderr,
        )
        return 2

    exiftool = require_exiftool()

    print("=" * 78)
    print("Gallery Organizer")
    print(f"脚本版本：{SCRIPT_VERSION}")
    print(f"来源目录：{source_root}")
    print(f"固定目标：{destination_root}")
    print(f"重复副本隔离：{duplicate_root}")
    print(f"同图非首选格式隔离：{FORMAT_VARIANT_DUPLICATE_ROOT}")
    print(f"apply 完成目录归档：{SOURCE_STAGE0_ROOT}/名称 -> {COMPLETED_SOURCE_ROOT}/名称")
    print(f"Android 时间容差：<= {TIME_TOLERANCE_SECONDS} 秒")
    print(
        "模式："
        + (
            "实际移动 --apply"
            if args.apply
            else "DRY RUN（只预览，不移动）"
        )
    )
    print("目标结构：YYYY/YYYY-MM；同名先做大小+SHA-256 判重，不同内容冲突时整组进入 YYYY-MM-DD 子目录")
    print("完全重复：mtime 更接近可信拍摄时间的一份进入/留在 Gallery，另一份移入 Old Duplicate；缺失 sidecar 可补入正常 Gallery")
    print("文件名：始终保持原样；不因重号自动改名")
    print("覆盖：禁止")
    print("可信 EXIF：照片设备/相机信息 + 内嵌拍摄时间 -> 直接按 EXIF 归类；PNG 不通过通用 trusted EXIF 自动放行")
    print("可信视频 metadata：设备/相机信息 + 强内嵌时间 -> 自定义文件名也可独立归类")
    print("无设备照片：DateTimeOriginal + IMG/MVIMG/PANO 文件名 <=20秒一致 -> 按 DateTimeOriginal 归类")
    print("Android 视频时间：强时间字段尝试原值 / UTC+1 / +2 / +8 / +9；直接匹配或 Media/CreateDate-Duration 推回开始时间 <=20秒才通过")
    print("Android 视频归类：验证成功后按文件名当地时间整理；只解释 metadata，不写入/修复 metadata")
    print("PANO：没有可靠内嵌时间仍保持原位；metadata 修复不由本脚本处理")
    print("无可靠内嵌时间：保持原位")
    print("隐藏媒体：照常扫描；仅明确系统索引/垃圾项排除")
    print("MVIMG：按 Android Motion Photo 命名适配；原文件不拆不改")
    print("Apple HEIC/JPG/DNG：确认同图后优先相机原图进入 Gallery，其他格式进入 Old Duplicate/同图不同格式")
    print("Sidecar：解析 XMP/AAE/DOP/PP3 内容；IMG_O####.AAE 将 Apple adjustment UTC 与 IMG_#### 的显式时区/OffsetTime/同 stem XMP 时区统一后核对；THM/LRV 用 metadata/时长辅助归属；不确定则整组不动")
    print(f"自定义文件名：可信识别并纳入归类后，保留已有 Finder 标签并追加‘{CUSTOM_FILENAME_FINDER_TAG}’")
    print("来源三态：CLEAN / AMBIGUOUS / EXCLUDED；强负面来源证据优先于可信 EXIF")
    print("来源检测：WhereFroms/quarantine + 下载/聊天目录 + Screenshot/Screen Recording 文件名与 metadata")
    print("quarantine：Photos/未知 quarantine 本身视为中性；仅浏览器/聊天 agent 或网络来源构成强负面")
    print("xattr：来源检测先单次列出属性名，仅在实际存在 WhereFroms/quarantine 时再读取")
    print("Sidecar/伴随文件：目录/name/stem、完整文件名文本匹配、ContentIdentifier/UUID、同 stem XMP、target→sidecar、未知附件均预建索引，避免 sidecar×media 重复扫描")
    print(f"SHA-256：完整哈希判重；持久缓存 {HASH_CACHE_DB}（文件状态变化自动失效）")
    print("进度显示：交互 Terminal 固定底部单行刷新；重定向日志时逐行记录；仅影响终端输出")
    print("截图/屏幕录制/网络下载：保持原位；中等来源负面证据：保持原位等待人工确认")
    print("=" * 78)
    print()

    stage_started = time.perf_counter()
    paths = list(
        iter_candidate_files(
            source_root,
            destination_root,
            DUPLICATE_ROOT.resolve(strict=False),
        )
    )
    stage_timings["扫描文件"] = time.perf_counter() - stage_started

    media_paths = [
        path
        for path in paths
        if classify(path) in ("photo", "video")
    ]

    sidecar_paths = [
        path
        for path in paths
        if classify(path) == "sidecar"
    ]

    print(f"发现主媒体：{len(media_paths)}")
    print(f"发现 sidecar：{len(sidecar_paths)}")
    print()

    stage_started = time.perf_counter()
    metadata_map: Dict[str, dict] = {}
    problems: List[Problem] = []
    metadata_paths = media_paths + sidecar_paths

    metadata_total = len(metadata_paths)
    metadata_done = 0
    metadata_batches = list(chunks(metadata_paths, args.batch_size))
    if metadata_total:
        print_stage_progress("元数据", 0, metadata_total, "准备读取 ExifTool metadata")

    for batch_no, batch in enumerate(metadata_batches, start=1):
        try:
            batch_map = read_metadata_batch(
                exiftool,
                batch,
            )

            metadata_map.update(batch_map)
            metadata_done += len(batch)
            print_stage_progress(
                "元数据",
                metadata_done,
                metadata_total,
                f"第 {batch_no}/{len(metadata_batches)} 批完成；成功读取 {len(batch_map)}/{len(batch)}",
            )

        except Exception as exc:
            reason = f"ExifTool 批次读取失败：{exc}"

            print(
                f"[失败] {reason}",
                file=sys.stderr,
            )

            for path in batch:
                problems.append(
                    Problem(
                        str(path),
                        reason,
                        "failure",
                    )
                )
            metadata_done += len(batch)
            print_stage_progress(
                "元数据",
                metadata_done,
                metadata_total,
                f"第 {batch_no}/{len(metadata_batches)} 批失败，已记录待人工检查",
            )

    finish_stage_progress()
    print()
    stage_timings["ExifTool metadata"] = time.perf_counter() - stage_started

    stage_started = time.perf_counter()
    infos = build_media_info(
        paths,
        metadata_map,
        source_root,
        show_progress=True,
    )
    finish_stage_progress()
    stage_timings["来源/xattr与媒体分析"] = time.perf_counter() - stage_started

    stage_started = time.perf_counter()

    substage_started = time.perf_counter()
    still_to_video, _ = pair_live_photos(infos)
    stage_timings["  Live/Motion Photo 配对"] = time.perf_counter() - substage_started

    substage_started = time.perf_counter()
    (
        variant_family_map,
        variant_problems,
        confirmed_variant_pairs,
        ambiguous_variant_pairs,
    ) = build_photo_variant_families(infos)
    problems.extend(variant_problems)
    stage_timings["  Apple 多格式照片关联"] = time.perf_counter() - substage_started

    substage_started = time.perf_counter()
    groups, planning_problems = build_groups(
        infos,
        still_to_video,
        source_root,
        variant_family_map,
    )
    stage_timings["  Sidecar/媒体组构建"] = time.perf_counter() - substage_started

    problems.extend(planning_problems)
    stage_timings["媒体组恢复/关联"] = time.perf_counter() - stage_started

    print(
        f"识别 Live/Motion Photo 静态图+视频配对："
        f"{len(still_to_video)} 组"
    )
    print(
        f"确认 Apple 多格式照片关联：{confirmed_variant_pairs} 对；"
        f"关系不确定：{ambiguous_variant_pairs} 对"
    )

    print(
        f"可进入整理流程的媒体组："
        f"{len(groups)}"
    )

    print()

    stats = {
        "groups": len(groups),
        "planned": 0,
        "moved_groups": 0,
        "moved_files": 0,
        "already_correct": 0,
        "conflicts": 0,
        "duplicate_groups": 0,
        "duplicate_files": 0,
        "moved_duplicate_files": 0,
        "metadata_variant_review_groups": 0,
        "move_failures": 0,
        "tagged_custom_files": 0,
        "tag_failures": 0,
    }

    reserved_destinations: Set[str] = set()
    stage_started = time.perf_counter()

    for index, group in enumerate(
        groups,
        start=1,
    ):
        print("-" * 78)
        print_stage_progress("整理进度", index, len(groups), group.description)
        print(
            f"日期来源：{group.date_source}"
        )

        ok, mappings, error, placement = preflight_group(
            group, reserved_destinations
        )

        if not ok:
            reason = error or "未知预检错误"

            if placement == "metadata-variant-review":
                stats["metadata_variant_review_groups"] += 1
                print(f"[同图 metadata variant，需人工确认] {reason}")
                problems.append(Problem(str(group.primary), reason, "warning"))
                continue

            if placement == "duplicate-check-error":
                print(f"[判重失败，跳过整组] {reason}", file=sys.stderr)
                problems.append(
                    Problem(
                        str(group.primary),
                        reason,
                        "failure",
                    )
                )
                continue

            if placement == "duplicate-mixed-conflict":
                stats["conflicts"] += 1
                print(f"[重复/冲突混合，跳过整组] {reason}")
                problems.append(Problem(str(group.primary), reason, "failure"))
                continue

            stats["conflicts"] += 1
            print(f"[跳过整组] {reason}")
            problems.append(
                Problem(
                    str(group.primary),
                    reason,
                    "failure",
                )
            )
            continue

        duplicate_mappings = [
            (source, destination)
            for source, destination in mappings
            if not same_path(source, destination)
            and is_duplicate_destination(destination)
        ]
        if duplicate_mappings:
            stats["duplicate_groups"] += 1
            stats["duplicate_files"] += len(duplicate_mappings)

        if error and placement == "day-fallback":
            print(f"[重号兜底] {error}")
        elif error and placement in ("month-with-duplicates", "day-with-duplicates"):
            print(f"[确认完全重复] {error}")

        # Finder 标签也先做只读预检。若已有标签 plist 无法安全解析，
        # 宁可整组不动，避免覆盖用户原有 Finder 标签。
        try:
            tag_plan = build_custom_filename_tag_plan(mappings, infos)
        except Exception as exc:
            stats["tag_failures"] += 1
            reason = f"自定义文件名 Finder 标签预检失败：{exc}"
            print(f"[跳过整组] {reason}", file=sys.stderr)
            problems.append(Problem(str(group.primary), reason, "failure"))
            continue

        for item in tag_plan:
            state = "已存在" if item.already_had_tag else "将新增"
            print(f"[Finder标签 {state}] {item.source} -> {CUSTOM_FILENAME_FINDER_TAG}")

        for source, destination in mappings:
            if not same_path(source, destination):
                reserved_destinations.add(
                    str(destination.resolve(strict=False)).casefold()
                )

        already_in_place = all(
            same_path(source, destination)
            for source, destination in mappings
        )

        if already_in_place:
            stats["already_correct"] += 1
            print(
                "[已在正确位置] "
                f"{mappings[0][1].parent if mappings else target_dir(group.year, group.month)}"
            )

            if not args.apply or not tag_plan:
                continue

            tag_ok, tag_error, added_count = apply_finder_tag_plan(tag_plan)
            if tag_ok:
                stats["tagged_custom_files"] += added_count
                for item in tag_plan:
                    print(f"[Finder标签成功] {item.target}: {CUSTOM_FILENAME_FINDER_TAG}")
                continue

            stats["tag_failures"] += 1
            reason = tag_error or "未知 Finder 标签写入错误"
            print(f"[Finder标签失败] {group.primary}: {reason}", file=sys.stderr)
            problems.append(Problem(str(group.primary), reason, "failure"))
            continue

        stats["planned"] += 1

        if not args.apply:
            for source, destination in mappings:
                if same_path(source, destination):
                    print(f"[已正确] {source}")
                elif is_format_variant_destination(destination):
                    print(f"[同图非首选格式预览] {source}")
                    print(f"    -> {destination}")
                elif is_duplicate_destination(destination):
                    print(f"[重复副本预览] {source}")
                    print(f"    -> {destination}")
                else:
                    print(f"[预览] {source}")
                    print(f"    -> {destination}")
            continue

        success, move_error, moved = move_group_with_rollback(mappings)

        if not success:
            stats["move_failures"] += 1
            reason = move_error or "未知移动错误"
            print(f"[失败] {group.primary}: {reason}", file=sys.stderr)
            problems.append(Problem(str(group.primary), reason, "failure"))
            continue

        # 文件移动成功后才真正写 Finder 标签。标签失败视为整组事务失败：
        # 恢复原标签 -> 回滚文件 -> 再次确认原路径标签状态。
        tag_ok, tag_error, added_count = apply_finder_tag_plan(tag_plan)
        if not tag_ok:
            stats["tag_failures"] += 1
            rollback_error = rollback_completed_move(moved)
            restore_error = restore_tag_states_at_sources(tag_plan)

            reason = tag_error or "未知 Finder 标签写入错误"
            if rollback_error:
                reason += f"；移动回滚失败：{rollback_error}"
            if restore_error:
                reason += f"；原路径 Finder 标签恢复失败：{restore_error}"

            print(f"[事务失败并回滚] {group.primary}: {reason}", file=sys.stderr)
            problems.append(Problem(str(group.primary), reason, "failure"))
            continue

        stats["moved_groups"] += 1
        stats["moved_files"] += len(moved)
        stats["moved_duplicate_files"] += sum(
            1
            for _, destination in moved
            if is_duplicate_destination(destination)
        )
        stats["tagged_custom_files"] += added_count

        for source, destination in mappings:
            if same_path(source, destination):
                print(f"[原本已正确] {source}")
            elif is_format_variant_destination(destination):
                print(f"[同图非首选格式已隔离] {source}")
                print(f"    -> {destination}")
            elif is_duplicate_destination(destination):
                print(f"[重复副本已隔离] {source}")
                print(f"    -> {destination}")
            else:
                print(f"[成功] {source}")
                print(f"    -> {destination}")

        for item in tag_plan:
            print(f"[Finder标签成功] {item.target}: {CUSTOM_FILENAME_FINDER_TAG}")

    finish_stage_progress()
    stage_timings["目标预检/判重/移动"] = time.perf_counter() - stage_started
    stage_timings["总耗时"] = time.perf_counter() - run_started
    print()
    print("=" * 78)
    print("运行结束")
    print("=" * 78)
    print(f"来源目录：             {source_root}")
    print(f"固定目标：             {destination_root}")
    print(f"重复副本隔离：         {duplicate_root}")
    print(f"可整理媒体组：         {stats['groups']}")
    print(f"需要移动媒体组：       {stats['planned']}")

    if args.apply:
        print(f"成功移动媒体组：       {stats['moved_groups']}")
        print(f"成功移动文件：         {stats['moved_files']}")
        print(f"已隔离重复文件：       {stats['moved_duplicate_files']}")

    print(f"原本已在正确位置：     {stats['already_correct']}")
    print(f"检测到完全重复媒体组： {stats['duplicate_groups']}")
    print(f"计划隔离重复文件：     {stats['duplicate_files']}")
    print(f"同图 metadata variant待人工确认：{stats['metadata_variant_review_groups']}")
    print(f"目标冲突整组跳过：     {stats['conflicts']}")

    if args.apply:
        print(f"移动失败媒体组：       {stats['move_failures']}")
        print(f"新增自定义文件名标签： {stats['tagged_custom_files']}")
        print(f"Finder 标签失败：      {stats['tag_failures']}")

    print()
    print("阶段耗时：")
    for label in (
        "扫描文件",
        "ExifTool metadata",
        "来源/xattr与媒体分析",
        "媒体组恢复/关联",
        "  Live/Motion Photo 配对",
        "  Apple 多格式照片关联",
        "  Sidecar/媒体组构建",
        "目标预检/判重/移动",
        "总耗时",
    ):
        if label in stage_timings:
            print(f"  {label:<22} {stage_timings[label]:8.2f} s")
    print(
        "  SHA-256 缓存"
        f"             命中 {_SHA256_CACHE.hits} / 未命中 {_SHA256_CACHE.misses}"
        + (f" / 缓存错误 {_SHA256_CACHE.errors}" if _SHA256_CACHE.errors else "")
    )

    if args.apply:
        existing_failures = [
            problem for problem in problems if problem.severity == "failure"
        ]
        if existing_failures or stats["move_failures"] or stats["tag_failures"]:
            print()
            print(
                "[阶段归档跳过] 本次存在失败项；来源目录仍保留在整理阶段0，"
                "请处理失败后重新执行。"
            )
        else:
            try:
                archived_source = archive_completed_source_directory(source_root)
                print()
                print(f"[阶段归档成功] {source_root}")
                print(f"    -> {archived_source}")
            except Exception as exc:
                reason = f"apply 已完成，但来源目录移入整理阶段1失败：{exc}"
                problems.append(Problem(str(source_root), reason, "failure"))
                print(f"[阶段归档失败] {reason}", file=sys.stderr)

    failures = [
        problem
        for problem in problems
        if problem.severity == "failure"
    ]

    skips = [
        problem
        for problem in problems
        if problem.severity == "skip"
    ]

    warnings = [
        problem
        for problem in problems
        if problem.severity == "warning"
    ]

    print()
    print(
        f"失败 / 需要人工检查："
        f"{len(failures)}"
    )

    if failures:
        for i, problem in enumerate(
            failures,
            start=1,
        ):
            print(
                f"  {i}. {problem.path}"
            )
            print(
                f"     原因：{problem.reason}"
            )
    else:
        print("  无")

    print_skip_problem_entries(skips)

    print()
    print(f"提示 / 关系不确定：{len(warnings)}")
    if warnings:
        for i, problem in enumerate(warnings, start=1):
            print(f"  {i}. {problem.path}")
            print(f"     原因：{problem.reason}")
    else:
        print("  无")

    if not args.apply:
        print()
        print("当前是 DRY RUN，没有移动任何文件。")
        print("确认预览无误后运行：")
        print(
            f'  python "{Path(__file__).name}" '
            f'"{source_root}" --apply'
        )

    return (
        1
        if args.apply and failures
        else 0
    )


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


def validate_tui_source(value: str) -> Tuple[Optional[Path], Optional[str]]:
    path_text = normalize_tui_path_input(value)
    if not path_text:
        return None, "请输入要处理的目录。"

    source_root = Path(path_text).expanduser().resolve()
    backup_root = BACKUP_APPLE_ROOT.resolve(strict=False)

    if not source_root.exists():
        return None, f"来源目录不存在：{source_root}"
    if not source_root.is_dir():
        return None, f"来源路径不是目录：{source_root}"
    if not path_is_within(source_root, backup_root):
        return None, f"来源目录必须位于 {BACKUP_APPLE_ROOT} 内。"

    return source_root, None


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


def print_tui_header() -> None:
    print("=" * 72)
    print("Gallery Organizer · TUI")
    print(f"版本：{SCRIPT_VERSION}")
    print(f"固定目标：{DESTINATION_ROOT}")
    print(f"重复副本：{DUPLICATE_ROOT}")
    print("=" * 72)


def tui_main(batch_size: int) -> int:
    """Interactive front end. The organizer engine itself remains unchanged."""
    while True:
        clear_terminal()
        print_tui_header()
        print()
        print("请输入要处理的目录（可直接拖入 Terminal 后回车）：")
        try:
            raw_folder = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return 0

        source_root, error = validate_tui_source(raw_folder)
        if error:
            print()
            print(f"错误：{error}")
            print("按任意键重新输入；Ctrl-C 退出。", end="", flush=True)
            try:
                read_tui_key()
            except KeyboardInterrupt:
                print("\n已退出。")
                return 0
            continue

        assert source_root is not None

        clear_terminal()
        print_tui_header()
        print()
        print(f"来源目录：{source_root}")
        print()
        print("请选择运行模式：")
        print("  [默认] DRY RUN  — 除 A 之外按任意键")
        print("  [A]     APPLY    — 立即实际移动")
        print()
        print("Ctrl-C 退出")
        print("选择：", end="", flush=True)

        try:
            key = read_tui_key()
        except KeyboardInterrupt:
            print("\n已退出。")
            return 0

        apply_now = key.upper() == "A"
        args = argparse.Namespace(
            folder=str(source_root),
            apply=apply_now,
            batch_size=batch_size,
        )

        print()
        result = run_organizer(args)

        if apply_now:
            print()
            print("=" * 72)
            print("APPLY 已结束。")
            print("  [Q] 退出")
            print("  [其他任意键] 返回初始界面，重新输入目录")
            print("注意：返回初始界面不会撤销已经成功执行的移动。")
            print("选择：", end="", flush=True)
            try:
                post_key = read_tui_key()
            except KeyboardInterrupt:
                print("\n已退出。")
                return result
            if post_key.upper() == "Q":
                return result
            continue

        print()
        print("=" * 72)
        print("DRY RUN 已结束，没有移动文件。")
        print("  [A] 对同一目录重新检查并执行 APPLY")
        print("  [Q] 退出")
        print("  [其他任意键] 返回初始界面，重新输入目录")
        print("选择：", end="", flush=True)

        try:
            post_key = read_tui_key()
        except KeyboardInterrupt:
            print("\n已退出。")
            return result

        if post_key.upper() == "Q":
            return result

        if post_key.upper() != "A":
            continue

        # APPLY always re-scans from scratch; dry-run state is never reused.
        print()
        apply_args = argparse.Namespace(
            folder=str(source_root),
            apply=True,
            batch_size=batch_size,
        )
        result = run_organizer(apply_args)

        print()
        print("=" * 72)
        print("APPLY 已结束。")
        print("  [Q] 退出")
        print("  [其他任意键] 返回初始界面，重新输入目录")
        print("注意：返回初始界面不会撤销已经成功执行的移动。")
        print("选择：", end="", flush=True)
        try:
            final_key = read_tui_key()
        except KeyboardInterrupt:
            print("\n已退出。")
            return result
        if final_key.upper() == "Q":
            return result


def main() -> int:
    args = parse_args()
    if args.folder is None:
        return tui_main(args.batch_size)
    return run_organizer(args)


if __name__ == "__main__":
    raise SystemExit(main())
