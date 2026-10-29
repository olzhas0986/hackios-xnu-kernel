# Munch display synchronization

## UMI command-DMA staging (new, device testing pending)

`gpu/stage-kernel.py --command-staging` stages the UMI-derived framebuffer
variant and `munch-command-scan.inc`. Shared upstream sources stay unchanged.
This mode is separate from the unsuccessful GPIO experiment below; the two
staging switches are mutually exclusive.

The reference is the supplied `umi-reference` device overlay, specifically
`devices/xiaomi-mi-10/overlays/xnu/files/bsd/dev/hakios.display.c` (SHA256
`b71fb15051e48100e37b90a2c322f4ed304f20392a4bcd1dc38b872e713cf0ce`) and
`iokit/Kernel/MiniIOMobileFramebuffer.cpp` (SHA256
`1a2f92a35d89975b63574cb11a60c2bb860ce239fe04d73aa19fba24b89b6fae`).

`fb_scan=1` activates the path only with loader identity `munch` and the known
1080x2400 framebuffer with 4320-byte stride. It checks power, register bus
clocks, command-mode TE configuration and initial height 2400, then measures
four TE wraps, line timing and the last observed active DMA phase. Only the
inherited 55–65 Hz mode is admitted. It does not switch panel mode or program
DSI/DPU timing. The two register-bus clocks remain on after successful setup;
failed setup restores clocks enabled by this attempt and retains the baseline.

Workers compose all layers into a private 10,368,000-byte buffer. SwapEnd waits
until the source pixels have been consumed; there is no early timeout allowing
CoreAnimation to reuse a surface still being read. A GPU timeout skips the
staged frame. Once staged, rendering of the next frame can overlap the panel
wait. Recording retains ownership until its source sampling finishes.

The single front-buffer copy requires completed DMA, a phase beyond the
measured active interval plus 32 lines, and enough remaining time for the
measured copy duration plus 25% and 500 us. The first copy-time measurement
writes an identical image during setup. Later measurements retain the largest
observed duration. Two phase reads surround DMA status; a wrap or a long
sampling interruption rejects admission. Most of the wait sleeps. A missed
window retains the previous displayed frame. Loss of clock/power or an invalid
phase after activation suspends front copies until reboot. Refresh notifications
follow the measured TE phase.

```sh
python3 devices/poco-f4/gpu/stage-kernel.py /path/to/prepared-xnu \
    --command-staging --touch-poll-ms 4 --report /path/to/source-report.json
bash xnu-build.sh -j8 BUILD_DSYM=0 build
```

Re-pin the rebuilt kernel and source report in a new native GPU config. Set
`display_scan=true`, `display_te=false`, `compositor_hz=60`; the packer adds
`fb_scan=1`. Use the existing native iOS packing command and independent
`touch/verify-image.py` verifier. The source report pins both new overlay files;
the verifier checks their hashes and the command-mode kernel marker. Omitting
both staging switches restores the original framebuffer source for rollback.

`ASAN_OPTIONS=detect_leaks=0 python3 devices/poco-f4/display/check-command-staging.py`
passed patch application, baseline/TE/command switching, preservation of
independent framebuffer edits, packer/verifier flag checks, and native tests
of the actual scan reader with mocked MMIO/time under ASan/UBSan. These cover
clock/power guards, calibration, DMA busy state, copy budget, TE wraps, sampling
stalls and bounded waits. LeakSanitizer was disabled because it cannot run
under the execution environment's tracing; address/undefined checks ran.

The command overlay was staged into XNU and the native kernel build target
passed with `BUILD_DSYM=0` on Fedora 44. The resulting ARM64 Mach-O and source
hashes were checked; artifacts and logs are in `out/munch-command-staging/`.
The GPU/scan r1 boot image was packed from the recovered reference FD/DTB and
identity, rebuilt Mesa and device-local firmware; independent image verification
passed. Its plain APFS helper passed apfsck and readback of all 468 entries.
The corrected bootshim passed seven emulated relocation layouts and rejection
tests. Device validation of this new image is still pending. Measured
copy budgets cannot bound all scheduler stalls or memory contention; this
remains an experimental CPU copy path, not an atomic page flip or a proven
tearing fix.

## Device feedback and r2 follow-up

