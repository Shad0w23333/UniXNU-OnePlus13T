# PaganiDiag

A diagnostic daemon for the OnePlus 13T iPhone-layout boots: it tells the BootProgress ramoops log why the deletable system apps
do not install. iOS user space is otherwise invisible here; the only channel is the kernel's boot log, read back after the reset.
For ten minutes from boot the daemon writes short lines through a kernel sysctl, which logs each as a BootProgress U line
`diag: ...`. Everything is off unless the Data seed carries the daemon.

Status: host tests pass (`Test/run.sh`), the kernel patch compiles with the build's own compile record, the daemon is built and
signed for iOS 17; not yet run on the device.

## How it starts

launchd of 21H461 takes every job below `/System/Library` from its signed cache `/System/Library/xpc/launchd.plist` on a normal
boot (only `rd=md0` or a Restore/Ramdisk `ReleaseType` makes it read job files; `sub_10000E904`, `sub_100017CE0` of `/sbin/launchd`),
so the owner overlay of `/System/Library/LaunchDaemons` (zzy-native-gpu-addon-mount.patch) adds no job and shadows none:
adrenoprobed never ran there (UniXNU-sheng `BootDiagExport.c`: "a launchd job that never runs"). The kernel patch therefore starts
the daemon itself: the first exec of `/usr/libexec/DumpPanic` by xpcproxy or launchd runs `/private/var/ngpu/bin/paganidiag` instead,
when that file exists and boot-arg `pagani-diag` is not 0. `com.apple.DumpPanic` is root, RunAtLoad, without MachServices, and
restarted only after a crash (its job files the previous boot's panic report, which this board does not keep); a later exec (the
restart after a crash) runs DumpPanic itself. The launch policy is not touched: `com.apple.bluetoothd` and `com.apple.iomfb_fdr_loader`
stay disabled. The launchd job `org.unixnu.pagani.diag.plist` still goes to `/private/var/ngpu/daemons` for a launchd that reads the
overlay; on 21H461 it is unused, and a second instance would stop at the lock (`/private/var/tmp/paganidiag.lock`).

The image needs boot-arg `sheng-no-lc=1` (`make-a830-layout.sh` adds it with `A830_DIAG`): launchd creates every job of its cache with
launch type 1, system service (`service_create`: the type is the job's system-service flag; xpcproxy passes it to
`posix_spawnattr_set_launch_type_np`), and the original AMFI's system-service launch policy (`site.LaunchTypeSystemServicePolicy`)
refuses an owner-trusted binary on the Data volume with "Launch Constraint Violation" (zzyc-owner-skip-launch-constraints.patch, which
skips the check with that argument). The daemon's own probes (launch type 0) run under the same argument. Without it the kernel still
logs `diag: launch ...` and then `diag: no line from pid N in 90 s`: the exec was refused after the redirect.

## What it logs

Every line is ASCII, at most 170 characters (the log lines; the kernel keeps 194 after its prefix), in one U record. Times `@N` are
seconds since boot (`kern.boottime`); a log line's time is that of the log entry.

| Line | When | Meaning |
| --- | --- | --- |
| `diag: launch pid N DumpPanic -> paganidiag` | kernel, at the start | the exec was redirected (`daemon error E, DumpPanic runs` when the file is not a regular file) |
| `diag: no line from pid N in 90 s` | kernel | the daemon wrote nothing (crashed, sysctl refused, or never got that far) |
| `diag: kernel limit of ... reached, ...` | kernel, once | over 300 lines a boot or 20 in 10 s; later refusals only counted (`kern.pagani_diag_dropped`) |
| `start v1 pid N @S uid U argv0 /usr/libexec/DumpPanic cfg 10m/30s/40/200[ bad KEYS]` | first | how it was started, and the configuration |
| `pN @S st <bytes>b n<count> <state>:<count>... lk n<count> <state>:<count>... mig M bun B stg G sen E lg <seen>/<kept>/<ms>ms[/np][ kd K]` | every pass | the summary, below |
| `pN not1 <state>:<id>,<id>... <state>:<id>...[ +N]` | pass 1, 9, 17 and when it changes | the apps of the state file in a state other than 1, `com.apple.` removed, 12 characters each |
| `mg fam <SupportedDeviceFamilies> dcn <DeviceClassNumber> dc <DeviceClass> pt <ProductType> hw <HWModelStr> arm64 <0/1>` | pass 1, 9, 17 | MobileGestalt's answers |
| `hw product <hw.product> target <hw.target> machine <hw.machine> model <hw.model> arch <cputype>/<subtype>` | pass 1, 9, 17 | sysctls and `HOST_PREFERRED_USER_ARCH` |
| `caps match=<0/1>[ mis=[...]] skip <0/1> fam <1 or 0 e<code> <domain> <description>>` | pass 1, 9, 17 | `MobileInstallationCheckCapabilitiesMatch(@[@"arm64"], nil)`; `MIDaemonConfiguration.skipDeviceFamilyCheck` for root; `MIIsApplicableToCurrentDeviceFamilyWithError(@[@1,@2])` |
| `force arr N str M own <uid>:<gid> <mode> skipfam mob <v> root <v> logpref <seed/made/off/fail E>` | pass 1, 9, 17 | the force install list read with `+[NSArray arrayWithContentsOfURL:error:]` as the migrator does (or `e<code> ...`), its owner and mode, `SkipDeviceFamilyCheck` in mobile's and root's preferences, the logging preference |
| `<tag> <L>@<S> <message>` | every pass | a unified-log entry, below |
| `suppressed N dup D` | a pass that left lines out | N new lines over the limits, D repeats of lines already logged |
| `probe <facts/state/log> timeout/sig N/exit N` | when it changes | a child probe that did not end well |
| `log <error>`, `log skipped N s` | once each | the log store, predicate or enumerator failed; a window the log probe could not read in time |
| `end pN @S log L sup S dup D sent N kd K` | last | totals |

