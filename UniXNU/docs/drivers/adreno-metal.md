# AdrenoMetal

Start with the [adaptation procedure](/porting/gpu) for board inputs, source changes, build integration and verification order.

AdrenoMetal provides the Metal frontend through metal2vulkan and Turnip. It
publishes a device through `MTLAddDevice`, translates AIR, and submits work to
the kernel GPU interface.

| Field | Value |
| --- | --- |
| Repository | [UniXNU/AdrenoMetal](https://github.com/UniXNU/AdrenoMetal) |
| Implementation | A740/SM8550 graphics stack and the selected OS ABI |
| Dependencies | Adreno740, Turnip, metal2vulkan, IOGPUFamily and IOSurface interfaces |
| Board inputs | Firmware, power policy, memory layout, display integration |
| Verified device | sheng |
| Runtime plugin | `NativeAdrenoMetal` |

sheng runs the GPU-composited desktop. Exported pipeline caches are reused across
boots. The render server primes every Metal library of the OS image while the
desktop is idle: the payload carries the list (`NativeAdrenoMetal.primelist`) and
a copy of the cryptex QuartzCore library, which the render server's sandbox
cannot reach. The idle desktop submits about 6 command buffers per 250 ms at
120 Hz, so priming waits only above 8. The PR log row shows its progress; all
162 libraries are primed on sheng. With the list present the render server does
not scan directories, and each library is read only while the display is quiet.

A render pass that cannot be encoded still returns an encoder, and its command
buffer is refused: RenderBox caches encoder methods and cannot take nil. The first problem of each process is written to the driver's
per-process text line from the thread that hit it.

Each client process allocates GPU memory inside its own GPU address window, which
Turnip reports as the memory heap. The runtime pools memory in blocks of at most
a sixteenth of that heap: 4 MiB in an app's 124 MiB window on the Adreno 830,
64 MiB in the render server's (bridge patch 0018). Larger blocks left an app no
room for its first texture. `scripts/check-texture-atlas-host.py` checks the
frameworks' first textures in each window over the host kernel stack.

```sh
python3 scripts/build-turnip-darwin.py --target ios
python3 scripts/build-adreno-metal-plugin.py
```

See [Adreno740](/drivers/adreno740) for the runtime path, staging commands, and checks.
