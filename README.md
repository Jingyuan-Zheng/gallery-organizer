# Gallery Organizer

[中文说明](README.zh-CN.md)

Gallery Organizer is an interactive terminal tool for organizing an exported photo library. It files confirmed camera photos and videos by capture year and month, keeps Live Photo parts and related sidecars together when they can be matched, and separates screenshots, screen recordings, saved images, and confirmed duplicate copies. It shows a sorting preview before you choose to move files; uncertain items are left for your review.

## Why use it?

- **Repeated filenames.** Apple devices and digital cameras can reuse numbered names, so different photos from different periods may have the same filename.
- **Companion files.** Portrait effects, edits, and other information can be stored in separate sidecar files. These need to stay with the right photo or video, even when names repeat.
- **Overlapping imports.** Importing the same library several times, or importing overlapping date ranges, can leave multiple copies of the same media mixed with new files.
- **Mixed media.** Apple library exports can contain camera photos, screenshots, screen recordings, and images saved from other apps in the same folder.
- **Split Live Photos.** An exported Live Photo consists of a photo and a short video. Finder shows them separately, making the pair easy to split or overlook.
- **Missing or incorrect information.** Past use of third-party import tools or incomplete imports can leave capture dates and other metadata missing or wrong, making time-based sorting harder.

## How the workflow runs

1. **Main pass:** Sort confirmed camera photos and videos, keep Live Photo parts and companion files together, and set confirmed duplicates aside. Items that need more checking stay in the batch.
2. **Remaining files:** Sort the batch again to separate screenshots, screen recordings, saved images, camera media identified on the second pass, and items needing review.
3. **Optional library check:** Look for confirmed exact duplicates that are already in the organized main library and set redundant copies aside.
4. **Optional date repair:** Repair missing capture dates in selected JPEG photos when enough evidence is available. The tool keeps backups and lists items that need manual review.

## What it can organize

- **Camera photos and videos:** File confirmed media into folders by capture year and month.
- **Live Photos and sidecars:** Keep related photo, video, and companion files together when their relationship can be confirmed.
- **Duplicate imports:** Identify confirmed exact duplicates and place redundant copies in a separate folder for review.
- **Different photos with the same name:** Avoid overwriting either file and report collisions that need review.
- **Screenshots, recordings, and saved or downloaded images:** Separate them from camera photos rather than mixing everything into one timeline.
- **Files with missing or conflicting information:** Set aside media with unclear origins, missing dates, or companion-file problems for manual review.
- **Selected JPEG photos with missing capture dates:** Repair the date only when there is enough evidence, while keeping an original-file backup.

The scripts preview changes before you apply them. They do not silently overwrite files with the same name; uncertain matches and destinations are reported for review.

## Sidecar files the scripts recognize

These files accompany a photo or video rather than being the main image. The scripts try to keep them with the right media when the relationship can be confirmed:

- **`.xmp`:** Photo or video information, such as descriptions and editing metadata.
- **`.aae`:** Apple Photos editing information.
- **`.thm` and `.lrv`:** A thumbnail and a smaller preview video, often supplied with camera footage.
- **`.dop` and `.pp3`:** Editing settings saved by photo editing software.

**Do not delete these sidecar files before sorting is complete; removing them may affect pairing or lose related information.**

## Getting started

Edit [config.ini](config.ini) to choose your photo folder and destinations. Run the main organizer on a batch in `Incoming/Stage0/`. The first command previews the plan; run the second only after checking it:

```sh
python3 organize_gallery_media.py "/actual/path/Incoming/Stage0/Batch A"
python3 organize_gallery_media.py "/actual/path/Incoming/Stage0/Batch A" --apply
```

After a successful run, the completed batch moves to `Incoming/Stage1/`. You can then preview and sort files that remain in it:

```sh
python3 organize_leftover_media.py "/actual/path/Incoming/Stage1/Batch A"
python3 organize_leftover_media.py "/actual/path/Incoming/Stage1/Batch A" --apply
```

Replace the sample paths with your own. You can also launch either organizer without a folder argument to use its interactive prompts. The library duplicate check and JPEG date repair are optional, separate tools.

## Configuration reference

Edit values after `=` in [config.ini](config.ini); you do not need to edit Python. Paths with spaces need no quotes. Paths beginning with `/` or `~` are absolute. Other paths are relative to the parent shown below. Changing `backup_root` moves all relative paths; changing `gallery_root` or `inbox_root` moves their relative children. Any field can instead contain a pasted absolute path.

### Default folder layout

This example follows one batch through the default folders. Folder locations can be changed in `config.ini`.

