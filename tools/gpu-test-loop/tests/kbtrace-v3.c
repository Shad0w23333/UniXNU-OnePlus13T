// SPDX-License-Identifier: BSD-2-Clause-Patent
// The keyboard trace's version 3 (phone-stall-probe.patch on phone-keyboard-trace.patch): pairing by number after a queue overflow, repeated touchUp:
// by the touch's timestamp and the up-done of each, long pairs with their begin and end, the last counts of a slot taken over, the stall probe's
// groups, and that probe's table of open pairs fed as kdebug.c feeds it (StallProbe.h): it must always equal the trace's queues, and empty with them.
// Built by tests/test_stall.py against the header the two patches give.
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "pagani_kbtrace.h"
#include "StallProbe.h"

enum { HZ = 19200000 };

static struct pagani_kbt t;
static struct stall_kbt probe;
static char line[PAGANI_KBT_LINE];

// What pagani_kbtrace_note does with an event.
static void event(int32_t pid, const char *name, int kind, uint64_t arg2, uint64_t us)
{
    (void)pagani_kbt_count(&t, pid, name, kind, arg2, us, line, sizeof(line));
    int end = 0;
    const int group = pagani_kbt_stall_group(kind, &end);
    if (t.follow && group >= 0) stall_kbt_note(&probe, pid, (unsigned)group, end, us * (HZ / 1000000), HZ);
}

static void reset(void)
{
    memset(&t, 0, sizeof(t));
    memset(&probe, 0, sizeof(probe));
}

static uint32_t probe_open(int32_t pid, unsigned group)
{
    const struct stall_kbt_open *s = stall_kbt_find(&probe, pid, group);
    return s ? s->open : 0;
}

// A keyboard with its task queue stuck and let go at random, as on the phone: every touch has dn and up; a new touch commits again every touch not
// yet committed, a repeated up each, also with a task. Tasks run in order; an up's first task commits its touch. Whatever overflows, the probe's
// open pairs stay the trace's queues, and in the end all are closed: no pair is left to be sampled for two minutes.
static void random_keyboard(uint32_t seed, unsigned steps)
{
    enum { TASKS = 8192, TOUCHES = 60 };     // fewer than PAGANI_KBT_UP_SEEN: every repeat is known as one
    static struct { int kind; uint32_t touch; } tasks[TASKS];
    static uint8_t committed[TOUCHES];
    unsigned head = 0, tail = 0;
    uint32_t touches = 0, fresh = 0, repeats = 0;
    uint64_t us = 1000;
    reset();
    memset(committed, 0, sizeof(committed));
    const int32_t pid = 448;
    const struct pagani_kbt_lat *lat[2] = {NULL, NULL};
    for (unsigned step = 0; step < steps || head != tail; step++) {
        seed = seed * 1664525u + 1013904223u;
        us += 1000 + (seed >> 28) * 1000;
        if (step < steps && (seed >> 16) % 3 == 0 && touches < TOUCHES && tail - head + touches + 2 < TASKS) {
            const uint32_t touch = touches++;
            event(pid, "Notes", PAGANI_KBT_DN, 0, us);
            tasks[tail++ % TASKS].kind = PAGANI_KBT_DN_DONE;
            for (uint32_t older = 0; older < touch; older++) {
                if (committed[older]) continue;
                event(pid, "Notes", PAGANI_KBT_UP, 0x41d0000000000000ULL + older, us);
                repeats++;
                tasks[tail % TASKS].kind = PAGANI_KBT_UP_DONE;
                tasks[tail++ % TASKS].touch = older;
            }
            event(pid, "Notes", PAGANI_KBT_UP, 0x41d0000000000000ULL + touch, us);
            fresh++;
            tasks[tail % TASKS].kind = PAGANI_KBT_UP_DONE;
            tasks[tail++ % TASKS].touch = touch;
        } else if (head != tail) {
            const int kind = tasks[head % TASKS].kind;
            const uint32_t touch = tasks[head++ % TASKS].touch;
            if (kind == PAGANI_KBT_UP_DONE) committed[touch] = 1;
            event(pid, "Notes", kind, 0, us);
        }
        if (!lat[0] && t.slot[0].pid) {
            lat[0] = &t.slot[0].lat[PAGANI_KBT_LAT_DN];
            lat[1] = &t.slot[0].lat[PAGANI_KBT_LAT_UP];
        }
        if (lat[0]) {
            assert(probe_open(pid, 0) == lat[0]->queued);
            assert(probe_open(pid, 1) == lat[1]->queued);
        }
    }
    assert(!lat[0] || (!lat[0]->queued && !lat[1]->queued && lat[1]->began == lat[1]->ended));
    assert(!stall_kbt_any(&probe));
    if (lat[0]) assert(lat[1]->n + lat[1]->lost == fresh && t.slot[0].up_dups == repeats && lat[1]->began == fresh + repeats);
}

