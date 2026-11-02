**结论：4 条审查都成立，全部处理完；另外修了一个第 1 条的修法带出来的副作用。全部没上机。**主机测试 45 过、0 败、1 跳过，内核对象 51 个通过。

## 改动文件

全部在 `/Users/xiaochoumao/unixnu-13t/Adreno830/`，基线快照在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/gpu-kdrv-final/before`。

| 类别 | 文件 |
|---|---|
| 调频 | `A830Dcvs.h`、`A830Dcvs.cpp` |
| 诊断门控 | `Adreno830Board.h`、`Adreno830Start.cpp`、`Adreno830Status.h`、`Adreno830Status.cpp` |
| 4 GiB 日志 | `NativeAdrenoPublish.h`、`NativeAdrenoPublish.cpp` |
| 文档 | `AGENTS.md`、`README.md`、`docs/ARCHITECTURE.md`、`docs/PROGRESS.md` |
| 测试 | `tests/test_a830dcvs.cpp`、`test_status.cpp`、`test_start.cpp`、`test_userclient.cpp` |

没有动 `NativeAdrenoABI.h`（前缀仍是 `20ac9298`），也没有动 Metal、Turnip、metal2vulkan、XNU 补丁和 `out-gpu`。

## 审查处理结果

**问题 1：成立，已修。** 默认值改为 `targetBusyPermille=750`、`downMarginPermille=850`。
- 审查的数字我都复跑出来了。有一处小口误：365 秒是全部秒数，fps≥60 的是 226 秒，它的表本身是对的。
- 逐帧模型（45 °C，±30% 加尖峰）：

| 配置 | 平均频率 | 734 MHz 以上 | 迟到帧 | 迟到超一帧 |
|---|---|---|---|---|
| 旧 70/80 | 451 MHz | 21.4% | 1.85% | 0.25% |
| 现 75/85 | 398 MHz | 0.5% | 4.49% | 0.52% |
| 第一版 85/90 | 366 MHz | 0.4% | 6.00% | 1.15% |

- **现配置的迟到帧仍比旧配置多。** 主要来自持续规则，已写进判据。

**问题 1 的修法带出的副作用：已修，这条不在审查里。**
- 改成 0.75/0.85 后，工作量 470–520（这轮 p90–p99 区间）被突发推到 832 MHz 就回不来了，因为需求 627 大于 0.85×734=624。实测复现过。
- 这会违背“832/900 接近 0”的判据。
- 修法是 734 MHz 以上不留降档余量，只改一个条件。改后 470、520 都回到 734 MHz。

**问题 2：成立，已修。**
- 【读码】改成在第一次真实映射到 4 GiB 以上时记一行：`gpu va past 4 GiB: <进程> slot <n> iova <起点> end <终点>`。
- 【读码】导入和邮箱都走同一条 BoMap，没有绕过的映射路径。
- 映射终点是开区间，正好止于 4 GiB 的映射不算过线。横跨这条线的 BO 能从 `iova` 和 `end` 直接看出来。

**问题 3：成立，已修，范围比审查说的大一点。**
- 三行诊断（`gpu sec`、`gpu noslot`、`gpu va past 4 GiB`）统一挂到 `pagani-diag` 下。`gpu noslot` 也要挂：单行约 600 字节、每次开机 40 行，事件环放不下。
- 按 `AGENTS.md` 走板级取参：和 `pagani-gpu-pool` 一样在 `Adreno830ParseKnobs` 里单独读，存进 `Adreno830Board::system_diag`，S0 交给状态块。不进旋钮表，不占标志位。
- 【真机】那轮命令行已有 `pagani-diag=1`，不多占命令行字节。
- 这是 `gpu sec` 门控的残余：`pagani-diag` 没有绑定 `sheng-log=1`，只开诊断不开 UFS 日志时事件环仍会翻。已写进文档。

**问题 4：成立，改文档和注释，不改代码。**
- 【读码+真机】`ddr_boot=11`、`ddr_floor=11`，命令行没有 `pagani-gpu-ddr*`。
- 删掉了“平均 DDR 档 5.7 → 4.2”，并把“DDR 第 10 档”改成“GPU 自己的票”。
- 判据里明确 DDR 不算收益。
- 【推测】RPMh 多方取最大这一点仍缺真机证据。

**审查的风险提示（S8 自检加 4 GiB 探针）：没做。** 这不是缺陷，首测只靠 `sheng-log=1` 兜底。

## 测试结果

- **全量主机测试**：45 过、0 败、0 已知失败、1 跳过（`test_sha256` 的固件镜像，没给 `--firmware`，和基线相同）。
- **内核对象**：51 个通过，输出在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/gpu-kdrv-final/host-all/kernel`。
- **板级胶水**：`PaganiGpuBoard.cpp` 按构建记录编过，零告警，目标文件含 `pagani-diag`。
- **`工具/gpu-test-loop/run-tests.sh`**：退出 0，63 行 ok。证据目录和 `out-gpu` 没有新文件。
- **变异**：16 个全部杀死。
- **补上的盲区**：
  - `testFrameHeadroom`（244 例）：±30% 的帧在工作量 60–540 下全部放进 TE，升降两个方向都不跳档；同样的扫描用 85/90 会出现迟到帧。
  - `testCompositing` 增加 3 例，覆盖突发后回落。
  - `test_status`：三个门控条件逐一验证，复位不清开关。
  - `test_start`：`pagani-diag` 解析和 S0 转交。
  - `test_userclient`：4 GiB 边界和行格式，用 16 GiB 窗口表算。

