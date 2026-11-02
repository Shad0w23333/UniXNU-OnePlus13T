# DarwinBoot

UEFI bootloader for UniXNU. Loads the payload, kernel, device tree and trust cache, then starts XNU.

Current state: boots from the firmware volume and supports the packed RAM disk handoff. Takes arm64 and arm64e kernels, plain or as an
`MH_FILESET` kernel collection, and hands `<kernel>.vroot` (root policies) to the kernel through `/chosen/memory-map/RootPolicies`.

Built as part of the firmware volume.
