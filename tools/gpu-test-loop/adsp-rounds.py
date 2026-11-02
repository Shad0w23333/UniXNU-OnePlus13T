#!/usr/bin/env python3
"""13T ADSP 跑批（build-userspace/a830-inputs/adsp-review.md 第 1 节）：逐轮调 gpu-loop.py，读回日志，按判据决定继续或停。
写法照 wifi-rounds.py，日志读取、序列门、桌面判据直接用它的（按路径载入，不改它）。

  adsp-rounds.py                                只打印计划：各轮的镜像、参数、字节数、分级。不碰手机
  adsp-rounds.py --replay DIR --round B4        用已有的运行目录回放某一轮的判据。不碰手机
  adsp-rounds.py --spec C3                      打印一轮的出包参数（make-adsp-rounds.sh 用）
  adsp-rounds.py --names B                      打印一段的轮次名（A、B、C、D、unattended、attended、all；make-adsp-rounds.sh 用）
  adsp-rounds.py --check-payload C3             核对这一轮镜像旁边的 .payload.json（make-adsp-rounds.sh 出包后调用）
  adsp-rounds.py --check-kernel                 核对 out-gpu 的内核带的是现在这份驱动（make-adsp-rounds.sh 出包前调用）
  adsp-rounds.py --yes [--from A1] [--to B2]    真跑。只有带 --yes 才会动手机，序列强制是 SERIAL_13T；停在第一个要人的轮次之前，只打印做法
  adsp-rounds.py --yes --attended --from B3 [--to C2]   用户在场时跑要人的轮次；不给 --to 就只跑这一轮

轮次表是文档第 1 节那张，这里是唯一的来源，make-adsp-rounds.sh 从这里取参数。分级：
  unattended  A1 A2 A3 B1 B2：不出声，不写 ADSP 的电源，不碰 SMMU，功放始终掉电，可以无人值守连跑，失败即停
  attended    B3：第一次写 ADSP 的电源票（lcx、lmx，和 GPU、触摸同一个 DRV-2）；B4：第一次起 Q6，charger PD 接管充电；C1、C2：Q6 全程跑着，
              各加一个客户端；这四轮的判据里有日志看不出的（触摸、桌面保持、插拔线）。C3 C4 C5：动 SMMU、DSP 地址；C6 C7：叫用户来听；
              D1 D2：等 audio2 路由，叫用户来听
判决三档：PASS 通过；ASK 存疑，停下等人；HARD 硬失败，立刻停。缺镜像算 HARD。

防误操作：--attended 只能从要人的轮次开始，不会从 A1 一路走进 B3；C3 和叫用户来听的轮次每次单独开跑，--to 跨不过去；严格按表的顺序（文档第 1 节），
--from 前面的每一轮在 runs/adsp-rounds-*.json 里都要有判决，而且最近一次不是 HARD，否则一个调用都不发。

和文档不一样的地方，以源码为准（文档写于 10-31）：
  - pagani-battery 现在按位解释，=1 仍是开驱动；别的值开 USB 探针或 PMIC 观察通道，这里一律不用。A830_BATTERY 不开，只有 A1、C1、D2 自己带
    pagani-battery=1：文档要 B4 没有 [battery] 行，而 make-stage16.sh 是开着的
  - C6、C7 删掉 pagani-audio：留着它，提示音之后驱动会把 adsp 后端交给 iOS（SpeakerBackend），那是 D1 的事，不是一次只加一样
  - C1 加 pagani-battery-log=2：state 行只在状态变化时才写，第一条写在 DSP 起来之前（src=gauge+inferred），src=gauge+charger 不一定有机会出现
  - B2 还写 SMEM 全局分区的 item 130、128（smem oplus sensor items），文档的“新碰的硬件”没有写
  - 整机复位时最后一行不一定是 smmu start：这一行写完马上就写寄存器，UFS 日志约 3 s 落盘一次，没有静默就落不了盘，判据看的是日志提前结束

基线是 make-stage16.sh 的 BASE，记录取它出的阶段 1.6 完整包（998 字节，tests 里从 board.toml 和 make-a830-images.sh 重新算过）。阶段 1.6 出过包但没上过机：
A1 同时也是这份内核和种子第一次上机，A1 失败先怀疑它们，不要先怀疑电池。
所有轮删 DROP_ALWAYS，最长的 C3 也放得下 1020（payload.py 的 KERNEL_ARGS_LIMIT）；文档说的余量 132 字节已过时。
"""
import argparse
import dataclasses
import hashlib
import importlib.util
import json
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
TOP = HERE.parents[1]
IMAGES = TOP / '测试镜像'
GPU_LOOP = HERE / 'gpu-loop.py'
BASELINE = IMAGES / 'boot-A830-阶段16-完整包.payload.json'
LIMIT = 1020                # payload.py 的 KERNEL_ARGS_LIMIT
CHECK_BASELINE = TOP / 'build-userspace/a830-inputs/ufslog/tools/check-baseline.sh'
TREE = TOP / 'UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree'
KERNEL = TREE.parent / 'kernel/kernel.macho'
DRIVERS = TOP / 'UniXNU-Pagani-Drivers/drivers'
SCM = TOP / 'Adreno830/platform/sm8750/Sm8750Scm.c'