The user booted GPU/scan r1 and still observed tearing. There was no runtime
evidence that TE calibration had succeeded; boot success alone does not prove
scan synchronization was active. A follow-up source audit found that r1 staged
`hakios_gpu_from_memory()` in the Adreno C driver but omitted the UMI consumer
changes in `hakios.gpu.cpp` and `MiniIOSurface.h`. Consequently CPU-side surface
reads did not invoke the new invalidation routine after GPU writes.

The r2 staging script applies `gpu/umi-surface-cache.patch`, matching those two
UMI overlay files exactly: tracks GPU write submissions, flushes write-target
surfaces before submission, checks for newer fences while waiting, and
invalidates the CPU view once a stable GPU completion is observed. Native
mocked-fence tests passed under ASan/UBSan, including timeout, changed fences,
bounded retries and invalidation exactly once. This corrects a source-coherency
omission; it is not proof of a physical scanout tearing fix.

GPU/scan r2 also enables diagnostic `fb_scan_hud=1` (native config
`display_scan_hud=true`). A four-line overlay reports `SCAN ACTIVE`/`SCAN OFF`,
reason, inherited TE configuration, measured rate in millihertz, front-copy
budget in microseconds, submitted frame count and missed windows. Miss counts
reset with the existing five-second statistics window. The overlay is composed
before the front copy; after scan loss, only its small diagnostic rectangle is
updated directly so the failure remains visible. This is a diagnostic build.

Reason codes: E0 active; E1 boot argument disabled; E2 board/geometry gate;
E3 power off; E4 register bus clock unavailable; E5 mapping/register guard;
E6 unsupported video mode; E7 TE configuration mismatch; E8 initial height
mismatch; E9 calibration timeout/sampling interruption; E10 missing counter/DMA
samples; E11 unexpected period; E12 derived geometry/idle margin; E13 runtime
power/clock loss; E14 phase outside calibration; E15 staging allocation failure.

The r2 native kernel build and independent image verifier passed. The Mesa
helper and panel timing are unchanged from r1. A photo of all four HUD lines
is needed to establish runtime mode before changing panel-specific thresholds.

## E12 feedback and r3 calibration correction

The user's r2 HUD read `SCAN OFF E12`, `TE 2400 CFG 180085 AUTO 80000001`,
`RATE 0 COPY 4000 US`, `FRAMES 2247 MISSES 0`. This proves the command scan
path rejected calibration and retained baseline presentation. The displayed
rate was still zero because r2 assigned it only after the rejected geometry
check; 4000 us was the initial budget, not a measured copy time. No staged
presentation attempts occurred. E12 combined several tests, so this report
does not distinguish a too-short phase count from a too-late DMA idle margin.

R3 removes the inherited `scan.lines >= scan.shown` assumption. In command
mode `scan.lines` describes timer-phase ticks, while `scan.shown` is the DMA
write-pointer completion threshold in physical rows. The comparison between
these distinct counters is unnecessary; a phase cycle below 2400 ticks can
still have a measured DMA idle interval. No panel or timing registers change.

The inferred phase span is rounded up. Runtime counter bounds use the observed
maximum plus the existing 32-tick margin, while copy admission still checks
time remaining against the measured frame period. Notification scheduling now
uses remaining nanoseconds with saturation rather than subtracting unsigned
phase values; a late phase cannot underflow. DMA completion, 32-tick active
margin, copy duration plus 25% and 500 us, and 55–65 Hz calibration limits
remain required. E17 rejects implausible phase spans; E18 rejects an idle
boundary outside the phase cycle.

The HUD now has six lines, adding `PHASE ... MAX ... IDLE ...` and
`TICK ... NS FRAME ... US`; the measured rate is retained on geometry failure.
ASan/UBSan tests cover phase spans below physical height, insufficient idle
margin, implausible spans and notification saturation, as well as the existing
DMA/budget/wrap/stall guards and HUD bounds. Native kernel build and independent
image verification passed. Hardware confirmation of r3 is pending.

## Active r3 feedback and r4 stalled-presentation recovery

The user reported r3 `SCAN ACTIVE E0`, 60,659 millihertz, copy budget 4,775 us,
phase span 2380, observed maximum 2376, idle boundary 1225, 6928 ns/tick,
16,485 us/frame, frames 433 and misses 0. Tearing disappeared, then the picture
and all HUD values froze. This establishes scan activation and a successful
visual result before the stall; it does not establish a kernel-wide hang.

