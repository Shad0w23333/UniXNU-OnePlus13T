// SPDX-License-Identifier: BSD-2-Clause-Patent
// Register facts: OnePlus gcc-sun.c, spmi-pmic-arb.c and Qualcomm dsi_ctrl_hw_cmn.c.
#pragma once
#include <stdint.h>

struct peripheral_io {
    void *context;
    uint32_t (*read)(void *,uint32_t);
    void (*write)(void *,uint32_t,uint32_t);
    void (*delay)(void *,unsigned);
};
static inline uint32_t peripheral_read(const struct peripheral_io *io,uint32_t reg) {return io->read(io->context,reg);}
static inline void peripheral_write(const struct peripheral_io *io,uint32_t reg,uint32_t value) {io->write(io->context,reg,value);}

#define SM8750_SPI_DELAY_COUNTERS 0x278
static inline int sm8750_spi_timing(const struct peripheral_io *se,unsigned cs,unsigned words,uint32_t *readback) {
    if (cs>0x3ff || words>0x3ff || !readback) return -22;
    uint32_t value=(cs<<10)|words;
    peripheral_write(se,SM8750_SPI_DELAY_COUNTERS,value);
    *readback=peripheral_read(se,SM8750_SPI_DELAY_COUNTERS);
    return (*readback&0xfffff)==value?0:-5;
}

// Observer reads need no peripheral write ownership; prefer the local EE's mapping.
static inline int spmi_find(const struct peripheral_io *core,const struct peripheral_io *cnfg,unsigned ppid,unsigned ee) {
    uint32_t version=peripheral_read(core,0),count=peripheral_read(core,4)&0x7ff;
    if (version<0x70000000 || version>=0x80000000 || !count || count>1024 || ppid>0xfff || ee>7) return -19;
    int found=-19;
    for (unsigned i=0;i<count;++i) {
        uint32_t map=peripheral_read(core,0x2000+4*i);
        if (map && ((map>>8)&0xfff)==ppid &&
            (found<0 || (peripheral_read(cnfg,4*i)&7)==ee)) found=(int)i;
    }
    return found;
}
static inline int spmi_read8(const struct peripheral_io *obs,unsigned ee,int apid,uint8_t offset,uint8_t *value) {
    if (ee>7 || apid<0 || apid>=1024 || !value) return -22;
    uint32_t ch=ee*0x8000+(unsigned)apid*0x20;
    peripheral_write(obs,ch,(1u<<27)|((uint32_t)offset<<4));
    for (unsigned n=0;n<1000;++n) {
        uint32_t status=peripheral_read(obs,ch+8);
        if (status&0xe) return -5;
        if (status&1) {*value=(uint8_t)peripheral_read(obs,ch+0x18);return 0;}
        obs->delay(obs->context,1);
    }
    return -110;
}

// The apps GCC branch vote register 0x52008 is one word shared by every driver whose clocks it gates: the touch (QUP1 S4,
// bits 18-21 and 26: sm8750_touch_clock), the I2C hub (AHB bit 7 and core bit 8 for all its engines, SE1 bit 11 for the fuel
// gauge in PaganiBattery, SE4 bit 14 for the speaker amplifiers in PaganiSpeaker) and PCIe0 (bits 0-6, PaganiWifi). Every update
// is a read-modify-write, and two that interleave lose the bits of the first. The lock word sm8750_gcc_vote_owner orders them
// with PaganiBattery's protocol: 0 free, else the holder's nonzero tag; taken by a compare-and-swap of 0 for the tag (acquire),
// tried every 10 us for at most 50 ms; given back by its holder (release). It is defined weak in every user, here as in
// PaganiBattery and PaganiWifi, so the kernel keeps one word whichever drivers it links; a file that defines the word itself
// defines SM8750_GCC_VOTE_OWNER_DEFINED before including this header. sm8750_gcc_vote_unlocked counts the updates written
// without the lock because it stayed held for the whole wait (a holder that never gives it back): the boot never waits longer.
#define SM8750_GCC_VOTE 0x52008u
#define SM8750_GCC_VOTE_STEP_US 10u
#define SM8750_GCC_VOTE_STEPS 5000u
#define SM8750_GCC_VOTE_TOUCH 0x043c0000u
#define SM8750_GCC_OWNER_TOUCH 0x54554348u
#ifndef SM8750_GCC_VOTE_OWNER_DEFINED
#define SM8750_GCC_VOTE_OWNER_DEFINED 1
#ifdef __cplusplus
extern "C" {
#endif
__attribute__((weak)) volatile uint32_t sm8750_gcc_vote_owner;
__attribute__((weak)) volatile uint32_t sm8750_gcc_vote_unlocked;
#ifdef __cplusplus
}
#endif
#endif

