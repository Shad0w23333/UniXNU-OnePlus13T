# 来源

- 主线目录：`UniXNU-Pagani-Drivers/drivers/PaganiDarwinBoot`
- 主线提交：`14f50fce9778a8bb19ec6b0b7dc199863526c996`
- 拷贝时间：2026-11-01 11:51:59 -0800
- 主线工作树在拷贝时有未提交改动，所以提交号不足以复现。每个文件拷贝时的 sha256 和修改时间在 `ORIGIN.sha256`。

拷贝时该目录的未提交改动：

```
（无）
```

相对拷贝基线的改动见本目录 `ORIGIN.diff`，说明见 `ios27-experiment/调研/drivers-27-port.md`。

## 从上游带来的代码

`DarwinBootPkg/Driver/DarwinBoot.c`、`Test/test-kernel-parser.py`：`DarwinBoot` 提交 `3c75734`。保留本项目的 `dram-base`、`dram-size` 占位符和 `CNTFRQ_EL0` 调试行。
