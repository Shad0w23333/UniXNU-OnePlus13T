# 上机前预检：phone-checkdirs-kernproc.patch 的引用计数模型

依据：内核 `kernel.macho`（sha256 前缀 `8c042601`，已确认含 `_checkdirs_callback` 里的 kernproc 分支）、打过补丁的 `xnu-work`、首测 `20261101-053955-stage15-smoke-1`、完整包运行 `20261031-223924-stage1-full-1`。APFS 地址与 `analysis.json` 用的那版一致（`0x56c8e64` 等处指令相同）。

下文 `xnu-work` 指 `UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree/.cache/xnu-work`，APFS 地址都省略高位 `0xfffffff00`。

证据标记：【真机】日志实测，【读码】XNU 源码，【反汇编】`kernel.macho` 里的 APFS，【未验证】推断。

## 结论

1. **根因站得住，修复是对的，错的只是预言。** 注释的 2 和 3 只数了“有名字的持有者”，漏了 `v_parent`：每个 vnode 都对父目录持有一个 usecount，APFS 自己在每个卷的根下面建了 4 个系统 vnode。
2. **8 和 7 恰好是修复后的精确账本，一个都不差。** 未修复时应该是 7 和 8。新根 7 就是修复生效的第一条真机证据：5 + 2 已经占满，没有 kernproc 多加的那一个。
3. **修复不会漏引用，也不会让卸载卡住。** 强制卸载不等 usecount，只等 iocount。修后旧根走到 0 结束，新根留 2 个在已回收的 vnode 上，无害。
4. **要更正一处：** panic 的最后一步不是 APFS 自己的 `vnode_rele(根)`（`0x56c8e6c`），而是下一步回收 `private-dir` 时释放 `v_parent`（`0x56c8e88` 的 `vflush` → `vfs_cache.c:1268`）。这不改变根因，也不改变修法。
5. **`vfs_setmntsystem` 运行中没有副作用。** XNU 只有三处检查，其中一处因 `MNT_UNION` 本来就不成立，用户态看不到这个位。
6. **launchd 之后 checkdirs 分支不会误判。** 只有再次覆盖当前 `rootvnode` 才会命中，那时命中也是对的。

没有发现 crash 或 hang 级别的新问题。

## 1. 打印那一刻谁持有引用

打印点：`xnu-work/bsd/kern/kern_exec.c:7675-7680`，SystemOS `mount()` 刚返回，Data 还没挂（seq 2043 早于 seq 2055）。此时只有 pid 0 和 pid 1（还没 exec），没有 bindfs，没有其他联合挂载。

| 持有者 | 旧根 | 新根 | 证据 |
|---|---:|---:|---|
| APFS 自持 `vnode_ref(根)` | 1 | 1 | 【反汇编】`0x56cea60` |
| `private-dir` 的 `v_parent` | 1 | 1 | 【反汇编】`0x56ce904-0x56ce90c` 把 `vnfs_dvp` 设为根，`0x56ce948` 建 vnode |
| `.snaps` 的 `v_parent` | 1 | 1 | 【反汇编】`0x56ce63c-0x56ce644`，`0x56ce67c` |
| 备用 xattr vnode（`+0x3e0`）的 `v_parent` | 1 | 1 | 【反汇编】`0x56ce014` 调 `0x5764d24`，`0x5764ef0-0x5764ef8` 设 `vnfs_dvp` |
| xattr I/O vnode（`+0x810`）的 `v_parent` | 1 | 1 | 【反汇编】`0x56ce04c` 同一函数 |
| SystemOS 的 `mnt_vnodecovered` | 1 | 0 | 【读码】`vfs_syscalls.c:1694` |
| `/dev` 的 `v_parent` | 1 | 0 | 【读码】`bsd_init.c:1163` → `devfs_vfsops.c:494` → `vfs_syscalls.c:kernel_mount` 的 `namei` |
| `/private` 的 `v_parent` | 1 | 0 | 【真机+读码】msgbuf `63.469812 Failed to open swap file 30`，见下 |
| `rootvnode`，kernproc 的 `fd_cdir` 共用 | 0 | 1 | 【读码】`vfs_syscalls.c:2622-2627`，`bsd_init.c:434` |
| pid 1 的 `fd_cdir` | 0 | 1 | 【读码】`kern_descrip.c:877-881` 取得，`vfs_syscalls.c:2559` 迁到新根 |
| **合计** | **8** | **7** | 【真机】seq 2043 |

