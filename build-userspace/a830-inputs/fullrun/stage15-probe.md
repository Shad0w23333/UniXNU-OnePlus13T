# 卡顿探针收尾结果

五条审查发现全部成立，已全部修复。最终代码上主机测试全过，但**没有上机，也没有链接整个内核**。主线在 `series` 末尾追加 `phone-stall-probe.patch` 后统一构建即可，种子不用重做。

## 改动文件

**本轮改动：**
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani_SM8750-gpu/patches/xnu/phone-stall-probe.patch`：现 1029 行，sha256 前缀 `58e491ed`。
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/BootProgress/StallProbe.h`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/BootProgress/BootProgress.c`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/BootProgress/Test/stall.c`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/BootProgress/Test/stall-glue.c`：新增。
- `/Users/xiaochoumao/unixnu-13t/UniXNU/scripts/build-xnu-4k-baseline.sh`：只改删除列表和注释，**日常线共用这个脚本**。
- `/Users/xiaochoumao/unixnu-13t/工具/gpu-test-loop/` 下：`stall_format.py`、`stall-report.py`、`symbolize-crashes.py`、`run-tests.sh`、`README.md`、`tests/test_stall.py`、`tests/kbtrace-v3.c`、`tests/test_ufslog.py`

`series`、`UniXNU/out`、`UniXNU/out-gpu`、Metal、Turnip、metal2vulkan 都没动。`NativeAdrenoABI.h` 前缀仍是 `20ac9298`。

## 审查处理结果

| # | 判定 | 处理 | 修复后结果 |
|---|---|---|---|
| 1 预算被动画结束烧掉 | 成立，主机复现 | 重写 gap 判定，分预算池，见下 | 400 次动画结束：触发从 1200 次降到 400 次，写入从 1995 行降到 798 行，剩余预算留给真卡顿 |
| 2 身份错乱 | 成立，读码确认 | 查映射改用独立静态结构，不再写 `t->info` | 新胶水测试覆盖；改回旧写法测试会失败 |
| 3 退出竞争的空指针 | 成立，读码确认 | pid 和名字在持 `tasks_threads_lock` 时拷好 | 只编译过 |
| 4 `up` 配对配错 | 成立【真机】seq 8401：`dn 20/20 up 301/301` | 重复的 up 占编号、不进队列，它的 `up-done` 不配对、也不告诉探针 | 复现程序：从 `max=3000 lost=1`、错配 2501 ms，变为 `n=2 max=7500 lost=0` |
| 5 干净构建编掉钩子 | 成立，读码确认 | 删除列表加入 `sched_prim`、`cpu`、`cpu_common`、`kdebug` | 未跑干净构建 |

审查末尾的小事实（`task_lock` 是阻塞的）不算缺陷，没有改。

**第 1 条的具体改法：**
- **阈值：** 改为 100、200、400、1000、3000、8000 ms，每个阈值一次采样，1 到 12 秒的卡顿中后段也有样本。
- **信号（`ask` 字段）：**
  - `ask=2`：触摸或键盘配对未结束。一直有效到下一帧，采样器保持醒着。
  - `ask=1`：只有 GPU 提交。只在提交新鲜时算，只用低档预算。
  - `ask=0`：只有帧前节奏。最多一次采样，只能用前 800 行。
- **预算：** 键盘单独一份，2000 行、每秒 20 行，互不挤占。

**审查之外一并修的问题：**
- 屏幕空闲后点击，旧逻辑的阈值已被空闲期耗完，永远不采样。现在从第一个信号起计时。
- 采样器 2 秒就睡，够不到长阈值。现在有 `ask=2` 信号时保持醒着。
- 键盘配对 64 次采样只够 32 s，而【真机】`dn` 最长 52.8 s。采样间隔改为 500 ms、1 s、2 s 逐级放慢，约 2 分钟才放弃。
- 环溢出后探针的未结束计数偏高，配对会空转约 2 分钟。现在探针计数始终等于队列长度。
- `Q` 头新增 `since`，报告不再把空闲时间算进卡顿时长；只有 `ask=0` 的卡顿显示为 `gap?`。
- `test_ufslog.py:464` 的环境误报：改为检查分区路径，不再匹配 `unixnu` 这个词。

## 测试结果（最终代码，实际运行）

| 项目 | 结果 |
|---|---|
| `run-tests.sh` | 退出 0，共 69 条 ok/PASS，0 条 Traceback、FAILED、Assertion |
| 历史证据重解码 | 24 个目录全过。`runs/` 共 48 个目录，其余 24 个没有 ramoops 文件被跳过，之前“48 个”的说法不准 |
| `stall.c`（ASan、UBSan）、`stall-threads.c`（TSan）、`test_stall.py` | 全过 |
| `stall-glue.c`（新增，ASan、UBSan） | 9 组场景全过，含引用计数平衡 |
| 变异检验 | 把修复逐个改回去共 20 处，全部被抓住。第 5 处第二轮曾存活，补了场景后才抓住；末尾补的测试只改测试文件 |
| 键盘补丁测试 | `test-phone-keyboard-trace.py --xnu <原版树>` 4 项通过，1 项跳过（未设 `UIKITCORE`）。默认路径指向已打补丁的树，会报错退出 |
| BootProgress、RamoopsLog、UfsLog、GunyahWdog、NativeUfs | 按构建记录单独编译，我们的代码零诊断；前四个无运行时辅助函数、无 FP/SIMD |
| XNU 5 个 `.c` | 新增诊断 0（与打补丁前对比） |
| 补丁 | 在构建树文件的新副本上干净打上，结果与草稿一致；已打的 85 个补丁 sha256 与 `xnu-inputs.json` 记录一致 |

【推测】用真机 F 行估算的预算（F 行只有每秒最大值，只算估计）：
- 全部算 `ask=2`：1729 行，≥400 ms 的 86 秒全部能采到。
- 全部算 `ask=0`：798 行。

## 需要主线做的事

1. 在 `series` 末尾追加 `phone-stall-probe.patch`。
2. 统一构建内核，再重打 payload 和 boot 镜像。补丁清单变化会触发 XNU 重新准备。
3. `scripts` 是拷进构建树的，构建时才同步到树里，确认用的是新脚本。
4. 启动参数不变，保留 `pagani-diag=1`、`pagani-kbtrace=1`。
5. 种子不用重做：本路没改任何进种子的文件。
6. 更新 `PROGRESS.md` 并提交，我没有提交。

## 上机判据

```sh
python3 stall-report.py runs/<目录> --a2s ../../build-userspace/a830-inputs/dsc-21H461.a2s --kernel <kernel.macho>
```

| 看什么 | 通过 | 不通过时 |
|---|---|---|
| 启动 | 有 `stall probe: ramoops M/Q records`，没有 `off` | 探针没开，补丁或 ABI 不匹配 |
| 无帧无触摸的卡死 | 最后一帧或触摸后 10 s 内每秒都有 M。采样器睡着的秒 `tick`、`idleq`、`stuck` 是 0，报告显示 `-/-`，那是没人看过，不是没问题；`M io free` 是瞬时值。再往后到恢复前没有 M，这是设计 | 10 s 内 M 就断了：门控有问题 |
| 写线程暂停 | `ufslog rounds … key presses N`：探针全跑完 N 是 12（10 块加 P1、P3 各一次落盘），最后一条统计行通常少几次；P1 门闩不开时 N 是 4 | N 是 0：探针没走到；头 `state` 2 且最后一行是 `… next`：这一块挂死在启动 CPU 或整机 |
| 键盘脚本 | `input script: armed`，约 241 s `start`，不晚于约 262 s `done`，不是 `abort`。只在 Spotlight 里点 8 下，看 `kbt` 的 dn/up 配对；备忘录要用户手打 | `abort` 且日志有 `real touch`：有人碰了屏幕 |
| `M ipi` 的 `tick` | 活动时接近 10 ms | 采样器被拖慢，这轮时序不可信 |
| `Q th` 的 `x=` | 多为 `ok` | 多为 `to`：目标核长时间关中断 |
| `Q th rs` | 确实是渲染线程 | 线程名匹配有误 |
| `ask` 分布 | 静止场景几乎没有 `ask=1` | 帧后还有不上屏的 GPU 工作，要收紧 GPU 信号 |
| `M io` 的 `q`、`kq` | 全程不到顶，≥400 ms 的样本没被拒 | 调整预算分档 |
| 报告里的 `gap*`（≥200 ms 却没有采样） | 很少，14 次夹心卡顿类都有 `gap` 或 `gap?` 行 | 还有采样盲区 |
| 秒级键盘卡顿 | 同一 `st=` 有多个样本，覆盖中后段 | 查配对和预算 |
| `kbt` 的 `up` | 队列排空后 `lat` 的 `l` 不再增长，`dup` 非零，之后不再有 `why=up` 采样 | 重复 up 的时序假设不成立 |
| 帧率与顺滑度 | 与上一轮无明显退步 | 探针本身有开销 |

## 未验证

- **没上机，没链接整个内核。**
- **XNU 侧只编译过：** 线程读取、pmap 读取、内核栈回溯、第 3 条修复。胶水测试跑在假实现上，不能证明真实内核行为。
- **`Stage7Probe.cpp`：** 仍只做过语法检查，本轮没动。
- **第 5 条：** 没跑干净构建。
- 【推测】任务队列严格按 FIFO 完成，`up` 配对依赖这一点。
- 【推测】重复的 `touchUp:` 时间戳不变。
- 【推测】帧记录之后没有该帧自身的 GPU 提交。读码看帧在拷贝完成后才记录，真机要靠 `ask=1` 的分布确认。
- 【读码】`up` 重复去重表每进程 64 项，更老的触摸重复时会被当成新触摸。
- 【读码】`PAGANI_KBT_RING` 仍是 8，而【真机】有 `dn 61/18` 的积压。溢出后最老配对没有 `long` 记录，但 Q 采样照常。
- 【推测】屏幕完全不响应的误点，每次最多耗 42 行，采样器多醒约 8 秒。

另外，开工时一条清理命令里的 `rm` 被安全检查拦下，没有执行。之后改用新建临时目录，全程没有删除任何东西。校验目录和变异目录都留在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/stall-closeout`。