# Gallery Organizer

[中文说明](README.zh-CN.md)

Gallery Organizer sorts photos, videos, and companion files such as XMP and AAE on macOS. It quarantines confirmed duplicates and can repair capture-time metadata in selected JPEGs. The four scripts run independently; they do not form an automatic pipeline. Normal runs preview changes. `--apply` or an explicit choice in the interactive interface performs changes. The metadata repair script also has two test options that write to files; read its help before using them.

## Language and compatibility

In [config.ini](config.ini), set `[general] language = zh` for the existing Chinese terminal interface or `language = en` for English terminal messages. English is the default if the setting is omitted or left blank. File names, user paths, and configured directory names are displayed as they exist on disk. Both languages use the same sorting rules and file operations.

**macOS only.** All four entry scripts check the operating system at startup. On Windows or Linux they display a message and exit before scanning or moving files. Python itself is cross-platform, but this project relies on macOS Finder tags, extended attributes, `sips`, and `ditto`. Changing paths or tool locations in `config.ini` does not make the scripts cross-platform.

## Where files go

The tree shows the default configuration. Change locations in [config.ini](config.ini). Scripts create destination folders only when needed.

```text
~/Pictures/GalleryOrganizer/                 ← backup_root: boundary for media sources and outputs
├── Incoming/                                 ← inbox_root
│   ├── Stage0/                               ← source_stage0_root
│   │   └── Batch A/                          ← pass this immediate child to the main script
│   └── Stage1/                               ← completed_source_root
│       └── Batch A/                          ← whole batch archived after a successful apply
└── Gallery/                                  ← gallery_root
    ├── Main/                                 ← main_library_root
    │   └── 2024/2024-06/photo.heic           ← confirmed camera media from the main script
    ├── SortedFromUnsorted/                   ← second_stage_library_root
    │   └── 2024/2024-06/photo.heic           ← camera media confirmed in stage two
    ├── Screenshots/                          ← screen_root
    │   └── 2024/2024-06/capture.png          ← confirmed screenshot or screen recording
    ├── OtherMedia/                           ← other_media_root
    │   ├── 下载与保存/…                        ← downloaded or saved media
    │   └── 来源无法确认/…                      ← origin cannot be confirmed
    ├── NeedsRepair/                          ← repair_media_root
    │   ├── 拍摄时间缺失或异常/…                  ← capture-time problem
    │   └── Sidecar关联异常/…                   ← companion-file association problem
    ├── Duplicate/                            ← duplicate_root
    │   ├── Incoming/Stage0/…                  ← exact duplicates, mirroring the source path
    │   ├── FormatVariants/…                   ← format_variant_duplicate_root
    │   └── Unsorted/…                         ← second_stage_duplicate_root
    ├── MetadataReview/                       ← metadata_review_root: manual review and reports
    ├── MetadataRepairBackups/                ← metadata_backup_root: original-file backups
    └── MetadataRepairWork/                   ← metadata_work_root: temporary repair files
```

The category folder names in this example are currently part of the file-sorting rules and may remain Chinese even with an English terminal interface. Files with a reliable date normally go under `YYYY/YYYY-MM/`. If a different file has the same name, the script may use a `YYYY-MM-DD/` or `SHA256-…/` subfolder. Files without a reliable date go under a category's `日期未知/` (“date unknown”) folder. The tree illustrates possible outputs; a run does not create every folder.

## How the four scripts work

| Step | Script | Behavior |
| --- | --- | --- |
| 1. Main organizer | `organize_gallery_media.py` | Scans a specified source, reads filenames, metadata, and origin evidence, and groups Live Photos with companion files. Confirmed camera media goes to `main_library_root`. Clear screenshots, screen recordings, downloads, and uncertain files stay in the source. Confirmed exact duplicates go to `duplicate_root`; alternate formats of the same image go to `format_variant_duplicate_root`. It does not overwrite a file because of a name collision. |
| 2. Remaining media | `organize_leftover_media.py` | Regroups files left in a specified source. Camera media it can confirm goes to `second_stage_library_root`; screenshots and screen recordings go to `screen_root`; downloads and uncertain-origin media go to `other_media_root`; date, association, and format problems go to `repair_media_root`. Confirmed duplicate groups go to `second_stage_duplicate_root`. |
| 3. Main-library duplicate review (optional) | `organize_gallery_exact_duplicates.py` | Scans a specified directory inside `main_library_root`, compares content and companion evidence, and moves confirmed redundant copies to `duplicate_root` while preserving their original relative paths. With `--apply`, it may also merge XMP annotations and remove unnecessary copy-number suffixes from filenames. Keep a backup and inspect the preview. |
| 4. JPEG capture-time repair (optional) | `repair_photo_metadata.py` | Processes only JPEGs matching its evidence rules. It backs up originals to `metadata_backup_root`, builds and verifies candidates in `metadata_work_root`, moves unresolved photos requiring manual review to `metadata_review_root`, and writes a CSV report there. Successfully repaired originals remain in place. |

After step 1 applies without failures, it archives the **entire** source directory from an immediate child of `source_stage0_root` to `completed_source_root`. Files it skipped remain in that archived directory and can be passed to step 2. The main script can process other source directories, but an apply there may move eligible files and then report that stage archiving failed. Use an immediate stage 0 child for the complete workflow.

The identification rules are conservative. A clear screenshot or screen recording is not treated as a camera photo; a PNG extension alone does not prove that a file is a screenshot. Filenames, parent folders, metadata, and origin evidence all matter. When associations or destinations cannot be confirmed safely, the scripts report the issue and try to leave the files in place.

## Configuration reference

Edit values after `=` in [config.ini](config.ini); you do not need to edit Python. Paths with spaces need no quotes. Paths beginning with `/` or `~` are absolute. Other paths are relative to the parent shown below. Changing `backup_root` moves all relative paths; changing `gallery_root` or `inbox_root` moves their relative children. Any field can instead contain a pasted absolute path.

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

## Example run order

Set your `backup_root` and destinations in [config.ini](config.ini) first. Replace these example paths with the real batch path. Omitting a source opens the interactive interface for the main and second-stage scripts.

```sh
python3 organize_gallery_media.py "/actual/path/Incoming/Stage0/Batch A"
python3 organize_gallery_media.py "/actual/path/Incoming/Stage0/Batch A" --apply
python3 organize_leftover_media.py "/actual/path/Incoming/Stage1/Batch A"
python3 organize_leftover_media.py "/actual/path/Incoming/Stage1/Batch A" --apply
```

Check the preview destinations before applying. Main-library duplicate review and JPEG capture-time repair are optional separate steps; the commands above do not start them. `--tag-test-only` and `--repair-test-only` in the JPEG repair script also write to real files.