int main(void)
{
    // A queue that overflowed: the ends of the begins pushed out pair with nothing, the others with their own begin.
    reset();
    for (int i = 0; i < PAGANI_KBT_RING + 2; i++) event(1, "a", PAGANI_KBT_IN, 0, (uint64_t)(i + 1) * 1000);
    struct pagani_kbt_lat *l = &t.slot[0].lat[PAGANI_KBT_LAT_IN];
    assert(l->lost == 2 && l->queued == PAGANI_KBT_RING);
    event(1, "a", PAGANI_KBT_IN_DONE, 0, 100000);       // begin 0, gone
    event(1, "a", PAGANI_KBT_IN_DONE, 0, 100000);       // begin 1, gone
    assert(l->n == 0 && l->lost == 2 && l->queued == PAGANI_KBT_RING);
    event(1, "a", PAGANI_KBT_IN_DONE, 0, 100000);       // begin 2, at 3 ms: not begin 0's 1 ms, and not a wrong neighbour
    assert(l->n == 1 && l->sum_us == 100000 - 3000 && l->queued == PAGANI_KBT_RING - 1);
    for (int i = 0; i < PAGANI_KBT_RING - 1; i++) event(1, "a", PAGANI_KBT_IN_DONE, 0, 100000);
    assert(l->n == PAGANI_KBT_RING && !l->queued);
    event(1, "a", PAGANI_KBT_IN_DONE, 0, 100000);       // an end without a begin
    assert(l->lost == 3 && l->n == PAGANI_KBT_RING);
    event(1, "a", PAGANI_KBT_IN, 0, 200000);
    event(1, "a", PAGANI_KBT_IN_DONE, 0, 200500);       // and the numbering goes on from there
    assert(l->n == PAGANI_KBT_RING + 1 && l->max_us >= 500);

    // Repeated touchUp: the same timestamp again counts in up and dup, takes no place in the queue and tells the stall probe nothing, but its number.
    reset();
    event(2, "b", PAGANI_KBT_UP, 0x41d0000000000001ULL, 1000);
    assert(t.follow == 1);
    for (int i = 0; i < 5; i++) {
        event(2, "b", PAGANI_KBT_UP, 0x41d0000000000001ULL, 2000);
        assert(t.follow == 0);
    }
    event(2, "b", PAGANI_KBT_UP, 0x41d0000000000002ULL, 3000);
    assert(t.follow == 1);
    event(2, "b", PAGANI_KBT_UP, 0, 3000);              // no timestamp: never a repeat
    event(2, "b", PAGANI_KBT_UP, 0, 3000);
    assert(t.follow == 1);
    struct pagani_kbt_slot *s = &t.slot[0];
    l = &s->lat[PAGANI_KBT_LAT_UP];
    assert(s->n[PAGANI_KBT_UP] == 9 && s->up_dups == 5 && l->queued == 4 && l->began == 9 && probe_open(2, 1) == 4);
    (void)pagani_kbt_take_dirty(&t, 0, line, sizeof(line));
    assert(strstr(line, " up 9/0 ") && strstr(line, " dup 5"));
    // Each has its up-done, in the queue's order: the first pairs with the touch, the five of the repeats with nothing, the next with the next touch.
    event(2, "b", PAGANI_KBT_UP_DONE, 0, 4000);
    assert(t.follow == 1 && l->n == 1 && l->max_us == 3000 && probe_open(2, 1) == 3);
    for (int i = 0; i < 5; i++) {
        event(2, "b", PAGANI_KBT_UP_DONE, 0, 4100);
        assert(t.follow == 0 && l->n == 1 && !l->lost);
    }
    event(2, "b", PAGANI_KBT_UP_DONE, 0, 5000);
    assert(t.follow == 1 && l->n == 2 && l->sum_us == 3000 + 2000 && probe_open(2, 1) == 2);
    // The table of timestamps turns over: an old timestamp is new again.
    for (uint64_t i = 0; i < PAGANI_KBT_UP_SEEN; i++) event(2, "b", PAGANI_KBT_UP, 0x1000 + i, 5000);
    const uint32_t dups = s->up_dups;
    event(2, "b", PAGANI_KBT_UP, 0x41d0000000000001ULL, 6000);
    assert(s->up_dups == dups);

    // On the phone (seq 8401: dn 20, up 301/301): touch A's up, A repeated when B comes, B's up; the queue stuck 3 s on A, then 5 s more on B. Its ends
    // come A, A's repeat, B: B lasts 7.5 s, nothing is lost, and its pair stays open for the probe until B's own end.
    reset();
    event(3, "MobileNotes", PAGANI_KBT_UP, 0x1111, 1000000);
    event(3, "MobileNotes", PAGANI_KBT_UP, 0x1111, 1500000);
    event(3, "MobileNotes", PAGANI_KBT_UP, 0x2222, 1500000);
    assert(probe_open(3, 1) == 2);
    event(3, "MobileNotes", PAGANI_KBT_UP_DONE, 0, 4000000);
    assert(probe_open(3, 1) == 1);
    event(3, "MobileNotes", PAGANI_KBT_UP_DONE, 0, 4001000);
    assert(probe_open(3, 1) == 1);                      // the repeat's end closes nothing
    event(3, "MobileNotes", PAGANI_KBT_UP_DONE, 0, 9000000);
    l = &t.slot[0].lat[PAGANI_KBT_LAT_UP];
    assert(!probe_open(3, 1) && !stall_kbt_any(&probe));
    assert(l->n == 2 && l->max_us == 7500000 && !l->lost && !l->queued);
    assert(pagani_kbt_take_long(&t, line, sizeof(line)) && !strcmp(line, "kbt MobileNotes[3] long up b=1000 e=4000 d=3000"));
    assert(pagani_kbt_take_long(&t, line, sizeof(line)) && !strcmp(line, "kbt MobileNotes[3] long up b=1500 e=9000 d=7500"));
    assert(!pagani_kbt_take_long(&t, line, sizeof(line)));

    // More touches open than the queue holds: the probe stays with the queue, and is closed when the last one is.
    reset();
    for (uint64_t i = 0; i < PAGANI_KBT_RING + 3; i++) {
        event(4, "c", PAGANI_KBT_UP, 0x100 + i, 1000 * (i + 1));
        assert(t.follow == (i < PAGANI_KBT_RING));
    }
    l = &t.slot[0].lat[PAGANI_KBT_LAT_UP];
    assert(l->lost == 3 && probe_open(4, 1) == PAGANI_KBT_RING);
    for (int i = 0; i < PAGANI_KBT_RING + 3; i++) {
        event(4, "c", PAGANI_KBT_UP_DONE, 0, 100000);
        assert(t.follow == (i >= 3) && probe_open(4, 1) == l->queued);
    }
    assert(!stall_kbt_any(&probe) && l->n == PAGANI_KBT_RING && !l->queued);

    // A slot taken over forgets its begins, the probe does not: the ends that come now close the pair there.
    reset();
    event(5, "d", PAGANI_KBT_IN, 0, 1000);
    for (int32_t p = 100; p < 100 + PAGANI_KBT_SLOTS; p++) event(p, "e", PAGANI_KBT_SET, 0, 2000 + (uint64_t)p * 1000000);
    assert(t.recycled == 1 && probe_open(5, 2) == 1);
    event(5, "d", PAGANI_KBT_IN_DONE, 0, 900000000);
    assert(t.follow == 1 && !probe_open(5, 2));
    // Numbers that wrap: they are only compared.
    reset();
    event(6, "f", PAGANI_KBT_IN, 0, 1000);
    l = &t.slot[0].lat[PAGANI_KBT_LAT_IN];
    l->ended = UINT32_MAX - 1;
    l->number[l->head] = UINT32_MAX - 1;
    l->began = UINT32_MAX;
    event(6, "f", PAGANI_KBT_IN, 0, 2000);
    event(6, "f", PAGANI_KBT_IN, 0, 3000);
    assert(l->began == 1);
    for (uint64_t i = 0; i < 3; i++) event(6, "f", PAGANI_KBT_IN_DONE, 0, 4000 + i * 1000);
    assert(l->n == 3 && !l->lost && !l->queued && l->max_us == 3000 && !stall_kbt_any(&probe));

    // All of it at random.
    for (uint32_t seed = 1; seed <= 200; seed++) random_keyboard(seed * 2654435761u, 300);

    // Pairs from a second on: begin and end, logged at the next summary; what does not fit is counted.
    reset();
    event(3, "MobileNotes", PAGANI_KBT_IN, 0, 829669000);
    event(3, "MobileNotes", PAGANI_KBT_IN_DONE, 0, 832806000);
    event(3, "MobileNotes", PAGANI_KBT_IN, 0, 833000000);
    event(3, "MobileNotes", PAGANI_KBT_IN_DONE, 0, 833999000);
    assert(t.long_count == 1);
    assert(pagani_kbt_take_long(&t, line, sizeof(line)));
    assert(!strcmp(line, "kbt MobileNotes[3] long in b=829669 e=832806 d=3137"));
    assert(!pagani_kbt_take_long(&t, line, sizeof(line)));
    for (uint64_t i = 0; i < PAGANI_KBT_LONGS + 2; i++) {
        event(3, "MobileNotes", PAGANI_KBT_DN, 0, i * 10000000);
        event(3, "MobileNotes", PAGANI_KBT_DN_DONE, 0, i * 10000000 + 2000000);
    }
    assert(t.long_count == PAGANI_KBT_LONGS && t.long_lost == 2);
    for (int i = 0; i < PAGANI_KBT_LONGS; i++) assert(pagani_kbt_take_long(&t, line, sizeof(line)) && strstr(line, " long dn "));
    assert(pagani_kbt_take_long(&t, line, sizeof(line)) && !strcmp(line, "kbt long lost 2"));
    assert(!pagani_kbt_take_long(&t, line, sizeof(line)));

    // A slot taken over: its last counts wait for the summary, sum then lat, in that order.
    reset();
    assert(!pagani_kbt_take_final(&t, line, sizeof(line)));
    for (int32_t p = 1; p <= PAGANI_KBT_SLOTS; p++) event(p, "app", PAGANI_KBT_IN, 0, (uint64_t)p * 1000000);
    event(1, "app", PAGANI_KBT_IN_DONE, 0, 1001000 * 20);
    event(2, "app", PAGANI_KBT_EV, 0, 30000000);
    for (int32_t p = 3; p <= PAGANI_KBT_SLOTS; p++) event(p, "app", PAGANI_KBT_EV, 0, 30000000);
    event(99, "new", PAGANI_KBT_SET, 0, 40000000);
    assert(t.recycled == 1 && t.final_lines == 2);
    char sum[PAGANI_KBT_LINE], lat[PAGANI_KBT_LINE];
    assert(pagani_kbt_take_final(&t, lat, sizeof(lat)) && pagani_kbt_take_final(&t, sum, sizeof(sum)) && !pagani_kbt_take_final(&t, line, sizeof(line)));
    assert(!strncmp(sum, "kbt app[1] final ev 0 set 0", 26) && strstr(sum, " in 1/1 "));
    assert(!strncmp(lat, "kbt app[1] final-lat in 1/", 26));

    // What the stall probe follows.
    int end = 0;
    assert(pagani_kbt_stall_group(PAGANI_KBT_DN, &end) == 0 && !end);
    assert(pagani_kbt_stall_group(PAGANI_KBT_DN_DONE, &end) == 0 && end);
    assert(pagani_kbt_stall_group(PAGANI_KBT_UP, &end) == 1 && !end);
    assert(pagani_kbt_stall_group(PAGANI_KBT_UP_DONE, &end) == 1 && end);
    assert(pagani_kbt_stall_group(PAGANI_KBT_IN, &end) == 2 && !end);
    assert(pagani_kbt_stall_group(PAGANI_KBT_IN_DONE, &end) == 2 && end);
    assert(pagani_kbt_stall_group(PAGANI_KBT_TASK, &end) == -1);
    assert(PAGANI_KBT_FLUSH_MS == 1000);
    printf("ok  keyboard trace v3: pairing after an overflow, repeated touchUp: and their up-done, long pairs, final counts, the probe's table against the queues\n");
    return 0;
}
