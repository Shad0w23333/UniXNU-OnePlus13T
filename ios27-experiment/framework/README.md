# 框架差异

`UniXNU-27/` 是上游 `dec111b` 的本地检出，改动留在工作区，没有提交。这里保存这些改动，上游更新后可以重放。

| 文件 | 内容 |
| --- | --- |
| `unixnu-27-tracked.patch` | 对上游已有文件的改动：`embed_payload`、`[userdata] partition` 和 `base_mib`、第二遍重编表、对照内核的 ubsan 桩、组件目录 |
| `xnuprelink-reviewed.patch` | `kits/XnuPrelink` 的审定记录：`thread_tid`、`thread_set_allocation_name` |
| `new-scripts/` | 上游没有的脚本：`build-pagani-source-storage.py`、`build-arm-platform.py`、`build-boot-progress.py` |

构建要用 Xcode 26.6，上游 CI 也用 26 系列，Xcode 27 没有 `ld-classic`：

```bash
export DEVELOPER_DIR=/Applications/Xcode-26.6.app/Contents/Developer
```
