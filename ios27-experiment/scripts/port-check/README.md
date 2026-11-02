# 移植验证脚本

这些脚本从草稿目录搬出来，是 `调研/drivers-27-port.md` 和 `调研/xnu-12377.md` 里实测结果的来源。
它们依赖草稿目录里的 12377 编译树（约 1.6 GB，含 `x12377`、`xnu-series`），路径写死在脚本里。编译树可以用
`../check-xnu-12377-series.sh`（`KEEP=1`）加 `build-series.sh` 重新做出来，下次运行前先改路径。

| 脚本 | 用途 |
| --- | --- |
| `cc27.py` | 用 arm64e 12377 的编译记录逐文件编译驱动，`SERIES=1` 用补丁系列的树 |
| `build-series.sh` | 编译打完补丁的 XNU 树 |
| `link-l.py`、`link.sh` | 打包 L 驱动并链接内核 |
| `macho.py`、`chain.py` | 走 `early_boot_progress_init` 的调用图和修复链 |
| `final-tests.sh` | 全部主机测试 |
| `build.sh`、`build-unixnu.sh`、`qemu-test.py` | `PaganiDarwinBoot` 的 EDK2 构建和 QEMU 验证 |
