# Gallery Organizer

用于在 macOS 上预览和整理照片、视频、重复文件，并修复部分 JPEG 的拍摄时间元数据。

## 首次使用

1. 用文本编辑器打开 [config.ini](config.ini)。在 `backup_root =` 后直接粘贴你的媒体总目录路径，例如 `/Volumes/My Photos`；路径有空格也无需加引号。其他路径可以填完整路径，也可以填相对 `backup_root` 的路径。
2. 按需要修改图库、来源、重复文件和缓存目录。`[tools]` 中的命令路径通常可以留空，脚本会从系统查找。
3. 先运行脚本预览，例如 `python3 organize_gallery_media.py /path/to/source`。确认输出与目标目录后，才加 `--apply` 执行。来源必须位于 `backup_root` 下。
4. 若要运行 `repair_photo_metadata.py`，在 `[metadata_repair]` 填写已确认的时区（如 `+08:00`）、起止日期（如 `20200101`、`20241231`）。日期范围包含首尾两天。三项留空时，修复脚本禁止写入。

| 脚本 | 用途 |
| --- | --- |
| `organize_gallery_media.py` | 整理可确认的相机媒体到主图库 |
| `organize_leftover_media.py` | 分类剩余媒体及重复文件 |
| `organize_gallery_exact_duplicates.py` | 检查并隔离主图库的精确重复副本 |
| `repair_photo_metadata.py` | 根据可验证的证据修复 JPEG 拍摄时间 |

脚本默认预览；仅 `--apply` 会移动文件或改写元数据。建议先在少量复制的样本上试运行。用户只需编辑 `config.ini`，无需修改 `gallery_config.py`。
