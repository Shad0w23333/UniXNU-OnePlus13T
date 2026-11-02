#!/usr/bin/env python3
"""Mutation check of the pagani-reset-seq tests: copies drivers/BootProgress to a scratch directory, applies one mutation
at a time (to RamoopsLog.c unless the entry names GunyahWdog.c), builds the test that covers it (Test/ramoops.c, or
Test/gunyah-wdog.c for GunyahWdog.c; -O1, ASan/UBSan) and expects the test to fail (or time out).
M1-M16: the first version (2026-10-31 07:00); M17-M35 and W1-W5: the review fixes F1 (implied clear and its gate), S3 (the
watchdog's hold and release), S4 (interrupts after the park) and S5 (the refused masks).
P1-P9: ramoops_log_power_off (pagani-halt): SYSTEM_OFF once, the watchdog held through it, no bite, no OEM reboot.
usage: mutate.py <BootProgress dir> <scratch dir>"""
import shutil, subprocess, sys
from pathlib import Path

src, work = Path(sys.argv[1]), Path(sys.argv[2])
SDI_LINE = ('    if (steps & RAMOOPS_RESET_SEQ_SDI) (void)reset_seq_call("reset seq: SDI off", RESET_SEQ_FN_SDI_OFF, 2, 1, 0, 0, '
            'NULL);\n')