Summary fields: `st` the installd state file `/private/var/installd/Library/MobileInstallation/BackedUpState/SystemAppInstallState.plist`
(`-` absent, `unreadable`, `bad`: not a dictionary; else its size, the number of apps and how many are in each state); `lk` installd's
answer to `MobileInstallationLookupSystemAppState(nil)` (`-` not available, `nil`, `fail`: probe timeout or crash); `mig`
`MobileInstallationGetSystemAppMigrationStatus` (1 done, 0 not, `e` error, `-` not available); `bun` entries of
`/private/var/containers/Bundle/Application` (one per installed app); `stg` `.app` entries in `/private/var/staged_system_apps`; `sen` the
migration sentinel `/private/var/datamigrator/migrate`; `lg` the previous log pass: entries read, entries kept, milliseconds (`/np`: the
predicate was refused and every entry was read; `to` timed out); `kd` lines the kernel refused.

Log lines: the entries of com.apple.migrationpluginwrapper (`mpw`), com.apple.datamigrator (`dm`), installd (`ind`) and
installcoordinationd (`icd`), the com.apple.migration subsystem, and messages with `SystemApp` from any process (other processes: their
name without `com.apple.`, 12 characters). Levels: `I` info, `N` notice (os_log default), `E` error, `F` fault. Info is kept because
DataMigration's `_DMLogFunc` logs every level as `OS_LOG_TYPE_INFO` (its `__CPLog_os_trace_type_map` is eight 0x01 bytes in 21H461):
every `MISystemAppMigrator:` line is info. The messages are shortened: MobileInstallation's thread address dropped,
`-[Class selector:...]: ` written `Class.selector: `, `MISystemAppMigrator: ` written `SAM: `, every `com.apple.` removed, control and
non-ASCII characters as spaces or `?`, a cut line ends in `~`. Example: `mpw I@312 SAM: Skipping installation of weather because it is
already uninstalled`. A line is logged once a boot (the time does not count). Classes decide what is left out first: 0 the `SystemApp`
lines of the four processes and of the com.apple.migration subsystem, and the four processes' errors and faults; 1 installd's and
installcoordinationd's other notices (at most 80 a boot); 2 the migration processes' other lines, the other com.apple.migration lines
and other processes' `SystemApp` lines at notice or above (at most 40). At most 40 log lines a pass and 200 a boot.
installd's notes of this daemon's own requests (`requested by paganidiag`) are left out.

The com.apple.migration subsystem keeps no info lines by default (its level inherits the default); the seed brings
`/private/var/preferences/Logging/Subsystems/com.apple.migration.plist` (`Enable` and `Persist` `Info`; `/Library/Preferences/Logging` is
that directory), and without it the daemon writes the same file before the migration starts and posts
`com.apple.system.logging.prefschanged` (`logpref made`). libsystem_trace reads the file without checking its owner.

## Budget