struct sm8750_gcc_vote_report {
    uint32_t before,after;   // the register as first read, and as read back at the end
    uint32_t waits;          // 10 us steps spent waiting for the lock word
    uint8_t wrote;           // 1: the register was written (a bit was missing)
    uint8_t locked;          // 1: the lock word was held for the update; 0 with wrote = 1: written without it (counted)
};
// 0 with the lock word held (owner: the caller's nonzero tag), -16 when it stayed held by another for 50 ms.
static inline int sm8750_gcc_vote_take(const struct peripheral_io *gcc,uint32_t owner,uint32_t *waits) {
    for (uint32_t step=0;;++step) {
        uint32_t expected=0;
        if (__atomic_compare_exchange_n(&sm8750_gcc_vote_owner,&expected,owner,0,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED)) {*waits=step;return 0;}
        if (step>=SM8750_GCC_VOTE_STEPS) {*waits=step;return -16;}
        gcc->delay(gcc->context,SM8750_GCC_VOTE_STEP_US);
    }
}
static inline void sm8750_gcc_vote_give(uint32_t owner) {
    uint32_t expected=owner;
    __atomic_compare_exchange_n(&sm8750_gcc_vote_owner,&expected,0u,0,__ATOMIC_RELEASE,__ATOMIC_RELAXED);
}
// Sets bits in the vote register and never clears one. A first read without the lock: when every bit is set already nothing more
// happens. Otherwise, under the lock word, one more read (another holder may have voted meanwhile), the write of the missing bits
// and a read back; without the lock (held elsewhere for the whole wait) the same update is written anyway and counted. Returns 0
// when every bit reads back set, -5 when one does not, -22 for a bad argument (nothing touched).
static inline int sm8750_gcc_vote_set(const struct peripheral_io *gcc,uint32_t bits,uint32_t owner,struct sm8750_gcc_vote_report *report) {
    struct sm8750_gcc_vote_report r={0,0,0,0,0};
    if (!gcc || !bits || !owner) {if (report) *report=r;return -22;}
    r.before=r.after=peripheral_read(gcc,SM8750_GCC_VOTE);
    if ((r.before&bits)!=bits) {
        r.locked=(uint8_t)(sm8750_gcc_vote_take(gcc,owner,&r.waits)==0);
        uint32_t now=peripheral_read(gcc,SM8750_GCC_VOTE);
        if ((now&bits)!=bits) {
            peripheral_write(gcc,SM8750_GCC_VOTE,now|bits);
            r.wrote=1;
            if (!r.locked) __atomic_add_fetch(&sm8750_gcc_vote_unlocked,1u,__ATOMIC_RELAXED);
        }
        r.after=peripheral_read(gcc,SM8750_GCC_VOTE);
        if (r.locked) sm8750_gcc_vote_give(owner);
    }
    if (report) *report=r;
    return (r.after&bits)==bits?0:-5;
}

// Adopt an already configured QUP1 S4 clock; do not change PLLs or DFS policy. Its five votes go through the shared update above;
// a read back without them is left to the branch polls, which fail as before when the clocks do not run.
static inline int sm8750_touch_clock(const struct peripheral_io *gcc,unsigned *hz,unsigned *select) {
    const uint32_t branches[]={0x2315c,0x23148,0x23140,0x23144,0x183cc};
    sm8750_gcc_vote_set(gcc,SM8750_GCC_VOTE_TOUCH,SM8750_GCC_OWNER_TOUCH,0);
    for (unsigned i=0;i<5;++i) {
        unsigned n=0;
        for (;n<200;++n) {
            uint32_t v=peripheral_read(gcc,branches[i]);
            if ((v&2) || !(v&0x80000000)) break;
            gcc->delay(gcc->context,1);
        }
        if (n==200) return -110;
    }
    uint32_t dfs=peripheral_read(gcc,0x183dc+0x14);
    unsigned best=0,index=0;
    for (unsigned i=0;i<((dfs&1)?8u:1u);++i) {
        uint32_t cfg=peripheral_read(gcc,0x183dc+((dfs&1)?0x1c+4*i:4));
        unsigned parent=(cfg>>8)&7;
        unsigned source=parent==0?19200000:parent==1?600000000:parent==6?300000000:0;
        // Fractional M/N modes are refused until their live settings are captured.
        if (!source || ((cfg>>12)&3)) continue;
        unsigned rate=(cfg&31)?(unsigned)((uint64_t)source*2/((cfg&31)+1)):source;
        if (rate && (!best || rate<best)) {best=rate;index=i;}
    }
    if (!best) return -95;
    *hz=best;*select=index;return 0;
}

