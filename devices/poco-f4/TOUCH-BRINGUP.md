# Munch FT3658U touch

FT3658U coordinates were verified in UEFI, followed by device-confirmed iOS
input. The preferred recorded native image sleeps 4 ms between reads; the
original prototype slept 16 ms. This is a polling sleep, not a measured sample
rate or end-to-end latency. Sustained latency/stability measurements remain open.

## Hardware and protocol

The Android capture identifies runtime ID 0x5652, FocalTech driver V3.2,
SPI mode 0 and maximum 6 MHz. Reset/IRQ are TLMM GPIO33/39; GPIO8..11 provide
the prototype's bit-banged SPI. Android reports ten contact slots and axes
0..10799 / 0..23999. The XNU protocol normalizes decoded panel coordinates
0..1079 / 0..2399 to 0..65535.

`ft3658u-protocol.h` checks contact fields and CRC16 using reflected polynomial
0x8408. Runtime reads verify both response status and CRC. The driver checks
both data-line directions and requires chip ID bytes 0x56/0x52.

`collect-touch-info.sh` collects Android input, SPI, driver, selected power/pin
information and matching local firmware files. Captures stay outside Git.
Primary references are Xiaomi munch-s-oss `focaltech_3658u`, `focaltech_spi`,
`pinctrl-kona.c` and `spi-geni-qcom.c`. The vendor driver is GPL-2.0; retain
applicable attribution/licensing for derived code. Do not substitute another
controller's firmware.

## Power handoff

Reset release alone did not produce a verified response. Android observations
and the power probe identified disabled SID5 L1/L10 supplies. The proven opt-in
path requires L1 at 1800 mV and L10 within 3000..3312 mV, recognized PMIC LDO
register types, initially disabled enables and reset low. It changes enable
votes without changing voltage/mode selectors.

`touch-power-enable.inc` resolves ldoc1/ldoc10 from the bounded command database
at 0x80860000 and requires unique names and VRM address class 4. It uses APPS RSC
DRV2 at 0x18220000, offset 0xd00, stride 0x2a0, and only active TCS2..4.
Sleep slots TCS0..1 are excluded. An eligible slot must be idle with no enabled
commands or pending completion. IRQ/control/command state is saved and restored.

Requests are bounded to 200 ms. A live timed-out command is not overwritten;
further requests stop and a reboot is requested. L1 is enabled first, then L10
after 1 ms; PMIC ready bits are checked before reset release and contact reads.
Normal cleanup restores GPIO/reset state, disables supplies in reverse order
and verifies the original disabled state. This temporary mechanism is not a
complete RPMh framework and cannot recover a nonreturning hardware operation.

The r5 device result showed `TOUCH CONTACTS: PASS`, CRC-valid coordinates and
`TOUCH POWER: ORIGINAL DISABLED STATE RESTORED`. Primary power references are
Xiaomi's `rpmh-rsc.c`, `cmd-db.c`, `qcom-rpmh-regulator.c` and FocalTech sequencing.

## XNU driver

`touch/xnu-ft3658u.c` reads controller reports and posts normalized down/move/up
events through `hakios_touch_post`. Five consecutive failed reads release held
contacts. Invalid point counts do not dispatch events. It sends no controller
firmware, power or reset commands; GPIO writes clock the SPI transport.

Two build paths remain supported:

- `touch/kernel-overlay.py` inserts the temporary driver into dormant Redwood
  code in the exact SHA-pinned prebuilt. It checks Mach-O symbols, bounds code to
  [0xfffffff0074a4b80, 0xfffffff0074a5e2c) and reuses the old 1032-byte TX BSS area.
- `gpu/stage-kernel.py` stages native source behind `munch_touch=1`, with a
  selectable 16- or 4-ms polling sleep. This is the current GPU-image path.

The loader enables power, verifies the ID with CRC and retains the link through
handoff. Failure prevents starting XNU; returning from XNU restores touch state.
The old Redwood firmware recovery/button initialization is bypassed. Do not add
`touchscreen=1` or unrelated Redwood button flags.

## Checks

```sh
python3 devices/poco-f4/touch/check-xnu-events.py
python3 devices/poco-f4/touch/check-power-guards.py
python3 devices/poco-f4/touch/verify-image.py /path/to/image.img \
    --fd /path/to/storage-revision-3.fd --dtb /path/to/live.dtb
```

The C checks require a compiler and ASan/UBSan. `check-protocol.c` covers packet
parsing and coordinate endpoints. Event checks cover ordering, duplicate
suppression, invalid counts and missing contacts; power checks cover address/
value bounds, command DB parsing, slot preservation and timeout lockout.
The independent image verifier checks the container and pinned inputs for the
selected binary-overlay or native profile. Host checks complement device tests.
