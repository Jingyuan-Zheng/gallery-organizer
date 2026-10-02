#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整理 Gallery 主库内的字节级重复副本。

默认只预览。--apply 只移动已确认的冗余副本，绝不删除、覆盖、重命名或重新编码。
目标在 Old Duplicate 中镜像其 gallery_config.py 的 BACKUP_ROOT 下的路径。

保留规则：
* 同名 HEIC/JPEG 画面等效时，保留 HEIC 及其 HEIC.xmp，归档无 XMP 的 JPEG。
* 跨目录同名 JPEG 画面等效时，优先保留带 AAE 的版本，归档无配对边车的版本。
* ``name (1).mov`` 与 ``name.mov`` 所有编码流完全相同时，归档无边车的 ``(1)`` 版本。
* 任何无法安全验证边车、人物标注或编辑内容的文件组，都整组保留。
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gallery_config import (
    BACKUP_ROOT, GALLERY_ROOT, MAIN_LIBRARY_ROOT as LIBRARY_ROOT,
    DUPLICATE_ROOT, FILE_TOOL, SIPS_TOOL, EXIFTOOL_TOOL,
    HEIF_CONVERT_TOOL, FFMPEG_TOOL,
)
HASH_WORKERS = min(8, max(1, os.cpu_count() or 4))
HASH_CHUNK_BYTES = 8 * 1024 * 1024
VISUAL_MEAN_DELTA_LIMIT = 7.0

MEDIA_SUFFIXES = {
    ".heic", ".heif", ".hif", ".jpg", ".jpeg", ".png", ".dng", ".mov",
    ".mp4", ".m4v", ".avi", ".xmp", ".aae", ".thm", ".lrv", ".dop", ".pp3",
}
SIDECAR_SUFFIXES = {".xmp", ".aae", ".dop", ".pp3"}
CAPTURE_TIMESTAMP_NAME = re.compile(r"^IMG_\d{8}_\d{6}(?:\.[^.]+)?$", re.IGNORECASE)
COPY_SUFFIX_NAME = re.compile(r"^(.*) \(\d+\)(\.[^.]+)$")
IMAGE_BASE_NAME = re.compile(r"^(IMG_\d+)(?: \(\d+\)|_edited)?$", re.IGNORECASE)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=None)
def visual_hash(path: Path) -> str | None:
    """Hash decoded RGB pixels for JPEGs, ignoring metadata differences."""
    if Image is None or path.suffix.casefold() not in {".jpg", ".jpeg"}:
        return None
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            digest = hashlib.sha256()
            digest.update(f"{image.width}x{image.height}".encode())
            digest.update(image.tobytes())
            return digest.hexdigest()
    except (OSError, ValueError):
        return None


def rendered_pixels(path: Path) -> tuple[int, int, bytes] | None:
    """Decode a scaled preview via sips, including HEIC files unsupported by PIL."""
    if Image is None:
        return None
    if path.suffix.casefold() in {".jpg", ".jpeg", ".png"}:
        try:
            with Image.open(path) as image:
                if ImageOps is not None:
                    image = ImageOps.exif_transpose(image)
                image = image.convert("RGB")
                image.thumbnail((768, 768))
                return image.width, image.height, image.tobytes()
        except (OSError, ValueError):
            return None
    try:
        # sips can return success while producing an all-black preview for
        # some Display P3 HEIC files. Use libheif for HEIC/HEIF instead.
        if path.suffix.casefold() in {".heic", ".heif", ".hif"}:
            descriptor, name = tempfile.mkstemp(suffix=".jpg")
            os.close(descriptor)
            output = Path(name)
            output.unlink()
            result = subprocess.run(
                [HEIF_CONVERT_TOOL, str(path), str(output)],
                text=True, capture_output=True, check=False, timeout=60,
            )
            if result.returncode != 0:
                return None
            with Image.open(output) as image:
                image = ImageOps.exif_transpose(image) if ImageOps is not None else image
                image = image.convert("RGB")
                image.thumbnail((768, 768))
                pixels = image.tobytes()
                if not pixels or max(pixels) == 0:
                    return None
                return image.width, image.height, pixels
        descriptor, name = tempfile.mkstemp(suffix=".png")
        os.close(descriptor)
        output = Path(name)
        output.unlink()
        result = subprocess.run(
            [SIPS_TOOL, "-Z", "768", "-s", "format", "png", str(path), "--out", str(output)],
            text=True, capture_output=True, check=False, timeout=60,
        )
        if result.returncode != 0:
            return None
        with Image.open(output) as image:
            image = image.convert("RGB")
            pixels = image.tobytes()
            if not pixels or max(pixels) == 0:
                return None
            return image.width, image.height, pixels
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    finally:
        if 'output' in locals():
            output.unlink(missing_ok=True)


def visually_equivalent_rendering(first: Path, second: Path) -> bool:
    """Compare decoded previews; allows only normal HEIC/JPEG render variance."""
    left, right = rendered_pixels(first), rendered_pixels(second)
    if not left or not right:
        return False
    variants = [right]
    if left[:2] != right[:2] and Image is not None:
        image = Image.frombytes("RGB", right[:2], right[2])
        variants = []
        for angle in (90, 270):
            rotated = image.rotate(angle, expand=True)
            variants.append((rotated.width, rotated.height, rotated.tobytes()))
    for candidate in variants:
        if left[:2] != candidate[:2]:
            continue
        total = sum(abs(a - b) for a, b in zip(left[2], candidate[2]))
        if total / len(left[2]) <= VISUAL_MEAN_DELTA_LIMIT:
            return True
    return False


