# Gallery Organizer

在 macOS 上整理照片、视频和伴随文件（例如 XMP、AAE），隔离确认的重复副本，并对符合特定证据规则的 JPEG 修复拍摄时间。四个脚本各自运行，**不会自动串成一条流水线**。普通运行默认只预览；命令行的 `--apply` 或交互界面中的执行确认会修改文件。时间修复脚本的两个测试选项也会写入，请先阅读其帮助。

## 系统兼容性

**目前仅支持 macOS。** 四个入口脚本在启动时检查操作系统；在 Windows 或 Linux 上会立即显示提示并退出，不会开始扫描或移动文件。Python 本身可在这些系统运行，但本项目使用了 macOS 的 Finder 标签、扩展属性、`sips` 和 `ditto` 等功能。修改 `config.ini` 的路径或工具位置不能使脚本跨平台运行。

## 先看文件会去哪

下面是默认配置的示意结构。实际位置由 [config.ini](config.ini) 决定；脚本仅在需要时创建目标目录，不会预先创建整棵目录树。

```text
~/Pictures/GalleryOrganizer/                 ← backup_root；所有来源和目标的边界
├── Incoming/                                 ← inbox_root
│   ├── Stage0/                               ← source_stage0_root
│   │   └── 一批待整理照片/                     ← 运行主脚本时指定这个直接子目录
│   └── Stage1/                               ← completed_source_root
│       └── 一批待整理照片/                     ← 主脚本无失败后，整个来源目录移到这里
└── Gallery/                                  ← gallery_root
    ├── Main/                                 ← main_library_root
    │   └── 2024/2024-06/照片.heic              ← 主脚本确认的相机媒体
    ├── SortedFromUnsorted/                   ← second_stage_library_root
    │   └── 2024/2024-06/照片.heic              ← 第二阶段重新确认的相机媒体
    ├── Screenshots/                          ← screen_root
    │   └── 2024/2024-06/截图.png               ← 第二阶段确认的截图或录屏
    ├── OtherMedia/                           ← other_media_root
    │   ├── 下载与保存/…
    │   └── 来源无法确认/…
    ├── NeedsRepair/                          ← repair_media_root
    │   ├── 拍摄时间缺失或异常/…
    │   └── Sidecar关联异常/…
    ├── Duplicate/                            ← duplicate_root
    │   ├── Incoming/Stage0/…                  ← 主脚本确认的完全重复副本，保留来源相对路径
    │   ├── FormatVariants/…                   ← format_variant_duplicate_root
    │   └── Unsorted/…                         ← second_stage_duplicate_root
    ├── MetadataReview/                       ← metadata_review_root；人工核对及报告
    ├── MetadataRepairBackups/                ← metadata_backup_root；修复前备份
    └── MetadataRepairWork/                   ← metadata_work_root；修复临时文件
```

有可靠日期的分类目录通常先用 `YYYY/YYYY-MM/`；同名但内容不同且发生冲突时，脚本可能改放 `YYYY-MM-DD/` 或 `SHA256-…/` 子目录。日期无法确认时放入“日期未知”。上述树只展示常见去向，不代表每次运行都会产生这些文件。

## 四个脚本怎样处理文件

| 步骤 | 脚本 | 处理逻辑与结果 |
| --- | --- | --- |
| 1. 主整理 | `organize_gallery_media.py` | 扫描指定来源，读取文件名、元数据和来源证据，恢复 Live Photo 与 sidecar 的关联。能够确认是相机媒体的整组进入 `main_library_root`；明确截图、录屏、下载和证据不够的文件留在来源目录。确认的重复副本进入 `duplicate_root`，同图非首选格式进入 `format_variant_duplicate_root`。文件不因同名而被覆盖。 |
| 2. 剩余媒体 | `organize_leftover_media.py` | 对指定来源中剩余的文件重新恢复媒体组并分类：能够重新确认的相机媒体进入 `second_stage_library_root`；截图和录屏进入 `screen_root`；下载或来源不明的媒体进入 `other_media_root`；时间、关联或格式问题进入 `repair_media_root`。确认的整组重复媒体进入 `second_stage_duplicate_root`。 |
| 3. 主库重复检查（按需） | `organize_gallery_exact_duplicates.py` | 扫描你指定的 `main_library_root` 内目录，比较内容及伴随信息，把确认的冗余副本移入 `duplicate_root` 并保留原相对路径。`--apply` 也可能合并 XMP 标注、清理不必要的文件名复制编号；因此务必先检查预览并保留备份。 |
| 4. JPEG 时间修复（按需） | `repair_photo_metadata.py` | 只针对符合脚本证据规则的 JPEG。修复前备份到 `metadata_backup_root`，在 `metadata_work_root` 制作和验证候选文件；无法自动修复且需要人工核对的照片进入 `metadata_review_root`，并在那里生成报告。修复成功的原照片留在原位置。 |

第一步只有在 `--apply` 后**没有失败项**，且来源正好是 `source_stage0_root` 的**直接子目录**时，才把整个来源目录移到 `completed_source_root`；里面未能整理的文件会一起保留，随后可以把该归档目录交给第二阶段处理。其他来源也能运行主脚本，但 `--apply` 在移动可处理文件后会报告阶段归档失败；因此需要完整执行这一流程时，请使用阶段0的直接子目录。

识别规则是保守的：明确截图或录屏不会被当作相机照片；单凭 PNG 扩展名也不会认定为截图。文件名、目录名、元数据和来源证据可能共同影响判断。无法确认关联或目标有冲突时，脚本会报告并尽量保持原位。

## 配置：每个非工具选项的含义

用文本编辑器修改 [config.ini](config.ini) 等号右边的值即可，不需要改 Python。路径有空格也不用加引号；`/` 或 `~` 开头的路径按完整路径使用，其他路径相对下表所列的上级目录。改 `backup_root` 会带动所有相对路径；改 `gallery_root` 或 `inbox_root` 会带动各自的下级相对路径。也可以给任一项直接粘贴完整路径。

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

## 运行顺序示例

先在 [config.ini](config.ini) 设置自己的 `backup_root` 和目标位置。下面的命令把一个位于阶段0的批次交给主脚本；路径只是示例，请换成实际路径。省略来源参数时，主脚本和第二阶段脚本会进入交互界面。

```sh
python3 organize_gallery_media.py "/实际路径/Incoming/Stage0/一批待整理照片"
python3 organize_gallery_media.py "/实际路径/Incoming/Stage0/一批待整理照片" --apply
python3 organize_leftover_media.py "/实际路径/Incoming/Stage1/一批待整理照片"
python3 organize_leftover_media.py "/实际路径/Incoming/Stage1/一批待整理照片" --apply
```

只有确认预览中的目标路径后再执行 `--apply`。主库重复检查和 JPEG 时间修复是另外的按需步骤，不会由上述命令自动触发。JPEG 时间修复的 `--tag-test-only` 和 `--repair-test-only` 也会写入实际文件。