为什么这些都算 usecount：

- `vnode_create_internal`：`vfs_subr.c:6990` 取 `dvp`，`vfs_subr.c:7149-7151` `if (dvp && vnode_ref(dvp) == 0) vp->v_parent = dvp;`。
- 查找路径补父指针时同样取引用：`vfs_lookup.c:692-697` → `vfs_cache.c:1127`。
- 只有回收子 vnode 才还：`vfs_subr.c:3174` → `vfs_cache.c:1268`。子 vnode 待在空闲表里时，引用一直在。

APFS 这 5 个每个卷都有：`handle_mount` 在打印 `vol-uuid` 那行后立刻调 `apfs_load_fs_vnodes`（`0x56cd4b4`）。msgbuf 里 `handle_mount:817` 对 `disk3s1` 和 `disk1s1` 各一行，都早于打印。

`/private` 的来历：交换文件线程走 `vm_swapfile_open` → `vnode_open("/private/var/vm/swapfile…", O_CREAT…)`（`vm_compressor_backing_file.c:64`，路径见 `vm_compressor_backing_store.h:60-61`）。返回 30（EROFS）说明前面三级目录都查到了；`osmnt-21H461/private/var/vm` 确实存在。时间 `63.469812` 早于 SystemOS `mount-complete` 的 `63.473431`，而 `checkdirs` 在 `VFS_MOUNT` 返回之后。

对照未修复内核：kernproc 分支走原路径，旧根再少 1，新根再多 1，即 **旧根 7、新根 8**（`vfs_syscalls.c:2557-2583`）。

把握和缺口：

- APFS 的 4 个系统 vnode 和 `vnode_ref` 是反汇编确认的，`/dev` 是读码确认的，`/private` 有真机时间戳。总数和日志完全吻合，没有余数。
- 【未验证】没有打印过子 vnode 名单，“真实子 vnode 恰好是这两个”靠总数吻合推出。
- launchd 之后两个数都会变：联合查找会给两边加子 vnode，目录 fd 读到末尾会把引用从上层换到下层（`vfs_syscalls.c:10463-10470`，`vfs_attrlist.c:4299-4323`）。

## 2. 关机路径

### 根因还站得住吗

站得住，而且比之前更硬。旧根的 8 个持有者在卸载时各释放一次，一共 8 次。未修复时计数只有 7，最后一次必然 -1。panic 现场全部对得上：`usecount -1`、`v_tag 0`、`v_type 8`、`v_flag 0x84801`（有 `VROOT`，没有 `VMOUNTEDHERE`）。

kernproc 确实进回调：`bsd_init.c:706` 在 allproc 里，`proc_iterate` 不过滤 pid 0，`proc_find(0)` 走 `kern_proc.c:proc_find` 的 `proc_ref(kernproc, false)`。所以“kernproc 没被遍历、补丁是空操作”这个解释也排除了。

### 完整路径