def duplicate_key(path: Path) -> tuple[str, str]:
    visual = visual_hash(path)
    return ("visual", visual) if visual else ("bytes", sha256(path))


def image_base(path: Path) -> str | None:
    match = IMAGE_BASE_NAME.fullmatch(path.stem)
    return match.group(1) if match else None


def xmp_annotations_present(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return False
    return any(marker in text for marker in (
        "PersonInImage", "mwg-rs:Regions", "MPRI:Regions", "MP:RegionInfo",
        "<dc:subject", "<photoshop:SupplementalCategories", "<xmp:Rating>",
    ))


XMP_LIST_TAGS = {
    "PersonInImage": "XMP-iptcExt:PersonInImage",
    "Subject": "XMP-dc:Subject",
    "SupplementalCategories": "XMP-photoshop:SupplementalCategories",
}
XMP_REGION_GROUPS = {
    "RegionInfo": "XMP-mwg-rs:all",
    "RegionInfoMP": "XMP-MP:all",
}
XMP_LIST_SEPARATOR = "|"


def read_xmp_annotations(path: Path) -> dict[str, object] | None:
    """Read only mergeable annotation fields as structured JSON."""
    if not path.is_file():
        return {}
    command = [
        EXIFTOOL_TOOL, "-j", "-struct",
        "-XMP-iptcExt:PersonInImage", "-XMP-mwg-rs:all", "-XMP-MP:all",
        "-XMP-dc:Subject", "-XMP-photoshop:SupplementalCategories",
        "-XMP-xmp:Rating", str(path),
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True,
                                check=False, timeout=30)
        values = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    if result.returncode != 0 or len(values) != 1:
        return None
    values[0].pop("SourceFile", None)
    return values[0]


def annotation_merge_analysis(source_xmp: Path, keep_xmp: Path) -> tuple[bool, list[str]]:
    """Allow unions and missing fields; reject any structured/scalar conflict."""
    source = read_xmp_annotations(source_xmp)
    keep = read_xmp_annotations(keep_xmp)
    if source is None or keep is None:
        return False, ["XMP 无法结构化读取"]
    changes: list[str] = []
    conflicts: list[str] = []
    for key in XMP_LIST_TAGS:
        source_values = source.get(key, [])
        keep_values = keep.get(key, [])
        if not isinstance(source_values, list):
            source_values = [source_values]
        if not isinstance(keep_values, list):
            keep_values = [keep_values]
        additions = [value for value in source_values if value not in keep_values]
        if additions:
            changes.append(f"{key} 新增 {additions}")
    for key in XMP_REGION_GROUPS:
        if key not in source:
            continue
        if key not in keep:
            changes.append(f"{key} 新增完整人脸区域")
        elif source[key] != keep[key]:
            conflicts.append(f"{key} 人脸区域冲突")
    if "Rating" in source:
        if "Rating" not in keep:
            changes.append(f"Rating 新增 {source['Rating']}")
        elif source["Rating"] != keep["Rating"]:
            conflicts.append(f"Rating 冲突：{keep['Rating']} vs {source['Rating']}")
    if conflicts:
        return False, conflicts
    return True, changes or ["没有新增字段；来源 XMP 为冗余副本"]


def visual_merge_plan(
    source: Path, keep: Path,
    sidecars_by_key: dict[tuple[Path, str], list[Path]],
) -> tuple[Path, Path, Path, list[str], bool] | None:
    """Return (source XMP, kept XMP, authoritative HEIC) for a safe merge."""
    base = image_base(source)
    if not base or base != image_base(keep):
        return None
    heic = source.parent / f"{base}.HEIC"
    source_xmp = sidecars_by_key.get((source.parent, source.name.casefold()), [])
    keep_xmps = sidecars_by_key.get((keep.parent, keep.name.casefold()), [])
    if (not heic.is_file() or len(source_xmp) != 1 or len(keep_xmps) > 1
            or not xmp_annotations_present(source_xmp[0])):
        return None
    # A missing kept-JPEG XMP is not a reason to retain a second JPEG with the
    # exact same decoded pixels. Create the sidecar during apply, merge only
    # annotations, and leave GPS authoritative in the embedded HEIC/JPEG data.
    keep_xmp = keep_xmps[0] if keep_xmps else keep.with_name(keep.name + ".xmp")
    safe, details = annotation_merge_analysis(source_xmp[0], keep_xmp)
    return source_xmp[0], keep_xmp, heic, details, safe


def is_plain_cross_number_jpeg_duplicate(
    source: Path, keep: Path,
    sidecars_by_key: dict[tuple[Path, str], list[Path]],
) -> bool:
    """Allow a same-pixel JPEG with no companion relation to be archived.

    This covers an imported file that reused an IMG number belonging to an
    unrelated HEIC. The JPEG itself must have no XMP/AAE, and any same-number
    HEIC must have a different capture time, so its sidecar is not detached.
    """
    if (source.suffix.casefold() not in {".jpg", ".jpeg"}
            or keep.suffix.casefold() not in {".jpg", ".jpeg"}
            or image_base(source) == image_base(keep)):
        return False
    source_keys = {(source.parent, source.name.casefold()), (source.parent, source.stem.casefold())}
    if any(sidecars_by_key.get(key) for key in source_keys):
        return False
    base = image_base(source)
    heic = source.parent / f"{base}.HEIC" if base else None
    return not heic or not heic.is_file() or capture_timestamp(heic) != capture_timestamp(source)


def edited_jpeg_heic_plan(
    source_root: Path,
) -> list[tuple[Path, Path, Path, list[str], bool]]:
    """Find sole JPEG renderings that are redundant only when no AAE exists."""
    plans: list[tuple[Path, Path, Path, list[str], bool]] = []
    for jpeg in source_root.rglob("*_edited.jpeg"):
        if not jpeg.is_file():
            continue
        base = image_base(jpeg)
        if not base:
            continue
        heic = jpeg.with_name(f"{base}.HEIC")
        aae = jpeg.with_name(f"{base}.AAE")
        source_xmp = jpeg.with_name(jpeg.name + ".xmp")
        heic_xmp = heic.with_name(heic.name + ".xmp")
        # With an AAE, this may be the only baked rendering of the edit.  It is
        # eligible only when another same-pixel JPEG is handled by the exact
        # duplicate pass above; never archive the last JPEG rendering here.
        if (heic.is_file() and not aae.is_file() and source_xmp.is_file()
                and xmp_annotations_present(source_xmp)
                and visually_equivalent_rendering(heic, jpeg)):
            safe, details = annotation_merge_analysis(source_xmp, heic_xmp)
            plans.append((jpeg, source_xmp, heic, details, safe))
    return plans


def merge_xmp_annotations(source_xmp: Path, keep_xmp: Path, heic: Path) -> bool:
    """Merge annotations transactionally; never overwrite conflicting face data."""
    safe, _details = annotation_merge_analysis(source_xmp, keep_xmp)
    if not safe:
        return False
    source = read_xmp_annotations(source_xmp)
    keep = read_xmp_annotations(keep_xmp)
    if source is None or keep is None:
        return False
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{keep_xmp.name}.", suffix=".tmp.xmp", dir=keep_xmp.parent,
    )
    os.close(descriptor)
    temp_xmp = Path(temp_name)
    temp_xmp.unlink()
    try:
        if keep_xmp.exists():
            shutil.copy2(keep_xmp, temp_xmp)
        else:
            create = [
                EXIFTOOL_TOOL, "-o", str(temp_xmp), "-all=",
                "-TagsFromFile", str(source_xmp),
                "-XMP-iptcExt:PersonInImage", "-XMP-mwg-rs:all", "-XMP-MP:all",
                "-XMP-dc:Subject", "-XMP-photoshop:SupplementalCategories",
                "-XMP-xmp:Rating", str(source_xmp),
            ]
            result = subprocess.run(create, text=True, capture_output=True,
                                    check=False, timeout=30)
            if result.returncode != 0 or not temp_xmp.is_file():
                return False

        command = [EXIFTOOL_TOOL, "-overwrite_original", "-sep", XMP_LIST_SEPARATOR]
        for key, tag in XMP_LIST_TAGS.items():
            source_values = source.get(key, [])
            keep_values = keep.get(key, [])
            if not isinstance(source_values, list):
                source_values = [source_values]
            if not isinstance(keep_values, list):
                keep_values = [keep_values]
            merged = list(keep_values)
            merged.extend(value for value in source_values if value not in merged)
            if merged != keep_values:
                command.append(f"-{tag}={XMP_LIST_SEPARATOR.join(str(value) for value in merged)}")
        copy_tags: list[str] = []
        for key, group in XMP_REGION_GROUPS.items():
            if key in source and key not in keep:
                copy_tags.append(f"-{group}")
        if "Rating" in source and "Rating" not in keep:
            copy_tags.append("-XMP-xmp:Rating")
        if copy_tags:
            command.extend(["-TagsFromFile", str(source_xmp), *copy_tags])
        command.extend(["-GPS*=", "-XMP-exif:GPS*=", "-XMP-photoshop:GPS*=", str(temp_xmp)])
        result = subprocess.run(command, text=True, capture_output=True,
                                check=False, timeout=30)
        if result.returncode != 0:
            return False
        verified, remaining = annotation_merge_analysis(source_xmp, temp_xmp)
        if not verified or remaining != ["没有新增字段；来源 XMP 为冗余副本"]:
            return False
        os.replace(temp_xmp, keep_xmp)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        temp_xmp.unlink(missing_ok=True)


