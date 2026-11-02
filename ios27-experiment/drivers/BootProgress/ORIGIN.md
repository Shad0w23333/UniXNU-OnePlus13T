# 来源

- 主线目录：`UniXNU-Pagani-Drivers/drivers/BootProgress`
- 主线提交：`14f50fce9778a8bb19ec6b0b7dc199863526c996`
- 拷贝时间：2026-11-01 11:51:59 -0800
- 主线工作树在拷贝时有未提交改动，所以提交号不足以复现。每个文件拷贝时的 sha256 和修改时间在 `ORIGIN.sha256`。

拷贝时该目录的未提交改动：

```
 M drivers/BootProgress/BootProgress.c
 M drivers/BootProgress/PeripheralStatus.h
 M drivers/BootProgress/Test/console.c
?? drivers/BootProgress/CpuClock.h
?? drivers/BootProgress/CrashTrace.h
?? drivers/BootProgress/FrameRate.h
?? drivers/BootProgress/GunyahWdog.c
?? drivers/BootProgress/GunyahWdog.h
?? drivers/BootProgress/RamoopsLog.c
?? drivers/BootProgress/RamoopsLog.h
?? drivers/BootProgress/StallProbe.h
?? drivers/BootProgress/Test/crash.c
?? drivers/BootProgress/Test/gunyah-wdog.c
?? drivers/BootProgress/Test/ramoops.c
?? drivers/BootProgress/Test/stall-glue.c
?? drivers/BootProgress/Test/stall-threads.c
?? drivers/BootProgress/Test/stall.c
?? drivers/BootProgress/Test/ufslog-e2e.c
?? drivers/BootProgress/Test/ufslog-platform.h
?? drivers/BootProgress/Test/ufslog-threads.c
?? drivers/BootProgress/Test/ufslog.c
?? drivers/BootProgress/UfsLog.c
?? drivers/BootProgress/UfsLog.h
```

相对拷贝基线的改动见本目录 `ORIGIN.diff`，说明见 `ios27-experiment/调研/drivers-27-port.md`。
