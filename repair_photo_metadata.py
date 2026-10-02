#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Modified: 2026-08-18 21:36 +02:00

"""
Safely repair missing capture-time EXIF for high-confidence old camera JPEGs.

This is the unified successor to repair_photo_metadata_20260818_0218.py.
It keeps the original A-G evidence rules, adds PANO_YYYYMMDD_HHMMSS support,
and generalizes the safe Vivo/JPEG-trailer write strategy from the former
12-file cleanup script.

Candidate scope
---------------
Only JPEG/JPG photos whose basename begins with either:
    IMG_YYYYMMDD_HHMMSS...
    PANO_YYYYMMDD_HHMMSS...
    IMGYYYYMMDDHHMMSS...
are considered. Existing valid DateTimeOriginal or CreateDate is never
replaced automatically. Obvious screenshots are skipped.

Timezone rule (dataset-specific)
--------------------------------
- Set the confirmed UTC offset and applicable date range in config.ini.
- Without a configured offset, all candidates require manual review.

High-confidence evidence rules
------------------------------
A. Strict IMG/PANO filename + filesystem mtime absolute-time agreement <= 90 s.
B. Huawei + GPS agreement <= 2 s + embedded ModifyDate agreement <= 5 s.
C. IMG _mh edited derivative: suffix is later same-day edit/save time <= 24 h.
D. No GPS conflict + embedded ModifyDate agrees with filename <= 2 s.
E. Filesystem BirthTime absolute instant agrees with filename <= 2 s.
F. One user-confirmed same-image GPS derivative (historical allowlist case).
G. Four user-validated 2016 IMG files whose surrounding sequence establishes
   the filename as original capture time (historical allowlist case).

PANO uses the same objective A/B/D/E cross-checks. It does NOT get a
filename-only fallback, and Rule C/F/G remain IMG-specific.

Metadata written
----------------
    EXIF:DateTimeOriginal
    EXIF:CreateDate
    EXIF:OffsetTimeOriginal = configured UTC offset
    EXIF:OffsetTimeDigitized = configured UTC offset
EXIF ModifyDate is not changed.

Transactional JPEG/trailer safety
---------------------------------
For every repair (ordinary JPEG or JPEG with vendor trailer):
1. Add/verify Finder tag "metadata修复" on the original.
2. Create a fresh macOS-preserving backup with ditto.
3. Fingerprint the original JPEG compressed scan region (first SOS through EOI)
   and all bytes after EOI (trailer).
4. Modify a temporary candidate copy, never the original directly.
5. If ExifTool hits the known Vivo trailer minor error, retry ONLY the candidate
   with -m. If that strips the trailer, append this file's own original trailer.
6. Require the compressed scan bytes to remain byte-for-byte identical.
7. Require the final trailer to remain byte-for-byte identical to this file's
   own original trailer (including the empty-trailer case).
8. Verify all target EXIF fields on the candidate.
9. Commit only candidate FILE CONTENT into the existing original inode, thus
   preserving Finder/xattr metadata; restore original filesystem atime/mtime.
10. Verify scan bytes, trailer bytes, EXIF fields and Finder tag again.
11. Any failure restores original content from the fresh backup.

The generalized trailer path deliberately does NOT use a dataset-wide fixed
Vivo trailer SHA-256. Every file is validated against its own original bytes.

Safety
------
- DRY RUN by default; --apply is required to write/move anything.
- No renaming and no overwrite of unrelated files.
- Fresh backup before every attempted EXIF write; backups are never auto-deleted.
- Operational repair failures stay in place.
- Only genuinely unresolved/evidence-insufficient candidates may be moved to:
      the METADATA_REVIEW_ROOT configured in config.ini
- Repaired files remain where they are; run Gallery Organizer afterwards.

Usage
-----
Interactive TUI (default, safest):
    python repair_photo_metadata.py

Preview from CLI:
    python repair_photo_metadata.py "来源目录"

Apply from CLI:
    python repair_photo_metadata.py "来源目录" --apply
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple


from gallery_config import (
    BACKUP_ROOT as BACKUP_APPLE_ROOT,
    SECOND_STAGE_LIBRARY_ROOT as LIBRARY_ROOT,
    METADATA_REVIEW_ROOT as REVIEW_ROOT,
    METADATA_BACKUP_ROOT as BACKUP_BASE,
    METADATA_WORK_ROOT as WORK_BASE,
    CONFIRMED_GPS_DERIVATIVE_FILENAME, CONFIRMED_GPS_DERIVATIVE_UTC,
    VALIDATED_FILENAME_SEQUENCE as VALIDATED_2016_FILENAME_SEQUENCE,
    XATTR_TOOL as XATTR_TOOL_PATH, DITTO_TOOL as DITTO_TOOL_PATH,
    EXIFTOOL_TOOL, REPAIR_UTC_OFFSET_MINUTES, REPAIR_OFFSET_TEXT, REPAIR_AUTO_FROM,
    REPAIR_AUTO_UNTIL, REPAIR_REVIEW_MONTHS,
)
SCRIPT_VERSION = "2026-08-18-2136-tui-timing"

REPAIR_TZ = timezone(timedelta(minutes=REPAIR_UTC_OFFSET_MINUTES or 0))

RULE_A_MTIME_TOLERANCE_SECONDS = 90
RULE_B_GPS_TOLERANCE_SECONDS = 2
RULE_B_MODIFY_TOLERANCE_SECONDS = 5
RULE_D_MODIFY_TOLERANCE_SECONDS = 2
RULE_E_BIRTHTIME_TOLERANCE_SECONDS = 2
RULE_C_MAX_EDIT_DELAY_SECONDS = 24 * 60 * 60

FINDER_TAG_NAME = "metadata修复"
FINDER_TAG_STORED_VALUE = "metadata修复\n0"
FINDER_TAG_XATTR = "com.apple.metadata:_kMDItemUserTags"
XATTR_TOOL = Path(XATTR_TOOL_PATH)
DITTO_TOOL = Path(DITTO_TOOL_PATH)

JPEG_EXTENSIONS = {".jpg", ".jpeg"}

# Exact automatic camera names. A duplicate suffix such as (0002) is accepted.
STRICT_CAMERA_RE = re.compile(
    r"^(IMG|PANO)_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(?:\(\d+\))?$",
    re.IGNORECASE,
)
# Prefix form allows known derivatives/suffixes while still extracting capture time.
PREFIX_CAMERA_RE = re.compile(
    r"^(IMG|PANO)_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})",
    re.IGNORECASE,
)
# Some Huawei/Vivo exports omit the separators entirely, e.g.
# IMG20181004110122.jpg. Keep this separate from the normal prefix form so
# strict filename checks continue to apply only to the canonical format.
COMPACT_CAMERA_RE = re.compile(
    r"^(IMG)(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})",
    re.IGNORECASE,
)
MH_EDIT_RE = re.compile(
    r"^IMG_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})_mh(\d{13})$",
    re.IGNORECASE,
)

SCREENSHOT_MARKERS = (
    "screenshot",
    "screenshots",
    "screen shot",
    "screen recording",
    "screenrecording",
    "截屏",
    "截图",
    "录屏",
)


@dataclass
class Evidence:
    path: Path
    camera_prefix: str
    filename_time: Optional[datetime]
    strict_filename: bool
    filesystem_mtime_epoch: float
    filesystem_birthtime_epoch: float
    datetime_original: Optional[datetime]
    create_date: Optional[datetime]
    make: str
    model: str
    modify_date: Optional[datetime]
    gps_utc: Optional[datetime]


@dataclass
class Decision:
    repair: bool
    rule: str
    proposed_time: Optional[datetime]
    reason: str


@dataclass
class ResultRow:
    path: str
    status: str
    rule: str
    proposed_time: str
    reason: str
    final_path: str = ""
    backup_path: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair high-confidence missing capture-time EXIF for IMG/PANO JPEGs."
    )
    parser.add_argument(
        "source",
        nargs="?",
        help=(
            "Source directory to recursively scan. Must be inside the configured BACKUP_ROOT. "
            "Omit it to start the interactive TUI."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write metadata and move unresolved photos. Without this flag, preview only.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="ExifTool metadata read batch size (default: 200).",
    )
    parser.add_argument(
        "--tag-test-only",
        action="store_true",
        help="Only test Finder tag writing on the first repairable photo. No EXIF changes/moves.",
    )
    parser.add_argument(
        "--repair-test-only",
        action="store_true",
        help="Fully repair/verify only the first repairable photo. No review moves.",
    )
    parser.add_argument(
        "--trust-compact-filename-time",
        action="store_true",
        help="Opt in to repairing compact IMGYYYYMMDDHHMMSS names from filename time only.",
    )
    return parser.parse_args()


def same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve(strict=False) == b.resolve(strict=False)
    except OSError:
        return os.path.abspath(str(a)) == os.path.abspath(str(b))


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except (ValueError, OSError):
        return False


def require_tools() -> str:
    exiftool = shutil.which(EXIFTOOL_TOOL)
    if not exiftool:
        raise RuntimeError("没有找到 ExifTool。安装：brew install exiftool")
    if not XATTR_TOOL.exists():
        raise RuntimeError(f"找不到 macOS 原生 xattr：{XATTR_TOOL}")
    if not DITTO_TOOL.exists():
        raise RuntimeError(f"找不到 macOS 原生 ditto：{DITTO_TOOL}")
    return exiftool


def require_exiftool() -> str:
    tool = shutil.which(EXIFTOOL_TOOL)
    if not tool:
        print("错误：没有找到 ExifTool。安装：brew install exiftool", file=sys.stderr)
        raise SystemExit(2)
    return tool


def is_obvious_screenshot(path: Path) -> bool:
    text = " / ".join(part.lower() for part in path.parts)
    return any(marker in text for marker in SCREENSHOT_MARKERS)


def should_skip_directory(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    if path.is_symlink():
        return True
    if same_path(path, BACKUP_BASE) or path_is_within(path, BACKUP_BASE):
        return True
    if same_path(path, WORK_BASE) or path_is_within(path, WORK_BASE):
        return True
    # When scanning a parent tree, do not recursively consume the review bucket.
    # If REVIEW_ROOT itself is explicitly supplied as source, its root files are still scanned.
    if same_path(path, REVIEW_ROOT) or path_is_within(path, REVIEW_ROOT):
        return True
    return False


def iter_candidate_jpegs(source: Path) -> Iterator[Path]:
    for current_dir, dirnames, filenames in os.walk(source, topdown=True, followlinks=False):
        current = Path(current_dir)
        kept: List[str] = []
        for name in dirnames:
            p = current / name
            if should_skip_directory(p):
                continue
            kept.append(name)
        dirnames[:] = kept

        for name in filenames:
            if name.startswith("."):
                continue
            path = current / name
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() not in JPEG_EXTENSIONS:
                continue
            if is_obvious_screenshot(path):
                continue
            if PREFIX_CAMERA_RE.match(path.stem) or COMPACT_CAMERA_RE.match(path.stem):
                yield path


def chunks(items: Sequence[Path], size: int) -> Iterator[List[Path]]:
    if size <= 0:
        size = 200
    for i in range(0, len(items), size):
        yield list(items[i:i + size])


def plain_tag_name(key: str) -> str:
    return key.split(":")[-1]


def metadata_values(metadata: dict, tag: str) -> List[str]:
    values: List[str] = []
    for key, value in metadata.items():
        if plain_tag_name(key) != tag:
            continue
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text:
                values.append(text)
    return values


def first_metadata_value(metadata: dict, tag: str) -> Optional[str]:
    vals = metadata_values(metadata, tag)
    return vals[0] if vals else None


def parse_exif_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    m = re.match(
        r"^\s*(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})",
        value,
    )
    if not m:
        return None
    parts = [int(x) for x in m.groups()]
    if parts[0] == 0 or parts[1] == 0 or parts[2] == 0:
        return None
    try:
        return datetime(*parts)
    except ValueError:
        return None


def parse_gps_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    m = re.match(
        r"^\s*(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z?\s*$",
        value,
        re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return datetime(*[int(x) for x in m.groups()], tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_filename_time(path: Path) -> Tuple[str, Optional[datetime], bool]:
    strict = STRICT_CAMERA_RE.fullmatch(path.stem)
    match = strict or PREFIX_CAMERA_RE.match(path.stem)
    if not match:
        match = COMPACT_CAMERA_RE.match(path.stem)
    if not match:
        return "", None, False
    prefix = match.group(1).upper()
    values = [int(match.group(i)) for i in range(2, 8)]
    try:
        return prefix, datetime(*values), strict is not None
    except ValueError:
        return prefix, None, strict is not None


def parse_mh_edit_time(path: Path) -> Optional[datetime]:
    match = MH_EDIT_RE.fullmatch(path.stem)
    if not match:
        return None
    millis = int(match.group(7))
    try:
        utc_dt = datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return utc_dt.astimezone(REPAIR_TZ).replace(tzinfo=None)


def auto_timezone_is_known(filename_time: datetime) -> bool:
    if REPAIR_UTC_OFFSET_MINUTES is None or REPAIR_AUTO_FROM is None or REPAIR_AUTO_UNTIL is None:
        return False
    if REPAIR_AUTO_FROM is not None and filename_time < REPAIR_AUTO_FROM:
        return False
    if REPAIR_AUTO_UNTIL is not None and filename_time >= REPAIR_AUTO_UNTIL:
        return False
    return (filename_time.year, filename_time.month) not in REPAIR_REVIEW_MONTHS


def read_metadata_batch(exiftool: str, paths: Sequence[Path]) -> Dict[str, dict]:
    if not paths:
        return {}
    cmd = [
        exiftool,
        "-j", "-G1", "-charset", "filename=UTF8",
        "-DateTimeOriginal", "-CreateDate",
        "-OffsetTimeOriginal", "-OffsetTimeDigitized",
        "-GPSDateTime", "-GPSDateStamp", "-GPSTimeStamp",
        "-Make", "-Model", "-ModifyDate", "-FileType", "-MIMEType",
        *[str(p) for p in paths],
    ]
    cp = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if cp.returncode not in (0, 1):
        raise RuntimeError(cp.stderr.strip() or f"ExifTool failed ({cp.returncode})")
    try:
        rows = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"无法解析 ExifTool JSON：{exc}") from exc
    result: Dict[str, dict] = {}
    for row in rows:
        src = row.get("SourceFile")
        if src:
            result[str(Path(src))] = row
    return result


def build_evidence(path: Path, metadata: dict) -> Evidence:
    prefix, filename_time, strict = parse_filename_time(path)
    try:
        st = path.stat()
        mtime = st.st_mtime
        birthtime = getattr(st, "st_birthtime", float("nan"))
    except OSError:
        mtime = float("nan")
        birthtime = float("nan")
    return Evidence(
        path=path,
        camera_prefix=prefix,
        filename_time=filename_time,
        strict_filename=strict,
        filesystem_mtime_epoch=mtime,
        filesystem_birthtime_epoch=birthtime,
        datetime_original=parse_exif_datetime(first_metadata_value(metadata, "DateTimeOriginal")),
        create_date=parse_exif_datetime(first_metadata_value(metadata, "CreateDate")),
        make=(first_metadata_value(metadata, "Make") or "").strip(),
        model=(first_metadata_value(metadata, "Model") or "").strip(),
        modify_date=parse_exif_datetime(first_metadata_value(metadata, "ModifyDate")),
        gps_utc=parse_gps_utc(first_metadata_value(metadata, "GPSDateTime")),
    )


def seconds_between_naive(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds())


def rule_name(letter: str, e: Evidence, suffix: str) -> str:
    # Keep the historical letter while making PANO decisions obvious in reports.
    return f"{letter}-{e.camera_prefix.lower()}-{suffix}"


def decide_repair(e: Evidence, *, trust_compact_filename_time: bool = False) -> Decision:
    if e.datetime_original is not None:
        return Decision(False, "existing-DateTimeOriginal", None,
                        "已有有效 DateTimeOriginal，不修改也不汇总到待处理目录")
    if e.create_date is not None:
        return Decision(False, "existing-CreateDate", None,
                        "已有有效 CreateDate；为避免覆盖现有内嵌日期，不自动修改")
    if e.filename_time is None or e.camera_prefix not in {"IMG", "PANO"}:
        return Decision(False, "no-filename-time", None, "文件名无法解析完整拍摄时间")
    if not auto_timezone_is_known(e.filename_time):
        return Decision(False, "timezone-review", None,
                        "拍摄时区或日期范围未在 config.ini 中确认，必须人工确认")

    if trust_compact_filename_time and COMPACT_CAMERA_RE.match(e.path.stem):
        return Decision(
            True, "H-img-compact-filename-confirmed", e.filename_time,
            "用户确认文件名时间采用配置的拍摄时区；按 compact IMG 文件名恢复",
        )

    # Historical Rule F: IMG-only, exact user-confirmed same-image derivative.
    if (
        e.camera_prefix == "IMG"
        and CONFIRMED_GPS_DERIVATIVE_UTC is not None
        and e.path.name.lower() == CONFIRMED_GPS_DERIVATIVE_FILENAME.lower()
        and e.gps_utc is not None
        and abs((e.gps_utc - CONFIRMED_GPS_DERIVATIVE_UTC).total_seconds()) <= 1
    ):
        gps_china = e.gps_utc.astimezone(REPAIR_TZ).replace(tzinfo=None)
        return Decision(
            True, "F-img-user-confirmed-gps-derivative", gps_china,
            "用户已确认与原图画面相同；采用保留下来的 GPS 拍摄时刻",
        )

    # Historical Rule G: IMG-only explicit validated sequence.
    if e.camera_prefix == "IMG" and e.path.name.lower() in VALIDATED_2016_FILENAME_SEQUENCE:
        return Decision(
            True, "G-img-validated-2016-filename-sequence", e.filename_time,
            "已人工验证同一序列的文件名时间；按文件名恢复",
        )

    # A: strict camera filename + filesystem absolute-time agreement.
    if e.strict_filename and not math.isnan(e.filesystem_mtime_epoch):
        filename_epoch = e.filename_time.replace(tzinfo=REPAIR_TZ).timestamp()
        diff = abs(filename_epoch - e.filesystem_mtime_epoch)
        if diff <= RULE_A_MTIME_TOLERANCE_SECONDS:
            return Decision(
                True, rule_name("A", e, "filename-mtime"), e.filename_time,
                f"标准 {e.camera_prefix} 文件名按 +08:00 与 filesystem mtime 绝对时刻相差 {diff:.0f} 秒",
            )

    # B: Huawei + GPS + ModifyDate cross-check, available to IMG and PANO.
    if e.make.upper() == "HUAWEI" and e.gps_utc is not None and e.modify_date is not None:
        filename_utc = e.filename_time.replace(tzinfo=REPAIR_TZ).astimezone(timezone.utc)
        gps_diff = abs((filename_utc - e.gps_utc).total_seconds())
        modify_diff = seconds_between_naive(e.filename_time, e.modify_date)
        if gps_diff <= RULE_B_GPS_TOLERANCE_SECONDS and modify_diff <= RULE_B_MODIFY_TOLERANCE_SECONDS:
            return Decision(
                True, rule_name("B", e, "huawei-gps-modify"), e.filename_time,
                f"Huawei {e.camera_prefix}：GPS 与文件名相差 {gps_diff:.0f} 秒；ModifyDate 相差 {modify_diff:.0f} 秒",
            )

    # C: IMG _mh only.
    if e.camera_prefix == "IMG":
        mh_edit_time = parse_mh_edit_time(e.path)
        if mh_edit_time is not None:
            delay = (mh_edit_time - e.filename_time).total_seconds()
            if 0 < delay <= RULE_C_MAX_EDIT_DELAY_SECONDS and mh_edit_time.date() == e.filename_time.date():
                return Decision(
                    True, "C-img-mh-edited-derivative", e.filename_time,
                    f"_mh 后缀为稍后编辑/保存时间 {mh_edit_time:%Y-%m-%d %H:%M:%S}；"
                    f"比文件名拍摄时间晚 {delay:.0f} 秒且仍为同一天",
                )

    # D: ModifyDate confirms filename, no GPS present.
    if e.gps_utc is None and e.modify_date is not None:
        modify_diff = seconds_between_naive(e.filename_time, e.modify_date)
        if modify_diff <= RULE_D_MODIFY_TOLERANCE_SECONDS:
            return Decision(
                True, rule_name("D", e, "filename-modify"), e.filename_time,
                f"无 GPS 冲突；内嵌 ModifyDate 与 {e.camera_prefix} 文件名相差 {modify_diff:.0f} 秒",
            )

    # E: BirthTime absolute instant confirms filename.
    if not math.isnan(e.filesystem_birthtime_epoch):
        filename_epoch = e.filename_time.replace(tzinfo=REPAIR_TZ).timestamp()
        birth_diff = abs(filename_epoch - e.filesystem_birthtime_epoch)
        if birth_diff <= RULE_E_BIRTHTIME_TOLERANCE_SECONDS:
            return Decision(
                True, rule_name("E", e, "filename-birthtime"), e.filename_time,
                f"{e.camera_prefix} 文件名按 +08:00 与 filesystem BirthTime 绝对时刻相差 {birth_diff:.0f} 秒",
            )

    details: List[str] = []
    if e.strict_filename and not math.isnan(e.filesystem_mtime_epoch):
        filename_epoch = e.filename_time.replace(tzinfo=REPAIR_TZ).timestamp()
        details.append(f"mtime差 {abs(filename_epoch - e.filesystem_mtime_epoch):.0f} 秒")
    if not math.isnan(e.filesystem_birthtime_epoch):
        filename_epoch = e.filename_time.replace(tzinfo=REPAIR_TZ).timestamp()
        details.append(f"BirthTime差 {abs(filename_epoch - e.filesystem_birthtime_epoch):.0f} 秒")
    if e.make:
        details.append(f"设备 {e.make} {e.model}".strip())
    if e.gps_utc is not None:
        filename_utc = e.filename_time.replace(tzinfo=REPAIR_TZ).astimezone(timezone.utc)
        details.append(f"GPS差 {abs((filename_utc - e.gps_utc).total_seconds()):.0f} 秒")
    if e.modify_date is not None:
        details.append(f"ModifyDate差 {seconds_between_naive(e.filename_time, e.modify_date):.0f} 秒")
    if e.camera_prefix == "IMG":
        mh = parse_mh_edit_time(e.path)
        if mh is not None:
            details.append(f"_mh编辑时间 {mh:%Y-%m-%d %H:%M:%S}")
    suffix = "；".join(details) if details else "缺少足够交叉验证字段"
    return Decision(False, "insufficient-evidence", None, "不满足高置信修复规则：" + suffix)


# ------------------------- Finder tags -------------------------

def run_xattr(args: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(XATTR_TOOL), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def finder_tag_base_name(tag: str) -> str:
    return tag.split("\n", 1)[0]


def read_finder_tags(path: Path) -> List[str]:
    listed = run_xattr([str(path)])
    if listed.returncode != 0:
        raise RuntimeError(listed.stderr.strip() or listed.stdout.strip() or f"xattr list failed ({listed.returncode})")
    names = {line.strip() for line in listed.stdout.splitlines() if line.strip()}
    if FINDER_TAG_XATTR not in names:
        return []
    printed = run_xattr(["-px", FINDER_TAG_XATTR, str(path)])
    if printed.returncode != 0:
        raise RuntimeError(printed.stderr.strip() or printed.stdout.strip() or f"xattr read failed ({printed.returncode})")
    hex_text = re.sub(r"[^0-9A-Fa-f]", "", printed.stdout)
    if not hex_text:
        raise RuntimeError("Finder 标签 xattr 存在但内容为空")
    try:
        value = plistlib.loads(bytes.fromhex(hex_text))
    except Exception as exc:
        raise RuntimeError(f"无法解析 Finder 标签二进制 plist：{exc}") from exc
    if not isinstance(value, list):
        raise RuntimeError("Finder 标签 xattr 不是数组，拒绝覆盖")
    return [v for v in value if isinstance(v, str)]


def write_finder_tags(path: Path, tags: Sequence[str]) -> None:
    payload = plistlib.dumps(list(tags), fmt=plistlib.FMT_BINARY, sort_keys=False)
    written = run_xattr(["-wx", FINDER_TAG_XATTR, payload.hex(), str(path)])
    if written.returncode != 0:
        raise RuntimeError(written.stderr.strip() or written.stdout.strip() or f"xattr write failed ({written.returncode})")


def add_and_verify_finder_tag(path: Path, tag_name: str) -> Tuple[bool, str]:
    try:
        existing = read_finder_tags(path)
        if not any(finder_tag_base_name(t) == tag_name for t in existing):
            updated = list(existing)
            updated.append(FINDER_TAG_STORED_VALUE)
            write_finder_tags(path, updated)
        verified = read_finder_tags(path)
        if not any(finder_tag_base_name(t) == tag_name for t in verified):
            return False, f"Finder 标签写入后验证失败：{tag_name}"
        return True, f"Finder 标签已确认：{tag_name}"
    except Exception as exc:
        return False, f"Finder 标签失败：{exc}"


# ------------------------- backup / raw JPEG safety -------------------------

def ditto_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    cp = subprocess.run(
        [str(DITTO_TOOL), "--rsrc", "--extattr", str(source), str(destination)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip() or f"ditto failed ({cp.returncode})")


def backup_file(source: Path, source_root: Path, run_backup_root: Path) -> Path:
    try:
        rel = source.relative_to(source_root)
    except ValueError:
        rel = Path(source.name)
    dest = run_backup_root / rel
    if dest.exists():
        raise FileExistsError(f"备份目标已存在：{dest}")
    ditto_copy(source, dest)
    return dest


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


STANDALONE_MARKERS = {0x01, 0xD8, 0xD9, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7}


def jpeg_sos_and_eoi(data: bytes) -> Tuple[int, int]:
    """Return offsets (first SOS marker, byte immediately after EOI)."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise ValueError("不是标准 JPEG SOI")
    i = 2
    first_sos: Optional[int] = None
    in_scan = False
    while i < len(data):
        if not in_scan:
            if data[i] != 0xFF:
                raise ValueError(f"JPEG marker expected at offset {i}")
            marker_start = i
            while i < len(data) and data[i] == 0xFF:
                i += 1
            if i >= len(data):
                raise ValueError("JPEG marker truncated")
            marker = data[i]
            i += 1
            if marker == 0x00:
                raise ValueError("unexpected FF00 outside scan")
            if marker == 0xD9:
                if first_sos is None:
                    raise ValueError("EOI before SOS")
                return first_sos, i
            if marker in STANDALONE_MARKERS:
                continue
            if i + 2 > len(data):
                raise ValueError("JPEG segment length truncated")
            seglen = int.from_bytes(data[i:i+2], "big")
            if seglen < 2:
                raise ValueError("invalid JPEG segment length")
            seg_end = i + seglen
            if seg_end > len(data):
                raise ValueError("JPEG segment exceeds file")
            if marker == 0xDA:
                if first_sos is None:
                    first_sos = marker_start
                i = seg_end
                in_scan = True
            else:
                i = seg_end
            continue

        ff = data.find(b"\xff", i)
        if ff < 0 or ff + 1 >= len(data):
            raise ValueError("JPEG scan has no EOI")
        j = ff + 1
        while j < len(data) and data[j] == 0xFF:
            j += 1
        if j >= len(data):
            raise ValueError("JPEG marker truncated inside scan")
        marker = data[j]
        if marker == 0x00:
            i = j + 1
            continue
        if 0xD0 <= marker <= 0xD7:
            i = j + 1
            continue
        if marker == 0xD9:
            if first_sos is None:
                raise ValueError("EOI without SOS")
            return first_sos, j + 1
        if marker == 0x01:
            i = j + 1
            continue
        length_pos = j + 1
        if length_pos + 2 > len(data):
            raise ValueError("JPEG marker length truncated inside scan")
        seglen = int.from_bytes(data[length_pos:length_pos+2], "big")
        if seglen < 2:
            raise ValueError("invalid JPEG marker length inside scan")
        seg_end = length_pos + seglen
        if seg_end > len(data):
            raise ValueError("JPEG marker segment exceeds file")
        if marker == 0xDA:
            i = seg_end
            in_scan = True
        else:
            i = seg_end
            in_scan = False
    raise ValueError("JPEG EOI not found")