def redundant_numbered_aaes(source_root: Path) -> list[tuple[Path, Path]]:
    """Find copy-suffixed JPEG companions whose AAE is semantically redundant."""
    pairs: list[tuple[Path, Path]] = []
    for jpeg in source_root.rglob("*"):
        if not jpeg.is_file() or jpeg.suffix.casefold() not in {".jpg", ".jpeg"}:
            continue
        base = image_base(jpeg)
        if not base or " (" not in jpeg.stem:
            continue
        numbered = jpeg.with_suffix(".AAE")
        canonical = jpeg.parent / f"{base}.AAE"
        if (numbered.is_file() and canonical.is_file() and adjustment_data_hash(numbered)
                and adjustment_data_hash(numbered) == adjustment_data_hash(canonical)):
            pairs.append((numbered, canonical))
    return pairs


def redundant_jpeg_renderings(source_root: Path) -> list[tuple[Path, Path, bool]]:
    """Find HEIC/JPEG compatibility copies while preserving Live Photo assets."""
    candidates: list[tuple[Path, Path, bool]] = []
    for jpeg in source_root.rglob("*"):
        if not jpeg.is_file() or jpeg.suffix.casefold() not in {".jpg", ".jpeg"}:
            continue
        base = image_base(jpeg)
        if not base or jpeg.stem != base:
            continue
        heic = jpeg.with_suffix(".HEIC")
        if not heic.is_file() and jpeg.parent.parent == source_root:
            parent_heic = source_root / f"{base}.HEIC"
            if parent_heic.is_file() and capture_timestamp(parent_heic) == capture_timestamp(jpeg):
                heic = parent_heic
        aae = jpeg.with_name(f"{base}.AAE")
        mov = jpeg.with_suffix(".MOV")
        jpeg_xmp = jpeg.with_name(jpeg.name + ".xmp")
        # An AAE means one JPEG rendering must remain because it may contain the
        # baked edit. Same-pixel extra JPEGs are still handled by the exact
        # duplicate pass. A matching MOV is retained, never moved.
        if not heic.is_file() or aae.is_file() or jpeg_xmp.is_file():
            continue
        candidates.append((jpeg, heic, mov.is_file()))

    def confirmed(candidate: tuple[Path, Path, bool]) -> tuple[Path, Path, bool] | None:
        jpeg, heic, has_mov = candidate
        return candidate if visually_equivalent_rendering(heic, jpeg) else None

    with ThreadPoolExecutor(max_workers=HASH_WORKERS) as pool:
        return [pair for pair in pool.map(confirmed, candidates) if pair is not None]


