# 来源

- 主线目录：`UniXNU-Pagani-Drivers/drivers/NativeUfs`
- 主线提交：`14f50fce9778a8bb19ec6b0b7dc199863526c996`
- 拷贝时间：2026-11-01 11:51:59 -0800
- 主线工作树在拷贝时有未提交改动，所以提交号不足以复现。每个文件拷贝时的 sha256 和修改时间在 `ORIGIN.sha256`。

拷贝时该目录的未提交改动：

```
 M drivers/NativeUfs/NativeUfs.cpp
 M drivers/NativeUfs/README.md
 M drivers/NativeUfs/RootCommands.h
 M drivers/NativeUfs/tests/RootCommands.cpp
 M drivers/NativeUfs/tests/RootCommandsArm64.cpp
 M drivers/NativeUfs/tests/RootCommandsBridge.cpp
 M drivers/NativeUfs/tests/check_arm64_root_commands.py
 M drivers/NativeUfs/tests/check_root_path_host.py
?? drivers/NativeUfs/LogInterface.h
?? drivers/NativeUfs/LogWindow.h
?? drivers/NativeUfs/NativeUfsLog.h
?? drivers/NativeUfs/NativeUfsPart.h
?? drivers/NativeUfs/PartitionTable.h
?? drivers/NativeUfs/tests/LogInterface.cpp
?? drivers/NativeUfs/tests/LogLun.h
?? drivers/NativeUfs/tests/LogWindow.cpp
?? drivers/NativeUfs/tests/PartitionTable.cpp
```

相对拷贝基线的改动见本目录 `ORIGIN.diff`，说明见 `ios27-experiment/调研/drivers-27-port.md`。

## 从上游带来的代码

| 文件 | 来源 | 版权和许可 |
| --- | --- | --- |
| `SourceStorage.cpp`、`SourceRootBinding.cpp`、`SourceRootPolicies.cpp` | `UniXNU-sheng` 的 `package/`，提交 `6b11bb5e40aabc75545a3dac87a92dbfde52c806`（`SourceStorage.cpp` 去掉了 `boot_log_*`，加了 `native_keystore_probe`） | Copyright (c) 2026, UniXNU contributors，BSD-2-Clause-Patent，全文见 `LICENSE.UniXNU-sheng` |
| `NativeUfs.cpp`、`RootLayout.h` 里的 `NATIVE_UFS_SOURCE_API`、清单 v2、`readPolicyTable`、只含头部的策略 | `UniXNU-UniXNU-sheng` 上游提交 `6dafef9` 的 `drivers/ShengUfs/` | 同上 |
| `VerifiedBlockPolicy.h` | `VirtioMmioBlock` 提交 `4e1013e` | BSD-2-Clause-Patent |
| `make-source-base.py` | `UniXNU` 的 `scripts/build-sheng-source-storage.py:152-165`，提交 `407db1aefd4e4e50a944fcda73442bde566dbce1` | Copyright (c) 2026, UniXNU contributors，BSD-2-Clause-Patent |