@dataclass(frozen=True)
class JpegFingerprint:
    scan_sha256: str
    trailer: bytes
    trailer_sha256: str


def jpeg_fingerprint(path: Path) -> JpegFingerprint:
    data = path.read_bytes()
    sos, eoi_end = jpeg_sos_and_eoi(data)
    scan = data[sos:eoi_end]
    trailer = data[eoi_end:]
    return JpegFingerprint(
        scan_sha256=sha256_bytes(scan),
        trailer=trailer,
        trailer_sha256=sha256_bytes(trailer),
    )


def append_exact_trailer(path: Path, trailer: bytes) -> None:
    if not trailer:
        raise RuntimeError("拒绝追加空 trailer")
    with path.open("ab") as f:
        f.write(trailer)
        f.flush()
        os.fsync(f.fileno())


def copy_content_in_place(source: Path, destination: Path, *, atime_ns: int, mtime_ns: int) -> None:
    with source.open("rb") as src, destination.open("r+b") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.truncate()
        dst.flush()
        os.fsync(dst.fileno())
    os.utime(destination, ns=(atime_ns, mtime_ns))


def restore_from_backup_content(
    backup: Path,
    original: Path,
    *,
    original_atime_ns: int,
    original_mtime_ns: int,
    expected_backup_sha256: str,
) -> Tuple[bool, str]:
    try:
        if sha256_file(backup) != expected_backup_sha256:
            return False, "备份自身 SHA-256 与创建时不一致，拒绝恢复"
        copy_content_in_place(
            backup, original,
            atime_ns=original_atime_ns,
            mtime_ns=original_mtime_ns,
        )
        if sha256_file(original) != expected_backup_sha256:
            return False, "恢复后原文件 SHA-256 与备份不一致"
        ok, msg = add_and_verify_finder_tag(original, FINDER_TAG_NAME)
        if not ok:
            return False, "原内容已恢复，但 Finder 标签未能确认：" + msg
        return True, "已从备份恢复原始文件内容，并确认 Finder 标签"
    except Exception as exc:
        return False, f"恢复失败：{exc}"


