# 钥匙串修复（sysfix 键 `keybag`）：易失软件钥匙包

- 时间：2026-10-31 08:40–09:30。没碰手机、没提交、没往 `UniXNU/out-gpu` 写任何东西；共享集成文件（Stage7Probe、components.toml、board.toml、defconfig、patches/xnu/series、make-a830-layout.sh、make-phone-seed.py）一个都没改。
- 改动只在本键拥有的驱动目录：`UniXNU-Pagani-Drivers/drivers/NativeKeyStoreEvents/`（13T 内核实际链接的 AppleKeyStore 用户客户端；构建树 `tree/drivers/NativeKeyStoreEvents` 是指向它的符号链接，由 `scripts/build-stage7-driver.py NativeKeyStoreEvents` 编进 Stage 7 显示归档，PROGRESS 09:16 第⑨条也写明钥匙串改动必须落在这里）。官方组件副本 `UniXNU/drivers/AppleKeyStoreState` 13T 不用，没动。
- 审查用补丁：`sysfix/keybag/NativeKeyStoreEvents.diff`（相对 `UniXNU-Pagani-Drivers` HEAD `14f50fc`，已验证能干净应用并逐字节还原工作区）。

## 1. 结论

1. 选择子 0xa（`aks_wrap_key`）、0xb（`aks_unwrap_key`）、0x1f（`aks_get_configuration` / `aks_prederived_is_enabled`）、0x8 / 0x20（`aks_lock_device`）按 21H461 的 libaks 契约实现。类密钥每次开机在第一次包/解包时由内核随机数生成器产生（每类 256 位），包装格式就是 RFC 3394 AES 密钥包装（默认 IV `A6A6A6A6A6A6A6A6`），输出 = 输入 + 8 字节，与 securityd 准备的缓冲区（包 40 字节、解 32 字节）完全吻合。AES 用内核里已注册的 corecrypto（libkern `aes_encrypt`/`aes_decrypt`，与 NativeAES 同一条路径）。
2. 默认开（`pagani-keybag` 缺省即 1）；`pagani-keybag=0` 精确退回旧行为（这五个选择子全部 `0xe00002c7`），`pagani-keybag=2` 另外要求钥匙串类（6–12）的调用者带 `com.apple.keystore.access-keychain-keys` 权限（securityd 有，未上机验证，可选加固）。默认开的理由见第 9 节。
3. 主机测试两套全过（ASan + UBSan，另跑 `-O2` 与 ThreadSanitizer）；14 个故意埋的缺陷（改 IV、去掉 prepare、去掉完整性校验、计数器差一、漏擦除等）全部被测试抓到；按构建树编译记录真编译 0 错误 0 警告，234 个外部符号在当前 `kernel.macho` 里全部存在。
4. 早上预期：`stage7 value=0xa / 0x1f / 0x8 / 0x20: key operation unavailable` 从每次开机 90 条左右降到 0；只剩 0x39、0x87 各 1 条（与钥匙串无关，见 3.7）。

## 2. 证据（今晚以前的运行）

| 运行 | 0xa | 0x1f | 0x8 | 0x20 | 0x39 | 0x87 |
| --- | --- | --- | --- | --- | --- | --- |
| `20261031-070836-pkgD2-widgets-wallpaper-1` | 64 | 21 | 3 | 2 | 1 | 1 |
| `20261031-070114-pkgD-longpress-1` | 68 | 21 | 6 | 2 | 1 | 1 |

（`grep -o "stage7 value=0x..: key operation unavailable" merged.log | sort | uniq -c`；第一条 0xa 在 D2 的 27.054 s。`20261031-063106-pkgA-diag-cc-f2-1` 只读回 109/382 条，没有这类记录。）原驱动只实现 0/4/5/9/12/13/26/27/55/101/102、7、14、17/35，其余一律 `0xe00002c7`。

## 3. 逆向依据（21H461，只记事实与地址，苹果文件留在本地）

工具：`ipsw dyld disass/xref/str/symaddr`（共享缓存 + `dsc-21H461.a2s`）、IDA 9.5 无界面 + IDAPython（`re/ida/*.py`）。原始输出在 `sysfix/keybag/re/`（本地证据，不要外传）。

