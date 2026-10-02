"""Load the user-editable config.ini for all gallery scripts."""
from __future__ import annotations

from configparser import ConfigParser
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import shutil

CONFIG_FILE = Path(__file__).with_name("config.ini")
_parser = ConfigParser(interpolation=None)
if not _parser.read(CONFIG_FILE, encoding="utf-8"):
    raise FileNotFoundError(f"找不到配置文件：{CONFIG_FILE}")


_ERRORS = {
    "missing": ("config.ini 缺少 [{section}] {key}", "config.ini is missing [{section}] {key}"),
    "empty_path": ("config.ini 的 [paths] {key} 不能为空", "config.ini [paths] {key} cannot be empty"),
    "date_format": ("config.ini 的 {key} 请填写 YYYYMMDD", "config.ini {key} must use YYYYMMDD"),
    "invalid_date": ("config.ini 的 {key} 不是有效日期：{value}", "config.ini {key} is not a valid date: {value}"),
    "offset_format": ("config.ini 的 utc_offset 请填写 +08:00 这样的时区", "config.ini utc_offset must use a format such as +08:00"),
    "invalid_offset": ("config.ini 的 utc_offset 无效：{value}", "config.ini utc_offset is invalid: {value}"),
    "date_order": ("config.ini 的 auto_from 晚于 auto_through", "config.ini auto_from is later than auto_through"),
    "month_format": ("config.ini 的 review_months 请填写 YYYYMM：{value}", "config.ini review_months must use YYYYMM: {value}"),
    "gps_format": ("config.ini 的 confirmed_gps_derivative_utc 请填写 YYYYMMDDTHHMMSSZ", "config.ini confirmed_gps_derivative_utc must use YYYYMMDDTHHMMSSZ"),
}


def _config_error(code: str, **values: str) -> str:
    chinese, english = _ERRORS[code]
    language = _parser.get("general", "language", fallback="zh").strip().lower()
    return (english if language == "en" else chinese).format(**values)


def _value(section: str, key: str) -> str:
    try:
        value = _parser[section][key].strip()
    except KeyError as exc:
        raise ValueError(_config_error("missing", section=section, key=key)) from exc
    return value


LANGUAGE = _value("general", "language").lower()
if LANGUAGE not in {"zh", "en"}:
    raise ValueError("config.ini: [general] language must be zh or en")


def _path(key: str, *, root: Path | None = None) -> Path:
    value = _value("paths", key)
    if not value:
        raise ValueError(_config_error("empty_path", key=key))
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        path = (root or CONFIG_FILE.parent) / path
    return path.resolve(strict=False)


def _date(key: str) -> datetime | None:
    value = _value("metadata_repair", key)
    if not value:
        return None
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError(_config_error("date_format", key=key))
    try:
        return datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(_config_error("invalid_date", key=key, value=value)) from exc


def _utc_offset() -> int | None:
    value = _value("metadata_repair", "utc_offset")
    if not value:
        return None
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", value)
    if not match:
        raise ValueError(_config_error("offset_format"))
    hours, minutes = int(match[2]), int(match[3])
    if hours > 23 or minutes > 59:
        raise ValueError(_config_error("invalid_offset", value=value))
    offset = (hours * 60 + minutes) * (1 if match[1] == "+" else -1)
    try:
        timezone(timedelta(minutes=offset))
    except ValueError as exc:
        raise ValueError(_config_error("invalid_offset", value=value)) from exc
    return offset


def _tool(key: str, executable: str, fallback: str | None = None) -> str:
    value = _value("tools", key)
    return value or shutil.which(executable) or fallback or executable


BACKUP_ROOT = _path("backup_root")
GALLERY_ROOT = _path("gallery_root", root=BACKUP_ROOT)
MAIN_LIBRARY_ROOT = _path("main_library_root", root=GALLERY_ROOT)
INBOX_ROOT = _path("inbox_root", root=BACKUP_ROOT)
SOURCE_STAGE0_ROOT = _path("source_stage0_root", root=INBOX_ROOT)
COMPLETED_SOURCE_ROOT = _path("completed_source_root", root=INBOX_ROOT)
SECOND_STAGE_LIBRARY_ROOT = _path("second_stage_library_root", root=GALLERY_ROOT)
DUPLICATE_ROOT = _path("duplicate_root", root=GALLERY_ROOT)
FORMAT_VARIANT_DUPLICATE_ROOT = _path("format_variant_duplicate_root", root=DUPLICATE_ROOT)
SECOND_STAGE_DUPLICATE_ROOT = _path("second_stage_duplicate_root", root=DUPLICATE_ROOT)
OTHER_MEDIA_ROOT = _path("other_media_root", root=GALLERY_ROOT)
REPAIR_MEDIA_ROOT = _path("repair_media_root", root=GALLERY_ROOT)
SCREEN_ROOT = _path("screen_root", root=GALLERY_ROOT)
METADATA_REVIEW_ROOT = _path("metadata_review_root", root=GALLERY_ROOT)
METADATA_BACKUP_ROOT = _path("metadata_backup_root", root=GALLERY_ROOT)
METADATA_WORK_ROOT = _path("metadata_work_root", root=GALLERY_ROOT)
HASH_CACHE_DB = _path("hash_cache_db", root=BACKUP_ROOT)

XATTR_TOOL = _tool("xattr", "xattr", "/usr/bin/xattr")
DITTO_TOOL = _tool("ditto", "ditto", "/usr/bin/ditto")
FILE_TOOL = _tool("file", "file", "/usr/bin/file")
SIPS_TOOL = _tool("sips", "sips", "/usr/bin/sips")
EXIFTOOL_TOOL = _tool("exiftool", "exiftool")
HEIF_CONVERT_TOOL = _tool("heif_convert", "heif-convert")
FFMPEG_TOOL = _tool("ffmpeg", "ffmpeg")

REPAIR_UTC_OFFSET_MINUTES = _utc_offset()
REPAIR_OFFSET_TEXT = _value("metadata_repair", "utc_offset") or "+00:00"
REPAIR_AUTO_FROM = _date("auto_from")
_through = _date("auto_through")
REPAIR_AUTO_UNTIL = _through + timedelta(days=1) if _through is not None else None
if REPAIR_AUTO_FROM and _through and REPAIR_AUTO_FROM > _through:
    raise ValueError(_config_error("date_order"))
_months = _value("metadata_repair", "review_months")
REPAIR_REVIEW_MONTHS: set[tuple[int, int]] = set()
for _month in filter(None, (item.strip() for item in _months.split(","))):
    if not re.fullmatch(r"\d{6}", _month) or not 1 <= int(_month[4:]) <= 12:
        raise ValueError(_config_error("month_format", value=_month))
    REPAIR_REVIEW_MONTHS.add((int(_month[:4]), int(_month[4:])))

CONFIRMED_GPS_DERIVATIVE_FILENAME = _value("metadata_repair", "confirmed_gps_derivative_filename")
_gps_utc = _value("metadata_repair", "confirmed_gps_derivative_utc")
if _gps_utc:
    try:
        CONFIRMED_GPS_DERIVATIVE_UTC = datetime.strptime(_gps_utc, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(_config_error("gps_format")) from exc
else:
    CONFIRMED_GPS_DERIVATIVE_UTC = None
VALIDATED_FILENAME_SEQUENCE = {
    item.strip().lower()
    for item in _value("metadata_repair", "validated_filename_sequence").split(",")
    if item.strip()
}