# ------------------------- EXIF candidate write / verify -------------------------

def format_exif_time(dt: datetime) -> str:
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def write_candidate_exif(exiftool: str, candidate: Path, proposed: datetime, *, minor: bool = False) -> Tuple[bool, str]:
    dt_text = format_exif_time(proposed)
    cmd = [exiftool]
    if minor:
        cmd.append("-m")
    cmd.extend([
        "-overwrite_original_in_place", "-P",
        f"-EXIF:DateTimeOriginal={dt_text}",
        f"-EXIF:CreateDate={dt_text}",
        f"-EXIF:OffsetTimeOriginal={REPAIR_OFFSET_TEXT}",
        f"-EXIF:OffsetTimeDigitized={REPAIR_OFFSET_TEXT}",
        str(candidate),
    ])
    cp = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    text = "\n".join(x for x in (cp.stderr.strip(), cp.stdout.strip()) if x)
    return cp.returncode == 0, text


def verify_repaired_metadata(exiftool: str, path: Path, proposed: datetime) -> Tuple[bool, str]:
    metadata_map = read_metadata_batch(exiftool, [path])
    md = metadata_map.get(str(path), {})
    dto = parse_exif_datetime(first_metadata_value(md, "DateTimeOriginal"))
    create = parse_exif_datetime(first_metadata_value(md, "CreateDate"))
    offset_original = first_metadata_value(md, "OffsetTimeOriginal")
    offset_digitized = first_metadata_value(md, "OffsetTimeDigitized")
    problems: List[str] = []
    if dto != proposed:
        problems.append(f"DateTimeOriginal={dto!r}")
    if create != proposed:
        problems.append(f"CreateDate={create!r}")
    if offset_original != REPAIR_OFFSET_TEXT:
        problems.append(f"OffsetTimeOriginal={offset_original!r}")
    if offset_digitized != REPAIR_OFFSET_TEXT:
        problems.append(f"OffsetTimeDigitized={offset_digitized!r}")
    if problems:
        return False, "验证失败：" + "；".join(problems)
    return True, "EXIF 四个目标字段验证通过"


