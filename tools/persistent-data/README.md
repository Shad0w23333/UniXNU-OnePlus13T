# 持久 Data：安卓端工具与主机脚本

iOS 的 Data 卷放进安卓 `/data` 里的一个 f2fs 锁定文件（pinned file）：安卓只负责“占住”这些 UFS 块（不移动、不回收、不 TRIM），
XNU 绕过文件系统直接读写这些块。设计、格式与 XNU 侧原型在
`UniXNU-Pagani-Drivers/drivers/PaganiPersistentData/`（README 与 `Test/run.sh`）。

| 文件 | 作用 |
| --- | --- |
| `unixnu-datafile.c` | 手机端工具（root 运行）：probe / create / verify / publish / revoke / guard / selftest-check / remove |
| `build-android.sh` | NDK 交叉编译成静态 aarch64 程序 `out/unixnu-datafile`（只编译，不推送） |
| `test.sh` | 主机侧全部检查：驱动侧测试、交叉编译、Python 解码器与 C 交叉核对、f2fs 元数据转储分析 |
| `df_decode.py` | 只读解码 unixnu 分区开头的描述符槽（`dd if=/dev/block/by-name/unixnu bs=4096 count=256` 的转储） |
| `f2fs_sb.py`、`f2fs_free_runs.py` | 只读解析 f2fs 超级块；统计空闲段并模拟锁定文件会被切成多少段 |

## 第一次在手机上创建（用户自己执行；这一轮没有执行）

前提：手机在安卓（或 TWRP 且 `/data` 已解密），有 root；`unixnu` 分区里是 `userdata.py` 生成的根镜像；`/data` 至少有
128 GiB + 48 GiB 空闲（10-29 的快照是 248.7 GiB 空闲）。

```sh
# Mac 上
cd /Users/xiaochoumao/unixnu-13t/工具/persistent-data
./build-android.sh
adb push out/unixnu-datafile /data/local/tmp/

# 手机上（adb shell 后 su；TWRP 里直接是 root）
cd /data/local/tmp && chmod 755 unixnu-datafile
./unixnu-datafile probe                                  # 只读：/data、dm 映射、分区、f2fs 几何、槽、文件
./unixnu-datafile create --size-gib 128 --confirm SERIAL_13T:128   # SERIAL_13T 换成本机 ro.serialno（adb devices 第一列）
./unixnu-datafile verify                                 # 只读：锁定、GC 失败数为 0、不可变、FIEMAP 与描述符一致、65536 个金丝雀
```

`create` 依次做：建空文件 → `F2FS_IOC_SET_PIN_FILE` → `fallocate` 整 128 GiB（f2fs 按整段 2 MiB 分配新段）→ FIEMAP →
映射证明（通过 f2fs 往每段第 0 块写 4 KiB，原始分区上对应块必须变化）→ 原始写入每段第 0 块的金丝雀并读回 →
`FS_IMMUTABLE_FL` → 描述符写进 `unixnu` 第 16..255 块的空闲槽（先表后头）并读回。任一步失败立即停止，不做后续写入。
大约需要几分钟（65536 段，每段一次经 f2fs 写和两次原始读写）。

之后：
- 重启一次安卓，再跑 `verify`；正常使用一两天后再跑一次（GC 失败数必须一直是 0）。
- 重新刷 `unixnu` 根镜像会把描述符槽清零：刷完回安卓跑 `./unixnu-datafile publish --confirm <序列号>:publish`。
- 不再需要时：`./unixnu-datafile remove --confirm <序列号>:remove`（先吊销，再去掉不可变标志，再删除）。
- **禁止**在文件存在时：缩放 f2fs（dual-sys 工具、`resize.f2fs`、TWRP 的 Resize）、改分区表、格式化 Data。要做先 `remove`。

可选的开机守护（安卓有 Magisk/KernelSU 时）：把工具放到 `/data/adb/unixnu-datafile`，模块的 `service.sh`：

```sh
#!/system/bin/sh
until [ "$(getprop sys.boot_completed)" = 1 ]; do sleep 5; done
/data/adb/unixnu-datafile guard >> /data/adb/unixnu-guard.log 2>&1
```

`guard` 每次开机核对一遍，发现文件被删、被移动、被解锁或金丝雀不符就写吊销头（只写 `unixnu` 的一个块），XNU 下次开机直接退回内存盘。
