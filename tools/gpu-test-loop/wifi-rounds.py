#!/usr/bin/env python3
"""13T Wi-Fi 跑批（build-userspace/a830-inputs/round-1101/wifi.md 第 5 节）：逐轮调 gpu-loop.py，读回日志，按判据决定继续或停。

  wifi-rounds.py                         只打印计划：各轮的镜像、参数、字节数。不碰手机
  wifi-rounds.py --replay DIR --round A  用已有的运行目录回放某一轮的判据。不碰手机（--round D 判设置页走查）
  wifi-rounds.py --spec C1               打印一轮的出包参数（make-wifi-rounds.sh 用）
  wifi-rounds.py --check-payload C1      核对这一轮镜像旁边的 .payload.json（make-wifi-rounds.sh 出包后调用）
  wifi-rounds.py --yes [--from C1] [--to C2]   真跑。只有带 --yes 才会动手机，序列强制是 SERIAL_13T

轮次走报告的合并方案，这张表是唯一的来源，make-wifi-rounds.sh 从这里取参数：
  A   pagani-wifi=2 stop=204：七路电源，不放 WLAN_EN。这是 S7I（UFS vccq 的上级）的风险测试
  B   pagani-wifi=5：一路到任务模式，协议栈不启动（B2 同样，加协议栈做的那些芯片写入：不在默认顺序里，C 起不来时单独跑）
  C1  pagani-wifi=6，不校准；C2 校准加主机重启
  C3  C1、C2 里走得远的那一轮，加 PDC 和 QDSS；C4 再加 features=0x46；C5 走得最远的一轮加 m3=0
A、B 失败立刻停。C 的矩阵：拿到真实 IP 就结束；变体没走通但系统健康就试下一个；panic、挂死、UFS 写错、
S7I 掉电立刻停。D 轮（设置页）要人操作，这里只打印做法：要手输密码，所以用带打字修复的 s7-final；要人用 900 s，所以面板是完整包的 65
（1217 的第 10 位 PaganiPanel/README.md 写明不进有人用的包，P2、P3 在 306 s、346 s 跑，P3 没上过机）。A、B、C 在 264 s 之前复位，到不了 P0。

持写者（WifiBringup.h）：上电和 PCIe 的每一段高风险访问前打一行 [wifi] guard <段名>，等它落盘后持住 UFS 写者，这一段走完放开。判据不变，
只是这些段把时间往后推：A 轮投票在 6.6 s 左右，B、C 轮 2.5 s 一段（带 pagani-ufslog-ms=1000），B 共 9 段，C 共 8 段，D 轮 4.5 s 一段。
挂死时写者不放开，最后一条 [wifi] 行就是那一段的 guard，HANG_BISECT 不用从头二分。

启动参数：基线是阶段 1.5 首测的 996 字节，所有轮删掉 DROP_ALWAYS，C3 起再删 DROP_C3/DROP_C4，C5 的最长组合再删 DROP_C5。
上限先按 1020 算（LIMIT，由 memory-boot 放宽 payload.py；现在 payload.py 还是 1000，C1 的 1010 字节会被它拒绝）。
"""
import argparse
import dataclasses
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
TOP = HERE.parents[1]
IMAGES = TOP / '测试镜像'
GPU_LOOP = HERE / 'gpu-loop.py'
BASELINE = IMAGES / 'boot-A830-阶段15-首测.payload.json'
SERIAL = 'SERIAL_13T'         # 13T。USB 上也可能是别的手机（OPD2513，SERIAL_OTHER_DEVICE）：gpu-loop.py 不带序列会重启错的设备
LIMIT = 1020                # memory-boot 放宽后的上限

# 和 Wi-Fi 无关、去掉也不改变被测路径的参数。每项写明省多少字节（含一个空格）。
# sheng-gpu-metal-opens=0 不能删（默认 40，语义不同），pagani-gpu-profile=0 也不能删（默认 1）。
DROP_ALWAYS = (
    'startup_debug',            # make-stage15.sh 本来就删
    'native-ufs-diagnostics',   # 同上（完整包才留）
    'pagani-input-script',      # 22 字节：键盘脚本，只为键盘卡顿测试
    'pagani-kbtrace',           # 17 字节：键盘追踪
    'pagani-gpu-status',        # 20 字节：GPU 状态行
)
DROP_C3 = ('pagani-audio-srzl', 'pagani-haptics-ios')   # 20 + 21 字节
DROP_C4 = ('pagani-haptics',)                           # 17 字节
DROP_C5 = ('pagani-battery',)                           # 18 字节：只有 C5 接在 C4-cbc0 之后才需要；电量显示与 Wi-Fi 无关

RESET_SHORT, RESET_STACK, RESET_MANUAL = 150, 240, 900
SEED_PLAIN, SEED_WIFI, SEED_TYPING = 's6-final', 's7-wifi', 's7-final'
PANEL_ROUNDS, PANEL_MANUAL = 1217, 65       # 996 字节基线的值；完整包的值
LOG_ARGS = ('pagani-wifi-log=15', 'pagani-ufslog-ms=1000')  # 调试行进启动日志，固件崩溃时整份转储进导出区，日志每秒落盘


@dataclasses.dataclass(frozen=True)
class Round:
    name: str
    step: str                   # A B C D
    reset: int
    seed: str
    args: Tuple[str, ...]
    drop: Tuple[str, ...] = ()
    panel: int = PANEL_ROUNDS

    @property
    def image(self) -> Path:
        return IMAGES / ('boot-A830-WiFi-%s.img' % self.name)

    @property
    def label(self) -> str:
        return 'wifi-' + self.name


def stack(name: str, *args: str, drop: Tuple[str, ...] = ()) -> Round:
    return Round(name, 'C', RESET_STACK, SEED_WIFI, ('pagani-wifi=6',) + args + LOG_ARGS, drop)


