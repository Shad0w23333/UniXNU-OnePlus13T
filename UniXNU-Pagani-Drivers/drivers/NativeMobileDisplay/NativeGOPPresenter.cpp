// SPDX-License-Identifier: BSD-2-Clause-Patent
// Transfer completed GPU pixels to actual firmware scanout. No CPU rendering,
// fabricated refresh event, Metal discovery or original display publication.
#include <IOKit/IOService.h>
#include <IOKit/IOUserClient.h>
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOLib.h>
#include <kern/task.h>
#include <kern/clock.h>
#include <pexpert/pexpert.h>
#include "NativeGOPWire.h"
#include "NativeGOPMappingCache.h"
#include "NativeDpu.h"
#include "../BootProgress/PeripheralStatus.h"
// The status band BootProgress keeps (NGP_METHOD_BAND); builds without it have none.
extern "C" __attribute__((weak)) int boot_progress_gpu_band(unsigned *,unsigned *) {return 0;}
extern "C" {
volatile uint32_t native_gop_ready,native_gop_failures;
volatile uint64_t native_gop_transfers,native_gop_bytes,native_gop_physical,native_gop_virtual;
volatile uint64_t native_gop_gpu_offers,native_gop_gpu_completions,native_gop_gpu_failures,native_gop_gpu_abandoned;
/* waitSwap: [0] calls, [1] timeouts, [2] the requested timeout of the last call (ms), [3] time the last timed-out call really waited (ns), [4] the shortest
 * and [5] the longest such wait (ns), [6] total ns waited in timed-out calls. A timed wait that returns long before its deadline shows here. */
volatile uint64_t native_gop_wait_stats[7];
/* Frame mappings in the client (NativeGOPMappingCache): [0] hits, [1] misses (new mappings), [2] evictions, [3] failures, [4] drops,
 * [5] mappings live, [6] BAND calls, [7] WAIT_SWAP2 calls. */
volatile uint64_t native_gop_map_stats[8];
IOService *native_gop_presenter_create(void);
bool native_gop_gpu_offer(IOService *,void *display,IOMemoryDescriptor *,uint64_t stride,uint32_t width,uint32_t height);
// The same offer with the frame's surface (the mapping cache's key, may be null) and the byte offset of the scanout buffer the GPU writes
// (page flips; 0 otherwise). Refused when that buffer is not inside the presenter's scanout memory.
bool native_gop_gpu_offer_frame(IOService *,void *display,IOMemoryDescriptor *,uint64_t stride,uint32_t width,uint32_t height,
                                OSObject *surface,uint64_t targetOffset);
void native_gop_presenter_set_source(IOService *presenter,IOService *nub);
// Provided by the display driver: finishes (status 0) or fails the swap that was offered. The timed form also passes when the client took
// the frame (waitSwap returned it) and when it reported the copy (completeSwap), mach ticks, 0 where that did not happen: the frame's
// "take" and "copy" in its breakdown (BootProgress pagani-fps, the SLOW row and the J records).
void native_legacy_gpu_complete(void *display,int status);
void native_legacy_gpu_complete_timed(void *display,int status,uint64_t taken,uint64_t copied);
// Stage7Probe: what the display path waits for now (PAGANI_STAGE_*, since `when`) and its events per second (PAGANI_DISPLAY_*).
void native_legacy_frame_stage(unsigned stage,uint64_t when);
void native_legacy_display_event(unsigned event);
}
class NativeGOPClient;
class NativeGOPPresenter;
static NativeGOPPresenter *gHandoff;   // the presenter in GPU mode: the hand-off entry points below reach it without the registry
class NativeGOPPresenter : public IOService {
    OSDeclareDefaultStructors(NativeGOPPresenter)
    friend class NativeGOPClient;
    IOMemoryMap *mapping=nullptr;
    IOLock *gate=nullptr;
    // The client's mappings of the offered frames, kept across frames (ARCHITECTURE 5.3.2 item 3); its own lock, because mapping into the
    // client task may block and must not happen under the gate.
    struct MapOps {
        IOMemoryMap *map(struct task *task,IOMemoryDescriptor *memory) {
            IOMemoryMap *created=memory->createMappingInTask(task,0,kIOMapAnywhere);
            if(created && !created->getVirtualAddress())OSSafeReleaseNULL(created);
            return created;
        }
        void retain(IOMemoryDescriptor *memory) {memory->retain();}
        void release(IOMemoryDescriptor *memory,IOMemoryMap *map) {OSSafeReleaseNULL(map);OSSafeReleaseNULL(memory);}
        void retainTask(struct task *task) {task_reference(task);}
        void releaseTask(struct task *task) {task_deallocate(task);}
    };
    NativeGOPMappingCache<struct task,IOMemoryDescriptor,IOMemoryMap> frames;
    IOLock *framesLock=nullptr;
    // Page flips (pagani-fb-flip=1): the scanout memory spans the three splash-region buffers; mode.bytes stays one frame.
    uint64_t scanoutBytes=0;
    IOService *owner=nullptr;
    NGPMode mode={};
    bool online=false;
    IOService *memorySource=nullptr;
    // GPU hand-off (sheng-fb-gpu=1): one offered frame at a time, from the display driver to the privileged client. `taken`: when waitSwap
    // handed it to the client (mach ticks).
    bool gpuMode=false,gpuClient=false;
    struct {bool offered,delivered;uint64_t sequence;void *display;IOMemoryDescriptor *memory;OSObject *surface;
            uint64_t stride;uint32_t width,height;uint64_t target,taken;} swap={};
    uint64_t swapSequence=0;
    IOReturn waitSwap(task_t,uint64_t timeoutMs,NGPSwap *,uint64_t *target=nullptr);
    IOReturn completeSwap(uint64_t sequence,int status);
    void abandonSwap();
    void dropFrames();
    void band(NGPBand *) const;
public:
    void setMemorySource(IOService *source) {memorySource=source;}
    static IOReturn handoffCall(uint32_t op,task_t task,const void *in,size_t inSize,void *out,size_t outSize);
    static IOMemoryDescriptor *handoffMemory();
    static void handoffClosed(task_t task);
    bool offer(void *display,IOMemoryDescriptor *,uint64_t stride,uint32_t width,uint32_t height,OSObject *surface=nullptr,
               uint64_t targetOffset=0);
    // The client's mapping of `memory` (the frame of `surface`): its id for NGPSwap.reserved (nonzero) and the mapping, or 0.
    uint32_t cacheMapping(task_t,OSObject *surface,IOMemoryDescriptor *memory,IOMemoryMap **map);
private:
    IOReturn transfer(task_t,const NGPPresent &,NGPTransfer *);
public:
    bool start(IOService *) override;
    void stop(IOService *) override;
    void free() override;
    bool handleOpen(IOService *client,IOOptionBits,void *) override {if(owner)return false;owner=client;return true;}
    void handleClose(IOService *client,IOOptionBits) override {if(owner==client)owner=nullptr;}
    bool handleIsOpen(const IOService *client) const override {return client?owner==client:owner!=nullptr;}
    IOReturn newUserClient(task_t,void *,UInt32,OSDictionary *,IOUserClient **) override;
};
class NativeGOPClient : public IOUserClient {
    OSDeclareDefaultStructors(NativeGOPClient)
    NativeGOPPresenter *controller=nullptr;
    task_t task=TASK_NULL;
    bool opened=false;
    static IOReturn modeAction(OSObject *,void *,IOExternalMethodArguments *);
    static IOReturn presentAction(OSObject *,void *,IOExternalMethodArguments *);
    static IOReturn waitAction(OSObject *,void *,IOExternalMethodArguments *);
    static IOReturn completeAction(OSObject *,void *,IOExternalMethodArguments *);
    static IOReturn bandAction(OSObject *,void *,IOExternalMethodArguments *);
    static IOReturn wait2Action(OSObject *,void *,IOExternalMethodArguments *);
public:
    bool initWithTask(task_t,void *,UInt32,OSDictionary *) override;
    bool start(IOService *) override;
    void stop(IOService *) override;
    void free() override;
    IOReturn clientClose() override {terminate();return kIOReturnSuccess;}
    IOReturn externalMethod(uint32_t,IOExternalMethodArguments *,IOExternalMethodDispatch *,OSObject *,void *) override;
    IOReturn clientMemoryForType(UInt32,IOOptionBits *,IOMemoryDescriptor **) override;
};
OSDefineMetaClassAndStructors(NativeGOPPresenter,IOService)
OSDefineMetaClassAndStructors(NativeGOPClient,IOUserClient)
IOService *native_gop_presenter_create(void) {return new NativeGOPPresenter;}
bool NativeGOPPresenter::start(IOService *provider) {
    if(!IOService::start(provider))return false;
    PE_Video video={};
    if(PE_current_console(&video) || !video.v_baseAddr || !video.v_width || !video.v_height ||
       video.v_width>16384 || video.v_height>16384 || video.v_depth!=32 || video.v_rotate ||
       video.v_rowBytes<video.v_width*4 || video.v_rowBytes>UINT64_MAX/video.v_height)return false;
    // The hand-off presenter is attached away from the display subsystem's node (the original display code enumerates that node's children),
    // so the framebuffer memory comes from the node it names instead of its provider.
    // Buffers are 4 KiB aligned (0xc3f000 apart for 1216x2640): the device pages of the splash region, not the client's page size.
    const uint64_t frame=video.v_rowBytes*video.v_height,frameStride=(frame+4095)&~4095ull;
    unsigned flips=0;PE_parse_boot_argn("pagani-fb-flip",&flips,sizeof flips);
    if(flips==1 && video.v_baseAddr==nativedpu::kSplashBase && frameStride*nativedpu::kScanoutBuffers<=nativedpu::kSplashBytes) {
        // Page flips: one device mapping over the three buffers of the splash region (ARCHITECTURE 5.3.4).
        IODeviceMemory *buffers=IODeviceMemory::withRange(video.v_baseAddr,frameStride*nativedpu::kScanoutBuffers);
        mapping=buffers?buffers->map(kIOMapAnywhere|kIOMapInhibitCache):nullptr;
        OSSafeReleaseNULL(buffers);
        if(mapping && (!mapping->getVirtualAddress() || mapping->getPhysicalAddress()!=video.v_baseAddr ||
                       mapping->getLength()!=frameStride*nativedpu::kScanoutBuffers))OSSafeReleaseNULL(mapping);
    }
    if(!mapping) {
        mapping=(memorySource?memorySource:provider)->mapDeviceMemoryWithIndex(0,kIOMapInhibitCache);
        if(!mapping || !mapping->getVirtualAddress() || mapping->getPhysicalAddress()!=video.v_baseAddr || mapping->getLength()!=frame)
            return false;
    }
    scanoutBytes=mapping->getLength();
    gate=IOLockAlloc();framesLock=IOLockAlloc();if(!gate || !framesLock)return false;
    {unsigned v=0;PE_parse_boot_argn("sheng-fb-gpu",&v,sizeof v);gpuMode=(v==1);}
    mode={NGP_VERSION,NGP_BGRX8888,video.v_width,video.v_height,video.v_rowBytes,frame};
    native_gop_physical=mapping->getPhysicalAddress();native_gop_virtual=mapping->getVirtualAddress();
    online=true;native_gop_ready=1;if(gpuMode)gHandoff=this;registerService();return true;
}
void NativeGOPPresenter::stop(IOService *provider) {
    if(gHandoff==this)gHandoff=nullptr;
    if(gate)IOLockLock(gate);online=false;native_gop_ready=0;if(gate){IOLockWakeup(gate,&swap,false);IOLockUnlock(gate);}
    abandonSwap();
    IOService::stop(provider);
}
void NativeGOPPresenter::free() {
    dropFrames();
    OSSafeReleaseNULL(mapping);if(gate){IOLockFree(gate);gate=nullptr;}if(framesLock){IOLockFree(framesLock);framesLock=nullptr;}
    IOService::free();
}
void NativeGOPPresenter::dropFrames() {
    if(!framesLock)return;
    IOLockLock(framesLock);
    MapOps ops;
    if(frames.live() || frames.owner)native_gop_map_stats[4]=native_gop_map_stats[4]+1;
    frames.drop(ops);native_gop_map_stats[5]=0;
    IOLockUnlock(framesLock);
}
uint32_t NativeGOPPresenter::cacheMapping(task_t task,OSObject *surface,IOMemoryDescriptor *memory,IOMemoryMap **map) {
    *map=nullptr;
    if(!framesLock)return 0;
    IOLockLock(framesLock);
    MapOps ops;
    const uint32_t id=frames.lookup(ops,task,surface,memory,map);
    native_gop_map_stats[0]=frames.hits;native_gop_map_stats[1]=frames.misses;native_gop_map_stats[2]=frames.evictions;
    native_gop_map_stats[3]=frames.failures;native_gop_map_stats[5]=frames.live();
    IOLockUnlock(framesLock);
    return id;
}
// The rows BootProgress keeps for its status text: the GPU client leaves them out of its copy (ARCHITECTURE 8.5).
void NativeGOPPresenter::band(NGPBand *out) const {
    *out={};
    unsigned y0=0,y1=0;
    if(!boot_progress_gpu_band(&y0,&y1) || y0>=y1 || y0>=mode.height)return;
    if(y1>mode.height)y1=uint32_t(mode.height);
    out->y0=y0;out->y1=y1;out->rows=(y1-y0+PAGANI_STATUS_LINE-1)/PAGANI_STATUS_LINE;
}
IOReturn NativeGOPPresenter::transfer(task_t task,const NGPPresent &q,NGPTransfer *result) {
    if(gpuMode)return kIOReturnUnsupported;   // in GPU mode no CPU copy exists, not even on request
    if(!q.address || q.bytes!=mode.bytes || q.width!=mode.width || q.height!=mode.height ||
       q.stride!=mode.stride || q.format!=mode.format || q.address>UINT64_MAX-q.bytes)return kIOReturnBadArgument;
    IOMemoryDescriptor *input=IOMemoryDescriptor::withAddressRange(q.address,q.bytes,kIODirectionIn,task);
    if(!input)return kIOReturnNoMemory;
    IOReturn status=input->prepare(kIODirectionIn);
    if(status==kIOReturnSuccess) {
        IOLockLock(gate);
        if(!online)status=kIOReturnNotReady;
        else if(input->readBytes(0,reinterpret_cast<void *>(mapping->getVirtualAddress()),q.bytes)!=q.bytes)status=kIOReturnUnderrun;
        else {
            __asm__ volatile("dsb oshst" ::: "memory");
            native_gop_transfers=native_gop_transfers+1;result->sequence=native_gop_transfers;
            result->bytes=q.bytes;native_gop_bytes=native_gop_bytes+q.bytes;
        }
        IOLockUnlock(gate);
        IOReturn completion=input->complete(kIODirectionIn);if(status==kIOReturnSuccess)status=completion;
    }
    input->release();if(status!=kIOReturnSuccess)__atomic_fetch_add(&native_gop_failures,1,__ATOMIC_RELAXED);return status;
}
// The display driver offers a finished frame. Accepted only while the privileged GPU client is alive and waiting for work; otherwise the
// display driver keeps its own path (counted by it as a fallback).
bool NativeGOPPresenter::offer(void *display,IOMemoryDescriptor *memory,uint64_t stride,uint32_t width,uint32_t height,OSObject *surface,
                               uint64_t targetOffset) {
    if(!gpuMode || !memory || !display || !gate || (targetOffset&4095) || targetOffset>scanoutBytes ||
       mode.bytes>scanoutBytes-targetOffset)return false;
    IOLockLock(gate);
    bool accepted=online && gpuClient && !swap.offered;
    if(accepted) {
        memory->retain();
        swap={true,false,++swapSequence,display,memory,surface,stride,width,height,targetOffset,0};
        native_gop_gpu_offers=native_gop_gpu_offers+1;
        IOLockWakeup(gate,&swap,false);
    }
    IOLockUnlock(gate);return accepted;
}
IOReturn NativeGOPPresenter::waitSwap(task_t task,uint64_t timeoutMs,NGPSwap *out,uint64_t *target) {
    if(!gpuMode)return kIOReturnUnsupported;
    if(timeoutMs>1000)timeoutMs=1000;
    uint64_t deadline;clock_interval_to_deadline((uint32_t)timeoutMs,kMillisecondScale,&deadline);
    const uint64_t began=mach_absolute_time();
    native_gop_wait_stats[0]=native_gop_wait_stats[0]+1;native_gop_wait_stats[2]=timeoutMs;
    IOLockLock(gate);
    gpuClient=true;
    while(online && (!swap.offered || swap.delivered)) {
        if(IOLockSleepDeadline(gate,&swap,deadline,THREAD_ABORTSAFE)!=THREAD_AWAKENED) {
            // The sleep ended on its deadline or a signal, with the gate held again: a frame offered meanwhile (at the deadline) is taken
            // now, not answered with an empty wait that leaves it to the client's next call.
            if(online && swap.offered && !swap.delivered)break;
            IOLockUnlock(gate);
            uint64_t ns=0;absolutetime_to_nanoseconds(mach_absolute_time()-began,&ns);
            native_gop_wait_stats[1]=native_gop_wait_stats[1]+1;native_gop_wait_stats[3]=ns;
            if(!native_gop_wait_stats[4] || ns<native_gop_wait_stats[4])native_gop_wait_stats[4]=ns;
            if(ns>native_gop_wait_stats[5])native_gop_wait_stats[5]=ns;
            native_gop_wait_stats[6]=native_gop_wait_stats[6]+ns;
            native_legacy_display_event(PAGANI_DISPLAY_TIMEOUT);    // an empty wait: the client had nothing to copy
            return kIOReturnTimeout;
        }
    }
    if(!online) {IOLockUnlock(gate);return kIOReturnNotReady;}
    swap.delivered=true;
    IOMemoryDescriptor *memory=swap.memory;memory->retain();
    OSObject *surface=swap.surface;
    const uint64_t sequence=swap.sequence,stride=swap.stride,offset=swap.target;const uint32_t width=swap.width,height=swap.height;
    IOLockUnlock(gate);
    // The frame's pages go into the client's task here, writable like any import (the GPU maps them for reading; the kernel pins and
    // maintains them). The mapping stays for the next time this surface comes round; its id lets the client keep its GPU import too.
    IOMemoryMap *map=nullptr;
    const uint32_t id=cacheMapping(task,surface,memory,&map);
    memory->release();
    IOLockLock(gate);
    if(!id || !map || !swap.offered || swap.sequence!=sequence) {
        // The frame could not be mapped into the client, or it was withdrawn meanwhile. A frame still ours fails here: the display driver
        // copies it with the CPU and goes on, and the client hears a timeout (nothing to serve) and keeps serving. An error here ended the
        // client's loop for the boot and left the frame delivered and never completed, so the display queue waited on it for good.
        const bool ours=swap.offered && swap.delivered && swap.sequence==sequence;
        auto failed=swap;
        if(ours)swap={};
        IOLockUnlock(gate);
        __atomic_fetch_add(&native_gop_gpu_failures,1,__ATOMIC_RELAXED);
        if(ours) {failed.memory->release();native_legacy_gpu_complete_timed(failed.display,kIOReturnNoMemory,0,0);}
        return kIOReturnTimeout;
    }
    out->sequence=sequence;out->address=map->getVirtualAddress();out->bytes=map->getLength();out->stride=stride;
    out->width=width;out->height=height;out->format=NGP_BGRX8888;out->reserved=id;
    if(target)*target=offset;
    // Taken: the client has the frame from here on (its wake-up and the mapping are the frame's "take", what follows its "copy").
    const uint64_t taken=mach_absolute_time();
    swap.taken=taken;
    IOLockUnlock(gate);
    native_legacy_frame_stage(PAGANI_STAGE_TAKEN,taken);
    return kIOReturnSuccess;
}
IOReturn NativeGOPPresenter::completeSwap(uint64_t sequence,int status) {
    const uint64_t copied=mach_absolute_time();     // the client's copy ended (its blit completed) just before this call
    IOLockLock(gate);
    // A completion for a frame that is no longer the delivered one (failed or withdrawn here meanwhile) changes nothing: it is counted and
    // answered with success, because the client ends its loop for the boot on any error and the screen would stop with it.
    if(!swap.offered || !swap.delivered || swap.sequence!=sequence) {
        IOLockUnlock(gate);__atomic_fetch_add(&native_gop_gpu_failures,1,__ATOMIC_RELAXED);return kIOReturnSuccess;
    }
    auto done=swap;swap={};
    IOLockUnlock(gate);
    done.memory->release();
    if(status)__atomic_fetch_add(&native_gop_gpu_failures,1,__ATOMIC_RELAXED);
    else {native_gop_gpu_completions=native_gop_gpu_completions+1;native_legacy_frame_stage(PAGANI_STAGE_COPIED,copied);}
    native_legacy_gpu_complete_timed(done.display,status,done.taken,status?0:copied);return kIOReturnSuccess;
}
// The client went away with a frame outstanding: the display driver is told it failed, so it can use its own path instead of waiting forever.
void NativeGOPPresenter::abandonSwap() {
    if(!gate)return;
    IOLockLock(gate);
    gpuClient=false;
    auto done=swap;swap={};
    IOLockUnlock(gate);
    dropFrames();       // the client's task goes with its mappings
    if(!done.offered)return;
    if(done.memory)done.memory->release();
    __atomic_fetch_add(&native_gop_gpu_abandoned,1,__ATOMIC_RELAXED);
    native_legacy_gpu_complete_timed(done.display,kIOReturnAborted,done.delivered?done.taken:0,0);
}
// The plugin reaches the hand-off through the Adreno accelerator's connection (open type 0x4148), not through NativeGOPClient, so the end
// of that client never ran abandonSwap: a render server that died between WAIT_SWAP and COMPLETE_SWAP left its frame delivered for good
// (every later offer refused, the display copying each frame with the CPU) and its task and mappings referenced. The Adreno830 shell calls
// native_gop_handoff_closed when such a connection closes (a weak no-op there until this defines it).
void NativeGOPPresenter::handoffClosed(task_t task) {
    NativeGOPPresenter *p=gHandoff;
    if(!p || !p->framesLock || !task)return;
    IOLockLock(p->framesLock);
    const bool ours=p->frames.owner==task;
    IOLockUnlock(p->framesLock);
    if(ours)p->abandonSwap();
}
extern "C" void native_gop_handoff_closed(task_t task) {NativeGOPPresenter::handoffClosed(task);}
extern "C" void native_gop_presenter_set_source(IOService *presenter,IOService *nub) {
    if(NativeGOPPresenter *p=OSDynamicCast(NativeGOPPresenter,presenter))p->setMemorySource(nub);
}
extern "C" bool native_gop_gpu_offer(IOService *presenter,void *display,IOMemoryDescriptor *memory,uint64_t stride,uint32_t width,uint32_t height) {
    NativeGOPPresenter *p=OSDynamicCast(NativeGOPPresenter,presenter);
    return p && p->offer(display,memory,stride,width,height);
}
extern "C" bool native_gop_gpu_offer_frame(IOService *presenter,void *display,IOMemoryDescriptor *memory,uint64_t stride,uint32_t width,
                                           uint32_t height,OSObject *surface,uint64_t targetOffset) {
    NativeGOPPresenter *p=OSDynamicCast(NativeGOPPresenter,presenter);
    return p && p->offer(display,memory,stride,width,height,surface,targetOffset);
}
// Entry points for the GPU client that reaches the hand-off through an already admitted connection (the Adreno accelerator's user client, which
// the original sandbox profiles allow where they do not allow this service): op 0 mode, 2 wait for a frame, 3 complete it, 4 the status
// band, 5 wait for a frame with its target buffer (the NGP_METHOD_* numbers). Same checks as the service's own methods; sizes are the wire
// records'. Ops 0-3 answer exactly as before ops 4 and 5 existed.
IOReturn NativeGOPPresenter::handoffCall(uint32_t op,task_t task,const void *in,size_t inSize,void *out,size_t outSize) {
    NativeGOPPresenter *p=gHandoff;
    if(!p || !p->gpuMode || !p->online)return kIOReturnNotReady;
    switch(op) {
    case NGP_METHOD_MODE:
        if(inSize || !out || outSize!=sizeof(NGPMode))return kIOReturnBadArgument;
        memcpy(out,&p->mode,sizeof(NGPMode));return kIOReturnSuccess;
    case NGP_METHOD_WAIT_SWAP: {
        if(!in || inSize!=sizeof(NGPWait) || !out || outSize!=sizeof(NGPSwap))return kIOReturnBadArgument;
        NGPWait q={};NGPSwap swap={};memcpy(&q,in,sizeof(q));
        IOReturn status=p->waitSwap(task,q.timeout_ms,&swap);
        if(status==kIOReturnSuccess)memcpy(out,&swap,sizeof(swap));return status;
    }
    case NGP_METHOD_COMPLETE_SWAP: {
        if(!in || inSize!=sizeof(NGPComplete) || outSize)return kIOReturnBadArgument;
        NGPComplete q={};memcpy(&q,in,sizeof(q));
        return p->completeSwap(q.sequence,(int)q.status);
    }
    case NGP_METHOD_BAND: {
        if(inSize || !out || outSize!=sizeof(NGPBand))return kIOReturnBadArgument;
        NGPBand band={};p->band(&band);native_gop_map_stats[6]=native_gop_map_stats[6]+1;
        memcpy(out,&band,sizeof(band));return kIOReturnSuccess;
    }
    case NGP_METHOD_WAIT_SWAP2: {
        if(!in || inSize!=sizeof(NGPWait) || !out || outSize!=sizeof(NGPSwap2))return kIOReturnBadArgument;
        NGPWait q={};NGPSwap2 swap={};memcpy(&q,in,sizeof(q));native_gop_map_stats[7]=native_gop_map_stats[7]+1;
        IOReturn status=p->waitSwap(task,q.timeout_ms,&swap.swap,&swap.target_offset);
        if(status==kIOReturnSuccess)memcpy(out,&swap,sizeof(swap));return status;
    }
    default:return kIOReturnUnsupported;
    }
}
// The scanout as a writable, retained descriptor (GPU mode only); the caller maps it into its task.
IOMemoryDescriptor *NativeGOPPresenter::handoffMemory() {
    NativeGOPPresenter *p=gHandoff;
    if(!p || !p->gpuMode || !p->online || !p->mapping)return nullptr;
    IOMemoryDescriptor *memory=p->mapping->getMemoryDescriptor();
    if(memory)memory->retain();
    return memory;
}
extern "C" IOReturn native_gop_handoff_call(uint32_t op,task_t task,const void *in,size_t inSize,void *out,size_t outSize) {
    return NativeGOPPresenter::handoffCall(op,task,in,inSize,out,outSize);
}
extern "C" IOMemoryDescriptor *native_gop_handoff_memory(void) {return NativeGOPPresenter::handoffMemory();}
IOReturn NativeGOPPresenter::newUserClient(task_t task,void *security,UInt32 type,OSDictionary *properties,IOUserClient **out) {
    if(!out || type)return kIOReturnBadArgument;
    *out=nullptr;NativeGOPClient *client=new NativeGOPClient;if(!client)return kIOReturnNoMemory;
    bool attached=false;
    if(!client->initWithTask(task,security,type,properties) || !(attached=client->attach(this)) || !client->start(this)) {
        if(attached)client->detach(this);client->release();return kIOReturnNotOpen;
    }
    *out=client;return kIOReturnSuccess;
}
bool NativeGOPClient::initWithTask(task_t ownerTask,void *security,UInt32 type,OSDictionary *properties) {
    if(!ownerTask || type || clientHasPrivilege(security,kIOClientPrivilegeAdministrator)!=kIOReturnSuccess ||
       !IOUserClient::initWithTask(ownerTask,security,type,properties))return false;
    task=ownerTask;task_reference(task);return true;
}
bool NativeGOPClient::start(IOService *provider) {
    controller=OSDynamicCast(NativeGOPPresenter,provider);
    if(!controller || !IOUserClient::start(provider))return false;
    opened=controller->open(this);return opened;
}
void NativeGOPClient::stop(IOService *provider) {
    if(opened){if(controller)controller->abandonSwap();controller->close(this);opened=false;}controller=nullptr;IOUserClient::stop(provider);
}
void NativeGOPClient::free() {if(task){task_deallocate(task);task=TASK_NULL;}IOUserClient::free();}
IOReturn NativeGOPClient::modeAction(OSObject *target,void *,IOExternalMethodArguments *a) {
    NativeGOPClient *client=OSDynamicCast(NativeGOPClient,target);
    if(!client || !client->opened || !client->controller)return kIOReturnNotOpen;
    if(a->structureOutputDescriptor || !a->structureOutput)return kIOReturnUnsupported;
    memcpy(a->structureOutput,&client->controller->mode,sizeof(NGPMode));return kIOReturnSuccess;
}
IOReturn NativeGOPClient::presentAction(OSObject *target,void *,IOExternalMethodArguments *a) {
    NativeGOPClient *client=OSDynamicCast(NativeGOPClient,target);
    if(!client || !client->opened || !client->controller)return kIOReturnNotOpen;
    if(a->structureInputDescriptor || a->structureOutputDescriptor || !a->structureInput || !a->structureOutput)return kIOReturnUnsupported;
    NGPPresent q={};NGPTransfer result={};memcpy(&q,a->structureInput,sizeof(q));
    IOReturn status=client->controller->transfer(client->task,q,&result);
    if(status==kIOReturnSuccess)memcpy(a->structureOutput,&result,sizeof(result));return status;
}
IOReturn NativeGOPClient::waitAction(OSObject *target,void *,IOExternalMethodArguments *a) {
    NativeGOPClient *client=OSDynamicCast(NativeGOPClient,target);
    if(!client || !client->opened || !client->controller)return kIOReturnNotOpen;
    if(a->structureInputDescriptor || a->structureOutputDescriptor || !a->structureInput || !a->structureOutput)return kIOReturnUnsupported;
    NGPWait q={};NGPSwap swap={};memcpy(&q,a->structureInput,sizeof(q));
    IOReturn status=client->controller->waitSwap(client->task,q.timeout_ms,&swap);
    if(status==kIOReturnSuccess)memcpy(a->structureOutput,&swap,sizeof(swap));return status;
}
IOReturn NativeGOPClient::completeAction(OSObject *target,void *,IOExternalMethodArguments *a) {
    NativeGOPClient *client=OSDynamicCast(NativeGOPClient,target);
    if(!client || !client->opened || !client->controller)return kIOReturnNotOpen;
    if(a->structureInputDescriptor || a->structureOutputDescriptor || !a->structureInput)return kIOReturnUnsupported;
    NGPComplete q={};memcpy(&q,a->structureInput,sizeof(q));
    return client->controller->completeSwap(q.sequence,(int)q.status);
}
IOReturn NativeGOPClient::bandAction(OSObject *target,void *,IOExternalMethodArguments *a) {
    NativeGOPClient *client=OSDynamicCast(NativeGOPClient,target);
    if(!client || !client->opened || !client->controller)return kIOReturnNotOpen;
    if(a->structureOutputDescriptor || !a->structureOutput)return kIOReturnUnsupported;
    NGPBand band={};client->controller->band(&band);
    memcpy(a->structureOutput,&band,sizeof(band));return kIOReturnSuccess;
}
IOReturn NativeGOPClient::wait2Action(OSObject *target,void *,IOExternalMethodArguments *a) {
    NativeGOPClient *client=OSDynamicCast(NativeGOPClient,target);
    if(!client || !client->opened || !client->controller)return kIOReturnNotOpen;
    if(a->structureInputDescriptor || a->structureOutputDescriptor || !a->structureInput || !a->structureOutput)return kIOReturnUnsupported;
    NGPWait q={};NGPSwap2 swap={};memcpy(&q,a->structureInput,sizeof(q));
    IOReturn status=client->controller->waitSwap(client->task,q.timeout_ms,&swap.swap,&swap.target_offset);
    if(status==kIOReturnSuccess)memcpy(a->structureOutput,&swap,sizeof(swap));return status;
}
IOReturn NativeGOPClient::externalMethod(uint32_t selector,IOExternalMethodArguments *a,IOExternalMethodDispatch *,OSObject *,void *) {
    static const IOExternalMethodDispatch methods[]={{modeAction,0,0,0,sizeof(NGPMode)},
                                                    {presentAction,0,sizeof(NGPPresent),0,sizeof(NGPTransfer)},
                                                    {waitAction,0,sizeof(NGPWait),0,sizeof(NGPSwap)},
                                                    {completeAction,0,sizeof(NGPComplete),0,0},
                                                    {bandAction,0,0,0,sizeof(NGPBand)},
                                                    {wait2Action,0,sizeof(NGPWait),0,sizeof(NGPSwap2)}};
    if(selector>=sizeof(methods)/sizeof(methods[0]))return kIOReturnUnsupported;
    return IOUserClient::externalMethod(selector,a,const_cast<IOExternalMethodDispatch *>(methods+selector),this,nullptr);
}
IOReturn NativeGOPClient::clientMemoryForType(UInt32 type,IOOptionBits *options,IOMemoryDescriptor **memory) {
    if(type>NGP_MEMORY_SCANOUT_RW || !options || !memory)return kIOReturnBadArgument;
    *memory=nullptr;if(!opened || !controller || !controller->online)return kIOReturnNotReady;
    // Type 1: the scanout itself, writable, for the GPU to write; only in GPU mode, where no CPU copy exists.
    if(type==NGP_MEMORY_SCANOUT_RW && !controller->gpuMode)return kIOReturnUnsupported;
    *memory=controller->mapping->getMemoryDescriptor();if(!*memory)return kIOReturnNoMemory;
    (*memory)->retain();if(type!=NGP_MEMORY_SCANOUT_RW)*options|=kIOMapReadOnly;return kIOReturnSuccess;
}
