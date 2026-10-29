# munch QCA6390 bring-up

Status: QCA6390 PCIe0 enumeration **passed on the phone** with r3, identifying
`17cb:1101`, subsystem `17cb:0108`, Gen2 x1, bus mastering disabled. Endpoint
reset cleanup and return to the loader are visible. This does not establish
functional Wi-Fi. The next SID/SMMU read-only checkpoint is built below;
SMMU ownership and DMA memory are not approved. GPU/display remain r8.

Capture: `munch-wifi-info-20261027-132551.tar.gz`, SHA256
`30446d38ca939409b93b07d96717f559cc58d691dd8ef8edc9c23cbc533026fd`.
Android identifies PCIe0 endpoint `17cb:1101`, subsystem `17cb:0108`.
Its CNSS log selects `qca6390/bd_l11rgl.elf`, not UMI's `bd_j1gl.elf`.

The captured merged DT confirms QCA6390, WLAN_EN GPIO20 and PCIe0 at
0x1c00000, PHY version 1102 (decimal, 0x44e), PERST/CLKREQ/WAKE GPIO79/80/81.
These match the UMI transport. Power is device-specific:

| Supply | munch resource | Minimum |
| --- | --- | --- |
| WLAN AON and digital, shared | smpa6 | 950 mV |
| WLAN IO | smpa4 | 1800 mV |
| WLAN RFA1 | smpa5 | 1900 mV |
| WLAN RFA2 | smpc8 | 1350 mV |
| PCIe vreg-1p8 | ldoa9 | 1200 mV, confirmed in munch live DT |
| PCIe vreg-0p9 | ldoa5 | 880 mV, confirmed in munch live DT |

UMI's WLAN digital vote for **smpf2 must not be used**. munch also declares
`vdd-wlan-3antenna` on ldoa16, range 3024–3304 mV and config ending in zero.
The final cell is `need_unvote` in the downstream CNSS parser, not an enable
flag. Zero does not justify skipping this supply; see the audit below. PCIe also declares
`cx.lvl`; matching regulator names alone does not validate a power sequence.

Both DTs expose RSC regions 0x18200000, 0x18210000 and 0x18220000. Comparing
only the first region does not establish incompatibility with the UMI DRV2
client at 0x18220000. Runtime resource resolution and TCS geometry are now observed below; command
contents and the power sequence still require validation.

`inspect-profile.py` compares merged DTs and resolves supply phandles in their
own trees. `prepare-firmware.py` verifies all four selected stock files before
staging them under an output `fw/qca6390` directory. Firmware stays in ignored
build output. `stage-ath11k.py` copies the shared build templates and the UMI
LinuxKPI adaptation into a private build directory, records overlay hashes,
and checks available source archives. It does not install a kernel link.

Download prerequisites from a networked terminal:

```sh
cd /home/kdyrolzhas/ios/hackios-xnu-kernel-munch
bash devices/poco-f4/wifi/fetch-deps.sh
```

Before enabling DMA: validate munch's SMMU stream/context and reserved memory
against UMI's fixed firmware IOVAs. First hardware checkpoint should be a gated
GCC/TLMM read-only survey, then a bounded PCIe enumeration with bus mastering
disabled. Normal Wi-Fi initialization must remain disabled until these pass.

## First device checkpoint

`munch-ios165-wifi-survey-r1.img` uses the r8 GPU/display profile and helper,
with `munch_wifi_step=1 munch_wifi_return=1`. It returns before launching iOS.
The kernel prints the UMI-derived GCC/TLMM survey and returns its RAM log to
the loader. No Wi-Fi power, PCIe, PHY, SMMU or DMA writes are added.
The one-shot loader has USB commands disabled: `getvar result` and
`get_staged` cannot retrieve its log. The r2 image prints a compact Wi-Fi
summary on screen and waits 90 seconds before resetting. Photograph it.
The new board gate also prevents entering the Redwood WPSS initialization
on munch, including when someone passes `wifi=1` or an unsupported step.

From the bootloader:

```sh
fastboot boot /home/kdyrolzhas/ios/munch-ios165-wifi-survey-r2.img
```

Build output and reports are in `out/munch-wifi/survey-r1/`. The independent
image verifier checks the full kernel, payload, FD, DTB, helper and diagnostic
bootargs. `check-survey.py` runs the actual include with fake DT/bootargs/MMIO,
checking board selection, unsupported steps, no writes and mapping failures
under ASan/UBSan. This does not validate hardware accessibility.

ath11k output is in `out/munch-wifi/ath11k-r2/xnu-deps/build/ath11k/out/`:
524584 bytes of code/data from 18 driver files, zero synthesized ath11k
function stubs, 53 external kernel names. It uses private UMI transport
declarations and is **not linked into the survey kernel**. Its additional
transport functions still require the munch PCIe/MHI/SMMU adaptation.

## Survey result and power audit

The device photograph from the r2 survey shows all 13 GCC and six GPIO reads,
followed by return to the loader and restoration of touch state. It establishes
access to those mapped registers, not endpoint enumeration or Wi-Fi operation.
Selected raw observations:

| Register / pin | Value |
| --- | --- |
| GCC pcie0_reset | 0 |
| GCC pcie0_gdsc | 0x22001 |
| GCC pcie0_clock_votes | 0x410040 |
| GPIO20 WLAN_EN | cfg=0x1, io=0 |
| GPIO21 BT_EN | cfg=0x1, io=0 |
| GPIO79 PERST | cfg=0x1, io=0 |
| GPIO80 CLKREQ | cfg=0x3, io=0x1 |
| GPIO81 WAKE | cfg=0x1, io=0x1 |
| GPIO124 SW_CTRL | cfg=0x1, io=0 |

The live DT also confirms ldoa9's 1200000-uV and ldoa5's 880000-uV regulator
constraints and PCIe voltage-level properties. AON and DIG resolve to the same
smpa6 phandle. CNSS separately retains `qcom,cmd_db_name="smpf2"`; downstream uses this property for CPR command lookup, as detailed below.
It does not override the resolved DIG supply.

The reserved `qcom,cmd-db` is at 0x80860000, size 0x20000. Apps RSC uses DRV2
at 0x18220000 and TCS offset 0xd00. Both captured munch and UMI trees specify
`qcom,tcs-config = <2 2 0 3 1 3 3 1>`. With the RPMh binding constants
ACTIVE=2, SLEEP=0, WAKE=1 and CONTROL=3, the normal slot order is ACTIVE 0..1,
SLEEP 2..4, WAKE 5..7. The old touch prototype's labeling of TCS2..4 as active
must not be copied into Wi-Fi voting code. This checkpoint performs no votes.