def table() -> Dict[str, Round]:
    rounds = [Round('A', 'A', RESET_SHORT, SEED_PLAIN, ('pagani-wifi=2', 'pagani-wifi-stop=204')),
              Round('B', 'B', RESET_SHORT, SEED_PLAIN, ('pagani-wifi=5', 'pagani-ufslog-ms=1000')),
              Round('B2', 'B', RESET_SHORT, SEED_PLAIN, ('pagani-wifi=5', 'pagani-wifi-chipreset=0x7d', 'pagani-ufslog-ms=1000')),
              stack('C1', 'pagani-wifi-cbc=0'),
              stack('C2')]
    for tag, cbc in (('cbc0', ('pagani-wifi-cbc=0',)), ('cbc1', ())):
        c3 = cbc + ('pagani-wifi-pdc=1', 'pagani-wifi-qdss=1')
        rounds.append(stack('C3-' + tag, *c3, drop=DROP_C3))
        rounds.append(stack('C4-' + tag, *c3, 'pagani-wifi-features=0x46', drop=DROP_C3 + DROP_C4))
    for base in list(rounds[3:]):
        extra = base.args[1:-len(LOG_ARGS)] + ('pagani-wifi-m3=0',)
        rounds.append(stack('C5-' + base.name, *extra, drop=base.drop + DROP_C5))
    return {r.name: r for r in rounds}


ROUNDS = table()


def manual_round(winner: Sequence[str]) -> Round:
    """D 轮：设置页，不带 join.txt，人操作。参数是矩阵里胜出的开关，删项跟胜出的那一轮：它的开关只有删了这些才放得下。"""
    same = next((r for r in ROUNDS.values() if winner_args(r.name) == list(winner)), None)
    return Round('D', 'D', RESET_MANUAL, SEED_TYPING, ('pagani-wifi=6', 'pagani-wifi-aj=0') + tuple(winner), same.drop if same else (), PANEL_MANUAL)


def baseline_args() -> List[str]:
    return json.loads(BASELINE.read_text())['kernel_args'].split()


def command_line(r: Round, base: Sequence[str]) -> List[str]:
    """基线上去掉 drop、换定时复位、加本轮参数后的命令行。"""
    gone = set(DROP_ALWAYS) | set(r.drop)
    words = [w for w in base if w.split('=')[0] not in gone]
    words = ['pagani-reset-after=%d' % r.reset if w.startswith('pagani-reset-after=') else w for w in words]
    words = ['pagani-panel=%d' % r.panel if w.startswith('pagani-panel=') else w for w in words]
    return words + list(r.args)


def size_of(r: Round, base: Sequence[str]) -> int:
    return len(' '.join(command_line(r, base)))


# ---- 日志 ----
RECORD = re.compile(r'^\s*(\d+)\s+(\d+\.\d+)\s+(\S)\s\s(.*)$')
UFS_LINE = re.compile(r'^UFS log at LBA \d+: .*state (\d+) \(([^)]*)\), up (\d+) s, (\d+) rounds, (\d+) slots, (\d+) write errors')
RAIL = re.compile(r'\[wifi\] rail (\w+) (now|after) ppid=(0x[0-9a-f]+) status=(0x[0-9a-f]+) en=(0x[0-9a-f]+) mode=(0x[0-9a-f]+) mv=(\d+)(.*)$')
FAIL_LINE = re.compile(r'\[wifi\] FAIL step=(\d+) err=(-?\d+)\s*(.*)$')
ADDRESS = re.compile(r'wifi-net: en0 address (\d+\.\d+\.\d+\.\d+)')
FIRMWARE = re.compile(r'\[wifi\] firmware (\S+) rc=(-?\d+)(?: bytes=(\d+) from=(\S+))?')
RAILS = ('ldof3', 'ldof2', 'ldob8', 'smpd4', 'smpf5', 'smpg3', 'smpi7')
S7I_MV = 1312
DIAG_APPS = re.compile(r'diag: r1 sum @\d+ .* wk (\d+)/(\d+)')
ROOT_READS = re.compile(r'^io rd (\d+) ')
ROOT_READS_FROM = 10        # 秒。Wi-Fi 线程 2 s 开始，rails 这段持写者要等 4.5 s，S7I 的投票在 6.6 s 左右，还是在这之前做完
GUARD = re.compile(r'\[wifi\] guard (\S+)')
# 每一段持写者挂住时看什么（WifiBringup.h、PaganiWifi.cpp）。pagani-wifi-stop 的步号在这一段里再分
GUARD_HANG = {
    'controller': '勘察读 PARF、DBI 时挂住：启动链留下的 GDSC 和时钟状态判断错了，对照 survey 行',
    'rails': '睡眠时钟脚的 SPMI 写或第一次 RPMh 投票时挂住：查 cmd-db、DRV-2 锁字，投票要不要先单跑 pagani-wifi-stop=201',
    'wlan_en': 'WLAN_EN、GDSC、TCSR 页第一次读或时钟这段挂住：=2 stop=205 只放 WLAN_EN，=2 stop=206 加 GDSC',
    'parf': 'PCIe0 外壳或 PHY 第一次访问挂住：对照 clocks 行的 halts，=3 stop=301 不碰 PHY',
    'dbi': '控制器 DBI 第一次访问或训练时挂住：=3 stop=304 不放 PERST#',
    'config': '第一个出 SoC 的配置读挂住：=3 stop=306 到 L0 为止，再看 link 行',
    'dma': 'DMA 窗口第一次碰低端内存，或 SMMU、BAR 分配时挂住：换 pagani-wifi-dmapa=0xc4000000，=4 stop=403 只碰内存',
    'bar0': 'BAR0 或芯片窗口的第一次读挂住：=4 stop=405 映射了不读，review 里 0x3278 窗口的风险',
    'chip': '芯片寄存器第一次经窗口读挂住：看 chipreset 掩码，=5 stop=501',
    'mhi': 'MHI 引导时挂住：对端 PCIe 核重启时读 BAR 可能挂，看有没有 BAR0 put back 行',
    'stack': '协议栈上电的片上复位时挂住：先跑 B2（同样的写入，走自己的 MHI）',
}