The measured idle interval is about 7.998 ms. With the copy guard (25% plus
500 us), a copy estimate above roughly 5.999 ms cannot fit even at its beginning.
R3 retains an unbounded high-water copy estimate and displays normal HUD
updates only in admitted frames. A single preempted copy can therefore cause
permanent presentation starvation while leaving an old `ACTIVE`/zero-miss HUD
on screen. This is a reproduced software failure path, not a confirmed cause
of this particular device freeze; its next copy duration was not observable.

R4 remeasures after at least three misses, at most twice a second. The presenter
lazily allocates one additional 10,368,000-byte scratch buffer, snapshots the
unchanged front image and copies that identical image back. Pending composition
is preserved. This measurement introduces no new scene pixels outside the
diagnostic rectangle; the independent HUD can still update that rectangle.
The new estimate is the greater of the measurement and the last admitted copy
budget. There is no blind budget decay or unguarded new-frame presentation.
If allocation fails, the old estimate remains and diagnostics continue.

A dedicated diagnostic kernel thread updates `BEAT ... S... AGE ... MS` every
500 ms when no new frame has been copied for 250 ms. It takes no framebuffer,
swap or GPU locks and performs no display MMIO reads. It draws only the small
HUD rectangle; it is enabled only with the diagnostic HUD. A moving BEAT
confirms some kernel scheduling/output continues; a frozen BEAT alone cannot
distinguish a stopped controller from a wider kernel hang.

Stages: S0 waiting for a new swap; S1 GPU completion; S2 composition/helper
completion; S3 TE admission; S4 front copy; S5 recording/statistics/surface
release; S6 identical-front remeasurement. AGE is time since the last completed
new-frame copy, not since the last diagnostic update. Snapshot fields are
best-effort diagnostics. Normal idle screens can also have a growing AGE.

ASan/UBSan checks passed for preservation of front and pending composition,
allocation failure/reuse, an injected copy-time outlier and budget floor, plus
admission using the user's measured geometry and seven-line HUD bounds. Native
kernel build and independent image verification passed. R4 device validation
is pending; GPU helper and physical scan timing are unchanged from r3.

## Stable r4 feedback and r5 wake timing

The user reports no freezes on r4 but 20–25 misses. The supplied HUD photo
shows ACTIVE E0, 60,718 millihertz, COPY 5539 us, FRAMES 1306, MISSES 26,
PHASE/MAX 2378, IDLE 1280, TICK 6927 ns, FRAME 16469 us, BEAT 353 S2 AGE 0.
At these measurements, idle time is 7,602,440 ns and the guarded copy budget
is 7,423,750 ns: only about 178,690 ns remain for admitting the copy after the
idle boundary. The image does not measure actual scheduler wake lateness, but
this small interval makes the old 200-us wake-ahead vulnerable to oversleep.

R5 changes only admission wait scheduling: wake 1 ms before the boundary
instead of 200 us and poll every 10 us instead of 50 us near it. Long known
active intervals are still slept through; the final polling interval can use
more CPU. DMA completion, active margin, copy-budget guard, bounded wait,
copy recovery and physical panel settings are unchanged.

The actual scan-reader test now injects a 400-us oversleep using the photo's
geometry. The old wait policy misses that interval; the new policy admits in
the same refresh. A larger copy budget that cannot fit remains rejected.
ASan/UBSan tests, native kernel build and independent image verification
passed. Real-device improvement in miss counts remains to be measured with
similar gestures and load; the modeled lateness is not a measured root cause.

## R5 feedback and r6 FPS/recent-copy accounting

The user's r5 photo shows ACTIVE E0, RATE 60658, COPY 5678 us, FRAMES 4513,
MISSES 22, PHASE 2381, MAX 2377, IDLE 1277, TICK 6926 ns, FRAME 16485 us,
BEAT 640 S2 AGE 0. Misses were initially zero, then increased. This geometry
leaves approximately 7,640,498 ns idle, while the guarded estimate is
7,597,500 ns, leaving only about 42,998 ns to admit a frame. Earlier polling
alone does not solve a persistently increasing copy budget.

R6 removes the last-admitted budget floor, which could only ratchet upwards.
The estimate is now the maximum positive copy duration recorded in the last
second (128 timestamped samples). New normal copies can age out old spikes.
After repeated misses, three identical-front probe copies add fresh samples;
a recent spike remains protected until it expires. There is still no arbitrary
decay or reduced 25%/500-us guard. A sustained real slowdown continues to
increase the estimate. The existing scratch-buffer recovery and independent
liveness monitor remain enabled. This is a heuristic for recent costs, not a
bound on future scheduler latency; device validation remains necessary.

