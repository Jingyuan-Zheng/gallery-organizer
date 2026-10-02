# Gallery Organizer

[中文说明](README.zh-CN.md)

## Why use it?

A photo export can look simple until you try to sort it in Finder. Apple devices and digital cameras reuse numbered filenames, so different photos may have the same name. Portrait effects, edits, and other information can also be stored in separate **sidecar files** that need to stay with the right photo or video. Repeated imports, especially imports from overlapping dates, make it hard to tell a new file from a copy you already have.

Apple library exports may mix camera photos with screenshots, screen recordings, and images saved from other apps. A **Live Photo** arrives as a photo and a short video; Finder shows them as separate files. Sorting these by hand can split a pair or leave its companion files behind.

Gallery Organizer helps sort these exports while keeping related files together. It separates media it can confidently identify, sets confirmed duplicates aside, and flags uncertain files for review. It previews planned changes before you choose to apply them.

## What it does

1. **Sort the main batch.** Groups camera photos and videos with their Live Photo and sidecar companions. It files confirmed camera media by date, sets confirmed duplicates aside, and leaves uncertain items for another pass.
2. **Sort what remains.** Separates confirmed screenshots and screen recordings, saved or downloaded images, camera media found in the second pass, and files needing review.
3. **Check an existing photo library, if needed.** Finds confirmed exact duplicates already in the main library and sets redundant copies aside.
4. **Repair selected JPEG dates, if needed.** Helps correct missing capture times when the required evidence is available, keeping backups and a review report.

Files with the same name are not silently overwritten. When a match or destination is uncertain, the scripts report it for review.

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

This example shows where a batch and its sorted files can go. Folder locations can be changed in `config.ini`.

```text
~/Pictures/GalleryOrganizer/
├── Incoming/
│   ├── Stage0/Batch A/             ← batch to organize
│   └── Stage1/Batch A/             ← batch after the main pass
└── Gallery/
    ├── Main/                       ← confirmed camera photos and videos
    ├── SortedFromUnsorted/         ← camera media found in the second pass
    ├── Screenshots/                ← screenshots and screen recordings
    ├── OtherMedia/                 ← saved, downloaded, or uncertain media
    ├── NeedsRepair/                ← files needing review
    ├── Duplicate/                  ← confirmed duplicates
    ├── MetadataReview/             ← JPEG review and reports
    ├── MetadataRepairBackups/      ← originals backed up for JPEG repair
    └── MetadataRepairWork/         ← temporary JPEG repair files
```

Date-based folders are created inside these destinations when needed. Some category names may appear in Chinese because they are part of the actual folder names.

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

## Compatibility

Gallery Organizer runs on **macOS only**. On Windows and Linux, the scripts show a message and exit without processing files.