@dataclasses.dataclass
class Evidence:
    """一次运行留下的东西：merged.log 的记录和 summary.txt 第一行的 UFS 日志头。"""
    directory: Path
    records: List[Tuple[float, str, str]]
    summary: str

    @classmethod
    def read(cls, directory: Path) -> 'Evidence':
        records = []
        merged = directory / 'merged.log'
        if merged.is_file():
            for line in merged.read_text(errors='replace').splitlines():
                m = RECORD.match(line)
                if m:
                    text = m.group(4)
                    records.append((float(m.group(2)), m.group(3), text[2:] if text.startswith('? ') else text))
        summary = directory / 'summary.txt'
        return cls(directory, records, summary.read_text(errors='replace') if summary.is_file() else '')

    def texts(self, kind: str = 'U') -> List[str]:
        return [text for _, k, text in self.records if k == kind]

    def has(self, *words: str) -> bool:
        return any(all(w in text for w in words) for text in self.texts())

    def first(self, pattern: 're.Pattern') -> Optional['re.Match']:
        for text in self.texts():
            m = pattern.search(text)
            if m:
                return m
        return None

    def every(self, pattern: 're.Pattern') -> List['re.Match']:
        return [m for m in map(pattern.search, self.texts()) if m]

    def ufs(self) -> Optional[dict]:
        for line in self.summary.splitlines():
            m = UFS_LINE.match(line)
            if m:
                return dict(state=int(m.group(1)), text=m.group(2), up=int(m.group(3)), rounds=int(m.group(4)), slots=int(m.group(5)),
                            write_errors=int(m.group(6)))
        return None

    def last_time(self) -> float:
        return self.records[-1][0] if self.records else 0.0


@dataclasses.dataclass
class Verdict:
    name: str
    status: str                 # PASS 通过；SOFT 这个变体没走通，系统健康；HARD 立刻停
    reasons: List[str]
    facts: List[str]
    next_step: List[str]
    progress: int = 0
    directory: Optional[Path] = None


def bad(reasons: List[str], text: str) -> None:
    reasons.append(text)


def health(e: Evidence, r: Round, hard: List[str], facts: List[str]) -> None:
    """三轮都要满足：有日志、UFS 写日志正常、没有 panic、到了定时复位。"""
    if not e.records:
        bad(hard, '没有日志（merged.log 空或不存在）')
        return
    panic = e.texts('P')
    if panic:
        bad(hard, 'panic：%s' % next((t for t in panic if 'panic' in t.lower()), panic[0]))
    u = e.ufs()
    if u is None:
        bad(hard, 'summary.txt 没有 UFS 日志头：这轮的日志不是这次运行的，或者 UFS 日志没读回来')
    else:
        facts.append('UFS 日志 state %d（%s），%d 轮，%d 写错误，运行 %d s' % (u['state'], u['text'], u['rounds'], u['write_errors'], u['up']))
        if u['write_errors']:
            bad(hard, 'UFS 写错误 %d' % u['write_errors'])
        if u['state'] != 6:
            bad(hard, 'UFS 日志状态 %d（%s），不是定时复位前的最后快照：挂死、panic 或写日志停了' % (u['state'], u['text']))
    if e.last_time() < r.reset - 15:
        bad(hard, '日志在 %.0f s 结束，早于 %d s 的定时复位：非计划复位或挂死' % (e.last_time(), r.reset))
    if not e.has('[wifi] thread started'):
        bad(hard, '没有 [wifi] thread started：这个内核没带 PaganiWifi，或者参数没生效')


def apps_up(e: Evidence, reasons: List[str], facts: List[str]) -> None:
    """桌面起来了：诊断第一轮的 wk 全到，根卷读在涨。"""
    m = e.first(DIAG_APPS)
    if not m:
        bad(reasons, '没有 diag: r1 sum 行：桌面没起来，或诊断没跑')
    elif m.group(1) != m.group(2):
        bad(reasons, 'diag wk %s/%s：有 App 没起来' % (m.group(1), m.group(2)))
    else:
        facts.append('桌面正常：wk %s/%s' % (m.group(1), m.group(2)))
    # M 行每秒一条，rd 是这一秒的读次数；桌面起来以后可以是 0，所以看 Wi-Fi 线程开始之后的总数
    reads = [(t, int(m.group(1))) for t, k, text in e.records if k == 'M' for m in [ROOT_READS.match(text)] if m]
    total = sum(n for t, n in reads if t >= ROOT_READS_FROM)
    if not total:
        bad(reasons, '%d s 之后根卷没有读（M io 行 %d 条）：UFS 可能出事了' % (ROOT_READS_FROM, len(reads)))
    else:
        facts.append('%d s 之后根卷读 %d 次' % (ROOT_READS_FROM, total))


def rails(e: Evidence, hard: List[str], soft: List[str], facts: List[str]) -> None:
    """七路电源：S7I 读得到、投后不低于 1312 mV、模式不降；每路都有 after 且 rc 为 0。"""
    now = {m.group(1): m for m in e.every(RAIL) if m.group(2) == 'now'}
    after = {m.group(1): m for m in e.every(RAIL) if m.group(2) == 'after'}
    if e.has('S7I dropped'):
        bad(hard, 'S7I dropped：这条路危险。回退完整包，人工审查')
    if e.has('S7I not read'):
        bad(hard, 'S7I 读不到（step 202）：PPID 或 SPMI 读有问题，查 0x8ad，不进 B')
    missing = [name for name in RAILS if name not in after]
    if missing:
        bad(hard, '这些电源没有 after 行：%s' % ' '.join(missing))
    failed = [name for name, m in after.items() if ' rc=' in m.group(8)]
    if failed:
        bad(hard, '这些电源投票失败：%s' % ' '.join(sorted(failed)))
    s7 = after.get('smpi7')
    if s7:
        mv, mode = int(s7.group(7)), int(s7.group(6), 16)
        before = now.get('smpi7')
        facts.append('S7I after %d mV mode %#x%s' % (mv, mode, ' (之前 %s mV mode %s)' % (before.group(7), before.group(6)) if before else ''))
        if mv < S7I_MV:
            bad(hard, 'S7I after %d mV，低于 %d' % (mv, S7I_MV))
        if not int(s7.group(5), 16) & 0x80:
            bad(hard, 'S7I after 没有使能')
        if before and mode < int(before.group(6), 16):
            bad(hard, 'S7I 模式降了：%s -> %#x' % (before.group(6), mode))
        if before and mode == int(before.group(6), 16):
            bad(soft, 'S7I 模式没变高（%#x）：投票可能没生效，人工看 F1 的 opmode' % mode)
    elif not e.has('[wifi] rail smpi7'):
        bad(hard, '没有 [wifi] rail smpi7 行')


