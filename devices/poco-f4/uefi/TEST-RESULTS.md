# F4 device results

This consolidates the recorded bring-up evidence. Device observations below are
from owner reports and photographs; it is not a fresh hardware test run. Host
build/integrity results do not establish hardware behavior. Current instructions
are in the component READMEs.

## UEFI and USB

| Stage | Recorded result | Limits |
| --- | --- | --- |
| Initial RELEASE probe | Black screen, return to Android after about 5–10 seconds | Entry point and reset cause were not identified |
| DEBUG probe | SEC/DXE output ended after FeatureEnablerDxe load with a device-error assertion | Last load message does not prove the driver caused the failure |
| Revision 2, FeatureEnablerDxe omitted | EFI diagnostic executed; GOP and memory-map queries succeeded | Internal cause of the earlier assertion remains unresolved |
| Initial USB probe | Host rejected the BOS descriptor | A malformed initializer was independently confirmed |
| USB revision 2 | Product query returned munch-usb-probe; oem ping returned OKAY | Large transfers, reconnects, sustained sessions and XNU coexistence were not established |
| Storage FD revision 3 | Exactly one iosroot discovered; first 64 KiB read successfully | Not every ConnectController status was successful |

Revision-2 GOP observations: framebuffer 0x9C000000, 1080x2400, stride 1080
pixels, pixel format 1 (BGR reserved). Large conventional-memory ranges included
[0xC0000000, 0xF0000000) and [0xF4000000, 0x200000000). The probe found no free
memory overlapping its checked DDR-hole/XNUP intervals; this was not a complete
memory-map audit.

The USB BOS array had only 41 explicit bytes in a 42-byte declaration, shifting
SSP fields and silently adding trailing padding. Restoring the descriptor and
adding structural checks preceded the successful USB command round trips.
The host CLI reported OKAY for ping without displaying a pong payload.

## XNU and initial userland

The recorded successful checkpoint sequence includes 1..5, 9, revised 10, 11,
31, 32, 34, 35, 12, 40, 41, 21, 50, 52, 54 and 56. Skipped intermediate stops
were not independently tested. Later combined stops exercise intervening code
but do not prove every initialized peripheral works.

Original checkpoint 10 hung after handoff. Checkpoint 9 returned; revised 10
and subsequent stops returned after bypassing the premature PE bootcpu query.
This supports the current-core workaround without establishing a general
alternate-core return fix.

Mode 57 produced static hello user-space output, followed by the program's
intentional PID-1 exit panic and return to UEFI. Exact greeting, PID/UID and panic
text were not captured. Mode 58 subsequently reached an Apple logo and iOS UI.
The initial installer --check was recorded for a 2476732416-byte system and
17179869184-byte iosroot; an --apply/readback transcript was not retained.

All five source IPSW members passed ZIP size/CRC checks with local SHA256
records. Initial system/helper APFS images passed structural checks and full
content/metadata readback: 62261 system entries and 458 helper entries.
These are integrity checks, not a separate Apple signature-chain validation.

## Touch

Initial runtime-read and reset-release probes did not verify the controller.
Power observation found SID5 L1/L10 disabled. The temporary-enable r5 probe
then reported `TOUCH CONTACTS: PASS`, 0x154 decoded positions and 0x25b CRC-valid
reads, with two failed reads across wiring candidates/polling. It confirmed
restoration of the original disabled power state. SID5 L10 subtype read 0x7d.

The XNU powered-handoff prototype subsequently provided iOS input. The preferred
native quiet image uses a 4-ms polling sleep; slightly better touch was reported.
No measured end-to-end latency or long-duration reliability result is recorded.

## GPU and bootshim

| Stage | Recorded result | Resulting change |
| --- | --- | --- |
| Drawing r1 | Step 12 failed after zap metadata submission; no bands | Reviewed SCM width and low-DMA handling |
| Drawing r2 | No contiguous low-DMA metadata page | Reserved low metadata memory in loader |
| Drawing r3 | PAS_INIT_IMAGE repeatedly interrupted until inherited limit | Preserved A6 continuation and extended bounded resumption |
| Drawing r4 | Orange, white and blue bands | Hardware command execution confirmed |
| First iOS GPU image | UEFI exception before XNU | Investigated shared bootshim relocation |
| iOS GPU r2 | UI reached; much smoother rendering reported | Accelerated UI confirmed, tearing investigation continued |

The r3/local -1 was not a final vendor signature verdict. The revised SCM path
uses a distinct local timeout result. Authentication progress and drawing were
subsequently demonstrated by the r4 bands; sustained shutdown/recovery behavior
was not separately established by that observation.

The inherited FD-first, forward-only bootshim could overwrite unread payload
input. Synthetic execution reproduced corruption. The replacement chooses copy
order from actual ranges, uses memmove directions, preserves x0/x1 and rejects
unsafe layouts. Unicorn checks passed seven relocation layouts and invalid-input
cases. The original device runtime source address was not captured, so the exact
overlap behind its first exception remains a hypothesis. The r2 image retained
the original FD/payload while replacing the shim and subsequently reached UI.

## Display and current endpoint

Horizontal discontinuities were reported during swipes/card movement. CPU copies
into the live framebuffer and synthetic VSync make tearing plausible; a complete
causal diagnosis has not been established.

GPIO66 TE measurements were 60.69, 60.68 and 60.68 Hz. Physical 120-Hz switching
has not been implemented. The earlier ten-minute return matched the configured
loader timer; disabling that timer removes the deliberate return but does not
rule out unrelated failures.

TE-staging r1 felt slower and did not visibly resolve tearing. The preferred
`munch-ios165-gpu-quiet-r1.img` restores baseline presentation, requests 587 MHz,
uses 4-ms touch polling sleep and suppresses screen logs. Eight CPUs are
requested; their actual online count has not been captured.

Pending: boot `munch-display-scan-r1.img` and record TE configuration plus three
Window summaries before adapting UMI's write-pointer synchronization. Physical
120 Hz, sustained stability, audio, Wi-Fi and modem support remain unestablished.

## R7 pre-loader exception report

The user reported an exception before xnuloader with
`munch-ios165-gpu-fast-r7.img` (the variant without CPU frequency changes).
The supplied photo includes ShmBridgeDxe/ScmDxe and DxeCore frames, ending in
ArmCpuDxe's exception-handler assertion. The image was independently verified
again after the report. Its shim (416 bytes), 3-MiB FD and appended live DTB
hashes are identical to the working r6 image; the r7 decompressed blob is only
16 bytes larger. This excludes an intentional FD/shim/DTB change and does not
prove the runtime placement or secure-firmware state was identical. Boot origin
and a same-state r6 control test are pending. No binary workaround is justified
from the photo alone; compositor settings are consumed after this stage.