The user also requested FPS and refresh rate. The eighth HUD line now displays
`FPS ... HZ ...`. FPS counts new composed frames actually copied to the front
within the last second, excluding missed swaps, identical-front probes and
HUD-only updates. It can drop to zero on a static screen. HZ is the panel rate
measured during TE calibration, displayed with two decimal places; it is not
an FPS estimate or an indication of a mode switch. FPS and HUD snapshots are
best-effort diagnostics.

ASan/UBSan tests cover recent-peak retention/expiry, zero samples, recovery
without modifying front/pending composition, FPS window/idle expiry, decimal
point rendering and eight-line HUD bounds, plus the previous admission tests.
Native kernel build and independent image verification passed. R6 real-device
FPS, miss counts and stability remain to be measured.

## R6 feedback and r7 comparison profiles

The user reports panel rate about 60 Hz but 20–29 presented FPS on r6. The
counter was audited: only completed composed-frame front copies are counted,
with probes and HUD updates excluded. Neither panel Hz nor the current HUD
stage alone establishes a compositor bottleneck.

The supplied UMI boot profile uses `fb_threads=6 fb_filter=0 cpufreq=1` and does
not force `glwait=1`. Redwood notes document the same scaling/thread controls,
DDR bandwidth votes and optional reduced rendering scale. R7 reuses the exact
r6 kernel/helper and changes reviewed loader configuration only:

- `compositor_profile=umi-fast`: six scaling threads, nearest-neighbor scaling,
  and omission of forced `glwait=1`. It retains `ca_vbl=1` and the display's GPU
  fence/cache wait. This is not `glwait=0`: the GL bridge's fallback wait remains
  available when a display driver does not advertise GPU waiting. Nearest-
  neighbor output may have rougher text/diagonals; layout/resolution is unchanged.
- Optional `cpu_frequency_profile=stock-max`: add `cpufreq=1 ddr=0`. The captured
  munch DT reports `qcom,cpufreq-hw-epss`, three 0x1000-byte domains at
  0x18591000/0x18592000/0x18593000 and a 4-byte LUT stride, matching the existing
  driver's EPSS addresses/layout. The packer and independent verifier check all
  four properties. The driver selects the highest row of its hardware table;
  it does not install new frequency/voltage values. This can increase heat and
  power; UMI's separate thermal-cap service has not been ported here.

DDR voting stays disabled explicitly in the stock-max comparison: munch's
captured apps RSC is 0x18200000, whereas the shared driver's DDR defaults use
0x18220000. Copying Redwood DDR defaults or enabling bare `cpufreq=1` would
also activate that unadapted DDR path. `fbscale=2` is not included: it changes
rendering resolution and the upstream notes record layout/debug artifacts.

Both r7 images passed independent boot/manifest/APFS-helper/kernel verification.
Profile tests reject unsupported names or missing scan/cache prerequisites;
the captured EPSS layout was accepted and a modified LUT-stride DTB rejected.
No new kernel source build was needed. Hardware performance comparison is
pending; neither image claims 60 presented FPS.

## R7 feedback and r8 compact HUD

The user subsequently booted the ordinary r7 fast image and reported 30–48
presented FPS. The earlier pre-loader exception's cause remains unresolved;
no UEFI fix is claimed. The user then requested removal of every HUD item
except FPS, before moving on to Wi-Fi work.

R8 retains the working UMI-fast compositor profile and firmware CPU-frequency
selection. The HUD now draws only `FPS ...` in a 162x24-pixel rectangle, rather
than the former 702x192 rectangle. Hertz, scan state, counters and heartbeat
text are removed from the visible overlay. Internal TE guards, timestamped
copy recovery and the diagnostic worker remain; no physical panel or CPU
clock setting is changed. The smaller overlay also removes most per-frame
font work, but its actual FPS benefit has not been measured on the device.

ASan/UBSan checks verify compact horizontal bounds and that former HUD rows
are untouched, as well as admission and copy recovery. Native kernel build
and independent image verification passed; the verifier checks the FPS-only
marker and absence of the old heartbeat/HZ strings. R8 device testing is pending.

## Previous GPIO TE staging experiment

The passive GPIO66 probe measured 60.69, 60.68 and 60.68 Hz in three
500-ms windows. The captured panel DT includes 60/120-Hz modes, but this
experiment does not change panel, DSI, DPU or clock registers.