# 失败时的下一步（wifi.md 第 5.5 节）
ADVICE_STEP = {
    302: 'PHY 没就绪：对照 rail 的 phy 行',
    306: '没进 L0：先看睡眠时钟脚，再考虑 RF_CLK1 投票',
    406: 'BAR0 全 1：看 bar0 行，用 pagani-wifi-stop=405 对照',
    501: '固件读不到：native_ufs_part_* 第一次真机失败，看 ufs= 状态码，先修 NativeUfs；种子兜底会超过 95% 预算线',
    601: '内核没带协议栈：查 build-report.json 的 stack_objects',
}
ADVICE_RESET = ('BAR 全 1、MHI 超时：协议栈的全局复位可能把链路弄掉了。跑 B2 预演（pagani-wifi=5 pagani-wifi-chipreset=0x7d，'
                '和协议栈做同样的写入）：make-wifi-rounds.sh B2，然后 wifi-rounds.py --yes --from B2；要在 stage 6 补链路恢复')
HANG_BISECT = '日志中断而没有 FAIL 行是硬挂死：先看最后一条 [wifi] guard，没有才依次跑 =2 stop=205、=2、=3、=4 stop=405、=4 二分'


def hung_in(e: Evidence) -> Optional[str]:
    """挂在哪一段：最后一条 [wifi] 行是 guard。这一行落盘之后才持写者，后面的行挂死时都出不来。"""
    lines = [t for t in e.texts() if '[wifi]' in t]
    m = GUARD.search(lines[-1]) if lines else None
    return m.group(1) if m else None


def stuck(e: Evidence, hard: List[str]) -> None:
    """C、D 轮：系统还活着到定时复位，Wi-Fi 线程却卡在一段上。各变体走同一段上电，试下去没有用，也是挂死的风险。"""
    name = hung_in(e)
    if name:
        bad(hard, 'Wi-Fi 线程停在 guard %s 这一段：挂死' % name)


def note_hang(e: Evidence, facts: List[str], advice: List[str]) -> None:
    name = hung_in(e)
    if name:
        facts.append('最后一条 [wifi] 行是 guard %s：挂在这一段，写者一直持着，线上没有 UFS 写' % name)
        advice.insert(0, GUARD_HANG.get(name, '段 %s 没有说明' % name))


def judge_a(e: Evidence, r: Round) -> Verdict:
    hard, soft, facts, advice = [], [], [], []
    health(e, r, hard, facts)
    rails(e, hard, soft, facts)
    if not e.has('stopped as asked') or not e.has('step=0xcc'):
        bad(hard, '没有 stopped as asked step=204：线程没走到第 204 步')
    fail = e.first(FAIL_LINE)
    if fail:
        bad(hard, '[wifi] FAIL step=%s err=%s %s' % fail.groups())
    apps_up(e, hard, facts)
    if hard:
        advice.append('立刻停，回退完整包（测试镜像/boot-A830-阶段15-完整包.img），人工审查 UFS 和 S7I')
        note_hang(e, facts, advice)
    return Verdict(r.name, 'HARD' if hard else 'PASS', hard + soft, facts, advice)


def judge_b(e: Evidence, r: Round) -> Verdict:
    hard, facts, advice = [], [], []
    health(e, r, hard, facts)
    fail = e.first(FAIL_LINE)
    if fail:
        step = int(fail.group(1))
        bad(hard, '[wifi] FAIL step=%s err=%s %s' % fail.groups())
        advice.append(ADVICE_STEP.get(step, '按 step=%d 查 wifi.md 第 5.5 节' % step))
    wlan = e.first(re.compile(r'\[wifi\] wlan_en ctl=\S+ io=(0x[0-9a-f]+)'))
    if not wlan:
        bad(hard, '没有 wlan_en 行')
    elif not int(wlan.group(1), 16) & 2:
        bad(hard, 'WLAN_EN 读回低（io=%s）' % wlan.group(1))
    wanted = (('[wifi] clocks', 'clocks'), ('[wifi] link', 'link'), ('[wifi] endpoint id=0x110e17cb', 'endpoint 17cb:110e'),
              ('[wifi] dma window, low memory', 'DMA 低端窗口'), ('[wifi] smmu sid', 'SMMU 流'), ('[wifi] mhi regs', 'MHI 寄存器'))
    for text, name in wanted:
        if not e.has(text):
            bad(hard, '没有 %s 行（%s）' % (text, name))
    version = e.first(re.compile(r'\[wifi\] soc hw version raw=\S+ major=(0x[0-9a-f]+)'))
    if version and int(version.group(1), 16) != 2:
        bad(hard, '芯片主版本 %s，不是 2' % version.group(1))
    firmware = {m.group(1): m for m in e.every(FIRMWARE)}
    amss = firmware.get('amss.bin')
    if not amss or amss.group(2) != '0' or amss.group(3) != '6840384':
        bad(hard, 'amss.bin 没读对：%s' % (amss.group(0) if amss else '没有 firmware amss.bin 行'))
        advice.append(ADVICE_STEP[501])
    else:
        facts.append('amss.bin 来自 %s' % amss.group(4))
    for name in ('board.bin', 'regdb.bin'):
        m = firmware.get(name)
        if m:
            facts.append('%s rc=%s 来自 %s' % (name, m.group(2), m.group(4)))
    msi = e.first(re.compile(r'\[wifi\] msi attach rc=(\S+)'))
    if not msi or int(msi.group(1), 16) != 0:
        bad(hard, 'msi attach 失败：%s' % (msi.group(0) if msi else '没有这一行'))
    mission = e.first(re.compile(r'\[wifi\] mission mode \(stage 5 ends here.* msis=(0x[0-9a-f]+)'))
    if not mission:
        bad(hard, '没到任务模式')
        if e.has('MHI boot'):
            advice.append('BHI 错：加 pagani-wifi-chipreset=0x7d')
        if not fail:
            advice.append(HANG_BISECT)
    else:
        count = int(mission.group(1), 16)
        facts.append('任务模式，MSI %d 次' % count)
        if not count:
            bad(hard, '任务模式但 MSI 0 次：Gunyah 不转发 SPI 141？试 pagani-wifi-msi=0')
            advice.append('MSI 不来：pagani-wifi-msi=0')
    apps_up(e, hard, facts)
    if hard:
        note_hang(e, facts, advice)
    return Verdict(r.name, 'HARD' if hard else 'PASS', hard, facts, advice)


