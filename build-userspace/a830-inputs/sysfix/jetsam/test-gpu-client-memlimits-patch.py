#!/usr/bin/env python3
"""Host test of phone-gpu-client-memlimits.patch (XNU: the memory limits of GPU clients).

1. Shape: the patch touches only bsd/kern/kern_memorystatus.c and iokit/Kernel/IOUserClient.cpp, adds lines and removes none; the floor
   is applied right before both places that store limits handed in from outside (memorystatus_update's posix_spawn path and
   memorystatus_set_memlimit_properties_internal), the over-limit trace sits in memorystatus_log_exception, and IOKit calls the marker
   only after a successful open, behind a weak reference.
2. Behaviour: the lines the patch adds to kern_memorystatus.c run in a host harness (stub proc, proc_list_lock, ledger, boot-arg and
   trace; the SET_/CACHE_ macros taken from the XNU tree when it is there) under ASan/UBSan: the boot-arg values, the floor (-1 and 0
   untouched, idempotent), the marking of iconservicesagent's J120 limits (active 20 soft, inactive 6 hard, clean -> 512/512 with the
   ledger written as inactive and fatal), every refusal case, RunningBoard's read-back-and-set loop, the U lines and their caps.
3. With an XNU tree (--xnu, default the GPU build's work tree, only read): the patch applies (git apply) on copies of both files, and with
   --compile both patched files compile with the build's own compile records and no diagnostic the unpatched files lack.

Run: python3 -I build-userspace/a830-inputs/sysfix/jetsam/test-gpu-client-memlimits-patch.py [--xnu TREE] [--compile]"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCH = HERE / 'phone-gpu-client-memlimits.patch'
GPU_TREE = Path('/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree')
DEFAULT_XNU = GPU_TREE / '.cache/xnu-work'
RECORDS = GPU_TREE / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/compile_commands.json'
FILES = ['bsd/kern/kern_memorystatus.c', 'iokit/Kernel/IOUserClient.cpp']
XNU = None
COMPILE = False


def file_sections(text):
    """{path: [hunk lines]} of a unified diff."""
    result, current = {}, None
    for line in text.splitlines():
        if line.startswith('+++ b/'):
            current = result.setdefault(line[6:], [])
        elif current is not None and not line.startswith('--- '):
            current.append(line)
    return result


def added_block(lines):
    """The added lines of the first hunk, markers stripped."""
    out, seen = [], 0
    for line in lines:
        if line.startswith('@@'):
            seen += 1
            if seen > 1:
                break
        elif seen == 1 and line.startswith('+'):
            out.append(line[1:])
    return '\n'.join(out) + '\n'


MACROS_STANDIN = r'''
#define SET_ACTIVE_LIMITS_LOCKED(p, limit, is_fatal) MACRO_BEGIN (p)->p_memstat_memlimit_active = (limit); \
    if (is_fatal) (p)->p_memstat_state |= P_MEMSTAT_MEMLIMIT_ACTIVE_FATAL; else (p)->p_memstat_state &= ~P_MEMSTAT_MEMLIMIT_ACTIVE_FATAL; MACRO_END
#define SET_INACTIVE_LIMITS_LOCKED(p, limit, is_fatal) MACRO_BEGIN (p)->p_memstat_memlimit_inactive = (limit); \
    if (is_fatal) (p)->p_memstat_state |= P_MEMSTAT_MEMLIMIT_INACTIVE_FATAL; else (p)->p_memstat_state &= ~P_MEMSTAT_MEMLIMIT_INACTIVE_FATAL; MACRO_END
#define CACHE_ACTIVE_LIMITS_LOCKED(p, is_fatal) MACRO_BEGIN (p)->p_memstat_memlimit = (p)->p_memstat_memlimit_active; \
    if ((p)->p_memstat_state & P_MEMSTAT_MEMLIMIT_ACTIVE_FATAL) { (p)->p_memstat_state |= P_MEMSTAT_FATAL_MEMLIMIT; is_fatal = TRUE; } \
    else { (p)->p_memstat_state &= ~P_MEMSTAT_FATAL_MEMLIMIT; is_fatal = FALSE; } MACRO_END
#define CACHE_INACTIVE_LIMITS_LOCKED(p, is_fatal) MACRO_BEGIN (p)->p_memstat_memlimit = (p)->p_memstat_memlimit_inactive; \
    if ((p)->p_memstat_state & P_MEMSTAT_MEMLIMIT_INACTIVE_FATAL) { (p)->p_memstat_state |= P_MEMSTAT_FATAL_MEMLIMIT; is_fatal = TRUE; } \
    else { (p)->p_memstat_state &= ~P_MEMSTAT_FATAL_MEMLIMIT; is_fatal = FALSE; } MACRO_END
'''


def tree_macros():
    """The four limit macros of the XNU tree's kern_memorystatus.c (the real ones), or the stand-ins."""
    source = (XNU / FILES[0]) if XNU else None
    if not source or not source.is_file():
        return MACROS_STANDIN, False
    text = source.read_text()
    start = text.index('#define SET_ACTIVE_LIMITS_LOCKED')
    end = text.index('MACRO_END', text.index('#define CACHE_INACTIVE_LIMITS_LOCKED')) + len('MACRO_END')
    return text[start:end] + '\n', True


