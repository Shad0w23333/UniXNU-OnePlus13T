/* SPDX-License-Identifier: BSD-2-Clause-Patent */
/* Host test of pagani_kbtrace.h (built by 工具/test-phone-keyboard-trace.py with -Werror and the kernel's warning set). */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "pagani_kbtrace.h"

static void test_kinds(void)
{
	/* The UIKit points of 21H461 (UIKitCore kdebug_trace sites, see the report) and their qualifier bits. */
	assert(pagani_kbt_kind(0x2bae006cu, 0) == PAGANI_KBT_DN);
	assert(pagani_kbt_kind(0x2bae006du, 0) == PAGANI_KBT_DN);         /* DBG_FUNC_START */
	assert(pagani_kbt_kind(0x2bae006eu, 0) == PAGANI_KBT_DN);         /* DBG_FUNC_END */
	assert(pagani_kbt_kind(0x2bae0070u, 0) == PAGANI_KBT_DN_DONE);
	assert(pagani_kbt_kind(0x2bae0074u, 0) == PAGANI_KBT_DR);
	assert(pagani_kbt_kind(0x2bae0078u, 0) == -1);                    /* dragged task end: not counted */
	assert(pagani_kbt_kind(0x2bae007cu, 0) == PAGANI_KBT_UP);
	assert(pagani_kbt_kind(0x2bae0080u, 0) == PAGANI_KBT_UP_DONE);
	assert(pagani_kbt_kind(0x2bae0024u, 0) == PAGANI_KBT_TASK);
	assert(pagani_kbt_kind(0x2bae0028u, 0) == PAGANI_KBT_TASK_RET);
	assert(pagani_kbt_kind(0x2bae0084u, 0) == PAGANI_KBT_IN);
	assert(pagani_kbt_kind(0x2bae0088u, 0) == PAGANI_KBT_IN_DONE);
	assert(pagani_kbt_kind(0x2bae00a4u, 0) == PAGANI_KBT_CAND);
	assert(pagani_kbt_kind(0x2bae00a8u, 0) == PAGANI_KBT_CAND_DONE);
	assert(pagani_kbt_kind(0x2bae001cu, 0) == PAGANI_KBT_HWKEY);
	assert(pagani_kbt_kind(0x2bae0010u, 0) == PAGANI_KBT_SET);
	assert(pagani_kbt_kind(0x2bae0004u, 0) == -1);
	/* sendEvent: arg1 = touches << 16 | subtype << 8 | type; only touches events (type 0) count. */
	assert(pagani_kbt_kind(0x2b8701acu, (1u << 16) | 0) == PAGANI_KBT_EV);
	assert(pagani_kbt_kind(0x2b8701acu, (0u << 16) | (3u << 8) | 3) == -1);
	assert(pagani_kbt_kind(0x2b8701acu, 0x0e) == -1);
	assert(pagani_kbt_kind(0x2b87010cu, 0) == -1);                     /* event fetcher ticks */
	assert(pagani_kbt_kind(0x01000000u, 0) == -1);
	assert(pagani_kbt_kind(0xffffffffu, 0) == -1);
}

static void test_first_and_summary(void)
{
	struct pagani_kbt t;
	memset(&t, 0, sizeof(t));
	char line[PAGANI_KBT_LINE];
	/* A tap on a key whose touch-down task never finishes (a stalled task queue). */
	assert(pagani_kbt_count(&t, 412, "MobileNotes", PAGANI_KBT_SET, 1000, line, sizeof(line)) == 1);
	assert(!strcmp(line, "kbt MobileNotes[412] first set @1000ms"));
	assert(pagani_kbt_count(&t, 412, "MobileNotes", PAGANI_KBT_EV, 2000, line, sizeof(line)) == 1);
	assert(pagani_kbt_count(&t, 412, "MobileNotes", PAGANI_KBT_EV, 2001, line, sizeof(line)) == 0);
	assert(pagani_kbt_count(&t, 412, "MobileNotes", PAGANI_KBT_DN, 2002, line, sizeof(line)) == 1);
	assert(!strcmp(line, "kbt MobileNotes[412] first dn @2002ms"));
	assert(pagani_kbt_count(&t, 412, "MobileNotes", PAGANI_KBT_TASK, 2003, line, sizeof(line)) == 1);
	assert(pagani_kbt_count(&t, 412, "MobileNotes", PAGANI_KBT_TASK_RET, 2004, line, sizeof(line)) == 1);
	assert(pagani_kbt_count(&t, 412, "MobileNotes", PAGANI_KBT_EV, 2100, line, sizeof(line)) == 0);
	assert(pagani_kbt_count(&t, 412, "MobileNotes", PAGANI_KBT_UP, 2101, line, sizeof(line)) == 1);
	assert(pagani_kbt_count(&t, 412, "MobileNotes", PAGANI_KBT_DN, 2500, line, sizeof(line)) == 0);
	int found = 0;
	for (unsigned i = 0; i < PAGANI_KBT_SLOTS; i++) {
		if (pagani_kbt_take_dirty(&t, i, line, sizeof(line))) {
			found++;
			assert(!strcmp(line, "kbt MobileNotes[412] sum ev 3 set 1 dn 2/0 dr 0 up 1/0 task 1/1 in 0/0 cand 0/0 hw 0"));
		}
	}
	assert(found == 1);
	for (unsigned i = 0; i < PAGANI_KBT_SLOTS; i++) {
		assert(!pagani_kbt_take_dirty(&t, i, line, sizeof(line)));     /* nothing new since */
	}
	assert(pagani_kbt_count(&t, 412, "MobileNotes", PAGANI_KBT_DN_DONE, 2600, line, sizeof(line)) == 1);
	assert(pagani_kbt_take_dirty(&t, 0, line, sizeof(line)) == 1);
	assert(strstr(line, " dn 2/1 "));
	assert(!pagani_kbt_take_dirty(&t, PAGANI_KBT_SLOTS, line, sizeof(line)));
}