# 阶段 6 走到哪一步：数字越大越远，用来挑 C3、C5 的基底
LADDER = (
    (1, '协议栈启动', lambda e: e.has('[wifi] stack started')),
    (2, 'QMI 连上', lambda e: e.has('peach: qmi') or e.has('wifi fw qmi service connected')),
    (3, '固件 FW_READY', lambda e: e.has('qmi firmware ready') or e.has('peach: qmi calibration report') or e.has('FW_READY again')),
    (4, 'en0 起来', lambda e: e.has('wifi-net: en0 is up')),
    (5, '扫到网络', lambda e: e.has('wifi-net: scan result') or e.has('wifi-net: auto join round', 'found')),
    (6, '加入网络', lambda e: e.has('join finished: module result 0')),
    (7, '拿到地址', lambda e: real_address(e) is not None),
)


def real_address(e: Evidence) -> Optional[str]:
    """en0 上一个真实地址：不是 none、0.0.0.0，也不是 DHCP 失败时的 169.254。"""
    for m in e.every(ADDRESS):
        if not m.group(1).startswith(('0.', '169.254.')):
            return m.group(1)
    return None


REQUEST = re.compile(r'wifi-net: request \d+: type (\d+) (get|set) len \d+ val -?\d+ -> (-?\d+) ')
REFUSED = 102               # EOPNOTSUPP：驱动没实现的 Apple80211 请求
# wifid、CoreWiFi 会发、驱动故意不答的类型（21H461 静态分析，名字是 _convertApple80211IOCTLToString 的）：答“不支持”它们都能走下去。
# 这张表之外的类型第一次出现才要看：那是静态分析没走到的路径。
REFUSED_KNOWN = {
    6: 'PROTMODE', 18: 'INT_MIT', 24: 'IBSS_MODE', 25: 'HOST_AP_MODE', 29: 'DEAUTH（读上次原因）', 32: 'RATE_SET', 42: 'STATION_LIST', 47: 'BACKGROUND_SCAN', 55: 'GUARD_INTERVAL',
    57: 'MCS', 66: 'MCS_INDEX_SET', 70: 'WOW_ENABLED', 79: 'STA_STATS', 95: 'VIRTUAL_IF_DELETE', 96: 'VIRTUAL_IF_ROLE', 97: 'VIRTUAL_IF_PARENT', 106: 'RSSI_BOUNDS', 107: 'ROAM', 115: 'POWER_DEBUG_INFO',
    117: 'AWDL_SYNC_ENABLED', 196: 'TX_NSS', 207: 'CHANNELS_INFO', 234: 'LEAKY_AP_AWD_MODE', 239: 'RESET_CHIP', 241: 'RANGING_ENABLE', 245: 'LEAKY_AP_SSID_STATS',
    248: 'LAST_ASSOC_HISTORY', 257: 'TRAP_INFO', 258: 'THERMAL_INDEX', 259: 'MAX_NSS_FOR_AP', 261: 'POWER_BUDGET', 264: 'SCAN_BACKOFF_REPORT', 267: 'SUPPRESS_SCANS',
    336: 'HOST_AP_MODE_HIDDEN', 337: 'LQM_CONFIG', 346: 'BEACON_INFO', 347: 'SOFTAP_PARAMS', 348: 'CHIP_POWER_RANGE', 350: 'SOFTAP_STATS', 352: 'SOFTAP_WIFI_NETWORK_INFO_IE',
    353: 'NSS', 363: 'USB_HOST_NOTIFICATION', 365: 'DISPLAY_STATE', 380: 'SET_RANGING_IDENTIFIER', 381: 'SET_RANGING_FOLLOW_MODE', 382: 'ABORT_SCAN', 384: 'LQM_METRICS_CRASHTRACER_DATA',
    386: 'LQM_SUMMARY', 387: 'COLOCATED_NETWORK_SCOPE_ID', 390: 'BEACON_SCAN_CACHE_REQ', 392: 'PM_MODE', 393: 'CCA', 397: 'RX_RATE', 403: 'SOFTAP_EXTENDED_CAPABILITIES_IE',
    408: 'SENSING_ENABLE', 409: 'SENSING_DISABLE', 419: 'LTE_RESTRICTED_CHANNELS', 420: 'NANPH_SOFTAP_CSA', 422: '6G_MODE', 423: 'COUNTRY_BAND_SUPPORT', 470: 'DHCP_RENEWAL_DATA',
    479: 'WIFI_NOISE_PER_ANT', 480: 'BLOCKED_BANDS',
}
JOIN_RESULT = re.compile(r'wifi-net: join finished: module result (\d+)')
# nw_abi.h 的 NW_RES_*，和设置页看到的（WiFiKit +[NSError associationErrorWithCode:...]）
JOIN_FAIL = {1: '扫描表里没有这个网络', 2: '超时（设置页：Association Timeout，信号弱时 Weak Network）', 3: '接入点拒绝（Unknown Error）',
             4: '密码错（设置页：Incorrect Password）', 5: '路由器安全设置不支持：要 WPA2-PSK/CCMP 或 WPA2/WPA3 过渡', 6: '加入失败或被取消'}


