# ui-rest 集成步骤

全部改动不需要重编内核、不改种子、不改信任包。内核 `f38f9e4b` 已含 `sheng-no-lc` 开关（`zzyc-owner-skip-launch-constraints.patch`）和文本修补机制（`native-text-fixup.patch`）。没有动任何共享文件，也没有上机、没有提交。

## 1. 启动参数（必做：修 WebKit）

打包时在现有的 `A830_EXTRA_ARGS` 里追加 `sheng-no-lc=1`：

```sh
A830_EXTRA_ARGS="<原有参数> sheng-no-lc=1" 工具/make-a830-layout.sh phone-island-apps ...
```

当前镜像命令行 981 字节，加上后 995 字节，上限 1000。其他工作流再加参数时可能超限；现在的命令行里 `pagani-gpu-status=2` 和 `pagani-gpu-status=1` 重复（后者生效），去掉前者能省 20 字节。

## 2. 文本修补（建议做：修 HEIC）

先给布局脚本加 `A830_EXTRA_FIXUPS` 开关。这个 diff 默认不改变任何行为，并给 `工具/test-phone-island-layout.py` 加了一项测试：

```sh
cd /Users/xiaochoumao/unixnu-13t
patch -p1 --dry-run < build-userspace/a830-inputs/ui-rest/integrate/01-layout-extra-fixups.diff
patch -p1 < build-userspace/a830-inputs/ui-rest/integrate/01-layout-extra-fixups.diff
python3 -B 工具/test-phone-island-layout.py      # 副本上实测 35 项全过
```

然后打包时带上：

```sh
A830_EXTRA_FIXUPS="$(tr '\n' ' ' < build-userspace/a830-inputs/ui-rest/integrate/vt-fixups.txt)" \
A830_EXTRA_ARGS="<原有参数> sheng-no-lc=1" 工具/make-a830-layout.sh phone-island-apps ...
```

`vt-fixups.txt` 的两条（只在字节完全相符时生效，内核逻辑见 `native-text-fixup.patch`）：

| 作用 | 符号与 VA | 文件,偏移 | 原字节 | 新字节 |
| --- | --- | --- | --- | --- |
| 所有进程在进程内解码，不走 `videocodecd` | `_VTShouldRunVideoDecodersInProcess` 0x197ae6900 | `dyld_shared_cache_arm64.07`,`321e900` | `fd7bbfa9fd030091` | `20008052c0035fd6`（`mov w0,#1; ret`） |
| “强制硬件”时也接受软件解码器 | `_VTSelectAndCreateVideoDecoderInstanceInternal+0x360` 0x19789ef58 | `dyld_shared_cache_arm64.07`,`2fd6f58` | `f4630139` | `ff630139`（`strb wzr,[sp,#0x58]`） |

想回退时去掉 `A830_EXTRA_FIXUPS` 即可，两条互相独立。

## 3. 打完包的主机核对

```sh
python3 -B build-userspace/a830-inputs/ui-rest/test-ui-rest.py -v      # 9 项，全部对着原始输入核对
```

再确认 `UniXNU/out-gpu/.../payload/payload.json` 的 `kernel_args` 含 `sheng-no-lc=1`，且镜像的 `/chosen/text-fixups` 含上面两条（`board.toml` 在打包时临时改写，结束后会自动恢复）。

## 4. 上机时看什么（用户说可以测的时候）

| 操作 | 期望 | 说明 |
| --- | --- | --- |
| 打开 Safari | 日志出现 `com.apple.WebKit.Web exec process signature accepted`（名字截断 20 字符），不再有 `com.apple.WebKit status=9 ns=0` | 这是第一部分的验收标准 |
| Safari 起始页 > 编辑 > 背景图片 | 9 张都显示：8 张 HEIC 加 1 张 PNG（`Dogface-Butterfly`） | PNG 是现成的对照组。PNG 显示而 HEIC 不显示，说明 HEIC 仍失败 |
| 打开“天气” | 背景与图标资源（`WeatherUI.framework/Mica/*.heic`）正常 | 第二个 HEIC 样本 |
| 打开“照片” | 网格正常 | 依赖 Metal（`@apps` 已覆盖），不依赖 HEIC |
| 在“文件”里预览 HEIC | 能预览 | QuickLook 传 `UseHardwareAcceleration=true`，只有第二条修补能让它通过 |
| 设置里点选项 | 记下卡顿的具体页面 | 设置页慢的根因尚未定位 |

若 WebKit 能启动但 GPU 进程反复崩溃，下一步是把种子 `NativeAdrenoMetal.clients` 末尾的 `!com.apple.WebKit.*` 去掉，给它 Metal 设备。这个 token 是 2026-10-30 为节省 GPU 槽位加的，不是这次的问题，本次不动。
