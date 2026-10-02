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

import sys
from platform_guard import require_macos

require_macos(__file__)

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
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple


from terminal_language import LocalizedArgumentParser, install_terminal_language, localized_input

from gallery_config import (
    LANGUAGE,
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
install_terminal_language(LANGUAGE)
if LANGUAGE == "zh":
    input = localized_input

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
    parser = LocalizedArgumentParser(
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
        raise RuntimeError('ExifTool not found. Installation: brew install exiftool')
    if not XATTR_TOOL.exists():
        raise RuntimeError(f'Cannot find native xattr for macOS: {XATTR_TOOL}')
    if not DITTO_TOOL.exists():
        raise RuntimeError(f'Cannot find native ditto for macOS: {DITTO_TOOL}')
    return exiftool


def require_exiftool() -> str:
    tool = shutil.which(EXIFTOOL_TOOL)
    if not tool:
        print('Error: ExifTool not found. Installation: brew install exiftool', file=sys.stderr)
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
        raise RuntimeError(f'Unable to parse ExifTool JSON: {exc}') from exc
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
                        'There is an effective DateTimeOriginal, which will not be modified or aggregated into the pending directory.')
    if e.create_date is not None:
        return Decision(False, "existing-CreateDate", None,
                        'There is an existing valid CreateDate; to avoid overwriting existing embedded dates, do not automatically modify.')
    if e.filename_time is None or e.camera_prefix not in {"IMG", "PANO"}:
        return Decision(False, "no-filename-time", None, 'The filename does not contain a complete parsable capture time')
    if not auto_timezone_is_known(e.filename_time):
        return Decision(False, "timezone-review", None,
                        'The capture time zone or date range has not been confirmed in config.ini; manual review is required.')

    if trust_compact_filename_time and COMPACT_CAMERA_RE.match(e.path.stem):
        return Decision(
            True, "H-img-compact-filename-confirmed", e.filename_time,
            'The time of user confirmation of the filename is based on the time zone configured during the capture; restore using compact IMG filename.',
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
            'The user has confirmed that it is the same as the original image; using the GPS-captured time from the remaining footage',
        )

    # Historical Rule G: IMG-only explicit validated sequence.
    if e.camera_prefix == "IMG" and e.path.name.lower() in VALIDATED_2016_FILENAME_SEQUENCE:
        return Decision(
            True, "G-img-validated-2016-filename-sequence", e.filename_time,
            'The file names and timestamps of the same sequence have been manually verified; restore by using the file names.',
        )

    # A: strict camera filename + filesystem absolute-time agreement.
    if e.strict_filename and not math.isnan(e.filesystem_mtime_epoch):
        filename_epoch = e.filename_time.replace(tzinfo=REPAIR_TZ).timestamp()
        diff = abs(filename_epoch - e.filesystem_mtime_epoch)
        if diff <= RULE_A_MTIME_TOLERANCE_SECONDS:
            return Decision(
                True, rule_name("A", e, "filename-mtime"), e.filename_time,
                f'Standard {e.camera_prefix} The filename is exactly 08:00 minutes behind the filesystem mtime absolute time. {diff:.0f} seconds',
            )

    # B: Huawei + GPS + ModifyDate cross-check, available to IMG and PANO.
    if e.make.upper() == "HUAWEI" and e.gps_utc is not None and e.modify_date is not None:
        filename_utc = e.filename_time.replace(tzinfo=REPAIR_TZ).astimezone(timezone.utc)
        gps_diff = abs((filename_utc - e.gps_utc).total_seconds())
        modify_diff = seconds_between_naive(e.filename_time, e.modify_date)
        if gps_diff <= RULE_B_GPS_TOLERANCE_SECONDS and modify_diff <= RULE_B_MODIFY_TOLERANCE_SECONDS:
            return Decision(
                True, rule_name("B", e, "huawei-gps-modify"), e.filename_time,
                f'Huawei {e.camera_prefix}: GPS is different from the filename {gps_diff:.0f} seconds; ModifyDate difference {modify_diff:.0f} seconds',
            )

    # C: IMG _mh only.
    if e.camera_prefix == "IMG":
        mh_edit_time = parse_mh_edit_time(e.path)
        if mh_edit_time is not None:
            delay = (mh_edit_time - e.filename_time).total_seconds()
            if 0 < delay <= RULE_C_MAX_EDIT_DELAY_SECONDS and mh_edit_time.date() == e.filename_time.date():
                return Decision(
                    True, "C-img-mh-edited-derivative", e.filename_time,
                    f"_mh suffix indicates the time for editing/saving later. {mh_edit_time:%Y-%m-%d %H:%M:%S}; filmed later than the filename's shooting time {delay:.0f} Seconds and still on the same day",
                )

    # D: ModifyDate confirms filename, no GPS present.
    if e.gps_utc is None and e.modify_date is not None:
        modify_diff = seconds_between_naive(e.filename_time, e.modify_date)
        if modify_diff <= RULE_D_MODIFY_TOLERANCE_SECONDS:
            return Decision(
                True, rule_name("D", e, "filename-modify"), e.filename_time,
                f'No GPS conflict; embedded ModifyDate with {e.camera_prefix} The file names are different. {modify_diff:.0f} seconds',
            )

    # E: BirthTime absolute instant confirms filename.
    if not math.isnan(e.filesystem_birthtime_epoch):
        filename_epoch = e.filename_time.replace(tzinfo=REPAIR_TZ).timestamp()
        birth_diff = abs(filename_epoch - e.filesystem_birthtime_epoch)
        if birth_diff <= RULE_E_BIRTHTIME_TOLERANCE_SECONDS:
            return Decision(
                True, rule_name("E", e, "filename-birthtime"), e.filename_time,
                f'{e.camera_prefix} The filename is 08:00 +08:00 hours away from the filesystem BirthTime absolute time. {birth_diff:.0f} seconds',
            )

    details: List[str] = []
    if e.strict_filename and not math.isnan(e.filesystem_mtime_epoch):
        filename_epoch = e.filename_time.replace(tzinfo=REPAIR_TZ).timestamp()
        details.append(f'mtime difference {abs(filename_epoch - e.filesystem_mtime_epoch):.0f} seconds')
    if not math.isnan(e.filesystem_birthtime_epoch):
        filename_epoch = e.filename_time.replace(tzinfo=REPAIR_TZ).timestamp()
        details.append(f'BirthTime difference {abs(filename_epoch - e.filesystem_birthtime_epoch):.0f} seconds')
    if e.make:
        details.append(f'Equipment {e.make} {e.model}'.strip())
    if e.gps_utc is not None:
        filename_utc = e.filename_time.replace(tzinfo=REPAIR_TZ).astimezone(timezone.utc)
        details.append(f'GPS error {abs((filename_utc - e.gps_utc).total_seconds()):.0f} seconds')
    if e.modify_date is not None:
        details.append(f'ModifyDate difference {seconds_between_naive(e.filename_time, e.modify_date):.0f} seconds')
    if e.camera_prefix == "IMG":
        mh = parse_mh_edit_time(e.path)
        if mh is not None:
            details.append(f'_mh editing time {mh:%Y-%m-%d %H:%M:%S}')
    suffix = "；".join(details) if details else 'Insufficient cross-validation fields'
    return Decision(False, "insufficient-evidence", None, 'Does not meet high-confidence repair rules: ' + suffix)


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
        raise RuntimeError('Finder tag xattr exists but content is empty')
    try:
        value = plistlib.loads(bytes.fromhex(hex_text))
    except Exception as exc:
        raise RuntimeError(f'Unable to parse the Finder tags binary plist: {exc}') from exc
    if not isinstance(value, list):
        raise RuntimeError('Finder tags xattr is not an array, rejected overwriting')
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
            return False, f'Finder tag verification failed after writing: {tag_name}'
        return True, f'Finder tags have been confirmed: {tag_name}'
    except Exception as exc:
        return False, f'Finder tag failed: {exc}'


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
        raise FileExistsError(f'Backup target exists: {dest}')
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
        raise ValueError('Not standard JPEG SOI')
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
        raise RuntimeError('Reject adding empty trailers')
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
            return False, 'Backup your own SHA-256 hash and it will be inconsistent with the creation time, and will be refused to restore.'
        copy_content_in_place(
            backup, original,
            atime_ns=original_atime_ns,
            mtime_ns=original_mtime_ns,
        )
        if sha256_file(original) != expected_backup_sha256:
            return False, 'SHA-256 of the original file after recovery is inconsistent with the backup'
        ok, msg = add_and_verify_finder_tag(original, FINDER_TAG_NAME)
        if not ok:
            return False, 'The original content has been restored, but Finder tags could not be confirmed: ' + msg
        return True, 'Recovered original file contents from a backup and confirmed Finder tags'
    except Exception as exc:
        return False, f'Recovery failed: {exc}'


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
        return False, 'Verification failed: ' + "；".join(problems)
    return True, 'EXIF four target fields verification passed'


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
        return False, tag_msg + '; EXIF repair not executed', None

    original_stat = original.stat()
    original_sha = sha256_file(original)
    try:
        original_fp = jpeg_fingerprint(original)
    except Exception as exc:
        return False, f'Unable to parse original JPEG structure, refuses to repair: {exc}', None

    try:
        backup = backup_file(original, source_root, backup_root)
    except Exception as exc:
        return False, f'Backup failed: {exc}', None

    backup_sha = sha256_file(backup)
    if backup_sha != original_sha:
        return False, 'Backup content SHA-256 is inconsistent with the original file, and refuses to be modified.', backup

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
                raise RuntimeError(write_msg or 'ExifTool writing failed')
            # Retry from a pristine copy, with -m restricted to the temporary candidate.
            shutil.copyfile(original, candidate)
            write_ok, retry_msg = write_candidate_exif(exiftool, candidate, proposed, minor=True)
            if not write_ok:
                raise RuntimeError('Vivo trailer: -m Temporary copy retry failed: ' + (retry_msg or "unknown"))
            write_msg = 'Vivo trailer minor error; used only in temporary copies -m'

        candidate_fp_after_write = jpeg_fingerprint(candidate)

        # Preserve this file's own original trailer exactly.
        if original_fp.trailer:
            if not candidate_fp_after_write.trailer:
                append_exact_trailer(candidate, original_fp.trailer)
            elif candidate_fp_after_write.trailer != original_fp.trailer:
                raise RuntimeError(
                    f'A non-empty but different JPEG trailer exists after writing, reject submission; before={original_fp.trailer_sha256} after={candidate_fp_after_write.trailer_sha256}'
                )
        else:
            if candidate_fp_after_write.trailer:
                raise RuntimeError('The original file lacks a trailer, but after being written, it generates additional EOI bytes, and it is refused to be submitted.')

        candidate_fp = jpeg_fingerprint(candidate)
        if candidate_fp.scan_sha256 != original_fp.scan_sha256:
            raise RuntimeError(
                f'JPEG SOS→EOI Compressed image data has changed, rejected submission; before={original_fp.scan_sha256} after={candidate_fp.scan_sha256}'
            )
        if candidate_fp.trailer != original_fp.trailer:
            raise RuntimeError(
                f'The candidate trailer differs from the original file; refusing to commit. Before={original_fp.trailer_sha256} after={candidate_fp.trailer_sha256}'
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
            raise RuntimeError('JPEG compressed image data hash is inconsistent after submission')
        if final_fp.trailer != original_fp.trailer:
            raise RuntimeError('The trailer is inconsistent with the original file after submission.')
        exif_ok, exif_msg = verify_repaired_metadata(exiftool, original, proposed)
        if not exif_ok:
            raise RuntimeError('After submission ' + exif_msg)
        tags = read_finder_tags(original)
        if not any(finder_tag_base_name(t) == FINDER_TAG_NAME for t in tags):
            raise RuntimeError('Finder tag metadata loss after submission')

        trailer_note = (
            f'; original trailer SHA-256={original_fp.trailer_sha256} Maintain consistency'
            if original_fp.trailer else '; the original file has no trailer, and the result still has none'
        )
        return True, 'Fix successful; compressed image data unchanged' + trailer_note + "；" + exif_msg, backup

    except Exception as exc:
        restored, restore_msg = restore_from_backup_content(
            backup,
            original,
            original_atime_ns=original_stat.st_atime_ns,
            original_mtime_ns=original_stat.st_mtime_ns,
            expected_backup_sha256=backup_sha,
        )
        suffix = "" if restored else '[Recovery not confirmed successfully]'
        return False, f'{exc}; Restore state: {restore_msg}{suffix}', backup
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
        return True, 'Already in the pending manual processing directory', path
    destination = review_destination(path)
    if destination.exists():
        return False, f'A file with the same name exists in the pending folder; refusing to overwrite: {destination}', None
    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(destination))
    return True, 'Moved to the pending manual processing directory', destination


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
    print('Stage durations: ')
    for label, seconds in timings:
        print(f"  {label:<28} {_format_elapsed(seconds):>10}")
    print(f"  {'Total time':<28} {_format_elapsed(total_seconds):>10}")


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
        return f'The source directory does not exist or is not a directory: {source}'
    if not path_is_within(source, BACKUP_APPLE_ROOT):
        return f'Input directory must be within {BACKUP_APPLE_ROOT}: {source}'
    return None


def run_once(args: argparse.Namespace) -> int:
    total_started = time.perf_counter()
    timings: List[Tuple[str, float]] = []

    source = Path(args.source).expanduser().resolve()
    source_error = validate_source(source)
    if source_error:
        print(f'Error: {source_error}', file=sys.stderr)
        return 2
    if (args.apply or args.tag_test_only or args.repair_test_only) and (REPAIR_UTC_OFFSET_MINUTES is None or REPAIR_AUTO_FROM is None or REPAIR_AUTO_UNTIL is None):
        print('Error: Please set the time zone and date range for shooting in config.ini first.', file=sys.stderr)
        return 2

    exiftool = require_exiftool()

    stage_started = time.perf_counter()
    paths = sorted(iter_candidate_jpegs(source), key=lambda p: str(p).lower())
    timings.append(('Scan candidate JPEG', time.perf_counter() - stage_started))

    print("=" * 92)
    print("Photo Metadata Repair")
    print(f'Version: {SCRIPT_VERSION}')
    print(f'Input directory: {source}')
    print(f'Pending manual processing directory: {REVIEW_ROOT}')
    print('Candidate naming: IMG_YYYYMMDD_HHMMSS... / PANO_YYYYMMDD_HHMMSS... / IMGYYYYMMDDHHMMSS...')
    print(f"Time zone for metadata repair: {(REPAIR_OFFSET_TEXT if REPAIR_UTC_OFFSET_MINUTES is not None else 'Not configured, all confirmed manually')}")
    print('Rule A: The absolute time difference between IMG/PANO file names + filesystem mtime must be less than 90 seconds.')
    print('Rule B: Huawei + GPS difference <= 2 seconds + ModifyDate difference <= 5 seconds')
    print('Rule C: IMG _mh suffix with the editing time later on the same day -> take the photo during the half-time of the file name')
    print('Rule D: No GPS conflict and ModifyDate is less than 2 seconds from the filename.')
    print("Rule E: filesystem BirthTime is at least 2 seconds different from the filename's absolute time.")
    print('Rule F/G: Retain existing historical manual confirmation/sequence confirmation, only IMG exceptions')
    print("Safe writing: temporary copy + verify SOS-to-EOI image bytes + preserve each file's original trailer")
    print(f'Finder tags before repair: {FINDER_TAG_NAME}(Keep existing tags)')
    print('Mode: ' + ('APPLY (write + summarize uncorrected)' if args.apply else 'DRY RUN (preview only)'))
    print("=" * 92)
    print(f'Scan the candidate JPEG: {len(paths)}')

    progress = ProgressLine()
    stage_started = time.perf_counter()
    metadata_map: Dict[str, dict] = {}
    effective_batch_size = args.batch_size if args.batch_size > 0 else 200
    batch_count = (len(paths) + effective_batch_size - 1) // effective_batch_size
    for index, batch in enumerate(chunks(paths, args.batch_size), start=1):
        progress.update(f'[metadata] ExifTool batch reading {index}/{batch_count} · {len(batch)} files')
        metadata_map.update(read_metadata_batch(exiftool, batch))
    progress.clear()
    timings.append(("ExifTool metadata", time.perf_counter() - stage_started))

    stage_started = time.perf_counter()
    decisions: List[Tuple[Evidence, Decision]] = []
    for index, path in enumerate(paths, start=1):
        if index == 1 or index == len(paths) or index % 100 == 0:
            progress.update(f'[evidence] Evidence judgment {index}/{len(paths)}')
        ev = build_evidence(path, metadata_map.get(str(path), {}))
        decisions.append((ev, decide_repair(
            ev, trust_compact_filename_time=args.trust_compact_filename_time
        )))
    progress.clear()
    timings.append(('Evidence construction/rule judgment', time.perf_counter() - stage_started))

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
    print(f'Highly reliable repair: {len(repairable)}（IMG {len(img_rep)}；PANO {len(pano_rep)}）')
    print(f"  A：{count_rule(repairable, 'A-')}")
    print(f"  B：{count_rule(repairable, 'B-')}")
    print(f"  C：{count_rule(repairable, 'C-')}")
    print(f"  D：{count_rule(repairable, 'D-')}")
    print(f"  E：{count_rule(repairable, 'E-')}")
    print(f"  F：{count_rule(repairable, 'F-')}")
    print(f"  G：{count_rule(repairable, 'G-')}")
    print(f'There are already valid embedded dates, no modification: {len(existing)}')
    print(f'Still needs manual processing: {len(unresolved)}（IMG {len(img_unres)}；PANO {len(pano_unres)}）')
    print()

    for e, d in repairable:
        assert d.proposed_time is not None
        print(f'[Fix] {e.path}\n       -> {format_exif_time(d.proposed_time)} {REPAIR_OFFSET_TEXT}\n       {d.rule}：{d.reason}')
    for e, d in unresolved:
        print(f'[Waiting for human intervention] {e.path}\n         {d.reason}\n         -> {review_destination(e.path)}')

    if args.tag_test_only:
        if not repairable:
            print('There are no fixable photos, so the Finder Tag test cannot be executed.')
            print_stage_timings(timings, time.perf_counter() - total_started)
            return 0
        try:
            require_tools()
        except Exception as exc:
            print(f'Error: Tool pre-check failed: {exc}', file=sys.stderr)
            return 2
        e, _ = repairable[0]
        stage_started = time.perf_counter()
        ok, msg = add_and_verify_finder_tag(e.path, FINDER_TAG_NAME)
        timings.append(('Finder Tag Test', time.perf_counter() - stage_started))
        print(('[Test successful] ' if ok else '[Test failed] ') + msg)
        print('No EXIF was modified, nor any files moved.')
        print_stage_timings(timings, time.perf_counter() - total_started)
        return 0 if ok else 1

    if args.repair_test_only:
        if not repairable:
            print('There are no fixable photos, so the single-file complete repair test cannot be executed.')
            print_stage_timings(timings, time.perf_counter() - total_started)
            return 0
        try:
            exiftool = require_tools()
        except Exception as exc:
            print(f'Error: Tool pre-check failed: {exc}', file=sys.stderr)
            return 2
        e, d = repairable[0]
        assert d.proposed_time is not None
        run_id = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        backup_root = BACKUP_BASE / ("TEST_" + run_id)
        work_root = WORK_BASE / ("TEST_" + run_id)
        stage_started = time.perf_counter()
        ok, msg, backup = repair_one_transactional(exiftool, e.path, d.proposed_time, source, backup_root, work_root)
        timings.append(('Single file transaction repair test', time.perf_counter() - stage_started))
        print(('[Test successful] ' if ok else '[Test failed] ') + msg)
        if backup:
            print(f'Backup: {backup}')
        print('Processed only 1 file, no files moved.')
        print_stage_timings(timings, time.perf_counter() - total_started)
        return 0 if ok else 1

    if not args.apply:
        print()
        print('DRY RUN completed: No Finder tags were written, no metadata was modified, and no files were moved.')
        print_stage_timings(timings, time.perf_counter() - total_started)
        return 0

    try:
        exiftool = require_tools()
    except Exception as exc:
        print(f'Error: Tool pre-check failed: {exc}', file=sys.stderr)
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
            print(f'[Successfully repaired {index}/{len(repairable)}] {e.path}：{msg}')
            rows.append(ResultRow(str(e.path), "repaired", d.rule, format_exif_time(d.proposed_time), d.reason, str(e.path), str(backup or "")))
        else:
            print(f'[Failed and retained in place {index}/{len(repairable)}] {e.path}：{msg}', file=sys.stderr)
            rows.append(ResultRow(str(e.path), "repair-failed", d.rule, format_exif_time(d.proposed_time), msg, str(e.path), str(backup or "")))
    timings.append(('Transaction repair/file-by-file verification', time.perf_counter() - stage_started))

    moved_review = 0
    review_failures = 0
    stage_started = time.perf_counter()
    for index, (e, d) in enumerate(unresolved, start=1):
        if not e.path.exists():
            rows.append(ResultRow(str(e.path), "review-move-failed", d.rule, "", d.reason + '; The source file does not exist'))
            review_failures += 1
            continue
        ok, move_msg, destination = move_to_review(e.path)
        if ok:
            moved_review += 1
            print(f'[Summary pending manual processing {index}/{len(unresolved)}] {e.path} -> {destination}')
            rows.append(ResultRow(str(e.path), "moved-to-review", d.rule, "", d.reason, str(destination or "")))
        else:
            review_failures += 1
            print(f'[Failure {index}/{len(unresolved)}] File requiring manual review was not moved: {e.path}：{move_msg}', file=sys.stderr)
            rows.append(ResultRow(str(e.path), "review-move-failed", d.rule, "", d.reason + "；" + move_msg))
    timings.append(('Pending manual aggregation and movement', time.perf_counter() - stage_started))

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
    timings.append(('Report/Temporary directory cleanup', time.perf_counter() - stage_started))

    print()
    print("=" * 92)
    print('Completed')
    print(f'Successfully repaired: {len(repaired_paths)}')
    print(f'Operation failed and retained in place: {len(repairable) - len(repaired_paths)}')
    print(f'Summarized into the manual directory: {moved_review}')
    print(f'Failed to move a file for manual review: {review_failures}')
    print(f'Backup directory: {backup_root}')
    print(f'Report: {report_path}')
    print('The photo is restored successfully and remains in its original location; next, run Gallery Organizer to classify it by the new EXIF.')
    print("=" * 92)
    print_stage_timings(timings, time.perf_counter() - total_started)
    return 0 if review_failures == 0 and len(repairable) == len(repaired_paths) else 1


def tui_header() -> None:
    print("=" * 72)
    print("Photo Metadata Repair · TUI")
    print(f'Version: {SCRIPT_VERSION}')
    print('Default DRY RUN; only apply if you explicitly press A.')
    print('Candidate: IMG_YYYYMMDD_HHMMSS... / PANO_YYYYMMDD_HHMMSS... JPEG')
    print("=" * 72)


def run_tui(batch_size: int) -> int:
    while True:
        clear_terminal()
        tui_header()
        print()
        print('Enter the directory whose metadata you want to repair (drag it into Terminal, or press Q to exit): ')
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
            print(f'Error: {source_error}')
            print('Press any key to return.')
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
            print('\nDRY RUN error; press any key to return to the initial interface.')
            read_tui_key()
            continue

        print()
        print('DRY RUN is complete; no files were modified.')
        print('  [A] Scan and execute APPLY for the same directory again')
        print('  [Q] Exit')
        print('  [Any other key] Return to the start screen')
        key = read_tui_key().lower()
        if key == "q":
            return 0
        if key != "a":
            continue

        print()
        print('After re-scanning the same directory, execute APPLY; the DRY RUN decision made previously will not be reused.')
        apply_args = argparse.Namespace(
            source=str(source),
            apply=True,
            batch_size=batch_size,
            tag_test_only=False,
            repair_test_only=False,
        )
        rc = run_once(apply_args)
        print()
        print('The APPLY has ended.')
        print('  [Q] Exit')
        print('  [Any other key] Return to the start screen')
        key = read_tui_key().lower()
        if key == "q":
            return rc


def main() -> int:
    args = parse_args()
    if args.source is None:
        if args.apply or args.tag_test_only or args.repair_test_only:
            print('Error: --apply/--tag-test-only/--repair-test-only must provide source at the same time; start the security TUI when no source is provided.', file=sys.stderr)
            return 2
        return run_tui(args.batch_size)
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