1. launchd 状态机：【真机】msgbuf `System shutdown initiated by: SpringBoard[27]`，经 `COMMITTED`、`BEGIN_SHUTDOWN` 等。`launchd` 字符串里有 `READY_FOR_REBOOT`、`Will be calling reboot(2) with flags`。
2. `reboot(2)`：`kern_xxx.c:154` 给 launchd 置 `P_REBOOT`，进 `reboot_kernel`（`kern_shutdown.c:177`）。
3. `proc_shutdown(TRUE)`（`kern_shutdown.c:233`）：杀掉 launchd 以外的进程。`kern_shutdown.c:745-747` 在这里返回，**launchd 没有挂起**。
4. `sync`（`:249`）。
5. 第一遍 `vfs_unmountall(TRUE)`（`:272`）：`vfs_subr.c:4303` 从链表尾往头走，`vfs_subr.c:4256-4259` 跳过 `MNT_ROOTFS` 和 `MNTK_SYSTEM`。根卷、devfs（`vfs_syscalls.c:1247-1250`）和修复后的 SystemOS 都跳过，其余 `dounmount(mp, MNT_FORCE, …)`。
6. `proc_shutdown(FALSE)`（`:279`）：`:752` 挂起 launchd。
7. 第二遍 `vfs_unmountall(FALSE)`（`:287`）：顺序是 SystemOS → devfs → 根卷。
8. `dounmount(SystemOS)`：`vfs_syscalls.c:2885` 回收普通 vnode，`2897` 进 APFS，`3042` 对旧根 `vnode_rele(coveredvp)`。修前这一步在第一遍，修后在第二遍。
9. `dounmount(根卷)`：`vfs_syscalls.c:2885` 带 `SKIPSYSTEM|SKIPROOT|FORCECLOSE`，回收 `/dev`、`/private` 和运行中产生的子 vnode，每个经 `vfs_subr.c:3174` 还 1 个。
10. `VFS_UNMOUNT` → `apfs_vfsop_unmount`（`0x56c8214`）：
    - `0x56c85f8` `vflush(SKIPSYSTEM|SKIPROOT|FORCECLOSE)`：普通 vnode 已没有了。
    - `0x56c8c74`、`0x56c8c80`、`0x56c8c8c`：释放 APFS 对两个 xattr vnode 和 `.snaps` 的自持引用（不是对根的）。
    - `0x56c8cb8` `vflush(private-dir, SKIPROOT|FORCECLOSE)`：回收这三个，根减 3。
    - `0x56c8d30` `vnode_isinuse(根, 2)`：强制卸载时结果被忽略。阈值 2 正好是“自持 + `private-dir`”，这是模型的旁证。
    - `0x56c8e64` `vflush(private-dir, FORCECLOSE)`：回收根。`vfs_subr.c:2945-2955` 走强制分支，`:3189` 置 `VT_NON`，`:6854` 置 `VBAD`，usecount 不变。
    - `0x56c8e6c` `vnode_rele(根)`。
    - `0x56c8e74` `vnode_rele(private-dir)`。
    - `0x56c8e88` `vflush(NULL, FORCECLOSE)`：回收 `private-dir`，`vfs_subr.c:3174` → `vfs_cache.c:1268` `vnode_rele_internal(根, 0, 0, 1)` → `vfs_subr.c:2683`。
11. 未修复：`old_usecount` 为 0，`vfs_subr.c:2691` panic，`usecount -ve : -1`。修复后：减到 0，`vfs_subr.c:2723` 见 `VL_DEAD`，`:2741` 进链表，正常返回。

### 旧根账本

`k` 是运行中多出来的子 vnode 和目录 fd。

| 时刻 | 未修复 | 修复后 |
|---|---:|---:|
| 打印时 | 7 | 8 |
| 运行中 | 7 + k | 8 + k |
| SystemOS 卸载，释放覆盖引用 | 6 + k | 7 + k |
| 根卷第一次 `vflush` 回收普通子 vnode（2 + k 个） | 4 | 5 |
| `0x56c8cb8` 回收 `.snaps` 和两个 xattr | 1 | 2 |
| `0x56c8e64` 回收根，变 VBAD | 1 | 2 |
| `0x56c8e6c` `vnode_rele(根)` | 0 | 1 |
| `0x56c8e88` 回收 `private-dir` | **-1，panic** | 0 |

新根修复后的走势：7 → 回收三个系统 vnode 后 4 → 3 → 2。剩下 `rootvnode` 和 launchd 的 `fd_cdir`，留在已回收的 vnode 上。未修复时是 3。

### 会不会漏引用、会不会卡住

不会。

- 补丁没有漏引用：kernproc 的 `fd_cdir` 从头到尾共用 `rootvnode` 那一个，`checkdirs` 第 2622-2627 行把它搬到新根。
- `vflush` 带 `FORCECLOSE` 时不看 usecount（`vfs_subr.c:2945`）。
- `vnode_drain` 只等 iocount（`vfs_subr.c:6590`）。
- `0x56c8d34` 的 `cbnz w23` 在强制卸载时跳过“root vnode is busy”。
- `dounmount` 返回 0，`vfs_unmountall` 的重试循环（`vfs_subr.c:4308-4313`）不会触发。

