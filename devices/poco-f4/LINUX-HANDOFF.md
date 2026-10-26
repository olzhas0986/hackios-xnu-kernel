# POCO F4 build handoff

Current device status and preferred image are recorded in [README.md](README.md).
The pending step is to boot `munch-display-scan-r1.img` and record its TE
configuration and three Window summaries. Controller-based presentation has
not been integrated into the preferred iOS image.

## Required local inputs

Git contains source and selected observations. Reproduction also requires:

- Captured XBL and live DTB, with their extraction/hash manifests.
- Mu-Silicium at `uefi/BASE`, its pinned submodules and device-local firmware.
- A prepared XNU tree, generated dependencies and the pinned prebuilt inputs.
- Local iOS system/helper APFS images, IPSW identity and source-integrity report.
- Mesa outputs, graphics report and matching native-kernel/iOS configurations.

Keep each image, kernel and helper with its JSON manifest. Configuration paths
may be absolute: after moving inputs, update those paths and recompute parent
manifest hashes when a referenced manifest changes. Keep hash validation enabled.
OS images, phone captures and vendor executables remain outside Git.

## Toolchain and entry points

The recorded native kernel build used clang 21; the latest probe packaging used
clang/lld 18. Recreate the host compiler, cctools and iOS SDK before rebuilding.
A saved environment script referring to temporary tool installations is not a
portable setup script.

1. Follow [uefi/README.md](uefi/README.md) to build the storage-enabled FD.
2. Stage native GPU/touch sources:

   ```sh
   python3 devices/poco-f4/gpu/stage-kernel.py /path/to/prepared-xnu \
       --touch-poll-ms 4 --report /path/to/source-report.json
   ```

3. With the valid toolchain environment loaded, build the native kernel:

   ```sh
   bash xnu-build.sh -j8 BUILD_DSYM=0 build
   ```

   The `build` target previously passed. Optional `all` packaging was not
   established as passing.

4. Prepare the Mesa helper and native configuration described in
   [GPU-BRINGUP.md](GPU-BRINGUP.md), then pack checkpoint 58 with
   `--cpus 8 --touch --no-early-bootargs --native-gpu-config` and the matching
   `--ios-config`. See [loader/README.md](loader/README.md).
5. Independently verify the packed image with `touch/verify-image.py`.

Normal `stage-kernel.py` use restores the baseline framebuffer. `--te-staging`
opts into the experiment that felt slower and did not resolve tearing.

## Host checks

Python, a C compiler and ASan/UBSan are needed for `gpu/check-scm.py`,
`touch/check-xnu-events.py`, `touch/check-power-guards.py`,
`loader/check-bottom-console.py`, `uefi/check-panel-te.py` and
`uefi/check-display-scan.py`. Compile/run `display/check-te-frame.c` and
`touch/check-protocol.c` with the same sanitizers.

`uefi/check-usb-descriptors.py` checks USB descriptors.
`uefi/check-bootshim.py` additionally requires Unicorn, clang, llvm-readelf and
aarch64-linux-gnu-objcopy. Host checks do not establish a safe physical display
copy interval; that requires device measurements.