```text
~/Pictures/GalleryOrganizer/
├── Incoming/
│   ├── Stage0/
│   │   └── Batch A/                  ← input batch before the main pass
│   └── Stage1/
│       └── Batch A/                  ← entire batch after a successful main pass;
│                                       files left for the second pass stay here
└── Gallery/
    ├── Main/                         ← camera media from the main pass
    │   └── 2024/
    │       └── 2024-06/
    │           ├── IMG_0100.HEIC     ← photo and Live Photo video stay together
    │           ├── IMG_0100.MOV
    │           ├── IMG_0100.HEIC.xmp ← matched companion file
    │           ├── IMG_0200.JPG
    │           └── 2024-06-15/
    │               └── IMG_0200.JPG ← different photo with a conflicting name
    ├── SortedFromUnsorted/           ← camera media found in the second pass
    │   └── 2024/2024-06/IMG_0300.HEIC
    ├── Screenshots/
    │   ├── 2024/2024-06/Screenshot.png
    │   └── 日期未知/ScreenRecording.mov
    ├── OtherMedia/
    │   ├── 下载与保存/2024/2024-06/saved-image.jpg
    │   └── 来源无法确认/日期未知/unknown.jpg
    ├── NeedsRepair/
    │   ├── 拍摄时间缺失或异常/日期未知/photo.jpg
    │   └── Sidecar关联异常/日期未知/IMG_0400.xmp
    ├── Duplicate/                    ← confirmed duplicates, kept separately
    │   ├── Incoming/Stage0/Batch A/IMG_0100.HEIC
    │   ├── FormatVariants/Incoming/Stage0/Batch A/IMG_0100.JPG
    │   └── Unsorted/截图与录屏/2024/2024-06/2024-06-15/
    │       └── SHA256-…/Screenshot.png
    ├── MetadataReview/               ← JPEG review files and CSV reports
    ├── MetadataRepairBackups/         ← originals backed up for JPEG repair
    └── MetadataRepairWork/            ← temporary JPEG repair files
```

The tree shows possible results, not folders that every run creates. `Stage0/Batch A` moves to `Stage1/Batch A` only after a successful main pass, so those two example positions represent different moments. A known capture date normally gives `YYYY/YYYY-MM/`; a same-name conflict may add `YYYY-MM-DD/`. `日期未知` means the date could not be confirmed. Main-pass duplicate folders mirror the source path; second-pass duplicates are grouped by category and date. The Chinese category names shown above are actual folder names used by the sorting rules.

### `[general]`

| Setting | Values | Purpose |
| --- | --- | --- |
| `language` | `en` or `zh` | Terminal language. English is the default; choose `zh` for the Chinese interface. |

### `[paths]`

| Setting | Relative to | Purpose |
| --- | --- | --- |
| `backup_root` | Directory containing `config.ini` | Boundary for media sources and classification destinations. Usually an absolute path. |
| `gallery_root` | `backup_root` | Parent of the library and classification outputs. |
| `main_library_root` | `gallery_root` | Camera media destination for the main script; scope for main-library duplicate review. |
| `inbox_root` | `backup_root` | Parent of stage 0 and stage 1. |
| `source_stage0_root` | `inbox_root` | Parent of input batches eligible for automatic archiving. Specify one immediate child when running. |
| `completed_source_root` | `inbox_root` | Receives the whole stage 0 batch after a run without failures. |
| `second_stage_library_root` | `gallery_root` | Camera media confirmed during stage two. |
| `duplicate_root` | `gallery_root` | Duplicate quarantine used by the main script and main-library review. |
| `format_variant_duplicate_root` | `duplicate_root` | Alternate image formats confirmed by the main script. |
| `second_stage_duplicate_root` | `duplicate_root` | Whole duplicate media groups found in stage two. |
| `other_media_root` | `gallery_root` | Downloads and uncertain-origin media classified in stage two. |
| `repair_media_root` | `gallery_root` | Stage-two media with capture-time, sidecar association, or format problems. |
| `screen_root` | `gallery_root` | Confirmed screenshots and screen recordings found in stage two. |
| `metadata_review_root` | `gallery_root` | Photos needing manual JPEG metadata review and the CSV reports. |
| `metadata_backup_root` | `gallery_root` | Original-file backups made before JPEG metadata repair; not automatically deleted. |
| `metadata_work_root` | `gallery_root` | Temporary JPEG repair work; empty folders are cleaned up afterward when possible. |
| `hash_cache_db` | `backup_root` | SHA-256 cache file. Deleting it affects speed only; the default is in the user's cache folder. |

The second-stage source is **not** fixed in the configuration: specify the directory when running that script, such as an archived stage 1 batch. `[tools]` contains external executable locations. Usually leave them blank to search the system.

### `[metadata_repair]`

These settings affect only `repair_photo_metadata.py`. Set `utc_offset`, `auto_from`, and `auto_through` together before a write is allowed. Use only a time zone and date range you have confirmed for the photos.

| Setting | Format | Purpose |
| --- | --- | --- |
| `utc_offset` | `+08:00`, `-05:30`, etc. | Confirmed capture UTC offset, written to EXIF time-zone fields. |
| `auto_from` | `YYYYMMDD` | First eligible day, inclusive. |
| `auto_through` | `YYYYMMDD` | Last eligible day, inclusive. |
| `review_months` | Comma-separated `YYYYMM` values | Months kept for manual review even within the eligible range; may be blank. |
| `confirmed_gps_derivative_filename` | Full filename | A personally verified derivative used by a special GPS evidence rule; usually blank. |
| `confirmed_gps_derivative_utc` | `YYYYMMDDTHHMMSSZ` | Verified GPS UTC time for that derivative; set together with its filename. |
| `validated_filename_sequence` | Comma-separated full filenames | Photos whose filename times have been individually verified; usually blank. |

## Notes

- **Organize only:** Gallery Organizer organizes photos and videos you have already exported. It cannot export photos from an iPhone, camera, or other device; use another tool to export them first.
- **macOS only:** On Windows and Linux, the scripts show a message and exit without processing files.