The app-switcher video shows horizontal discontinuities. The existing
framebuffer compositor writes horizontal stripes concurrently into the live
front buffer. This is a plausible contributor; the video alone does not
establish the complete cause.

`munch_te=1` enables a separate composition buffer on the exact munch
1080x2400/4320-byte-stride configuration. Workers finish before a single
serial front-buffer copy. GPIO66 is read without changing its mux or IRQ.
The initial measured TE period must be 15–18 ms. Presentation attempts a
14-ms offset derived from the stock 60-Hz DT transfer-time budget. This is
an experimental phase assumption, not a verified blanking interval or
atomic page flip. A missing signal, late frame, or excessive measured copy
time falls back to serial staging; repeated missing pulses disable polling.
It may reduce frame rate and does not guarantee tear-free output.

Build with `gpu/stage-kernel.py --te-staging`. The source manifest and image
configuration must both enable staging. Normal staging without that option
restores the baseline framebuffer implementation.

Validation: the native kernel build target passed with BUILD_DSYM=0;
`check-te-frame.c` passed with AddressSanitizer and UndefinedBehaviorSanitizer
for geometry, period, copy-window, lateness and overflow policy. The packed
image passed the independent image verifier. The subsequent device test felt
slower and did not resolve tearing; see the rollback below.

`munch-ios165-gpu-te-r1.img` retains the existing Mesa helper and iOS root,
requests the reviewed 587-MHz GPU selection, keeps software refresh at 60 Hz,
and disables the deliberate 600-second loader return. It does not install
or rewrite the iOS root. Keep the previous boot image for comparison.

## Device feedback and rollback

The TE-staging r1 image felt slower and did not visibly resolve tearing.
It is not the preferred presentation path. The touch4 r1 image restores
the original framebuffer source, including its existing frame pacing, and
changes only the native touch loop's sleep from 16 to 4 ms relative to the
session r1 profile. The GPIO SPI transaction timings and CRC/retry checks
are unchanged. Four milliseconds is the sleep interval, not a measured
250-Hz sample rate or an end-to-end latency claim. More polling can use
more CPU and power. Device latency and stability testing is pending.

## UMI command-mode scan adaptation: measurement first

The UMI `hk_display.c` command-mode path checks `te_initial == 2340`,
calibrates TE counter wraps, and checks the write-pointer counter before
copying. Munch's selected panel is 2400 lines tall. Its captured live DT
lists INTF offsets 0x6b000/0x6b800/0x6c000/0x6c800 at MDP base 0xae00000,
so the first DSI interface is 0xae6b800, as in UMI/Kona.

`munch-display-scan-r1.img` observes INTF1 TE configuration and three
200-ms windows of raw and write-counter ranges, wraps, busy/idle samples,
and the last observed busy phase. It does not boot iOS or modify DSI/DPU
registers. GCC display AHB at 0x10b00c and DISPCC AHB at 0xaf02080 may be
temporarily enabled, then restored on every ordinary return path. MDSS
power at 0xaf03000 must already be on; the probe does not power it up.
Clock/power guards precede INTF reads. This does not provide recovery
from a hardware bus hang or an unexpected reset.

The counter semantics and offsets were checked against Xiaomi's
[alioth-r-oss sde_hw_intf.c](https://github.com/MiCode/Xiaomi_Kernel_OpenSource/blob/alioth-r-oss/techpack/display/msm/sde/sde_hw_intf.c),
[gcc-kona.c](https://github.com/MiCode/Xiaomi_Kernel_OpenSource/blob/alioth-r-oss/drivers/clk/qcom/gcc-kona.c),
[dispcc-kona.c](https://github.com/MiCode/Xiaomi_Kernel_OpenSource/blob/alioth-r-oss/drivers/clk/qcom/dispcc-kona.c),
and the GDSC offset against Linux v6.12
[dispcc-sm8250.c](https://github.com/torvalds/linux/blob/v6.12/drivers/clk/qcom/dispcc-sm8250.c).
These references establish register layout, not a proven safe copy interval
on this handset. No UMI 2340-line calibration is assumed valid for munch.

`uefi/check-display-scan.py` executes the actual probe with mocked MMIO
and time. ASan/UBSan checks cover period measurement, power/clock guards,
clock timeout, restoration and an invalid timer. The new command-staging overlay calibrates again in XNU; this passive probe
remains useful for diagnosing rejected prerequisites. Device validation of
controller-based presentation is still pending.
