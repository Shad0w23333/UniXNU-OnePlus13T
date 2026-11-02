# 收尾报告：四件改动已复核，全部没上机

结论：两条审查都成立，都已处理，代码逻辑不用改，只改了注释和说明。两个补丁未改动，内容前缀仍是 `6fc11db8`、`10a93bf9`。

## 一、改动文件清单

**实现者的改动，本轮未动：**
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani_SM8750-gpu/patches/xnu/phone-checkdirs-kernproc.patch`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani_SM8750-gpu/patches/xnu/phone-app-exec-count.patch`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani_SM8750-gpu/scripts/test-checkdirs-kernproc-patch.py`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani_SM8750-gpu/scripts/test-stage6.py`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiArmCpu/ArmCpu.cpp`
- `/Users/xiaochoumao/unixnu-13t/工具/make-a830-final-seed.sh`

**本轮修改（代码行为不变）：**

| 文件 | 改动 |
|---|---|
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiArmCpu/CpuFreqGovernor.h` | 只改 `controlCheck` 上方注释 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiArmCpu/README.md` | 新增压档日志行的判读说明 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiArmCpu/Test/scmi-perf.cpp` | 新增“我们自己的热上限不产生日志行”的断言 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/NativeUfs/README.md` | 新增固态声明的完整影响清单 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/NativeRamBlock/README.md` | 新增一行，指向上面的清单 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/NativeUfs/NativeUfs.cpp` | 只改注释 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/NativeRamBlock/NativeRamBlock.cpp` | 只改注释 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani_SM8750-gpu/scripts/test-app-exec-count-patch.py` | 模型加入路径后的参数；新增 `--xnu` 前提核对 |
| `/Users/xiaochoumao/unixnu-13t/工具/test-edit-phone-seed-launchd.py` | 新增种子脚本这一步的合成种子和真实种子用例 |

未提交、未推送，`series` 未动。

## 二、每条审查的处理结果

### 审查 1（中）：`why heat cap` 会被读反 —— 成立，已修

- 【读码】这一行只在 `index < c->level[d]` 时写。`why` 取的是 `x.asked`，只解释请求值，所以实际值低于请求值不可能由我们的上限造成。
- 修法：
  - 注释和 README 改为：这一行表示固件压着，或者 `set` 很小时设档还没生效。
  - 实现说明第 4 件的“`heat cap` 表示压档来自我们自己的热上限”作废，主线不要沿用。
- 【主机】补了反向断言：我们的上限单独生效、平台停在该档时，不计 `lowered`，也不写 `why heat cap`。
- 【主机】变异验证：让代码在我们的上限下也写行，新断言（`scmi-perf.cpp:2510`）报失败。

### 审查 2（低）：固态声明的影响范围写窄了 —— 成立，已修

- 【读码】审查表里的 `vfs_cluster.c`、`vm_phantom_cache.c`、`vm_compressor_backing_store.c` 各项，以及三处“无差别”，都对照本树核对无误。
- 【读码】审查表没列的调用点，我补查了：

| 位置 | 结论 |
|---|---|
| `vfs_cluster.c:3457` | `WRITE_BEHIND` 与 SSD 版都是 1，无差别 |
| `vfs_subr.c:1315` | 只在 macOS 分支 |
| `imageboot.c:346` 与 `kpi_vfs.c:3962` | 只在 pivot 镜像时生效 |
| `kern_symfile.c:581` | 只影响 polled 文件 |

- 【反汇编】抽查 `0xfffffff00566edc0`：取属性后有判空，读的是 Target Disk Mode，与审查一致。
- 【反汇编】抽查 `0xfffffff00571b918`：字符串属性的转换结果有判空；函数开头对字典的判空这次没有再核对。
- 清单已写进 `NativeUfs/README.md`。实现说明第 2 件以这份清单为准。

### 审查里点到的测试盲区

| 盲区 | 处理 |
|---|---|
| `test-stage6.py` 没跑 | 已跑，10 行 PASS 全过 |
| `--sos-off` 与 `--disable` 同用只读了码 | 合成种子和真实 `s2-audio` 副本都跑过，末尾 grep 判据也测了 |
| `spec_close` 没逐行核对 | 已读。【读码】块设备分支里 `vflush` 保留 `VBLK` 和 spec 操作表、不发关闭，引用计数不失衡，走不到 `negative open count` |
| 整条种子脚本 | 仍没跑：脚本会先删 `final/` 里的正式产物，我只跑了改动的那一步 |
| 完整内核链接 | 仍没做，只做了符号交叉核对 |

### 我另外补的盲区

原测试模型里路径后面没有参数。现在加了一个带 `.app/` 的参数，覆盖“参数不算应用启动”。【主机】变异让条件越过 NUL 去搜参数，用例报失败。

## 三、测试结果（实际运行）

**通过：**

| 项目 | 结果 |
|---|---|
| `scmi-perf`（ASan、UBSan） | 2851641 个检查通过 |
| `psci-idle` | 2634553 个检查通过 |
| `spi-gate` | 15 个检查通过 |
| `test-checkdirs-kernproc-patch.py --xnu --inputs` | 9 项通过，0 跳过 |
| `test-app-exec-count-patch.py --xnu --inputs` | 6 项通过，0 跳过 |
| `test-stage6.py`（需 `llvm-objdump` 在 PATH，并加 `--openssl-prefix /opt/homebrew/opt/openssl@3`） | 全过，含 `check_solid_state` |
| `test-edit-phone-seed-launchd.py` | 33 项：31 通过，2 跳过 |
| 真实种子用例 `RealSeed.test_final_seed_scripts_step`（`s2-audio` 副本） | 通过 |
| `test-root-audit.py` | 通过 |
| `test-peripherals.py` | 42614324 个检查通过 |
| `test-bindfs-vget-patch.py` | 6 项，跳过 2 项 |
| `test-idle-flush-patch.py` | 4 项，跳过 1 项 |
| `test-gpu-inputs.py` | 5 项检查通过，1 项无 `--live-dtb` 跳过 |
| `test-ram-data.py` | 5 项通过 |
| `test-kernel-source.py`（用 `UniXNU/scripts`） | 9 项通过 |
| `run-host-tests.sh` | BootProgress 和 NativeUfs 全部通过 |
| NativeRamBlock 的 `direct`、`persist`、`seeddisk` | 通过 |

`run-host-tests.sh` 在 `check_qemu_root_commands.py` 这一步中断，原因是我把 `TMPDIR` 设到草稿目录，路径超过 104 字节。我随后用默认临时目录单独跑了这一步和 `gpu-test-loop/run-tests.sh`，都是 rc=0，所以这轮不是一次跑完。

**单文件编译：**
- `kern_exec.c`、`vfs_syscalls.c`（打完 85 个补丁加新补丁）、`ArmCpu.cpp`、`NativeRamBlock.cpp`：0 错误。
- `NativeUfs.cpp`：通过 `kernel-compile-check.py`，没有新增告警。

**符号核对：**
- `kern_exec.o` 定义 `_pagani_app_execs`，`ArmCpu.o` 引用它。
- `XNU_OWNER_UNION_SYSTEMOS=1` 在构建记录里，`kern_exec.o` 确实引用 `_vfs_setmntsystem`，说明防护分支编译进去了。
- 现有内核里有 `_strnstr` 和 `_vfs_setmntsystem`。

**补丁顺序：** 全新克隆上，85 个补丁加两个新补丁加在途的 `phone-stall-probe.patch`，两种顺序都能干净打上。85 个输入补丁与构建记录的哈希一致。

**失败或未跑，判断与本轮无关：**

| 项目 | 现象 | 判断 |
|---|---|---|
| `test-bootimg.py` | 找不到 `LPUNXINU` 标记 | 本轮没碰启动镜像 |
| `test-format2.py` | 组件数断言 22 不符 | 本轮没碰组件配置 |
| `test-adt.py` | `fdtget` 读 `sysinfo/live.dtb` 失败 | 环境缺件 |
| `test-stage7.py` | 提取出的 `refresh.cpp` 编译不过 | 是 NativeMobileDisplay 的代码，本轮没碰 |
| NativeRamBlock `sparse.cpp` | 断言失败（`sparse.cpp:19`） | `SparseDisk.h` 和 `sparse.cpp` 都没改过 |

这些是按“没碰相关文件”判断的，我没有回退到改动前去复现。`packed-hash` 和 `seed-identity` 要种子文件参数，没跑。

## 四、需要主线做的事

1. 在 `UniXNU-Pagani_SM8750-gpu/patches/xnu/series` 末尾按序追加 `phone-checkdirs-kernproc.patch`、`phone-app-exec-count.patch`。这个文件现在 23 行，85 是各层合计。两个都要加：`ArmCpu.cpp` 强引用 `_pagani_app_execs`，漏掉第二个内核链接失败。`phone-stall-probe.patch` 放前放后都行。
2. 重建内核。
3. 重跑 `工具/make-a830-final-seed.sh`，产物是脚本末尾的 `$S6`，它的 `--check` 里要有 `com.apple.powerlogHelperd`。信任包不变；这一步只改 `disabled.plist`。
4. 写 `PROGRESS.md` 时更正：
   - 第 478 行的 `status=100` 是十六进制，即 `exit(1)`。
   - 按审查 1 的判读写压档原因。

## 五、上机判据

| 判据 | 依据 |
|---|---|
| 启动记录 `SystemOS root refs`：决定性的是 `root use`。old 8、root 7 或 old 7、root 7 都是修复生效，old 7、root 8 是没修复。不要看两数之和（`preflight-refcount.md` 第 5 节） | 【真机】崩溃轮 8 和 7，【反汇编】APFS 根自带 5 个引用 |
| 滑动关机后没有 P 记录，能走到 `power off as restart`。R 记录（`phone-shutdown-progress.patch`）：第一遍 `/` 和 `/dev` 是 `unmount skipped`，第二遍 `unmount returned value=0x0: /` 出现两次，中间夹 `/dev`（第一个 `/` 是 SystemOS，最后一个才是根卷），之后有 `ufslog last snapshot before the reset`。若 panic 地址能和 `old <地址>` 对上，可印证“第二遍卸载” | 【读码】 |
| 日志里不再有 powerlogHelperd | 【读码】 |
| launch 日志词变成 `an app start`，守护进程触发的提升消失（这轮 39 条里约 18 条） | 【真机】对比基数 |
| xpcproxy 的 `load_machfile` 不再有约 86 ms 尖峰，`约 16 ms` 只是预期值 | 【推测】 |
| 只要出现 `d0 at lv... asked lv...` 行，就是固件压档；`set` 很小时可能是设档未生效。`why` 不用来判断谁压的 | 【读码】 |

## 六、未验证项

- 四件改动全部没上机，修后的 `bufferclean`、`if_down_all`、`host_reboot` 这段真机没跑过。
- 关机时“第二遍卸载”仍是推断，【真机】日志没有调用栈。
- 强制卸载窗口里 launchd 不崩，只有一个样本；已打开文件描述符这一项没有逐个核对。
- 第 3 件里 helperd 是走数据库还是 `containerPath` 退出，日志分不清；Mach 消息留在端口上是【推测】。
- APFS 的 TRIM 线程、`vnode_isonssd` 是否依赖固态标志没查清；ADT 只查了 `boot`、`device-tree` 目录。
- backupd 每 64 s 的触发者未知，未动。
- 完整内核未链接，整条种子脚本未跑。

临时文件在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/closeout-1`。