HARNESS_HEAD = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int boolean_t;
typedef int kern_return_t;
#define TRUE 1
#define FALSE 0
#define KERN_SUCCESS 0
#define MAXCOMLEN 16
#define MIN(a, b) (((a)<(b))?(a):(b))
#define MAX(a, b) (((a)>(b))?(a):(b))
#define MACRO_BEGIN do {
#define MACRO_END } while (0)
#define P_MEMSTAT_TERMINATED                0x00000020
#define P_MEMSTAT_INTERNAL                  0x00001000
#define P_MEMSTAT_FATAL_MEMLIMIT            0x00002000
#define P_MEMSTAT_MEMLIMIT_ACTIVE_FATAL     0x00004000
#define P_MEMSTAT_MEMLIMIT_INACTIVE_FATAL   0x00008000
#define P_MEMSTAT_FROZEN_FOCAL_THAW         0x00200000
#define os_atomic_inc_orig(p, m) ((*(p))++)
struct task {int id;};
typedef struct task *task_t;
struct proc {
    uint32_t p_memstat_state;
    int32_t p_memstat_memlimit, p_memstat_memlimit_active, p_memstat_memlimit_inactive;
    char p_name[2 * MAXCOMLEN + 1];
    char p_comm[MAXCOMLEN + 1];
    int pid;
    bool exited, active;
    struct task task;
};
typedef struct proc *proc_t;
#define PROC_NULL ((proc_t)0)
static proc_t cur;
static int locked, lock_errors;
static proc_t current_proc(void) {return cur;}
static void proc_list_lock(void) {if (locked) lock_errors++; locked = 1;}
static void proc_list_unlock(void) {if (!locked) lock_errors++; locked = 0;}
static bool proc_list_exited(proc_t p) {return p->exited;}
static int proc_getpid(proc_t p) {return p->pid;}
static task_t proc_task(proc_t p) {return &p->task;}
static boolean_t proc_jetsam_state_is_active_locked(proc_t p) {if (!locked) lock_errors++; return p->active ? TRUE : FALSE;}
static int ledger_calls, ledger_limit, ledger_active, ledger_fatal;
static kern_return_t ledger_result;
static kern_return_t task_set_phys_footprint_limit_internal(task_t t, int limit, int *old, boolean_t active, boolean_t fatal)
{
    (void)t; (void)old;
    if (!locked) lock_errors++;
    ledger_calls++; ledger_limit = limit; ledger_active = active; ledger_fatal = fatal;
    return ledger_result;
}
static uint64_t footprint_bytes;
static uint64_t get_task_phys_footprint(task_t t) {(void)t; return footprint_bytes;}
static int bootarg_present, bootarg_reads;
static uint32_t bootarg_value;
static int PE_parse_boot_argn(const char *name, void *out, int size)
{
    bootarg_reads++;
    if (strcmp(name, "pagani-jetsam") || size != (int)sizeof(uint32_t)) abort();
    if (!bootarg_present) return 0;
    memcpy(out, &bootarg_value, sizeof(bootarg_value));
    return 1;
}
static char trace_lines[512][200];
static int trace_count;
static void trace_line(const char *text)
{
    if (trace_count < 512) snprintf(trace_lines[trace_count], sizeof(trace_lines[0]), "%s", text);
    trace_count++;
}
/* The kernel's weak_import reference: null when BootProgress is not linked (no_trace). */
static void (*boot_progress_trace)(const char *text) = trace_line;
static int memorystatus_highwater_enabled = 1;
'''

HARNESS_TESTS = r'''
static int failures;
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d: CHECK(%s) failed\n", __func__, __LINE__, #c); failures++; } } while (0)