def repair_one_transactional(
    exiftool: str,
    original: Path,
    proposed: datetime,
    source_root: Path,
    backup_root: Path,
    work_root: Path,
) -> Tuple[bool, str, Optional[Path]]:
    """Repair one JPEG transactionally; return (ok, message, backup_path)."""
    ok, tag_msg = add_and_verify_finder_tag(original, FINDER_TAG_NAME)
    if not ok:
        return False, tag_msg + "；未执行 EXIF 修复", None

    original_stat = original.stat()
    original_sha = sha256_file(original)
    try:
        original_fp = jpeg_fingerprint(original)
    except Exception as exc:
        return False, f"无法解析原 JPEG 结构，拒绝修复：{exc}", None

    try:
        backup = backup_file(original, source_root, backup_root)
    except Exception as exc:
        return False, f"备份失败：{exc}", None

    backup_sha = sha256_file(backup)
    if backup_sha != original_sha:
        return False, "备份内容 SHA-256 与原文件不一致，拒绝修改", backup

    try:
        rel = original.relative_to(source_root)
    except ValueError:
        rel = Path(original.name)
    candidate = work_root / rel
    candidate.parent.mkdir(parents=True, exist_ok=True)
    if candidate.exists():
        candidate.unlink()
    shutil.copyfile(original, candidate)

    try:
        write_ok, write_msg = write_candidate_exif(exiftool, candidate, proposed, minor=False)
        if not write_ok:
            if "[minor] Error rewriting Vivo trailer" not in write_msg:
                raise RuntimeError(write_msg or "ExifTool 写入失败")
            # Retry from a pristine copy, with -m restricted to the temporary candidate.
            shutil.copyfile(original, candidate)
            write_ok, retry_msg = write_candidate_exif(exiftool, candidate, proposed, minor=True)
            if not write_ok:
                raise RuntimeError("Vivo trailer：-m 临时副本重试失败：" + (retry_msg or "unknown"))
            write_msg = "Vivo trailer minor error；已仅在临时副本使用 -m"

        candidate_fp_after_write = jpeg_fingerprint(candidate)

        # Preserve this file's own original trailer exactly.
        if original_fp.trailer:
            if not candidate_fp_after_write.trailer:
                append_exact_trailer(candidate, original_fp.trailer)
            elif candidate_fp_after_write.trailer != original_fp.trailer:
                raise RuntimeError(
                    "写入后存在非空但不同的 JPEG trailer，拒绝提交；"
                    f"before={original_fp.trailer_sha256} after={candidate_fp_after_write.trailer_sha256}"
                )
        else:
            if candidate_fp_after_write.trailer:
                raise RuntimeError("原文件无 trailer，但写入后产生额外 EOI 后字节，拒绝提交")

        candidate_fp = jpeg_fingerprint(candidate)
        if candidate_fp.scan_sha256 != original_fp.scan_sha256:
            raise RuntimeError(
                "JPEG SOS→EOI 压缩图像数据发生变化，拒绝提交；"
                f"before={original_fp.scan_sha256} after={candidate_fp.scan_sha256}"
            )
        if candidate_fp.trailer != original_fp.trailer:
            raise RuntimeError(
                "最终 candidate trailer 与原文件不一致，拒绝提交；"
                f"before={original_fp.trailer_sha256} after={candidate_fp.trailer_sha256}"
            )

        exif_ok, exif_msg = verify_repaired_metadata(exiftool, candidate, proposed)
        if not exif_ok:
            raise RuntimeError("candidate " + exif_msg)

        # Commit file bytes into existing original inode, preserving xattrs/Finder tags.
        copy_content_in_place(
            candidate, original,
            atime_ns=original_stat.st_atime_ns,
            mtime_ns=original_stat.st_mtime_ns,
        )

        final_fp = jpeg_fingerprint(original)
        if final_fp.scan_sha256 != original_fp.scan_sha256:
            raise RuntimeError("提交后 JPEG 压缩图像数据哈希不一致")
        if final_fp.trailer != original_fp.trailer:
            raise RuntimeError("提交后 trailer 与原始文件不一致")
        exif_ok, exif_msg = verify_repaired_metadata(exiftool, original, proposed)
        if not exif_ok:
            raise RuntimeError("提交后 " + exif_msg)
        tags = read_finder_tags(original)
        if not any(finder_tag_base_name(t) == FINDER_TAG_NAME for t in tags):
            raise RuntimeError("提交后 Finder 标签 metadata修复 丢失")

        trailer_note = (
            f"；原 trailer SHA-256={original_fp.trailer_sha256} 保持一致"
            if original_fp.trailer else "；原文件无 trailer，最终仍无 trailer"
        )
        return True, "修复成功；压缩图像数据未变" + trailer_note + "；" + exif_msg, backup

    except Exception as exc:
        restored, restore_msg = restore_from_backup_content(
            backup,
            original,
            original_atime_ns=original_stat.st_atime_ns,
            original_mtime_ns=original_stat.st_mtime_ns,
            expected_backup_sha256=backup_sha,
        )
        suffix = "" if restored else "【恢复未确认成功】"
        return False, f"{exc}；恢复状态：{restore_msg}{suffix}", backup
    finally:
        try:
            candidate.unlink(missing_ok=True)
        except Exception:
            pass


