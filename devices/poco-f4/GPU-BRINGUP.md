# Munch Adreno 650

Device reports confirm the r4 hardware drawing test and Mesa/freedreno-backed
iOS UI after the bootshim overlap fix. Horizontal tearing remains unresolved;
GPU-backed rendering does not establish synchronization with panel scanout.
See [display notes](display/README.md) for the pending controller measurement.

## Source and hardware

The adaptation uses the UMI A650/Kona work at
`6b6fb2b0c5a1720496ec71e88c95d2a45a0046a4`; the matching userland reference is
`1ce76c12717e2926651afa847a7e8e5307ab2156`. `gpu/umi-a650.patch` adapts it to
upstream `hakios_*` symbols and the HakiosGPU service ABI. UMI's HKGPU userland
interface is not interchangeable with HakiosGPU.

The phone reports Adreno650v3, chip ID 0x06050002, and Android frequencies
305/400/441.6/490/525/587/670 MHz. Packed profiles allow only reviewed indices
1 (305 MHz) and 5 (587 MHz); 670 MHz is not added to the kernel table.
GMU GFX/MX rail dependencies come from CMD DB.

`collect-gpu-info.sh` collects identity, clocks, filtered logs and local firmware:
a650_gmu.bin, a650_sqe.fw, a650_zap.mdt and a650_zap.b00/.b01/.b02.
The recorded capture contained all six files with consistent split-ELF structure.
Structural/hash checks do not validate the vendor signature; TrustZone performs
runtime authentication. Firmware stays outside Git.

## Build and helper

`gpu/stage-kernel.py` requires the prepared XNU 8796.121.2 base
`c54f35ca767986246321eb901baf8f5ff7923f6a` and checks the upstream GPU source hash.
It applies the A650 patch, stages FT3658U behind `munch_touch=1`, bypasses the
premature bootcpu query and selects baseline or opt-in TE framebuffer staging.

```sh
python3 devices/poco-f4/gpu/stage-kernel.py /path/to/prepared-xnu \
    --touch-poll-ms 4 --report /path/to/source-report.json
bash xnu-build.sh -j8 BUILD_DSYM=0 build
```

Run the build with the prepared toolchain environment. The kernel `build` target
previously passed; optional `all` packaging encountered dSYM/export/debugger
installation failures. `XNU_HOST_CFLAGS` and `XNU_HOST_CLANG_DIR` select host
headers and compiler paths.

Mesa 25.0.7 is built through the upstream Darwin/freedreno integration.
`gpu/prepare-gl-helper.py` builds the EAGL bridge and GL test, stages device-local
firmware and runtime dependencies, then builds/checks a separate APFS helper:

```sh
python3 devices/poco-f4/gpu/prepare-gl-helper.py \
    --base /path/to/base-helper-directory --mesa /path/to/mesa-output \
    --sdk /path/to/ios-sdk --output /path/to/new-helper-build
```

The script checks ARM64 identity, install names, required exports, dependencies
and HakiosGPU/freedreno markers. Private freedreno symbols are hidden by Mesa's
export policy. The recorded bridge has 409 entries: 356 forwarded, 30 implemented
by hand and 23 without a Mesa function. Those existing bridge limitations remain.
The recorded GL helper passed APFS checks and content readback of all 468 entries.

Use graphics.json's helper hash in the iOS config and pin the graphics report in
the native `munch-a650-ios-gpu` config. Revision 4 or newer is required for the
SCM continuation fix. The packer validates Mesa version, service and helper hash.
The installed system APFS does not need replacement for a helper/boot change.
See [loader instructions](loader/README.md) for packing and independent checks.

## SCM metadata and continuation

The native loader reserves 64 KiB below 4 GiB after the final kernel segment and
publishes its base/size in `/chosen/munch-scm-metadata`. The driver validates
alignment, size and bounds, maps normal noncacheable 16-KiB slots and uses a fresh
slot per PAS metadata submission. At most four submissions fit. Without the
property, the bounded low-DMA allocator remains the fallback.

This reservation addresses the observed allocation failure: the multi-gigabyte
resident APFS image places topOfKernelData above the available low VM pages.
The address is derived from the loaded kernel extent, not a fixed kernel write.
SMC32/64 availability negotiation preserves call type and owner.

The inherited 100-attempt SCM continuation limit produced a local -1 while
TrustZone was still returning QCOM_SCM_INTERRUPTED. The r4 path retains A6 state
across calls and resumes with x0=1, bounded by 1,048,576 calls and five seconds
between returns. A single nonreturning SMC is not bounded by that timer.
`LOCAL SCM RESUME TIMEOUT` uses local status -10001, separate from vendor results.
Primary behavior references are Xiaomi munch-s-oss
`drivers/firmware/qcom_scm-64.c` and `qcom_scm.c`.

`gpu/check-scm.py` checks width normalization, call-type preservation, reservation
validation, continuation beyond 100 calls and time/call limits with mocked host
execution. The r4 drawing result subsequently confirmed hardware commands ran.

## Profiles and remaining work

| Profile/image | Recorded behavior |
| --- | --- |
| A650 diagnostic step 22 / drawing r4 | Orange, white and blue bands reported |
| iOS GPU r2 | SpringBoard and much smoother accelerated UI reported |
| 587-MHz comparison | Requests reviewed speed index 5; does not change physical display timing |
| Session r1 | Disables ten-minute return; enables synthetic display notifications and GL wait |
| TE-staging r1 | Felt slower and did not resolve tearing; rolled back |
| Quiet r1 | Preferred recorded image; baseline framebuffer, 4-ms touch sleep, screen logs suppressed |

The original iOS GPU image failed before XNU with a UEFI exception. An independent
bootshim FD/payload overlap defect was reproduced and fixed; the r2 image then
reached accelerated UI. The exact original runtime overlap was not captured.
See [test results](uefi/TEST-RESULTS.md) for the evidence and limits.

The framebuffer uses CPU copies and synthetic VSync notifications. `ca_vbl=1`,
`glwait=1` and `fb_hz=120` do not switch the panel or implement hardware page flips.
Physical 120 Hz, sustained stability, actual online CPU count and a verified
tear-free presentation interval remain open.
