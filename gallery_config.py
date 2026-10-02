"""Edit this file to choose media directories and optional command locations.

All scripts use these shared paths. Defaults stay in the current user's Pictures
folder so a fresh checkout contains no personal or machine-specific media paths.
Run scripts without --apply first to preview changes.
"""
from pathlib import Path
import shutil
from datetime import datetime

# Change this to the parent directory that contains all sources and destinations.
BACKUP_ROOT = Path.home() / "Pictures" / "GalleryOrganizer"
GALLERY_ROOT = BACKUP_ROOT / "Gallery"
MAIN_LIBRARY_ROOT = GALLERY_ROOT / "Main"
INBOX_ROOT = BACKUP_ROOT / "Incoming"
SOURCE_STAGE0_ROOT = INBOX_ROOT / "Stage0"
COMPLETED_SOURCE_ROOT = INBOX_ROOT / "Stage1"
SECOND_STAGE_INBOX_ROOT = INBOX_ROOT / "Unsorted"
SECOND_STAGE_LIBRARY_ROOT = GALLERY_ROOT / "SortedFromUnsorted"
DUPLICATE_ROOT = GALLERY_ROOT / "Duplicate"
FORMAT_VARIANT_DUPLICATE_ROOT = DUPLICATE_ROOT / "FormatVariants"
SECOND_STAGE_DUPLICATE_ROOT = DUPLICATE_ROOT / "Unsorted"
OTHER_MEDIA_ROOT = GALLERY_ROOT / "OtherMedia"
REPAIR_MEDIA_ROOT = GALLERY_ROOT / "NeedsRepair"
SCREEN_ROOT = GALLERY_ROOT / "Screenshots"
METADATA_REVIEW_ROOT = GALLERY_ROOT / "MetadataReview"
METADATA_BACKUP_ROOT = GALLERY_ROOT / "MetadataRepairBackups"
METADATA_WORK_ROOT = GALLERY_ROOT / "MetadataRepairWork"
HASH_CACHE_DB = Path.home() / "Library" / "Caches" / "GalleryOrganizer" / "sha256-cache.sqlite3"

# Optional command overrides: replace the executable name with an absolute path.
XATTR_TOOL = shutil.which("xattr") or "/usr/bin/xattr"
DITTO_TOOL = shutil.which("ditto") or "/usr/bin/ditto"
FILE_TOOL = shutil.which("file") or "/usr/bin/file"
SIPS_TOOL = shutil.which("sips") or "/usr/bin/sips"
EXIFTOOL_TOOL = shutil.which("exiftool") or "exiftool"
HEIF_CONVERT_TOOL = shutil.which("heif-convert") or "heif-convert"
FFMPEG_TOOL = shutil.which("ffmpeg") or "ffmpeg"

# Optional evidence for a particular photo collection. Leave empty by default.
CONFIRMED_GPS_DERIVATIVE_FILENAME = ""
CONFIRMED_GPS_DERIVATIVE_UTC = None
VALIDATED_FILENAME_SEQUENCE = set()

# Metadata repair needs a confirmed local timezone. None disables automatic repair.
# Example: UTC+8 -> 8, UTC-5 -> -5. Limit the dates for which this is known.
REPAIR_UTC_OFFSET_HOURS = None
REPAIR_AUTO_FROM = None  # e.g. datetime(2020, 1, 1)
REPAIR_AUTO_UNTIL = None  # e.g. datetime(2024, 7, 1), exclusive
REPAIR_REVIEW_MONTHS = set()  # e.g. {(2018, 9)}
