# F4 XNU loader and iOS RAM boot

The loader has executed XNU through early BSD initialization, a static RAM-root
hello program, and iOS 16.5 SpringBoard on the device. Native GPU/touch profiles
are described in [GPU-BRINGUP.md](../GPU-BRINGUP.md). Device results are kept in
[TEST-RESULTS.md](../uefi/TEST-RESULTS.md).

The source overlay targets upstream `xnuloader/loader.c` at `2404c65`, SHA256
`7104a1403bb0007a8fc7a7cf4dfb327bc7136a09ba9479f17e5eca449499557b`.
The loader retains its MIT license; XNU remains APSL-2.0. Diagnostic images use
a placeholder identity. iOS mode uses a local IPSW-derived identity kept outside Git.

## Packing

Required tools include clang, lld-link, patch, fdtget and
aarch64-linux-gnu-objcopy. Use absolute paths for inputs and outputs.

```sh
python3 devices/poco-f4/loader/pack-checkpoint.py --help
```

The packer verifies its pinned loader/kernel or native manifests, FD metadata,
and captured live-DTB model. It compiles an ARM64 EFI loader, embeds an XNUP
payload and packages the shared bootshim and FD in an Android header-v1 image.
The payload is bounded by its reserved RAM capacity.

For an early single-CPU diagnostic:

```sh
python3 devices/poco-f4/loader/pack-checkpoint.py \
    --fd /path/to/revision-2.fd --dtb /path/to/live.dtb \
    --checkpoint 5 --output /path/to/munch-checkpoint5.img
```

The CLI lists the supported checkpoints, including later bootstrap stops and
modes 57/58. Early probes publish only the current CPU. A successful checkpoint
confirms the path to that stop and return, not all later initialization.

For a native GPU/touch iOS image:

```sh
python3 devices/poco-f4/loader/pack-checkpoint.py \
    --fd /path/to/storage-revision-3.fd --dtb /path/to/live.dtb \
    --checkpoint 58 --cpus 8 --touch --no-early-bootargs \
    --ios-config /path/to/ios.json \
    --native-gpu-config /path/to/native-gpu.json \
    --output /path/to/munch-ios165.img
python3 devices/poco-f4/touch/verify-image.py /path/to/munch-ios165.img \
    --fd /path/to/storage-revision-3.fd --dtb /path/to/live.dtb
```

Native configs pin the kernel and source report. The `munch-a650-ios-gpu`
profile also pins the graphics report and matching helper. The diagnostic
`munch-a650-diagnostic-22` profile runs the bounded drawing test instead.

`--cpus 8` restores PSCI-affinity-filtered enumeration and requests eight CPUs;
actual online count needs runtime evidence. The preferred quiet profile uses
`loader_quiet=true`, `verbose_console=false`, `return_after_seconds=0`, and the
reviewed GPU speed index 5. A zero return interval disables the deliberate
session timer; it does not prevent panic returns.

## Recovering the supplied pre-GPU image

`recover_ramboot.py` supports the inspected `munch-ios165-ramboot.img` only,
SHA256 `86c028bfa606e296e4e3e0e35121f88e2032009aba30d95a94a2f6b182b939eb`.
It checks the entire image and loader hashes before extracting the FD, live DTB,
base helper and iOS identity tables. Table addresses/counts and system-length/
CRC instructions were checked in that loader. The generated `ios.json` retains
its existing 2,476,732,416-byte system read and CRC32 `81ec3d39`; the system itself
is not downloaded or read on the host. This does not validate what is currently
on the phone; the rebuilt loader performs the original CRC check at boot.

```sh
python3 devices/poco-f4/loader/recover_ramboot.py /path/to/munch-ios165-ramboot.img \
    --output /path/to/new-recovered-directory
```

The recovered FD metadata names its reference hash rather than inventing a
build revision. The packer reopens the pinned reference and checks FD, DTB,
identity and system parameters before accepting the recovered config. For a GPU
image, replace that config's root entry with the verified new GPU helper and use
a matching native GPU config. The old loader cannot simply be reused: its
command-line guard and single-core publication would reject the new profile,
and it lacks the native touch/SCM handoff. The packer rebuilds it and uses the
corrected bootshim. The original image stays unchanged.