static struct proc make(const char *name, int pid, int32_t active, bool active_fatal, int32_t inactive, bool inactive_fatal, bool is_active)
{
    struct proc p;
    memset(&p, 0, sizeof(p));
    snprintf(p.p_name, sizeof(p.p_name), "%s", name);
    snprintf(p.p_comm, sizeof(p.p_comm), "%.16s", name);
    p.pid = pid;
    SET_ACTIVE_LIMITS_LOCKED(&p, active, active_fatal);
    SET_INACTIVE_LIMITS_LOCKED(&p, inactive, inactive_fatal);
    boolean_t fatal;
    if (is_active) {CACHE_ACTIVE_LIMITS_LOCKED(&p, fatal);} else {CACHE_INACTIVE_LIMITS_LOCKED(&p, fatal);}
    (void)fatal;
    p.active = is_active;
    return p;
}

static void floor_value(void)
{
    /* argv-driven: main sets the boot-arg; this prints the floor for the Python side to compare. */
    printf("floor %d\n", native_gpu_memlimit_floor_mb());
    int reads = bootarg_reads;
    (void)native_gpu_memlimit_floor_mb();
    CHECK(bootarg_reads == reads);            /* read once */
}

static void floored(void)
{
    CHECK(native_gpu_memlimit_floored(-1, 512) == -1);
    CHECK(native_gpu_memlimit_floored(0, 512) == 0);
    CHECK(native_gpu_memlimit_floored(6, 512) == 512);
    CHECK(native_gpu_memlimit_floored(511, 512) == 512);
    CHECK(native_gpu_memlimit_floored(512, 512) == 512);
    CHECK(native_gpu_memlimit_floored(840, 512) == 840);
    CHECK(native_gpu_memlimit_floored(native_gpu_memlimit_floored(15, 512), 512) == 512);
}

static void floor_locked_cases(void)
{
    struct proc p = make("AegirPoster", 300, 30, false, 15, true, true);
    int32_t a = 30, i = 15;
    locked = 1;
    native_gpu_memlimit_floor_locked(&p, &a, &i);           /* not a GPU client: untouched */
    CHECK(a == 30 && i == 15 && trace_count == 0);
    p.p_memstat_state |= P_MEMSTAT_NATIVE_GPU_CLIENT;
    native_gpu_memlimit_floor_locked(&p, &a, &i);
    CHECK(a == 512 && i == 512 && trace_count == 1);
    CHECK(!strcmp(trace_lines[0], "jetsam: floor AegirPoster pid 300 active 30->512 inactive 15->512 MB"));
    native_gpu_memlimit_floor_locked(&p, &a, &i);           /* already at the floor: nothing, no line */
    CHECK(a == 512 && i == 512 && trace_count == 1);
    a = -1; i = 0;
    native_gpu_memlimit_floor_locked(&p, &a, &i);
    CHECK(a == -1 && i == 0 && trace_count == 1);
    a = 1000; i = 600;
    native_gpu_memlimit_floor_locked(&p, &a, &i);
    CHECK(a == 1000 && i == 600 && trace_count == 1);
    for (int n = 0; n < 40; n++) {a = 10; i = 10; native_gpu_memlimit_floor_locked(&p, &a, &i); CHECK(a == 512 && i == 512);}
    CHECK(trace_count == 32);                                /* lines capped, raising not */
    locked = 0;
    CHECK(lock_errors == 0);
}

static void floor_off(void)
{
    struct proc p = make("iconservicesagent", 101, 20, false, 6, true, false);
    p.p_memstat_state |= P_MEMSTAT_NATIVE_GPU_CLIENT;
    int32_t a = 20, i = 6;
    locked = 1;
    native_gpu_memlimit_floor_locked(&p, &a, &i);
    locked = 0;
    CHECK(a == 20 && i == 6 && trace_count == 0);
    cur = &p;
    struct proc q = make("WidgetRenderer_Default", 120, 98, false, 45, false, true);
    cur = &q;
    native_memorystatus_gpu_client();
    CHECK(!(q.p_memstat_state & P_MEMSTAT_NATIVE_GPU_CLIENT));
    CHECK(q.p_memstat_memlimit_active == 98 && q.p_memstat_memlimit_inactive == 45 && ledger_calls == 0 && trace_count == 0);
}

