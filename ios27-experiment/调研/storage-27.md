# 27 的根卷、Data 卷和与 17 主线并存

时间：2026-11-01。证据等级：**【实测】**、**【读码】**、**【推断】**。手机只做只读查询。

## 结论

- **能并存，不用动分区表，不用碰 17 的任何字节。** `unixnu` 分区 64 GiB，17 只用了开头 8948 MiB 和末尾的日志区，中间约 54 GiB 空闲。
- 27 根卷放在分区内 10 GiB 处，有自己的 `SHNGROOT` 清单。Data 第一阶段放内存，以后再放到分区内 44 GiB 到 63 GiB。
- 需要改的只有一处内核代码：根布局解析要支持“清单不在分区第 0 块”。写入手机要用户另外同意。
- “前 9147 MiB”出自 `Adreno830/docs/PROGRESS.md:470,504`，和其他记录对不上：手机上的清单是 8948 MiB，基线文件实际哈希前 `0x22F500` 块，即 8949 MiB（`build-userspace/a830-inputs/ufslog/baseline-20261031.txt`）。本方案从 10240 MiB 开始，三个数都安全。

## 现状

| 项 | 值 | 证据 |
| --- | --- | --- |
| `unixnu` | `/dev/block/sda16`，68,719,476,736 字节，逻辑块 4096 字节 | 【实测】 |
| 分区位置 | LBA 107409152 到 124186367，LUN 0 末尾 | 【读码】`board.toml` |
| `userdata` | 394 GiB，已用 146 GiB，空闲 248 GiB | 【实测】`df` |
| 清单 | `SHNGROOT` v1，2 个窗口，`image_bytes` 9,382,658,048（8948 MiB） | 【实测】第 0 块 |
| OS 窗口 | 偏移 1 MiB，6,077,580,800 字节，只读 | 【实测】 |
| SystemOS 窗口 | 偏移 5798 MiB，3150 MiB，只读 | 【实测】 |
| 日志窗口 | 分区末尾 32 MiB，魔数 `PGLOG001` 已在 | 【实测】第 16769024 块 |
| 导出区 | 日志窗口下面 256 MiB | 【读码】`NativeUfs/LogWindow.h:9-10` |
| 日志保留区 | 分区末尾 1024 MiB，`sheng-log-reserve` 可改 | 【读码】`NativeUfs.cpp:1104-1115` |
| 主线基线 | 哈希范围是主 GPT、前 8949 MiB、第 16515072 到 16703487 块（63 GiB 起 736 MiB，含非零预留区）、日志和导出区 288 MiB | 【读码】基线文件、`PROGRESS.md:504` |
| 第 2341632 块（9147 MiB 处） | 一块全零 | 【实测】 |
| 持久 Data 描述符 | 设计中，放在 `unixnu` 第 16 到 255 块 | 【读码】`PaganiPersistentData/README.md`，未编入内核 |

## 27 的体积

BuildManifest 从 Apple 服务器按范围读取，没有下载镜像【实测】。

| 组件 | iPad17,3（上游根） | iPhone16,1（备选根） |
| --- | --- | --- |
| OS | `043-68950-668.dmg.aea`，8,254,390,272 字节 | `043-69111-662.dmg.aea`，8,992,587,776 字节 |
| Cryptex1,SystemOS | `043-69086-713.dmg.aea`，2,348,810,240 字节 | `043-70459-708.dmg.aea`，2,348,810,240 字节 |
| Cryptex1,AppOS | `043-69297-793.dmg`，16 MiB | 同一个文件 |
| KernelCache | `kernelcache.release.ipad17`，24,014,290 字节 | `kernelcache.release.iphone16`，22,511,975 字节 |

iPad17,3 的原始大小来自 sheng 的校验记录，针对同一份镜像【读码，`scripts/policies/ipados-27.0.1-24A446/*/verification.json`】：

| 窗口 | 原始大小 |
| --- | --- |
| OS | 9,613,344,768 字节（9168 MiB） |
| SystemOS | 6,016,729,088 字节（5738 MiB） |
| 哈希表（数据对象） | 约 117 MiB【读码，DroidVM `ipados27/board.toml`】 |
| **根镜像合计** | **约 15,030 MiB，14.7 GiB** |

