#!/usr/bin/env python3
"""wifi-rounds.py 的判据和走法，全在主机上：合成的运行目录（格式与 runs/ 里的一样）、假的 gpu-loop 输出，再用两个真实运行目录做反例。
不碰手机：真跑的入口从不调用；不带 --yes 的一次用假的 adb、fastboot 确认它们没被调用。

    python3 tests/test_wifi_rounds.py
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
spec = importlib.util.spec_from_file_location('wifi_rounds', TOOLS / 'wifi-rounds.py')
wr = importlib.util.module_from_spec(spec)
sys.modules['wifi_rounds'] = wr
spec.loader.exec_module(wr)

UFS_OK = 'UFS log at LBA 124178176: boot_id 0x47bbc9eb, ramoops run 0x92b77965, state 6 (last snapshot before a reset), up 148 s, 50 rounds, 20 slots, 0 write errors, gpt 0/0, period 1000 ms'
checks = 0


def check(condition, message=''):
    global checks
    assert condition, message
    checks += 1


class Log:
    """一份 merged.log：(时间, 种类, 文本)。"""

    def __init__(self, *lines):
        self.lines = list(lines)

    def write(self, directory: Path, summary: str = UFS_OK):
        directory.mkdir(parents=True, exist_ok=True)
        ordered = sorted(self.lines, key=lambda item: item[0])
        text = ''.join('%8d %10.3f %s  %s\n' % (i + 1, t, kind, line) for i, (t, kind, line) in enumerate(ordered))
        (directory / 'merged.log').write_text(text)
        (directory / 'summary.txt').write_text(summary + '\n\nrecords 1\n')
        return directory

    def without(self, word):
        return Log(*[item for item in self.lines if word not in item[2]])

    def plus(self, *lines):
        return Log(*(self.lines + [(100.0, 'U', line) for line in lines]))

    def replaced(self, old, new):
        return Log(*[(t, k, line.replace(old, new)) for t, k, line in self.lines])


def rail(name, when, ppid, mode, mv, tail=''):
    return (2.1, 'U', '[wifi] rail %s %s ppid=%s status=0x80 en=0x80 mode=%s mv=%d%s' % (name, when, ppid, mode, mv, tail))


COMMON = [(0.0, 'B', 'UniXNU ramoops log v3 run=0x92b77965 mask=1'), (2.0, 'U', '[wifi] thread started stage=0x2'),
          (20.0, 'M', 'io rd 300 40 vfy 1.0 free 1 avail 1'), (48.7, 'U', 'diag: r1 sum @77 mg 6/14 pwr 100 c0 net 1 data 1300/2048M wk 35/35'),
          (149.9, 'T', 'ufslog last snapshot before the reset: timer')]
ALL_RAILS = [('ldof3', '0x5c3', 1800), ('ldof2', '0x5c2', 1200), ('ldob8', '0x1c8', 2800), ('smpd4', '0x3a4', 876), ('smpf5', '0x5a7', 876),
             ('smpg3', '0x6a1', 1860), ('smpi7', '0x8ad', 1312)]
ROUND_A = Log(*(COMMON + [rail('smpi7', 'now', '0x8ad', '0x4', 1224)] + [rail(n, 'after', p, '0x7', mv) for n, p, mv in ALL_RAILS] +
                [(2.2, 'U', '[wifi] stopped as asked step=0xcc')]))
ROUND_B = Log(*(COMMON + [
    (2.3, 'U', '[wifi] wlan_en ctl=0x2c3 io=0x3 perst=0x1 clkreq=0x5'), (2.4, 'U', '[wifi] clocks v52008=0x7f v52000=0x581800 halts=0x10'),
    (2.5, 'U', '[wifi] link debug0=0x11 debug1=0x10 gen=2 width=1 ms=30 perst_low_ms=120'), (2.6, 'U', '[wifi] endpoint id=0x110e17cb class=0x28000000 subsys=0x0'),
    (2.7, 'U', '[wifi] dma window, low memory bus=0xc0000000 bytes=0x3000000'), (2.8, 'U', '[wifi] smmu sid stream=0x1401 slot=30 bank=58 reuse=0'),
    (2.9, 'U', '[wifi] mhi regs reglen=0x1000 ver=0x1000000 cfg=0x0 bhi=0x100 bhie=0x124 ctrl=0x0 status=0x0 ee=0x0'),
    (3.0, 'U', '[wifi] soc hw version raw=0x20200 major=0x2'),
    (3.1, 'U', '[wifi] firmware amss.bin rc=0 bytes=6840384 from=modem_a:image/peach/amss20.bin'),
    (5.0, 'U', '[wifi] firmware board.bin rc=0 bytes=125420 from=modem_a:image/peach/bdwlan.elf'),
    (5.1, 'U', '[wifi] msi attach rc=0x0 router=0x0'),
    (6.0, 'U', '[wifi] mission mode (stage 5 ends here; pagani-wifi=6 runs the stack) ms=0x400 msis=0x12')]))
STACK = [(2.9, 'U', '[wifi] thread started stage=0x6'), (3.1, 'U', '[wifi] firmware amss.bin rc=0 bytes=6840384 from=modem_a:image/peach/amss20.bin'),
         (5.0, 'U', '[wifi] firmware board.bin rc=0 bytes=125328 from=/private/var/ngpu/firmware/wlan/peach/bdwlan.elf'),
         (5.1, 'U', '[wifi] firmware regdb.bin rc=0 bytes=24278 from=/private/var/ngpu/firmware/wlan/peach/regdb.bin'),
         (6.0, 'U', '[wifi] stack started, MSI on SPI 141 msi=0x0')]
QMI = STACK + [(6.2, 'U', '[wifi] ath6 peach: qmi tme-lite patch refused: result 1 error 83')]
READY = QMI + [(6.4, 'U', '[wifi] ath7 qmi firmware ready')]
EN0 = READY + [(9.0, 'U', '[wifi] ath6 wifi-net: en0 is up, hardware address 02:11:22:33:44:55, capabilities 0x7')]
SCAN = EN0 + [(11.0, 'U', '[wifi] ath6 wifi-net: scan result 0: 2437 MHz, -50 dBm, HomeWiFi')]
JOIN = SCAN + [(13.0, 'U', '[wifi] ath6 wifi-net: join finished: module result 0')]
IP = JOIN + [(30.0, 'U', '[wifi] ath6 wifi-net: en0 address 192.168.1.23, packets in 40 out 20; frames to the stack 40, refused 0 (last 0)'),
             (50.0, 'U', 'diag: r1@77 net lo0+ en0+ utun0+')]
TAIL = [(20.0, 'M', 'io rd 300 40 vfy 1.0 free 1 avail 1'), (48.7, 'U', 'diag: r1 sum @77 mg 6/14 net 1 wk 35/35'), (239.9, 'T', 'ufslog last snapshot before the reset: timer')]


def stage6(*lines):
    return Log(*(list(lines) + TAIL))


def judge(work, name, log, summary=UFS_OK, tag='x'):
    return wr.judge(wr.Evidence.read(log.write(work / (name + tag), summary)), wr.ROUNDS[name])


def reasons(verdict):
    return ' | '.join(verdict.reasons)


def test_table():
    base = wr.baseline_args()
    check(len(' '.join(base)) == 996, 'the baseline of wifi.md')
    sizes = {name: wr.size_of(r, base) for name, r in wr.ROUNDS.items()}
    check(sizes['A'] == 972 and sizes['B'] == 973, sizes)      # 报告第 5.3 节的表
    check(sizes['C2'] == 992 and sizes['C1'] == 1010, sizes)   # 比报告多 ufslog-ms（22）和 log=15 的一位
    check('pagani-wifi-chipreset=0x7d' in wr.ROUNDS['B2'].args and sizes['B2'] <= wr.LIMIT)
    check(max(sizes.values()) <= wr.LIMIT, sizes)
    for r in wr.ROUNDS.values():
        words = wr.command_line(r, base)
        check(all(a in words for a in r.args), r.name)
        check(len({w.split('=')[0] for w in words}) == len(words), 'an option twice: %s' % r.name)
        check('pagani-reset-after=%d' % r.reset in words and 'sheng-log=1' in words, r.name)
        # 这些不能删：默认值的语义不同；日志和定时复位也要留着
        for keep in ('sheng-gpu-metal-opens=0', 'pagani-gpu-profile=0', 'pagani-ramoops=1', 'pagani-panic-reset=20', 'pagani-wdog=30', 'pagani-reset-wdog=1',
                     'pagani-diag=1', 'native-ufs-root=1'):
            check(keep in words, (r.name, keep))
        check(not any(w.startswith(tuple(wr.DROP_ALWAYS)) for w in words), r.name)
        check(r.reset == (wr.RESET_SHORT if r.step in 'AB' else wr.RESET_STACK), r.name)
    check(wr.ROUNDS['C1'].seed == wr.ROUNDS['C5-C2'].seed == wr.SEED_WIFI and wr.ROUNDS['A'].seed == wr.SEED_PLAIN)
    check('pagani-wifi-cbc=0' in wr.ROUNDS['C1'].args and 'pagani-wifi-cbc=0' not in wr.ROUNDS['C2'].args)
    check('pagani-wifi-m3=0' in wr.ROUNDS['C5-C4-cbc1'].args and 'pagani-wifi-features=0x46' in wr.ROUNDS['C5-C4-cbc1'].args)
    d = wr.manual_round(['pagani-wifi-cbc=0'])
    check(d.reset == 900 and d.seed == 's7-final' and 'pagani-wifi-aj=0' in d.args)     # 要手输密码：带打字修复的种子
    check(d.panel == 65 and 'pagani-panel=65' in wr.command_line(d, base))              # 人用 900 s：第 10 位不能在
    check(all(r.panel == 1217 for r in wr.ROUNDS.values()))
    # 胜出的轮次放得下，D 轮就放得下：删项跟它走，而 D 没有 pagani-wifi-log 和 pagani-ufslog-ms
    for r in wr.ROUNDS.values():
        if r.step == 'C':
            d = wr.manual_round(wr.winner_args(r.name))
            check(d.drop == r.drop and wr.size_of(d, base) <= wr.LIMIT, (r.name, wr.size_of(d, base)))
    check(wr.manual_round(['pagani-wifi-nosuch=1']).drop == ())
    check(wr.winner_args('C4-cbc0') == ['pagani-wifi-cbc=0', 'pagani-wifi-pdc=1', 'pagani-wifi-qdss=1', 'pagani-wifi-features=0x46'])
    print('ok  rounds: every command line fits, longest %d bytes' % max(sizes.values()))


def test_round_a(work):
    v = judge(work, 'A', ROUND_A)
    check(v.status == 'PASS', reasons(v))
    for log, word in ((ROUND_A.plus('[wifi] FAIL step=202 err=-3 S7I not read: no rail voted'), 'S7I'),
                      (ROUND_A.plus('[wifi] FAIL step=202 err=-2 S7I dropped'), 'S7I dropped'),
                      (ROUND_A.without('rail smpg3 after'), 'smpg3'),
                      (ROUND_A.replaced('rail smpi7 after ppid=0x8ad status=0x80 en=0x80 mode=0x7 mv=1312', 'rail smpi7 after ppid=0x8ad status=0x80 en=0x80 mode=0x7 mv=1300'), '1300'),
                      (ROUND_A.replaced('mode=0x7 mv=1312', 'mode=0x2 mv=1312'), '模式降'),
                      (ROUND_A.replaced('rail smpf5 after ppid=0x5a7 status=0x80 en=0x80 mode=0x7 mv=876', 'rail smpf5 after ppid=0x5a7 status=0x80 en=0x80 mode=0x7 mv=876 rc=-5'), '投票失败'),
                      (ROUND_A.without('stopped as asked'), '204'),
                      (ROUND_A.replaced('step=0xcc', 'step=0xcd'), '204'),
                      (ROUND_A.replaced('wk 35/35', 'wk 20/35'), '20/35'),
                      (ROUND_A.without('diag: r1 sum'), 'diag'),
                      (ROUND_A.replaced('io rd 300', 'io rd 0'), '根卷'),
                      (ROUND_A.without('thread started'), 'thread started'),
                      (Log(), '没有日志')):
        v = judge(work, 'A', log)
        check(v.status == 'HARD', (word, v))
        check(word in reasons(v), (word, reasons(v)))
        check(v.next_step and '完整包' in v.next_step[0])
    # 模式没变高：不算失败，要人看
    v = judge(work, 'A', ROUND_A.replaced('mode=0x4 mv=1224', 'mode=0x7 mv=1224'))
    check(v.status == 'PASS' and '模式没变高' in reasons(v), v)
    # UFS 日志的三种坏法
    for summary, word in ((UFS_OK.replace('0 write errors', '3 write errors'), '写错误'),
                          (UFS_OK.replace('state 6 (last snapshot before a reset)', 'state 5 (panic flush)'), '状态 5'),
                          (UFS_OK.replace('state 6 (last snapshot before a reset)', 'state 2 (writing)'), '状态 2'),
                          ('no such line', 'UFS 日志头')):
        v = judge(work, 'A', ROUND_A, summary)
        check(v.status == 'HARD' and word in reasons(v), (word, reasons(v)))
    # panic，提前结束
    v = judge(work, 'A', Log(*(ROUND_A.lines + [(100.0, 'P', 'OnePlus 13T / XNU PANIC')])))
    check(v.status == 'HARD' and 'panic' in reasons(v))
    v = judge(work, 'A', Log(*[item for item in ROUND_A.lines if item[0] < 100]))
    check(v.status == 'HARD' and '早于' in reasons(v), reasons(v))
    print('ok  round A')


def test_round_b(work):
    v = judge(work, 'B', ROUND_B)
    check(v.status == 'PASS', reasons(v))
    check('amss.bin 来自 modem_a:image/peach/amss20.bin' in v.facts and any('MSI 18' in f for f in v.facts), v.facts)
    for log, word in ((ROUND_B.plus('[wifi] FAIL step=306 err=-11 no L0 within 1 s'), 'step=306'),
                      (ROUND_B.without('wlan_en'), 'wlan_en'),
                      (ROUND_B.replaced('io=0x3', 'io=0x1'), 'WLAN_EN'),
                      (ROUND_B.without('[wifi] clocks'), 'clocks'),
                      (ROUND_B.replaced('id=0x110e17cb', 'id=0xffffffff'), 'endpoint'),
                      (ROUND_B.without('dma window'), 'DMA'),
                      (ROUND_B.without('smmu sid'), 'SMMU'),
                      (ROUND_B.without('mhi regs'), 'MHI'),
                      (ROUND_B.replaced('major=0x2', 'major=0x1'), '主版本'),
                      (ROUND_B.replaced('bytes=6840384', 'bytes=6840000'), 'amss'),
                      (ROUND_B.replaced('firmware amss.bin rc=0 bytes=6840384 from=modem_a:image/peach/amss20.bin', 'firmware amss.bin rc=-5 last=image/peach/amss20.bin ufs=-1'), 'amss'),
                      (ROUND_B.without('firmware amss.bin'), 'amss'),
                      (ROUND_B.replaced('msi attach rc=0x0', 'msi attach rc=0xe00002c7'), 'msi'),
                      (ROUND_B.without('msi attach'), 'msi'),
                      (ROUND_B.without('mission mode'), '任务模式'),
                      (ROUND_B.replaced('msis=0x12', 'msis=0x0'), 'MSI 0 次')):
        v = judge(work, 'B', log)
        check(v.status == 'HARD' and word in reasons(v), (word, reasons(v)))
    v = judge(work, 'B', ROUND_B.plus('[wifi] FAIL step=306 err=-11 no L0 within 1 s'))
    check(any('L0' in a for a in v.next_step), v.next_step)
    v = judge(work, 'B', ROUND_B.without('mission mode'))
    check(any('二分' in a for a in v.next_step), v.next_step)
    print('ok  round B')


def test_round_c(work):
    summary = UFS_OK.replace('up 148 s', 'up 238 s')
    v = judge(work, 'C1', stage6(*IP), summary)
    check(v.status == 'PASS' and v.progress == 7, reasons(v))
    check('en0 地址 192.168.1.23' in v.facts and any('TME-Lite' in f for f in v.facts), v.facts)
    check(not [r for r in v.reasons if 'modem_a' in r])
    for lines, level in ((STACK, 1), (QMI, 2), (READY, 3), (EN0, 4), (SCAN, 5), (JOIN, 6)):
        v = judge(work, 'C2', stage6(*lines), summary)
        check(v.status == 'SOFT' and v.progress == level, (level, v))
    # 地址不算数的情况
    for address in ('none', '0.0.0.0', '169.254.10.1'):
        v = judge(work, 'C2', stage6(*JOIN, (30.0, 'U', '[wifi] ath6 wifi-net: en0 address %s, packets in 0 out 0; frames to the stack 0, refused 0 (last 0)' % address)), summary)
        check(v.status == 'SOFT' and v.progress == 6, address)
    # 立刻停的
    v = judge(work, 'C1', stage6((2.9, 'U', '[wifi] thread started stage=0x6'), (2.9, 'U', '[wifi] FAIL step=601 err=-78 the Ath12kWifi stack is not linked: nothing was powered')), summary)
    check(v.status == 'HARD' and '没带协议栈' in reasons(v), reasons(v))
    v = judge(work, 'C1', stage6(*STACK, (3.0, 'U', '[wifi] FAIL step=505 err=-6 MHI boot')), summary)
    check(v.status == 'HARD')
    # 协议栈起不来：变体救不了，给 B2 的建议
    v = judge(work, 'C1', stage6((2.9, 'U', '[wifi] thread started stage=0x6'), (6.0, 'U', '[wifi] FAIL step=601 err=-110 the stack did not start')), summary)
    check(v.status == 'HARD' and any('B2' in a for a in v.next_step), v)
    v = judge(work, 'C1', Log(*(list(EN0) + [(30.0, 'P', 'XNU PANIC')] + TAIL)), summary)
    check(v.status == 'HARD' and 'panic' in reasons(v))
    # 阶段 6 的失败只是这个变体没走通
    v = judge(work, 'C1', stage6(*STACK, (7.0, 'U', '[wifi] FAIL step=603 err=-1 no module handle for the network classes')), summary)
    check(v.status == 'SOFT', reasons(v))
    # 崩溃：有转储就指向它
    v = judge(work, 'C2', stage6(*READY, (8.0, 'U', '[wifi] ath3 rddm upload: status=0x80000000 ms=70 table=0xc0001000 bytes=240')), summary)
    check(v.status == 'SOFT' and any('RDDM' in a for a in v.next_step) and any('RDDM 行' in f for f in v.facts), v)
    # 走了主机重启
    v = judge(work, 'C2', stage6(*READY, (8.0, 'U', '[wifi] ath4 peach: no FW_READY after the mode OFF: the host powers the firmware down and up')), summary)
    check(any('主机重启' in f for f in v.facts))
    # 种子里的 /odm 版本没用上：不算失败，要说出来
    log = stage6(*[(t, k, x.replace('from=/private/var/ngpu/firmware/wlan/peach/bdwlan.elf', 'from=modem_a:image/peach/bdwlan.elf')) for t, k, x in IP])
    v = judge(work, 'C1', log, summary)
    check(v.status == 'PASS' and 'modem_a' in reasons(v), reasons(v))
    print('ok  round C')


def guarded(log, names):
    """同一份日志，加上持写者的 guard 行：判据不该变。"""
    return Log(*(log.lines + [(1.5 + i, 'U', '[wifi] guard ' + name) for i, name in enumerate(names)]))


def test_guards(work):
    """持写者只多了几行 guard 和往后推的时间：判决不变。挂死时最后一条 [wifi] 行指出是哪一段。"""
    v = judge(work, 'A', guarded(ROUND_A, ['rails']), tag='g')
    check(v.status == 'PASS' and not [f for f in v.facts if 'guard' in f], reasons(v))
    # A 轮的投票推到 6.6 s，rails 的 now、after 行的时间：根卷读从 10 s 算起，投票还是在这之前
    late = Log(*[(t + 4.5 if t < 3 and k == 'U' else t, k, x) for t, k, x in ROUND_A.lines])
    check(max(t for t, k, x in late.lines if 'rail smpi7 after' in x) < wr.ROOT_READS_FROM)
    check(judge(work, 'A', guarded(late, ['rails']), tag='l').status == 'PASS')
    v = judge(work, 'B', guarded(ROUND_B, ['rails', 'wlan_en', 'parf', 'dbi', 'config', 'dma', 'bar0', 'chip', 'mhi']), tag='g')
    check(v.status == 'PASS', reasons(v))
    summary = UFS_OK.replace('up 148 s', 'up 238 s')
    v = judge(work, 'C1', Log(*(guarded(stage6(*IP), ['rails', 'wlan_en', 'parf', 'dbi', 'config', 'dma', 'bar0', 'stack']).lines)), summary, tag='g')
    check(v.status == 'PASS' and v.progress == 7, reasons(v))
    # 挂住：最后一条 [wifi] 行是 guard，判决里写明是哪一段、下一步查什么
    hang = Log(*[item for item in ROUND_B.lines if '[wifi]' not in item[2]],
               (2.0, 'U', '[wifi] thread started stage=0x5'), (2.1, 'U', '[wifi] guard rails'), (4.1, 'U', '[wifi] rail smpi7 after ppid=0x8ad status=0x80 en=0x80 mode=0x7 mv=1312'),
               (6.0, 'U', '[wifi] guard wlan_en'), (6.1, 'U', '[wifi] wlan_en ctl=0x2c3 io=0x3 perst=0x1 clkreq=0x5'), (9.0, 'U', '[wifi] guard parf'))
    v = judge(work, 'B', hang, tag='h')
    check(v.status == 'HARD' and any('guard parf' in f for f in v.facts), v)
    check(v.next_step[0].startswith('PCIe0 外壳或 PHY') and any('二分' in a for a in v.next_step), v.next_step)
    check(wr.hung_in(wr.Evidence.read(hang.write(work / 'hx'))) == 'parf')
    # 还有 [wifi] 行跟在后面：不是挂在这一段
    check(wr.hung_in(wr.Evidence.read(hang.plus('[wifi] link debug0=0x11').write(work / 'hy'))) is None)
    # 每一段都有下一步可看，名字和驱动一致
    for name in ('controller', 'rails', 'wlan_en', 'parf', 'dbi', 'config', 'dma', 'bar0', 'chip', 'mhi', 'stack'):
        check(name in wr.GUARD_HANG, name)
    source = (TOOLS.parents[1] / 'UniXNU-Pagani-Drivers/drivers/PaganiWifi')
    if source.is_dir():
        import re
        names = re.findall(r'guard\(b, "(\w+)"\)', (source / 'WifiBringup.cpp').read_text()) + re.findall(r'guard\(gB, "(\w+)"\)', (source / 'PaganiWifi.cpp').read_text())
        check(set(names) == set(wr.GUARD_HANG), names)
    # C、D 轮：系统活到定时复位，线程却停在一段上。不算“这个变体没走通”，矩阵要停
    v = judge(work, 'C1', stage6(*(READY[:1] + [(5.0, 'U', '[wifi] guard stack')])), summary, tag='c')
    check(v.status == 'HARD' and 'guard stack' in reasons(v) and v.next_step[0].startswith('协议栈上电'), v)
    # A 轮挂在投票上：回退完整包的建议还在，段的说明排在前面
    hang = Log(*[item for item in ROUND_A.lines if '[wifi]' not in item[2]], (2.0, 'U', '[wifi] thread started stage=0x2'), (2.1, 'U', '[wifi] guard rails'))
    v = judge(work, 'A', hang, tag='h')
    check(v.status == 'HARD' and any('guard rails' in f for f in v.facts) and any('完整包' in a for a in v.next_step), v)
    print('ok  the guards change no verdict, and a hang names its stretch')


def test_network_facts(work):
    """C 轮补的判据：都不是立刻停的条件，只进问题和事实。"""
    summary = UFS_OK.replace('up 148 s', 'up 238 s')
    opened = '[wifi] ath6 wifi-net: the network stack was opened (type 0)'
    quiet = '[wifi] ath6 wifi-net: IOKit is quiet'
    good = lambda *lines: stage6(*IP, (39.0, 'U', quiet), *[(40.0, 'U', x) for x in lines])
    base = (opened, '[wifi] ath6 wifi-net: request 1: type 19 get len 24 val 0 -> 0 (len 24 val 0)')
    v = judge(work, 'C1', good(*base), summary, tag='n')
    check(v.status == 'PASS' and not v.reasons, reasons(v))
    # configd 没开 IONetworkStack，wifid 一条请求都没有
    v = judge(work, 'C1', stage6(*IP), summary, tag='o')
    check(v.status == 'PASS' and 'IONetworkStack' in reasons(v) and '没有发过' in reasons(v) and 'IOKit' in reasons(v), reasons(v))
    # IOKit 一直忙：把忙的那一行带出来
    v = judge(work, 'C1', stage6(*IP, (40.0, 'U', '[wifi] ath6 wifi-net: IOKit busy 2: wlan(IO80211Controller)')), summary, tag='b')
    check('IO80211Controller' in reasons(v), reasons(v))
    # 不答的请求：表里的不提，表外的要看
    v = judge(work, 'C1', good(*base, '[wifi] ath6 wifi-net: request 2: type 386 get len 8 val 0 -> 102 (len 8 val 0)'), summary, tag='k')
    check(not v.reasons and any('不答的请求 1 种' in f for f in v.facts), v)
    v = judge(work, 'C1', good(*base, '[wifi] ath6 wifi-net: request 2: type 141 set len 8 val 0 -> 102 (len 8 val 0)'), summary, tag='u')
    check('141/set' in reasons(v), reasons(v))
    # 输入线程卡过，OFFER 没等到 ACK
    v = judge(work, 'C1', good(*base, '[wifi] ath6 wifi-net: input stalls 2, kicks 0, cleared after a kick 0, longest 3 s'), summary, tag='s')
    check('输入线程' in reasons(v), reasons(v))
    v = judge(work, 'C1', good(*base, '[wifi] ath6 wifi-net: input stalls 0, kicks 0, cleared after a kick 0, longest 0 s'), summary, tag='t')
    check(not v.reasons, reasons(v))
    v = judge(work, 'C2', stage6(*JOIN, (20.0, 'U', '[wifi] ath6 wifi-net: dhcp in type 2 xid 01020304 flags 0000 to ff:ff:ff:ff:ff:ff yiaddr 192.168.1.23')), summary, tag='d')
    check('OFFER' in reasons(v), reasons(v))
    # 加入失败的原因
    v = judge(work, 'C2', stage6(*SCAN, (20.0, 'U', '[wifi] ath6 wifi-net: join finished: module result 4')), summary, tag='j')
    check(any('密码错' in f for f in v.facts), v.facts)
    v = judge(work, 'C2', stage6(*SCAN, (20.0, 'U', '[wifi] ath6 wifi-net: join finished: module result 5')), summary, tag='j5')
    check(any('安全设置' in f for f in v.facts), v.facts)
    # 表里的类型都不是驱动答的：驱动答了的不能混进“不支持也行”的表
    source = TOOLS.parents[1] / 'UniXNU-Pagani-Drivers/drivers/PaganiWifi/PaganiWifiNet.cpp'
    if source.is_dir() or source.is_file():
        import re
        header = (source.parent / 'WifiApple80211.h').read_text()
        answered = {name: int(value) for name, value in re.findall(r'\b(k[A-Z]\w+) = (\d+)', header)}
        cases = {answered[name] for name in re.findall(r'case (k[A-Z]\w+):', source.read_text()) if name in answered}
        cases -= {29}                                       # DEAUTH：写答，读不答，表里说的是读
        check(not cases & set(wr.REFUSED_KNOWN), cases & set(wr.REFUSED_KNOWN))
    print('ok  the network side of rounds C and D')


def test_round_d(work):
    """D 轮不是 wifi-rounds.py 跑的，--replay DIR --round D 判走查日志。"""
    d = wr.manual_round(['pagani-wifi-cbc=0'])
    summary = UFS_OK.replace('up 148 s', 'up 898 s')
    tail = [(20.0, 'M', 'io rd 300 40 vfy 1.0 free 1 avail 1'), (48.7, 'U', 'diag: r1 sum @77 mg 6/14 net 1 wk 35/35'), (899.9, 'T', 'ufslog last snapshot before the reset: timer')]
    joined = [(30.0, 'U', '[wifi] ath6 wifi-net: join: ssid length 8, bssid given 0, key length 32 cipher 6, private address asked (the board\'s stays)')]
    web = [(60.0, 'U', '[wifi] ath6 wifi-net: en0 address 192.168.1.23, packets in 900 out 700; frames to the stack 900, refused 0 (last 0)')]
    wrong = [(25.0, 'U', '[wifi] ath6 wifi-net: join finished: module result 4')]
    leave = [(70.0, 'U', '[wifi] ath6 wifi-net: leave'), (70.1, 'U', '[wifi] ath6 wifi-net: link lost: reason 0, as asked')]
    swap = [(80.0, 'U', '[wifi] ath6 wifi-net: join: ssid length 5, bssid given 0, key length 32 cipher 6, from a joined network')]
    power = [(90.0, 'U', '[wifi] ath6 wifi-net: power set 0'), (91.0, 'U', '[wifi] ath6 wifi-net: power set 1')]
    opened = [(10.0, 'U', '[wifi] ath6 wifi-net: the network stack was opened (type 0)'), (10.5, 'U', '[wifi] ath6 wifi-net: IOKit is quiet'),
              (11.0, 'U', '[wifi] ath6 wifi-net: request 1: type 19 get len 24 val 0 -> 0 (len 24 val 0)')]

    def run(*parts, tag='d'):
        lines = sum(parts, []) + tail
        return wr.judge(wr.Evidence.read(Log(*lines).write(work / tag, summary)), d)
    v = run(IP, opened, joined, wrong, web, leave, swap, power, tag='all')
    check(v.status == 'PASS' and not v.reasons and not v.next_step, (reasons(v), v.next_step))
    check(any('错的密码' in f and '开关' in f for f in v.facts), v.facts)
    # 只走了必须的：通过，没走到的列出来
    v = run(IP, opened, joined, web, tag='min')
    check(v.status == 'PASS' and len(v.next_step) == 4 and not v.reasons, (reasons(v), v.next_step))
    # Safari 没打开：列表、地址都有也不算通过
    v = run([x if 'en0 address' not in x[2] else (x[0], x[1], x[2].replace('packets in 40', 'packets in 40')) for x in IP], opened, joined, tag='noweb')
    check(v.status == 'SOFT' and '上网' in reasons(v), reasons(v))
    # 根本没有从设置页发起的加入（join finished 是自动加入留下的）
    v = run(IP, opened, tag='nojoin')
    check(v.status == 'SOFT' and '加入' in reasons(v), reasons(v))
    # 带了 join.txt：这轮不算数
    v = run(IP, opened, joined, web, [(29.0, 'U', '[wifi] ath6 wifi-net: auto join requested: 0')], tag='auto')
    check(v.status == 'HARD' and 'join.txt' in reasons(v), reasons(v))
    # 900 s 之前断了
    v = wr.judge(wr.Evidence.read(Log(*(IP + opened + joined + web)).write(work / 'short', summary)), d)
    check(v.status == 'HARD' and '早于' in reasons(v), reasons(v))
    # 命令行入口：--replay --round D
    out = subprocess.run([sys.executable, str(TOOLS / 'wifi-rounds.py'), '--replay', str(work / 'all'), '--round', 'D', '--winner', 'pagani-wifi-cbc=0'], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    check(out.returncode == 0 and 'D：通过' in out.stdout, out.stdout + out.stderr)
    print('ok  round D is judged from its log')


def test_real_runs():
    """两个真实目录：一个是干净的 420 s 运行（内核不带 Wi-Fi 参数），一个 4.8 s 就 panic。"""
    runs = TOOLS / 'runs'
    good, crash = runs / '20261101-110201-stage15-smoke-1', runs / '20261101-053955-stage15-smoke-1'
    if not good.is_dir() or not crash.is_dir():
        print('(no saved runs: the real logs were not replayed)')
        return
    e = wr.Evidence.read(good)
    check(len(e.records) == 11396 and e.ufs()['state'] == 6 and e.ufs()['write_errors'] == 0, e.ufs())
    hard, facts = [], []
    wr.apps_up(e, hard, facts)
    check(not hard and any('wk 35/35' in f for f in facts), hard)      # 这份日志的桌面和根卷都是好的
    v = wr.judge(e, wr.ROUNDS['A'])
    check(v.status == 'HARD' and 'thread started' in reasons(v) and 'after' in reasons(v), reasons(v))
    v = wr.judge(wr.Evidence.read(crash), wr.ROUNDS['C1'])
    check(v.status == 'HARD' and 'panic' in reasons(v) and '状态 5' in reasons(v), reasons(v))
    print('ok  two real runs replayed')


class Fake:
    """假的 gpu-loop：每轮按脚本写一个运行目录，输出 evidence: 行。"""

    def __init__(self, work, scripts):
        self.work, self.scripts, self.commands = work, scripts, []

    def __call__(self, command):
        self.commands.append(command)
        name = command[command.index('--label') + 1][len('wifi-'):]
        check('--yes' in command and command[command.index('--serial') + 1] == 'SERIAL_13T', command)
        check(Path(command[1]).name == 'gpu-loop.py' and command[command.index('--image') + 1].endswith('boot-A830-WiFi-%s.img' % name))
        log, summary = self.scripts[name]
        directory = log.write(self.work / ('run-%s-%d' % (name, len(self.commands))), summary)
        return 0, 'lots of output\nevidence: %s\n' % directory

    def played(self):
        return [c[c.index('--label') + 1][len('wifi-'):] for c in self.commands]


def test_matrix(work):
    summary = UFS_OK.replace('up 148 s', 'up 238 s')
    a, b = (ROUND_A, UFS_OK), (ROUND_B, UFS_OK)
    ok = stage6(*IP), summary
    soft = {level: (stage6(*lines), summary) for level, lines in ((1, STACK), (2, QMI), (3, READY), (4, EN0), (5, SCAN))}
    bad_a = ROUND_A.plus('[wifi] FAIL step=202 err=-2 S7I dropped'), UFS_OK
    bad_b = ROUND_B.without('mission mode'), UFS_OK
    panic = Log(*(list(EN0) + [(30.0, 'P', 'XNU PANIC')] + TAIL)), summary
    every = lambda r: True

    def run(scripts, **options):
        fake = Fake(work / str(len(os.listdir(work))), scripts)
        done = wr.matrix(fake, images=every, **options)
        return fake.played(), done

    played, done = run(dict(A=bad_a))
    check(played == ['A'] and done[-1].status == 'HARD')            # A 失败不进 B
    played, done = run(dict(A=a, B=bad_b))
    check(played == ['A', 'B'] and done[-1].status == 'HARD')       # B 失败不进 C
    played, done = run(dict(A=a, B=b, C1=ok))
    check(played == ['A', 'B', 'C1'] and done[-1].status == 'PASS') # 拿到 IP 结束矩阵
    played, done = run(dict(A=a, B=b, C1=panic))
    check(played == ['A', 'B', 'C1'])                               # panic 立刻停
    # C2 走得远：C3、C4 用校准那一支，C5 接在最远的一轮上
    played, done = run({'A': a, 'B': b, 'C1': soft[2], 'C2': soft[3], 'C3-cbc1': soft[1], 'C4-cbc1': soft[4], 'C5-C4-cbc1': soft[5]})
    check(played == ['A', 'B', 'C1', 'C2', 'C3-cbc1', 'C4-cbc1', 'C5-C4-cbc1'], played)
    # 一样远：取 C1（不依赖新写的重启路径）
    played, done = run({'A': a, 'B': b, 'C1': soft[3], 'C2': soft[3], 'C3-cbc0': soft[1], 'C4-cbc0': soft[1], 'C5-C1': soft[1]})
    check(played == ['A', 'B', 'C1', 'C2', 'C3-cbc0', 'C4-cbc0', 'C5-C1'], played)
    # 矩阵中途拿到 IP
    played, done = run({'A': a, 'B': b, 'C1': soft[2], 'C2': soft[1], 'C3-cbc0': ok})
    check(played[-1] == 'C3-cbc0' and done[-1].status == 'PASS')
    # --from、--to
    played, done = run({'C1': soft[1], 'C2': soft[1], 'C3-cbc0': soft[1]}, first='C1', last='C2')
    check(played == ['C1', 'C2'], played)
    played, done = run({'C2': ok}, first='C2')
    check(played == ['C2'], played)
    played, done = run({'C3-cbc1': soft[1], 'C4-cbc1': soft[1], 'C5-C3-cbc1': soft[1]}, first='C3-cbc1')
    check(played == ['C3-cbc1', 'C4-cbc1', 'C5-C3-cbc1'], played)
    played, done = run({'C5-C2': ok}, first='C5-C2')
    check(played == ['C5-C2'], played)
    played, done = run({'B2': b}, first='B2')
    check(played == ['B2'], played)
    # 没有镜像：一轮都不跑，算失败，结论的退出码不是 0
    fake = Fake(work / 'none', {})
    done = wr.matrix(fake, images=lambda r: False)
    check([v.status for v in done] == ['HARD'] and not fake.commands and wr.conclude(done) == 1)
    # 前面都过了，缺的是后面的镜像：跑到那里停，不算通过
    fake = Fake(work / 'late', {'A': a, 'B': b})
    done = wr.matrix(fake, images=lambda r: r.name != 'C1')
    check(fake.played() == ['A', 'B'] and [v.status for v in done] == ['PASS', 'PASS', 'HARD'] and wr.conclude(done) == 1)
    # gpu-loop 没留下证据
    v = wr.one_round(wr.ROUNDS['A'], lambda c: (4, 'Android not seen'))
    check(v.status == 'HARD' and 'USB' in ' '.join(v.next_step))
    # 结论的退出码
    check(wr.conclude([]) == 2)
    check(wr.conclude([wr.Verdict('A', 'HARD', ['x'], [], [])]) == 1)
    check(wr.conclude([wr.Verdict('C1', 'PASS', [], [], [])]) == 0)
    check(wr.conclude([wr.Verdict('B', 'PASS', [], [], [])]) == 0)
    check(wr.conclude([wr.Verdict('C1', 'SOFT', [], [], [], 3), wr.Verdict('C5-C1', 'SOFT', [], [], [], 2)]) == 1)
    print('ok  the order of the rounds and where it stops')


def test_serial():
    def tool(answers):
        return lambda command: answers[command[0]]
    check(wr.SERIAL == 'SERIAL_13T')
    check(wr.serial_attached(tool(dict(adb=(0, 'List of devices attached\nSERIAL_13T\tdevice\n'), fastboot=(0, '')))))
    check(wr.serial_attached(tool(dict(adb=(0, 'List of devices attached\n'), fastboot=(0, 'SERIAL_13T\tfastboot\n')))))
    # USB 上是另一台：不算
    check(not wr.serial_attached(tool(dict(adb=(0, 'List of devices attached\nSERIAL_OTHER_DEVICE\tdevice\n'), fastboot=(0, 'SERIAL_OTHER_DEVICE fastboot\n')))))
    check(not wr.serial_attached(tool(dict(adb=(1, ''), fastboot=(1, '')))))
    check(not wr.serial_attached(tool(dict(adb=(0, 'xSERIAL_13T\tdevice\n'), fastboot=(0, '')))))
    print('ok  only the 13T counts')


def test_dry_run(work):
    """不带 --yes：计划打印出来，adb、fastboot、gpu-loop.py 都没有被调用。"""
    bin_dir = work / 'bin'
    bin_dir.mkdir()
    marker = work / 'touched'
    for name in ('adb', 'fastboot'):
        path = bin_dir / name
        path.write_text('#!/bin/sh\necho "$0 $*" >> %s\n' % marker)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH='%s:%s' % (bin_dir, os.environ['PATH']), PYTHONDONTWRITEBYTECODE='1')
    out = subprocess.run([sys.executable, str(TOOLS / 'wifi-rounds.py')], capture_output=True, text=True, env=env)
    check(out.returncode == 0 and 'SERIAL_13T' in out.stdout and '什么都没做' in out.stdout, out.stdout + out.stderr)
    check(not marker.exists(), marker.read_text() if marker.exists() else '')
    out = subprocess.run([sys.executable, str(TOOLS / 'wifi-rounds.py'), '--limit', '1000'], capture_output=True, text=True, env=env)
    check(out.returncode == 1 and '超过上限' in out.stderr, 'the rounds do not fit in 1000 bytes: said so')
    out = subprocess.run([sys.executable, str(TOOLS / 'wifi-rounds.py'), '--spec', 'C3-cbc1'], capture_output=True, text=True, env=env)
    lines = dict(line.split('=', 1) for line in out.stdout.splitlines())
    check(lines['image'] == 'boot-A830-WiFi-C3-cbc1.img' and lines['reset'] == '240' and lines['seed'] == 's7-wifi' and lines['panel'] == '1217', lines)
    check('pagani-audio-srzl' in lines['drop'] and lines['args'].startswith('pagani-wifi=6 '), lines)
    out = subprocess.run([sys.executable, str(TOOLS / 'wifi-rounds.py'), '--spec', 'D', '--winner', 'pagani-wifi-cbc=0'], capture_output=True, text=True, env=env)
    check('args=pagani-wifi=6 pagani-wifi-aj=0 pagani-wifi-cbc=0' in out.stdout, out.stdout)
    check('panel=65' in out.stdout and 'seed=s7-final' in out.stdout, out.stdout)
    check(not marker.exists())
    # 真跑也要先认出 13T：USB 上是另一台时只问了 devices，什么都没动。这里的 adb、fastboot 是上面写的假脚本
    for name in ('adb', 'fastboot'):
        (bin_dir / name).write_text('#!/bin/sh\necho "$0 $*" >> %s\necho "SERIAL_OTHER_DEVICE device"\n' % marker)
    out = subprocess.run([sys.executable, str(TOOLS / 'wifi-rounds.py'), '--yes'], capture_output=True, text=True, env=env)
    check(out.returncode == 2 and '都看不到 SERIAL_13T' in out.stderr, out.stdout + out.stderr)
    asked = marker.read_text().splitlines()
    check(len(asked) == 2 and all(line.endswith(' devices') for line in asked), asked)
    print('ok  a plan touches nothing')


def test_check_payload(work):
    r = wr.ROUNDS['C2']
    good = wr.command_line(r, wr.baseline_args())
    saved = wr.IMAGES
    wr.IMAGES = work
    try:
        record = wr.ROUNDS['C2'].image.with_suffix('.payload.json')

        def verdict(words):
            record.write_text(json.dumps(dict(kernel_args=' '.join(words))))
            return wr.check_payload('C2', wr.LIMIT)
        check(verdict(good) == 0)
        check(verdict([w for w in good if not w.startswith('pagani-wifi=')]) == 1)
        check(verdict(good + ['pagani-kbtrace=1']) == 1)
        check(verdict([w.replace('pagani-reset-after=240', 'pagani-reset-after=420') for w in good]) == 1)
        check(verdict([w for w in good if w != 'sheng-log=1']) == 1)
        check(verdict(good + ['x=' + 'y' * 40]) == 1)
        check(verdict([w.replace('pagani-panel=1217', 'pagani-panel=65') for w in good]) == 1)
        # 开跑前的同一套核对：记录不对或没有记录，这一轮不跑
        r.image.write_bytes(b'')
        record.write_text(json.dumps(dict(kernel_args=' '.join(good))))
        check(wr.image_ready(wr.ROUNDS['C2']))
        record.write_text(json.dumps(dict(kernel_args=' '.join(good + ['pagani-kbtrace=1']))))
        check(not wr.image_ready(wr.ROUNDS['C2']))
        record.unlink()
        check(not wr.image_ready(wr.ROUNDS['C2']))
    finally:
        wr.IMAGES = saved
    print('ok  the payload record is checked')


def main():
    with tempfile.TemporaryDirectory(prefix='wifi-rounds-') as scratch:
        work = Path(scratch)
        test_table()
        test_round_a(work / 'a')
        test_round_b(work / 'b')
        test_round_c(work / 'c')
        (work / 'g').mkdir()
        test_guards(work / 'g')
        (work / 'n').mkdir()
        test_network_facts(work / 'n')
        (work / 'w').mkdir()
        test_round_d(work / 'w')
        test_real_runs()
        (work / 'm').mkdir()
        test_matrix(work / 'm')
        test_serial()
        (work / 'd').mkdir()
        test_dry_run(work / 'd')
        (work / 'p').mkdir()
        test_check_payload(work / 'p')
    print('PASS wifi-rounds: %d checks' % checks)


if __name__ == '__main__':
    main()
