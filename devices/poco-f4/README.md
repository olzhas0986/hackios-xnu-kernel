# POCO F4 (munch)

Experimental iOS 16.5 / 20F66 port. Device reports confirm SpringBoard,
Mesa/freedreno-backed Adreno 650 rendering and FT3658U touch input.
Horizontal tearing remains under investigation. Audio, Wi-Fi and modem support
are not established; sustained stability and the actual online CPU count have
not been measured.

The preferred recorded image is `munch-ios165-gpu-quiet-r1.img`: it requests
587 MHz GPU operation and eight CPUs, uses a 4-ms touch polling sleep, suppresses
screen logs and disables the deliberate ten-minute return to UEFI. It retains
the original framebuffer path. Physical 120-Hz switching is not implemented;
GPIO66 TE was measured at approximately 60.68 Hz.

The next device test is `munch-display-scan-r1.img`, a standalone UEFI probe.
Its TE configuration and three Window summaries are needed before adapting
controller-based presentation. See [display notes](display/README.md).

## Documentation

| File | Purpose |
| --- | --- |
| [LINUX-HANDOFF.md](LINUX-HANDOFF.md) | Build prerequisites, local artifacts and current next step |
| [BOOT-INPUTS.md](BOOT-INPUTS.md) | Reviewed Android/XBL inputs and memory reservations |
| [uefi/README.md](uefi/README.md) | Firmware overlay, extraction, build and diagnostic probes |
| [uefi/TEST-RESULTS.md](uefi/TEST-RESULTS.md) | Consolidated device results and unresolved observations |
| [loader/README.md](loader/README.md) | XNU/iOS packing, installation and verification |
| [TOUCH-BRINGUP.md](TOUCH-BRINGUP.md) | FT3658U protocol, power handoff and driver |
| [GPU-BRINGUP.md](GPU-BRINGUP.md) | A650 integration, SCM fixes and graphics profiles |
| [display/README.md](display/README.md) | Tearing investigation and scan-counter probe |

## Observed hardware

| Component | Observation |
| --- | --- |
| SoC | SM8250-AC; CPU MPIDRs 0x000..0x700 in steps of 0x100 |
| RAM banks | [0x80000000, 0xBCC00000), [0xC0000000, 0x200000000) |
| GICv3 | Distributor 0x17A00000; redistributors 0x17A60000 |
| Timer / PSCI | 19.2 MHz; virtual timer INTID 27; SMC |
| Display | 1080x2400, stride 4320 bytes, BGR; framebuffer 0x9C000000 |
| Panel | qcom,mdss_dsi_l11r_38_08_0a_dsc_cmd; command-mode OLED/DSC |
| GPU | Adreno 650, chip ID 0x06050002 |
| Touch | FT3658U SPI; ID 0x5652; reset GPIO33; IRQ GPIO39 |
| Touch supplies | PM8150A L1 1.8 V; L10 3.0..3.312 V |

RAM bank coverage does not imply free memory. Preserve firmware reservations,
including the larger XBL display reservation ending at 0x9E400000, and use the
EFI memory map when choosing the kernel window.

## Inputs and storage

`observed.json` records selected live-DT facts. Reproduce it with:

```sh
python3 devices/poco-f4/inspect-dt.py /path/to/device-tree.tar > /tmp/munch-observed.json
```

The Android collectors read device information and local firmware into archives.
Raw captures, vendor firmware, iOS images, identities and generated build
outputs stay outside Git. Captured boot images describe the installed slot and
may include root or earlier modifications.

The system APFS is staged on the dedicated 16-GiB `iosroot` partition. The
loader reads it into RAM; persistent iOS data is not configured. Existing small
RAM-boot variants reuse that installation. Only an explicit installer `--apply`
replaces its contents; see the loader instructions.