@lru_cache(maxsize=None)
def media_stream_hashes(path: Path) -> tuple[str, ...] | None:
    """Hash every encoded MOV stream while ignoring container-only metadata."""
    try:
        result = subprocess.run(
            [
                FFMPEG_TOOL, "-v", "error", "-i", str(path), "-map", "0",
                "-c", "copy", "-f", "streamhash", "-hash", "sha256", "-",
            ],
            text=True, capture_output=True, check=False, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    hashes = tuple(sorted(line.strip() for line in result.stdout.splitlines() if line.strip()))
    return hashes if result.returncode == 0 and hashes else None


def redundant_copy_suffix_movs(source_root: Path) -> list[tuple[Path, Path]]:
    """Find ``name (1).mov`` copies whose encoded streams equal ``name.mov``.

    QuickTime creation/modify timestamps may differ even when every video,
    audio and Apple metadata stream is byte-identical. Only archive the copy
    suffix version, and only when it has no independent XMP/AAE sidecar.
    """
    results: list[tuple[Path, Path]] = []
    for source in source_root.rglob("*"):
        if not source.is_file() or source.suffix.casefold() != ".mov":
            continue
        match = COPY_SUFFIX_NAME.fullmatch(source.name)
        if not match:
            continue
        keep = source.with_name(match.group(1) + match.group(2))
        if not keep.is_file() or keep.suffix.casefold() != ".mov":
            continue
        source_sidecars = (
            source.with_name(source.name + ".xmp"),
            source.with_suffix(".xmp"),
            source.with_suffix(".AAE"),
            source.with_suffix(".aae"),
        )
        if any(path.is_file() for path in source_sidecars):
            continue
        source_hashes = media_stream_hashes(source)
        if source_hashes and source_hashes == media_stream_hashes(keep):
            results.append((source, keep))
    return results


@lru_cache(maxsize=None)
def capture_timestamp(path: Path) -> str:
    """Read the original capture timestamp used for cross-directory matching."""
    try:
        result = subprocess.run(
            [EXIFTOOL_TOOL, "-s3", "-DateTimeOriginal", str(path)],
            text=True, capture_output=True, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def actual_type(path: Path) -> str:
    """仅用于主库版本的排序；失败时返回空字符串，不影响 SHA-256 判定。"""
    try:
        return subprocess.run(
            [FILE_TOOL, "-b", str(path)], text=True, capture_output=True,
            check=False, timeout=15,
        ).stdout.casefold()
    except (OSError, subprocess.SubprocessError):
        return ""


def extension_matches_content(path: Path) -> bool:
    kind = actual_type(path)
    suffix = path.suffix.casefold()
    expected = {
        ".mov": ("quicktime", "iso media"), ".mp4": ("iso media",),
        ".heic": ("heif", "iso media"), ".heif": ("heif", "iso media"),
        ".jpg": ("jpeg",), ".jpeg": ("jpeg",), ".png": ("png",),
        ".xmp": ("xml",), ".aae": ("xml",),
    }
    return any(token in kind for token in expected.get(suffix, ()))


def is_sidecar(path: Path) -> bool:
    return path.suffix.casefold() in SIDECAR_SUFFIXES


def companion_key(path: Path) -> str:
    """返回同目录主文件的匹配键。

    ``IMG_0001.HEIC.xmp`` 对应 ``IMG_0001.HEIC``，而 ``IMG_0001.AAE``
    对应任意扩展名为 ``IMG_0001`` 的主媒体。
    """
    if path.suffix.casefold() == ".xmp":
        return path.with_suffix("").name.casefold()
    return path.stem.casefold()


def is_camera_style_name(path: Path) -> bool:
    """相机常用 IMG_ 名优先于随机哈希、导入工具生成的文件名。"""
    return path.name.casefold().startswith("img_")


@lru_cache(maxsize=None)
def camera_model(path: Path) -> str:
    """读取嵌入的相机型号；失败返回空字符串，不从文件名推断设备。"""
    try:
        result = subprocess.run(
            [SIPS_TOOL, "-g", "model", str(path)], text=True, capture_output=True,
            check=False, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"^\s*model:\s*(.+?)\s*$", result.stdout, re.MULTILINE)
    if not match:
        return ""
    model = match.group(1).strip()
    return "" if model == "<nil>" else model


def naming_preference(path: Path) -> int:
    """按设备类型选择主库文件名；数值越小越优先。

    非 iPhone 相机的时间戳文件名优先，iPhone 则保留原始 IMG_连续编号。
    没有可读型号时不臆测设备，退回到既有的 IMG_ 命名优先原则。
    """
    model = camera_model(path).casefold()
    timestamp_name = bool(CAPTURE_TIMESTAMP_NAME.fullmatch(path.name))
    if model and "iphone" not in model and timestamp_name:
        return 0
    if model and "iphone" in model and is_camera_style_name(path) and not timestamp_name:
        return 0
    if is_camera_style_name(path):
        return 1
    return 2


def canonical_score(path: Path, source_root: Path) -> tuple[int, int, int, int, int, str]:
    """分数越小越优先留在主库。仅在同一 SHA-256 组内使用。"""
    name = path.name.casefold()
    # 实际类型与扩展名一致优先，避免把 JPEG 误名为 .MOV 的文件留在主库。
    wrong_extension = 0 if extension_matches_content(path) else 1
    # A JPEG with its own non-empty AAE is the only file that can render that
    # edit; prefer it over an otherwise identical JPEG with no AAE.
    base = image_base(path)
    aae_companion = 0 if (
        path.suffix.casefold() in {".jpg", ".jpeg"} and base
        and (path.parent / f"{base}.AAE").is_file()
    ) else 1
    # 非 iPhone 优先拍摄时间命名；iPhone 优先原始连续编号；均优先于随机哈希名。
    name_preference = naming_preference(path)
    # 无“(2)”这类复制编号的名称优先。
    copy_name = 1 if " (" in name or " copy" in name else 0
    # 月目录直系文件优先于历史误放进日期子目录的副本。
    nesting = len(path.relative_to(source_root).parts)
    edited_name = 1 if "_edited" in path.stem.casefold() else 0
    return wrong_extension, aae_companion, name_preference, edited_name, copy_name, nesting, str(path).casefold()


def duplicate_target(source: Path) -> Path:
    try:
        relative = source.resolve(strict=False).relative_to(BACKUP_ROOT.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"来源不在 Backup Apple 中：{source}") from exc
    return DUPLICATE_ROOT / relative


def rename_unnecessary_copy_suffixes(source_root: Path) -> int:
    """Remove numeric copy suffixes only when the clean name is unused."""
    renames: list[tuple[Path, Path]] = []
    for path in source_root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in MEDIA_SUFFIXES:
            continue
        name_to_clean = path.with_suffix("").name if path.suffix.casefold() == ".xmp" else path.name
        match = COPY_SUFFIX_NAME.fullmatch(name_to_clean)
        if not match:
            continue
        clean_name = match.group(1) + match.group(2)
        target = path.with_name(clean_name + ".xmp") if path.suffix.casefold() == ".xmp" else path.with_name(clean_name)
        if not target.exists():
            # Do not detach an AAE/XMP sidecar whose clean name is occupied.
            if not is_sidecar(path):
                sidecar_conflict = any(
                    p.is_file() and p.suffix.casefold() in SIDECAR_SUFFIXES
                    and p.stem.casefold() == path.stem.casefold()
                    and p.with_name(re.sub(r" \(\d+\)(?=\.[^.]+$)", "", p.name)).exists()
                    for p in path.parent.iterdir()
                )
                if sidecar_conflict:
                    continue
            renames.append((path, target))
    for source, target in sorted(renames, key=lambda pair: str(pair[0]).casefold()):
        source.rename(target)
        print(f"RENAME {source}\n  ->  {target}")
    return len(renames)


def scan(source_root: Path) -> tuple[list[list[Path]], list[Path]]:
    candidates = [
        p for p in source_root.rglob("*")
        if p.is_file() and p.suffix.casefold() in MEDIA_SUFFIXES
    ]
    by_size: dict[int, list[Path]] = {}
    for path in candidates:
        by_size.setdefault(path.stat().st_size, []).append(path)
    # Byte duplicates can be narrowed by size. Visual JPEG duplicates may have
    # different metadata sizes, so all JPEGs must also be considered.
    hash_candidates = list({
        p for paths in by_size.values() if len(paths) > 1 for p in paths
    } | {
        p for p in candidates if p.suffix.casefold() in {".jpg", ".jpeg"}
    })
    with ThreadPoolExecutor(max_workers=HASH_WORKERS) as pool:
        hashes = dict(zip(hash_candidates, pool.map(duplicate_key, hash_candidates)))
    by_hash: dict[tuple[str, str], list[Path]] = {}
    for path, digest in hashes.items():
        by_hash.setdefault(digest, []).append(path)
    return [paths for paths in by_hash.values() if len(paths) > 1], candidates


@lru_cache(maxsize=None)
def xmp_is_metadata_only(path: Path) -> bool:
    """是否为 osxphotos 导出的基础 metadata XMP，而非编辑/人物/标签 sidecar。

    此类 XMP 只重复记录照片内已有的拍摄时间、GPS 等信息；若其主照片是精确
    重复副本，可随该副本一同归档。解析失败或出现任何用户标注/区域/调整信号，
    一律返回 False，保留整组文件。
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return False
    if 'x:xmptk="osxphotos"' not in text or "SidecarForExtension" not in text:
        return False
    markers = (
        "PersonInImage", "mwg-rs:Regions", "MPRI:Regions", "MP:RegionInfo",
        "<dc:subject", "<lr:", "<crs:", "<photoshop:SupplementalCategories",
    )
    if any(marker in text for marker in markers):
        return False
    rating = re.search(r"<xmp:Rating>\s*([^<]+?)\s*</xmp:Rating>", text)
    if rating and rating.group(1).strip() not in {"", "0"}:
        return False
    # 基础 XMP 的 rdf:li 均为空；出现文字通常代表标题、说明、人物或关键词。
    return not bool(re.search(r"<rdf:li(?:\s[^>]*)?>\s*[^<\s][^<]*</rdf:li>", text))


def xmp_annotations_equivalent(first: Path, second: Path) -> bool:
    """Compare mergeable XMP annotations while ignoring RDF array order."""
    left, right = read_xmp_annotations(first), read_xmp_annotations(second)
    if left is None or right is None:
        return False
    def normalized(value: object) -> object:
        if isinstance(value, dict):
            return {key: normalized(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            items = [normalized(item) for item in value]
            return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        return value
    return normalized(left) == normalized(right)


@lru_cache(maxsize=None)
def adjustment_data_hash(path: Path) -> str | None:
    """返回 Apple AAE 的实际编辑参数哈希，不把渲染/时间记录当作编辑内容。"""
    try:
        with path.open("rb") as handle:
            data = plistlib.load(handle).get("adjustmentData")
    except (OSError, plistlib.InvalidFileException):
        return None
    if not isinstance(data, bytes) or not data:
        return None
    return hashlib.sha256(data).hexdigest()


def matching_sidecar_moves(
    source: Path,
    keep: Path,
    sidecars_by_key: dict[tuple[Path, str], list[Path]],
    group_by_path: dict[Path, list[Path]],
    already_planned: set[Path],
) -> list[tuple[Path, Path | None, str, str | None, str]] | None:
    """返回可随主媒体一起移动的边车及其保留项。

    原则上源边车和保留边车必须同属一个 SHA-256 精确重复组，且两端都分别与源/
    保留主媒体同名。例外：基础 metadata XMP 可随来源照片归档；AAE 只要实际
    ``adjustmentData`` 相同，也视为同一编辑参数（渲染标记/时间可不同）。
    其他任一无法配对的边车会返回 ``None``，使整组主媒体保持不动。
    """
    source_keys = {(source.parent, source.name.casefold()), (source.parent, source.stem.casefold())}
    source_sidecars = {
        path for key in source_keys for path in sidecars_by_key.get(key, [])
    } - already_planned
    planned: list[tuple[Path, Path | None, str, str | None, str]] = []
    for sidecar in sorted(source_sidecars, key=lambda p: str(p).casefold()):
        siblings = group_by_path.get(sidecar)
        keep_key = keep.name.casefold() if sidecar.suffix.casefold() == ".xmp" else keep.stem.casefold()
        matching_keeps = sidecars_by_key.get((keep.parent, keep_key), [])
        if sidecar.suffix.casefold() == ".aae":
            if len(matching_keeps) != 1:
                return None
            source_adjustment = adjustment_data_hash(sidecar)
            keep_adjustment = adjustment_data_hash(matching_keeps[0])
            if not source_adjustment or source_adjustment != keep_adjustment:
                return None
            planned.append((
                sidecar, matching_keeps[0], sha256(sidecar), sha256(matching_keeps[0]),
                "AAE 实际 adjustmentData 相同；仅渲染标记或时间不同",
            ))
            continue
        if (sidecar.suffix.casefold() == ".xmp" and len(matching_keeps) == 1
                and xmp_annotations_equivalent(sidecar, matching_keeps[0])):
            planned.append((
                sidecar, matching_keeps[0], sha256(sidecar), sha256(matching_keeps[0]),
                "人物、人脸区域和标签内容相同；仅 XMP 数组顺序不同",
            ))
            continue
        if not siblings:
            if sidecar.suffix.casefold() == ".xmp" and xmp_is_metadata_only(sidecar):
                planned.append((sidecar, None, sha256(sidecar), None, "仅含基础 metadata 的 XMP"))
                continue
            return None
        matching_keeps = [
            path for path in matching_keeps
            if path in siblings
        ]
        if len(matching_keeps) == 1:
            digest = sha256(sidecar)
            planned.append((sidecar, matching_keeps[0], digest, digest, "SHA-256 精确重复边车"))
            continue
        if sidecar.suffix.casefold() == ".xmp" and xmp_is_metadata_only(sidecar):
            planned.append((sidecar, None, sha256(sidecar), None, "仅含基础 metadata 的 XMP"))
            continue
        return None
    return planned


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "directory", type=Path,
        help="要整理的目录（必须位于 gallery_config.py 的 MAIN_LIBRARY_ROOT 内）",
    )
    parser.add_argument("--apply", action="store_true", help="实际移动已确认的精确重复副本")
    args = parser.parse_args()
    source_root = args.directory.expanduser().resolve(strict=False)
    if not source_root.is_dir():
        print(f"指定目录不存在：{source_root}", file=sys.stderr)
        return 2
    try:
        source_root.relative_to(LIBRARY_ROOT.resolve(strict=False))
    except ValueError:
        print(f"指定目录必须位于主库内：{LIBRARY_ROOT}", file=sys.stderr)
        return 2
    if not DUPLICATE_ROOT.is_dir() and args.apply:
        DUPLICATE_ROOT.mkdir(parents=True, exist_ok=True)

    groups, candidates = scan(source_root)
    sidecars_by_key: dict[tuple[Path, str], list[Path]] = {}
    for path in candidates:
        if is_sidecar(path):
            sidecars_by_key.setdefault((path.parent, companion_key(path)), []).append(path)
    group_by_path = {path: group for group in groups for path in group}
    # source, target, reference, source_hash, reference_hash
    moves: list[tuple[Path, Path, Path | None, str, str | None]] = []
    merges: list[tuple[Path, Path, Path, list[str]]] = []
    print(f"扫描目录：{source_root}")
    print(f"重复副本目录：{DUPLICATE_ROOT}")
    print(f"精确重复组：{len(groups)}")
    protected_media = 0
    planned_sidecars: set[Path] = set()
    score = lambda path: canonical_score(path, source_root)
    for paths in sorted(groups, key=lambda group: str(min(group, key=score)).casefold()):
        if is_sidecar(paths[0]):
            continue
        keep = min(paths, key=score)
        digest = sha256(keep)
        print(f"\nKEEP  {keep}")
        print(f"  SHA-256 {digest}")
        for source in sorted((p for p in paths if p != keep), key=lambda p: str(p).casefold()):
            # Identical decoded pixels do not imply identical metadata. Keep
            # visually equal but byte-different media unless their annotations
            # can be merged while retaining HEIC/JPEG GPS as the authority.
            if visual_hash(source) and sha256(source) != digest:
                merge = visual_merge_plan(source, keep, sidecars_by_key)
                if merge is None:
                    if is_plain_cross_number_jpeg_duplicate(source, keep, sidecars_by_key):
                        target = duplicate_target(source)
                        print(
                            f"MOVE  {source}\n  ->  {target}\n"
                            "  原因：跨编号 JPEG 图像数据相同，且无可配对边车；"
                            "同编号 HEIC 与其拍摄时间不同，不关联"
                        )
                        moves.append((source, target, keep, sha256(source), digest))
                        continue
                    protected_media += 1
                    print(
                        f"PROTECT {source}\n"
                        "  原因：画面像素相同但元数据无法安全合并"
                    )
                    continue
                source_xmp, keep_xmp, heic, merge_details, merge_safe = merge
                if not merge_safe:
                    protected_media += 1
                    print(
                        f"PROTECT {source}\n  原因：XMP 字段冲突\n"
                        + "\n".join(f"  {detail}" for detail in merge_details)
                    )
                    continue
                print(
                    f"MERGE XMP  {source_xmp}\n  ->  {keep_xmp}\n"
                    f"  GPS 权威来源：{heic}\n"
                    + "\n".join(f"  {detail}" for detail in merge_details)
                )
                merges.append((source_xmp, keep_xmp, heic, merge_details))
                target = duplicate_target(source)
                print(f"MOVE  {source}\n  ->  {target}")
                moves.append((source, target, keep, sha256(source), digest))
                xmp_target = duplicate_target(source_xmp)
                print(f"MOVE SIDECAR  {source_xmp}\n  ->  {xmp_target}\n  原因：已合并其用户标注")
                moves.append((source_xmp, xmp_target, None, sha256(source_xmp), None))
                planned_sidecars.add(source_xmp)
                numbered_aae = source.with_suffix(".AAE")
                base_aae = source.parent / f"{image_base(source)}.AAE"
                if (numbered_aae != base_aae and numbered_aae.is_file() and base_aae.is_file()
                        and adjustment_data_hash(numbered_aae)
                        and adjustment_data_hash(numbered_aae) == adjustment_data_hash(base_aae)):
                    aae_target = duplicate_target(numbered_aae)
                    print(
                        f"MOVE SIDECAR  {numbered_aae}\n  ->  {aae_target}\n"
                        f"  KEEP SIDECAR  {base_aae}\n"
                        "  原因：AAE 实际 adjustmentData 相同；为去除 JPEG 编号归档冗余配套文件"
                    )
                    moves.append((numbered_aae, aae_target, base_aae, sha256(numbered_aae), sha256(base_aae)))
                    planned_sidecars.add(numbered_aae)
                continue
            sidecar_moves = matching_sidecar_moves(
                source, keep, sidecars_by_key, group_by_path, planned_sidecars,
            )
            if sidecar_moves is None:
                protected_media += 1
                print(f"PROTECT {source}\n  原因：同名边车无法与保留项组成精确重复配对")
                continue
            target = duplicate_target(source)
            print(f"MOVE  {source}\n  ->  {target}")
            moves.append((source, target, keep, sha256(source), digest))
            for sidecar, sidecar_keep, sidecar_hash, sidecar_keep_hash, reason in sidecar_moves:
                sidecar_target = duplicate_target(sidecar)
                if sidecar_keep is None:
                    print(
                        f"MOVE SIDECAR  {sidecar}\n  ->  {sidecar_target}\n"
                        f"  原因：{reason}，随精确重复照片归档"
                    )
                else:
                    print(
                        f"MOVE SIDECAR  {sidecar}\n  ->  {sidecar_target}\n"
                        f"  KEEP SIDECAR  {sidecar_keep}\n  原因：{reason}"
                    )
                moves.append((sidecar, sidecar_target, sidecar_keep, sidecar_hash, sidecar_keep_hash))
                planned_sidecars.add(sidecar)

    for numbered_aae, canonical_aae in redundant_numbered_aaes(source_root):
        if numbered_aae in planned_sidecars:
            continue
        target = duplicate_target(numbered_aae)
        print(
            f"MOVE SIDECAR  {numbered_aae}\n  ->  {target}\n"
            f"  KEEP SIDECAR  {canonical_aae}\n"
            "  原因：AAE 实际 adjustmentData 相同；允许清理 JPEG 的复制编号"
        )
        moves.append((numbered_aae, target, canonical_aae, sha256(numbered_aae), sha256(canonical_aae)))
        planned_sidecars.add(numbered_aae)

    already_planned_sources = {source for source, *_rest in moves}
    for source, keep in redundant_copy_suffix_movs(source_root):
        if source in already_planned_sources:
            continue
        target = duplicate_target(source)
        print(
            f"MOVE STREAM-EQUIVALENT MOV  {source}\n  ->  {target}\n"
            f"  KEEP MOV  {keep}\n"
            "  原因：视频、音频和 Apple 元数据流逐流 SHA-256 完全相同；"
            "仅 QuickTime 容器时间不同"
        )
        moves.append((source, target, keep, sha256(source), sha256(keep)))
        already_planned_sources.add(source)

    for jpeg, heic, has_live_photo_mov in redundant_jpeg_renderings(source_root):
        target = duplicate_target(jpeg)
        print(
            f"MOVE COMPAT JPEG  {jpeg}\n  ->  {target}\n"
            f"  KEEP HEIC  {heic}\n"
            "  原因：同名 HEIC/JPEG 画面等效且无 JPEG XMP；"
            + ("保留同名 Live Photo MOV" if has_live_photo_mov else "非 Live Photo 组")
        )
        moves.append((jpeg, target, heic, sha256(jpeg), sha256(heic)))

    for jpeg, source_xmp, heic, merge_details, merge_safe in edited_jpeg_heic_plan(source_root):
        heic_xmp = heic.with_name(heic.name + ".xmp")
        if source_xmp in planned_sidecars:
            continue
        if not merge_safe:
            protected_media += 1
            print(
                f"PROTECT {jpeg}\n  原因：XMP 字段冲突\n"
                + "\n".join(f"  {detail}" for detail in merge_details)
            )
            continue
        print(
            f"MERGE HEIC XMP  {source_xmp}\n  ->  {heic_xmp}\n"
            f"  GPS 权威来源：{heic}\n"
            + "\n".join(f"  {detail}" for detail in merge_details)
        )
        merges.append((source_xmp, heic_xmp, heic, merge_details))
        jpeg_target = duplicate_target(jpeg)
        xmp_target = duplicate_target(source_xmp)
        print(f"MOVE COMPAT EDITED JPEG  {jpeg}\n  ->  {jpeg_target}")
        print(f"MOVE SIDECAR  {source_xmp}\n  ->  {xmp_target}\n  原因：人脸信息已转移到 HEIC 旁车")
        moves.append((jpeg, jpeg_target, heic, sha256(jpeg), sha256(heic)))
        moves.append((source_xmp, xmp_target, None, sha256(source_xmp), None))
        planned_sidecars.add(source_xmp)

    moved_sources = {source for source, *_rest in moves}
    retained_media = [
        path for path in candidates
        if not is_sidecar(path) and path not in moved_sources
    ]
    retained_media_names = {
        (path.parent, path.name.casefold()) for path in retained_media
    }
    retained_media_stems = {
        (path.parent, path.stem.casefold()) for path in retained_media
    }
    retained_sidecars = [
        path for path in candidates
        if is_sidecar(path) and path not in planned_sidecars
    ]
    paired_retained_sidecars = [
        path for path in retained_sidecars
        if (
            path.suffix.casefold() == ".xmp"
            and (path.parent, companion_key(path)) in retained_media_names
        ) or (
            path.suffix.casefold() != ".xmp"
            and (path.parent, companion_key(path)) in retained_media_stems
        )
    ]
    unpaired_sidecars = [
        path for path in retained_sidecars
        if path not in paired_retained_sidecars
    ]

    if not args.apply:
        print(
            f"\nDRY RUN：计划移动 {len(moves)} 个文件；"
            f"保留 {len(paired_retained_sidecars)} 个已配对边车文件、"
            f"保留 {len(unpaired_sidecars)} 个未找到保留主媒体的边车文件、"
            f"保护 {protected_media} 个主媒体；"
            "未修改任何文件。"
        )
        return 0

    # 先验证整份计划。任何一项不再满足条件都完全不开始移动，保证打包的主媒体和
    # 边车不会因后续条目失败而被拆开。
    for source, target, reference, expected_source_hash, expected_reference_hash in moves:
        if not source.is_file() or (reference is not None and not reference.is_file()):
            print(f"ERROR 来源或保留文件已变化：{source}", file=sys.stderr)
            return 1
        if sha256(source) != expected_source_hash:
            print(f"ERROR SHA-256 复核失败：{source}", file=sys.stderr)
            return 1
        if reference is not None and sha256(reference) != expected_reference_hash:
            print(f"ERROR 保留项 SHA-256 复核失败：{reference}", file=sys.stderr)
            return 1
        if target.exists():
            print(f"ERROR Duplicate 目标已存在，拒绝覆盖：{target}", file=sys.stderr)
            return 1

    for source_xmp, keep_xmp, heic, _merge_details in merges:
        if not source_xmp.is_file() or not heic.is_file():
            print(f"ERROR 合并来源已变化：{source_xmp}", file=sys.stderr)
            return 1
        if not merge_xmp_annotations(source_xmp, keep_xmp, heic):
            print(f"ERROR XMP 元数据合并失败：{source_xmp}", file=sys.stderr)
            return 1
        print(f"MERGED {source_xmp} -> {keep_xmp}")

    moved = 0
    for source, target, _reference, expected_source_hash, _expected_reference_hash in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        if not target.is_file() or sha256(target) != expected_source_hash:
            print(f"ERROR 移动后 SHA-256 验证失败：{target}", file=sys.stderr); return 1
        moved += 1
        print(f"MOVED {source} -> {target}")
    renamed = rename_unnecessary_copy_suffixes(source_root)
    print(f"\n完成：移动 {moved}/{len(moves)} 个重复副本，重命名 {renamed} 个主库文件。")
    return 0 if moved == len(moves) else 1


if __name__ == "__main__":
    raise SystemExit(main())