// One DCS write packet through the controller's command FIFO: type 0x05 (short, no parameter: length 1), 0x15 (short, one parameter:
// length 2) or 0x39 (long, 1 to DSI_PANEL_PACKET_MAX bytes). The FIFO words follow dsi_ctrl.c/dsi_ctrl_hw_cmn.c: the header word holds the
// last-command flag (bit 31), the long-packet flag (bit 30), the data type and the word count (long) or the two data bytes (short); the
// payload follows little-endian, padded with 0xff to a word; an odd word count gets a zero word. The firmware's scanout setup stays.
// mode: high speed unless DSI_PANEL_LP (the DT's dsi_hs_mode/dsi_lp_mode for that command set: brightness and display off are high speed,
// the on sequence low power); DSI_PANEL_FORCE skips the checks of the enable bit, the test-pattern FIFO and the MDP stream (for a handoff
// that reads otherwise than the sibling boards': whether the engine answers then decides).
// The packet and the frames share the DSI link. The MDP's autorefresh sends every frame by itself and nothing here stops it: only the
// controller's command DMA is touched, never CTL or INTF. The hardware arbitrates the two, as on Android, whose command mode schedules no
// DMA; a packet still waits for the gap between two frames (at 120 Hz 1.5 ms of 8.3), polled every DSI_PANEL_POLL_US.
// Returns 0, -22 bad argument, -19 not sent (enable bit clear, or the test-pattern FIFO in use), -16 the engine stayed busy for 20 ms
// (nothing written), -110 no DMA done in 50 ms: the command stays the engine's and nothing is restored, except with DSI_PANEL_FORCE when
// the DMA engine does not report busy (it never took the command).
#define DSI_PANEL_PACKET_MAX 16
#define DSI_PANEL_LP 1u
#define DSI_PANEL_FORCE 2u
#define DSI_PANEL_POLL_US 100u
#define DSI_PANEL_BUSY_POLLS 200u
#define DSI_PANEL_DONE_POLLS 500u
static inline void dsi_panel_restore(const struct peripheral_io *io,uint32_t enables,uint32_t dma,uint32_t dma1,uint32_t length) {
    peripheral_write(io,0x110,enables|1);
    peripheral_write(io,0x15c,0);
    peripheral_write(io,0x1ec,1);io->delay(io->context,1);peripheral_write(io,0x1ec,0);
    peripheral_write(io,0x3c,dma);peripheral_write(io,0x390,dma1);peripheral_write(io,0x4c,length);
}
static inline int dsi_panel_packet_mode(const struct peripheral_io *io,uint8_t type,const uint8_t *payload,unsigned length,unsigned mode) {
    if (!payload || (type==0x05 && length!=1) || (type==0x15 && length!=2) ||
        (type==0x39 && (!length || length>DSI_PANEL_PACKET_MAX)) || (type!=0x05 && type!=0x15 && type!=0x39)) return -22;
    if (!(mode&DSI_PANEL_FORCE) && (!(peripheral_read(io,4)&1) || peripheral_read(io,0x15c))) return -19;
    // Busy: the command DMA, and the MDP stream too (status bit 2: the frame on its way) unless forced.
    const uint32_t busy=(mode&DSI_PANEL_FORCE)?2:6;
    unsigned n;
    for (n=0;n<DSI_PANEL_BUSY_POLLS && (peripheral_read(io,8)&busy);++n) io->delay(io->context,DSI_PANEL_POLL_US);
    if (n==DSI_PANEL_BUSY_POLLS) return -16;
    uint32_t words[2+DSI_PANEL_PACKET_MAX/4];
    unsigned count=0;
    if (type==0x39) {
        words[count++]=0xc0000000u|((uint32_t)type<<16)|length;
        for (unsigned i=0;i<length;i+=4) {
            uint32_t word=0;
            for (unsigned b=0;b<4;++b) word|=(uint32_t)(i+b<length?payload[i+b]:0xff)<<(8*b);
            words[count++]=word;
        }
    } else {
        words[count++]=0x80000000u|((uint32_t)type<<16)|(length>1?(uint32_t)payload[1]<<8:0)|payload[0];
    }
    const uint32_t bytes=4*count;
    if (count&1) words[count++]=0;
    uint32_t dma=peripheral_read(io,0x3c),dma1=peripheral_read(io,0x390),saved=peripheral_read(io,0x4c);
    // Status bits are W1C; keep interrupt enables without acknowledging other clients.
    const uint32_t enable_mask=0xa222aa02;
    uint32_t enables=peripheral_read(io,0x110)&enable_mask;
    peripheral_write(io,0x110,enables|1);
    peripheral_write(io,0x15c,0x30006);
    for (unsigned i=0;i<count;++i) peripheral_write(io,0x17c,words[i]);
    peripheral_write(io,0x3c,(dma&~0xc4000000u)|0x10000000u|((mode&DSI_PANEL_LP)?0x04000000u:0));
    peripheral_write(io,0x390,dma1&~2u);
    peripheral_write(io,0x4c,bytes);
    peripheral_write(io,0x90,1);
    for (n=0;n<DSI_PANEL_DONE_POLLS && !(peripheral_read(io,0x110)&1);++n) io->delay(io->context,DSI_PANEL_POLL_US);
    // Do not reset a command still owned by the engine on timeout. Caller disables further requests.
    if (n==DSI_PANEL_DONE_POLLS) {
        if ((mode&DSI_PANEL_FORCE) && !(peripheral_read(io,8)&2)) dsi_panel_restore(io,enables,dma,dma1,saved);
        return -110;
    }
    dsi_panel_restore(io,enables,dma,dma1,saved);
    return 0;
}
// The packet writer as the panel's power sequences use it: low power, the DT's state of the on sequence.
static inline int dsi_panel_packet(const struct peripheral_io *io,uint8_t type,const uint8_t *payload,unsigned length) {
    return dsi_panel_packet_mode(io,type,payload,length,DSI_PANEL_LP);
}
// DCS display on/off and 16-bit brightness, high speed. Brightness goes out high byte first (0x51, high, low): the panel's DT
// (qcom,mdss-dsi-bl-inverted-dbv, its 51 0d bb hbm-off table) and the vendor's UEFI Panel_Default_Brightness_Level say so.
static inline int dsi_panel_command_mode(const struct peripheral_io *io,uint8_t command,unsigned level,unsigned mode) {
    if ((command!=0x51 && command!=0x28 && command!=0x29) || level>0xffff) return -22;
    if (command==0x51) {
        const uint8_t payload[3]={0x51,(uint8_t)(level>>8),(uint8_t)level};
        return dsi_panel_packet_mode(io,0x39,payload,3,mode);
    }
    return dsi_panel_packet_mode(io,0x05,&command,1,mode);
}
static inline int dsi_panel_command(const struct peripheral_io *io,uint8_t command,unsigned level) {
    return dsi_panel_command_mode(io,command,level,0);
}
// What the controller was left in, read only (the zorn port read the same offsets on SM8650 under Gunyah; DSI1 and the DPU stay unread:
// a read of a block without power hangs this SoC).
struct dsi_panel_state {uint32_t version,ctrl,status,fifo,dma,trigger,lanes,ints,clocks,tpg,ack,timeout,secure,split;};
static inline void dsi_panel_snapshot(const struct peripheral_io *io,struct dsi_panel_state *s) {
    s->version=peripheral_read(io,0x000);s->ctrl=peripheral_read(io,0x004);s->status=peripheral_read(io,0x008);
    s->fifo=peripheral_read(io,0x00c);s->dma=peripheral_read(io,0x03c);s->trigger=peripheral_read(io,0x084);
    s->lanes=peripheral_read(io,0x0a8);s->ints=peripheral_read(io,0x110);s->clocks=peripheral_read(io,0x11c);
    s->tpg=peripheral_read(io,0x15c);s->ack=peripheral_read(io,0x068);s->timeout=peripheral_read(io,0x0c0);
    s->secure=peripheral_read(io,0x2cc);s->split=peripheral_read(io,0x330);
}
