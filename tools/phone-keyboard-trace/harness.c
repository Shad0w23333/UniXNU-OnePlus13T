/* SPDX-License-Identifier: BSD-2-Clause-Patent */
/*
 * Host harness for the kdebug.c part of phone-keyboard-trace.patch. test-phone-keyboard-trace.py applies the patch to a
 * pristine xnu-10063.141.1 bsd/kern/kdebug.c, cuts out the patched functions (the declaration block, kdebug_trace64,
 * kdebug_commpage_state, kdebug_init, pagani_kbtrace_flush, pagani_kbtrace_note) into patched_parts.inc, and compiles them
 * here against stubs of the kernel interfaces they call. The scenario below then drives them like the device would.
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* ---- kernel interfaces used by the patched code ---- */
#define __improbable(x) __builtin_expect(!!(x), 0)
#define __probable(x)   __builtin_expect(!!(x), 1)
#ifndef __unused
#define __unused        __attribute__((unused))
#endif
#define EPERM 1
#define NSEC_PER_MSEC 1000000ull
#define MACH_PORT_NULL 0
#define DBG_MACH  1
#define DBG_TRACE 7
#define KDBG_EVENTID(c, s, code) ((((uint32_t)(c) & 0xff) << 24) | (((uint32_t)(s) & 0xff) << 16) | (((uint32_t)(code) & 0x3fff) << 2))
#define KDBG_CODE(c, s, code) KDBG_EVENTID(c, s, code)
#define KDBG_EXTRACT_CSC(id) (((id) >> 16) & 0xffff)
#define KDEBUG_COMMPAGE_ENABLE_TRACE      0x1
#define KDEBUG_COMMPAGE_ENABLE_TYPEFILTER 0x2
#define KDEBUG_COMMPAGE_CONTINUOUS        0x4
#define KDBG_TYPEFILTER_CHECK 0x00400000
#define KDBG_CONTINUOUS_TIME  0x00000010
#define THREAD_CALL_PRIORITY_KERNEL 1
#define MPSC_DAEMON_INIT_NONE 0
#define setbit(a, i) ((a)[(i) / 8] |= (uint8_t)(1u << ((i) % 8)))
#define isset(a, i)  ((a)[(i) / 8] & (1u << ((i) % 8)))

typedef uint8_t *typefilter_t;
typedef int mach_port_t;
enum kdebug_opts { KDOPT_NONE = 0 };
struct proc { int pid; const char *name; };
struct kdebug_trace64_args { uint32_t code; uint64_t arg1, arg2, arg3, arg4; };
typedef void *thread_call_param_t;
typedef void (*thread_call_func_t)(thread_call_param_t, thread_call_param_t);
typedef struct { thread_call_func_t func; int entered; uint64_t deadline; } thread_call_data_t;
typedef thread_call_data_t *thread_call_t;
typedef struct { int unused; } lck_grp_t;
typedef struct { int held; } lck_spin_t;

static uint32_t kdebug_enable;
static struct { uint32_t kdc_flags; } kd_control_trace;
static uint8_t typefilter_bits[8192];
static typefilter_t kdbg_typefilter;
static mach_port_t kdbg_typefilter_memory_entry;
static int _coproc_notify_queue, commpage_updates, recorded, spins;
static lck_grp_t kdebug_lck_grp;
static const char *boot_arg_kbtrace;   /* value of pagani-kbtrace, NULL when absent */
static uint64_t abs_now = 1000000000ull;