def network_facts(e: Evidence, soft: List[str], facts: List[str]) -> None:
    """C、D 轮共用：网络一侧。都不是立刻停的条件，要人看。"""
    refused = {}
    for m in e.every(REQUEST):
        if int(m.group(3)) == REFUSED:
            refused.setdefault(int(m.group(1)), m.group(2))
    new = sorted(t for t in refused if t not in REFUSED_KNOWN)
    if refused:
        facts.append('驱动不答的请求 %d 种' % len(refused))
    if new:
        bad(soft, '静态分析之外的请求被拒：%s' % ' '.join('%d/%s' % (t, refused[t]) for t in new))
    if e.has('wifi-net: en0 is up'):
        if not e.has('wifi-net: the network stack was opened'):
            bad(soft, 'configd 没有打开 IONetworkStack：wifid 可能卡在等 IOKit 空闲')
        if not e.has('wifi-net: IOKit is quiet'):
            busy = [t for t in e.texts() if 'wifi-net: IOKit busy' in t]
            bad(soft, 'IOKit 一直没空闲（wifid 在等它）：%s' % (busy[-1] if busy else '没有 IOKit busy 行'))
        if not e.has('wifi-net: request'):
            bad(soft, 'wifid 没有发过一条 Apple80211 请求：它没绑到 en0')
    stalls = [m for m in e.every(re.compile(r'wifi-net: input stalls (\d+)')) if m.group(1) != '0']
    if stalls:
        bad(soft, 'en0 输入线程卡过：%s' % stalls[-1].group(0))
    for m in e.every(JOIN_RESULT):
        if m.group(1) != '0':
            facts.append('加入失败：%s' % JOIN_FAIL.get(int(m.group(1)), '结果 %s' % m.group(1)))
    if e.has('wifi-net: dhcp in type 2') and not e.has('wifi-net: dhcp in type 5'):
        bad(soft, '收到 DHCP OFFER 没收到 ACK')


def judge_c(e: Evidence, r: Round) -> Verdict:
    hard, soft, facts, advice = [], [], [], []
    health(e, r, hard, facts)
    for m in e.every(FAIL_LINE):
        step = int(m.group(1))
        if step == 601 and m.group(2) == '-78':
            bad(hard, '内核没带协议栈（step=601 err=-78）：矩阵没有意义')
            advice.append(ADVICE_STEP[601])
        elif step == 601:
            bad(hard, '[wifi] FAIL step=%s err=%s %s：协议栈起不来，C 的变体都走同一段上电，试下去没有用' % m.groups())
            advice.append(ADVICE_RESET)
        elif step < 600:
            bad(hard, '[wifi] FAIL step=%s err=%s %s：B 过了这里却没过' % m.groups())
        else:
            bad(soft, '[wifi] FAIL step=%s err=%s %s' % m.groups())
    if e.has('S7I dropped'):
        bad(hard, 'S7I dropped')
    progress = 0
    reached = '协议栈没启动'
    for level, name, test in LADDER:
        if test(e):
            progress, reached = level, name
    facts.append('走到：%s（%d/7）' % (reached, progress))
    for name in ('board.bin', 'regdb.bin'):
        m = {x.group(1): x for x in e.every(FIRMWARE)}.get(name)
        if m:
            facts.append('%s 来自 %s' % (name, m.group(4)))
            if m.group(4) and m.group(4).startswith('modem_a') and r.seed == SEED_WIFI:
                bad(soft, '%s 用的是 modem_a 那份，不是种子里的 /odm 版本：种子没带它，或 Data 没在时限内挂上' % name)
    if e.has('tme-lite patch refused'):
        facts.append('TME-Lite 被拒，Android 上也这样（result 1 error 83）')
    if e.has('no FW_READY after the mode OFF'):
        facts.append('校准后走了主机重启')
    rddm = [t for t in e.texts() if ' rddm' in t]
    if rddm:
        facts.append('固件崩溃，RDDM 行 %d 条' % len(rddm))
        advice.append('读 RDDM 文本，按它改 M3、QDSS、特性位、PDC；转储在证据目录 ufs/export/（table.bin、segNN.bin）')
    if e.has('no calibration done'):
        advice.append('校准失败：落回任务模式，看后续')
    if e.has('network security not supported'):
        advice.append('路由器安全设置不支持：要 WPA2-PSK/CCMP 或 WPA2/WPA3 过渡')
    if progress >= 4 and progress < 5:
        advice.append('扫不到网络：校准或 BDF。换 /odm BDF，查 L8B 是否 2.8 V')
    if progress in (1, 2, 3) and not rddm:
        advice.append('QMI 停住：看 level 7 的日志，走 C3、C4、C5')
    network_facts(e, soft, facts)
    stuck(e, hard)
    if hard:
        note_hang(e, facts, advice)
    if hard:
        status = 'HARD'
    elif progress >= 7:
        status = 'PASS'
        facts.append('en0 地址 %s' % real_address(e))
        net = e.has('diag: r1@', ' net ', 'en0+')
        if not net:
            bad(soft, 'diag 的 net 行没有 en0+')
    else:
        status = 'SOFT'
        bad(soft, '没有拿到真实地址')
    return Verdict(r.name, status, hard + soft, facts, advice, progress)


# D 轮的走查（设置页，人操作）：每一步在日志里留下什么。走查顺序在 wifi.md 第 5 节和这里各写一份，这里是判据
WALK = (
    ('列表', '进设置的无线局域网页，等列表出来', lambda e: e.has('wifi-net: scan result')),
    ('错的密码', '点一个网络，输错密码，要提示密码不正确并能重输', lambda e: e.has('join finished: module result 4')),
    ('加入', '输对密码，网络打勾', lambda e: e.has('wifi-net: join: ', 'cipher') and e.has('join finished: module result 0')),
    ('拿到地址', '状态栏出现无线图标，详情页有 IP、路由器、DNS', lambda e: real_address(e) is not None),
    ('上网', 'Safari 打开一个网页', lambda e: traffic(e) >= 100),
    ('断开', '详情页忽略此网络（或控制中心断开），打勾消失', lambda e: e.has('wifi-net: leave') and e.has('wifi-net: link lost: reason 0, as asked')),
    ('换网络', '连着 A 时直接点 B', lambda e: e.has('wifi-net: join: ', 'from a joined network')),
    ('开关', '关掉无线局域网再打开，列表回来', lambda e: e.has('wifi-net: power set 0') and e.has('wifi-net: power set 1')),
)
PACKETS = re.compile(r'wifi-net: en0 address \S+, packets in (\d+) out (\d+)')


def traffic(e: Evidence) -> int:
    """en0 收到的包数：Safari 打开一个网页至少一百个。"""
    return max((int(m.group(1)) for m in e.every(PACKETS)), default=0)