### 3.1 libaks 全部选择子（AppleKeyStore.framework 1655.140.2，`re/aks-selectors.txt`）

整份镜像反汇编后找出每个 `IOConnectCallMethod` 前的 `w1`。与本次有关的：`0x7 _aks_get_lock_state`、`0x8/0x20 _aks_lock_device`（`csel w1, w9(0x20), w8(0x8), ne`，按第二个参数选）、`0xa _aks_wrap_key`、`0xb _aks_unwrap_key`、`0xe _aks_get_system`、`0x1f _aks_get_configuration` 与 `_aks_prederived_is_enabled`、`0x2b __aks_operation`（ref key，ACL 条目用）、`0x39 _aks_remote_peer_get_state`、`0x81–0x84 _aks_kc_backup_*`、`0x87 _aks_bak_get_beacon_internal/_swizzler_internal`。0x8、0x20、0xa、0xb、0x1f 没有别的函数用。

### 3.2 调用约定（IOConnectCallMethod 参数）

| 选择子 | 函数（地址） | 标量输入 | 结构输入 | 标量输出 | 结构输出 |
| --- | --- | --- | --- | --- | --- |
| 0xa | `_aks_wrap_key(key, len, class, bag, out, &outlen, &class_out)` 0x202fd5090 | 2 个：`[bag, class]`，均为 int32 符号扩展 | 明文密钥，`len` 字节 | 1 个：实际类别 | 包装结果，容量 `*outlen`，返回后 `*outlen` = 实际长度 |
| 0xb | `_aks_unwrap_key(wrapped, len, class, bag, out, &outlen)` 0x202fd51a0 | 2 个：`[bag, class]` | 包装结果 | 无（`outputCnt` 传 NULL） | 明文密钥 |
| 0x1f | `_aks_get_configuration(bag, &dict)` 0x202fc0648；`_aks_prederived_is_enabled(bag, &ctx)` 0x202fd40dc | 1 个：`[bag]` | 无 | 无 | 0x8000 字节缓冲（大于 4096，IOKitLib 走带外描述符）；返回长度大于 0x8000 时 libaks 报 `0xe007c00a` |
| 0x8 / 0x20 | `_aks_lock_device(bag, flag)` 0x202fd80a8 | 1 个：`[bag]` | 无 | 无 | 无 |
| 0x39 | `_aks_remote_peer_get_state` 0x202fd7e04 | 1 个 | 对端 ID | 无 | 0x1000 字节 DER |
| 0x87 | `_aks_bak_get_beacon_internal` 0x202fdaa30 | 2 个 | 有 | 无 | 0x4000 字节 |

libaks 在拿不到连接时自己报 `0xe00002bc`，参数为空报 `0xe00002c2`。连接建立 `__copy_aks_client_connection`（0x202fc0554）：先按路径 `IOService:/IOResources/AppleKeyStore`，失败再按类名匹配，`IOServiceOpen` 类型 0 后发选择子 0 握手，握手失败就关连接。

### 3.3 包装格式

libaks 自带的用户态解包 `_rfc3394_unwrap`（0x202fc44c8，源文件名 `AppleKeyStore_libs/aeskeywrap.c`）是标准 RFC 3394：A 与给定 IV 比较、KEK 为 16/24/32 字节；`_rfc3394_unwrapped_size` = 输入 − 8。真机钥匙串条目里的包装密钥也是 40 字节的 RFC 3394 结果。所以输出与真机同构：32 字节密钥 → 40 字节，类别靠“每类一把密钥”绑定（用错类别解包就过不了完整性校验）。

### 3.4 securityd 用法（`/usr/libexec/securityd`，静态链接了同一份 libaks 客户端代码）