和已跑通的路径相比，这次的差别很小：第一遍只少了链表最靠前的 SystemOS 这一步，第二遍多了这一步，而且 launchd 已挂起。SystemOS 的强制卸载在上次完整运行里成功过（panic 的 `v_flag` 没有 `VMOUNTEDHERE`）。所有补丁里没有长期持有 vnode 引用的代码（已扫描两个目录的补丁）。

【未验证】`dounmount(根卷)` 里 `apfs_vfsop_unmount` 从 `0x56c8e8c` 往后的部分、`vfs_syscalls.c:2912-2921` 对根设备的收尾、`if_down_all`、`host_reboot` 和 `pagani-halt=2` 从未在这台机器上走过，不在本次审查范围内。

## 3. `vfs_setmntsystem(cache_mount)` 的副作用

设置点 `kern_exec.c:7710`，它写的是 `mnt_kern_flag` 的 `MNTK_SYSTEM`（`kpi_vfs.c:662-667`）。

全部读取点只有三处【读码，`bsd iokit osfmk security libkern` 全文搜索】：

| 位置 | 作用 | 对 SystemOS 的影响 |
|---|---|---|
| `vfs_subr.c:4256` `unmount_callback` | 第一遍跳过 | 这是想要的效果 |
| `vfs_syscalls.c:2749` `safedounmount` | `unmount(2)` 返回 `EBUSY` 并打一行日志 | 只影响有人主动卸载 `/`，修前那样做会把系统弄死，修后更安全 |
| `vfs_syscalls.c:9253` `renameat_internal` 重命名挂载点 | 不允许 | 无变化：同一条件 `9252` 已排除 `MNT_UNION` |

逐项回答：

- 强制只读：没有关系。`MNT_RDONLY` 由挂载时的 `MNT_RDONLY` 决定。
- fsevents：`vfs_syscalls.c:2793` 只看 `MNT_FORCE`，与这个位无关。
- 快照：`MNTK_SYSTEM` 不参与。
- 卸载顺序：改变，符合预期。
- launchd 的挂载检查：用户态看不到。`statfs` 的 `f_flags` 来自 `mnt_flag`，`vfs_flags()` 只掩码 `MNT_CMDFLAGS|MNT_VISFLAGMASK`（`kpi_vfs.c:467`）。`vfs_syscalls.c:3597` 看的是 `MNTK_SYSTEMDATA`，是另一个位。
- 内核扩展：`mount.h:1354` 只有设置函数，没有读取函数。APFS 自己只在 `apfs_graft` 路径调用设置函数。【未验证】没有逐条排查闭源扩展是否按偏移直接读结构体，但 APFS 用的都是访问函数。

这个位的目的也成立：`vm_unix.c:2622` 要求共享缓存文件和 `rootvnode` 同一挂载，所以 launchd 映射的缓存确实在 SystemOS 上；`kern_sig.c:2148` 的 `P_REBOOT` 只拦 `psignal_internal`，异常走 `threadsignal` 不受影响，`kern_exit.c:1701` 对 launchd 总是 panic。APFS 在 `apfs_graft` 路径里也给嫁接卷设这个位（`0x5686e6c`），补丁的做法和它一致。

## 4. launchd 之后的 checkdirs

`checkdirs` 只有两个调用点：`vfs_syscalls.c:1701`（`mount_common` 的全新挂载）和 `:2132`（`MNT_IMGSRC_BY_INDEX`，这里不用）。`MNT_UPDATE` 走 `goto update` 分支，不调 `checkdirs`。

launchd 之前：`/private/var`、全部 bindfs 视图、`/private/preboot` 及其下两个绑定都走过，覆盖点都不是 `rootvnode`，kernproc 在 `:2515` 第一道检查就返回。【真机】首测里 `overlay … stage 5 status 0`、`system apps view stage 5 status 0`，全部成功。

launchd 之后，完整包运行里能看到的挂载相关进程只有三个【真机】：`fsck`（pid 2）、`mount`（pid 3，内置任务 `mount -P 1`，10 ms 结束，没有 `mount_apfs` 子进程）、`mount_hfs`（pid 381，退出状态 1）。【未验证】`mount -P 1` 内部有没有发起 `mount(2)`，日志看不出来。