The kernel takes 300 lines a boot and 20 in any 10 s; the daemon leaves 0.55 s between lines (never more than 19 in 10 s) and retries a
refused line twice after 2 s. At the defaults a boot writes at most about 270 lines: start and end, 4 facts lines 3 times, a summary
line a pass (and a few `not1` lines) for at most 20 passes, 200 log lines and up to 20 `suppressed` lines. A record is 34 bytes plus the text (`diag: ` and up to 170
characters): the log lines alone can fill the 32 KiB events ring (`RAMOOPS_LOG_EVENTS_BYTES`) and push out earlier U lines; the facts are
repeated on passes 9 and 17 and the summary every pass so that they are in the ring's last window. Lower `boot_lines` for a shorter
footprint. Every 30 s the log probe opens the system log store (logd snapshots its in-memory buffers for it) and reads the entries since
the previous pass through a predicate; the daemon waits at most 27 s for it and it stops reading after 24 s. The daemon runs at
nice 10.

## Configuration and turning it off

`/private/var/ngpu/bin/paganidiag.conf` (optional; `make-phone-seed.py --diag-daemon` copies `paganidiag.conf` from the build
directory when it is there), `key=value` lines, `#` comments: `minutes` (10, 0..60; 0 writes only start and end), `period` (30 s),
`pass_lines` (40), `boot_lines` (200), `notice_lines` (80), `info_lines` (40), `facts_every` (8), `gap_ms` (550), `log` (1: read the
unified log), `logpref` (1: write the logging preference when the seed has none). Unknown keys and bad values are named in the start line.
A manual run (as root on the phone, or on a Mac for the control flow) prints instead of writing to the kernel:
`PAGANIDIAG_CONFIG=<file> paganidiag --print` (the variable is read only with `--print`).

Off: build the seed without `--diag-daemon` (no daemon file: DumpPanic runs as always, the kernel logs nothing; without `A830_DIAG`
the layout script adds no `sheng-no-lc`); or boot with `pagani-diag=0` (the kernel does not redirect); or `minutes=0` in the
configuration. Without phone-diag-sysctl.patch the daemon's first write fails and it exits (status 3).

## Files

- `DiagCore.h`, `DiagCore.cpp`: what the lines say and how many (filter, compaction, de-duplication, rationing, pacing, fact and summary
  lines); no device APIs.
- `PaganiDiag.mm`: the daemon. Everything that talks to another daemon or loads foreign code runs in a child (`--probe facts`,
  `--probe state`, `--probe log`) that is killed after a timeout: MobileGestalt and MobileInstallation through `dlopen`, the device family
  check from `/System/Library/DataClassMigrators/SystemAppMigrator.migrator/SystemAppMigrator` (the bundle exports its copy of
  `MIIsApplicableToCurrentDeviceFamilyWithError`; InstalledContentLibrary keeps its own local), and the system log store:
  `+[OSLogStore storeWithScope:error:]` of iOS 17 answers only the current process (any other scope: nil without an error), so the
  probe wraps LoggingSupport's local store (`+[OSLogEventStore localStore]`, logd's in-flight buffers through its admin connection) in an
  `OSLogStore` as `+[OSLogStore storeWithURL:error:]` wraps an archive (`-initForFactory`, `_source`); `log store scope - ...` names
  why when that fails. `paganidiag --print` writes the lines to standard output.
- `paganidiag.entitlements`: `com.apple.private.security.no-container`, `com.apple.private.logging.admin`/`.diagnostic`/`.stream` (as
  adrenoprobed), `com.apple.private.mobileinstall.allowedSPI` `LookupSystemAppState` and `GetSystemAppMigrationStatus` (installd's
  `_ValidateMIAllowedSPIEntitlement`; `checkCapabilities` needs none).
- `seed/`: the launchd job and the logging preference.
- `kernel/pagani_diag.h` (the line and the limits, pure C), `kernel/kern_exec-diag.inc` (sysctls and the start, inserted before
  `exec_activate_image`), `kernel/phone-diag-sysctl.patch` (made by `scripts/make-diag-patch.py` from the built tree's `kern_exec.c`;
  in the device series after phone-keyboard-trace.patch).
- `scripts/build-paganidiag.py [--out DIR]`: build, sign (4 KiB pages), check; `scripts/make-diag-trust.py BASE OUT DIR`: an owner trust
  bundle with the daemon's CDHash added.
- `integration/make-phone-seed.diff` (`--diag-daemon DIR`) and `integration/make-a830-layout.diff` (`A830_DIAG=DIR`), for 工具/.
- `Test/run.sh`: the core and kernel C tests with ASan/UBSan at -O0 and -O2, then `Test/check-paganidiag-host.py` (patch applies and
  compiles with `kern_exec.o.json`, build and signature, trust bundle, seed staging on a copy of a phone seed, layout dry runs).