def judge_d(e: Evidence, r: Round) -> Verdict:
    """设置页走查的回放。PASS 要前五步都有；断开、换网络、开关是加的，没走到只记一笔。"""
    hard, soft, facts, advice = [], [], [], []
    health(e, r, hard, facts)
    if e.has('wifi-net: auto join requested'):
        bad(hard, '这一轮不该有 join.txt 的自动加入：种子带了 join.txt，或者 pagani-wifi-aj=0 没生效')
    progress = 0
    reached = '协议栈没启动'
    for level, name, test in LADDER:
        if test(e):
            progress, reached = level, name
    facts.append('走到：%s（%d/7）' % (reached, progress))
    done = [name for name, _, test in WALK if test(e)]
    facts.append('走查走到：%s' % (' '.join(done) or '一步也没有'))
    for name, how, test in WALK[:5]:
        if name == '错的密码':
            continue
        if not test(e):
            bad(soft, '第「%s」步没有留下日志：%s' % (name, how))
    for name, how, test in WALK:
        if name not in done and name not in [w[0] for w in WALK[:5] if w[0] != '错的密码']:
            advice.append('没走到「%s」：%s' % (name, how))
    network_facts(e, soft, facts)
    stuck(e, hard)
    if hard:
        note_hang(e, facts, advice)
    essential = all(test(e) for name, _, test in WALK[:5] if name != '错的密码')
    status = 'HARD' if hard else 'PASS' if essential else 'SOFT'
    return Verdict(r.name, status, hard + soft, facts, advice, progress)


def judge(e: Evidence, r: Round) -> Verdict:
    verdict = {'A': judge_a, 'B': judge_b, 'C': judge_c, 'D': judge_d}[r.step](e, r)
    verdict.directory = e.directory
    return verdict


def show(v: Verdict) -> None:
    title = {'PASS': '通过', 'SOFT': '这个变体没走通', 'HARD': '失败，立刻停'}[v.status]
    print('== %s：%s' % (v.name, title))
    if v.directory:
        print('   证据 %s' % v.directory)
    for text in v.facts:
        print('   事实 %s' % text)
    for text in v.reasons:
        print('   问题 %s' % text)
    for text in v.next_step:
        print('   下一步 %s' % text)


# ---- 跑 ----
Runner = Callable[[List[str]], Tuple[int, str]]


def run_streaming(command: List[str]) -> Tuple[int, str]:
    """运行，边打印边收集输出。"""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    lines = []
    for line in process.stdout:
        print(line, end='', flush=True)
        lines.append(line)
    return process.wait(), ''.join(lines)


def serial_attached(run: Runner) -> bool:
    """手机在 adb 或 fastboot 里，序列是 13T。别的序列一概不认。"""
    for tool in (['adb', 'devices'], ['fastboot', 'devices']):
        status, text = run(tool)
        if status == 0 and any(line.split()[:1] == [SERIAL] for line in text.splitlines()):
            return True
    return False


def one_round(r: Round, run: Runner) -> Verdict:
    command = [sys.executable, str(GPU_LOOP), '--image', str(r.image), '--label', r.label, '--serial', SERIAL, '--yes']
    print('---- %s：%s' % (r.name, ' '.join(shlex.quote(c) for c in command)), flush=True)
    status, output = run(command)
    found = re.findall(r'^evidence: (.+)$', output, re.M)
    if not found:
        return Verdict(r.name, 'HARD', ['gpu-loop.py 退出 %d，没有证据目录' % status], [],
                       ['先看手机屏幕：安卓可能已起来但 USB 没枚举，插拔线；再用 read-ramoops.py 读日志，不要强制重启'])
    verdict = judge(Evidence.read(Path(found[-1])), r)
    if status:
        verdict.reasons.append('gpu-loop.py 退出 %d' % status)
    return verdict


def best(verdicts: Sequence[Verdict]) -> Verdict:
    """走得最远的一轮。一样远时取先跑的：C1 不依赖新写的重启路径。"""
    return max(enumerate(verdicts), key=lambda item: (item[1].progress, -item[0]))[1]


def winner_args(name: str) -> List[str]:
    keep = ('pagani-wifi=', 'pagani-wifi-log=', 'pagani-ufslog-ms=')
    return [a for a in ROUNDS[name].args if not a.startswith(keep)]


class Stop(Exception):
    pass


def image_ready(r: Round, limit: int = LIMIT) -> bool:
    """镜像在，旁边的 payload 记录也对得上这一轮：gpu-loop.py 从那里读启动参数，出包后手动换过镜像的话在这里拦住。"""
    return r.image.is_file() and r.image.with_suffix('.payload.json').is_file() and not payload_problems(r, limit)[0]


def matrix(run: Runner, first: str = 'A', last: Optional[str] = None, images: Callable[[Round], bool] = image_ready) -> List[Verdict]:
    """A -> B -> C1 -> C2 -> C3 -> C4 -> C5。返回跑过的各轮判决。"""
    done: List[Verdict] = []

    def play(name: str) -> Verdict:
        r = ROUNDS[name]
        if not images(r):
            # 算一轮失败：结论要说停在这里，退出码也不能是 0，无人值守时才看得出矩阵没跑完
            text = '没有可用的镜像 %s：先跑 make-wifi-rounds.sh %s' % (r.image, name)
            print(text, file=sys.stderr)
            done.append(Verdict(name, 'HARD', [text], [], []))
            raise Stop()
        verdict = one_round(r, run)
        show(verdict)
        done.append(verdict)
        if verdict.status == 'HARD' or (r.step in 'AB' and verdict.status != 'PASS') or (r.step == 'C' and verdict.status == 'PASS') or name == last:
            raise Stop()
        return verdict

    order = ['A', 'B', 'C1', 'C2']
    try:
        if first not in order and not first.startswith('C3-'):
            play(first)                 # 单独一轮：B2、C4-…、C5-…
            return done
        for name in order[order.index(first):] if first in order else ():
            play(name)
        c = [v for v in done if v.name in ('C1', 'C2')]
        tag = first.split('-')[1] if first.startswith('C3-') else 'cbc0' if not c or best(c).name == 'C1' else 'cbc1'
        c3 = play('C3-' + tag)
        c4 = play('C4-' + tag)
        play('C5-' + best(c + [c3, c4]).name)
    except Stop:
        pass
    return done