- 静态副本的包装/解包：`sub_100019BF0`（选择子 0xa，`input = {bag, class}`）、`sub_100014C70`（0xb），参数布局与框架版一致。
- `ks_crypt`（`sub_100014870`）的错误映射（决定我们该返回什么）：
  - `0xe00002f0`（kIOReturnNotFound）→ `errSecNotAvailable`（-25291）“No key available for class”；
  - `0xe007c013`（libaks 解码错误）和 `0xe00002bc` → `errSecDecode`（-26275）“Item can't be decrypted on this device, ever, so drop the item”（securityd 会删掉该条目）；
  - `0xe00002c1` / `0xe00002e2` → `errSecInteractionNotAllowed`（-25308）“keychain is locked”；
  - 其它 → `errSecNotAvailable`。
- 缓冲大小：`-[SecDbKeychainItemV7 wrapToAKS:…]`（0x10001ee24）无 ACL 时 `initWithLength:40`，并把返回的类别写回 `self->_keyclass`；`-[… unwrapFromAKS:…]`（0x100017660）`dataWithLength:32`；元数据密钥 `writeKey:…`（0x1001bacf0）同样 40 字节，并把返回类别存进 `metadatakeys.actualKeyclass`；`validateWrappedKey:…`（0x1001ba90c）解包用 32 字节，`actualKeyclass` 为 0 且失败时会再用 `class | 0x20` 试一次；`keyForKeyclass:…`（0x1000158a4）把 ≥32 的类别 `& 0x1f`。带 ACL 约束的条目走 ref key（`0x2b`），不经过 0xa。
- 钥匙包句柄：全局 `dword_1003460D0` 在 `start`（0x10002940c，日志 “using system keychain handle”）和转密路径（0x1001360f0、0x100140790）被设成 **-6**，在 0x100135eac（“using default keybag”）被设成 **0**。MobileKeyBag 的 `handleToUse`（0x1b5192b90 / 0x1b5192658）单用户为 0、多用户为 -3。所以 0、-3、-6 都要认，并且必须是同一个钥匙包（securityd 存条目时不记句柄）。
- 锁状态：securityd 用选择子 7（`sub_1000251B0`，6 处调用，含 `-[CKKSActualLockStateProvider queryAKSLocked]`）判断“已锁/开机后解锁过”；现有回答 4（开机后解锁过、未锁）就让 A 类与 C 类都可用，不用改。`aks_get_system(0)`（`sub_100147E20`，选择子 0xe）拿到 0 后打印 “using hwaes key”。
- securityd 只在备份钥匙包路径（`aks_kc_backup_open_keybag`，`sub_10002568C`）调用 0x1f；每次开机那 21 次 0x1f 来自 MobileKeyBag 的客户端。
- securityd 的权限里有 `com.apple.keystore.access-keychain-keys`（`pagani-keybag=2` 就是检查它）。

### 3.5 0x1f 需要回答什么

- 回答格式由 `_der_dict_iterate`（0x202fc1778）+ `_dict_find_params_cb` 决定：外层 DER SET（0x31），每项 SEQUENCE{UTF8String 键（头部必须 2 字节），值}；数值 `_der_get_number`（INTEGER，1–8 字节，有符号）。键名（取自 libaks 数据）：`gp` GracePeriod、`bd` BackOffDelay、`mua` MaxUnlockAttempts、`epp`、`etp`、`cf` ConfigFlags（位 1 = InactivityRebootEnabled）、`uuuid`、`guuid`、`ri`、`rf`、`rti`、`ms`、`mbe`、`cmpg`、`cpg`；预派生口令：`pdps` 盐、`pdpi`、`pdpt`。键缺失时 `_set_cf_key` 直接跳过。
- `aks_prederived_is_enabled` 找不到 `pdps` 就返回“未启用”——没有口令时正是正确答案。
- 消费者：`MKBGetDeviceConfigurations`（0x1b5192374）原样把字典交出；`MKBDeviceGetGracePeriod` 先查类型再用；SpringBoard `-[SBFMobileKeyBag maxUnlockAttempts]`（0x190076ce0）在字典为空、键缺失、不是数或为 0 时都记一条错误日志并用默认值 10。
- 所以回答 `{cf 0, mua 10}`（21 字节）：不触发 SpringBoard 的错误日志，不编造宽限期、托管、恢复、备忘、预派生等任何口令相关数据。