PROBE_LINE = '    if ((mask & RAMOOPS_RESET_SEQ_PROBE) && !(mask & RAMOOPS_RESET_SEQ_DLOAD) && !implied)'
RELEASE = '    if (!__atomic_load_n(&reset_power_off, __ATOMIC_ACQUIRE)) ramoops_platform_wdog_release(reason);\n'
MUTATIONS = {
    'M1 steps for every resetter': ('    if (!first) {\n        const uint64_t start = log_counter(), limit',
                                    '    if (0) {\n        const uint64_t start = log_counter(), limit'),
    'M2 SDI before the download mode': (SDI_LINE, ''),          # moved, see below
    'M3 SDI arguments (0, 1)': ('RESET_SEQ_FN_SDI_OFF, 2, 1, 0, 0, NULL', 'RESET_SEQ_FN_SDI_OFF, 2, 0, 1, 0, NULL'),
    'M4 download mode written 0x20': ('RESET_SEQ_FN_IO_WRITE, 2,\n                             RESET_SEQ_DLOAD_PA, 0, 0, NULL',
                                      'RESET_SEQ_FN_IO_WRITE, 2,\n                             RESET_SEQ_DLOAD_PA, 0x20, 0, NULL'),
    'M5 no INTERRUPTED continuation': ('if (regs[0] != 1 || answer->smcs >= RESET_SEQ_RESUMES) return;', 'return;'),
    'M6 continuation without x6': ('        x6 = regs[6];\n', '        x6 = 0;\n'),
    'M7 steps on the bite path': ('    if (bite) {\n        struct text t;', '    if (0) {\n        struct text t;'),
    'M8 no claim before the first record': (
        '    if (first && reset_seq_mask && !bite) __atomic_store_n(&reset_seq_state, RESET_SEQ_RUNNING, __ATOMIC_RELEASE);\n', ''),
    'M9 no wait for the parked CPUs': ('    while (parked < signalled && log_counter() - start < limit) {',
                                       '    while (0) {'),
    'M10 probe at every start': ('    if ((reset_seq_mask & RAMOOPS_RESET_SEQ_PROBE) && !reset_seq_probed) {',
                                 '    if (reset_seq_mask & RAMOOPS_RESET_SEQ_PROBE) {'),
    'M11 steps without the boot-arg': ('    if (reset_seq_mask) reset_seq(reason, first, bite);', '    reset_seq(reason, first, bite);'),
    'M12 a failure ends the steps': (
        SDI_LINE,
        '    if ((steps & RAMOOPS_RESET_SEQ_SDI) && reset_seq_call("reset seq: SDI off", RESET_SEQ_FN_SDI_OFF, 2, 1, 0, 0, NULL)) '
        '{ irq_restore_flags(flags); return; }\n'),
    'M13 arbiter halt without its argument count': ('RESET_SEQ_FN_ARB_HALT, 1, 0, 0, 0, NULL', 'RESET_SEQ_FN_ARB_HALT, 0, 0, 0, 0, NULL'),
    'M14 OEM call not logged before': ('        reset_seq_record(RAMOOPS_KIND_TRACE, "reset seq: OEM warm reboot SCM 0xc3000922() next");\n', ''),
    'M15 the probe reads at the reset with dload set too': (PROBE_LINE, '    if (mask & RAMOOPS_RESET_SEQ_PROBE)'),
    'M16 x0 judged on 64 bits': ('    int32_t value = (int32_t)(uint32_t)result;', '    int64_t value = result;'),
    # F1: the clear that comes with the warm candidates, and its gate.
    'M17 no implied clear': ('    const int implied = warm && !(mask & RAMOOPS_RESET_SEQ_DLOAD);',
                             '    const int implied = 0;'),
    'M18 pagani-reset-cold implies nothing': ('    const int warm = (mask & RAMOOPS_RESET_SEQ_WARM) || reset_cold;',
                                              '    const int warm = (mask & RAMOOPS_RESET_SEQ_WARM) != 0;'),
    'M19 no gate': ('        if (warm && ((uint32_t)x0 || (uint32_t)x1)) {', '        if (0) {'),
    'M20 the gate looks at x0 only': ('        if (warm && ((uint32_t)x0 || (uint32_t)x1)) {', '        if (warm && (uint32_t)x0) {'),
    'M35 the gate judges 64 bits': ('        if (warm && ((uint32_t)x0 || (uint32_t)x1)) {', '        if (warm && (x0 || x1)) {'),
    'M21 the gate keeps pagani-reset-cold': ('            reset_cold_skipped = reset_cold != 0;', '            reset_cold_skipped = 0;'),
    'M22 SYSTEM_RESET2 ignores the skip': ('    return (!reset_cold || reset_cold_skipped) && ', '    return !reset_cold && '),
    'M23 the gate skips nothing': ('            steps &= ~(uint32_t)RAMOOPS_RESET_SEQ_WARM;\n', ''),
    # S3: the watchdog's hold before the steps and its release right before PSCI.
    'M24 no hold': ('    if (!bite && (reset_seq_mask || power_off)) held = ramoops_platform_wdog_hold(reason);\n', ''),
    'M25 no release': (RELEASE, ''),
    'M26 every resetter releases': (
        '&& log_counter() - start < limit) relax();\n        return;',
        '&& log_counter() - start < limit) relax();\n        ramoops_platform_wdog_release(reason);\n        return;'),
    'M27 released before the steps': (RELEASE, ''),           # moved, see below
    'M28 hold on the bite path too': ('    if (!bite && (reset_seq_mask || power_off)) held', '    if (reset_seq_mask) held'),
    # ramoops_log_power_off.
    'P1 the bite on a power off': ('    if (reset_wdog && !power_off) bite', '    if (reset_wdog) bite'),
    'P2 no hold on a power off': ('    if (!bite && (reset_seq_mask || power_off)) held', '    if (!bite && reset_seq_mask) held'),
    'P3 the OEM reboot on a power off': ('power_off ? reset_seq_mask & ~(uint32_t)RAMOOPS_RESET_SEQ_OEM : reset_seq_mask', 'reset_seq_mask'),
    'P4 released before SYSTEM_OFF': (RELEASE, '    ramoops_platform_wdog_release(reason);\n'),
    'P5 no SYSTEM_OFF': ('    if (first && power_off) {\n', '    if (0) {\n'),
    'P6 every resetter powers off': ('    if (first && power_off) {\n', '    if (power_off) {\n'),
    'P7 no release when SYSTEM_OFF comes back': ('        log_reset_text();\n        ramoops_platform_wdog_release(reason);\n    }\n',
                                                 '        log_reset_text();\n    }\n'),
    'P8 SYSTEM_RESET for SYSTEM_OFF': ('ramoops_platform_psci(psci_hvc, PSCI_SYSTEM_OFF, 0, 0, 0)',
                                       'ramoops_platform_psci(psci_hvc, PSCI_SYSTEM_RESET, 0, 0, 0)'),
    'P9 no flag': ('    __atomic_store_n(&reset_power_off, 1, __ATOMIC_RELEASE);\n', ''),
    # S4: interrupts after the CPU park.
    'M29 interrupts back after the park': ('    if (!(steps & RAMOOPS_RESET_SEQ_CPUS)) irq_restore_flags(flags);',
                                           '    irq_restore_flags(flags);'),
    'M30 interrupts never back': ('    if (!(steps & RAMOOPS_RESET_SEQ_CPUS)) irq_restore_flags(flags);',
                                  '    if (0) irq_restore_flags(flags);'),
    # S5: the refused masks.
    'M31 unknown bits accepted': ('    if (value & ~(uint32_t)RAMOOPS_RESET_SEQ_KNOWN) return "unknown bits";\n', ''),
    'M32 the first occurrence counts': ('        *length = value ? (unsigned)(p - value) : 0;\n    }',
                                        '        *length = value ? (unsigned)(p - value) : 0;\n        break;\n    }'),
    'M33 nine hex digits taken': ('length < 3 || length > 10 ||', 'length < 3 || length > 11 ||'),
    'M34 a refused mask still runs': ('        if (seq_why) reset_seq_refused = value;\n        else reset_seq_mask = value;',
                                      '        if (seq_why) reset_seq_refused = value;\n        reset_seq_mask = value;'),
    # GunyahWdog.c (Test/gunyah-wdog.c): the hold and the release themselves.
    'W1 hold without the pet': ('GunyahWdog.c', '    struct wdog_result pet = wdog_call(GUNYAH_WDOG_FN_PET, 0, 0);\n    if (!pet.error)',
                                '    struct wdog_result pet = {0, 0, 0};\n    if (!pet.error)'),
    'W2 the time set before the pet': ('GunyahWdog.c', None, None),    # moved, see below
    'W3 the hold keeps the pets': (
        'GunyahWdog.c',
        '    __atomic_store_n(&wdog_pets_stopped, 1, __ATOMIC_RELEASE);\n    if (__atomic_load_n(&wdog_state, __ATOMIC_ACQUIRE) != '
        'WDOG_ARMED) return 0;\n    if (__atomic_exchange_n(&wdog_resetting, 1, __ATOMIC_ACQ_REL)) return 2;',
        '    if (__atomic_load_n(&wdog_state, __ATOMIC_ACQUIRE) != WDOG_ARMED) return 0;\n'
        '    if (__atomic_exchange_n(&wdog_resetting, 1, __ATOMIC_ACQ_REL)) return 2;'),
    'W4 a second hold arms again': ('GunyahWdog.c', '    if (__atomic_exchange_n(&wdog_resetting, 1, __ATOMIC_ACQ_REL)) return 2;',
                                    '    (void)__atomic_exchange_n(&wdog_resetting, 1, __ATOMIC_ACQ_REL);'),
    'W5 release without the state check': (
        'GunyahWdog.c',
        '    struct wdog_result r = {0, 0, 0};\n    if (!wdog_disable(&r)) return;\n    struct wdog_text t;\n    wdog_text_start(&t, '
        'wdog_release_text',
        '    struct wdog_result r = wdog_call(GUNYAH_WDOG_FN_CONTROL, GUNYAH_WDOG_CTRL_DISABLE, 0);\n    struct wdog_text t;\n'
        '    wdog_text_start(&t, wdog_release_text'),
}
survived = []
for name, entry in MUTATIONS.items():
    file, (old, new) = ('RamoopsLog.c', entry) if len(entry) == 2 else (entry[0], entry[1:])
    test = 'gunyah-wdog' if file == 'GunyahWdog.c' else 'ramoops'
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(src, work)
    path = work / file
    text = path.read_text()
    if name == 'W2 the time set before the pet':
        pet = '    struct wdog_result pet = wdog_call(GUNYAH_WDOG_FN_PET, 0, 0);\n    if (!pet.error) __atomic_store_n(&wdog_last_pet, wdog_now(), __ATOMIC_RELEASE);\n'
        times = '    struct wdog_result times = wdog_call(GUNYAH_WDOG_FN_SET_TIME, bark, bite);\n    struct wdog_text t;\n    wdog_text_start(&t, wdog_hold_text'
        assert text.count(pet + times) == 1, name
        text = text.replace(pet + times, times.split('\n')[0] + '\n' + pet + '    struct wdog_text t;\n    wdog_text_start(&t, wdog_hold_text')
    else:
        if text.count(old) != 1:
            print(f'{name}: pattern found {text.count(old)} times; fix the mutation')
            sys.exit(2)
        if name == 'M2 SDI before the download mode':
            text = text.replace(old, '')
            text = text.replace(PROBE_LINE, old + PROBE_LINE)
        elif name == 'M27 released before the steps':
            text = text.replace(old, '')
            text = text.replace('    reset_seq_run(reason);\n', RELEASE + '    reset_seq_run(reason);\n')
        else:
            text = text.replace(old, new)
    path.write_text(text)
    exe = work / f'{test}-mutant'
    build = subprocess.run(['cc', '-std=c11', '-O1', '-Wall', '-Wextra', '-fsanitize=address,undefined', '-Wno-unused-variable',
                            '-Wno-unused-but-set-variable', '-Wno-unused-function', str(work / f'Test/{test}.c'), '-o', str(exe)],
                           capture_output=True, text=True)
    if build.returncode:
        print(f'{name}: does not build (counts as caught)\n{build.stderr[:400]}')
        continue
    try:
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120, env={'ASAN_OPTIONS': 'detect_leaks=0'})
        caught = run.returncode != 0
        why = (run.stderr.strip().splitlines() or ['?'])[-1][:160] if caught else 'PASSED'
    except subprocess.TimeoutExpired:
        caught, why = True, 'timeout'
    print(f'{name}: {"caught" if caught else "SURVIVED"} ({why})')
    if not caught:
        survived.append(name)
print('survivors:', survived or 'none')
sys.exit(1 if survived else 0)