static typefilter_t typefilter_create(void) { return typefilter_bits; }
static mach_port_t typefilter_create_memory_entry(typefilter_t tf) { return tf ? 7 : 0; }
static void typefilter_reject_all(typefilter_t tf) { memset(tf, 0, sizeof(typefilter_bits)); }
static void typefilter_allow_all(typefilter_t tf) { memset(tf, 0xff, sizeof(typefilter_bits)); }
static void typefilter_allow_class(typefilter_t tf, uint8_t c) { memset(&tf[c * 32], 0xff, 32); }
static void typefilter_allow_csc(typefilter_t tf, uint16_t csc) { setbit(tf, csc); }
static bool typefilter_is_debugid_allowed(typefilter_t tf, uint32_t id) { return isset(tf, KDBG_EXTRACT_CSC(id)); }
static void _deferred_coproc_notify(void) {}
static int mpsc_daemon_queue_init_with_thread_call(int *q, void (*f)(void), int pri, int flags)
{ (void)q; (void)f; (void)pri; (void)flags; return 0; }
static void kdebug_trace_start(unsigned int n, char *desc, enum kdebug_opts o) { (void)n; (void)desc; (void)o; }
static void commpage_update_kdebug_state(void) { commpage_updates++; }
static void ktrace_assert_lock_held(void) {}
static void kdbg_clear(void) {}
static int _kernel_only_event(uint32_t id) { return (id >> 24) == DBG_TRACE; }
static void kernel_debug_internal(uint32_t id, uintptr_t a, uintptr_t b, uintptr_t c, uintptr_t d, uintptr_t t, int f)
{ (void)id; (void)a; (void)b; (void)c; (void)d; (void)t; (void)f; recorded++; }
static uintptr_t thread_tid(void *t) { (void)t; return 1; }
static void *current_thread(void) { return NULL; }
static int PE_parse_boot_argn(const char *name, void *out, int size)
{
	if (strcmp(name, "pagani-kbtrace") || !boot_arg_kbtrace || size != 4) return 0;
	uint32_t v = 0;
	for (const char *s = boot_arg_kbtrace; *s; s++) v = v * 10 + (uint32_t)(*s - '0');
	memcpy(out, &v, 4);
	return 1;
}
static uint64_t mach_absolute_time(void) { return abs_now; }
static void absolutetime_to_nanoseconds(uint64_t a, uint64_t *ns) { *ns = a * 125 / 3; }   /* 24 MHz timebase */
static void nanoseconds_to_absolutetime(uint64_t ns, uint64_t *a) { *a = ns * 3 / 125; }
static int proc_pid(struct proc *p) { return p->pid; }
static char *proc_best_name(struct proc *p) { return (char *)p->name; }
static void lck_spin_lock_grp(lck_spin_t *l, lck_grp_t *g) { (void)g; assert(!l->held); l->held = 1; spins++; }
static void lck_spin_unlock(lck_spin_t *l) { assert(l->held); l->held = 0; }
#define LCK_SPIN_DECLARE(var, grp) lck_spin_t var
static thread_call_data_t *setup_call;
static int setups;
static thread_call_data_t allocated_call;
/* The patch allocates its call (thread_call_data_t is MACH_KERNEL_PRIVATE, out of reach of bsd/kern/kdebug.c). */
static thread_call_t thread_call_allocate(thread_call_func_t f, thread_call_param_t p)
{ (void)p; allocated_call.func = f; allocated_call.entered = 0; setup_call = &allocated_call; setups++; return &allocated_call; }
static int thread_call_enter_delayed(thread_call_data_t *c, uint64_t deadline)
{ assert(c->func); c->entered++; c->deadline = deadline; return 0; }
#define os_atomic_load(p, m) __atomic_load_n(p, __ATOMIC_SEQ_CST)
#define os_atomic_store(p, v, m) __atomic_store_n(p, v, __ATOMIC_SEQ_CST)
#define os_atomic_cmpxchg(p, e, v, m) ({ __typeof__(*(p)) _e = (e); __atomic_compare_exchange_n(p, &_e, v, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); })

static char lines[64][256];
static int nlines;
/* Weak like the kernel's weak_import declaration, so the patched "if (boot_progress_trace)" compiles the same way. */
void boot_progress_trace(const char *text) __attribute__((weak));
void boot_progress_trace(const char *text)
{
	assert(nlines < 64);
	snprintf(lines[nlines++], sizeof(lines[0]), "%s", text);
}

/* ---- the patched code ---- */
#include "patched_parts.inc"

/* ---- scenario ---- */
static int syscall(struct proc *p, uint32_t code, uint64_t arg1)
{
	struct kdebug_trace64_args a = { code, arg1, 0, 0, 0 };
	int32_t rv = 0;
	return kdebug_trace64(p, &a, &rv);
}

static int has_line(const char *text)
{
	for (int i = 0; i < nlines; i++) if (!strcmp(lines[i], text)) return 1;
	return 0;
}