static void iconservicesagent(void)
{
    /* J120: Daemon/Override com.apple.iconservices.iconservicesagent ActiveSoftMemoryLimit 20, Daemon/Override/Global
     * InactiveHardMemoryLimit 6; launchd tracks it dirty/clean (EnableTransactions), clean = inactive. */
    struct proc p = make("iconservicesagent", 101, 20, false, 6, true, false);
    CHECK(p.p_memstat_memlimit == 6 && (p.p_memstat_state & P_MEMSTAT_FATAL_MEMLIMIT));
    cur = &p;
    native_memorystatus_gpu_client();
    CHECK(p.p_memstat_state & P_MEMSTAT_NATIVE_GPU_CLIENT);
    CHECK(p.p_memstat_memlimit_active == 512 && !(p.p_memstat_state & P_MEMSTAT_MEMLIMIT_ACTIVE_FATAL));
    CHECK(p.p_memstat_memlimit_inactive == 512 && (p.p_memstat_state & P_MEMSTAT_MEMLIMIT_INACTIVE_FATAL));
    CHECK(p.p_memstat_memlimit == 512 && (p.p_memstat_state & P_MEMSTAT_FATAL_MEMLIMIT));
    CHECK(ledger_calls == 1 && ledger_limit == 512 && ledger_active == FALSE && ledger_fatal == TRUE);
    CHECK(trace_count == 1);
    CHECK(!strcmp(trace_lines[0], "jetsam: gpu client iconservicesagent pid 101 active 20->512 soft inactive 6->512 hard floor 512"));
    native_memorystatus_gpu_client();                        /* a second connection: nothing */
    CHECK(ledger_calls == 1 && trace_count == 1);
    /* Then dirty: the active limit, soft, goes to the ledger by the kernel's own path; RunningBoard-style settings stay floored. */
    int32_t a = 20, i = 6;
    locked = 1;
    native_gpu_memlimit_floor_locked(&p, &a, &i);
    locked = 0;
    CHECK(a == 512 && i == 512);
    CHECK(lock_errors == 0 && !locked);
}

static void active_now(void)
{
    struct proc p = make("WidgetRenderer_Default", 120, 98, false, 45, false, true);
    cur = &p;
    native_memorystatus_gpu_client();
    CHECK(p.p_memstat_memlimit_active == 512 && p.p_memstat_memlimit_inactive == 512 && p.p_memstat_memlimit == 512);
    CHECK(!(p.p_memstat_state & (P_MEMSTAT_MEMLIMIT_ACTIVE_FATAL | P_MEMSTAT_MEMLIMIT_INACTIVE_FATAL | P_MEMSTAT_FATAL_MEMLIMIT)));
    CHECK(ledger_calls == 1 && ledger_limit == 512 && ledger_active == TRUE && ledger_fatal == FALSE);
    CHECK(!strcmp(trace_lines[0], "jetsam: gpu client WidgetRenderer_Default pid 120 active 98->512 soft inactive 45->512 soft floor 512"));
    struct proc big = make("PosterBoard", 130, 350, false, 350, false, true);
    cur = &big;
    native_memorystatus_gpu_client();
    CHECK(big.p_memstat_memlimit_active == 512 && big.p_memstat_memlimit_inactive == 512 && ledger_calls == 2);
}

static void unlimited(void)
{
    struct proc p = make("backboardd", 60, -1, true, -1, true, true);
    cur = &p;
    native_memorystatus_gpu_client();
    CHECK(p.p_memstat_state & P_MEMSTAT_NATIVE_GPU_CLIENT);
    CHECK(p.p_memstat_memlimit_active == -1 && p.p_memstat_memlimit_inactive == -1 && ledger_calls == 0);
    CHECK(!strcmp(trace_lines[0], "jetsam: gpu client backboardd pid 60 active -1->-1 hard inactive -1->-1 hard floor 512"));
}