def wifi_rounds():
    """wifi-rounds.py 的文件名带连字符，只能按路径载入。这里只用它，不改它的行为。"""
    module = sys.modules.get('wifi_rounds')
    if module is None:
        spec = importlib.util.spec_from_file_location('wifi_rounds', HERE / 'wifi-rounds.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules['wifi_rounds'] = module
        spec.loader.exec_module(module)
    return module


wr = wifi_rounds()
Evidence, Verdict, Runner = wr.Evidence, wr.Verdict, wr.Runner
SERIAL = wr.SERIAL          # 13T。USB 上也可能是别的手机（SERIAL_OTHER_DEVICE）：gpu-loop.py 不带序列会重启错的设备

# 和 ADSP 无关、去掉也不改变被测路径的参数，所有轮一样删，轮次之间只差本轮的参数。合计 42 字节，C3 从 1055 降到 1013。
# pagani-gpu-status 留着：B3 要看 GPU 照常，G 行和 F 行都靠它。
DROP_ALWAYS = (
    'startup_debug',            # make-stage16.sh 本来就删
    'native-ufs-diagnostics',   # 25 字节：完整包才留
    'pagani-kbtrace',           # 17 字节：键盘追踪
)
AUDIO_OFF = ('pagani-audio',)   # 只有 C6、C7 删：见文件头

# 够看到判据就行。桌面（diag 第一轮）在 45-49 s（runs/ 里三次），固件分区的重试上限 90 s，等 adsp_apps 的上限 120 s
RESET_BUS, RESET_STOP, RESET_DSP, RESET_DESK, RESET_MANUAL = 90, 120, 150, 240, 900
PANEL, SEED = 65, 's7-final'
ANDROID_WAIT, ANDROID_AFTER_RESET = 900, 300     # gpu-loop.py --android-timeout 从 fastboot boot 算起，默认 900 s：D 段定时复位就是 900 s，等不到
LAST_SNAPSHOT = 'ufslog last snapshot before the reset: '   # 后面是复位原因：timer 才是定时复位（RamoopsLog.c reset_now）


@dataclasses.dataclass(frozen=True)
class Round:
    name: str
    step: str                   # A B C D
    reset: int
    args: Tuple[str, ...]
    touches: str                # 文档的“新碰的硬件”
    human: str = ''             # 要人做什么、看什么
    drop: Tuple[str, ...] = ()
    attended: bool = False      # 要用户在场：跑批默认停在它之前
    listen: bool = False        # 要叫用户来听：日志全过也只能算存疑，听到没有要人答
    solo: bool = False          # 不跟在上一轮后面自动开始（listen 也是）：用户看过上一轮再开跑
    panel: int = PANEL
    seed: str = SEED

    @property
    def image(self) -> Path:
        return IMAGES / ('boot-A830-ADSP-%s.img' % self.name)

    @property
    def label(self) -> str:
        return 'adsp-' + self.name


def number(text: Optional[str]) -> Optional[int]:
    try:
        return int(text, 0)
    except (TypeError, ValueError):
        return None


def forbidden(words: Sequence[str]) -> List[str]:
    """今天不能开的参数（adsp-review.md 第 1 节末）。值看不懂的按不能开算。"""
    found = []
    adsp = 'pagani-adsp=1' in words
    for word in words:
        name, _, value = word.partition('=')
        amount = number(value)
        if (name == 'pagani-battery-charge'                                 # 写充电器
                or (name == 'pagani-battery' and amount != 1)               # 按位解释：别的位是 USB 探针和 PMIC 观察通道
                or (name == 'pagani-battery-pmic' and amount not in (0, 1))  # 2 写观察通道
                or (name == 'pagani-speaker-ceiling' and (amount is None or amount < 12))
                or (name == 'pagani-speaker-feedback' and amount != 0)      # 功放往数据线上驱动
                or (name == 'pagani-gpu-async' and amount != 0)             # GPU 的 SCM、QMP 会和 ADSP 重叠
                or name.startswith('pagani-audio-tone-')                    # 扫频、满幅正弦都从这几个参数来
                or (adsp and name in ('pagani-wifi', 'pagani-sensors') and amount != 0)):   # armcpu_spi_attach 不是线程安全的
            found.append(word)
    return found


def table() -> Dict[str, Round]:
    tone_note = '手机放安静的地方，贴近听：声音在 DSP 起来之后，开机约几十秒内，只有 0.5 秒'
    rounds = [
        Round('A1', 'A', RESET_BUS, ('pagani-battery=1',), 'SPMI 表只读；I2C hub 时钟（0x52008 位 7、8、11）；SE1；电量计读与一次 DEVICE_TYPE',
              '状态栏电量与安卓一致：脚本只比 pct 和回安卓后的 level，人在的话看一眼状态栏'),
        Round('A2', 'A', RESET_BUS, ('pagani-speaker=1', 'pagani-speaker-stop=4'), 'L15B 投票；SE4；gpio2、164 复位脉冲'),
        Round('A3', 'A', RESET_BUS, ('pagani-speaker=1', 'pagani-speaker-stop=5'), '功放寄存器（掉电状态）'),
        Round('B1', 'B', RESET_STOP, ('pagani-adsp=1', 'pagani-adsp-stop=3'), '只读：SMEM 表与 apps↔ADSP 分区、modem_a、AOSS、RSC'),
        Round('B2', 'B', RESET_STOP, ('pagani-adsp=1', 'pagani-adsp-stop=5'), 'SMEM 写 item 429 和全局分区 item 130、128；TCSR 互斥 3；IPCC 中断与门铃'),
        Round('B3', 'B', RESET_STOP, ('pagani-adsp=1', 'pagani-adsp-stop=7'), 'AOSS QMP；lcx/lmx 投票（DRV-2）',
              '用户在场：第一次写 ADSP 的电源票，和 GPU、触摸走同一个 DRV-2。摸一下屏幕：日志只看得出桌面起来、投票之后 GPU 在出帧', attended=True),
        Round('B4', 'B', RESET_DESK, ('pagani-adsp=1',), '全程：PAS、Q6 起机、GLINK、QRTR',
              '用户在场：第一次起 Q6，charger PD 接管充电。桌面保持 3 分钟，留意 auth 那一下卡顿：定时复位 240 s，日志只看 SpringBoard 没有重启', attended=True),
        Round('C1', 'C', RESET_DSP, ('pagani-battery=1', 'pagani-adsp=1', 'pagani-battery-log=2'), 'battmgr 通道，只读',
              '用户在场：插拔线一次，看 state changed 行；回安卓后看充电和电流前后变化', attended=True),
        Round('C2', 'C', RESET_DSP, ('pagani-adsp=1', 'pagani-speaker=1', 'pagani-speaker-stop=7'), 'adsp_apps、APM 查询',
              '用户在场看着屏幕：Q6 全程跑着，audio PD 和 APM 第一次起来；功放配过寄存器但掉电，不该出声', attended=True),
        Round('C3', 'C', RESET_DSP, ('pagani-adsp=1', 'pagani-speaker=1', 'pagani-speaker-stop=8', 'pagani-speaker-sids=1'),
              '全程最危险的一步：apps SMMU 写一个 S2CR、一个 SMR（只有 0x1001）',
              '用户在场看着屏幕。可能整机复位：复位了就停，这台机器 Gunyah 不接受音频流路由，不要再试别的银行', attended=True, solo=True),
        Round('C4', 'C', RESET_DSP, ('pagani-adsp=1', 'pagani-speaker=1', 'pagani-speaker-stop=8'), '四个音频流',
              '用户在场看着屏幕。只有后三个失败不致命，记下', attended=True),
        Round('C5', 'C', RESET_DSP, ('pagani-adsp=1', 'pagani-speaker=1', 'pagani-speaker-stop=10'), '映射 0xd0000000；建图；MEM_MAP',
              '用户在场看着屏幕。同步异常说明这 64 KiB 没映射给 HLOS', attended=True),
        Round('C6', 'C', RESET_DSP, ('pagani-adsp=1', 'pagani-speaker=1', 'pagani-audio-tone=1'), '位时钟、功放上电、声音（440 Hz、500 ms、-30 dBFS）',
              '叫用户来听：应该听到一声小声的音。' + tone_note, drop=AUDIO_OFF, attended=True, listen=True),
        Round('C7', 'C', RESET_DSP, ('pagani-adsp=1', 'pagani-speaker=1', 'pagani-audio-tone=2'), '同上，走一遍两颗功放',
              '叫用户来听：依次两边、0x34、0x35，隔 1 秒，记下哪颗是上喇叭。' + tone_note, drop=AUDIO_OFF, attended=True, listen=True),
        Round('D1', 'D', RESET_MANUAL, ('pagani-adsp=1', 'pagani-speaker=1'), 'iOS 出声，天花板 -12 dBFS',
              '等 audio2 路由：diag 的 aud 还是 none 就别跑。叫用户来听：放一段音乐，看 play stop 行', attended=True, listen=True),
        Round('D2', 'D', RESET_MANUAL, ('pagani-battery=1', 'pagani-adsp=1', 'pagani-speaker=1'), '日常组合',
              '等 audio2 路由。叫用户来听：放一段音乐，同时看电量和充电', attended=True, listen=True),
    ]
    for r in rounds:
        if forbidden(r.args):
            raise ValueError('%s 带了今天不能开的参数：%s' % (r.name, ' '.join(forbidden(r.args))))
    return {r.name: r for r in rounds}


ROUNDS = table()


def names_of(which: str) -> List[str]:
    if which == 'all':
        return list(ROUNDS)
    if which in ('unattended', 'attended'):
        return [r.name for r in ROUNDS.values() if r.attended == (which == 'attended')]
    return [r.name for r in ROUNDS.values() if r.step == which]


def baseline_args() -> List[str]:
    """make-stage16.sh 的命令行，不带 A830_BATTERY 加的 pagani-battery=1。"""
    return [w for w in json.loads(BASELINE.read_text())['kernel_args'].split() if not w.startswith('pagani-battery=')]


def command_line(r: Round, base: Sequence[str]) -> List[str]:
    """基线上去掉 drop、换定时复位和面板、加本轮参数后的命令行。"""
    gone = set(DROP_ALWAYS) | set(r.drop)
    words = [w for w in base if w.split('=')[0] not in gone]
    words = ['pagani-reset-after=%d' % r.reset if w.startswith('pagani-reset-after=') else w for w in words]
    words = ['pagani-panel=%d' % r.panel if w.startswith('pagani-panel=') else w for w in words]
    return words + list(r.args)


def size_of(r: Round, base: Sequence[str]) -> int:
    return len(' '.join(command_line(r, base)))


def turned_on(r: Round, name: str) -> bool:
    return any(a.startswith(name + '=') for a in r.args)


def argument(r: Round, name: str) -> int:
    """本轮某个参数的值，没带就是 0。"""
    return next((number(a.split('=', 1)[1]) or 0 for a in r.args if a.startswith(name + '=')), 0)


# ---- 判据 ----
def fields(text: str) -> Dict[str, str]:
    """一行日志里的 key=value。"""
    return dict(re.findall(r'(\w+)=(\S+)', text))


class Sheet:
    """一轮的判决单：行按前缀找，前缀是驱动源码里的原文（tests 逐条对过源码）。"""

    def __init__(self, e: Evidence, r: Round):
        self.e, self.r = e, r
        self.hard: List[str] = []
        self.ask: List[str] = []
        self.facts: List[str] = []
        self.advice: List[str] = []
        self.level: Optional[int] = None    # 回到安卓后 dumpsys battery 的电量

    def lines(self, prefix: str) -> List[str]:
        return [t for t in self.e.texts() if t.startswith(prefix)]

    def first(self, prefix: str) -> Dict[str, str]:
        found = self.lines(prefix)
        return fields(found[0]) if found else {}

    def last(self, prefix: str) -> str:
        found = self.lines(prefix)
        return found[-1] if found else ''

    def early(self) -> bool:
        """日志提前结束，又没有 panic：整机复位或挂死。"""
        return not self.e.texts('P') and self.e.last_time() < self.r.reset - 15

    def verdict(self) -> Verdict:
        status = 'HARD' if self.hard else 'ASK' if self.ask else 'PASS'
        return Verdict(self.r.name, status, self.hard + self.ask, self.facts, self.advice, 0, self.e.directory)


def healthy(s: Sheet) -> bool:
    """每轮都要满足：有日志、UFS 写日志正常、没有 panic、到了定时复位。"""
    e, r = s.e, s.r
    if not e.records:
        s.hard.append('没有日志（merged.log 空或不存在）')
        return False
    panic = e.texts('P')
    if panic:
        s.hard.append('panic：%s' % next((t for t in panic if 'panic' in t.lower()), panic[0]))
    u = e.ufs()
    if u is None:
        s.hard.append('summary.txt 没有 UFS 日志头：这轮的日志不是这次运行的，或者 UFS 日志没读回来')
    else:
        s.facts.append('UFS 日志 state %d（%s），%d 轮，%d 写错误，运行 %d s' % (u['state'], u['text'], u['rounds'], u['write_errors'], u['up']))
        if u['write_errors']:
            s.hard.append('UFS 写错误 %d' % u['write_errors'])
        if u['state'] != 6:
            s.hard.append('UFS 日志状态 %d（%s），不是定时复位前的最后快照：挂死、panic 或写日志停了' % (u['state'], u['text']))
        else:
            # 状态 6 只说“复位前写了快照”，不说是谁要复位的：observer 是定时器所在的 CPU 卡死了，别的原因是有人重启
            why = [t[len(LAST_SNAPSHOT):] for t in e.texts('T') if t.startswith(LAST_SNAPSHOT)]
            if why[-1:] != ['timer']:
                s.hard.append('最后的复位不是定时复位（原因 %s）：CPU 卡死或有人重启' % (why[-1] if why else '没有记录'))
    if e.last_time() < r.reset - 15:
        s.hard.append('日志在 %.0f s 结束，早于 %d s 的定时复位：%s' % (e.last_time(), r.reset, '先 panic 了' if panic else '整机复位或挂死'))
        for tag in ('[adsp] ', '[spk] ', '[battery] '):
            if s.last(tag):
                s.facts.append('最后一条 %s行：%s' % (tag, s.last(tag)))
    return True


def right_run(s: Sheet) -> None:
    """日志里的启动参数就是这一轮的，没有混进今天不能开的。"""
    seen = ' '.join(s.e.texts('A')).split()
    if not seen:
        s.hard.append('日志里没有启动参数记录：看不出是不是这一轮')
        return
    for word in s.r.args:
        if word not in seen:
            s.hard.append('这份日志的启动参数没有 %s：不是这一轮的日志，或者参数没生效' % word)
    if forbidden(seen):
        s.hard.append('启动参数里有今天不能开的：%s' % ' '.join(forbidden(seen)))


def only_asked(s: Sheet) -> None:
    """一次只加一样：这一轮没开的驱动不该有日志行。"""
    for name, tag in (('pagani-adsp', '[adsp] '), ('pagani-speaker', '[spk] '), ('pagani-battery', '[battery] ')):
        found = s.lines(tag)
        if found and not turned_on(s.r, name):
            s.hard.append('这一轮没开 %s，却有 %d 条 %s行，第一条：%s' % (name, len(found), tag, found[0]))


def desktop_stayed(s: Sheet) -> None:
    """桌面进程没有重启：exec、exit 记录和 summary.txt 的致命退出。exec、exit 走控制台环，开机时会丢（面板 65 的两次真实运行第一次 exec 都没留下），
    所以不能只数次数：桌面起来之后的 exec、任何一次 exit 都算。status=9 的被杀进不了 FATAL EXITS。"""
    up = next((t for t, kind, text in s.e.records if kind == 'U' and wr.DIAG_APPS.search(text)), None)
    for name in ('SpringBoard', 'backboardd'):
        starts = [t for t, kind, text in s.e.records if kind == 'S' and text.endswith('%s exec process signature enter' % name)]
        late = [t for t in starts if up is not None and t > up]
        if len(starts) > 1 or late:
            s.hard.append('%s 启动了 %d 次，最后一次在 %.0f s：桌面没有保持住' % (name, len(starts), starts[-1]))
        for text in s.e.texts('S'):
            if re.match(r'exit pid=\d+ %s ' % name, text):
                s.hard.append('%s 退出了（%s）：桌面没有保持住' % (name, text))
        if re.search(r'^\s+%s\s+status=' % name, s.e.summary, re.M):
            s.hard.append('%s 致命退出（summary.txt 的 FATAL EXITS）' % name)


def android(s: Sheet) -> None:
    """回到安卓后的 dumpsys battery 和复位类型（gpu-loop.py 写的 device.txt）。"""
    device = s.e.directory / 'device.txt'
    if not device.is_file():
        s.facts.append('没有 device.txt：回到安卓后的电量和充电没读到')
        return
    text = device.read_text(errors='replace')
    level, status = re.search(r'^\s+level: (\d+)', text, re.M), re.search(r'^\s+status: (\d+)', text, re.M)
    usb, reset = re.search(r'USB powered: (\w+)', text), re.search(r'Reset Type: ([^,]+),Reset Trigger: ([^,]+)', text)
    if level:
        s.level = int(level.group(1))
        s.facts.append('回到安卓：电量 %d %%，USB %s，status %s' % (s.level, usb.group(1) if usb else '?', status.group(1) if status else '?'))
    if reset:
        s.facts.append('上次复位 %s/%s' % reset.groups())
        if reset.groups() != ('HARD_RESET', 'PS_HOLD'):     # runs/ 里定时复位全是这一种：别的是 PMIC 看门狗或长按救回来的，日志的复位没有真的走完
            s.ask.append('回到安卓看到的上次复位是 %s/%s，不是定时复位的 HARD_RESET/PS_HOLD' % reset.groups())
    if usb and usb.group(1) == 'true' and status and status.group(1) not in ('2', '5'):
        s.ask.append('回到安卓 USB 接着却没在充电（dumpsys battery status %s，2 充电、5 充满）：先看充电正不正常' % status.group(1))
    powered = re.findall(r'^\s+(?:AC|USB|Wireless|Dock) powered: (\w+)', text, re.M)
    if powered and 'true' not in powered:
        s.ask.append('回到安卓 adb 走着 USB，dumpsys battery 却说没有任何供电：先看充电正不正常')


def battery(s: Sheet) -> None:
    dsp = turned_on(s.r, 'pagani-adsp')
    start = s.first('[battery] start ')
    if not start:
        s.hard.append('没有 [battery] start 行：内核没带 PaganiBattery，或者 pagani-battery=1 没生效')
        return
    if start.get('adsp') != ('1' if dsp else '0'):
        s.hard.append('[battery] start 的 adsp=%s，这一轮应该是 %d' % (start.get('adsp'), dsp))
    if start.get('charge') != '0':
        s.hard.append('[battery] start 的 charge=%s：今天不能写充电器' % start.get('charge'))
    if s.lines('[battery] dsp set'):
        s.hard.append('出现 dsp set：立刻停，这违反只读（%s）' % s.last('[battery] dsp set'))
    failed = s.lines('[battery] FAIL')
    s.hard.extend(failed)
    if failed or not s.lines('[battery] i2c up st=5 err=0'):
        if not failed:
            s.hard.append('没有 [battery] i2c up st=5 err=0 行')
        s.advice.append('总线挂了或卡死：I2C hub 没有 qup-config 互连票也许不通，A2 起全部暂停，SE4 走同一个 hub')
        return
    gauge = s.first('[battery] gauge addr=')
    if gauge.get('addr') != '0x55' or gauge.get('rc') != '0' or gauge.get('type') != '0x4231':
        s.hard.append('电量计不对，要 addr=0x55 rc=0 type=0x4231：%s' % (s.last('[battery] gauge addr=') or '没有这一行'))
    published = s.first('[battery] state published ')
    if not published:
        s.hard.append('没有 [battery] state published 行：状态栏没有电量')
    else:
        s.facts.append('电量 %s %%，%s mV' % (published.get('pct'), published.get('mv')))
        if s.level is not None and abs((number(published.get('pct')) or 0) - s.level) > 5:
            s.ask.append('XNU 读到 %s %%，回到安卓 %d %%：差得太多' % (published.get('pct'), s.level))
    if s.lines('[battery] gauge stopped answering') or s.lines('[battery] i2c clock votes dropped by another driver, not back'):
        s.ask.append('电量计半路断了：%s' % (s.last('[battery] gauge stopped answering') or s.last('[battery] i2c clock votes dropped')))
    if not dsp:
        return
    if not s.lines('[battery] dsp channel PMIC_RTR_ADSP_APPS registered'):
        s.hard.append('没有 dsp channel PMIC_RTR_ADSP_APPS registered 行：%s' % (s.last('[battery] dsp channel') or '通道没注册'))
        return
    if not s.lines('[battery] dsp channel up'):
        s.hard.append('没有 [battery] dsp channel up 行：charger PD 没开 battmgr 通道')
        return
    cycle = [fields(t) for t in s.lines('[battery] dsp cyc=') if fields(t).get('cyc') == '2']
    if not cycle:
        s.hard.append('没有 [battery] dsp cyc=2 行：battmgr 请求没走完')
        return
    s.facts.append('battmgr：layout=%s usb=%s icl=%s ans=%s err=%s to=%s' % tuple(cycle[0].get(k) for k in ('layout', 'usb', 'icl', 'ans', 'err', 'to')))
    if cycle[0].get('layout') != 'oplus':
        s.ask.append('属性布局 layout=%s，不是探测确认的 oplus：充电属性的编号不能信' % cycle[0].get('layout'))
    if not [t for t in s.lines('[battery] state ') if fields(t).get('src') == 'gauge+charger']:
        s.ask.append('没有 src=gauge+charger 的 state 行：充电器视图没用上（看 dsp cyc 行的 usb=）')
    currents = [fields(t).get('ma') for t in s.lines('[battery] gauge pass=')]
    if len(currents) > 1:
        s.facts.append('电量计电流 %s mA -> %s mA：ADSP 起来 charger PD 就接管充电' % (currents[0], currents[-1]))


# 走到第几个阶段该有哪一行（AdspCore.c 的阶段）
ADSP_WALK = (
    (1, '[adsp] toc '), (1, '[adsp] smem part '), (2, '[adsp] fw part='), (2, '[adsp] fw parsed'), (3, '[adsp] smem touch'), (3, '[adsp] survey '),
    (4, '[adsp] smp2p out'), (5, '[adsp] ipcc intid='), (6, '[adsp] qmp rc='), (6, '[adsp] qmp load_state adsp on'), (7, '[adsp] rails on '),
    (8, '[adsp] pas loaded adsp_dtb '), (9, '[adsp] pas loaded adsp '), (10, '[adsp] pas auth_and_reset pas=0x1 '), (11, '[adsp] ready wait '),
    (12, '[adsp] glink version agreed'), (13, '[adsp] channel IPCRTR up'), (13, '[adsp] qrtr hello '),
)
BUSY_US = 100000            # 一次安全调用这么久不让出 CPU：中断都路由到启动核，显示、UFS、GPU 中断被压住同样长的时间


def adsp_failed(s: Sheet) -> bool:
    """FAIL 行和运行中的坏事。有 FAIL 的话，后面的行本来就不会有。"""
    failed = s.lines('[adsp] FAIL ')
    for line in failed:
        s.hard.append(line)
        fail = fields(line)
        stage, error = number(fail.get('st')), number(fail.get('err'))
        if error == -16:
            s.advice.append('rc=-16：锁字被占，查谁占')
        if stage in (-8, -9, -10):
            s.advice.append('PAS 被拒：看 scm 行的 x0、x1。undo 行应全 0，桌面应不受影响')
        if stage == -11:
            s.advice.append('没有 ready，可能是 SoCCP 依赖。undo 行出现 kept_votes= 表示 PAS 关不掉，票故意留着，下次开机就清了')
    if failed and s.lines('[adsp] undo '):
        s.facts.append(s.last('[adsp] undo '))
    if s.lines('[adsp] fw mount failed'):
        s.advice.append('固件分区挂不上：用 pagani-adsp-part=modem_b 再试')
    parked = [t for t in s.lines('[adsp] ') if 'scm_parked=1' in t]
    if parked:
        s.hard.append('安全调用超出 20 s，会话留在 TZ（%s）' % parked[0])
        s.advice.append('scm_parked=1：auth 真的慢，或者唤醒被 GPU 抢走。此后不要再试任何安全调用，重启')
    for prefix, text in (('[adsp] DSP fatal error while running', 'DSP 运行中崩溃'), ('[adsp] glink stream broken', 'GLINK 流坏了'),
                         ('[adsp] glink: no version answer', 'GLINK 5 s 没有版本应答')):
        if s.lines(prefix):
            s.hard.append('%s：%s' % (text, s.last(prefix)))
    for line in s.lines('[adsp] pd crash report'):
        s.ask.append(line)
    return bool(failed or parked)


def adsp_stopped(s: Sheet, stop: int) -> None:
    """pagani-adsp-stop 的轮次：停在要求的阶段；到过 AOSS 和投票的，要全部撤干净。"""
    stopped = fields(s.last('[adsp] stopped as asked (pagani-adsp-stop)'))
    if stopped.get('st') != str(stop):
        s.hard.append('没有 stopped as asked st=%d 行：%s' % (stop, s.last('[adsp] ')))
    if stop < 6:
        return
    off, undo = s.first('[adsp] rails off '), s.first('[adsp] undo ')
    if off.get('rc') != '0':
        s.hard.append('rails off 不是 rc=0：%s' % (s.last('[adsp] rails off ') or '没有这一行'))
    if not undo or any(undo.get(k) != '0' for k in ('shutdown_adsp', 'shutdown_dtb', 'unvote', 'load_state_off')) or 'kept_votes' in undo:
        s.hard.append('undo 没有全 0：%s' % (s.last('[adsp] undo ') or '没有这一行'))


def adsp_running(s: Sheet) -> None:
    """走完全程的轮次：两个镜像的安全调用、ready、handover、定位器。"""
    for line in s.lines('[adsp] scm '):
        if fields(line).get('rc') != '0':
            s.hard.append('安全调用失败：%s' % line)
    for pas in ('0x24', '0x1'):
        if not s.lines('[adsp] scm auth_and_reset pas=%s ' % pas):
            s.hard.append('没有 scm auth_and_reset pas=%s 行' % pas)
    auth = s.first('[adsp] scm auth_and_reset pas=0x1 ')
    if auth:
        s.facts.append('auth_and_reset %s us，intr=%s' % (auth.get('us'), auth.get('intr')))
        if auth.get('intr') == '0' and (number(auth.get('us')) or 0) >= BUSY_US:
            s.facts.append('记下：intr=0 且 us 很大，TZ 这段时间不让出 CPU，线程在 0 号核时显示、UFS、GPU 中断被压住同样久，留意这一下卡顿')
    ready = s.first('[adsp] ready wait ')
    s.facts.append('ready 用了 %s ms，slave=%s，门铃 %s 次' % (ready.get('ms'), ready.get('slave'), ready.get('irqs')))
    if not (number(ready.get('slave')) or 0) & 2:
        s.hard.append('ready wait 的 slave 没有 ready 位：%s' % s.last('[adsp] ready wait '))
    if not s.lines('[adsp] handover: proxy votes dropped rc=0'):
        s.ask.append('没有 handover: proxy votes dropped rc=0 行：proxy 票还在（%s）' % (s.last('[adsp] handover') or '没有 handover'))
    if not s.lines('[adsp] locator ask=avs/audio domains=1 sent=1'):
        s.ask.append('没有 locator ask=avs/audio domains=1 sent=1 行：DSP 没问 audio PD，后面 C2 起不来')
    if s.lines('[adsp] ipcc interrupt attached but silent, polling'):
        s.facts.append('记下：ipcc 中断线不通，转轮询，功能可用但费电')
    if s.lines('[adsp] glink opens nobody serves:'):
        s.facts.append(s.last('[adsp] glink opens nobody serves:'))


def adsp(s: Sheet) -> None:
    stop = argument(s.r, 'pagani-adsp-stop')
    start = s.first('[adsp] start ')
    if not start:
        s.hard.append('没有 [adsp] start 行：内核没带 PaganiAdsp，或者 pagani-adsp=1 没生效')
        return
    if start.get('stop') != str(stop):
        s.hard.append('[adsp] start 的 stop=%s，这一轮是 %d' % (start.get('stop'), stop))
    if s.e.texts('P') and 'smem touch' in s.last('[adsp] '):
        s.advice.append('最后一行是 smem touch 后 panic：这页 XNU 读不了，ADSP 路线到此为止，先查分区偏移')
    if adsp_failed(s):
        return
    for stage, prefix in ADSP_WALK:
        if stage <= (stop or 13) and not s.lines(prefix):
            s.hard.append('没有 %s 行：日志断在这里，又没有 FAIL' % prefix.strip())
            return
    part = s.first('[adsp] fw part=')
    if (part.get('part'), part.get('lun'), part.get('first')) != ('modem_a', '4', '4870'):
        s.ask.append('固件分区不是 modem_a lun=4 first=4870：%s' % s.last('[adsp] fw part='))
    survey = s.first('[adsp] survey ')
    if (survey.get('items'), survey.get('slave'), survey.get('qmp'), survey.get('refused')) != ('0x0', '-1', 'LIAM', '0'):
        s.ask.append('survey 不是 items=0x0 slave=-1 qmp=LIAM refused=0：%s' % s.last('[adsp] survey '))
    if s.lines('[adsp] smem oplus sensor items'):
        s.facts.append(s.last('[adsp] smem oplus sensor items'))
    if stop == 0 or stop >= 5:
        if s.lines('[adsp] ipcc interrupt unavailable, polling'):
            s.facts.append('记下：%s，能继续' % s.last('[adsp] ipcc interrupt unavailable, polling'))
        elif s.first('[adsp] ipcc intid=').get('readback') != '0xa003':
            s.ask.append('ipcc readback 不是 0xa003：%s' % s.last('[adsp] ipcc intid='))
    if stop == 0 or stop >= 6:
        if s.first('[adsp] qmp rc=').get('reused') != '1':
            s.ask.append('qmp 不是 reused=1：GPU 打开的通道没有复用（%s）' % s.lines('[adsp] qmp rc=')[0])
    if stop == 0 or stop >= 7:
        if s.first('[adsp] rails on ').get('vlvl') != '384':
            s.ask.append('rails on 不是 vlvl=384：%s' % s.last('[adsp] rails on '))
        # 投票和 GPU 走同一个 DRV-2：投过票，GPU 还要在出帧。真实运行的第一帧在 54-58 s，固件分区重试时投票可以比它晚，所以看投票之后的帧
        voted = next(t for t, kind, text in s.e.records if kind == 'U' and text.startswith('[adsp] rails on '))
        if not [text for t, kind, text in s.e.records if kind == 'F' and t > voted and re.search(r'\| gpu [1-9]', text)]:
            s.ask.append('投票（%.0f s）之后没有 GPU 出帧（F 行的 gpu 全是 0）：投票之后 GPU 可能不照常' % voted)
    if stop:
        adsp_stopped(s, stop)
    else:
        adsp_running(s)


# 走到第几个阶段该有哪一行（SpeakerCore.h 的阶段）
SPEAKER_WALK = (
    (2, '[spk] rail ldob15 '), (3, '[spk] bus '), (4, '[spk] amp 0x34 rev='), (4, '[spk] amp 0x35 rev='), (5, '[spk] amp 0x34 config ok'),
    (5, '[spk] amp 0x35 config ok'), (6, '[spk] dsp up '), (7, '[spk] apm state=ready '), (8, '[spk] smmu start '), (8, '[spk] smmu 0x1001='),
    (9, '[spk] buffer '), (10, '[spk] graphs '),
)
SPEAKER_ADVICE = (
    ('the DSP never opened adsp_apps', 'audio PD 没起来：看 locator 与 pd crash report 行'),
    ('no TFA9873 answered', '功放不应答：先看 rail 行'),
    ('stream 0x1001 not in pass-through', '0x1001=-1：没找到 UFS 的旁路银行，没有写任何东西；别的负数看 SpeakerSmmu.h'),
    ('period buffer not mapped', '用 pagani-speaker-pa= 换到 DXE_Heap 里别的地址'),
)
BUFFER = dict(pa='0xd0000000', dsp='0x1d0000000', bytes='65536')
CEILING_DB, CEILING = '-12', '8231'     # 默认天花板：SpeakerLimit.h，今天不能低于 12 dB
AMPS = ('0x34', '0x35')
AMP_ALARMS = 0x3c           # REG_STATUS 的 OTDS、OCDS、UVDS、MANALARM（Tfa9873.h，位序 VDDS DCOCPOK OTDS OCDS UVDS MANALARM CLKS）
TONE_HZ, TONE_MS, TONE_ATTEN = '440', '500', '30'


def speaker_failed(s: Sheet) -> bool:
    failed = s.lines('[spk] FAIL ')
    for line in failed:
        s.hard.append(line)
        s.advice.extend(text for key, text in SPEAKER_ADVICE if key in line)
    absent = s.lines('[spk] amp 0x34 absent') + s.lines('[spk] amp 0x35 absent')
    mismatch = s.lines('[spk] amp 0x34 config MISMATCH') + s.lines('[spk] amp 0x35 config MISMATCH')
    broken = s.lines('[spk] amp 0x34 init failed') + s.lines('[spk] amp 0x35 init failed')
    broken += s.lines('[spk] amp 0x34 verify read failed') + s.lines('[spk] amp 0x35 verify read failed')
    s.hard.extend(absent + mismatch + broken)
    if absent and not failed:
        s.advice.append('功放不应答：先看 rail 行')
    if mismatch:
        s.advice.append('config MISMATCH：停，记下 reg/got/want')
    for prefix in ('[spk] stall: the DSP stopped answering', '[spk] play lost: the DSP channel closed', '[spk] play refused'):
        s.hard.extend(s.lines(prefix))
    return bool(failed or absent or mismatch or broken)


def speaker_values(s: Sheet, stop: int) -> None:
    """走到了的阶段，值也要对。"""
    rail, bus = s.first('[spk] rail ldob15 '), s.first('[spk] bus ')
    if rail.get('mv') != '1800' or rail.get('rc') != '0':
        s.ask.append('L15B 投票不是 mv=1800 rc=0（功放应答了，电源可能本来就开着）：%s' % s.last('[spk] rail ldob15 '))
    if bus.get('clk') != '0' or bus.get('rc') != '0':
        s.hard.append('总线不是 clk=0 rc=0：%s' % s.last('[spk] bus '))
    for amp in AMPS:
        identity = s.first('[spk] amp %s rev=' % amp)
        if identity.get('type') != 'tfa9873' or (number(identity.get('rev')) or 0) & 0xff != 0x73:
            s.hard.append('功放 %s 不是 rev=0x..73 type=tfa9873：%s' % (amp, s.last('[spk] amp %s rev=' % amp)))
        if identity.get('table') == '0':    # tfa9873_known 只看低 8 位，寄存器表只有 0x0a73 和 0x0b73：别的版本照样配置、上电，却少了这张表
            s.hard.append('功放 %s 的版本没有寄存器表（table=0）：不能往下走：%s' % (amp, s.last('[spk] amp %s rev=' % amp)))
    if stop and stop < 8:
        return
    wanted = ['0x1001'] if argument(s.r, 'pagani-speaker-sids') == 1 else ['0x1001', '0x1081', '0x1041', '0x1061']
    streams = re.findall(r'(0x[0-9a-f]+)=(ok|-\d+)/', s.last('[spk] smmu 0x1001='))
    if [sid for sid, _ in streams] != wanted:
        s.hard.append('smmu 行不是 %s：%s' % (' '.join(wanted), s.last('[spk] smmu 0x1001=')))
    for sid, result in streams:
        if result != 'ok':
            if sid == '0x1001':
                s.hard.append('流 0x1001 没进直通：%s' % s.last('[spk] smmu 0x1001='))
            else:
                s.facts.append('记下：流 %s=%s，只有后三个失败不致命' % (sid, result))
    if stop and stop < 10:
        return
    buffer = s.first('[spk] buffer ')
    if any(buffer.get(k) != v for k, v in BUFFER.items()):
        s.ask.append('buffer 行不是 pa=0xd0000000 dsp=0x1d0000000 bytes=65536：%s' % s.last('[spk] buffer '))
    if not (number(s.first('[spk] graphs ').get('handle')) or 0):
        s.hard.append('graphs 没有 handle：%s' % s.last('[spk] graphs '))


def amp_alarms(s: Sheet) -> None:
    """放音时功放自己报的过温、过流、欠压：日志全过也要有人看。"""
    for amp in AMPS:
        for line in s.lines('[spk] amp %s state=' % amp):
            if (number(fields(line).get('st')) or 0) & AMP_ALARMS:
                s.ask.append('功放 %s 报警（st 的 OTDS、OCDS、UVDS、MANALARM）：%s' % (amp, line))


def tones(s: Sheet, mode: int) -> None:
    """提示音：C6 一声，C7 三声。音量、天花板、限幅器一项都不能错。"""
    wanted = ['0x3'] if mode == 1 else ['0x3', '0x1', '0x2']
    if mode == 2 and not s.lines('[spk] tone walk:'):
        s.hard.append('没有 tone walk 行')
    played = [fields(t) for t in s.lines('[spk] tone rc=')]
    if [t.get('ch') for t in played] != wanted:
        s.hard.append('提示音的声道是 %s，这一轮应该是 %s' % (' '.join(str(t.get('ch')) for t in played) or '（没有 tone 行）', ' '.join(wanted)))
    for line in s.lines('[spk] tone rc='):
        tone = fields(line)
        if tone.get('rc') != '0':
            s.hard.append(line)
        if (tone.get('hz'), tone.get('ms'), tone.get('atten'), tone.get('ceil')) != (TONE_HZ, TONE_MS, TONE_ATTEN, CEILING):
            s.hard.append('提示音不是 440 Hz、500 ms、-30 dBFS、天花板 8231：%s' % line)
        if tone.get('amps') != '0x3' or tone.get('clks_failed') != '0':
            s.ask.append('提示音不是 amps=0x3 clks_failed=0：%s' % line)
    if not [t for t in s.lines('[spk] play start ') if fields(t).get('run') == '1' and fields(t).get('amps') == '0x3']:
        s.ask.append('没有 play start run=1 amps=0x3 行：%s' % s.last('[spk] play start '))
    stops = s.lines('[spk] play stop ')
    if len(stops) != len(wanted):
        s.ask.append('play stop 行 %d 条，这一轮应该 %d 条' % (len(stops), len(wanted)))
    for line in stops:
        stop = fields(line)
        if stop.get('lim') != '0' or stop.get('clamp') != '0':
            s.hard.append('-30 dBFS 的音不该碰到限幅器，要 lim=0 clamp=0：%s' % line)
        if any(stop.get(k) != '0' for k in ('werr', 'sendfail', 'stalls')):
            s.ask.append('播放有错，要 werr=0 sendfail=0 stalls=0：%s' % line)
    for amp in AMPS:
        states = [fields(t) for t in s.lines('[spk] amp %s state=' % amp)]
        if not states:
            s.ask.append('没有 amp %s state= 行' % amp)
        elif any((t.get('state'), t.get('clks'), t.get('plls')) != ('9', '1', '1') for t in states):
            s.ask.append('功放 %s 不是 state=9 clks=1 plls=1：%s' % (amp, s.last('[spk] amp %s state=' % amp)))
            if any(t.get('clks') != '1' for t in states):
                s.advice.append('clks=0：加 pagani-speaker-prm-port=7 试')
    if s.lines('[spk] backend adsp handed'):
        s.hard.append('后端交给 iOS 了：这一轮只放内核的提示音，应该没有 pagani-audio')
    elif not s.lines('[spk] pagani-audio is not set'):
        s.ask.append('没有 pagani-audio is not set 行：%s' % s.last('[spk] '))
    if not s.hard and not s.ask:
        s.advice.append('日志全过却没声：下一张加 pagani-speaker-sd=2（只在 feedback=0 时）')


def speaker(s: Sheet) -> None:
    r = s.r
    stop, tone = argument(r, 'pagani-speaker-stop'), argument(r, 'pagani-audio-tone')
    start = s.first('[spk] start ')
    if not start:
        s.hard.append('没有 [spk] start 行：内核没带 PaganiSpeaker，或者 pagani-speaker=1 没生效')
        return
    if start.get('feedback') != '0':
        s.hard.append('[spk] start 的 feedback=%s：今天不能让功放往数据线上驱动' % start.get('feedback'))
    if start.get('ceil_db') != CEILING_DB or start.get('ceil') != CEILING:
        s.hard.append('天花板不是 ceil_db=-12 ceil=8231：%s' % s.last('[spk] start '))
    if start.get('stop') != str(stop):
        s.hard.append('[spk] start 的 stop=%s，这一轮是 %d' % (start.get('stop'), stop))
    if number(start.get('sids')) != (argument(r, 'pagani-speaker-sids') or 0xf):
        s.hard.append('[spk] start 的 sids=%s，这一轮不是这个' % start.get('sids'))
    if s.early() and (stop == 0 or stop >= 8) and not s.lines('[spk] smmu 0x1001='):
        s.advice.append('整机复位，SMMU 的结果没有落盘（smmu start 那一行多半也来不及，最后一行可能更早）。C2 走过同样的路：这就是写 SMMU 这一步，'
                        '这台机器 Gunyah 不接受音频流路由，停，不要再试别的银行')
    if s.e.texts('P') and s.lines('[spk] smmu 0x1001=') and not s.lines('[spk] buffer '):
        s.advice.append('清 0xd0000000 时 panic，多半是同步异常：这 64 KiB 没映射给 HLOS，用 pagani-speaker-pa= 换到 DXE_Heap 里别的地址')
    if speaker_failed(s):
        return
    for stage, prefix in SPEAKER_WALK:
        if stage <= (stop or 10) and not s.lines(prefix):
            s.hard.append('没有 %s 行：日志断在这里，又没有 FAIL' % prefix.strip())
            return
    speaker_values(s, stop)
    if stop:
        if fields(s.last('[spk] stopped as asked (pagani-speaker-stop)')).get('st') != str(stop):
            s.hard.append('没有 stopped as asked st=%d 行：%s' % (stop, s.last('[spk] ')))
        return
    amp_alarms(s)
    if tone:
        tones(s, tone)
    else:
        if not s.lines('[spk] backend adsp handed to the PaganiAudio service'):
            s.hard.append('后端没交给 PaganiAudio：%s' % s.last('[spk] '))
        for line in s.lines('[spk] play stop '):
            s.facts.append(line)
            if fields(line).get('clamp') != '0':
                s.hard.append('天花板之上还有样本，要 clamp=0：%s' % line)


def judge(e: Evidence, r: Round) -> Verdict:
    s = Sheet(e, r)
    if healthy(s):
        right_run(s)
        only_asked(s)
        wr.apps_up(e, s.hard, s.facts)
        desktop_stayed(s)
        android(s)
        if turned_on(r, 'pagani-battery'):
            battery(s)
        if turned_on(r, 'pagani-adsp'):
            adsp(s)
        if turned_on(r, 'pagani-speaker'):
            speaker(s)
        if r.listen and not s.hard and not s.ask:
            s.ask.append('日志全过，声音要人答。%s' % r.human)
        if r.human and not r.listen:
            s.facts.append('日志看不出、要人看的：%s' % r.human)
    return s.verdict()


TITLE = {'PASS': '通过', 'ASK': '存疑，停下等人', 'HARD': '失败，立刻停'}


def show(v: Verdict) -> None:
    print('== %s：%s' % (v.name, TITLE[v.status]))
    if v.directory:
        print('   证据 %s' % v.directory)
    for text in v.facts:
        print('   事实 %s' % text)
    for text in v.reasons:
        print('   问题 %s' % text)
    for text in v.next_step:
        print('   下一步 %s' % text)


# ---- 跑 ----
def one_round(r: Round, run: Runner) -> Verdict:
    command = [sys.executable, str(GPU_LOOP), '--image', str(r.image), '--label', r.label, '--serial', SERIAL, '--yes',
               '--android-timeout', str(max(ANDROID_WAIT, r.reset + ANDROID_AFTER_RESET))]
    print('---- %s：%s' % (r.name, ' '.join(shlex.quote(c) for c in command)), flush=True)
    status, output = run(command)
    found = re.findall(r'^evidence: (.+)$', output, re.M)
    if not found:
        return Verdict(r.name, 'HARD', ['gpu-loop.py 退出 %d，没有证据目录' % status], [], [
            '先看手机屏幕：安卓可能已起来但 USB 没枚举，插拔线；再用 read-ramoops.py 读日志，不要强制重启'])
    verdict = judge(Evidence.read(Path(found[-1])), r)
    if status:
        verdict.reasons.append('gpu-loop.py 退出 %d' % status)
        if verdict.status == 'PASS':
            verdict.status = 'ASK'
    if "the log's boot-args differ from the image's" in output:     # 日志里的启动参数和这张镜像逐项不同：不是这次运行的日志
        verdict.reasons.insert(0, 'gpu-loop.py 说日志的启动参数和镜像不一样：这份日志不是这次运行的')
        verdict.status = 'HARD'
    return verdict


def payload_problems(r: Round, limit: int) -> Tuple[List[str], int]:
    """镜像旁边的 payload 记录和这一轮的命令行逐项一样：多一项少一项都不是单变量。"""
    try:
        record = json.loads(r.image.with_suffix('.payload.json').read_text())
        words = record['kernel_args'].split()
        want = command_line(r, baseline_args())
        built = hashlib.sha256(KERNEL.read_bytes()).hexdigest()
    except (OSError, ValueError, KeyError, TypeError) as error:
        return ['payload 记录读不了：%s' % error], 0
    problems = ['缺 %s' % w for w in want if w not in words]
    problems += ['多 %s' % w for w in words if w not in want]
    problems += ['今天不能开 %s' % w for w in forbidden(words)]
    if record.get('kernel_sha256') != built:    # --check-kernel 只在出包时看：出包之后内核重编过，这张镜像带的还是旧驱动
        problems.append('镜像的内核不是 out-gpu 现在这份（%s）：重新出包' % built[:16])
    size = len(' '.join(words))
    if size > limit:
        problems.append('%d 字节，超过 %d' % (size, limit))
    return problems, size


def image_ready(r: Round, limit: int = LIMIT) -> bool:
    """镜像在，旁边的 payload 记录也对得上这一轮：gpu-loop.py 从那里读启动参数，出包后手动换过镜像的话在这里拦住。"""
    return r.image.is_file() and r.image.with_suffix('.payload.json').is_file() and not payload_problems(r, limit)[0]


def batch(run: Runner, first: str = 'A1', last: Optional[str] = None, attended: bool = False,
          images: Callable[[Round], bool] = image_ready) -> List[Verdict]:
    """从 first 按表走，失败或存疑就停。要人的轮次：不带 attended 停在它之前；带了也只跑到 last，没给 last 就只跑一轮。
    C3 和叫用户来听的轮次单独开跑：上一轮刚结束，用户未必准备好，声音只有 0.5 秒。"""
    done: List[Verdict] = []
    names = list(ROUNDS)
    for name in names[names.index(first):]:
        r = ROUNDS[name]
        if r.attended and not attended:
            break
        if (r.listen or r.solo) and done:
            break
        if not images(r):
            # 算一轮失败：结论要说停在这里，退出码也不能是 0，无人值守时才看得出没跑完
            text = '没有可用的镜像 %s：先跑 make-adsp-rounds.sh %s' % (r.image, name)
            print(text, file=sys.stderr)
            done.append(Verdict(name, 'HARD', [text], [], []))
            break
        if r.attended:
            print('>>>> %s：%s' % ('叫用户来听' if r.listen else '用户在场', r.human), flush=True)
        verdict = one_round(r, run)
        show(verdict)
        done.append(verdict)
        if verdict.status != 'PASS' or name == last or (r.attended and last is None):
            break
    return done


def out_of_order(first: str) -> Optional[str]:
    """文档第 1 节：严格按顺序，不跳步。first 前面的每一轮要有判决，最近一次不是 HARD；ASK 是用户看过才往下走的。报告坏了当没跑过。"""
    latest: Dict[str, str] = {}
    for path in sorted((HERE / 'runs').glob('adsp-rounds-*.json')):
        try:
            latest.update((v['name'], v['status']) for v in json.loads(path.read_text()))
        except (OSError, ValueError, KeyError, TypeError) as error:
            print('跳过读不了的报告 %s：%s' % (path, error), file=sys.stderr)
    for name in ROUNDS:
        if name == first:
            break
        if name not in latest:
            return '%s 没有判决（runs/adsp-rounds-*.json）：不跳步，先跑 --from %s' % (name, name)
        if latest[name] == 'HARD':
            return '%s 最近一次判 HARD：不跳步，先看日志，重跑它通过以后才能往下走' % name
    return None


def up_next(done: Sequence[Verdict], first: str) -> Optional[Round]:
    """跑完 done 之后轮到谁：一轮都没跑就是 first，停在问题上就没有下一轮。"""
    if not done:
        return ROUNDS[first]
    if done[-1].status != 'PASS':
        return None
    names = list(ROUNDS)
    index = names.index(done[-1].name) + 1
    return ROUNDS[names[index]] if index < len(names) else None


def brief(r: Round) -> None:
    """要人的轮次：只打印做法，不跑。"""
    print('==== 下一轮 %s 要用户在场，脚本不替用户开始' % r.name)
    print('  新碰的硬件：%s' % r.touches)
    print('  要用户做的：%s' % r.human)
    print('  本轮参数：%s' % ' '.join(r.args))
    print('  出包：bash build-userspace/a830-inputs/fullrun/make-adsp-rounds.sh %s' % r.name)
    print('  开跑：python3 工具/gpu-test-loop/adsp-rounds.py --yes --attended --from %s' % r.name)


def conclude(done: Sequence[Verdict], first: str = 'A1') -> int:
    print('==== 结论')
    for v in done:
        print('  %-3s %-4s %s' % (v.name, v.status, v.directory or ''))
    coming = up_next(done, first)
    if not done:
        print('  一轮都没跑')
        if coming and coming.attended:
            brief(coming)
        return 2
    print('  回到安卓后跑基线核对（只读）：ANDROID_SERIAL=%s bash %s' % (SERIAL, shlex.quote(str(CHECK_BASELINE))))
    last = done[-1]
    if last.status == 'HARD':
        print('  停在 %s：%s' % (last.name, last.reasons[0] if last.reasons else '见上'))
        return 1
    if last.status == 'ASK':
        print('  %s 存疑，停下等人：%s' % (last.name, last.reasons[0] if last.reasons else '见上'))
        return 1
    print('  %s 通过，到这里按要求停' % last.name)
    if coming and coming.attended:
        brief(coming)
    return 0


def plan(limit: int) -> int:
    base = baseline_args()
    print('基线 %s，不带 pagani-battery=1 是 %d 字节；上限按 %d 算' % (BASELINE.name, len(' '.join(base)), limit))
    print('所有轮删掉：%s；C6、C7 再删：%s' % (' '.join(DROP_ALWAYS), ' '.join(AUDIO_OFF)))
    worst = 0
    for r in ROUNDS.values():
        size = size_of(r, base)
        worst = max(worst, size)
        print('%-3s %-10s %4d 字节  复位 %3d s  %s%s' % (r.name, 'attended' if r.attended else 'unattended', size, r.reset, ' '.join(r.args),
                                                     '' if r.image.is_file() else '  （镜像还没有）'))
    kernel = kernel_problems()
    print('out-gpu 的内核：%s' % ('带的是现在这份驱动' if not kernel else '；'.join(kernel)))
    print('序列强制 %s。这是计划，什么都没做；加 --yes 才会重启手机。' % SERIAL)
    if worst > limit:
        print('超过上限：最长 %d 字节' % worst, file=sys.stderr)
        return 1
    return 0


def spec(name: str) -> int:
    """make-adsp-rounds.sh 读这几行。"""
    r = ROUNDS.get(name)
    if r is None:
        print('没有这一轮：%s' % name, file=sys.stderr)
        return 2
    print('image=%s' % r.image.name)
    print('reset=%d' % r.reset)
    print('panel=%d' % r.panel)
    print('seed=%s' % r.seed)
    print('drop=%s' % ' '.join(DROP_ALWAYS + r.drop))
    print('args=%s' % ' '.join(r.args))
    return 0


def check_payload(name: str, limit: int) -> int:
    r = ROUNDS[name]
    problems, size = payload_problems(r, limit)
    for problem in problems:
        print('%s: %s' % (r.image.name, problem), file=sys.stderr)
    print('%s: %d 字节%s' % (r.image.name, size, '，有问题' if problems else ''))
    return 1 if problems else 0


def kernel_problems() -> List[str]:
    """文档开头那句“内核不含这些修复”：各组件 build-report.json 的源码哈希对照工作副本，再看 SCM 的共享唤醒。"""
    if not KERNEL.is_file():
        return ['没有内核 %s' % KERNEL]
    problems = []
    for build, driver in (('pagani-adsp', 'PaganiAdsp'), ('pagani-speaker', 'PaganiSpeaker'), ('pagani-battery', 'PaganiBattery')):
        report = TREE / 'build' / build / 'build-report.json'
        try:
            sources = json.loads(report.read_text())['source_sha256']
        except (OSError, ValueError, KeyError) as error:
            problems.append('读不了 %s：%s' % (report, error))
            continue
        if KERNEL.stat().st_mtime < report.stat().st_mtime:
            problems.append('内核比 %s 的构建报告旧：组件编过，内核没重链' % build)
        for name, value in sources.items():
            path = DRIVERS / driver / name
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != value:
                problems.append('%s/%s 改过，内核里还是旧的' % (driver, name))
    try:
        built = json.loads((TREE / 'build/adreno830/build-report.json').read_text())['source_sha256'].get(str(SCM))
        source = SCM.read_bytes()
    except (OSError, ValueError, KeyError) as error:
        return problems + ['读不了 SCM 的构建记录：%s' % error]
    if b'all_wakes' not in source:
        problems.append('Sm8750Scm.c 没打共享唤醒（Sm8750Scm-shared-wakes.diff）：上 B4 之前要有')
    elif built != hashlib.sha256(source).hexdigest():
        problems.append('Sm8750Scm.c 改过，内核里还是旧的')
    return problems


def check_kernel() -> int:
    problems = kernel_problems()
    for problem in problems:
        print(problem, file=sys.stderr)
    print('out-gpu 的内核%s' % ('要重编' if problems else '带的是现在这份驱动'))
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--yes', action='store_true', help='真跑：重启手机')
    parser.add_argument('--attended', action='store_true', help='用户在场：可以跑要人的轮次')
    parser.add_argument('--from', dest='first', default='A1', help='从这一轮开始')
    parser.add_argument('--to', dest='last', help='跑完这一轮就停')
    parser.add_argument('--replay', type=Path, help='回放：用这个运行目录判 --round 这一轮，不碰手机')
    parser.add_argument('--round', help='--replay 判哪一轮')
    parser.add_argument('--spec', help='打印一轮的出包参数')
    parser.add_argument('--names', help='打印一段的轮次名')
    parser.add_argument('--check-payload', help='核对一轮镜像的 payload 记录')
    parser.add_argument('--check-kernel', action='store_true', help='核对 out-gpu 的内核带的是现在这份驱动')
    parser.add_argument('--limit', type=int, default=LIMIT, help='命令行字节上限（默认 %(default)s）')
    args = parser.parse_args()
    if args.spec:
        return spec(args.spec)
    if args.names:
        names = names_of(args.names)
        if not names:
            print('没有这一段：%s' % args.names, file=sys.stderr)
            return 2
        print(' '.join(names))
        return 0
    if args.check_payload:
        return check_payload(args.check_payload, args.limit)
    if args.check_kernel:
        return check_kernel()
    if args.replay:
        r = ROUNDS.get(args.round or '')
        if r is None:
            parser.error('--replay 要带 --round')
        verdict = judge(Evidence.read(args.replay), r)
        show(verdict)
        return 0 if verdict.status == 'PASS' else 1
    if not args.yes:
        return plan(args.limit)
    names = list(ROUNDS)
    if args.first not in ROUNDS or (args.last and args.last not in ROUNDS):
        parser.error('没有这一轮')
    if args.last and names.index(args.last) < names.index(args.first):
        parser.error('--to 在 --from 前面')
    if args.attended and not ROUNDS[args.first].attended:
        parser.error('--attended 只从要人的轮次开始（%s 不是）：无人值守的轮次不带它跑，停下以后用户到场再开始' % args.first)
    blocked = out_of_order(args.first)
    if blocked:
        print(blocked, file=sys.stderr)
        return 2
    if not wr.serial_attached(wr.run_streaming):
        print('adb 和 fastboot 都看不到 %s：手机没接回来，什么都没做。USB 上别的手机不算数。' % SERIAL, file=sys.stderr)
        return 2
    started = time.time()
    done = batch(wr.run_streaming, args.first, args.last, args.attended, lambda r: image_ready(r, args.limit))
    status = conclude(done, args.first)
    report = HERE / 'runs' / ('adsp-rounds-%s.json' % time.strftime('%Y%m%d-%H%M%S'))
    report.write_text(json.dumps([dict(dataclasses.asdict(v), directory=str(v.directory)) for v in done], ensure_ascii=False, indent=1) + '\n')
    print('用时 %.0f 分钟；判决写在 %s' % ((time.time() - started) / 60, report))
    return status


if __name__ == '__main__':
    sys.exit(main())
