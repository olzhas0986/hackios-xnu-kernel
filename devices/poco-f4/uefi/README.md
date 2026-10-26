# Experimental F4 UEFI source package

Revision 2 established EFI application execution and XNU diagnostic handoff.
Storage-enabled revision 3 read iosroot and is used by the iOS RAM-boot profiles.
SpringBoard, native touch and accelerated UI have since been reported; see
[device results](TEST-RESULTS.md) and the [current status](../README.md).

## Provenance

The platform package is adapted from `uefi/umi/files` in
`https://github.com/JopangJovan/hackios-xnu-kernel-umi`, commit
`6b6fb2b0c5a1720496ec71e88c95d2a45a0046a4`, against Mu-Silicium at `BASE`.
Upstream copyright and BSD-2-Clause-Patent notices are retained in derived
Project Mu files. UMI-specific code inherits that repository's APSL-2.0 license;
see the main repository's `LICENSE.APSL`. No vendor executable is included here.

F4 changes:

- SM8250-AC (`SOC_TYPE=2`), F4 model identity, and a 1080×2400 framebuffer.
- F4 XBL's 3 MiB FD at `0x9FC00000` and `SecPagePoolCount=0x980`.
- DDR hole `[0xBCC00000, 0xC0000000)`, matching the captured live DT.
- Explicit reservations for the TZ/PIL gap and pstore/display dump area.
- XBL's MCCC register window included.
- A separately reserved 64 MiB XNUP region at `0xF0000000`. This is a build
  choice requiring verification against the actual EFI map during bring-up.
- RAM-only payload hook; no filesystem or raw-partition log fallback.
- Vendor modules extracted from the target F4. The original TzDxe bytes are
  retained; the UMI-specific binary patch is not applied.
- Diagnostic revision 2 omits FeatureEnablerDxe after the first DEBUG device
  trace ended with a device-error assertion immediately after its load message.
  This isolates a possible inherited-clock/TA initialization problem; see
  `TEST-RESULTS.md` for evidence and limits.

The inherited framebuffer was observed at 0x9C000000 with 1080x2400 BGR output
and 1080-pixel stride. These observations validate the recorded device setup,
not arbitrary firmware revisions or controller-based presentation.

## Rebuild

Use a dedicated Mu-Silicium checkout at `BASE` with its pinned recursive
submodules. Install its `pip-requirements.txt` plus `uefi_firmware==1.16` in a
Python environment. Required host tools include clang, lld-link, nasm/ndisasm,
and iasl. The UMI build's local-nasm override may be used when NuGet/Mono is
unavailable; never substitute firmware modules from another phone.

From the HakiOS repository root, with an absolute `MU_ROOT`:

```sh
export MU_ROOT=/absolute/path/to/Mu-Silicium
export UEFI_PYTHON=/absolute/path/to/venv/bin/python
"$UEFI_PYTHON" devices/poco-f4/extract-xbl.py /path/to/xbl_b.img \
    --templates "$MU_ROOT/Binaries/alioth" --output "$MU_ROOT/Binaries/munch"
PREPARE=1 bash devices/poco-f4/uefi/build.sh
```

The extractor refuses an existing output directory. The build verifies the
reviewed XBL hash and extracted module hashes. Its output is a raw FD, not an
Android boot image, and must not be passed directly to fastboot.

## Diagnostic boot probe

`pack-diagnostic.py` compiles `diagnostic.c`, embeds it as the only XNUP entry,
and packages the FD plus captured live F4 DTB in a boot-header-v1 image. This
uses the UMI/Mu boot format that was accepted during the recorded device tests.
The installed Android header-v3 image is a separate packaging input.

```sh
python3 devices/poco-f4/uefi/pack-diagnostic.py \
    --fd "$MU_ROOT/Build/munchPkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd" \
    --dtb /path/to/live.dtb --output /path/to/munch-uefi-diagnostic.img
```

The probe prints GOP parameters, Qualcomm USB protocol lookup status, and large
conventional memory ranges. It checks for free descriptors overlapping the DDR
hole or payload reservation. Protocol presence does not verify USB transfers.
It attempts a cold reset after 90 seconds if execution reaches that point.
It never opens storage protocols or starts XNU. Photograph the displayed results.

Test only the packaged image with `fastboot boot`; no partition flash is needed.

## Validation and DEBUG builds

The recorded RELEASE/CLANGPDB firmware build produced a 3145728-byte FD.
Diagnostic applications compiled as ARM64 EFI executables with warnings treated
as errors. Device outcomes, including the initial failed probe and subsequent
successful revision 2, are consolidated in [TEST-RESULTS.md](TEST-RESULTS.md).

Set `BUILD_TARGET=DEBUG` to enable framebuffer debug output and assertions.
Package the corresponding DEBUG FD together with its `.munch.json` manifest.
The packer records the build target, image/payload hashes and shim size.
Generated artifacts and vendor executables remain outside Git.

## USB round-trip probe

Add `--usb-probe` to the packaging command to retain the same FD and replace
only the EFI application with a small USB test. Its descriptors and protocol
initialization sequence derive from the UMI loader under APSL-2.0; see
`usb-probe.inc`. It implements no storage or kernel commands.

After temporary boot, query it from the host within five minutes:

```sh
fastboot getvar product   # expected: munch-usb-probe
fastboot oem ping         # expected: pong
fastboot reboot
```

The screen reports USB initialization and transfer errors. Enumeration on
Windows may require binding the Android fastboot interface driver. A device
listed by fastboot alone does not establish a successful command round trip.

## Optional read-only storage bring-up

Set MUNCH_STORAGE=1 when invoking build.sh to include the pinned vendor
UFSDxe, DiskIoDxe and PartitionDxe modules. This produces FD diagnostic revision
3, distinct from the successful revision-2 kernel diagnostic baseline. Package
it using pack-diagnostic.py --root-probe. The application enumerates Block I/O,
connects those controllers recursively and reads the first 64 KiB of exactly
one iosroot GPT partition. It never calls write_blocks or flush_blocks. The
revision-3 device test reported a successful 64-KiB read. Full iOS RAM-boot
profiles subsequently used this storage-enabled FD.

### Shim overlap regression check

The shared bootshim now selects FD/payload copy order from runtime ranges and
uses aligned memmove semantics. It rejects source layouts that would overwrite
its running code. The existing fixed FD and payload destinations are unchanged.
With clang, llvm-readelf, aarch64-linux-gnu-objcopy and Python Unicorn available,
run `python3 devices/poco-f4/uefi/check-bootshim.py` from the repository root.
The test executes the actual compiled ARM64 shim and checks seven relocation
layouts, preserved x0/x1 and invalid-range rejection. Image manifests record
`shim_size`; old 208-byte-shim images remain supported by the inspector.

## Display observation probes

`--panel-te-probe` passively measures GPIO66 TE cadence; the recorded result was
approximately 60.68 Hz. `--display-scan-probe` observes INTF1 counter/configuration
state and three measurement windows. The latter is the pending device test:

```sh
python3 devices/poco-f4/uefi/pack-diagnostic.py --display-scan-probe \
    --fd /path/to/reviewed.fd --dtb /path/to/live.dtb \
    --output /path/to/munch-display-scan-r1.img
```

The scan probe may temporarily enable guarded display bus clocks and restores
them on ordinary return paths. It does not change DSI/DPU presentation registers
or start iOS. See [display notes](../display/README.md) for measurement limits.