即使有，也不会误判：

- 分支条件是 `p == kernproc && olddp == rootvnode`，只有挂到当前根 `/` 上才成立，普通挂载点不成立。
- kernproc 的 `fd_cdir` 始终等于 `rootvnode`（`bsd_init.c:434`、`imageboot.c:489/953`、本补丁），`fd_rdir` 为空，所以不存在“kernproc 指向 `olddp` 但 `olddp` 不是根”的情况。
- 真有人再盖一层到 `/`，分支照样正确：`checkdirs` 第 2622-2627 行还是会搬 `rootvnode` 的引用。

## 5. 判据和注释

### 下一次上机看什么

看 `SystemOS root refs` 这一行，**决定性的是 `root use`**：

| old | root | 含义 |
|---:|---:|---|
| 8 | 7 | 修复生效，和本次一样 |
| 7 | 7 | 修复生效，只是交换文件线程晚于打印。查 msgbuf，`Failed to open swap file` 会在 `mount-complete` 之后。这种情况 `/private` 依然落在旧根（SystemOS 卷只有 `System` 和 `usr`，联合查找回落到下层） |
| 7 | 8 | 没修复，或补丁没进这个内核 |
| 其他 | | 多了真实子 vnode，账本要重算，不能直接判失败 |

不要用两数之和判断：修前修后都是 15。

关机侧的判据不变：没有 P 记录，出现 `power off as restart`。万一再 panic，比对 panic 里的 vp 和 `old <地址>`，并看 msgbuf 有没有 `vfs_unmountall(system) start`。

更稳的做法是打印子 vnode 数，这样判据不依赖启动时序：`use - kids` 旧根应为 2，新根应为 3，修前是 1 和 4，原来的预言在这个口径下完全成立。写法（放 `vfs_subr.c` 或 `kern_exec.c`，后者已包含 `mount_internal.h` 和 `vnode_internal.h`）：

```c
static int
native_owner_root_children(vnode_t dvp)
{
	mount_t mp = vnode_mount(dvp);
	vnode_t vp;
	int count = 0;

	mount_lock(mp);
	TAILQ_FOREACH(vp, &mp->mnt_vnodelist, v_mntvnodes) {
		if (vp->v_parent == dvp) {
			count++;
		}
	}
	mount_unlock(mp);
	return count;
}
```

本次预期 `old use 8 kids 6, root use 7 kids 4`。这只是加固，不值得为它单独重编。

### 注释改法

注释不影响二进制，随下一次重编带上即可。

`phone-checkdirs-kernproc.patch` 第 8-10 行（`kern_exec.c:7672-7674`）改成：

```c
	/* phone-checkdirs-kernproc.patch. Before launchd runs (its directory descriptors move these counts). Every APFS root starts at 5:
	 * the volume's own reference and the v_parent references of private-dir, .snaps and the two xattr vnodes (every vnode holds one on
	 * its parent). The covered root adds the covering mount's and its children looked up so far, /dev and /private (the swap file's
	 * path): 8. The new root adds rootvnode's, which kernproc's current directory shares, and pid 1's current directory: 7. A checkdirs
	 * that took kernproc's reference it never had gives 7 and 8. The addresses compare with a vnode panic's. */
```

`phone-checkdirs-kernproc.patch` 第 41-45 行（`vfs_syscalls.c:2522-2526`）后半句改成：

```c
	 * the release below would take a reference another holder of the covered root still has to drop. The last of them, private-dir's
	 * v_parent reference, then goes when apfs_vfsop_unmount's last vflush reclaims it, and finds the reclaimed root at 0: the panic in
	 * _vnode_rele_internal at usecount -1, once SystemOS is mounted over "/".
```

同步修改：

- `build-userspace/a830-inputs/fullrun/stage15-small.md:134`：判据改成修后 8 和 7，修前 7 和 8。
- `Adreno830/docs/PROGRESS.md:476`：“判据 use 2 和 3”同样改掉。
- `UniXNU-Pagani_SM8750-gpu/scripts/test-checkdirs-kernproc-patch.py` 第 5-6 行：说明里的 2 和 1 是“不含 `v_parent` 的持有者”。模型最好补上 4 个系统子 vnode 和 2 个真实子 vnode，并按第 2 节的释放顺序走，断言 8/7、7/8 和最后的 0/-1。