我之前按 17 的压缩比估的 12 到 13 GB 偏小：SystemOS 不是原始镜像，2.35 GB 的 AEA 解开是 6.0 GB。

根窗 A 的 17 GiB 放得下，和 sheng 的上限约 16.4 GiB 也一致【计算：232.4 GiB 的 `userdata` 减 `data_gib = 215`，减 1 GiB 日志保留】。

iPhone16,1 没有校验记录。OS 的 AEA 比 iPad 大 9%，按比例估根镜像约 15.5 GiB，也放得下【推断】。

## 27 的清单和 17 不同

上游 `scripts/unixnu/userdata.py` 比我们的多一层【读码】：

- 清单版本 2：除窗口外还有“数据对象”（`--object NAME=FILE[:OFFSET]`），`block_size` 4096，`flags` 3。内核自己读，不发布成块设备。
- 用途是“只含头部的根策略”的哈希表和固件。`CONFIG_XNU_SOURCE_STORAGE_TABLES="userdata"`，启动镜像带签名头，哈希表放在分区里（`SourceRootPolicies.cpp:72-73`）。
- 上游版本没有 `[userdata] partition`，这是我们的社区改动，合并时要带上。

所以 27 根镜像是“清单 v2 + OS + SystemOS + 策略哈希表”，不是两个窗口那么简单。

## 分区布局

偏移相对 `unixnu` 起点。

| 区间 | 大小 | 用途 | 状态 |
| --- | --- | --- | --- |
| 0 到 1 MiB | 1 MiB | 17 清单，持久 Data 描述符 | 不动 |
| 1 MiB 到 8948 MiB | 8947 MiB | 17 的 OS、SystemOS | 不动 |
| 8948 MiB 到 10 GiB | 1292 MiB | 保护带 | 空 |
| 10 GiB 到 27 GiB | 17 GiB | **27 根窗 A**，上游 iPad17,3 根 | 第一次写入要用户同意 |
| 27 GiB 到 44 GiB | 17 GiB | **27 根窗 B**，iPhone16,1 根 | 以后 |
| 44 GiB 到 63 GiB | 19 GiB | 27 持久 Data | 以后 |
| 63 GiB 到 64 GiB | 1 GiB | 日志保留区，17、27 共用 | 不动 |

换算：根窗 A 第一块是分区内第 2621440 块（绝对 LBA 110030592）；根窗 B 是第 7077888 块（绝对 LBA 114487040）；Data 区是第 11534336 块（绝对 LBA 118943488）。

## 为什么不破坏 17

1. 17 的内核只从第 0 块读清单，只访问清单列出的窗口和末尾日志区【读码，`RootLayout.h:59`、`NativeUfs.cpp:1546` 起、`LogWindow.h`】。
2. 17 的镜像只用 `fastboot boot` 临时启动，27 也一样，分区里的内容不决定启动哪个系统。选择靠启动镜像【读码，`测试清单.md`】。
3. 27 内核的写窗口只开日志区和以后的 Data 区，都不覆盖 17。
4. 持久 Data 设计里的描述符在第 16 到 255 块，f2fs 文件在 `userdata`，都不冲突。主线落地后 27 可以用同样的办法另开一个文件，避开 19 GiB 的上限。

## 要改的内核代码

`findRootLayout` 现在在分区第 0 块读清单，窗口偏移相对分区起点（`RootLayout.h:5-6`，`:57-59`）。27 要加一个基址：

- 启动参数 `pagani-root-base-mib=10240`（根窗 B 用 27648），只有 27 内核认。
- 清单读取和每个窗口的绝对 LBA 都加上基址。
- `imageBytes` 参与的两处检查要加基址：`ufsLogPlan`（`LogWindow.h`）和 `native_ufs_store_open`（`NativeUfs.cpp:1136` 起），否则日志和 Data 会判定与根镜像重叠的位置不对。

这个改动只进 27 的驱动副本，不改主线。

## Data 卷