def conclude(done: Sequence[Verdict]) -> int:
    print('==== 结论')
    for v in done:
        print('  %-14s %-4s 走到 %d/7  %s' % (v.name, v.status, v.progress, v.directory or ''))
    if not done:
        print('  一轮都没跑')
        return 2
    last = done[-1]
    if last.status == 'PASS' and last.name[0] == 'C':
        print('  阶段 6 通了：%s。下一步 D 轮（设置页，人操作）：' % last.name)
        winner = ' '.join(winner_args(last.name))
        print('    bash build-userspace/a830-inputs/fullrun/make-wifi-rounds.sh D %s' % shlex.quote(winner))
        print('    走查：%s；复位 %d s，wifi-rounds.py 不跑这一轮' % ('，'.join(how for _, how, _ in WALK), RESET_MANUAL))
        print('    跑完判：wifi-rounds.py --replay <运行目录> --round D --winner %s' % shlex.quote(winner))
        return 0
    if last.status == 'HARD':
        print('  停在 %s：%s' % (last.name, last.reasons[0] if last.reasons else '见上'))
        return 1
    if last.status == 'PASS':
        print('  %s 通过，到这里按要求停' % last.name)
        return 0
    print('  矩阵走完没有拿到地址。走得最远：%s（%d/7）' % (best(done).name, best(done).progress))
    return 1


def plan(limit: int) -> int:
    base = baseline_args()
    print('基线 %s，%d 字节；上限按 %d 算' % (BASELINE.name, len(' '.join(base)), limit))
    print('所有轮删掉：%s' % ' '.join(DROP_ALWAYS))
    print('C3 起再删：%s；C4 起再删：%s；C5 再删：%s' % (' '.join(DROP_C3), ' '.join(DROP_C4), ' '.join(DROP_C5)))
    worst = 0
    for r in ROUNDS.values():
        size = size_of(r, base)
        worst = max(worst, size)
        print('%-14s %4d 字节  复位 %3d s  种子 %-8s  %s%s' % (r.name, size, r.reset, r.seed, ' '.join(r.args), '' if r.image.is_file() else '  （镜像还没有）'))
    print('序列强制 %s。这是计划，什么都没做；加 --yes 才会重启手机。' % SERIAL)
    if worst > limit:
        print('超过上限：最长 %d 字节' % worst, file=sys.stderr)
        return 1
    return 0


def spec(name: str, winner: str = '') -> int:
    """make-wifi-rounds.sh 读这几行。"""
    r = manual_round(winner.split()) if name == 'D' else ROUNDS.get(name)
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


def payload_problems(r: Round, limit: int) -> Tuple[List[str], int]:
    """镜像旁边的 payload 记录：本轮参数都在，删掉的都不在，复位和面板对，字节数不超。"""
    words = json.loads(r.image.with_suffix('.payload.json').read_text())['kernel_args'].split()
    problems = ['缺 %s' % a for a in r.args if a not in words]
    problems += ['还在 %s' % a for a in DROP_ALWAYS + r.drop if any(w.split('=')[0] == a for w in words)]
    if 'pagani-reset-after=%d' % r.reset not in words:
        problems.append('定时复位不是 %d' % r.reset)
    if 'pagani-panel=%d' % r.panel not in words:
        problems.append('面板不是 %d' % r.panel)
    if 'sheng-log=1' not in words:
        problems.append('缺 sheng-log=1')
    size = len(' '.join(words))
    if size > limit:
        problems.append('%d 字节，超过 %d' % (size, limit))
    return problems, size


def check_payload(name: str, limit: int, winner: str = '') -> int:
    r = manual_round(winner.split()) if name == 'D' else ROUNDS[name]
    problems, size = payload_problems(r, limit)
    for problem in problems:
        print('%s: %s' % (r.image.name, problem), file=sys.stderr)
    print('%s: %d 字节%s' % (r.image.name, size, '，有问题' if problems else ''))
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--yes', action='store_true', help='真跑：重启手机')
    parser.add_argument('--from', dest='first', default='A', help='从这一轮开始（A、B、C1、C2、C3-cbc0、C5-C2 ...）')
    parser.add_argument('--to', dest='last', help='跑完这一轮就停')
    parser.add_argument('--replay', type=Path, help='回放：用这个运行目录判 --round 这一轮，不碰手机')
    parser.add_argument('--round', help='--replay 判哪一轮')
    parser.add_argument('--spec', help='打印一轮的出包参数')
    parser.add_argument('--check-payload', help='核对一轮镜像的 payload 记录')
    parser.add_argument('--winner', default='', help='D 轮用：矩阵里胜出的开关')
    parser.add_argument('--limit', type=int, default=LIMIT, help='命令行字节上限（默认 %(default)s）')
    args = parser.parse_args()
    if args.spec:
        return spec(args.spec, args.winner)
    if args.check_payload:
        return check_payload(args.check_payload, args.limit, args.winner)
    if args.replay:
        r = manual_round(args.winner.split()) if args.round == 'D' else ROUNDS.get(args.round or '')
        if r is None:
            parser.error('--replay 要带 --round')
        verdict = judge(Evidence.read(args.replay), r)
        show(verdict)
        return 0 if verdict.status == 'PASS' else 1
    if not args.yes:
        return plan(args.limit)
    if args.first not in ROUNDS or (args.last and args.last not in ROUNDS):
        parser.error('没有这一轮')
    if not serial_attached(run_streaming):
        print('adb 和 fastboot 都看不到 %s：手机没接回来，什么都没做。USB 上别的手机不算数。' % SERIAL, file=sys.stderr)
        return 2
    started = time.time()
    done = matrix(run_streaming, args.first, args.last, lambda r: image_ready(r, args.limit))
    status = conclude(done)
    report = HERE / 'runs' / ('wifi-rounds-%s.json' % time.strftime('%Y%m%d-%H%M%S'))
    report.write_text(json.dumps([dict(dataclasses.asdict(v), directory=str(v.directory)) for v in done], ensure_ascii=False, indent=1) + '\n')
    print('用时 %.0f 分钟；判决写在 %s' % ((time.time() - started) / 60, report))
    return status


if __name__ == '__main__':
    sys.exit(main())