### 3.6 0x8 / 0x20 为什么可以直接成功

`MKBLockDevice`（0x1b51991b4）只把 `LockDeviceNow` 选项翻成 `aks_lock_device(handleToUse(), flag)` 并原样返回错误码；SpringBoard 的 `-[SBFMobileKeyBag lockSkippingGracePeriod:]`（0x1900769bc，块 0x190076a28）调用后根本不看返回值。没有口令的钥匙包本来就没有可锁的东西，所以成功且不改任何状态：锁状态仍是 4，A 类密钥仍可用——与没设口令的真机一致；今天每次开机那 3–6 条 0x8 和 2 条 0x20 失败只是日志噪声。

### 3.7 为什么 0x39、0x87 不答

0x39 是“远端对端”（手表/Mac 解锁一类）的令牌状态，0x87 是备份钥匙包的 beacon/swizzler；与钥匙串存取无关，这台机器没有对端也没有备份钥匙包，真机此时同样是失败返回。今天每次开机各失败 1 次、没有可见后果，维持原样（不编造状态）。

## 4. 实现要点（`NativeKeyStoreEvents.cpp`）

- 类与句柄：有密钥的类 1–4（A B C D）、6–11（ak ck dk aku cku dku）。12（akpu，需要设了口令）和其它所有类（0、5、13+、带 0x20 的代际变体、负数）返回 `0xe00002f0`——与没有口令的真机一致（securityd：errSecNotAvailable）。句柄 0、-3、-6 指同一个钥匙包，其它句柄 `0xe00002f0`。标量不是 int32（符号或零扩展）时 `0xe00002c2`。
- 尺寸：密钥 16–512 字节、8 字节对齐，否则 `0xe00002c2`；输出不够 `0xe00002c4`；解包输入长度不可能来自包装时直接按解码错误 `0xe007c013` 处理；没有输入 `0xe00002c2`；带外输入（>4096 字节）不碰、直接 `0xe00002c2`。
- 解包先在内核临时区做完 RFC 3394 并做常数时间的 IV 比较，通过后才拷给调用者；失败时临时区清零，调用者缓冲区原封不动。所有临时密钥、AES 上下文、中间块都 `cc_clear`。
- 密钥生命周期：服务 `start` 只读启动参数、分配锁；第一次包/解包时在锁内依次检查 corecrypto 的 CBC 模式已注册、块长 16、上下文不超过 libkern 固定上下文（否则 `aes_encrypt_key` 会 panic——这里先查，绝不调用），跑一遍 RFC 3394 第 4.6 节向量（包、解、篡改一位必须被拒），再 `read_random` 取 10 把密钥并检查非零、互不相同。任一步失败：本次开机钥匙包关闭，包/解包回 `0xe00002d8`，记一条 U 行。服务 `stop` 时先关模式再清零密钥（锁保留，免得仍在调用中的客户端用到已释放的锁）；最后一个引用释放（`free`）时再清零一次并释放锁。
- 顺带修了一个潜在 panic：原来 17/35 在调用者给出 >4096 字节输出（走带外描述符）时，不 `prepare()` 就 `writeBytes()`，而 XNU 对未上锁的用户描述符取物理段会 `panic("IOGMD: not wired …")`。libaks 自己只给 4096 字节所以没触发过；0x1f 一定走描述符，所以统一改成 prepare → writeBytes → complete。
- 日志：失败照旧经 Stage7Probe 记 R 行（选择子）；另外每类情况只记一次 U 行：`keybag: pagani-keybag=…`、`keybag: up: 10 class keys …`、`keybag: first wrap by <进程>: bag … class … 32 -> 40 bytes`、`keybag: first unwrap by …`、`keybag: refused <操作> for <进程>: bag … class … in … out … rc …`、`keybag: configuration answered …`、`keybag: lock request answered …`，以及第 64/1024/16384 次成功时的计数行。每次开机新增约 6–8 条 U 行，同时少掉约 90 条 R 行，32 KiB 事件环净减负。
- 不占启动参数预算（PROGRESS 09:16 第④条：只剩约 135 字节），因为默认开、不用加参数。