GPU/scan r1 uses the device firmware archive supplied locally, Mesa 25.0.7,
`display_scan=true`, `display_te=false`, `compositor_hz=60`, GPU speed index 5,
4-ms native touch polling, and the quiet loader with no fixed session-return
timer. `prepare-gl-helper.py --no-compression` builds a plain APFS helper when
Python multiprocessing IPC is unavailable; the roughly 20-MiB helper fits the
existing RAM limits and is still checked by apfsck and complete content readback.
No generated identity, firmware or helper bytes belong in Git.

## Handoff and return

The overlay requires the observed F4 GOP geometry, BGR format and stride, uses
EFI-map-based allocation, keeps logs in RAM, and removes raw-partition logging.
Redwood-specific watchdog/charger paths remain disabled by platform identity.
Early diagnostics pause before attempting a reset; later return timers cannot
recover a hang before their initialization.

The original checkpoint-10 C return hung after checkpoint 9 had returned.
The overlay skips an early `PE_parse_boot_argn("bootcpu", ...)` call before
Platform Expert initialization. In the pinned binary this replaces the guarded
BL at VA 0xfffffff007456160 with `mov w0, #0`; the native source staging applies
the equivalent current-core bypass. This is not a general alternate-core fix.

The native loader reserves 64 KiB below 4 GiB after the kernel extent for SCM
metadata and publishes `/chosen/munch-scm-metadata`. It checks the reservation
against the helper and resident-image layout before handoff.

## System and helper images

Mode 58 uses a local iOS 16.5 / 20F66 configuration. Source members are checked
against Apple-CDN ZIP sizes/CRC32 and recorded with SHA256. This checks archive
integrity, not an independent Apple signature trust chain. The initial system
was 2476732416 bytes; the later Mesa helper is separate from the initial 4-MiB
helper. Keep the helper matched to its graphics/configuration manifests.

`ios-root.inc` requires exactly one GPT `iosroot` partition, validates block
geometry and contiguous RAM capacity, reads the configured system length and
checks APFS NXSB magic plus compiled CRC32. It does not call WriteBlocks or
FlushBlocks. System and helper are supplied as RAM disks through the upstream
resident-image path. CRC32 is a corruption check, not authentication.

Touch handoff enables the verified supplies, releases reset, checks ID 5652
with CRC and retains the link into XNU. A failed check prevents handoff.
The loader restores touch state when XNU returns. Do not add unrelated upstream
Redwood touch/button flags.

## Installation

Existing small boot variants reuse the installed `iosroot`; rebuilding a boot
image does not require replacing it. To intentionally replace the system,
`make-install-package.py` packages local inputs and generates an Android/recovery
installer from `install-iosroot.sh.in`.

The installer requires root, device identity `munch`, exact
`iosroot -> /dev/block/sda35`, an unmounted target without device-mapper holders,
correct image size/SHA256 and sufficient capacity. `--check` validates;
`--apply` overwrites the dedicated partition and verifies a full image-length
SHA256 readback. Other Android partitions are not installer targets.

Known packaging limitation: the packager checks the boot image's own hash but
does not bind its `ramroot_sha256` to the supplied iOS config or record a complete
system/config binding. Keep boot/system/helper manifests from the same build;
ZIP integrity alone does not establish their compatibility.

Boot the verified Android image with `fastboot boot IMAGE`. A raw FD is not a
boot image. No persistent iOS data setup is provided.

Wi-Fi return diagnostics can explicitly set `wifi_usb_log: true` in the native
config to expose a five-minute read-only fastboot RAM-log server after cleanup.
See [Wi-Fi USB log instructions](../wifi/README.md#usb-ram-log-export-r1).
The general USB dispatcher stays disabled. `check-wifi-usb.py` tests export
framing, command restrictions and failed transfer handling under ASan/UBSan.