# ------------------------- review/report -------------------------

def review_destination(path: Path) -> Path:
    return REVIEW_ROOT / path.name


def move_to_review(path: Path) -> Tuple[bool, str, Optional[Path]]:
    if same_path(path.parent, REVIEW_ROOT):
        return True, "已在待人工处理目录", path
    destination = review_destination(path)
    if destination.exists():
        return False, f"待处理目录存在同名文件，禁止覆盖：{destination}", None
    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(destination))
    return True, "已移动到待人工处理目录", destination


def write_report(path: Path, rows: Sequence[ResultRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Path", "Status", "Rule", "ProposedDateTimeOriginal", "Reason", "FinalPath", "BackupPath"])
        for row in rows:
            w.writerow([row.path, row.status, row.rule, row.proposed_time, row.reason, row.final_path, row.backup_path])


def count_rule(repairable: Sequence[Tuple[Evidence, Decision]], prefix: str) -> int:
    return sum(d.rule.startswith(prefix) for _, d in repairable)


def _format_elapsed(seconds: float) -> str:
    return f"{seconds:.2f} s"


def print_stage_timings(timings: Sequence[Tuple[str, float]], total_seconds: float) -> None:
    print()
    print("阶段耗时：")
    for label, seconds in timings:
        print(f"  {label:<28} {_format_elapsed(seconds):>10}")
    print(f"  {'总耗时':<28} {_format_elapsed(total_seconds):>10}")


class ProgressLine:
    """TTY-only single-line progress; avoids scroll-region ANSI state."""

    def __init__(self) -> None:
        self.enabled = bool(sys.stdout.isatty())
        self._visible = False

    def update(self, text: str) -> None:
        if not self.enabled:
            return
        width = max(20, shutil.get_terminal_size((100, 24)).columns - 1)
        rendered = text[:width]
        sys.stdout.write("\r" + rendered.ljust(width))
        sys.stdout.flush()
        self._visible = True

    def clear(self) -> None:
        if not self.enabled or not self._visible:
            return
        width = max(20, shutil.get_terminal_size((100, 24)).columns - 1)
        sys.stdout.write("\r" + (" " * width) + "\r")
        sys.stdout.flush()
        self._visible = False


def clear_terminal() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def normalize_tui_path_input(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    try:
        parts = shlex.split(text)
        if len(parts) == 1:
            return parts[0]
    except ValueError:
        pass
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
    return text.replace("\\ ", " ")


def read_tui_key() -> str:
    if not sys.stdin.isatty():
        try:
            return input().strip()[:1]
        except EOFError:
            return "q"
    try:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print()
        return ch
    except Exception:
        try:
            return input().strip()[:1]
        except EOFError:
            return "q"


def validate_source(source: Path) -> Optional[str]:
    if not source.exists() or not source.is_dir():
        return f"源目录不存在或不是目录：{source}"
    if not path_is_within(source, BACKUP_APPLE_ROOT):
        return f"源目录必须位于 {BACKUP_APPLE_ROOT} 内：{source}"
    return None


def run_once(args: argparse.Namespace) -> int:
    total_started = time.perf_counter()
    timings: List[Tuple[str, float]] = []

    source = Path(args.source).expanduser().resolve()
    source_error = validate_source(source)
    if source_error:
        print(f"错误：{source_error}", file=sys.stderr)
        return 2
    if (args.apply or args.tag_test_only or args.repair_test_only) and (REPAIR_UTC_OFFSET_MINUTES is None or REPAIR_AUTO_FROM is None or REPAIR_AUTO_UNTIL is None):
        print("错误：请先在 config.ini 设置拍摄时区和日期范围。", file=sys.stderr)
        return 2

    exiftool = require_exiftool()

    stage_started = time.perf_counter()
    paths = sorted(iter_candidate_jpegs(source), key=lambda p: str(p).lower())
    timings.append(("扫描候选 JPEG", time.perf_counter() - stage_started))

    print("=" * 92)
    print("Photo Metadata Repair")
    print(f"版本：{SCRIPT_VERSION}")
    print(f"源目录：{source}")
    print(f"待人工处理目录：{REVIEW_ROOT}")
    print("候选命名：IMG_YYYYMMDD_HHMMSS... / PANO_YYYYMMDD_HHMMSS... / IMGYYYYMMDDHHMMSS...")
    print(f"元数据修复时区：{REPAIR_OFFSET_TEXT if REPAIR_UTC_OFFSET_MINUTES is not None else '未配置，全部人工确认'}")
    print("规则 A：严格 IMG/PANO 文件名 + filesystem mtime 绝对时刻差 <= 90 秒")
    print("规则 B：Huawei + GPS 差 <= 2 秒 + ModifyDate 差 <= 5 秒")
    print("规则 C：IMG _mh 后缀为同日稍后编辑时间 -> 按文件名前半段拍摄时间")
    print("规则 D：无 GPS 冲突且 ModifyDate 与文件名差 <= 2 秒")
    print("规则 E：filesystem BirthTime 与文件名绝对时刻差 <= 2 秒")
    print("规则 F/G：保留既有历史人工确认/序列确认，仅 IMG 特例")
    print("写入安全：临时副本 + SOS→EOI 图像字节验证 + 每文件自身 trailer 原样保留")
    print(f"修复前 Finder 标签：{FINDER_TAG_NAME}（保留已有标签）")
    print("模式：" + ("APPLY（写入 + 汇总未修复）" if args.apply else "DRY RUN（只预览）"))
    print("=" * 92)
    print(f"扫描候选 JPEG：{len(paths)}")

    progress = ProgressLine()
    stage_started = time.perf_counter()
    metadata_map: Dict[str, dict] = {}
    effective_batch_size = args.batch_size if args.batch_size > 0 else 200
    batch_count = (len(paths) + effective_batch_size - 1) // effective_batch_size
    for index, batch in enumerate(chunks(paths, args.batch_size), start=1):
        progress.update(f"[metadata] ExifTool 批量读取 {index}/{batch_count} · {len(batch)} 文件")
        metadata_map.update(read_metadata_batch(exiftool, batch))
    progress.clear()
    timings.append(("ExifTool metadata", time.perf_counter() - stage_started))

    stage_started = time.perf_counter()
    decisions: List[Tuple[Evidence, Decision]] = []
    for index, path in enumerate(paths, start=1):
        if index == 1 or index == len(paths) or index % 100 == 0:
            progress.update(f"[evidence] 证据判断 {index}/{len(paths)}")
        ev = build_evidence(path, metadata_map.get(str(path), {}))
        decisions.append((ev, decide_repair(
            ev, trust_compact_filename_time=args.trust_compact_filename_time
        )))
    progress.clear()
    timings.append(("证据构建/规则判断", time.perf_counter() - stage_started))

    repairable = [(e, d) for e, d in decisions if d.repair]
    existing = [(e, d) for e, d in decisions if d.rule in {"existing-DateTimeOriginal", "existing-CreateDate"}]
    unresolved = [
        (e, d) for e, d in decisions
        if not d.repair and d.rule not in {"existing-DateTimeOriginal", "existing-CreateDate"}
    ]

    img_rep = [(e, d) for e, d in repairable if e.camera_prefix == "IMG"]
    pano_rep = [(e, d) for e, d in repairable if e.camera_prefix == "PANO"]
    img_unres = [(e, d) for e, d in unresolved if e.camera_prefix == "IMG"]
    pano_unres = [(e, d) for e, d in unresolved if e.camera_prefix == "PANO"]

    print()
    print(f"可高置信修复：{len(repairable)}（IMG {len(img_rep)}；PANO {len(pano_rep)}）")
    print(f"  A：{count_rule(repairable, 'A-')}")
    print(f"  B：{count_rule(repairable, 'B-')}")
    print(f"  C：{count_rule(repairable, 'C-')}")
    print(f"  D：{count_rule(repairable, 'D-')}")
    print(f"  E：{count_rule(repairable, 'E-')}")
    print(f"  F：{count_rule(repairable, 'F-')}")
    print(f"  G：{count_rule(repairable, 'G-')}")
    print(f"已有有效内嵌日期、不修改：{len(existing)}")
    print(f"仍需人工处理：{len(unresolved)}（IMG {len(img_unres)}；PANO {len(pano_unres)}）")
    print()

    for e, d in repairable:
        assert d.proposed_time is not None
        print(f"[修复] {e.path}\n       -> {format_exif_time(d.proposed_time)} {REPAIR_OFFSET_TEXT}\n       {d.rule}：{d.reason}")
    for e, d in unresolved:
        print(f"[待人工] {e.path}\n         {d.reason}\n         -> {review_destination(e.path)}")

    if args.tag_test_only:
        if not repairable:
            print("没有可修复照片，无法执行 Finder 标签测试。")
            print_stage_timings(timings, time.perf_counter() - total_started)
            return 0
        try:
            require_tools()
        except Exception as exc:
            print(f"错误：工具前置检查失败：{exc}", file=sys.stderr)
            return 2
        e, _ = repairable[0]
        stage_started = time.perf_counter()
        ok, msg = add_and_verify_finder_tag(e.path, FINDER_TAG_NAME)
        timings.append(("Finder 标签测试", time.perf_counter() - stage_started))
        print(("[测试成功] " if ok else "[测试失败] ") + msg)
        print("未修改任何 EXIF，也未移动任何文件。")
        print_stage_timings(timings, time.perf_counter() - total_started)
        return 0 if ok else 1

    if args.repair_test_only:
        if not repairable:
            print("没有可修复照片，无法执行单文件完整修复测试。")
            print_stage_timings(timings, time.perf_counter() - total_started)
            return 0
        try:
            exiftool = require_tools()
        except Exception as exc:
            print(f"错误：工具前置检查失败：{exc}", file=sys.stderr)
            return 2
        e, d = repairable[0]
        assert d.proposed_time is not None
        run_id = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        backup_root = BACKUP_BASE / ("TEST_" + run_id)
        work_root = WORK_BASE / ("TEST_" + run_id)
        stage_started = time.perf_counter()
        ok, msg, backup = repair_one_transactional(exiftool, e.path, d.proposed_time, source, backup_root, work_root)
        timings.append(("单文件事务修复测试", time.perf_counter() - stage_started))
        print(("[测试成功] " if ok else "[测试失败] ") + msg)
        if backup:
            print(f"备份：{backup}")
        print("仅处理了 1 个文件，没有移动任何文件。")
        print_stage_timings(timings, time.perf_counter() - total_started)
        return 0 if ok else 1

    if not args.apply:
        print()
        print("DRY RUN 完成：没有写 Finder 标签、没有修改 metadata，也没有移动任何文件。")
        print_stage_timings(timings, time.perf_counter() - total_started)
        return 0

    try:
        exiftool = require_tools()
    except Exception as exc:
        print(f"错误：工具前置检查失败：{exc}", file=sys.stderr)
        return 2

    run_id = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_root = BACKUP_BASE / run_id
    work_root = WORK_BASE / run_id
    rows: List[ResultRow] = []
    repaired_paths = set()

    stage_started = time.perf_counter()
    for index, (e, d) in enumerate(repairable, start=1):
        assert d.proposed_time is not None
        ok, msg, backup = repair_one_transactional(
            exiftool, e.path, d.proposed_time, source, backup_root, work_root
        )
        if ok:
            repaired_paths.add(e.path)
            print(f"[成功修复 {index}/{len(repairable)}] {e.path}：{msg}")
            rows.append(ResultRow(str(e.path), "repaired", d.rule, format_exif_time(d.proposed_time), d.reason, str(e.path), str(backup or "")))
        else:
            print(f"[失败并原地保留 {index}/{len(repairable)}] {e.path}：{msg}", file=sys.stderr)
            rows.append(ResultRow(str(e.path), "repair-failed", d.rule, format_exif_time(d.proposed_time), msg, str(e.path), str(backup or "")))
    timings.append(("事务修复/逐文件验证", time.perf_counter() - stage_started))

    moved_review = 0
    review_failures = 0
    stage_started = time.perf_counter()
    for index, (e, d) in enumerate(unresolved, start=1):
        if not e.path.exists():
            rows.append(ResultRow(str(e.path), "review-move-failed", d.rule, "", d.reason + "；源文件已不存在"))
            review_failures += 1
            continue
        ok, move_msg, destination = move_to_review(e.path)
        if ok:
            moved_review += 1
            print(f"[汇总待人工 {index}/{len(unresolved)}] {e.path} -> {destination}")
            rows.append(ResultRow(str(e.path), "moved-to-review", d.rule, "", d.reason, str(destination or "")))
        else:
            review_failures += 1
            print(f"[失败 {index}/{len(unresolved)}] 待人工文件未移动：{e.path}：{move_msg}", file=sys.stderr)
            rows.append(ResultRow(str(e.path), "review-move-failed", d.rule, "", d.reason + "；" + move_msg))
    timings.append(("待人工汇总移动", time.perf_counter() - stage_started))

    stage_started = time.perf_counter()
    report_path = REVIEW_ROOT / f"metadata_repair_report_{run_id}.csv"
    write_report(report_path, rows)

    try:
        # Only remove empty temporary run folders; backups are never deleted.
        for p in sorted(work_root.rglob("*"), key=lambda x: len(x.parts), reverse=True) if work_root.exists() else []:
            if p.is_dir() and not any(p.iterdir()):
                p.rmdir()
        if work_root.exists() and not any(work_root.iterdir()):
            work_root.rmdir()
        if WORK_BASE.exists() and not any(WORK_BASE.iterdir()):
            WORK_BASE.rmdir()
    except Exception:
        pass
    timings.append(("报告/临时目录清理", time.perf_counter() - stage_started))

    print()
    print("=" * 92)
    print("执行完成")
    print(f"成功修复：{len(repaired_paths)}")
    print(f"操作失败且原地保留：{len(repairable) - len(repaired_paths)}")
    print(f"汇总到待人工目录：{moved_review}")
    print(f"待人工移动失败：{review_failures}")
    print(f"备份目录：{backup_root}")
    print(f"报告：{report_path}")
    print("修复成功照片仍留原位置；下一步运行 Gallery Organizer 按新 EXIF 归类。")
    print("=" * 92)
    print_stage_timings(timings, time.perf_counter() - total_started)
    return 0 if review_failures == 0 and len(repairable) == len(repaired_paths) else 1


def tui_header() -> None:
    print("=" * 72)
    print("Photo Metadata Repair · TUI")
    print(f"版本：{SCRIPT_VERSION}")
    print("默认 DRY RUN；只有明确按 A 才 APPLY。")
    print("候选：IMG_YYYYMMDD_HHMMSS... / PANO_YYYYMMDD_HHMMSS... JPEG")
    print("=" * 72)


def run_tui(batch_size: int) -> int:
    while True:
        clear_terminal()
        tui_header()
        print()
        print("请输入要修复 metadata 的目录（可拖入 Terminal；Q 退出）：")
        try:
            raw = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if raw.strip().lower() == "q":
            return 0
        normalized = normalize_tui_path_input(raw)
        if not normalized:
            continue
        source = Path(normalized).expanduser().resolve()
        source_error = validate_source(source)
        if source_error:
            print(f"错误：{source_error}")
            print("按任意键返回。")
            read_tui_key()
            continue

        dry_args = argparse.Namespace(
            source=str(source),
            apply=False,
            batch_size=batch_size,
            tag_test_only=False,
            repair_test_only=False,
        )
        print()
        rc = run_once(dry_args)
        if rc != 0:
            print("\nDRY RUN 出错；按任意键返回初始界面。")
            read_tui_key()
            continue

        print()
        print("DRY RUN 已结束，没有修改文件。")
        print("  [A] 对同一目录重新扫描并执行 APPLY")
        print("  [Q] 退出")
        print("  [其他任意键] 返回初始界面")
        key = read_tui_key().lower()
        if key == "q":
            return 0
        if key != "a":
            continue

        print()
        print("将重新扫描同一目录后执行 APPLY；不会复用刚才的 DRY RUN 决策。")
        apply_args = argparse.Namespace(
            source=str(source),
            apply=True,
            batch_size=batch_size,
            tag_test_only=False,
            repair_test_only=False,
        )
        rc = run_once(apply_args)
        print()
        print("APPLY 已结束。")
        print("  [Q] 退出")
        print("  [其他任意键] 返回初始界面")
        key = read_tui_key().lower()
        if key == "q":
            return rc


def main() -> int:
    args = parse_args()
    if args.source is None:
        if args.apply or args.tag_test_only or args.repair_test_only:
            print("错误：--apply/--tag-test-only/--repair-test-only 需要同时提供 source；无 source 时启动安全 TUI。", file=sys.stderr)
            return 2
        return run_tui(args.batch_size)
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