static void test_slots(void)
{
	struct pagani_kbt t;
	memset(&t, 0, sizeof(t));
	char line[PAGANI_KBT_LINE];
	for (int pid = 100; pid < 100 + PAGANI_KBT_SLOTS; pid++) {
		assert(pagani_kbt_count(&t, pid, "p", PAGANI_KBT_EV, (uint64_t)pid, line, sizeof(line)) == 1);
	}
	assert(t.recycled == 0);
	/* pid 100 is used again, so 101 is the least recently used one and goes to the newcomer. */
	(void)pagani_kbt_count(&t, 100, "p", PAGANI_KBT_EV, 500, line, sizeof(line));
	assert(pagani_kbt_count(&t, 900, "SpringBoard", PAGANI_KBT_DN, 600, line, sizeof(line)) == 1);
	assert(t.recycled == 1);
	int has101 = 0, has100 = 0, has900 = 0;
	for (unsigned i = 0; i < PAGANI_KBT_SLOTS; i++) {
		has101 |= t.slot[i].pid == 101;
		has100 |= t.slot[i].pid == 100;
		has900 |= t.slot[i].pid == 900;
	}
	assert(!has101 && has100 && has900);
	/* A slot freed by memset in the middle is reused before any recycling. */
	struct pagani_kbt u;
	memset(&u, 0, sizeof(u));
	(void)pagani_kbt_count(&u, 1, "a", PAGANI_KBT_EV, 1, line, sizeof(line));
	(void)pagani_kbt_count(&u, 2, "b", PAGANI_KBT_EV, 2, line, sizeof(line));
	memset(&u.slot[0], 0, sizeof(u.slot[0]));
	(void)pagani_kbt_count(&u, 3, "c", PAGANI_KBT_EV, 3, line, sizeof(line));
	assert(u.slot[0].pid == 3 && u.slot[1].pid == 2 && u.recycled == 0);
	/* Invalid input is ignored. */
	assert(pagani_kbt_count(&u, 0, "x", PAGANI_KBT_EV, 4, line, sizeof(line)) == 0);
	assert(pagani_kbt_count(&u, 5, "x", -1, 4, line, sizeof(line)) == 0);
	assert(pagani_kbt_count(&u, 5, "x", PAGANI_KBT_KINDS, 4, line, sizeof(line)) == 0);
}

static void test_bounds(void)
{
	struct pagani_kbt t;
	memset(&t, 0, sizeof(t));
	char line[PAGANI_KBT_LINE];
	/* Long names are cut to 16 characters; a NULL name prints "?". */
	(void)pagani_kbt_count(&t, 7, "com.apple.WebKit.WebContent", PAGANI_KBT_EV, 1, line, sizeof(line));
	assert(!strcmp(t.slot[0].name, "com.apple.WebKit"));
	(void)pagani_kbt_count(&t, 8, NULL, PAGANI_KBT_EV, 1, line, sizeof(line));
	assert(!strncmp(line, "kbt ?[8] first ev", 17));
	/* The largest summary still fits the line, and tiny buffers stay terminated. */
	for (unsigned k = 0; k < PAGANI_KBT_KINDS; k++) {
		t.slot[0].n[k] = UINT32_MAX - 1;
	}
	(void)pagani_kbt_count(&t, 7, "x", PAGANI_KBT_EV, 2, line, sizeof(line));
	assert(t.slot[0].n[PAGANI_KBT_EV] == UINT32_MAX);
	(void)pagani_kbt_count(&t, 7, "x", PAGANI_KBT_EV, 3, line, sizeof(line));
	assert(t.slot[0].n[PAGANI_KBT_EV] == UINT32_MAX);                   /* saturates */
	size_t n = pagani_kbt_summary(&t.slot[0], line, sizeof(line));
	assert(n == strlen(line) && n < sizeof(line) - 1);
	printf("longest summary %zu bytes: %s\n", n, line);
	char tiny[8];
	memset(tiny, 'z', sizeof(tiny));
	n = pagani_kbt_summary(&t.slot[0], tiny, sizeof(tiny));
	assert(n == 7 && tiny[7] == '\0' && !strcmp(tiny, "kbt com"));
	char one[1] = {'z'};
	assert(pagani_kbt_summary(&t.slot[0], one, sizeof(one)) == 0 && one[0] == '\0');
}

int main(void)
{
	test_kinds();
	test_first_and_summary();
	test_slots();
	test_bounds();
	printf("pagani_kbtrace.h: all checks passed\n");
	return 0;
}