## 5. 主机测试

```
python3 -B UniXNU-Pagani-Drivers/drivers/NativeKeyStoreEvents/test_closed_boundary.py
python3 -B UniXNU-Pagani-Drivers/drivers/NativeKeyStoreEvents/test_volatile_keybag.py
```

两套都把真实的 `NativeKeyStoreEvents.cpp` 编进主机替身（`Test/mock` 模拟 IOKit/libkern/corecrypto/pexpert，`Test/host.h` 提供钩子、按 FIPS-197 写的参考 AES、按 libaks 写法实现的 DER 读取器、按 RFC 3394 独立实现的参考包装），`-Wall -Wextra -Werror -fsanitize=address,undefined`。`-I`（隔离模式）、从任意目录运行都通过。

- `test_closed_boundary.py`（`Test/boundary.cpp`）：原测试的全部断言（关闭边界 12 次拒绝、握手/记账选择子、7/14/17/35 的回答与 DER 解码、各种参数错误、启动失败与属性失败）+ 设备状态走描述符时必须先 prepare（替身对未 prepare 的写直接 abort，模拟内核 panic）+ 0–300 全部选择子分类表，`pagani-keybag=0` 时 8/10/11/31/32 必须是 `0xe00002c7`。
- `test_volatile_keybag.py`（`Test/keybag.cpp`）：FIPS-197 附录 C.1–C.3；RFC 3394 第 4.1–4.6 节六组向量（驱动实现与独立参考实现都要对上），每组每一位翻转（共 1920 个篡改块）都被拒且不留明文；用 securityd 的真实缓冲（包 40、解 32）对 10 个类 × 3 个句柄往返，输出逐字节等于“用该类密钥做 RFC 3394”，用任一其它类解包都报 `0xe007c013`；16–512 全部合法长度（内联与描述符两种输出）往返；class 12、0/5/13/14/31/32/38/-1/极值类、未知句柄、非 int32 标量、错误标量个数、各种非法长度、带外输入、少 1 字节的输出、prepare 失败、截断/加长/随机/跨开机的密文全部按预期拒绝且调用者缓冲区不被写；0x1f 三个句柄都返回 `{cf 0, mua 10}` 且无 `pdps`/`gp`，描述符 prepare/complete 成对；0x8/0x20 成功后锁状态仍是 4、A 类仍可用；`pagani-keybag=0`/`2`/`7`（越界按 1）；锁分配失败；corecrypto 未注册、上下文超大、块长不对、自检三种失败（AES 输出错、密钥扩展失败、加密失败）、随机数全零或重复——都拒绝且密钥保持全零、不调用 AES；密钥生成后 AES 出错报 `0xe00002bc`；服务释放后密钥清零、锁释放，`stop()` 后密钥清零、模式关闭、锁留到最后释放；8 线程并发首次使用（3200 次成功，`read_random` 只调用 1 次）。
- 变异检验：在临时副本里分别埋 14 个缺陷（改 IV、去 prepare、丢句柄 -6、类别输出加 1、去完整性校验、去上下文大小检查、给 akpu 发密钥、完整性失败不清零、拆掉擦除、去重复密钥检查、0x1f 不受开关控制、计数器差一、`stop` 就释放锁、权限门失效），每一个都至少让一套测试失败。
- 另外跑过：两套测试 `-O2` 无消毒器各一遍、keybag 测试 ThreadSanitizer 一遍（8 线程并发首次使用），都通过且无数据竞争报告。

## 6. 真编译（零警告）

`python3 sysfix/keybag/tools/compile-keystore.py [源文件] [输出目录]`：复刻 `build-stage7-driver.py` 对 NativeKeyStoreEvents 的做法（IOKit 编译记录 `IOPlatformExpert.cpo.json` 经 `build-arm-platform.py` 的 `compile_command`，去掉记录里所有 `-W`，再加 Stage 7 的 `-mgeneral-regs-only -Wall -Wextra -Werror -Wno-unused-parameter -Wno-c99-designator -Wno-reorder-init-list -Wno-deprecated-enum-enum-conversion`），环境与项目整核编译一致（`工具/make-a830-package.sh` 与 PROGRESS“编译前”：`CCC_OVERRIDE_OPTIONS='# +-Wno-unnecessary-virtual-specifier'`，只压 XNU 自己 `OSSymbol.h` 那条告警；不带它时连原版文件和 NativeAES 都过不了，这是工具链环境，不是本改动引入的）。输出只写到 `sysfix/keybag/build/`。

