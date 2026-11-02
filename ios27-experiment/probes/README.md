# PAC 探针

两个探针回答同一组问题：Oryon 在 Gunyah 下是否满足 arm64e 配置的要求。都没有在手机上运行过。

| 探针 | 运行位置 | 对手机的影响 | 能回答 | 回答不了 |
| --- | --- | --- | --- | --- |
| `pac-el0/pac-el0` | 安卓 EL0 | 要把一个文件放到 `/data/local/tmp`，运行不重启 | 内核净化后的 ID 字段（APA、API、GPA、GPI、APA3、GPA3）、逐核 PAC 往返、失败时有没有 FPAC 行为 | EL1 写密钥（那是内核的事） |
| `pac-el1/pagani_pac_probe.c` | 17.7.11 的 XNU，EL1 | 要编进内核，随主线下一包启动，`pagani-pac-probe=1` 才运行 | Gunyah 过滤后的 ID 寄存器、五组密钥写入、逐核 PAC 往返 | 失败认证（EL1 上有 FPAC 时会 panic，故意不测） |

重新编译 EL0 探针：

```bash
~/Library/Android/sdk/ndk/28.2.13676358/toolchains/llvm/prebuilt/darwin-x86_64/bin/aarch64-linux-android34-clang -static -O2 -Wall -Wextra -Werror pac-el0/pac-el0.c -o pac-el0/pac-el0
```

用户同意后才运行，运行完删除。这台电脑上还会连着别的安卓设备，命令必须带序列号：

```bash
adb -s SERIAL_13T push pac-el0/pac-el0 /data/local/tmp/pac-el0
```

```bash
adb -s SERIAL_13T shell /data/local/tmp/pac-el0
```

```bash
adb -s SERIAL_13T shell rm /data/local/tmp/pac-el0
```

判读：每核四种密钥都显示“通过”，pacga 通过。`API` 或 `APA` 大于等于 4 表示带 FPAC，`APA=API=0` 且 `APA3` 非零表示 QARMA3。算法不影响 arm64e：签名都在本机生成。
