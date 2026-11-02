# 来源

- 主线目录：`UniXNU-Pagani-Drivers/drivers/NativeKeyStoreEvents`
- 主线提交：`14f50fce9778a8bb19ec6b0b7dc199863526c996`
- 拷贝时间：2026-11-01 11:51:59 -0800
- 主线工作树在拷贝时有未提交改动，所以提交号不足以复现。每个文件拷贝时的 sha256 和修改时间在 `ORIGIN.sha256`。

拷贝时该目录的未提交改动：

```
 M drivers/NativeKeyStoreEvents/NativeKeyStoreEvents.cpp
 M drivers/NativeKeyStoreEvents/README.md
 M drivers/NativeKeyStoreEvents/test_closed_boundary.py
?? drivers/NativeKeyStoreEvents/Test/
?? drivers/NativeKeyStoreEvents/test_volatile_keybag.py
```

相对拷贝基线的改动见本目录 `ORIGIN.diff`，说明见 `ios27-experiment/调研/drivers-27-port.md`。

## 从上游带来的代码

`KeyStoreProbe.cpp`：`UniXNU-sheng` 的 `package/KeyStoreProbe.cpp`，提交 `6b11bb5e40aabc75545a3dac87a92dbfde52c806`，加上主线 `Stage7Probe.cpp` 的 `startKeyState`。Copyright (c) 2026, UniXNU contributors，BSD-2-Clause-Patent，全文见 `LICENSE.UniXNU-sheng`。