## 需要主线做的事

1. 重编内核。板级胶水每次构建都会重编，不用单独处理。
2. 没有新 XNU 补丁，series 不用动，不需要“接在 series 之后”的验证。
3. 种子和用户态不用重做，着色器包不受影响。
4. 打包时保留 `pagani-diag=1`、`sheng-log=1`、`pagani-gpu-status=3`。
5. 确认 `AGENTS.md` 里 `gpu sec` 这条例外，措辞已改成要 `pagani-diag`。
6. 命令行放不下 A/B 参数，这轮只有这一组默认值。

## 上机判据

1. `g9.* note gpu aperture GiB, table pages 10 2011`，不是 `4 805`（S3 那一行留不下来，2026-11-01 在 S9 补了一条）。
2. `pf 0/0/0/0`、`ovf 0`、`f0 h0`。
3. **最要紧**：`gpu va past 4 GiB` 这行出现后，该客户端、桌面、小组件画面都正常。如果错误早于这一行，就和 4 GiB 无关。窗口表已经不允许横跨 4 GiB，这行的 `iova` 应该不小于 `0x100000000`。出页故障时看 `gpu fault cb.. far ..`：地址落在低 4 GiB 说明高位丢了，落在窗口外或三段空隙里说明越界。槽 5 去掉高位后落不到任何窗口，丢高位几乎都会变成页故障；槽 6 以上丢高位可能落在低窗口的已映射页上，`pf 0` 证明不了这些槽。
4. `nos` 不再涨，或 `gpu noslot` 里小窗确实为 0。
5. `fw`、`ctx` 不比这轮差。`vmr <对象>/<区段>`、`cs` 第三个数在同样的连接数下不比这轮差（区段上限已翻倍，但块也变大了，连接数更多时上升不算回退）。对象数上限 24576 这次没动，`vmr` 第一个数上涨就要抬它。
6. G 行 832/900 MHz 的采样占比从约 19% 接近 0，到 95 °C 晚于 47 s。
7. **fps≥60 的秒里，≥115 fps 的占比不低于 18.6%（这轮 42/226）**，p99 中位不差于 32 ms。这条掉了就先放宽持续规则。

Aegir 崩溃不计入判据，DDR 不算收益。

## 未验证项

- 全部改动都没上机，帧余量和发热数字都来自模型和相关性，不是对照实验。
- GPU 地址超过 4 GiB 在真机上从没记录过。
- `notePast4G` 的调用点在 `NativeAdrenoPublish.cpp`，它不进主机测试，只编过内核对象。主机测试只覆盖判断和行格式。
- 跨 4 GiB 的 BO 只在 `test_vm` 的 VM 层测过，用户客户端层的夹具没有 16 GiB 光圈。
- 【推测】如果 4 GiB 以上表现为挂死，32 窗口下 60 s 内 3 次恢复后 GPU 永久放弃，只能重编回退。
- 工作量 550 以上时持续规则把频率留在 734 MHz，这一段迟到帧是预期内的。