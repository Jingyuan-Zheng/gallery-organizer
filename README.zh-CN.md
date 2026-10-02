# Gallery Organizer（中文）

[English README](README.md)

Gallery Organizer 是一款整理已导出照片图库的终端交互工具。它把确认的相机照片和视频按拍摄年月归档，尽量让能配对的 Live Photo 照片、视频与 sidecar 伴随文件待在一起，并把截图、录屏、保存的图片和确认重复的副本分别整理。移动文件前会先展示预览，由你决定是否执行；无法可靠判断的文件留待人工核对。

## 为什么需要它？

- **编号重复。** 苹果设备和数码相机可能重复使用编号文件名，不同时期的不同照片也可能同名。
- **伴随文件难配对。** 人像效果、图像编辑和其他信息可能保存在单独的 sidecar 文件中。即使文件名重复，也必须让它们跟对原来的照片或视频。
- **多次导入产生重复。** 重复导入图库，或分批导入互相重叠的时间段，会让同一媒体的多个副本与新文件混在一起。
- **不同来源混在一起。** 苹果图库导出的相机照片，可能与截图、录屏和从其他 App 保存的图片处于同一目录。
- **Live Photo 容易拆散。** 导出的 Live Photo 是一张照片和一段短视频，Finder 会分别显示，手动整理时容易拆散或遗漏。
- **照片信息缺失或错误。** 过去使用第三方导入工具，或导入过程不规范，可能造成拍摄时间等元数据缺失、错误，让按时间整理更困难。

## 整理流程

1. **先整理主要文件：** 归档能确认的相机照片和视频，让 Live Photo 与伴随文件保持成组，隔离确认的重复文件。需要进一步判断的文件暂留在这批文件中。
2. **再整理剩余文件：** 分开截图、录屏、保存的图片、第二次识别出的相机媒体，以及需要人工核对的文件。
3. **按需检查已有图库：** 找出已整理主图库中确认完全重复的文件，单独存放多余副本。
4. **按需修复日期：** 证据足够时，修复部分 JPEG 照片缺失的拍摄时间；保留备份，并列出需要人工核对的文件。

## 可以整理哪些内容

- **相机照片和视频：** 将确认的媒体按拍摄年份、月份归档。
- **Live Photo 和 sidecar 文件：** 能确认关联时，让对应的照片、视频和伴随文件一起整理。
- **重复导入的文件：** 识别确认完全重复的媒体，把多余副本放到单独目录，方便核对。
- **同名但不同的照片：** 避免相互覆盖，并报告需要核对的同名冲突。
- **截图、录屏和保存或下载的图片：** 与相机照片分开整理，避免混成一条时间线。
- **信息缺失或冲突的文件：** 将来源不明、日期缺失或伴随文件关联异常的媒体留给人工核对。
- **缺少拍摄时间的部分 JPEG：** 只有证据足够时才修复日期，同时保留原文件备份。

脚本会先预览整理计划，确认执行后才修改文件。遇到同名文件不会悄悄覆盖；无法确定文件关联或去向时，会报告并留待核对。

## 脚本识别的伴随文件

这类文件不是主照片，而是跟随照片或视频保存的信息。能确认对应关系时，脚本会尝试让它们与原媒体一起整理：

- **`.xmp`：** 照片或视频的说明、编辑信息等。
- **`.aae`：** 苹果照片的编辑信息。
- **`.thm` 和 `.lrv`：** 缩略图和较小的预览视频，常见于相机录像。
- **`.dop` 和 `.pp3`：** 修图软件保存的编辑设置。

**整理完成前请不要删除这些伴随文件，以免影响配对或丢失相关信息。**

## 开始使用

先在 [config.ini](config.ini) 设置照片所在位置和整理目标。把一批照片放在 `Incoming/Stage0/` 下，先运行预览命令；确认计划后，再运行带 `--apply` 的命令：

```sh
python3 organize_gallery_media.py "/实际路径/Incoming/Stage0/一批照片"
python3 organize_gallery_media.py "/实际路径/Incoming/Stage0/一批照片" --apply
```

主整理顺利完成后，整个批次会移到 `Incoming/Stage1/`。如果里面还有文件，可以再预览并整理：

```sh
python3 organize_leftover_media.py "/实际路径/Incoming/Stage1/一批照片"
python3 organize_leftover_media.py "/实际路径/Incoming/Stage1/一批照片" --apply
```

请把示例路径换成自己的目录。运行前两种整理脚本时也可以不填目录，改用交互提示。图库重复检查和 JPEG 日期修复是另外两个按需使用的工具。

## 配置：每个非工具选项的含义

用文本编辑器修改 [config.ini](config.ini) 等号右边的值即可，不需要改 Python。配置文件注释使用英文；此中文文档解释所有设置。路径有空格也不用加引号；`/` 或 `~` 开头的路径按完整路径使用，其他路径相对下表所列的上级目录。改 `backup_root` 会带动所有相对路径；改 `gallery_root` 或 `inbox_root` 会带动各自的下级相对路径。也可以给任一项直接粘贴完整路径。

### 默认目录示意

下面展示一批待整理文件及其可能去向。实际目录可以在 `config.ini` 中修改。