## 发现清单

### R1（wrong-data）补丁注释和上机判据的数字错了

位置：`UniXNU-Pagani_SM8750-gpu/patches/xnu/phone-checkdirs-kernproc.patch:8`，以及 `stage15-small.md:134`、`PROGRESS.md:476`、测试脚本说明第 5-6 行。

路径：`handle_mount`（`0x56cd4b4`）→ `apfs_load_fs_vnodes`（`0x56cdf94`）建 4 个 `vnfs_dvp` 为根的系统 vnode → `vfs_subr.c:7149` 各取 1 个 usecount；`devfs_kernel_mount` 和交换文件路径再给旧根加 2 个子 vnode。

主机测试抓不到的原因：模型的 `struct vnode` 只有 `v_usecount`，没有父子关系，也没有 APFS 的行为，账本在自己的假设里是自洽的。闭源 APFS 的系统 vnode 只有反汇编或上机才看得到。

### R2（minor）之前对 panic 调用点的认定差一步

位置：`build-userspace/a830-inputs/fullrun/analysis.json:203`、`:291`。

认定的是 `0x56c8e6c vnode_rele(根)`，实际这一步从 1 减到 0，下一次 `vflush`（`0x56c8e88`）回收 `private-dir` 才到 -1。旧账本“旧根修前 1、修后 2”也要改成 7 和 8。根因和修法不受影响。

主机测试抓不到的原因：panic 记录没有回溯，模型里没有 `private-dir`。

### R3（minor，未验证）关机全程没有进度记录

位置：`xnu-work/bsd/vfs/vfs_subr.c:4245`（`unmount_callback`），`kern_shutdown.c` 全文也没有 `boot_progress` 调用。

这不是缺陷。第一次走通的路径如果卡住或再 panic，现有证据只有 P 记录，`unmount(...)` 的 `printf` 只进 msgbuf，而上次 msgbuf 停在 panic 前 3 秒。`analysis.json` 当时建议过补这一点，这一轮没有做。

建议：在 `unmount_callback` 的跳过、`dounmount` 前后各加一行 `boot_progress_root`（声明在 `kern/early_boot_progress.h`），内容是挂载名和 `u_only_non_system`、`error`。下一次本来就要重编时带上，不必单独重编。

主机测试抓不到的原因：这是真机现场的可观测性问题。

## 核对范围

已逐项核对：

- 补丁：`phone-checkdirs-kernproc.patch`、`phone-bindfs-vget.patch`、`native-union-getattrlistbulk.patch`、`native-union-lower-directory.patch`，以及两个补丁目录里所有 `vnode_ref`、`vnode_rele`、`vnode_open`、`rootvnode`、`unmount` 相关改动。
- `kern_exec.c`：`native_owner_cache_prepare`、`native_owner_data_prepare`、`native_gpu_addon_mount`、`native_cryptex_os_bind`、`load_init_program`。
- VFS：`mount_common`、`__mac_mount`、`kernel_mount`、`checkdirs`、`checkdirs_callback`、`dounmount`、`dounmount_submounts`、`safedounmount`、`vflush`、`vclean`、`vnode_reclaim_internal`、`vnode_rele_internal`、`vnode_ref_ext`、`vnode_create_internal`、`vnode_update_identity`、`unmount_callback`、`vfs_unmountall`、`lookup_traverse_union`、`getdirentries` 的联合切换。
- 进程和关机：`reboot`、`reboot_kernel`、`proc_shutdown`、`set_rootvnode`、`bsd_init` 的根卷段、`fdt_fork`、`proc_iterate`、`proc_find`、`IOBSDMountChange`。
- bindfs：`bindfs_mount`、`bindfs_unmount`。
- APFS 反汇编：`handle_mount` 调用点、`apfs_load_fs_vnodes`、`create_empty_xattr_vnode`、`apfs_vfsop_unmount`。
- 证据：首测的 `merged.log`、`msgbuf.txt`，完整包运行的 `merged.log`、`msgbuf.txt`，`osmnt-21H461` 和 `systemos-21H461` 的目录，`launchd` 和 `mount` 的字符串。
