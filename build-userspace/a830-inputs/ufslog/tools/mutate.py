#!/usr/bin/env python3
"""Mutation check of the UFS log's gates: copies the sources to a scratch directory, applies one change at a time, runs the test that should
notice and expects it to fail (or time out). A mutation that survives is a gate no test holds.

  G*  NativeUfs: LogWindow.h, RootCommands.h          tests/LogWindow.cpp with the kernel's flags (ro: as a read-only image; plain: neither)
  I*  NativeUfs: LogInterface.h                       tests/LogInterface.cpp, the actual writer linked in
  U*  BootProgress: UfsLog.c, RamoopsLog.c            Test/ufslog.c
  T*  BootProgress: what only threads show            Test/ufslog-threads.c under ThreadSanitizer
  W*  BootProgress: GunyahWdog.c                      Test/gunyah-wdog.c
  P*  工具/gpu-test-loop/ufslog_format.py             tests/test_ufslog.py (with the copy of the reader)

usage: mutate.py <scratch dir> [name prefix ...]"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DRIVERS = ROOT / 'UniXNU-Pagani-Drivers/drivers'
LOOP = ROOT / '工具/gpu-test-loop'
work = Path(sys.argv[1]).resolve()
only = sys.argv[2:]
KERNEL = ['-DNATIVE_UFS_SM8750=1', '-DNATIVE_UFS_READ_ONLY=1', '-DNATIVE_UFS_LOG_WRITES=1']
RO = ['-DNATIVE_UFS_SM8750=1', '-DNATIVE_UFS_READ_ONLY=1']
RC, LW, LI = 'NativeUfs/RootCommands.h', 'NativeUfs/LogWindow.h', 'NativeUfs/LogInterface.h'
UL, RL, WD = 'BootProgress/UfsLog.c', 'BootProgress/RamoopsLog.c', 'BootProgress/GunyahWdog.c'
PY = 'ufslog_format.py'

MUTATIONS = [
    # The switch and the two windows.
    ('G1 hostLogOpen open in a plain read-only image', RC, ' && !(defined(NATIVE_UFS_LOG_WRITES) && NATIVE_UFS_LOG_WRITES)\n  return false;',
     '\n  (void)0;', 'ro'),
    ('G2 the Data store opens', RC, '''static bool hostStoreOpen (uint64_t first, uint64_t blocks)
{
#if defined(NATIVE_UFS_READ_ONLY) && NATIVE_UFS_READ_ONLY
  return false;
#endif''', '''static bool hostStoreOpen (uint64_t first, uint64_t blocks)
{'''),
    # hostInWindow.
    ('G3 one block past the window', RC, 'blocks <= gLogBlocks - (lba - gLogFirst);', 'blocks <= gLogBlocks - (lba - gLogFirst) + 1;'),
    ('G4 one block before the window', RC, 'return gLogOpen && lba >= gLogFirst && lba - gLogFirst < gLogBlocks', 'return gLogOpen && lba + 1 >= gLogFirst'),
    # hostScsiDir's gate.
    ('G5 any opcode with data out', RC, 'if (direction == 1 && cdb[0] == 0x2a) {', 'if (direction == 1) {'),
    ('G6 the gate does not read the CDB', RC, ' &&\n           hostInWindow (loadBe32 (cdb + 2), blocks);', ';'),
    ('G7 flag bits pass', RC, 'return !cdb[1] && !cdb[6] && !cdb[9] && blocks &&', 'return !cdb[6] && !cdb[9] && blocks &&'),
    ('G8 CDB and data may disagree', RC, ' && dataBytes == blocks * 4096U &&', ' &&'),
    ('G9 any LUN', RC, 'if (!gLogOpen || lun || cdbLength != 10) return false;', 'if (!gLogOpen || cdbLength != 10) return false;'),
    ('G10 before the window opens', RC, 'if (!gLogOpen || lun || cdbLength != 10) return false;', 'if (lun || cdbLength != 10) return false;'),
    ('G11 any SYNCHRONIZE CACHE flags', RC, 'for (unsigned index = 1; index < 10; ++index) if (cdb[index]) return false;\n', ''),
    ('G12 SYNCHRONIZE CACHE(16) and the like', RC, 'if (!gLogOpen || lun || cdbLength != 10) return false;', 'if (!gLogOpen || lun) return false;'),
    ('G13 hostWrite4k skips the window', RC, 'return gHost.ready && src && blocks && blocks <= kDataMax / 4096 && !lun && hostInWindow (lba, blocks);',
     'return gHost.ready && src && blocks && blocks <= kDataMax / 4096 && !lun;', 'plain'),
    ('G14 hostWrite4k on any LUN', RC, ' && !lun && hostInWindow (lba, blocks);', ' && hostInWindow (lba, blocks);'),
    ('G15 the close does nothing', RC, '  gLogOpen = false;\n  gLogFirst = gLogBlocks = 0;\n', ''),
    # LogWindow.h.
    ('G16 no name check', LW, 'if (!name || kLogPartition[index] || name[index]) {', 'if (0) {'),
    ('G17 a prefix of the name passes', LW, 'if (!name || kLogPartition[index] || name[index]) {', 'if (!name || kLogPartition[index]) {'),
    ('G18 no guard', LW, 'partition - reserveBlocks < imageBlocks + 1024', 'partition - reserveBlocks < imageBlocks'),
    ('G19 the reserve may wrap', LW, 'reserveBlocks >= partition || ', ''),
    ('G20 the reserve need not hold the regions', LW, 'if (reserveBlocks < kLogReserveBlocks + kExportBlocks) plan.refusal = "reserve too small for the log";\n  else if',
     'if'),
    ('G21 the window one block low', LW, 'plan.logFirst = last + 1 - kLogReserveBlocks;', 'plan.logFirst = last - kLogReserveBlocks;'),
    ('G22 first block of the log not gated', LW, 'if (!ufsLogBlockZero (block) && !ufsLogBlockIs (block, "PGLOG001")) return "content gate: foreign data at the log window\'s first block";\n', ''),
    ('G23 last block of the log not gated', LW, 'if (!ufsLogBlockZero (block)) return "content gate: foreign data at the log window\'s last block";\n', ''),
    ('G23a a magic at the last block of the log passes', LW, 'if (!ufsLogBlockZero (block)) return "content gate: foreign data at the log window\'s last block";',
     'if (!ufsLogBlockZero (block) && !ufsLogBlockIs (block, "PGLOG001")) return "content gate: foreign data at the log window\'s last block";'),
    ('G24 first block of the export not gated', LW, 'if (!ufsLogBlockZero (block) && !ufsLogBlockIs (block, "SHEXR001")) return "content gate: foreign data at the export region\'s first block";\n', ''),
    ('G25 last block of the export not gated', LW, 'if (!ufsLogBlockZero (block)) return "content gate: foreign data at the export region\'s last block";\n', ''),
    ('G25a a magic at the last block of the export passes', LW, 'if (!ufsLogBlockZero (block)) return "content gate: foreign data at the export region\'s last block";',
     'if (!ufsLogBlockZero (block) && !ufsLogBlockIs (block, "SHEXR001")) return "content gate: foreign data at the export region\'s last block";'),
    ('G26 the wrong block read', LW, 'read (exportFirst + kExportBlocks - 1, block)', 'read (exportFirst + kExportBlocks - 2, block)'),
    ('G27 a short zero check', LW, 'for (unsigned index = 0; index < 4096; ++index) any |= block[index];', 'for (unsigned index = 0; index < 4095; ++index) any |= block[index];'),
    ('G28 a read failure passes', LW, 'if (!read (logFirst, block)) return "content gate: log window first block unreadable";', '(void)read (logFirst, block);'),
    ('G29 the gate skipped', LW, 'if (const char *foreign = ufsLogContentGate (plan.logFirst, plan.exportFirst, block, read)) return foreign;\n', ''),
    ('G30 the claim never gives up', LW, '    if (expired ()) return false;\n', ''),
    # The quarantine and the read's time.
    ('G31 a quarantined write touches the data area', RC, '  if (gHost.poisoned || !gHost.ready) return kIOReturnNotReady;\n  gRootDiagnostic.lba = lba;\n', '  gRootDiagnostic.lba = lba;\n'),
    ('G32 a read keeps its second with the log open', RC, 'gLogOpen ? kHostWriteTimeoutUs : kHostReadTimeoutUs', 'kHostReadTimeoutUs'),
    ('G33 every read gets the long time', RC, 'gLogOpen ? kHostWriteTimeoutUs : kHostReadTimeoutUs', 'kHostWriteTimeoutUs'),
    # The interface.
    ('I1 a log write past the region', LI, 'if (!gLogRegionBlocks || !src || !blocks || rel >= gLogRegionBlocks || blocks > gLogRegionBlocks - rel) return 0;\n  while (blocks) {',
     'if (!gLogRegionBlocks || !src || !blocks) return 0;\n  while (blocks) {'),
    ('I2 an export write past the region', LI, 'if (!gLogRegionBlocks || !src || !blocks || rel >= kExportBlocks || blocks > kExportBlocks - rel) return 0;',
     'if (!gLogRegionBlocks || !src || !blocks) return 0;'),
    ('I3 the export region one block low', LI, 'const uint64_t base = gLogRegionFirst - kExportBlocks;', 'const uint64_t base = gLogRegionFirst - kExportBlocks - 1;'),
    ('I4 the close leaves the region', LI, '  gLogClosed = true;\n  gLogRegionBlocks = 0;\n', '  gLogClosed = true;\n'),
    ('I5 a closed window opens again', LI, '  else if (gLogClosed) reason = "closed";\n', ''),
    ('I6 a quarantine does not stop the claim', LI, 'if (!gLogRegionBlocks || !gHost.lock || !gHost.ready || gHost.poisoned) return 0;', 'if (!gLogRegionBlocks || !gHost.lock) return 0;'),
    ('I7 a claim that does not end', LI, '[&] { return mach_absolute_time () >= deadline; }', '[&] { return false; }'),
    ('I8 held writes without a claim', LI, 'if (!gLogPanicOwned || !gLogRegionBlocks || rel >= gLogRegionBlocks', 'if (!gLogRegionBlocks || rel >= gLogRegionBlocks'),
    ('I9 held writes past the log', LI, 'if (!gLogPanicOwned || !gLogRegionBlocks || rel >= gLogRegionBlocks || blocks > gLogRegionBlocks - rel) return 0;', 'if (!gLogPanicOwned || !gLogRegionBlocks) return 0;'),
    ('I10 a flush without a claim', LI, 'return gLogPanicOwned && gLogRegionBlocks && gHost.ready && hostSyncCacheHeld (timeoutUs) == kIOReturnSuccess;',
     'return gLogRegionBlocks && gHost.ready && hostSyncCacheHeld (timeoutUs) == kIOReturnSuccess;'),
    ('I11 the GPT entries not compared', LI, 'return rootCrc32 (entries, gStoreGeometry.entryCount * 128) == gStoreGeometry.entriesCrc ? 0 : 5;', 'return 0;'),
    ('I12 the GPT header not compared', LI, '      le32 (block + 16) != gStoreGeometry.headerCrc) return 3;', '      false) return 3;'),
    ('I13 the window at the wrong end', LI, '      gLogRegionFirst = first;\n', '      gLogRegionFirst = first - 1;\n'),
    # The writer.
    ('U1 no flush before the header that names an export', UL, '    if (ok && h->export_generation != w.export_seen) ok = timed_sync();\n', ''),
    ('U1a a flush before every header', UL, 'if (ok && h->export_generation != w.export_seen) ok = timed_sync();', 'if (ok) ok = timed_sync();'),
    ('U2 no flush after the header', UL, 'ok = timed_write(0, 1, w.block) && timed_sync();', 'ok = timed_write(0, 1, w.block);'),
    ('U3 rounds without new records', UL, '    if (fresh && due) {', '    if (due) {'),
    ('U4 the console ring does not advance the slot', UL, ' || s.console_total - w.base_console > w.advance)) {', ')) {'),
    ('U5 the slot reaches back only to its own first snapshot', UL, 'w.base_console = w.have_slot ? w.last_console : 0;', 'w.base_console = s.console_total;'),
    ('U6 no pinned slots', UL, 'return logical < UFS_LOG_PINNED ? logical : UFS_LOG_PINNED + (logical - UFS_LOG_PINNED) % (w.slot_count - UFS_LOG_PINNED);',
     'return logical % w.slot_count;'),
    ('U7 a fourth failed round', UL, '++w.errors >= UFS_LOG_MAX_ERRORS', '++w.errors > UFS_LOG_MAX_ERRORS'),
    ('U8 errors never forgiven', UL, '    w.errors = 0;\n    if (copy) {', '    if (copy) {'),
    ('U9 a changed GPT goes unnoticed', UL, 'if (h->gpt_last != h->gpt_open) {', 'if (0) {'),
    ('U10 the stop leaves the window open', UL, '    native_ufs_log_close();\n}', '}'),
    ('U11 on without sheng-log', UL, '!ufs_log_platform_arg("sheng-log", &mode) || mode != 1', '(ufs_log_platform_arg("sheng-log", &mode), 0)'),
    ('U12 any sheng-log value', UL, '!ufs_log_platform_arg("sheng-log", &mode) || mode != 1', '!ufs_log_platform_arg("sheng-log", &mode)'),
    ('U13 the export overwrites its last blob', UL, 'export_target = export_ready && !export_ready_first ? half : 0;', 'export_target = 0;'),
    ('U14 a blob counts before it is whole', UL, '        if (!status && done) {\n            export_ready', '        if (!status) {\n            export_ready'),
    ('U15 a blob may fill the half', UL, '(uint64_t)(half - 1) * UFS_LOG_BLOCK', '(uint64_t)half * UFS_LOG_BLOCK'),
    ('U16 the export after a stop', UL, 'if (!half || __atomic_load_n(&writer_stopped, __ATOMIC_ACQUIRE) || !native_ufs_export_write) status = -4;', 'if (!half || !native_ufs_export_write) status = -4;'),
    ('U17 pieces out of order', UL, 'if (!status && (generation != export_generation || total != export_total || offset != export_filled)) status = -3;', ''),
    ('U18 a failed staging allocation goes on', UL, '    if (!status && !export_stage) status = -2;\n', ''),
    ('U19 a panic leaves no mark', UL, 'if (__atomic_exchange_n(&panic_flushed, 1, __ATOMIC_ACQ_REL) || !pending) return;', 'if (!pending) return;'),
    ('U20 the panic ignores a stopped writer', UL, 'return !__atomic_load_n(&panic_flushed, __ATOMIC_ACQUIRE) && !__atomic_load_n(&writer_stopped, __ATOMIC_ACQUIRE) && native_ufs_log_panic &&',
     'return !__atomic_load_n(&panic_flushed, __ATOMIC_ACQUIRE) && native_ufs_log_panic &&'),
    ('U21 the last snapshot ignores pagani-ufslog-panic=0', UL, 'return __atomic_load_n(&live, __ATOMIC_ACQUIRE) && w.panic_on && native_ufs_log_panic_write', 'return __atomic_load_n(&live, __ATOMIC_ACQUIRE) && native_ufs_log_panic_write'),
    ('U22 the panic ignores the reset', UL, '    if (limit_ms && limit_ms < budget / 1000 * 5 / 4) budget = (uint32_t)(limit_ms * 1000 / 5 * 4);\n', ''),
    ('U23 the panic over the current slot', UL, 'slot_position(h.slots_written) * UFS_LOG_SLOT_BLOCKS;', 'slot_position(h.slots_written - 1) * UFS_LOG_SLOT_BLOCKS;'),
    ('U24 no flush after the last snapshot', UL, '    if (timeout) (void)native_ufs_log_panic_sync(timeout);\n', '    (void)timeout;\n'),
    ('U25 a failed panic write goes on', UL, 'if (!timeout || !native_ufs_log_panic_write(base, UFS_LOG_SLOT_BLOCKS, w.panic_slot, timeout)) return;',
     'if (timeout) (void)native_ufs_log_panic_write(base, UFS_LOG_SLOT_BLOCKS, w.panic_slot, timeout);'),
    ('U26 steps past the budget', UL, 'return left < step ? left : step;', 'return step;'),
    ('U27 the header keeps its state', UL, '        h.state = state;\n', ''),
    ('U28 no round in the last seconds', UL, 'now >= w.next_round || last_seconds || events', 'now >= w.next_round || events'),
    ('U29 no early round', UL, ' || events >= w.burst || console >= w.burst;', ';'),
    ('U30 the slot always in whole', UL, 'next || !w.shown_valid ? NULL : w.shown', 'NULL'),
    ('U31 a failed round trusts the medium', UL, '        w.shown_valid = 0;\n', ''),
    ('U32 no floor under the period', UL, '    if (w.period_ms < UFS_LOG_PERIOD_MIN_MS) w.period_ms = UFS_LOG_PERIOD_MIN_MS;\n', ''),
    ('U33 the message buffer not oldest first', UL, 'const char c = m->msg_bufc[at < size ? at : at - size];', 'const char c = m->msg_bufc[i];'),
    ('U34 the oldest of a large message buffer', UL, 'for (uint32_t i = size > MSG_BYTES ? size - MSG_BYTES : 0; i < size; i++) {', 'for (uint32_t i = 0; i < size && n < MSG_BYTES; i++) {'),
    ('U35 the first header waits for a round', UL, '    if (!native_ufs_log_write(0, 1, w.block) || !native_ufs_log_sync()) {\n        h->write_errors = ++w.write_errors;\n        w.errors = 1;\n    }\n', ''),
    ('U36 a foreign GPT at the start', UL, '''    if (gpt) {
        writer_stop(UFS_LOG_STATE_GPT, "the GPT differs from the root start: check", gpt);
        return -1;
    }
''', ''),
    ('U37 a refusal keeps trying', UL, 'if (ufs_same_text(why, "no root layout")) return 0;', 'return 0;'),
    ('U38 the header names an export the flush did not cover', UL, '''    if (ok && h->export_generation != w.export_seen) ok = timed_sync();
    if (ok) {''', '''    if (ok && h->export_generation != w.export_seen) ok = timed_sync();
    if (ok) {
        ufs_log_platform_lock(export_lock);
        h->export_generation = export_ready;
        h->export_bytes = export_ready_bytes;
        h->export_first = export_ready_first;
        ufs_log_platform_unlock(export_lock);'''),
    ('U39 the ring totals not counted', RL, '    __atomic_store_n(&a->total, a->total + n, __ATOMIC_RELEASE);\n', ''),
    ('U40 no flush on the panic path', RL, '    ufs_log_panic(wait && counter_hz ? wait * 1000 / counter_hz : 0);\n', ''),
    ('U41 the flush outside the wait', RL, '    ramoops_platform_wdog_flushed();        // what stayed armed through the flush goes off now: no bite may race the reset below\n    if (!wait) return;\n',
     '    ramoops_platform_wdog_flushed();\n    now = log_counter();\n    if (!wait) return;\n'),
    ('U42 interrupts on during the flush', RL, '    const uint64_t irqs = irq_off();\n    ufs_log_panic(', '    const uint64_t irqs = 0;\n    ufs_log_panic('),
    ('U43 interrupts left off after the flush', RL, '    ufs_log_panic(wait && counter_hz ? wait * 1000 / counter_hz : 0);\n    irq_restore_flags(irqs);\n', '    ufs_log_panic(wait && counter_hz ? wait * 1000 / counter_hz : 0);\n    (void)irqs;\n'),
    ('U44 the snapshot ignores cap', RL, 'const unsigned words = (copy_bytes < cap ? copy_bytes : cap) / 4;', 'const unsigned words = copy_bytes / 4;'),
    ('U45 a past deadline reads as none', RL, 'return ms ? ms : 1;', 'return ms;'),
    # A reset's way out.
    ('U46 a reset does not wait for the log', RL, '    ufs_log_quiesce(reason);\n', ''),
    ('U47 the quiet comes after the bite is set', RL, '''    const uint64_t irqs = irq_off();
    ufs_log_quiesce(reason);
    irq_restore_flags(irqs);
    // pagani-reset-wdog=1 with pagani-wdog: the watchdog's bite resets (it kept the log where SYSTEM_RESET flipped weak DRAM cells);
    // PSCI only if no bite came within 10 s. Otherwise no watchdog bite races the PSCI reset (GunyahWdog.h). A power off never takes
    // the bite, which would restart the phone.
    int bite = 0, held = 0;
    if (reset_wdog && !power_off) bite = ramoops_platform_wdog_bite(reason);
''', '''    int bite = 0, held = 0;
    if (reset_wdog && !power_off) bite = ramoops_platform_wdog_bite(reason);
    const uint64_t irqs = irq_off();
    ufs_log_quiesce(reason);
    irq_restore_flags(irqs);
'''),
    ('U48 interrupts on during the quiet', RL, '    const uint64_t irqs = irq_off();\n    ufs_log_quiesce(reason);\n', '    const uint64_t irqs = 0;\n    ufs_log_quiesce(reason);\n'),
    ('U49 interrupts left off after the quiet', RL, '    ufs_log_quiesce(reason);\n    irq_restore_flags(irqs);\n', '    ufs_log_quiesce(reason);\n    (void)irqs;\n'),
    ('U50 a closed window still claims', UL, 'if (!writer_started || __atomic_load_n(&writer_stopped, __ATOMIC_ACQUIRE) || !native_ufs_log_panic) return;', 'if (!writer_started || !native_ufs_log_panic) return;'),
    ('U51 pets go on under the flush', UL, '    if (snapshot) ufs_log_platform_stop_pets();\n', '    (void)snapshot;\n'),
    ('U52 the pets stop with nothing to flush', UL, '    if (snapshot) ufs_log_platform_stop_pets();\n', '    ufs_log_platform_stop_pets();\n'),
    ('U53 the switch does not hold before a reset', UL, 'UFS_LOG_QUIESCE_CLAIM_US, snapshot ? state : 0, reason)) return;', 'UFS_LOG_QUIESCE_CLAIM_US, state, reason)) return;'),
    ('U54 a second resetter does not wait', UL, '        if (phase == LAST_DONE || ufs_log_platform_counter() - began >= patience) return;\n', '        if (phase != LAST_IDLE) return;\n'),
    ('U55 a second resetter waits for ever', UL, '        if (phase == LAST_DONE || ufs_log_platform_counter() - began >= patience) return;\n', '        if (phase == LAST_DONE) return;\n'),
    ('U56 a failed claim ends it for good', UL, '''    if (!claim_us || !native_ufs_log_panic(claim_us)) {
        __atomic_store_n(&last_state, LAST_IDLE, __ATOMIC_RELEASE);
''', '''    if (!claim_us || !native_ufs_log_panic(claim_us)) {
        __atomic_store_n(&last_state, LAST_DONE, __ATOMIC_RELEASE);
'''),
    ('U57 the claim as long as the panic\'s', UL, 'UFS_LOG_QUIESCE_BUDGET_US, UFS_LOG_QUIESCE_CLAIM_US, snapshot', 'UFS_LOG_QUIESCE_BUDGET_US, UFS_LOG_PANIC_CLAIM_US, snapshot'),
    ('U58 a reset\'s header says writing', UL, 'UFS_LOG_STATE_PANIC : UFS_LOG_STATE_RESET;', 'UFS_LOG_STATE_PANIC : UFS_LOG_STATE_WRITING;'),
    # The Power key.
    ('U80 rounds go on while the key is down', UL, 'const int due = held ? !w.held : now >= w.next_round', 'const int due = now >= w.next_round'),
    ('U81 no round for the press', UL, 'const int due = held ? !w.held : now >= w.next_round', 'const int due = held ? 0 : now >= w.next_round'),
    ('U82 the press counts once for ever', UL, '    w.held = held;\n', '    w.held |= held;\n'),
    ('U83 the export goes on while the key is down', UL, 'if (held || !native_ufs_export_write(', 'if (!native_ufs_export_write('),
    ('U84 a press writes every pass', UL, '    w.held = held;\n', ''),
    ('U85 a refused piece leaves the blob half way', UL, '''                status = held ? -9 : -6;
                export_generation = 0;              // the next generation starts over''', '''                status = held ? -9 : -6;'''),
    ('U86 a round starts after a failed claim', UL, "!= LAST_IDLE || __atomic_load_n(&reset_coming, __ATOMIC_ACQUIRE)) return 1;", "!= LAST_IDLE) return 1;"),
    ('U87 an export piece after a failed claim', UL, 'const int held = ufs_log_platform_power_held() || __atomic_load_n(&reset_coming, __ATOMIC_ACQUIRE);', 'const int held = ufs_log_platform_power_held();'),
    ('U88 presses not counted', UL, '    if (held && !w.held) w.presses++;\n', ''),
    ('U59 a late snapshot forgets the panic', UL, 'const uint32_t state = __atomic_load_n(&panic_flushed, __ATOMIC_ACQUIRE) ? UFS_LOG_STATE_PANIC : UFS_LOG_STATE_RESET;', 'const uint32_t state = UFS_LOG_STATE_RESET;'),
    ('U60 no flush without a snapshot', UL, '''    // Also with nothing to add: the claim may have come between a round's writes and its flush.
    timeout = panic_step(started, budget, UFS_LOG_PANIC_SYNC_US);
''', '''    if (!copy) return;
    timeout = panic_step(started, budget, UFS_LOG_PANIC_SYNC_US);
'''),
    ('U61 rounds go on during the way out', UL, 'if (__atomic_load_n(&last_state, __ATOMIC_ACQUIRE) != LAST_IDLE || __atomic_load_n(&reset_coming, __ATOMIC_ACQUIRE)) return 1;',
     'if (__atomic_load_n(&reset_coming, __ATOMIC_ACQUIRE)) return 1;'),
    ('U62 the header read before the claim', UL, '''    int expected = LAST_IDLE;
    if (!__atomic_compare_exchange_n(&last_state, &expected, LAST_BUSY, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return 0;
    if (!claim_us''', '''    int expected = LAST_IDLE;
    if (!__atomic_compare_exchange_n(&last_state, &expected, LAST_BUSY, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return 0;
    if (state) { last_snapshot(started, budget, state, reason); }
    if (!claim_us'''),
    ('U63 the reason not in the last slot', UL, '        ramoops_log_line(RAMOOPS_KIND_RESET, l.text);\n    }\n    struct ramoops_snapshot s;\n    const unsigned copy = ramoops_log_snapshot(w.panic_slot', '    }\n    struct ramoops_snapshot s;\n    const unsigned copy = ramoops_log_snapshot(w.panic_slot'),
    # The last snapshot's switch.
    ('U64 the last snapshot on without a backstop', UL, 'value = ufs_log_platform_wdog_armed() || ramoops_log_reset_in_ms();', 'value = 1;'),
    ('U65 a timed reset is no backstop', UL, 'value = ufs_log_platform_wdog_armed() || ramoops_log_reset_in_ms();', 'value = ufs_log_platform_wdog_armed();'),
    ('U66 a watchdog is no backstop', UL, 'value = ufs_log_platform_wdog_armed() || ramoops_log_reset_in_ms();', 'value = ramoops_log_reset_in_ms() != 0;'),
    ('U67 pagani-ufslog-panic does not count', UL, '    (void)ufs_log_platform_arg("pagani-ufslog-panic", &value);\n', ''),
    ('U68 the watchdog goes off before the flush is over', RL, '    ramoops_platform_wdog_flushed();        // what stayed armed through the flush goes off now: no bite may race the reset below\n    if (!wait) return;\n',
     '    if (!wait) return;\n'),
    # The export.
    ('U69 a blob over the one the header names', UL, '    else if (!offset && export_ready != export_named) status = -8;          // this blob would go over the one the medium\'s header names\n', ''),
    ('U70 a blob without its magic', UL, '    else if (!offset && (size < 8 || !ufs_same_magic(data, UFS_LOG_EXPORT_MAGIC))) status = -7;     // the content gate would close the log\n', ''),
    ('U71 a short first piece', UL, '!offset && (size < 8 || !ufs_same_magic(data, UFS_LOG_EXPORT_MAGIC))', '!offset && !ufs_same_magic(data, UFS_LOG_EXPORT_MAGIC)'),
    ('U72 the header\'s generation not kept', UL, '    export_named = h->export_generation;\n', ''),
    ('U73 named before the header is on the medium', UL, '''    if (ok && h->export_generation != w.export_seen) ok = timed_sync();
    if (ok) {''', '''    if (ok && h->export_generation != w.export_seen) ok = timed_sync();
    export_named = h->export_generation;
    if (ok) {'''),
    # The statistics.
    ('U74 a round counted without its header', UL, '''        h->max_round_us = w.max_round_us;   // of the rounds before this one: this one ends with this header
        header_block(w.block, h);
''', '''        note_longest(elapsed_us(began), &w.max_round_us, &w.stat_round_us);
        h->max_round_us = w.max_round_us;
        header_block(w.block, h);
''', '''    note_longest(elapsed_us(began), &w.max_round_us, &w.stat_round_us);
    if (!ok) {
''', '''    if (!ok) {
'''),
    ('U74a the header counts its own round in part', UL, '''        h->max_round_us = w.max_round_us;   // of the rounds before this one: this one ends with this header
''', '''        note_longest(elapsed_us(began), &w.max_round_us, &w.stat_round_us);
        h->max_round_us = w.max_round_us;
'''),
    ('U75 lately never starts over', UL, '    w.stat_round_us = w.stat_command_us = 0;\n', ''),
    ('U76 writes not timed', UL, '    note_longest(elapsed_us(began), &w.max_command_us, &w.stat_command_us);\n    return ok;\n}\n\nstatic int timed_sync', '    (void)began;\n    return ok;\n}\n\nstatic int timed_sync'),
    ('U76a flushes not timed', UL, '''    const int ok = native_ufs_log_sync();
    note_longest(elapsed_us(began), &w.max_command_us, &w.stat_command_us);
''', '''    const int ok = native_ufs_log_sync();
    (void)began;
'''),
    # What only threads show (the 2026-10-31 review's list; the rest of it is U77..U79).
    ('T1 the snapshot copies before it reads the totals', RL, '''    info->events_total = __atomic_load_n(&areas[AREA_EVENTS].total, __ATOMIC_ACQUIRE);
    info->console_total = __atomic_load_n(&areas[AREA_CONSOLE].total, __ATOMIC_ACQUIRE);
    info->events_bytes = areas[AREA_EVENTS].bytes;''', '''    info->events_bytes = areas[AREA_EVENTS].bytes;''',
     '''        for (unsigned i = 0; i < words; i++) to[i] = from[i];
    }
''', '''        for (unsigned i = 0; i < words; i++) to[i] = from[i];
    }
    info->events_total = __atomic_load_n(&areas[AREA_EVENTS].total, __ATOMIC_ACQUIRE);
    info->console_total = __atomic_load_n(&areas[AREA_CONSOLE].total, __ATOMIC_ACQUIRE);
'''),
    ('T2 the totals move before the data', RL, '''    if (!a->ring && a->position + n > a->bytes) a = &areas[AREA_EVENTS];
    for (unsigned c = 0; c < copy_count; c++) {''', '''    if (!a->ring && a->position + n > a->bytes) a = &areas[AREA_EVENTS];
    __atomic_store_n(&a->total, a->total + n, __ATOMIC_RELEASE);
    for (unsigned c = 0; c < copy_count; c++) {''', '''    complete_stores();
    __atomic_store_n(&a->total, a->total + n, __ATOMIC_RELEASE);
''', '''    complete_stores();
'''),
    ('T3 the stop not serialised with the export', UL, '''    if (export_lock) ufs_log_platform_lock(export_lock);
    __atomic_store_n(&writer_stopped, 1, __ATOMIC_RELEASE);
    if (export_lock) ufs_log_platform_unlock(export_lock);
''', '''    writer_stopped = 1;
'''),
    ('T4 the round reads the export without its lock', UL, '''    ufs_log_platform_lock(export_lock);
    h->export_generation = export_ready;
    h->export_bytes = export_ready_bytes;
    h->export_first = export_ready_first;
    ufs_log_platform_unlock(export_lock);
''', '''    h->export_generation = export_ready;
    h->export_bytes = export_ready_bytes;
    h->export_first = export_ready_first;
'''),
    ('T5 the export lock published before the region', UL, '''    export_region = (uint32_t)native_ufs_export_blocks();
    __atomic_store_n(&export_lock, lock, __ATOMIC_RELEASE);     // the export's way in: it finds all of the above
''', '''    export_lock = lock;
    export_region = (uint32_t)native_ufs_export_blocks();
'''),
    ('U77 live moves before its header is flushed', UL, '''        header_block(w.block, h);
        ok = timed_write(0, 1, w.block) && timed_sync();
''', '''        header_block(w.block, h);
        __atomic_store_n(&live, h, __ATOMIC_RELEASE);
        ok = timed_write(0, 1, w.block) && timed_sync();
'''),
    ('U78 the snapshot before the claim', UL, '''    if (!claim_us || !native_ufs_log_panic(claim_us)) {
        __atomic_store_n(&last_state, LAST_IDLE, __ATOMIC_RELEASE);
        return 1;
    }
    if (state) last_snapshot(started, budget, state, reason);
''', '''    if (claim_us) last_snapshot_copy();
    if (!claim_us || !native_ufs_log_panic(claim_us)) {
        __atomic_store_n(&last_state, LAST_IDLE, __ATOMIC_RELEASE);
        return 1;
    }
    if (state) last_snapshot(started, budget, state, reason);
''', '''    const unsigned copy = ramoops_log_snapshot(w.panic_slot, UFS_LOG_SLOT_BYTES, &s);
    if (copy) {
''', '''    const unsigned copy = ramoops_log_snapshot(NULL, 0, &s);
    if (copy) {
''', '''// Claims the transport and, with a `state`, takes the last snapshot.''', '''static void last_snapshot_copy(void) { struct ramoops_snapshot s; (void)ramoops_log_snapshot(w.panic_slot, UFS_LOG_SLOT_BYTES, &s); }

// Claims the transport and, with a `state`, takes the last snapshot.'''),
    ('U79 the export without its lock', UL, '''    ufs_log_platform_lock(export_lock);
    int status = 0;
''', '''    int status = 0;
''', '''    ufs_log_platform_unlock(export_lock);
    return status;
''', '''    return status;
'''),
    # The watchdog through a panic's flush.
    ('W1 disabled before the flush', WD, '    if (ramoops_log_panic_footer() && ufs_log_panic_pending()) {\n        __atomic_store_n(&wdog_panic_waits, 1, __ATOMIC_RELEASE);\n        return;\n    }\n', ''),
    ('W2 never disabled after it', WD, '    if (__atomic_exchange_n(&wdog_panic_waits, 0, __ATOMIC_ACQ_REL) && __atomic_load_n(&wdog_state, __ATOMIC_ACQUIRE) == WDOG_ARMED) wdog_panic_settle();\n', ''),
    ('W3 settled with no panic waiting', WD, 'if (__atomic_exchange_n(&wdog_panic_waits, 0, __ATOMIC_ACQ_REL) && __atomic_load_n(&wdog_state, __ATOMIC_ACQUIRE) == WDOG_ARMED) wdog_panic_settle();',
     'if (__atomic_load_n(&wdog_state, __ATOMIC_ACQUIRE) == WDOG_ARMED) wdog_panic_settle();'),
    ('W4 held without a software reset', WD, 'if (ramoops_log_panic_footer() && ufs_log_panic_pending()) {', 'if (ufs_log_panic_pending()) {'),
    ('W5 stop_pets stops nothing', WD, 'void gunyah_wdog_stop_pets(void) { __atomic_store_n(&wdog_pets_stopped, 1, __ATOMIC_RELEASE); }', 'void gunyah_wdog_stop_pets(void) {}'),
    # The reader.
    ('P1 the header CRC unchecked', PY, "if zlib.crc32(block[:HEADER.size - 4]) != fields['crc']:", 'if False:'),
    ('P2 a slot of another run taken', PY, 'elif stated.run_id != run:', 'elif False:'),
    ('P3 uncounted slots read', PY, 'return list(range(min(h.slots_written, pinned))) + list(range(max(pinned, h.slots_written - ring), h.slots_written))',
     'return list(range(h.slot_count))'),
    ('P4 same boot_id passes as new', PY, 'if known and h is not None and h.boot_id == before.boot_id:', 'if False:'),
    ('P5 another run passes', PY, 'if pstore_run is not None and log.run is not None and pstore_run != log.run:', 'if False:'),
    ('P6 paths from the phone taken as they are', PY, "if not relative or relative.startswith('/') or '\\0' in relative or '..' in relative.split('/'):", 'if False:'),
    ('P7 unchecked records of pstore merged', PY, 'if record.seq not in merged and record.verified:', 'if record.seq not in merged:'),
    ('P8 pstore over the UFS log', PY, 'if record.seq not in merged and record.verified:', 'if record.verified:'),
    ('P9 export block numbers from the window', PY, 'region = self.blocks - h.window_blocks - h.export_below', 'region = self.blocks - h.export_below'),
    ('P10 the wrong end of the partition', PY, 'return self.read(self.blocks - WINDOW_BLOCKS, WINDOW_BLOCKS)', 'return self.read(0, WINDOW_BLOCKS)'),
    ('P11 a window nothing tells goes into the run\'s log', PY, "    if verdict.kind != 'current':\n        return decoded\n", "    if verdict.kind == 'old':\n        return decoded\n"),
    ('P12 first_lba ignored', PY, 'if first_lba is not None and h is not None and h.first_lba != first_lba:', 'if False:'),
    ('P13 the partition\'s start taken in blocks', PY, 'return int(start) // 8 + self.blocks - WINDOW_BLOCKS if start.isdigit() and not int(start) % 8 else None', 'return int(start) + self.blocks - WINDOW_BLOCKS if start.isdigit() else None'),
    ('P14 a blank window before the run says nothing', PY, "    if before == BLANK:\n        return Verdict('current', 'UFS log: the window was blank before this run')\n", ''),
    ('P15 a blank header reads as unknown', PY, "return BLANK if why == 'blank' else h", 'return h'),
    ('P16 paths that cannot be files escape', PY, '        except OSError as error:                    # paths that cannot be files here: "a" and "a/b", a name too long\n            raise FormatError(\'export file %r: %s\' % (relative, error)) from error\n', '        finally:\n            pass\n'),
    ('P17 the wrong place reads its export', 'read-ufslog.py', "export = None if args.no_export or verdict.kind == 'old' else uf.read_export(reader, log, print)", 'export = None if args.no_export else uf.read_export(reader, log, print)'),
]


def fresh():
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    drivers = work / 'drivers'
    drivers.mkdir()
    for name in ('NativeUfs', 'BootProgress'):
        shutil.copytree(DRIVERS / name, drivers / name)
    # The reader's test imports its siblings and builds the writer test, so the whole tool without its runs.
    shutil.copytree(LOOP, work / 'loop', ignore=shutil.ignore_patterns('runs', '__pycache__'))


def run(command, timeout=120, env=None):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env)


def fails(test):
    try:
        if test == 'U':
            run(['cc', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror=implicit-function-declaration', '-fsanitize=address,undefined',
                 str(work / 'drivers/BootProgress/Test/ufslog.c'), '-o', str(work / 'test')]).check_returncode()
        elif test == 'W':
            run(['cc', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror=implicit-function-declaration', '-fsanitize=address,undefined',
                 str(work / 'drivers/BootProgress/Test/gunyah-wdog.c'), '-o', str(work / 'test')]).check_returncode()
        elif test == 'T':
            run(['cc', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror=implicit-function-declaration', '-fsanitize=thread',
                 str(work / 'drivers/BootProgress/Test/ufslog-threads.c'), '-o', str(work / 'test')]).check_returncode()
            return run([str(work / 'test')], 180, dict(os.environ, TSAN_OPTIONS='halt_on_error=1')).returncode != 0
        elif test == 'I':
            run(['cc', '-std=c11', '-g', '-Wall', '-Wextra', '-fsanitize=address,undefined', '-c', str(work / 'drivers/BootProgress/Test/ufslog-e2e.c'),
                 '-o', str(work / 'e2e.o')]).check_returncode()
            run(['clang++', '-std=c++17', '-g', '-fsanitize=address,undefined', *KERNEL, str(work / 'drivers/NativeUfs/tests/LogInterface.cpp'),
                 str(work / 'e2e.o'), '-o', str(work / 'test')]).check_returncode()
        elif test == 'P':
            return run(['python3', '-B', str(work / 'loop/tests/test_ufslog.py'), '--writer-test',
                        str(work / 'drivers/BootProgress/Test/ufslog.c')], 400).returncode != 0
        else:
            run(['clang++', '-std=c++17', '-g', '-fsanitize=address,undefined',
                 *(RO if test == 'ro' else ['-DNATIVE_UFS_SM8750=1'] if test == 'plain' else KERNEL),
                 str(work / 'drivers/NativeUfs/tests/LogWindow.cpp'), '-o', str(work / 'test')]).check_returncode()
        return run([str(work / 'test')], 60).returncode != 0
    except subprocess.TimeoutExpired:
        return True


survived, broken = [], []
fresh()
assert not any(fails(test) for test in ('G', 'ro', 'plain', 'U', 'I', 'T', 'W', 'P')), 'the tests must pass without a mutation'
print('baseline passes')
for entry in MUTATIONS:
    name, file = entry[:2]
    if only and not any(name.startswith(prefix + ' ') for prefix in only):
        continue
    path = work / ('loop' if file in (PY, 'read-ufslog.py') else 'drivers') / file
    original = path.read_text()
    # (old, new) pairs, then at most the name of the test: 'ro', 'plain'.
    rest = list(entry[2:])
    test = rest.pop() if len(rest) % 2 else name[0]
    changed = original
    bad = False
    for old, new in zip(rest[::2], rest[1::2]):
        if changed.count(old) != 1:
            print('%s: pattern found %d times; fix the mutation' % (name, changed.count(old)))
            broken.append(name)
            bad = True
            break
        changed = changed.replace(old, new)
    if bad:
        continue
    path.write_text(changed)
    try:
        caught = fails(test)
    finally:
        path.write_text(original)
    print('%-9s %s' % ('caught' if caught else 'SURVIVED', name), flush=True)
    if not caught:
        survived.append(name)
print('%d mutations, %d survived, %d did not apply' % (len(MUTATIONS), len(survived), len(broken)))
sys.exit(1 if survived or broken else 0)