结果（`build/compile.log`）：0 错误 0 警告；arm64 MH_OBJECT、iOS 17.0；`build-stage7-display.py` 的晚注册处理（去 `__mod_init_func`、全局化静态初始化）后指令逐字节不变；234 个未定义符号在当前 `kernel/kernel.macho` 中全部存在（新增依赖：`aes_encrypt_key/aes_encrypt/aes_decrypt_key/aes_decrypt`、`g_crypto_funcs`、`cc_clear`、`read_random`、`IOLockAlloc/IOLockFree/lck_mtx_lock/unlock`、`PE_parse_boot_argn`、`vsnprintf`、`proc_selfname`、`boot_progress_trace`、`current_task`、`kOSBooleanTrue`、`IOUserClient::copyClientEntitlement`）。最终源文件 `NativeKeyStoreEvents.cpp` sha256 `91a414178438862696efa82d0aaf91506ec7e214b33216592e4c369b1337f776`。

## 7. 安全取舍（直白说）

- **数据本来就是易失的**：Data 卷是每次开机从种子新建的内存盘，关机/复位就没了。钥匙包每次开机重新随机生成类密钥，所以上一次开机包起来的东西这一次一定解不开——但也不存在“上一次的东西”。Stage7Probe 只在 `/chosen ephemeral-storage = 1` 时才注册这个服务，这一前提由它保证。
- **不能和持久 Data 一起用**：将来如果开了持久 Data（`sheng-data-persist`、PaganiPersistentData），旧开机存的钥匙串条目在新开机全部解不开，securityd 会把它们当“这台设备永远解不开”删除。届时必须换成持久、受保护的类密钥方案，不能直接沿用。
- **没有口令**：没有任何东西把类密钥和用户口令绑在一起。“锁屏”不会让任何类密钥变得不可用；需要口令的 akpu 类（12）一律拒绝。
- **没有硬件密钥**：真 iPhone 的类密钥由安全隔区里的设备唯一密钥（UID）保护，拆下闪存也解不开。这里没有安全隔区、没有 UID，类密钥只是内核内存里的 320 字节随机数。能在内核里跑代码、或能读物理内存的人（比如复位后 RAM 内容还在，ramoops 就是靠这一点）可以拿到密钥——不过同一块内存里的内存盘本身就是明文，所以密钥在这里并不多保护什么。
- **默认不查调用者身份**：真机只让带权限的进程（securityd）使用钥匙串类密钥；这里默认任何能打开 AppleKeyStore 用户客户端的进程都能包/解包。实际风险有限：要解开某个条目，先得拿到它的密文，而钥匙串数据库只有 securityd 能读。`pagani-keybag=2` 可以把钥匙串类限制给有 `com.apple.keystore.access-keychain-keys` 的进程（尚未上机验证）。
- **包装是确定性的**（RFC 3394 没有随机数）：同一把类密钥下相同明文得到相同密文。securityd 每个条目的密钥都是随机的，不受影响；真机也是 RFC 3394。
- 一句话：这是“让钥匙串在这次开机里能用”的功能，不是安全存储；它不比内存盘本身更安全，也不更不安全。

## 8. 早上上机看什么