The runtime resources are resolved. The exact munch regulator list/order,
CPR behavior and inherited enabled TCS commands still need checking before
a power-enable experiment.
No PCIe PHY, endpoint, SMMU or DMA access is added here.

## CMD DB / RSC checkpoint

`munch-ios165-wifi-power-survey-r1.img` selects `wifi_survey_step=2` in the native
config, producing `munch_wifi_step=2 munch_wifi_return=1`. It reads the reserved
CMD DB and DRV2 registers, reports resource addresses and eight TCS snapshots,
and returns before starting iOS. The previous step 1 remains available.

The CMD DB parser validates version/magic/flags, bank/entry/auxiliary ranges,
address classes and conflicting aliases. Identical aliases are accepted.
Missing required resources cause a failed survey; smpf2 is observational only.
RSC geometry is checked before TCS reads. No request, IRQ acknowledgement,
voltage, GPIO, reset, clock or PCIe register write is performed by this survey.
A bus access that never returns cannot be recovered by software bounds checks.

Packing and independent verification require the captured DT's CMD DB/RSC
layout. Host ASan/UBSan checks cover parser bounds, aliases, complete/missing
resource sets, unsupported geometry, map failures, boot gates and unchanged
mocked register memory. The kernel build and independent image verification
passed. Hardware resource values are recorded below.

From the repository root:

```sh
fastboot boot out/munch-wifi/power-survey-r1/munch-ios165-wifi-power-survey-r1.img
```

Photograph the `CMD DB`, resource, `RSC DRV2` and `TCS0..7` lines during the
90-second pause. The system partition does not need reinstalling. The known r8
profile, GL helper, captured FD and DTB are retained; the Wi-Fi survey kernel
and gated step are changed. Native manifests/build logs are alongside the image.


## CMD DB / RSC device result (2026-10-27)

The supplied photograph shows CMD DB version `0x1`, magic `0xc0330db`, flags
`0`. Machine-readable transcription: [results/power-survey-r1.json](results/power-survey-r1.json).
Photo SHA256: `4fcc17e6793a3d96683e520a2a3ad802e268deeedbcc0a663ee86ce8c28bda7f`.
The image hash is the operator-reported r1 identity; the photograph cannot
independently authenticate the booted image. The final survey verdict line is
not visible, but every required resource reports `resolved`:

| Resource | Runtime address | Result |
| --- | --- | --- |
| smpa6 | 0x40200 | resolved |
| smpa4 | 0x40000 | resolved |
| smpa5 | 0x40100 | resolved |
| smpc8 | 0x40400 | resolved |
| ldoa9 | 0x41b00 | resolved |
| ldoa5 | 0x41800 | resolved |
| ldoa16 | 0x40b00 | resolved |
| cx.lvl | 0x30000 | resolved |
| smpf2 | 0 | missing/invalid (observational, not required) |

RSC DRV2: `id=0x20000 config=0x800c8104 tcs=8 cmds=16`, IRQ status `0`.
The normal slot types below come from captured DT order, not from the photo:

| TCS | Type | ctl | idle | en | wait |
| --- | --- | --- | --- | --- | --- |
| 0 | ACTIVE | 0x1010000 | 0x1 | 0x3 | 0 |
| 1 | ACTIVE | 0x10000 | 0x1 | 0 | 0 |
| 2 | SLEEP | 0 | 0x1 | 0 | 0 |
| 3 | SLEEP | 0 | 0x1 | 0 | 0 |
| 4 | SLEEP | 0 | 0x1 | 0 | 0 |
| 5 | WAKE | 0 | 0x1 | 0 | 0 |
| 6 | WAKE | 0 | 0x1 | 0 | 0 |
| 7 | WAKE | 0 | 0x1 | 0 | 0 |

TCS0 has two enabled commands and control trigger/AMC bits set despite idle=1.
Do not clear or reuse it. TCS1 is a candidate empty ACTIVE slot, with AMC_EN
already set; a later writer must recheck live state and pending IRQs. This
snapshot does not establish ownership. The two TCS0 command addresses/data
were not printed, so conflicts with proposed regulator votes remain unknown.
The loader's restoration messages and 90-second pause are visible. This
confirms read access and return, not physical rail voltage, PCIe or Wi-Fi.

`check-survey.py` now replays these header/resource/RSC values in synthetic
MMIO under ASan/UBSan, checks acceptance without smpf2, rejects loss of each
required resource, and verifies no memory writes. DB bank metadata/order in
that replay are fabricated; this is not a replay of captured CMD DB bytes.

## Downstream audit and next power/enumeration boundary

Primary downstream evidence:

