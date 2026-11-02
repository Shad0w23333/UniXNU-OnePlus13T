# 来源

- 主线目录：`UniXNU-Pagani-Drivers/drivers/NativeRamBlock`
- 主线提交：`14f50fce9778a8bb19ec6b0b7dc199863526c996`
- 拷贝时间：2026-11-01 11:51:59 -0800
- 主线工作树在拷贝时有未提交改动，所以提交号不足以复现。每个文件拷贝时的 sha256 和修改时间在 `ORIGIN.sha256`。

拷贝时该目录的未提交改动：

```
 M drivers/NativeRamBlock/NativeRamBlock.cpp
 M drivers/NativeRamBlock/README.md
```

相对拷贝基线的改动见本目录 `ORIGIN.diff`，说明见 `ios27-experiment/调研/drivers-27-port.md`。

## 从上游带来的代码

`PersistOverlay.h` 全取、`NativeRamBlock.cpp` 里的 `Kept`、`Test/{direct,persist,seeddisk}.cpp`：`RamBlock` 提交 `4be2d7e`。`declareSolidState` 是本项目的，保留。