| 阶段 | 做法 | 依据 |
| --- | --- | --- |
| 里程碑 M2、M3 | 内存 Data：从 IPSW 的 `private/var` 生成种子，打进启动载荷，重启即丢 | 与 17 主线相同；上游 `--init-data`，`CONFIG_XNU_OWNER_DATA_MOUNT` 要求有种子【读码，`packaging.md`、`payload.md`】 |
| 以后 | 分区内 44 到 63 GiB 持久窗口，`sheng-data-persist=2` | 【读码】`userdata.md:68`。要改 `NATIVE_UFS_READ_ONLY`，第一次由内核写手机存储，要用户另外同意 |
| 更以后 | 等主线的持久 Data 文件方案定型后复用 | 【读码】`PaganiPersistentData/README.md` |

上游明确写了种子不能重做：持久 Data 绑定种子的内容身份，“仅保留 UUID 或从同一 IPSW 重做都不能替代原文件”【读码，上游 `474cfee` 的 `packaging.md`】。所以一旦开始用持久 Data，`data.dbr` 必须长期保存，放 `ios27-experiment/inputs/`，不放临时目录。

内存：13T 有约 16 GiB，主线内核窗口 6 GiB（`owned_memory_size = 0x180000000`），DarwinBoot 上限 8 GiB【读码，`board.toml`】。sheng 跑 27 用 4.75 GiB（`0x130000000`），所以 6 GiB 够起步【推断】。

## 启动镜像

sheng 的 27：`boot.img` 185,733,120 字节，`payload.img` 184,549,376 字节，`kernel.macho` 96,354,304 字节【读码，上游 `porting.toml`】。

13T 的情况不一样：这台手机的 ABL 在 `fastboot boot` 时用的是 `init_boot` 的 ramdisk，所以载荷嵌在 gzip 压缩的内核段里（`embed_payload = true`）【读码，`board.toml`】。现有最大的镜像 113,045,504 字节，载荷卷 192 MiB【实测】。`boot_a` 分区只有 96 MiB，但临时启动不受它限制【实测，113 MB 的镜像已经启动过】。

27 的内核比 17 大，载荷卷大概要 200 MiB 以上。ABL 解压缓冲有没有上限没有验证，这是 M1 第一次上机就要确认的事。

## 写入方式

27 根镜像不能用 `fastboot flash unixnu`，那会从第 0 块覆盖 17。两种可行：

| 方式 | 做法 | 取舍 |
| --- | --- | --- |
| 安卓下 `dd` | 先 `adb push` 到 `/data`，`dd of=/dev/block/by-name/unixnu bs=4M seek=2560 conv=notrunc,fsync` | 不用重启；`userdata` 空闲 248 GiB。**推荐** |
| 稀疏镜像 | 前 10 GiB 标记 DONT_CARE，`fastboot flash` | 项目做过（`flash-unixnu-sparse.log`），要进 fastboot |

写前写后都要做的检查：

0. 所有 `adb` 命令都写 `-s SERIAL_13T`。这台电脑上还会连着别的安卓设备，默认目标不可靠（2026-11-01 本次调研就撞上了）。
1. 写前保存第 0 块，确认 `SHNGROOT` 和 `image_bytes` 与本文一致。
2. `seek` 换算后的字节偏移必须 ≥ 10 GiB，结束 < 27 GiB。
3. 写后第 0 块逐字节不变，17 两个窗口的 SHA-256 与清单一致。
4. 读回 27 根窗校验。

风险：这颗闪存 `bDataReliability` 为 0，掉电时规范不保证同一 LUN 的数据【读码，`测试清单.md`】。`unixnu`、`userdata`、`super` 都在 LUN 0。安卓自己每天也在这样写，但这次是连续写约 15 GB，要保持充电，别在写入时强制重启。

## 未核实

- 根窗 A、B 和 Data 区现在的内容。计划的抽样没有做成：13T 中途离开 adb，我的抽样命令落到了另一台设备（序列号 `SERIAL_OTHER_DEVICE`，`OPD2513`），那台没有这个分区，结果全部作废。这段区间是从 `userdata` 切出来的，可能有旧字节，但没有任何消费者，写入前再核对一次。
- ABL 对更大嵌入载荷的接受度。
- 清单 v2 的数据对象在 `NativeUfs` 里没有实现，要从 `ShengUfs` 移植，见 `调研/drivers-27.md`。