static void refused(void)
{
    struct proc launchd = make("launchd", 1, -1, true, -1, true, true);
    launchd.p_memstat_state |= P_MEMSTAT_INTERNAL;
    struct proc dying = make("AegirPoster", 301, 30, false, 15, true, true);
    dying.p_memstat_state |= P_MEMSTAT_TERMINATED;
    struct proc gone = make("AegirPoster", 302, 30, false, 15, true, true);
    gone.exited = true;
    cur = &launchd; native_memorystatus_gpu_client();
    cur = &dying; native_memorystatus_gpu_client();
    cur = &gone; native_memorystatus_gpu_client();
    cur = PROC_NULL; native_memorystatus_gpu_client();
    CHECK(!(launchd.p_memstat_state & P_MEMSTAT_NATIVE_GPU_CLIENT) && !(dying.p_memstat_state & P_MEMSTAT_NATIVE_GPU_CLIENT) &&
        !(gone.p_memstat_state & P_MEMSTAT_NATIVE_GPU_CLIENT));
    CHECK(dying.p_memstat_memlimit_inactive == 15 && gone.p_memstat_memlimit_inactive == 15);
    CHECK(ledger_calls == 0 && trace_count == 0 && lock_errors == 0 && !locked);
}

static void ledger_error(void)
{
    struct proc p = make("AegirPoster", 303, 30, false, 15, true, false);
    cur = &p;
    ledger_result = 5;
    native_memorystatus_gpu_client();
    CHECK(trace_count == 1 && strstr(trace_lines[0], " ledger error 5"));
    CHECK(p.p_memstat_memlimit_inactive == 512);
}

static void client_lines_capped(void)
{
    struct proc ps[60];
    for (int n = 0; n < 60; n++) {
        ps[n] = make("HelloPosterExtension", 400 + n, 30, false, 15, true, true);
        cur = &ps[n];
        native_memorystatus_gpu_client();
        CHECK(ps[n].p_memstat_memlimit_inactive == 512);
    }
    CHECK(trace_count == 48 && ledger_calls == 60);
}

static void runningboard_loop(void)
{
    /* -[RBProcess _lock_applyMemoryLimits] with preventBaseMemoryLimitReduction and no explicit limit sets the kernel's current
     * inactive limit again (MEMORYSTATUS_CMD_GET_MEMLIMIT_PROPERTIES, then SET): a floor must not ratchet. */
    struct proc p = make("MercuryPosterExtension", 500, 30, false, 15, true, true);
    cur = &p;
    native_memorystatus_gpu_client();
    for (int n = 0; n < 10; n++) {
        int32_t a = p.p_memstat_memlimit_inactive, i = p.p_memstat_memlimit_inactive;
        locked = 1;
        native_gpu_memlimit_floor_locked(&p, &a, &i);
        SET_ACTIVE_LIMITS_LOCKED(&p, a, false);
        SET_INACTIVE_LIMITS_LOCKED(&p, i, true);
        locked = 0;
    }
    CHECK(p.p_memstat_memlimit_active == 512 && p.p_memstat_memlimit_inactive == 512);
}

static void over_lines(void)
{
    struct proc p = make("iconservicesagent", 101, 20, false, 6, true, false);
    footprint_bytes = 41ull << 20;
    native_gpu_memlimit_trace_over(&p, 6, FALSE, TRUE);
    CHECK(!strcmp(trace_lines[0], "jetsam: iconservicesagent pid 101 over inactive hard limit 6 MB, footprint 41 MB"));
    p.p_memstat_state |= P_MEMSTAT_NATIVE_GPU_CLIENT;
    native_gpu_memlimit_trace_over(&p, 512, TRUE, FALSE);
    CHECK(!strcmp(trace_lines[1], "jetsam: iconservicesagent pid 101 over active soft limit 512 MB, footprint 41 MB, gpu client"));
    native_gpu_memlimit_trace_over(PROC_NULL, 6, FALSE, TRUE);
    CHECK(trace_count == 2);
    for (int n = 0; n < 100; n++) native_gpu_memlimit_trace_over(&p, 512, TRUE, FALSE);
    CHECK(trace_count == 64);
}

static void long_name(void)
{
    struct proc p = make("AmbientPhotoFramePosterProvider", 600, 30, false, 15, true, true);
    cur = &p;
    native_memorystatus_gpu_client();
    CHECK(strstr(trace_lines[0], "gpu client AmbientPhotoFramePosterProvider pid 600 ") != NULL);
}

static void no_trace(void)
{
    /* Without BootProgress (the weak reference null) everything works and nothing is written. */
    boot_progress_trace = NULL;
    struct proc p = make("iconservicesagent", 101, 20, false, 6, true, false);
    cur = &p;
    native_memorystatus_gpu_client();
    CHECK(p.p_memstat_memlimit_inactive == 512 && ledger_calls == 1);
    int32_t a = 30, i = 15;
    locked = 1;
    native_gpu_memlimit_floor_locked(&p, &a, &i);
    locked = 0;
    CHECK(a == 512 && i == 512);
    native_gpu_memlimit_trace_over(&p, 512, TRUE, FALSE);
    CHECK(trace_count == 0);
}