int main(void)
{
	/* Off: no boot-arg, the commpage stays 0 and the syscall does not count. */
	kdebug_init(0, "", KDOPT_NONE);
	assert(!pagani_kbtrace_on && kdebug_commpage_state() == 0 && commpage_updates == 0);
	struct proc notes = { 412, "MobileNotes" }, sb = { 34, "SpringBoard" };
	assert(syscall(&notes, 0x2bae006c, 0) == 0 && nlines == 0 && spins == 0 && recorded == 0);

	/* pagani-kbtrace=2 is not 1: still off. */
	boot_arg_kbtrace = "2";
	kdebug_init(0, "", KDOPT_NONE);
	assert(!pagani_kbtrace_on && kdebug_commpage_state() == 0);

	/* 1: DBG_TRACE and the keyboard subclass only (0x2b87, UIKit's signposts, would cost every animating app a dozen syscalls a frame). */
	boot_arg_kbtrace = "1";
	kdebug_init(0, "", KDOPT_NONE);
	assert(pagani_kbtrace_on && !pagani_kbtrace_signposts && commpage_updates == 1);
	assert(typefilter_is_debugid_allowed(kdbg_typefilter, 0x2bae006c) && !typefilter_is_debugid_allowed(kdbg_typefilter, 0x2b8701ac));

	/* 3: also the signpost subclass; the commpage says trace + typefilter. */
	boot_arg_kbtrace = "3";
	kdebug_init(0, "", KDOPT_NONE);
	assert(pagani_kbtrace_on && pagani_kbtrace_signposts && commpage_updates == 2);
	assert(kdebug_commpage_state() == (KDEBUG_COMMPAGE_ENABLE_TRACE | KDEBUG_COMMPAGE_ENABLE_TYPEFILTER));
	assert(typefilter_is_debugid_allowed(kdbg_typefilter, 0x2bae006c) && typefilter_is_debugid_allowed(kdbg_typefilter, 0x2b8701ac));
	assert(typefilter_is_debugid_allowed(kdbg_typefilter, KDBG_CODE(DBG_TRACE, 1, 0)));
	assert(!typefilter_is_debugid_allowed(kdbg_typefilter, 0x2bad0000) && !typefilter_is_debugid_allowed(kdbg_typefilter, 0x01000000));
	assert(setups == 0);     /* the thread call is set up at the first point, not at boot */

	/* A key tap in Notes whose touch-down task never finishes. */
	assert(syscall(&notes, 0x2b8701ac, (1u << 16) | 0) == 0);           /* touches event */
	assert(setups == 1 && setup_call && setup_call->entered == 1);
	uint64_t deadline = setup_call->deadline;
	assert(deadline - abs_now == (uint64_t)PAGANI_KBT_FLUSH_MS * NSEC_PER_MSEC * 3 / 125);
	assert(syscall(&notes, 0x2b8701ac, (3u << 8) | 3) == 0);            /* a presses event: not counted */
	assert(syscall(&notes, 0x2bae006c, 0) == 0);
	assert(syscall(&notes, 0x2bae0024, 0) == 0);
	assert(syscall(&notes, 0x2bae0028, 0) == 0);
	assert(syscall(&notes, 0x2b8701ac, (1u << 16)) == 0);
	assert(syscall(&notes, 0x2bae007c, 0) == 0);
	assert(syscall(&sb, 0x2b8701ac, (1u << 16)) == 0);
	assert(syscall(&notes, 0x2bad0000, 0) == 0);                        /* not ours */
	assert(setup_call->entered == 1);   /* one pending summary only */
	assert(has_line("kbt MobileNotes[412] first ev @41666ms"));
	assert(has_line("kbt MobileNotes[412] first dn @41666ms"));
	assert(has_line("kbt MobileNotes[412] first task @41666ms"));
	assert(has_line("kbt MobileNotes[412] first task-ret @41666ms"));
	assert(has_line("kbt MobileNotes[412] first up @41666ms"));
	assert(has_line("kbt SpringBoard[34] first ev @41666ms"));
	int before = nlines;
	setup_call->func(NULL, NULL);       /* the thread call fires */
	assert(nlines == before + 2);
	assert(has_line("kbt MobileNotes[412] sum ev 2 set 0 dn 1/0 dr 0 up 1/0 task 1/1 in 0/0 cand 0/0 hw 0"));
	assert(has_line("kbt SpringBoard[34] sum ev 1 set 0 dn 0/0 dr 0 up 0/0 task 0/0 in 0/0 cand 0/0 hw 0"));
	/* The next point schedules a new summary. */
	abs_now += 24000000;
	assert(syscall(&notes, 0x2bae0070, 0) == 0);
	assert(setup_call->entered == 2);
	setup_call->func(NULL, NULL);
	assert(has_line("kbt MobileNotes[412] sum ev 2 set 0 dn 1/1 dr 0 up 1/0 task 1/1 in 0/0 cand 0/0 hw 0"));

	/* kdebug proper still works as before when someone enables it. */
	kdebug_enable = 1;
	assert(syscall(&notes, 0x2bae0084, 0) == 0 && recorded == 1);
	assert(syscall(&notes, KDBG_CODE(DBG_TRACE, 1, 0), 0) == EPERM);
	assert(kdebug_commpage_state() == KDEBUG_COMMPAGE_ENABLE_TRACE);
	kdebug_enable = 0;

	/* A ktrace session's end (kdebug_reset) leaves DBG_TRACE alone in the shared filter: the keyboard points are opened again. */
	int updates = commpage_updates;
	kdebug_reset();
	assert(typefilter_is_debugid_allowed(kdbg_typefilter, 0x2bae006c) && typefilter_is_debugid_allowed(kdbg_typefilter, 0x2b8701ac));
	assert(commpage_updates == updates + 1);
	assert(kdebug_commpage_state() == (KDEBUG_COMMPAGE_ENABLE_TRACE | KDEBUG_COMMPAGE_ENABLE_TYPEFILTER));

	/* A ktrace reset opens the typefilter to everything: the commpage state drops to 0 instead of flooding syscalls. */
	typefilter_allow_all(kdbg_typefilter);
	assert(kdebug_commpage_state() == 0);
	printf("phone-keyboard-trace kdebug.c parts: all checks passed (%d log lines)\n", nlines);
	for (int i = 0; i < nlines; i++) printf("  U %s\n", lines[i]);
	return 0;
}