- [CNSS CPR property change](https://android.googlesource.com/kernel/msm/+/fe5fde1f683974d29a2af2f3db4948a9065d4099%5E2..fe5fde1f683974d29a2af2f3db4948a9065d4099/):
  `cnss_get_cpr_info()` reads `qcom,cmd_db_name` and resolves a CPR PMIC
  address. It is not the regulator supply selector. A missing address is an
  error on that path. Do not substitute a guessed smpf2 address or rewrite
  an inherited CPR command to make this checkpoint pass.
- [CNSS regulator parser](https://android.googlesource.com/kernel/msm/+/f733fefafbab37343e298b1119e34145063a0d73/drivers/net/wireless/cnss2/power.c):
  the five cells are min_uV, max_uV, load_uA, delay_us, need_unvote.
- [CNSS regulator lifecycle](https://android.googlesource.com/kernel/msm.git/+/a1e88665ac803e356dca10c57c9eed8098f0591e/drivers/net/wireless/cnss2/power.c):
  `cnss_vreg_on()` enables the acquired list; `need_unvote` gates the separate
  unvote path. Thus the zero fifth cell for 3antenna is not disable/optional.
  With a present supply and a matching list entry, it must not be omitted on
  this basis. Exact munch list membership remains unverified.

These are Qualcomm downstream revisions, not proof of the exact Android
binary's source revision. Xiaomi lineage-20 `main.h` also has `need_unvote`,
and `main.c` calls unvote after firmware-ready; the matching `power.c` could
not be retrieved in this session (shell DNS failed; web fetch cache miss).
Full munch-specific sequencing is therefore not yet validated.

Preparation for the next bounded power + enumeration implementation:

1. Resolve the exact munch regulator list/order, RF clock/pinctrl timing,
   PCIe PHY sequence and CPR failure policy from retrievable matching sources.
   Include ldoa16 if the matching acquired list includes 3antenna. AON and DIG
   share smpa6; aggregate compatible constraints instead of independent votes.
2. Before any write, revalidate CMD DB and DRV2, inspect enabled commands in
   both ACTIVE slots (including TCS0's two commands), and reject resource
   conflicts. Select only an empty idle ACTIVE slot; never borrow SLEEP/WAKE.
   Use bounded polling/readback; quarantine after timeout, without retry/reset
   of inherited slots. Preserve all unrelated IRQ bits and rail consumers.
3. Keep endpoint reset asserted during rail/clock/PHY preparation. Log each
   bounded step and stop on the first failed readback. Cleanup must track
   acquired state; do not blindly disable shared regulators on loader return.
4. After link-up, inspect RC and endpoint PCI COMMAND with bus mastering
   cleared and verified. Use width-correct config writes (avoid writing W1C
   STATUS bits). Enumerate identity/class/link only; expect `17cb:1101` and
   subsystem `17cb:0108`. Do not start MHI, load firmware, program MSI,
   enable bus mastering, or touch SMMU/DMA mappings.
5. Treat enumeration success separately from Wi-Fi functionality. DMA remains
   gated on munch stream IDs/context and reserved-memory validation; no fixed
   UMI IOVA is approved by this survey.

The step-3 image below implements an explicit bounded diagnostic based on
munch's DT constraints. It reads enabled ACTIVE commands itself and refuses
conflicting resources before any write. This is not a claim that the exact
stock binary's regulator acquisition order has been reproduced. Existing
survey images and GPU r8 artifacts remain intact.


## Step 3: power and config-only PCIe enumeration, r1

Prepared image: `out/munch-wifi/pcie-enum-r1/munch-ios165-wifi-pcie-enum-r1.img`.
Size: **11673600 bytes**. SHA256:
`c72bb1c6a3e3754924f4e2b1b2d91e9f4a9746e23ebab8c39f2e4e8407109aa0`.
The r1 device run stopped at the inherited CX guard before writes; see the
r2 update below. Normal Wi-Fi remains disabled.

```sh
fastboot boot out/munch-wifi/pcie-enum-r1/munch-ios165-wifi-pcie-enum-r1.img
```

Native config is `native-wifi-pcie-enum.json`, with mode
`power-enumeration-return` and `munch_wifi_step=3 munch_wifi_return=1`.
No system-partition installation is needed. The FD, boot DTB, GL helper and
actual GPU/display/touch sources match the previous r8-based checkpoint.
The boot DTB (`da0ef458...`) comes from the recovered working image; the newer
Android Wi-Fi capture DTB (`3b9acb89...`) is a different blob. The boot image
retains the former. `check-power-profile.py` independently checks its resolved
supplies, complete PHY table and pinctrl against the diagnostic constants.
Both DTs have the same inspected Wi-Fi supply constraints and PHY sequence.

The image performs these bounded operations:

1. Map only the CMD DB/RSC, GCC/TLMM, PCIe controller/PHY, DBI/ATU and one
   endpoint config page. Validate resources, ARC levels and RSC geometry.
   Read and print enabled ACTIVE command addresses/data, including TCS0.
   Refuse conflicts, an occupied/pending candidate, malformed resources,
   unsupported geometry, or WLAN/BT/PERST already high. No writes on this path.
2. Assert PERST, then send ACTIVE-only votes through an empty idle TCS0/1.
   Request CX SVS using the index resolved from CMD DB LE16 levels (never
   write abstract level 256 directly). Vote smpa4=1800 mV, shared
   smpa6=950 mV once, smpa5=1900 mV, smpc8=1350 mV, ldoa16=3024 mV,
   ldoa9=1200 mV and ldoa5=880 mV. Retain inherited modes. No smpf2 fallback
   or CPR command override. Timeout quarantines the owned slot, without retry.
3. Apply the matched pinctrl, clocks and full munch PHY table; release PERST
   after the DT delay and attempt Gen2 x1 link training. Use CFG0 only,
   read PCI ID, subsystem and class/revision. Expect `0x110117cb` and
   `0x010817cb`. Root and endpoint bus mastering remain off. PCI control
   accesses use 16-bit stores to preserve W1C status fields.
4. Assert PERST and drive WLAN_EN low before returning to the loader, on
   success or failure. Check output-latch readback; completed shared ACTIVE
   rail votes remain until hardware reset. Do not disable shared supplies.
   The loader shows the Wi-Fi log and waits 90 seconds before reboot.

No SID table, BAR assignment, memory ATU, MSI, SMMU, DMA, MHI or firmware
initialization is performed. Resource acknowledgement does not measure rail
voltage. Successful enumeration is only the next transport checkpoint.

Photograph the final `PCIe` lines and `enumeration ...` / `survey ...` result.
On failure, retain the inherited-command, preflight or RPMh timeout lines.
A failure can stop before any PCIe access; do not interpret that as endpoint
absence. If an MMIO bus read never returns, bounded polling cannot recover it.

Checks completed:

- `check-survey.py`: ASan/UBSan board/boot gates and read-only parser cases.
- `check-enumeration.py`: ASan/UBSan, 20 success/failure scenarios with the
  actual step-3 code, simulated completion, no writes on rejected preflight,
  timeout quarantine, unrelated IRQ/TCS preservation, no DMA/SID/BAR writes,
  W1C status preservation and reset cleanup.
- `check-power-profile.py`: exact boot DT, voltage constraints/shared smpa6,
  CX level, all PHY tuples and pinctrl values.
- Native XNU build (`CCACHE_DISABLE=1 bash xnu-build.sh -j9 BUILD_DSYM=0 build`),
  independent `touch/verify-image.py`, and `git diff --check`.
- Actual r8 GPU/display/touch source hashes checked against its manifest;
  FD/DTB/helper match power-survey-r1. Isolated ath11k stays unlinked.

Reports, native manifest and logs are in `out/munch-wifi/pcie-enum-r1/`,
including `validation.json` and `power-profile-validation.json`.
The stock-binary-specific regulator order remains unproven. The diagnostic
uses the downstream IO-before-AON/RFA ordering for the common rails, the
explicit munch 3antenna constraint, and the matched local PCIe sequence.
The downstream probe also calls `cnss_get_cpr_info()` without testing its
return before resource/power setup; a missing CPR lookup need not prevent
this config-only diagnostic. This does not validate CPR-assisted firmware
operation. [Downstream probe](https://android.googlesource.com/kernel/msm/+/65064d0a54557a769996560d5318d0bc21470540/drivers/net/wireless/cnss2/main.c),
[common regulator order](https://android.googlesource.com/kernel/msm.git/+/2e92a0be4fafcd694d34c8c1090155dd286a0e35/drivers/net/wireless/cnss2/power.c).


## Step-3 r1 hardware result and r2 continuation

The next photograph shows:

```text
MUNCH Wi-Fi: step 3 power/PCIe; no DMA, SID, BAR or MHI
MUNCH Wi-Fi: inherited ACTIVE TCS0 cmd0 addr=0x30000 data=0x2
MUNCH Wi-Fi: power preflight rejected; no writes
```

The loader returned and restored touch state. Transcription and photo hash:
[results/pcie-enum-r1.json](results/pcie-enum-r1.json). The address resolves to
`cx.lvl`; data 2 is an ARC table index, not a measured voltage or an abstract
level. Its level, message ID and command status are not visible in this photo.
CMD1 is also unknown because r1 returned immediately at CMD0. No power votes
or PCIe enumeration occurred. This is the intended conservative guard firing,
not evidence that the endpoint is absent.

R2 permits retaining an inherited CX request only when the runtime CMD DB
maps its index to at least SVS (256), the slot is idle without a pending IRQ,
the message is an acknowledged write, and its status has ISSUED and COMPLETE.
It never changes that slot, payload or IRQ. Before each regulator vote it
rechecks that the retained request is still present with the same address,
data and message, and remains valid. Insufficient, unknown or changing state
stops the diagnostic. Wi-Fi rail conflicts still reject preflight. When no
inherited CX request exists, the original bounded ARC vote path remains.

Preflight now logs both enabled ACTIVE commands before deciding, with message
and status on separate short lines. RPMh diagnostics go directly to the loader
RAM log rather than the generic radio logger. The no-DMA, no-SID/BAR/MHI
boundary and reset cleanup are unchanged.

Load the new image from the repository root:

```sh
fastboot boot out/munch-wifi/pcie-enum-r2/munch-ios165-wifi-pcie-enum-r2.img
```

SHA256: `f34653b92ec630b1303782219f8e7f8faf1798ed7a5e0892ab7be3cdd4a69c37`.
R2 returned before writes on the device; its result and r3 continuation are below. No iosroot installation is needed.
Photograph `CX SVS`, both inherited commands and their `msg/status` lines,
`retained CX`, and the final PCIe/result or failure lines during the 90-second
pause. A new preflight rejection still means no power or PCIe writes.

Validation: 30 ASan/UBSan step-3 scenarios, including retained index 2 mapped
to SVS, higher/lower levels, invalid index, pending IRQ, incomplete/read
commands, a second-command rail conflict, and mutation after preflight.
The original parser/gate tests, native XNU build, power-profile validation
and independent image verification passed. Reports are in
`out/munch-wifi/pcie-enum-r2/`. GPU/display/touch remain at r8; FD, DTB and
GL helper match r1, and both previous survey and enumeration images remain.


## Step-3 r2 device result and r3 acknowledged CX refresh

The next photograph confirms:

```text
CX SVS index=5 levels=9
inherited ACTIVE TCS0 cmd0 addr=0x30000 data=0x2
TCS0 cmd0 msg=0x10008 status=0x101
retained CX index=2 level=64 unproven/insufficient; stop
inherited ACTIVE TCS0 cmd1 addr=0x30010 data=0x2
TCS0 cmd1 msg=0x10008 status=0x101
power preflight rejected; no writes
```

The loader returned. Transcription: [results/pcie-enum-r2.json](results/pcie-enum-r2.json).
Only level count 9, CX index 2 -> level 64, and SVS index 5 -> level 256
are established; the other table entries have not been photographed. The
second ARC resource name has not been independently resolved. Neither power
nor PCIe enumeration was exercised by this run.

The primary RSC source defines WRITE bit 16 and RESP_REQ bit 8, making
`0x10008` a write without a response request. Its completion handler requires
ISSUED but requires COMPL only for requests which asked to wait. Thus
`status=0x101` is not evidence of an acknowledged resource-ready transition.
Active request conflict handling tracks requests still in flight, rather than
interpreting every retained payload as an ongoing transfer. Sources:
[Qualcomm downstream RSC completion handling](https://android.googlesource.com/kernel/msm/+/b6410c3f75259c0ca2df45a01838d2e46272723e/drivers/soc/qcom/rpmh-rsc.c),
[primary RSC request-conflict handling](https://android.googlesource.com/kernel/common/+/442489c219235991de86d0277b5d859ede6d8792/drivers/soc/qcom/rpmh-rsc.c).
The local Linux 7.0.14 `drivers/soc/qcom/rpmh-rsc.c` was also inspected;
SHA256 `2b684127b60e9ce437b844a75145371f3898509a50f2543f2000c4d2a70fcb7d`.

R3 adds a narrow diagnostic policy for the observed idle UEFI batch. TCS0 must
have control `0x1010000`, idle 1, enable 3, wait 0 and no pending IRQ. Both
commands must retain exact message/status `0x10008`/`0x101`, be aligned ARC
addresses, with CMD0 targeting resolved CX and a valid table index, and CMD1
targeting a distinct ARC address. Snapshot all these fields. This recognizes
an issued, idle posted batch at boot handoff; it does not claim the resource
is ready or that Linux's software ownership bitmap exists in XNU.

Only in that case, submit a new acknowledged CX request in the other empty
ACTIVE slot, using `max(inherited index, SVS index)` from the monotonic runtime
table. For the photographed state this is index 5. TCS0 is never reclaimed,
retriggered, acknowledged, masked, cleared or rewritten, and CMD1's resource
is not voted. Recheck the frozen TCS0 snapshot before the request, immediately
before triggering, and before each later rail vote. A busy slot, pending IRQ,
unsupported header/status, invalid index, changed batch or Wi-Fi rail conflict
still stops the diagnostic. Wait for fresh completion of the new response-
requested transfer before any regulator vote. Timeout quarantines the owned
slot without retry. Higher inherited CX indices are not lowered.

This policy is a bounded boot-handoff experiment, not reproduction of the
stock driver's full concurrency/ownership model. The existing strict path for
borrowing an adequate acknowledged CX request is unchanged.

R3 image, hardware enumeration now passed (result below):

```sh
fastboot boot out/munch-wifi/pcie-enum-r3/munch-ios165-wifi-pcie-enum-r3.img
```

SHA256: `f9981143feacf06195ca339d4be52db01548c2137a6fa17931d8273caf257474`.
No iosroot reinstall. Retain a photograph of `refresh CX`, the RPMh completion
or timeout lines, and the final `PCIe`/enumeration result. The screen pause
remains 90 seconds. DMA, SID/BAR/MSI/SMMU/MHI/firmware initialization remain
disabled. Shared completed votes are held until reset, and endpoint reset
cleanup is retained. GPU/display/touch remain r8; earlier images remain intact.

Checks: 42 ASan/UBSan scenarios, including the observed posted batch with
synthetic auxiliary entries constrained by the photo, an acknowledged CX
refresh before rails, higher-level preservation, timeout with no later votes,
busy/pending/unissued/read/invalid batches, a second-command rail conflict,
and batch mutation before power or immediately before trigger. Tests prohibit
writes to TCS0 and verify unrelated IRQ/TCS and W1C status preservation.
The native build, profile check and independent image verification passed.
Reports and manifests are in `out/munch-wifi/pcie-enum-r3/`.


## Step-3 r3 device result: PCIe enumeration passed

The supplied photograph shows:

```text
vote ldoa5 minimum=880 mV
RPMh TCS1 done=1 ...
PCIe link status=0xb0120008
PCIe endpoint PCI ID=0x110117cb
PCIe endpoint command (DMA disabled)=0x400
PCIe root command (DMA disabled)=0x400
PCIe endpoint subsystem=0x10817cb
PCIe endpoint class/revision=0x2800001
enumeration passed; PERST asserted, WLAN_EN low
```

The endpoint ID is Qualcomm QCA6390 `17cb:1101`, subsystem `17cb:0108`.
The link status reports Gen2 x1. Both PCI COMMAND values have I/O, memory
decode and bus mastering clear, with INTx disabled. Loader return and touch
restoration are visible. Transcription and photo hash:
[results/pcie-enum-r3.json](results/pcie-enum-r3.json).

The upper log is cropped/scrolled; earlier CX and individual rail transactions
are not all visible. The final ldoa5 completion and successful config-space
access confirm that the bounded sequence reached enumeration. This is not a
voltage measurement. The photographed COMMAND values and diagnostic code
establish the no-bus-mastering checkpoint, not Wi-Fi firmware operation.
The r3 image is retained with SHA256
`f9981143feacf06195ca339d4be52db01548c2137a6fa17931d8273caf257474`.

## Next checkpoint: step 4 read-only SID/SMMU survey

`inspect-dma-profile.py` audited both the working boot DT and the later Android
capture. Both map RC RID 0 to SID `0x1c00` and endpoint RID `0x100` (01:00.0)
to SID `0x1c01` at `/soc/apps-smmu@15000000`, compatible `qcom,qsmmu-v500`.
The SMMU declares `qcom,skip-init` and two IOMMU cells. These are static DT
facts; a usable runtime route/context is not established by them.

Reports: `out/munch-wifi/dma-audit/boot-dt.json`, `android-dt.json` and
`comparison.json`. CNSS has no `memory-region` property in either capture.
Its `smmu_iova_ipa` register resource is `0xb0000000/0x10000`, and
`qcom,wlan-ramdump-dynamic` is `0x420000`. These properties do not approve
physical allocations or UMI's firmware IOVAs. The reports inventory 28 fixed
reserved-memory entries per tree; no region is adopted for DMA by this audit.

Load the next image from the repository root:

```sh
fastboot boot out/munch-wifi/smmu-survey-r1/munch-ios165-wifi-smmu-survey-r1.img
```

SHA256: `557a228136b91727d53138b1b9884524bdb7f00e0ee35f6e2187524f653b5843`.
R1 completed its reads on the phone; see the recorded result below. It uses
`munch_wifi_step=4 munch_wifi_return=1`.
No iosroot reinstall is required. Prior images remain intact.

Step 4 repeats the r3 power/config-only enumeration, then reads the inherited
PARF BDF-to-SID config and bounded CRC8 collision chain for RID `0x100`.
It reports bypass state, raw entries and any observed target SID. It never
initializes the table or clears bypass. Cycles and invalid reads fail safely.
The new SMMU mapping covers only `0x15000000/0x2000`: SCR0 and IDR0..2,
then standard SMR/S2CR entries after geometry checks. Extended SID mode and
unsupported layouts are rejected without scanning. Report matches for root
and endpoint, invalid SMRs and context indices referenced by valid translating
S2CRs. Invalid/unreferenced entries are observations, not ownership grants.
No context-bank registers or page tables are touched.

Root and endpoint PCI COMMAND guards remain clear before and after the read
survey. No SID, SMMU, context, fault-ack, BAR, MSI, MHI or DMA write is added.
Endpoint reset cleanup runs even if the read survey fails. `enumeration
passed` and `SID/SMMU survey passed/failed` are separate results. A survey
pass means supported reads completed; **DMA NOT APPROVED** remains explicit,
even if the inherited SID route is absent or disagrees with the DT.

Photograph the final `SID cfg`, table/route, `SMMU SCR0/IDR`, matching
`SMR/S2CR`, counts and final result lines. The pause remains 90 seconds.
A mapped bus read which never returns remains outside software timeout recovery.

Primary register references:
[ARM SMMU register definitions](https://android.googlesource.com/kernel/common/+/b892a5885fce5ad44c73e4c88c2427b0540045b0/drivers/iommu/arm-smmu.h),
[Qualcomm BDF-to-SID format and CRC chain](https://gbmc.googlesource.com/linux/+/88fac17500f4ea49c7bac136cf1b27e7b9980075/drivers/pci/controller/dwc/pcie-qcom.c).
The UMI reference's survey was inspected but its DMA context, SID writes and
fixed firmware IOVAs are not imported.

Checks: 54 ASan/UBSan diagnostic scenarios, including SMMU map failure,
unsupported IDs/extended SID mode, masked owners, invalid S2CR/context index,
absent/colliding/cyclic/mismatching SID routes, unchanged SMMU/SID memory,
and no SID/SMMU writes. Earlier r3 power/timeout/race checks remain covered.
Native XNU build, power-profile audit and independent image verification passed.
Actual GPU/display/touch/early-return source hashes match r8; FD, DTB and GL
helper match r3. Isolated ath11k remains unlinked. Reports and manifests are
in `out/munch-wifi/smmu-survey-r1/`.


## Step-4 r1 device result and bounded context-bank reads in r2

The photograph confirms PCIe enumeration again with both PCI COMMAND values
`0x400`. The inherited SID configuration is `0`, so bypass bit 0 is clear.
CRC chain for endpoint RID `0x100`:

| Index | Raw table entry |
| --- | --- |
| 21 | 0x10000004 |
| 4 | 0xfff7ec7f |
| 127 | 0x28014040 |
| 64 | 0xee9ffffa |
| 250 | 0xbfe3fffd |
| 253 | 0x83190000 |

The chain terminates with `found=0 cycle=0`. No entry along this path targets
RID `0x100`; this is not a usable endpoint route. Its contents alone do not
establish the cause or prove memory corruption. The standard SM8250 table
and config offsets are confirmed in the
[primary Qualcomm PCIe driver](https://github.com/torvalds/linux/blob/master/drivers/pci/controller/dwc/pcie-qcom.c):
`0x2000` and `0x2c00`. Initializing a new table before use is separate work;
this survey neither initialized it nor enabled DMA.

SMMU values visible in the photograph:

| Register / count | Value |
| --- | --- |
| SCR0 | 0x250406 |
| IDR0 | 0x4c017e57 |
| IDR1 | 0x6000004a |
| IDR2 | 0x5111 |
| Mapping groups / invalid SMRs | 87 / 82 |
| Context banks / referenced by translating valid S2CRs | 74 / 4 |
| Root / endpoint SMR matches | 0 / 0 |

SCR0 has CLIENTPD clear and USFCFG set: the global block is enabled and
unmatched streams are configured to fault. There are five valid SMRs, but
r1 printed only root/endpoint matches, hence their identities and referenced
context indices are still unknown. The other 70 context indices are merely
unreferenced in this scan, not proven free. No SMMU write permission or context
ownership has been established. The loader returned after `reads finished;
DMA NOT APPROVED`. Transcription/photo hash:
[results/smmu-survey-r1.json](results/smmu-survey-r1.json).

R2 extends step 4 with read-only ownership observations. It prints every valid
SMR/S2CR, then derives context geometry from IDR1 and checks it against the
captured 1 MiB aperture. The observed IDR1 implies 4 KiB pages, a 128-page
(0x80000) global area, context base `0x15080000`, and 74 context banks.
See the [primary ARM SMMU geometry calculation](https://android.googlesource.com/kernel/common/+/95e6ba5133163f8241c9ea2439369cec0452fec6/drivers/iommu/arm-smmu.c).
Unsupported geometry is rejected before context reads.

Read at most the first four stage-1-capable context indices not referenced
by valid translating S2CRs. For each, report its derived physical address,
SCTLR, FSR, CBAR, CBA2R, TTBR0, TCR and TCR2. Map/unmap one 4 KiB bank at a
time. An enabled context or pending fault is only reported, never disabled or
acknowledged; no bank is claimed. Both active and disabled banks can belong
to other consumers, including secure firmware. These reads precede any future
bounded context install/restore test. UMI's fixed context index, translation
settings and IOVAs are not adopted.

Load r2 from the repository root:

```sh
fastboot boot out/munch-wifi/smmu-survey-r2/munch-ios165-wifi-smmu-survey-r2.img
```

SHA256: `a6025d1686cb10f8adf3f1f500ffdb0caa4053296291ddbfe1ca350bee067351`.
R2 returned before SMMU/CB reads due to a SID cycle; see the r3 correction below. Photograph valid `SMR...` and `CB...`
lines plus the final survey result during the 90-second pause. No iosroot
installation is needed. Earlier successful PCIe and survey images are retained.
GPU/display/touch remain r8; FD/DTB/helper match the previous checkpoint.

Checks: 62 ASan/UBSan scenarios, including observed ID registers/SID chain
replayed with explicitly synthetic owner entries constrained by the photo's
counts, context geometry/map failures, enabled unreferenced banks, pending
faults left intact and unchanged SMMU/SID memory. The native XNU build,
power-profile check and independent image verifier passed. Reports are in
`out/munch-wifi/smmu-survey-r2/`. DMA, SID writes, SMMU writes, BAR, MSI,
MHI and firmware initialization remain disabled.


## Step-4 r2 SID-cycle result and r3 independent SMMU continuation

The latest photograph again shows endpoint `17cb:1101` / subsystem `17cb:0108`
and both COMMAND values `0x400`. Link status is `0x38110008`, Gen1 x1 in this
run (earlier successful r3 enumeration was Gen2 x1). Enumeration still passed.
The SID table changed between boots; its first eight visible chain entries
are recorded in [results/smmu-survey-r2.json](results/smmu-survey-r2.json).
The walk reports `found=0 cycle=1`. The full cycle is not visible: the screen
printer showed only the first eight entries, ending at index 254 with NEXT=255.
Do not infer the unseen entry at 255 from this photograph.

R2 returned immediately on the SID cycle, so there are **no new SMMU, valid
SMR or context-bank observations** from this run. That early return was a
probe dependency error: with root/endpoint bus mastering and decode disabled,
a malformed inherited SID route does not prevent independent CPU reads of
SMMU registers. The state remains unsuitable for DMA, but it should not hide
the ownership data needed for the next bounded test.

R3 retains the bounded cycle detector and explicitly logs the cyclic route,
then continues the existing read-only SMMU/CB survey. It repeats the route
`found/cycle` flags beside the final `DMA NOT APPROVED` message. A survey pass
means supported reads completed, never that a cyclic SID route is usable.
Invalid MMIO reads, unsupported SMMU geometry and failed context mappings
still abort their respective paths. No SID/SMMU write, context claim, fault
acknowledgement or DMA enable is added. Power sequencing and PCIe enumeration
are unchanged; endpoint reset cleanup remains active.

Load r3 from the repository root:

```sh
fastboot boot out/munch-wifi/smmu-survey-r3/munch-ios165-wifi-smmu-survey-r3.img
```

SHA256: `70e598ede174625bff05099968d21dc446dfaf699d285e5fcb177cda039f2908`.
Hardware execution is pending. Photograph `SMMU`, valid `SMR...`, `CB...`
and the final result lines. The pause remains 90 seconds. No iosroot reinstall.
GPU/display/touch remain r8; FD/DTB/helper and earlier images are preserved.

Validation: 64 ASan/UBSan scenarios, including a SID cycle followed by SMMU/CB
reads, a cycle followed by a failed SMMU mapping, and the visible photographed
chain with a clearly synthetic unseen tail to exercise cycle handling. Tests
continue to verify unchanged SID/SMMU/context/fault memory and no DMA writes.
The native build, independent image verifier and `git diff --check` passed.
Reports are in `out/munch-wifi/smmu-survey-r3/`; the unchanged power-profile
validation is retained from `smmu-survey-r2/power-profile-validation.json`.


## Step-4 r3 video stills and reclaimable mapping fix in r4

Four video stills show the power sequence, repeated PCIe enumeration, SID
`found=0 cycle=0`, and the standard SMMU header. All five valid SMRs were
printed. Their visible S2CR context indices are 0, 1, 2, 2 and 4. The context
geometry line is `CB page=0x1000 base=0x15080000 S2-only=0`; there are no CB
register-value lines. The first unreferenced index derived from this scan is
3, whose register address is `0x15083000`. This is not an ownership grant.
Transcription: [results/smmu-survey-r3.json](results/smmu-survey-r3.json).
The displayed panic/backtrace has not been precisely symbolicated. Loader
touch restoration is visible, but Wi-Fi reset cleanup after the CB survey is
not confirmed by these stills.

Inspection of this XNU's `osfmk/arm/io_map.c` and
`osfmk/arm64/machine_routines.c` found a real mapping-lifetime bug in our
probe: `ml_io_map()` calls `io_map(..., unmappable=false)`, which uses permanent
`io_submap` allocations after VM initialization. `ml_io_unmap()` removes the
mapping and frees it through `kernel_map`. The supported reclaimable path is
`ml_io_map_unmappable(..., VM_WIMG_IO)`, which requests an unmappable
`kernel_map` allocation. Pairing the old APIs could panic during cleanup.
Furthermore, freeing a 4 KiB context-bank pointer without preserving the
full host VM-page span is invalid when the VM page size is larger.

The hardware translation granule for this BCM2837 configuration is 4 KiB;
XNU's advertised VM page size is selected at boot in `arm_init.c` according
to the supplied memory size. The earlier commentary's unconditional 16 KiB
assumption was too strong. R4 uses runtime `PAGE_SIZE`, prints `VM-page=...`,
and is tested at both 4 and 16 KiB. Its wrapper aligns the physical start,
rounds the span including the bank offset, calls `ml_io_map_unmappable()`
with I/O attributes, and frees exactly that aligned span. All munch Wi-Fi
survey mappings use this pair; no global VM or GPU/display code is changed.

This source bug is a plausible explanation for the missing post-cleanup
lines, not a confirmed symbolication of the photographed panic. R4 adds
staged diagnostics before GR1 reads and bank mapping, and prints raw CB
register values **before unmapping and before all-ones rejection**. Mapping
failure and all-ones values now have explicit messages. Unsupported state
continues to stop the experiment; no context is claimed, configured or
acknowledged. SID/SMMU/DMA writes remain disabled.

Load r4 from the repository root:

```sh
fastboot boot out/munch-wifi/smmu-survey-r4/munch-ios165-wifi-smmu-survey-r4.img
```

SHA256: `861efe7f1c5710beb0205e7c3a245ea5916ff489a5d376b7fadd96641fd17a0b`.
Hardware execution is pending. Photograph `VM-page`, `CB3 inspect/map`, raw
`CB...` register lines, any error and the final result. The 90-second loader
pause and r8 GPU/display/helper remain. No iosroot reinstall is needed.

Checks: 65 ASan/UBSan scenarios at each of 4/16 KiB VM page sizes (130 runs),
with aligned mapping/free assertions and the observed context-reference set
forcing CB3 at an offset within a larger page. Map failure, pending faults,
all-ones reads, unchanged SID/SMMU/context memory, power timeouts and races
remain covered. Read-only parser/gate checks, native XNU build, independent
image verification and `git diff --check` passed. Old r3 and successful PCIe
images remain intact. Reports: `out/munch-wifi/smmu-survey-r4/`.


## Compact full-screen display for r4 diagnostics

The user could not see the CB lines among power logs and leftover framebuffer
text. `wifi_screen_summary=true` now selects a clean full-console summary in
the one-shot loader. It queries the existing UEFI text mode and clears the
console before rendering. It does not change GOP mode, kernel display code,
font configuration, hardware sequencing or the diagnostic kernel.

The summary strips `MUNCH Wi-Fi:` and omits verbose power/inspect/map chatter.
It prioritizes endpoint identity/COMMAND, CB geometry and raw CB register
values, then SMMU/route/result and valid SMR rows. It always reserves two rows
for the last Wi-Fi stage, including when no CB values were produced. Text is
bounded to the actual console width and available rows, with space left for
touch restoration and reboot footer so firmware scrolling does not erase the
summary. Unsupported text protocols or absent tagged logs use the previous
log renderer. Full logs remain in the RAM payload.

```sh
fastboot boot out/munch-wifi/smmu-screen-r1/munch-ios165-wifi-smmu-screen-r1.img
```

SHA256: `9bf23f408d058e0bba8f81c67adaec1612b4620a671f77c889b3c42a8743cdbc`.
The kernel is **byte-identical to smmu-survey-r4**. FD, DTB, GL helper and
bootargs are unchanged; only the loader log presentation changes. Hardware
execution of the new screen renderer is pending. Photograph the clean summary,
particularly `CB...` or `LAST KERNEL WIFI STAGE`, during the 90-second pause.
No iosroot reinstall. GPU r8 and previous images are retained.

`loader/check-wifi-screen.py` exercises the actual renderer under ASan/UBSan
with nine console geometries, bounded rows/columns, last-stage retention,
CB prioritization, noise filtering, long/unterminated lines and protocol
fallback. Independent image verification checks the helper hash and embedded
screen marker, full kernel and payload. `git diff --check` passed.
Reports: `out/munch-wifi/smmu-screen-r1/`.

## USB RAM log export r1

Built `out/munch-wifi/usb-log-r1/munch-ios165-wifi-usb-log-r1.img`.
The Wi-Fi step-4 kernel is identical to smmu-survey-r4; compact screen remains enabled.
After kernel return and touch cleanup, UEFI starts a restricted fastboot server for
300 seconds (serial `xnuloader`, product `munch-wifi-log`). It exports the frozen
full loader/kernel RAM log via `upload` / host `get_staged`; no partition access,
download, general command dispatcher, or kernel USB driver is enabled.
Wi-Fi DMA remains disabled. This does not demonstrate working Wi-Fi.

```sh
fastboot boot out/munch-wifi/usb-log-r1/munch-ios165-wifi-usb-log-r1.img
python3 devices/poco-f4/wifi/fetch-usb-log.py out/munch-wifi/logs/wifi-usb-r1.txt
```

The host helper waits for the correct product and atomically saves the log.
Direct alternative: `fastboot -s xnuloader get_staged wifi-log.txt`.
`oem log` acknowledges the already staged snapshot without replacing its kernel
contents. Unsupported commands fail; `reboot` is available after an acknowledged
request. Each IN transfer is bounded by three seconds; total session is five
minutes, then the original screen/reboot fallback runs. A cable disconnect can
reconnect within that window. Watchdog is serviced while waiting.

Image SHA256: `e8b23ba2f829f9c7f635fffd225f851be1d094b84e8bd2cb9973221d6068414d`.
Independent image verifier, actual command parser under ASan/UBSan, and mocked
host success/failure preservation passed. Hardware export is still untested;
`fastboot devices` showed no connected device during this build. Prior isolated
UEFI USB ping success is not yet evidence for large uploads after XNU return.

### Hardware result: USB export and complete CB observations

The user successfully retrieved `out/munch-wifi/logs/wifi-usb-r1.txt` through
post-return fastboot: 105330 bytes, host upload time 0.095 seconds, result
`returned 102767 0`. The local saved file was checked and hashed; structured
observations are in `results/usb-log-r1.json`. This supersedes the untested
USB-export status above. Reconnect and interrupted-transfer recovery remain
untested on hardware.

The full log confirms completion of all four CB reads and a normal survey
return. PCI identity remains 17cb:1101 / subsystem 17cb:0108; root and endpoint
COMMAND remain 0x400. SID route is absent, without a cycle in this run; no SMR
matches either root SID 0x1c00 or endpoint SID 0x1c01.

| CB | Physical address | SCTLR | FSR | CBAR | CBA2R | TTBR0 | TCR | TCR2 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3 | 0x15083000 | 0xe0 | 0x400 | 0x100ff | 1 | 0 | 0 | 0x40010072 |
| 5 | 0x15085000 | 0xe0 | 0x400 | 0x100ff | 1 | 0 | 0 | 0x38071 |
| 6 | 0x15086000 | 0xa1f0106 | 0 | 0 | 0 | 0 | 0 | 0x38071 |
| 7 | 0x15087000 | 0xc00106 | 0 | 0 | 0 | 0 | 0 | 0x38071 |

These are observations of banks unreferenced by the scanned valid SMRs, not
proof of available ownership. Nonzero control/fault state must be accounted
for before taking a context. No SID/SMMU changes or DMA were enabled. The
next implementation must establish an owned context and validate memory for
translation tables/buffers before enabling bus mastering or MHI. USB log
retrieval is now the preferred way to collect the full next-stage result.

## Owned RAM preflight r1 (hardware passed)

The next image keeps step 4 and adds an explicit `munch_wifi_memory=1` gate
(native config `wifi_memory_preflight=true`). It does not claim a context or
write SID/SMMU registers. The CB survey now prints inactive/fault-clear/root-empty
predicates, so the pending fault state in CB3/CB5 is explicit instead of selecting
the first unreferenced bank. These predicates alone do not establish ownership.

The CPU-only memory check allocates one VM page from XNU with KMA_ZERO, resolves
every 4 KiB physical subpage, checks alignment/physical continuity and containment
in captured munch RAM, and excludes every captured reserved-memory region.
`munch-wifi-memory-ranges.h` records exclusive bounds from the exact boot DT
SHA256 `da0ef458ed5b563b7c0e444aeb923e2360f82b345673153c9996bd20c1c9603c`.
The packer requires that DT and hashes both new source dependencies. The check
verifies zero contents and CPU pattern readback, zeroes the allocation and frees
it. It allocates no fixed PA or IOVA and publishes no translation table. DMA is
still disabled. This establishes allocator prerequisites, not SMMU translation.

```sh
fastboot boot out/munch-wifi/memory-preflight-r1/munch-ios165-wifi-memory-preflight-r1.img
python3 devices/poco-f4/wifi/fetch-usb-log.py out/munch-wifi/logs/wifi-memory-r1.txt
```

Look for `owned memory pa=... RAM/reserved/contiguous=1`, followed by
`memory preflight passed; allocation released; no IOVA or DMA`, and normal
survey return. A failed physical check must not be bypassed. The allocation
is released on every successful-allocation path, including failed validation.
The existing five-minute USB export and compact screen are enabled. GPU/display,
FD, DTB and GL helper are retained; previous images are untouched.

Image SHA256: `f8bc5180488cc382abc73552f4dc7777936f1613d0c23f4fb2e77f611ea8803c`.
Checks: native build, independent container/payload/kernel/source/bootargs
verification, 18 actual memory-code sanitizer scenarios over 4/16-KiB pages,
130 enumeration sanitizer runs, parser/gates, USB parser and compact-screen
sanitizers. Results and build log: `out/munch-wifi/memory-preflight-r1/`.

### Hardware result: memory preflight r1

The full USB log `out/munch-wifi/logs/wifi-memory-r1.txt` was retrieved and
verified locally: 106552 bytes, host upload time 0.096 seconds, kernel return
`returned 103969 0`. Structured result: `results/memory-preflight-r1.json`.
XNU allocated 0x4000 bytes at physical address 0x1920b8000. RAM containment,
reserved-memory exclusion, 4-KiB subpage continuity, zero/pattern/zero readback
all passed, and the allocation was freed before normal endpoint reset and
return. This is an observed allocator address, never a fixed future allocation
or IOVA. In particular, physical memory can be above 4 GiB, so future TTBR and
DMA-address handling must preserve upper bits.

CB3/CB5 remain disabled with pending FSR 0x400 and are excluded from the next
context experiment. CB6/CB7 are disabled, fault-clear and TTBR0-empty; neither
has been claimed. SID routing remains absent, PCI COMMAND remains 0x400 at
both root and endpoint. No SMMU writes, translation or DMA were tested here.
The next bounded experiment is an empty translation-context installation with
register readback and complete restoration, under the shared SMMU lock and
with bus mastering off. It must revalidate runtime state rather than treating
these observations as persistent ownership.
