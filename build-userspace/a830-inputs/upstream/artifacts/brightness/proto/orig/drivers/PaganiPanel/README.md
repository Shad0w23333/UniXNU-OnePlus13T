# PaganiPanel

DSI brightness and display blanking for the OnePlus 13T AA594_P_7_A0020 panel. Enable with `pagani-panel=1`. Stage7Probe loads AppleARMPlatform and registers an owned subclass with genuine `AppleARMBacklight` ancestry; its constructor, storage size, destructor and 169-slot class extent are checked against the pinned 21H461 image. The subclass uses the ordinary IOService lifecycle and guards the unqualified extra virtual method.

`setProperties` accepts an integer `brightness` from 0 to the board's normal maximum, 0xdbb. The worker sends DCS 0x51 with the panel's low-byte-first brightness payload. Values are published only after command completion. The display PM callback requests DCS 0x28/0x29 for blanking and unblanking. These commands retain the existing clocks, PHY, DSC configuration and GOP scanout; they do not power down panel rails.

`Sm8750Platform/PeripheralIO.h` uses the DSI command FIFO, waits for the command path to be idle and preserves unrelated interrupt status. A completion timeout disables subsequent requests and leaves the outstanding command untouched. The first brightness command must succeed before the service advertises `backlight-control`.

The current refresh source remains the existing 60 Hz software presentation timer. Logical display-off pauses notifications, and display-on starts a fresh deadline. No panel timing, DSC mode or refresh rate is changed. TE/vblank synchronization and high-refresh modes require hardware measurements.

The portable tests cover packet byte order, register preservation, busy engines and completion timeouts. They do not verify physical luminance, CoreBrightness sandbox access, display-off/on behavior or coexistence with the firmware scanout. FadePeriod interpolation and full panel suspend/resume are not implemented.

## Display power for the other drivers (`pagani_screen_state`)

The display PM callback (`pagani_panel_set_power`, called by NativeLegacyDisplay's `setPowerState`) also publishes the system's display power in the weak word `pagani_screen_state`: bit 0 set while the display is off, the number of changes above it, so a reader that missed an off and an on still sees the count move. It is written whatever `pagani-panel` says. PaganiArmCpu's governor (`pagani-screenoff` bit 0) and Adreno830's screen rule (bits 1 and 3) read it; each carries the same weak zero definition, so the kernel link keeps one word and a build without this driver reads the display as on.

## Sleep mode while the display is off (`pagani-screenoff` bit 2)

Default off: the display PM callback then sends 0x28 and 0x29 alone, exactly as before (the tick takes the old path). With bit 2, `PanelPower.h` runs the panel's own sequences from the PKX110 live device tree (`qcom,mdss_dsi_panel_AA594_P_7_A0020_dsc_cmd`, the same in all four timings), one at a time, each to its end, honouring the DT's delays on the 16 ms tick (the timer is armed for a shorter delay when one is due earlier):

- off: display off 0x28, 20 ms, enter sleep 0x10, 120 ms (the DT's `qcom,mdss-dsi-off-command`). Android then turns the rails off; here the rails, reset, DSI, PHY, DSC and the scanout stay as the boot loader left them.
- back: exit sleep 0x11, 120 ms, then the 51 writes the vendor sends after exit sleep in `qcom,mdss-dsi-on-command` (page 0x51/0x52 tables, identical in every timing, unlike the part before exit sleep, which the boot loader wrote and the panel keeps), page 0, display on 0x29; the brightness is written again right after.
- a display asked back on between 0x28 and 0x10 skips the sleep; an on during the 120 ms after 0x10 waits for them (MIPI DCS); an off during the wake runs the wake to its end first.

Packets go through `dsi_panel_packet` (`Sm8750Platform/PeripheralIO.h`: DCS short 0x05/0x15 and long 0x39 writes up to 16 bytes, the same FIFO, engine checks and register restore as `dsi_panel_command`, which keeps its own code). A failed packet disables further requests as every DSI failure does; the panel then stays as it was (asleep: dark until reboot). Each finished sequence and a failure go to the ramoops log (`U` records: `panel: display off, sleep in in 141 ms (asleep 1, woken 0, packets 2)`, `panel: sleep out, vendor writes, display on in 137 ms ...`, `panel: DSI failed -110 at packet 0 of the wake sequence; requests disabled`); the registry shows `PanelSleepMode` and `PanelAsleep`. Whether the MDP's autorefresh pauses while the panel sleeps (its TE stops) and whether exit sleep with these writes restores the picture are hardware questions; the option stays off until a device test.

Host test (sequences on a virtual 16 ms tick, the wake table pinned by CRC-32 to the DT, `dsi_panel_packet` against the DSI register model, including its equivalence with `dsi_panel_command` for 0x28/0x29/0x51): `xcrun clang++ -std=c++17 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=all -I <UniXNU-sm8750-common>/kits/Sm8750Platform Test/panel-power.cpp -o /tmp/panel-power && /tmp/panel-power`.