int main(int argc, char **argv)
{
    if (argc < 2) return 2;
    if (argc >= 3) {bootarg_present = 1; bootarg_value = (uint32_t)strtoul(argv[2], NULL, 0);}
    const char *t = argv[1];
    if (!strcmp(t, "floor_value")) floor_value();
    else if (!strcmp(t, "floored")) floored();
    else if (!strcmp(t, "floor_locked")) floor_locked_cases();
    else if (!strcmp(t, "floor_off")) floor_off();
    else if (!strcmp(t, "iconservicesagent")) iconservicesagent();
    else if (!strcmp(t, "active_now")) active_now();
    else if (!strcmp(t, "unlimited")) unlimited();
    else if (!strcmp(t, "refused")) refused();
    else if (!strcmp(t, "ledger_error")) ledger_error();
    else if (!strcmp(t, "client_lines_capped")) client_lines_capped();
    else if (!strcmp(t, "runningboard_loop")) runningboard_loop();
    else if (!strcmp(t, "over_lines")) over_lines();
    else if (!strcmp(t, "long_name")) long_name();
    else if (!strcmp(t, "no_trace")) no_trace();
    else return 2;
    if (lock_errors) {fprintf(stderr, "lock errors %d\n", lock_errors); failures++;}
    return failures ? 1 : 0;
}
'''


class PatchShape(unittest.TestCase):
    def setUp(self):
        self.text = PATCH.read_text()
        self.sections = file_sections(self.text)

    def test_files_and_pure_additions(self):
        self.assertEqual(re.findall(r'^--- a/(\S+)', self.text, re.M), FILES)
        self.assertEqual(sorted(self.sections), sorted(FILES))
        removed = [l for l in self.text.splitlines() if l.startswith('-') and not l.startswith('--- ')]
        self.assertEqual(removed, [])

    def test_floor_before_every_external_store(self):
        body = self.sections[FILES[0]]
        joined = '\n'.join(body)
        # memorystatus_update: after the inactive limit's normalisation, before the first SET.
        self.assertRegex(joined, r'@@ [^\n]*memorystatus_update\(proc_t p[^\n]*\n(?:[ +][^\n]*\n)*?'
                                 r' \t\t\tmemlimit_inactive_is_fatal = TRUE;\n \t\t}\n \n'
                                 r'\+\t\t/\* UniXNU: [^\n]*\*/\n\+\t\tnative_gpu_memlimit_floor_locked\(p, &memlimit_active, &memlimit_inactive\);\n')
        self.assertRegex(joined, r'@@ [^\n]*memorystatus_set_memlimit_properties_internal\(proc_t p[^\n]*\n(?: [^\n]*\n)*?'
                                 r' \tLCK_MTX_ASSERT\(&proc_list_mlock, LCK_MTX_ASSERT_OWNED\);\n \n\+[^\n]*\n'
                                 r'\+\tnative_gpu_memlimit_floor_locked\(p, &p_entry->memlimit_active, &p_entry->memlimit_inactive\);\n')
        self.assertRegex(joined, r'@@ [^\n]*memorystatus_log_exception\([^\n]*\n(?:[ ][^\n]*\n)+?'
                                 r'\+\tnative_gpu_memlimit_trace_over\(p, max_footprint_mb, memlimit_is_active, memlimit_is_fatal\);\n }')
        self.assertIn('+#define NATIVE_GPU_MEMLIMIT_DEFAULT_MB  512', body)
        self.assertIn('+\t\t(void)PE_parse_boot_argn("pagani-jetsam", &arg, sizeof(arg));', body)
        self.assertNotIn('panic(', joined)

    def test_iokit_hook_after_successful_open(self):
        joined = '\n'.join(self.sections[FILES[1]])
        self.assertIn('+extern "C" void native_memorystatus_gpu_client(void) __attribute__((weak_import));', joined)
        self.assertRegex(joined, r' \t\t\tclient->setTerminateDefer\(service, false\);\n'
                                 r'\+\t\t\tif \(native_memorystatus_gpu_client && service->getProperty\("MetalPluginName"\)\) \{\n'
                                 r'\+\t\t\t\tnative_memorystatus_gpu_client\(\);\n\+\t\t\t}\n \t\t}\n \t}while \(false\);')
        # Right after the failure branch's break: a refused open (the plugin's progress markers) never reaches the call.
        self.assertRegex(joined, r' \t\t\t\tbreak;\n \t\t\t}\n \t\t\tclient->setTerminateDefer\(service, false\);\n'
                                 r'\+\t\t\tif \(native_memorystatus_gpu_client')

    def test_flag_bit_is_free(self):
        if not XNU or not (XNU / 'bsd/sys/kern_memorystatus.h').is_file():
            self.skipTest('no XNU tree')
        header = (XNU / 'bsd/sys/kern_memorystatus.h').read_text()
        used = {int(v, 16) for v in re.findall(r'#define P_MEMSTAT_[A-Z_]+\s+(0x[0-9a-fA-F]+)', header)}
        self.assertNotIn(0x00400000, used)
        tree = (XNU / FILES[0]).read_text()
        self.assertNotIn('0x00400000', tree)


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        macros, cls.real_macros = tree_macros()
        block = added_block(file_sections(PATCH.read_text())[FILES[0]])
        decl = 'extern void boot_progress_trace(const char *text) __attribute__((weak_import));\n'
        if block.count(decl) != 1:
            raise AssertionError('the trace declaration moved')
        block = block.replace(decl, '')
        source = Path(cls.temp.name) / 'harness.c'
        source.write_text(HARNESS_HEAD + macros + block + HARNESS_TESTS)
        cls.binary = Path(cls.temp.name) / 'harness'
        flags = ['-std=gnu11', '-g', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                 '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        r = subprocess.run(['cc', *flags, str(source), '-o', str(cls.binary)], capture_output=True, text=True)
        if r.returncode:
            raise AssertionError('harness does not compile:\n' + r.stderr[-4000:])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_case(self, name, *arg):
        r = subprocess.run([str(self.binary), name, *arg], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f'{name} {arg}: {r.stdout}{r.stderr}')
        return r.stdout

    def test_boot_arg(self):
        for arg, floor in [(None, 512), ('0', 0), ('1', 512), ('2', 64), ('63', 64), ('64', 64), ('300', 300), ('768', 768),
                           ('4096', 4096), ('5000', 4096), ('0xffffffff', 4096)]:
            out = self.run_case('floor_value', *([arg] if arg is not None else []))
            self.assertEqual(out.strip(), f'floor {floor}', arg)

    def test_cases(self):
        for name in ['floored', 'floor_locked', 'iconservicesagent', 'active_now', 'unlimited', 'refused', 'ledger_error',
                     'client_lines_capped', 'runningboard_loop', 'over_lines', 'long_name', 'no_trace']:
            with self.subTest(name=name):
                self.run_case(name)

    def test_off(self):
        self.run_case('floor_off', '0')

    def test_real_macros_used(self):
        if XNU and (XNU / FILES[0]).is_file():
            self.assertTrue(self.real_macros)


def compile_record(rel):
    records = json.loads(RECORDS.read_text())
    return next(r for r in records if r['file'] == str(DEFAULT_XNU / rel))


def compile_diagnostics(record, path, rel):
    out = tempfile.mktemp(suffix='.o')
    args, skip = [], False
    for x in record['arguments']:
        if skip:
            skip = False
            continue
        if x == record['file']:
            args.append(str(path))
            continue
        if x == '-o':
            args += ['-o', out]
            skip = True
            continue
        if x.startswith('-MD') or x.startswith('-MF') or x == '-MMD':
            continue
        args.append(x)
    args += ['-iquote', str(DEFAULT_XNU / os.path.dirname(rel))]
    r = subprocess.run(args, cwd=record['directory'], capture_output=True, text=True)
    size = os.path.getsize(out) if os.path.exists(out) else 0
    if os.path.exists(out):
        os.unlink(out)
    return r.returncode, r.stderr, size


class Tree(unittest.TestCase):
    def setUp(self):
        if not XNU or not all((XNU / f).is_file() for f in FILES):
            self.skipTest('no XNU tree')
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'xnu'
        for f in FILES:
            (self.root / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(XNU / f, self.root / f)
        git = ['git', '-C', str(self.root), '-c', 'user.name=t', '-c', 'user.email=t@t']
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run([*git, 'add', '-A'], check=True)
        subprocess.run([*git, 'commit', '-q', '-m', 'base'], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_applies(self):
        r = subprocess.run(['git', '-C', str(self.root), 'apply', '--check', str(PATCH)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        subprocess.run(['git', '-C', str(self.root), 'apply', str(PATCH)], check=True)
        text = (self.root / FILES[0]).read_text()
        self.assertEqual(text.count('native_gpu_memlimit_floor_locked(p, '), 2)
        iokit = (self.root / FILES[1]).read_text()
        self.assertEqual(iokit.count('native_memorystatus_gpu_client();'), 1)
        # In is_io_service_open_extended, on the success path: after the failure branch (client released, break) and the client's
        # last setup step, before the function hands the connection out.
        body = iokit[iokit.index('\nis_io_service_open_extended('):iokit.index('\n/* Routine io_service_close */')]
        self.assertRegex(body, r'\t\t\t\tclient = NULL;\n\t\t\t\tbreak;\n\t\t\t}\n\t\t\tclient->setTerminateDefer\(service, false\);\n'
                               r'\t\t\tif \(native_memorystatus_gpu_client && service->getProperty\("MetalPluginName"\)\) \{\n'
                               r'\t\t\t\tnative_memorystatus_gpu_client\(\);\n\t\t\t}\n\t\t}\n\t}while \(false\);\n\n\t\*connection = client;')
        # A second application must fail (no silent double insert).
        r = subprocess.run(['git', '-C', str(self.root), 'apply', '--check', str(PATCH)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)

    def test_compiles_without_new_diagnostics(self):
        if not COMPILE:
            self.skipTest('--compile not given')
        if not RECORDS.is_file():
            self.skipTest('no compile records')
        subprocess.run(['git', '-C', str(self.root), 'apply', str(PATCH)], check=True)
        pattern = re.compile(r'^(.*?):(\d+):(\d+): (warning|error): (.*)$')
        for rel in FILES:
            with self.subTest(file=rel):
                record = compile_record(rel)
                base = Path(self.temp.name) / ('base-' + os.path.basename(rel))
                base.write_text((XNU / rel).read_text())
                rc0, e0, _ = compile_diagnostics(record, base, rel)
                rc1, e1, size = compile_diagnostics(record, self.root / rel, rel)
                self.assertEqual((rc0, rc1), (0, 0), e1[-3000:])
                self.assertGreater(size, 0)
                old = (XNU / rel).read_text().splitlines()
                new = (self.root / rel).read_text().splitlines()
                mapping, added = {}, set()
                for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
                    if tag == 'equal':
                        for k in range(j2 - j1):
                            mapping[j1 + k + 1] = i1 + k + 1
                    else:
                        added.update(range(j1 + 1, j2 + 1))

                def norm(stderr, path, remap):
                    out = []
                    for line in stderr.splitlines():
                        m = pattern.match(line)
                        if not m:
                            continue
                        where, number = m.group(1), int(m.group(2))
                        if Path(where).name == path.name and Path(where).parent == path.parent:
                            if remap and number in added:
                                out.append(('added', number, m.group(4), m.group(5)))
                                continue
                            number = mapping.get(number, -1) if remap else number
                            where = 'source'
                        else:
                            where = Path(where).name
                        out.append((where, number, m.group(4), m.group(5)))
                    return Counter(out)

                fresh = norm(e1, self.root / rel, True) - norm(e0, base, False)
                self.assertEqual(list(fresh.elements()), [])


def main():
    global XNU, COMPILE
    parser = argparse.ArgumentParser()
    parser.add_argument('--xnu', default=str(DEFAULT_XNU))
    parser.add_argument('--compile', action='store_true')
    args, rest = parser.parse_known_args()
    XNU = Path(args.xnu) if args.xnu and Path(args.xnu).is_dir() else None
    COMPILE = args.compile
    holder = None
    # The build's work tree carries the patch since the kernel of 2026-10-31: test against a copy of its files with the patch taken out.
    if XNU and (XNU / FILES[0]).is_file() and 'P_MEMSTAT_NATIVE_GPU_CLIENT' in (XNU / FILES[0]).read_text():
        holder = tempfile.TemporaryDirectory()
        root = Path(holder.name)
        for rel in [*FILES, 'bsd/sys/kern_memorystatus.h']:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(XNU / rel, root / rel)
        subprocess.run(['patch', '-p1', '-R', '--batch', '-s', '-i', str(PATCH)], cwd=root, check=True)
        XNU = root
    unittest.main(argv=[sys.argv[0], '-v', *rest])


if __name__ == '__main__':
    main()
