# 来源

- 主线目录：`UniXNU-Pagani-Drivers/drivers/PaganiArmCpu`
- 主线提交：`14f50fce9778a8bb19ec6b0b7dc199863526c996`
- 拷贝时间：2026-11-01 11:51:59 -0800
- 主线工作树在拷贝时有未提交改动，所以提交号不足以复现。每个文件拷贝时的 sha256 和修改时间在 `ORIGIN.sha256`。

拷贝时该目录的未提交改动：

```
 M drivers/PaganiArmCpu/ArmCpu.cpp
 M drivers/PaganiArmCpu/README.md
?? drivers/PaganiArmCpu/ArmCpuSpiGate.h
?? drivers/PaganiArmCpu/CpuFreqGovernor.h
?? drivers/PaganiArmCpu/PsciIdle.h
?? drivers/PaganiArmCpu/ScmiPerf.h
?? drivers/PaganiArmCpu/Test/
```

相对拷贝基线的改动见本目录 `ORIGIN.diff`，说明见 `ios27-experiment/调研/drivers-27-port.md`。

## 从上游带来的代码

注册次序和 `bootInitialized`：`UniXNU-ArmCpu` 提交 `93a5030c5d983d0ef90f0fca93879d6e36ad1ba8` 的 `ArmCpu.cpp:440-462,587,594`。次级核那段次序和去掉 `processor_start` 是本项目改的，上游没有。
