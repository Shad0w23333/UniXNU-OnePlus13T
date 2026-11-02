/* SPDX-License-Identifier: BSD-2-Clause-Patent */
/*
 * Keyboard trace for the iPhone identity (boot-arg pagani-kbtrace=1, phone-keyboard-trace.patch).
 *
 * 21H461 UIKit marks every stage of a software-keyboard key press with a kdebug point. They are syscalls only while the
 * commpage says tracing is on and the shared typefilter allows their class/subclass; the patch allows exactly the two below
 * and counts them per process here. The counts tell where a tap on a key stops:
 *   ev   -[UIApplication sendEvent:] of a touches event                     (0x2b8701ac, arg1 bits 0..7 = event type 0)
 *   set  -[UIKeyboardSceneDelegate setKeyWindowSceneInputViews:...]         (0x2bae0010, the keyboard is being shown)
 *   dn   -[UIKeyboardLayout touchDown:]   (touch reached the key layout)     (0x2bae006c) / its task finished (0x2bae0070)
 *   dr   -[UIKeyboardLayout touchDragged:]                                   (0x2bae0074)
 *   up   -[UIKeyboardLayout touchUp:]                                        (0x2bae007c) / its task finished (0x2bae0080)
 *   tk   -[UIKeyboardTaskEntry execute:] begin (0x2bae0024) / task block returned (0x2bae0028)
 *   in   -[UIKeyboardImpl handleKeyboardInput:executionContext:] (0x2bae0084) / kbd answered (0x2bae0088)
 *   cq   -[UIKeyboardImpl generateCandidatesAsynchronously...] (0x2bae00a4) / candidates came back (0x2bae00a8)
 *   hk   -[UIKeyboardImpl _handleKeyEvent:executionContext:] (0x2bae001c, hardware key events)
 * Pure C without kernel dependencies so the host test compiles the same code (工具/test-phone-keyboard-trace.py).
 */
#ifndef PAGANI_KBTRACE_H
#define PAGANI_KBTRACE_H

#include <stddef.h>
#include <stdint.h>

#define PAGANI_KBT_CSC_KEYBOARD 0x2baeu /* class 0x2b (DBG_ARIADNE), UIKit keyboard subclass 0xae */
#define PAGANI_KBT_CSC_EVENTS   0x2b87u /* UIKit signpost subclass 0x87: sendEvent, also update cycle, animation, scrolling */
#define PAGANI_KBT_SLOTS        8
#define PAGANI_KBT_LINE         240     /* worst case (16-character name, every count at UINT32_MAX) is 222 */
#define PAGANI_KBT_FLUSH_MS     4000    /* a changed process is summarized this long after its first unreported event (the
                                         * events ring is 32 KiB: 600 ms filled it in about 80 s of typing) */

enum {
	PAGANI_KBT_EV, PAGANI_KBT_SET, PAGANI_KBT_DN, PAGANI_KBT_DN_DONE, PAGANI_KBT_DR, PAGANI_KBT_UP, PAGANI_KBT_UP_DONE,
	PAGANI_KBT_TASK, PAGANI_KBT_TASK_RET, PAGANI_KBT_IN, PAGANI_KBT_IN_DONE, PAGANI_KBT_CAND, PAGANI_KBT_CAND_DONE,
	PAGANI_KBT_HWKEY, PAGANI_KBT_KINDS
};

static const char *const pagani_kbt_kind_names[PAGANI_KBT_KINDS] = {
	"ev", "set", "dn", "dn-done", "dr", "up", "up-done", "task", "task-ret", "in", "in-done", "cand", "cand-done", "hwkey",
};

struct pagani_kbt_slot {
	int32_t  pid;                       /* 0: free */
	char     name[17];
	uint8_t  dirty;                     /* counts changed since the last summary */
	uint64_t used_ms;                   /* last event, for recycling the least recently used slot */
	uint32_t seen;                      /* kinds already announced (bit per kind) */
	uint32_t n[PAGANI_KBT_KINDS];
};

struct pagani_kbt {
	struct pagani_kbt_slot slot[PAGANI_KBT_SLOTS];
	uint32_t recycled;                  /* slots taken over from another process */
};

/* The counted kind of a kdebug point, or -1. The function qualifier bits (DBG_FUNC_START/END) are ignored. */
static inline int
pagani_kbt_kind(uint32_t code, uint64_t arg1)
{
	switch (code & ~3u) {
	case 0x2b8701acu: return (arg1 & 0xff) == 0 ? PAGANI_KBT_EV : -1;
	case 0x2bae0010u: return PAGANI_KBT_SET;
	case 0x2bae006cu: return PAGANI_KBT_DN;
	case 0x2bae0070u: return PAGANI_KBT_DN_DONE;
	case 0x2bae0074u: return PAGANI_KBT_DR;
	case 0x2bae007cu: return PAGANI_KBT_UP;
	case 0x2bae0080u: return PAGANI_KBT_UP_DONE;
	case 0x2bae0024u: return PAGANI_KBT_TASK;
	case 0x2bae0028u: return PAGANI_KBT_TASK_RET;
	case 0x2bae0084u: return PAGANI_KBT_IN;
	case 0x2bae0088u: return PAGANI_KBT_IN_DONE;
	case 0x2bae00a4u: return PAGANI_KBT_CAND;
	case 0x2bae00a8u: return PAGANI_KBT_CAND_DONE;
	case 0x2bae001cu: return PAGANI_KBT_HWKEY;
	default: return -1;
	}
}

/* Bounded appends; the line stays NUL-terminated however small it is. */
static inline size_t
pagani_kbt_put(char *line, size_t size, size_t at, const char *s)
{
	while (*s && at + 1 < size) {
		line[at++] = *s++;
	}
	if (size) {
		line[at < size ? at : size - 1] = '\0';
	}
	return at;
}