```text
~/Pictures/GalleryOrganizer/
├── Incoming/
│   ├── Stage0/一批照片/            ← 待整理的批次
│   └── Stage1/一批照片/            ← 主整理后归档的批次
└── Gallery/
    ├── Main/                       ← 确认的相机照片和视频
    ├── SortedFromUnsorted/         ← 第二次整理发现的相机媒体
    ├── Screenshots/                ← 截图和录屏
    ├── OtherMedia/                 ← 保存、下载或来源不明的媒体
    ├── NeedsRepair/                ← 需要核对的文件
    ├── Duplicate/                  ← 确认的重复文件
    ├── MetadataReview/             ← JPEG 人工核对文件和报告
    ├── MetadataRepairBackups/      ← JPEG 修复前的原文件备份
    └── MetadataRepairWork/         ← JPEG 修复临时文件
```

需要时，脚本会在目标位置下建立按日期划分的子目录。部分分类目录使用中文名称，这是实际目录名。

### `[general]`

| 配置项 | 可填值 | 用途 |
| --- | --- | --- |
| `language` | `en` 或 `zh` | 终端输出语言；默认英文，指定 `zh` 才使用中文交互。 |

### `[paths]`

| 配置项 | 相对目录 | 用途 |
| --- | --- | --- |
| `backup_root` | 配置文件所在目录 | 所有脚本允许的媒体来源及分类目标的总目录，也是相对路径的顶层基准。通常填一个完整路径。 |
| `gallery_root` | `backup_root` | 图库及分类结果的上级目录。 |
| `main_library_root` | `gallery_root` | 主整理脚本确认的相机媒体目标，也是主库重复检查的范围。 |
| `inbox_root` | `backup_root` | 阶段0与阶段1的上级目录。 |
| `source_stage0_root` | `inbox_root` | 主脚本完成后可自动归档的来源目录上级；运行时仍须指定其中一个直接子目录。 |
| `completed_source_root` | `inbox_root` | 主脚本无失败后接收整个阶段0来源子目录。 |
| `second_stage_library_root` | `gallery_root` | 第二阶段重新确认的相机媒体目标。 |
| `duplicate_root` | `gallery_root` | 主脚本及主库重复检查使用的重复文件目录。 |
| `format_variant_duplicate_root` | `duplicate_root` | 主脚本确认的同图非首选格式隔离目录。 |
| `second_stage_duplicate_root` | `duplicate_root` | 第二阶段确认的整组重复媒体隔离目录。 |
| `other_media_root` | `gallery_root` | 第二阶段的下载、来源不明等普通媒体分类目录。 |
| `repair_media_root` | `gallery_root` | 第二阶段的拍摄时间、sidecar 关联及格式等问题分类目录。 |
| `screen_root` | `gallery_root` | 第二阶段确认的截图和录屏目标目录。 |
| `metadata_review_root` | `gallery_root` | JPEG 时间修复时的人工核对文件与 CSV 报告。 |
| `metadata_backup_root` | `gallery_root` | JPEG 时间修复前生成的原文件备份；脚本不会自动删除备份。 |
| `metadata_work_root` | `gallery_root` | JPEG 时间修复的临时工作位置；结束后尝试清理空目录。 |
| `hash_cache_db` | `backup_root` | SHA-256 缓存数据库文件，只影响重复检查速度；默认设在用户缓存目录。 |

第二阶段**没有固定的来源目录配置**：运行时指定要处理的目录，例如阶段1中仍有文件的批次。`[tools]` 是外部程序位置；一般可以保持空白，脚本会从系统查找。

### `[metadata_repair]`

这些选项只影响 `repair_photo_metadata.py`。`utc_offset`、`auto_from`、`auto_through` 必须一起填写，脚本才允许执行写入。请只填写你能确认适用于这批照片的时区和日期范围。

| 配置项 | 格式 | 用途 |
| --- | --- | --- |
| `utc_offset` | `+08:00`、`-05:30` 等 | 待修复照片拍摄地的 UTC 时差；会写入 EXIF 时区字段。 |
| `auto_from` | `YYYYMMDD` | 允许自动修复的第一天，包含这天。 |
| `auto_through` | `YYYYMMDD` | 允许自动修复的最后一天，包含这天。 |
| `review_months` | `YYYYMM`，多个用逗号分隔 | 即使在日期范围内，这些月份也转为人工核对，不自动修复。可留空。 |
| `confirmed_gps_derivative_filename` | 完整文件名 | 某张已人工核实的衍生照片。只在具有对应 GPS 证据时启用特殊规则；一般留空。 |
| `confirmed_gps_derivative_utc` | `YYYYMMDDTHHMMSSZ` | 上述照片的已核实 GPS UTC 时刻，须和文件名一起填写；一般留空。 |
| `validated_filename_sequence` | 完整文件名，多个用逗号分隔 | 已逐张核实文件名时间的照片清单；一般留空。 |

## 注意事项

- **仅作为整理工具：** Gallery Organizer 只整理已经导出的照片和视频，不能从 iPhone、相机或其他设备导出照片。请先用其他工具导出。
- **仅支持 macOS：** 在 Windows 或 Linux 上，脚本会显示提示并退出，不会处理文件。