- U 行（`grep "U  keybag:" merged.log`）：Stage 7 时一条 `keybag: pagani-keybag=1: volatile class keys at first use …`；开机约 27 s 后 `keybag: up: 10 class keys …`、`keybag: first wrap by securityd: bag 0 class N 32 -> 40 bytes`（句柄也可能是 -6）、`keybag: first unwrap by …`；之后可能有 `keybag: configuration answered …`、`keybag: lock request answered …`，以及成功满 64 次时一条计数行（形如 `keybag: 30 wraps, 34 unwraps, 0 refused`，具体数字因开机而异）。不应出现 `keybag: off …`。
- R 行（`inventory/tools/aggregate_runs.py` 的 `aks_fail`）：0xa、0xb、0x1f、0x8、0x20 应为 0；0x39、0x87 各 1 属正常。若有少量 0xa/0xb，看对应的 `keybag: refused …` U 行：class 12（akpu）或带 ACL 的条目属预期。
- 功能：设置 → 密码 → 手动添加一条网站密码应能保存并再次打开（重启后消失是设计如此）；Safari 登录时“存储密码”应成功；App 的登录令牌在本次开机内能记住。锁屏/亮屏照旧正常；锁屏后返回不需要密码。
- 仍然不行（不属本修复）：设置锁屏密码（需要 `aks_change_secret` 0xf 等一整套口令状态机，见第 10 节）；带生物识别/用户在场约束的条目；iCloud 钥匙串与备份钥匙包。

## 9. 集成

- 不需要改任何共享文件：下一次 `unixnu build kernel` 会通过构建树的符号链接自动编入新的 `NativeKeyStoreEvents.cpp`（Stage 7 显示归档），默认就开。
- 默认开而不是默认关的理由：内核侧有界、所有失败路径都回错误码不会 panic（上面列的每个守卫都有测试）；钥匙串坏掉才是现在的异常状态，打开后是 iOS 的正常路径；整个用户客户端本来就只在 `sheng-aks=1` + `offline-key-events=1` + 易失存储时才存在；启动参数只剩约 135 字节，默认开不占预算；早上测试本来就要它开着。
- 要关（回到今晚以前的行为）：`A830_EXTRA_ARGS="pagani-keybag=0"` 打包（`make-a830-layout.sh` 已支持这个变量，不用改脚本）；要试权限加固：`A830_EXTRA_ARGS="pagani-keybag=2"`。
- 建议（不是本键的文件，交给盘点脚本的主人）：`inventory/code-run-host-tests.sh` 在 `t ksevents` 那行后面加一行
  `t kskeybag    python3 -B $D/NativeKeyStoreEvents/test_volatile_keybag.py`。
- 编译环境提醒：手工编 Stage 7 对象时要带 `CCC_OVERRIDE_OPTIONS='# +-Wno-unnecessary-virtual-specifier'`（与整核编译相同），否则当前 Xcode-beta clang 会因 XNU 头文件报错，与本改动无关。

## 10. 没做的与后续

- 设锁屏密码：需要 `aks_change_secret`（0xf）、`aks_unlock_device`（0x9，现在只是无条件成功）、锁状态位、A/B 类随锁定失效、错误次数与退避，以及 SpringBoard/设置的整套流程；在易失数据上做口令只会带来把自己锁在外面的风险，没有安全收益。若要做，建议另立一项、先上机抓 Settings 设密码时的完整选择子序列。
- 带 ACL 的条目（ref key，`__aks_operation` 0x2b）、iCloud 钥匙串备份（0x81–0x84）、钥匙包 UUID（0x17）：仍是 `0xe00002c7`，securityd 会按“不可用”处理。
- `pagani-keybag=2` 的权限检查走 `IOUserClient::copyClientEntitlement` → AMFI；默认模式从不调用它，上机验证后才建议启用。

## 11. 文件

- 驱动：`UniXNU-Pagani-Drivers/drivers/NativeKeyStoreEvents/NativeKeyStoreEvents.cpp`、`README.md`、`test_closed_boundary.py`（改写为跑 `Test/boundary.cpp`）、新增 `test_volatile_keybag.py`、`Test/`（`host.h`、`refaes.h`、`boundary.cpp`、`keybag.cpp`、`mock/…`）。
- 本目录：`notes.md`（本文）、`NativeKeyStoreEvents.diff`（审查补丁）、`tools/compile-keystore.py`（真编译）、`build/`（编译产物与日志，可删）、`re/`（逆向输出与 IDAPython 脚本；复制的 securityd 和 IDA 数据库已删，需要时用 `re/ida/*.py` 在 OS 卷上重建）。