static inline size_t
pagani_kbt_put_u(char *line, size_t size, size_t at, uint64_t v)
{
	char digits[21];
	unsigned n = 0;
	do {
		digits[n++] = (char)('0' + v % 10);
		v /= 10;
	} while (v && n < sizeof(digits));
	char text[22];
	unsigned i = 0;
	while (n) {
		text[i++] = digits[--n];
	}
	text[i] = '\0';
	return pagani_kbt_put(line, size, at, text);
}

/* "kbt <name>[<pid>] <what>" header shared by both line kinds. */
static inline size_t
pagani_kbt_head(char *line, size_t size, const struct pagani_kbt_slot *s, const char *what)
{
	size_t at = pagani_kbt_put(line, size, 0, "kbt ");
	at = pagani_kbt_put(line, size, at, s->name[0] ? s->name : "?");
	at = pagani_kbt_put(line, size, at, "[");
	at = pagani_kbt_put_u(line, size, at, (uint64_t)(s->pid > 0 ? s->pid : 0));
	at = pagani_kbt_put(line, size, at, "] ");
	return pagani_kbt_put(line, size, at, what);
}

/* Cumulative counts of one process: "kbt Notes[412] ev 12 set 1 dn 3/2 dr 0 up 3/2 task 9/9 in 2/2 cand 1/1 hw 0". */
static inline size_t
pagani_kbt_summary(const struct pagani_kbt_slot *s, char *line, size_t size)
{
	static const struct { const char *label; int a, b; } fields[] = {
		{" ev ", PAGANI_KBT_EV, -1}, {" set ", PAGANI_KBT_SET, -1}, {" dn ", PAGANI_KBT_DN, PAGANI_KBT_DN_DONE},
		{" dr ", PAGANI_KBT_DR, -1}, {" up ", PAGANI_KBT_UP, PAGANI_KBT_UP_DONE},
		{" task ", PAGANI_KBT_TASK, PAGANI_KBT_TASK_RET}, {" in ", PAGANI_KBT_IN, PAGANI_KBT_IN_DONE},
		{" cand ", PAGANI_KBT_CAND, PAGANI_KBT_CAND_DONE}, {" hw ", PAGANI_KBT_HWKEY, -1},
	};
	size_t at = pagani_kbt_head(line, size, s, "sum");
	for (unsigned i = 0; i < sizeof(fields) / sizeof(fields[0]); i++) {
		at = pagani_kbt_put(line, size, at, fields[i].label);
		at = pagani_kbt_put_u(line, size, at, s->n[fields[i].a]);
		if (fields[i].b >= 0) {
			at = pagani_kbt_put(line, size, at, "/");
			at = pagani_kbt_put_u(line, size, at, s->n[fields[i].b]);
		}
	}
	return at;
}

/*
 * Counts one event of process pid. The first event of each kind in a process is announced at once (returns 1 with
 * "kbt Notes[412] first dn @523411ms" in line), so the order in which the stages are first reached survives; later events
 * only mark the process dirty for the next summary (pagani_kbt_take_dirty). Returns 0 when nothing is to be logged now.
 * A process that does not have a slot takes a free one, else the least recently used one (counted in recycled).
 */
static inline int
pagani_kbt_count(struct pagani_kbt *t, int32_t pid, const char *name, int kind, uint64_t now_ms, char *line, size_t size)
{
	if (kind < 0 || kind >= PAGANI_KBT_KINDS || pid <= 0) {
		return 0;
	}
	struct pagani_kbt_slot *s = NULL, *victim = NULL;
	for (unsigned i = 0; i < PAGANI_KBT_SLOTS; i++) {
		struct pagani_kbt_slot *c = &t->slot[i];
		if (c->pid == pid) {
			s = c;
			break;
		}
		if (c->pid == 0) {
			if (!victim || victim->pid != 0) {
				victim = c;     /* the first free slot beats any used one */
			}
		} else if (!victim || (victim->pid != 0 && c->used_ms < victim->used_ms)) {
			victim = c;             /* else the least recently used one */
		}
	}
	if (!s) {
		s = victim;
		if (s->pid != 0) {
			t->recycled++;
		}
		*s = (struct pagani_kbt_slot){ .pid = pid };
		for (unsigned i = 0; name && name[i] && i + 1 < sizeof(s->name); i++) {
			s->name[i] = name[i];
		}
	}
	s->used_ms = now_ms;
	if (s->n[kind] != UINT32_MAX) {
		s->n[kind]++;
	}
	s->dirty = 1;
	if (s->seen & (1u << kind)) {
		return 0;
	}
	s->seen |= 1u << kind;
	size_t at = pagani_kbt_head(line, size, s, "first ");
	at = pagani_kbt_put(line, size, at, pagani_kbt_kind_names[kind]);
	at = pagani_kbt_put(line, size, at, " @");
	at = pagani_kbt_put_u(line, size, at, now_ms);
	(void)pagani_kbt_put(line, size, at, "ms");
	return 1;
}

/* The summary of slot i when it changed since its last summary (returns 1 and clears the mark), else 0. */
static inline int
pagani_kbt_take_dirty(struct pagani_kbt *t, unsigned i, char *line, size_t size)
{
	if (i >= PAGANI_KBT_SLOTS || !t->slot[i].pid || !t->slot[i].dirty) {
		return 0;
	}
	t->slot[i].dirty = 0;
	pagani_kbt_summary(&t->slot[i], line, size);
	return 1;
}

#endif /* PAGANI_KBTRACE_H */
