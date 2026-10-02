# Gallery Organizer

用于在 macOS 上预览和整理照片、视频、重复文件，并修复部分 JPEG 的拍摄时间元数据。

## 首次使用

1. 打开 [gallery_config.py](gallery_config.py)，设置 `BACKUP_ROOT` 以及各来源、图库、重复文件、缓存目录。所有脚本共用这一个配置文件；需要特殊工具位置时也在此修改。
2. 先运行脚本预览，例如 `python3 organize_gallery_media.py /path/to/source`。确认输出与目标目录后，才加 `--apply` 执行。来源必须位于 `BACKUP_ROOT` 下。
3. `repair_photo_metadata.py` 具有按相机时间修改 EXIF 的专用规则。使用前须在配置文件中明确设置 `REPAIR_UTC_OFFSET_HOURS` 和适用日期范围。默认不会自动修复任何时间；人工证据列表也默认为空。

| 脚本 | 用途 |
| --- | --- |
| `organize_gallery_media.py` | 整理可确认的相机媒体到主图库 |
| `organize_leftover_media.py` | 分类剩余媒体及重复文件 |
| `organize_gallery_exact_duplicates.py` | 检查并隔离主图库的精确重复副本 |
| `repair_photo_metadata.py` | 根据可验证的证据修复 JPEG 拍摄时间 |

脚本默认预览；仅 `--apply` 会移动文件或改写元数据。建议先在少量复制的样本上试运行。
