#!/usr/bin/env python3
"""Build a fixed, one-shot XNU entry-checkpoint probe for the inspected F4."""
import argparse
import gzip
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

LOADER_SHA = '7104a1403bb0007a8fc7a7cf4dfb327bc7136a09ba9479f17e5eca449499557b'
KERNEL_SHA = '04263e76d6cc8b13e8f9e89229afa8f4d3039d8f3714ca7fd1d6e8603091d28b'
CMDLINE = '-v cpus=1 loader_return_after=8'

def check_munch_wifi_power_layout(dtb):
    """Require captured reserved RAM/RSC layout before the read-only step 2."""
    expected = {
        ('/reserved-memory/reserved-memory@80860000', 'reg'): [0, 0x80860000, 0, 0x20000],
        ('/soc/rsc@18200000', 'reg'): [0x18200000, 0x10000, 0x18210000, 0x10000, 0x18220000, 0x10000],
        ('/soc/rsc@18200000', 'qcom,drv-id'): [2],
        ('/soc/rsc@18200000', 'qcom,tcs-offset'): [0xd00],
        ('/soc/rsc@18200000', 'qcom,tcs-config'): [2, 2, 0, 3, 1, 3, 3, 1],
    }
    for (node, prop), values in expected.items():
        raw = subprocess.check_output(['fdtget', '-t', 'x', str(dtb), node, prop], text=True)
        if [int(v, 16) for v in raw.split()] != values:
            raise ValueError('Unreviewed Wi-Fi CMD DB/RSC layout: ' + prop)
    compatible = subprocess.check_output(['fdtget', '-t', 's', str(dtb),
        '/reserved-memory/reserved-memory@80860000', 'compatible'], text=True).strip()
    if compatible != 'qcom,cmd-db':
        raise ValueError('Expected the captured command database reservation')


def check_munch_epss(dtb):
    """Stock CPU profile is allowed only for the captured compatible layout."""
    node = '/soc/qcom,cpufreq-hw'
    def get(prop, kind):
        return subprocess.check_output(['fdtget', '-t', kind, str(dtb), node, prop], text=True).split()
    if (get('compatible', 's') != ['qcom,cpufreq-hw-epss'] or
            get('reg-names', 's') != ['freq-domain0', 'freq-domain1', 'freq-domain2'] or
            [int(x, 16) for x in get('reg', 'x')] != [0x18591000, 0x1000, 0x18592000, 0x1000, 0x18593000, 0x1000] or
            [int(x, 16) for x in get('qcom,lut-row-size', 'x')] != [4]):
        raise ValueError('Stock CPU frequency profile requires the verified munch EPSS layout')


def intermediate_kernel(original, checkpoint):
    """Insert a diagnostic return before or during the early arm_init prologue.

    Addresses and expected instructions apply only to the SHA-pinned baseline.
    Reuse the existing assembly checkpoint ABI without calling early C helpers.
    """
    segments = []
    offset = 32
    for _ in range(struct.unpack_from('<I', original, 16)[0]):
        command, size = struct.unpack_from('<II', original, offset)
        if command == 0x19:
            va, length, file_offset, file_size = struct.unpack_from('<QQQQ', original, offset + 24)
            segments.append((va, file_size, file_offset))
        offset += size
    def file_at(va, length):
        for base, size, start in segments:
            if base <= va and va + length <= base + size:
                return start + va - base
        raise ValueError('Diagnostic address is outside file-backed segments')
    cave = 0xfffffff0074424ac
    hook = {6: 0xfffffff0074454a8, 7: 0xfffffff00743952c,
            8: 0xfffffff007439568, 9: 0xfffffff0074396ec}[checkpoint]
    source = 0xfffffff007445430
    patched = bytearray(original)
    block = bytearray(original[file_at(source, 60):file_at(source, 60) + 60])
    expected = [0xd0ffffe9, 0x911fc129, 0xf940092a, 0xf100155f, 0x54000161,
                0xd2800000, 0xd2800001, 0xd2800042, 0xd28000a3, 0xd538100b,
                0x927ff96b, 0xd518100b, 0xd5033fdf, 0xf9400529, 0xd61f0120]
    if list(struct.unpack('<15I', block)) != expected:
        raise ValueError('Unexpected baseline assembly checkpoint instructions')
    stub_size = {6: 60, 7: 76, 8: 76, 9: 84}[checkpoint]
    if original[file_at(cave, stub_size):file_at(cave, stub_size) + stub_size] != bytes(stub_size):
        raise ValueError('Expected unused zero-filled entry padding')
    hook_bytes = (struct.pack('<III', 0x8b160280, 0xcb170000, 0xd65f03c0)
                  if checkpoint == 6 else struct.pack('<I',
                      {7: 0xd10303ff, 8: 0x94000695, 9: 0x940072f7}[checkpoint]))
    if original[file_at(hook, len(hook_bytes)):file_at(hook, len(hook_bytes)) + len(hook_bytes)] != hook_bytes:
        raise ValueError('Unexpected boot CPU arm_init handoff')
    # ADRP x9, _LowExceptionVectorBase: source page 0x7442 -> 0x7443.
    # No conditional fall-through: this image always stops at this location.
    words = expected[:]
    words[0] = 0xb0000009
    words[3] = words[4] = 0xd503201f
    words[8] = 0xd2800003 | (checkpoint << 5)  # mov x3, checkpoint
    if checkpoint in (7, 8, 9):
        # Switch from the high virtual alias to its identity-mapped physical
        # alias before using the existing MMU-off return sequence. x22/x23
        # retain the bootstrap VA/PA bases before the first arm_init helper call.
        words = [0x1000008b, 0xcb16016b, 0x8b17016b, 0xd61f0160] + words
        # adr x11, .+16; sub x11,x11,x22; add x11,x11,x23; br x11
    if checkpoint == 9:
        # Recover the bootstrap bases from the unchanged arm_init save slots.
        words = [0xf9404bf6, 0xf94047f7] + words  # ldr x22,[sp,#0x90]; ldr x23,[sp,#0x88]
    patched[file_at(cave, stub_size):file_at(cave, stub_size) + stub_size] = struct.pack('<' + 'I' * len(words), *words)
    branch = 0x14000000 | (((cave - hook) // 4) & 0x03ffffff)
    struct.pack_into('<I', patched, file_at(hook, 4), branch)
    return bytes(patched)


def no_early_bootargs_kernel(original):
    """Skip only loader_jump's PE bootcpu query before PE initialization."""
    address = 0xfffffff007456160
    offset = 32
    for _ in range(struct.unpack_from('<I', original, 16)[0]):
        command, size = struct.unpack_from('<II', original, offset)
        if command == 0x19:
            base, _, start, length = struct.unpack_from('<QQQQ', original, offset + 24)
            if base <= address and address + 4 <= base + length:
                at = start + address - base
                if original[at:at + 4] != struct.pack('<I', 0x9416f833):
                    raise ValueError('Unexpected loader_jump PE_parse_boot_argn call')
                patched = bytearray(original)
                # Return false from the skipped query, retaining current-core mode.
                struct.pack_into('<I', patched, at, 0x52800000)  # mov w0, #0
                return bytes(patched)
        offset += size
    raise ValueError('Early boot-argument call is outside file-backed segments')


def startup_checkpoint_kernel(original, checkpoint):
    """Stop at a reviewed machine/kernel bootstrap boundary."""
    segments = []
    offset = 32
    for _ in range(struct.unpack_from('<I', original, 16)[0]):
        command, size = struct.unpack_from('<II', original, offset)
        if command == 0x19:
            base, _, start, length = struct.unpack_from('<QQQQ', original, offset + 24)
            segments.append((base, start, length))
        offset += size
    def at(address, size):
        for base, start, length in segments:
            if base <= address and address + size <= base + length:
                return start + address - base
        raise ValueError('Startup checkpoint patch is outside file-backed segments')
    cave, target = 0xfffffff0074424ac, 0xfffffff0074562c8
    hook, expected = {50: (0xfffffff007446d14, 0x17fb2369),
                      51: (0xfffffff00730fb28, 0xb0004668),
                      52: (0xfffffff0073101cc, 0x9400005d),
                      53: (0xfffffff007a9f68c, 0x97fddcb7),
                      54: (0xfffffff007a9f844, 0x97f37ddf),
                      55: (0xfffffff00777f190, 0xb0ffcc20),
                      56: (0xfffffff00777f6a0, 0x9409c738)}[checkpoint]
    if original[at(cave, 8):at(cave, 8) + 8] != bytes(8):
        raise ValueError('Startup checkpoint requires unused entry padding')
    if struct.unpack_from('<I', original, at(hook, 4))[0] != expected:
        raise ValueError('Unexpected startup checkpoint hook instruction')
    def branch(source, destination):
        return 0x14000000 | (((destination - source) // 4) & 0x03ffffff)
    patched = bytearray(original)
    struct.pack_into('<II', patched, at(cave, 8), 0x52800000 | (checkpoint << 5), branch(cave + 4, target))
    struct.pack_into('<I', patched, at(hook, 4), branch(hook, cave))
    return bytes(patched)


def pack(fd_path, dtb_path, output, checkpoint=1, no_early_bootargs=False, ios_config=None, touch=False, cpus=1, native_gpu_config=None):
    if cpus not in (1, 8) or (cpus != 1 and checkpoint != 58):
        raise ValueError('Eight-core mode is supported only for the iOS profile')
    if touch and (checkpoint != 58 or not no_early_bootargs):
        raise ValueError('Touch prototype requires iOS mode and the tested return overlay')
    if checkpoint not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 21, 31, 32, 34, 35, 40, 41, 50, 51, 52, 53, 54, 55, 56, 57, 58):
        raise ValueError('Only reviewed checkpoints 1 through 12, 21, 31, 32, 34, 35, 40, 41, 50, 51, 52, 53, 54, 55, 56, 57 and 58 are supported')
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    uefi = here.parent / 'uefi'
    source = root / 'xnuloader/loader.c'
    native = None
    if native_gpu_config is not None:
        native = json.loads(native_gpu_config.read_text())
        if checkpoint != 58 or cpus != 8 or not touch or not no_early_bootargs or native.get('profile') not in ('munch-a650-diagnostic-22', 'munch-a650-ios-gpu'):
            raise ValueError('Native A650 config requires the reviewed touch/eight-CPU diagnostic profile')
        kernel = Path(native['kernel']['path']).read_bytes()
        if hashlib.sha256(kernel).hexdigest() != native['kernel']['sha256']:
            raise ValueError('Native kernel hash mismatch')
        source_report = Path(native['source_report']['path']).read_bytes()
        if hashlib.sha256(source_report).hexdigest() != native['source_report']['sha256']:
            raise ValueError('Native source report hash mismatch')
    else:
        kernel = (root / 'prebuilt/mach.development.bcm2837').read_bytes()
    sha = lambda data: hashlib.sha256(data).hexdigest()
    if sha(source.read_bytes()) != LOADER_SHA or (sha(kernel) != KERNEL_SHA if native is None else len(kernel) != native['kernel']['size']):
        raise ValueError('Loader or kernel revision differs from the reviewed checkpoint baseline')
    if kernel[:4] != b'\xcf\xfa\xed\xfe' or b'XNULDRST' not in kernel:
        raise ValueError('Expected the patched ARM64 Mach-O kernel with loader return slot')
    touch_report = None
    if touch and native is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location('munch_touch_overlay', here.parent / 'touch/kernel-overlay.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='munch-touch-code-') as directory:
            kernel, touch_report = module.build(kernel, Path(directory))
    if checkpoint in (6, 7, 8, 9):
        kernel = intermediate_kernel(kernel, checkpoint)
    if no_early_bootargs:
        if checkpoint not in (10, 11, 12, 21, 31, 32, 34, 35, 40, 41, 50, 51, 52, 53, 54, 55, 56, 57, 58):
            raise ValueError('Early boot-argument bypass is reviewed only for checkpoints 10, 11, 12, 21, 31, 32, 34, 35, 40, 41, 50, 51, 52, 53, 54, 55, 56, 57 and 58')
        if native is None:
            kernel = no_early_bootargs_kernel(kernel)
    if checkpoint in (50, 51, 52, 53, 54, 55, 56):
        kernel = startup_checkpoint_kernel(kernel, checkpoint)
    hello = None
    if checkpoint == 57:
        if not no_early_bootargs:
            raise ValueError('Hello probe requires the tested current-core return overlay')
        hello = (root / 'prebuilt/userland/hello').read_bytes()
        if sha(hello) != '48a910940230506813aa0b75692e58d1c6698feccf7c6f7771c8790b6c97522d':
            raise ValueError('Hello prebuilt differs from the reviewed raw-syscall program')
        if len(hello) > 65536 or hello[:4] != b'\xcf\xfa\xed\xfe':
            raise ValueError('Expected a small Mach-O RAM root')
    ios = None
    recovered = None
    cmdline = CMDLINE
    if checkpoint == 58:
        if not no_early_bootargs or ios_config is None:
            raise ValueError('iOS mode requires a verified config and tested return overlay')
        ios = json.loads(ios_config.read_text())
        if ios['version'] != '16.5' or ios['build'] != '20F66':
            raise ValueError('Unreviewed iOS version')
        if 'reference_ramboot' in ios:
            from recover_ramboot import recover, REFERENCE_SHA
            info = ios['reference_ramboot']
            if info['sha256'] != REFERENCE_SHA:
                raise ValueError('Unknown recovery reference')
            recovered = recover(Path(info['path']))
            if (ios['system'] != recovered['system'] or
                    Path(ios['identity']['path']).read_bytes() != recovered['identity'] or
                    fd_path.read_bytes() != recovered['fd'] or dtb_path.read_bytes() != recovered['dtb']):
                raise ValueError('Recovery identity, system checks, FD or DTB differs from the inspected reference')
        for key in (('root', 'identity') if recovered is not None else ('system', 'root', 'identity')):
            info = ios[key]
            path = Path(info['path'])
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if path.stat().st_size != info['size'] or digest != info['sha256']:
                raise ValueError('iOS config hash/size mismatch: ' + key)
        if native is not None and native['profile'] == 'munch-a650-ios-gpu':
            info = native['graphics_report']
            graphics_bytes = Path(info['path']).read_bytes()
            if sha(graphics_bytes) != info['sha256']:
                raise ValueError('Graphics report hash mismatch')
            graphics = json.loads(graphics_bytes)
            if (graphics['mesa_version'] != '25.0.7' or graphics['driver'] != 'freedreno' or
                    graphics['service'] != 'HakiosGPU' or graphics['helper']['sha256'] != ios['root']['sha256']):
                raise ValueError('GPU helper does not match the Mesa/service profile')
        hello = Path(ios['root']['path']).read_bytes()
        if len(hello) > 0x03e00000 or hello[32:36] != b'NXSB':
            raise ValueError('Expected a small APFS helper RAM root')
        cmdline = ('cpus=8 small_cores_after=0 loader_return_after=600' if cpus == 8
                   else '-v cpus=1 loader_return_after=600')
        if native is not None:
            if native['profile'] == 'munch-a650-ios-gpu':
                if native.get('revision', 0) < 4:
                    raise ValueError('iOS GPU requires the device-proven SCM resume kernel')
                speed = native.get('gpu_speed_index', 1)
                if speed not in (1, 5):
                    raise ValueError('Only reviewed A650 305/587 MHz selections are supported')
                hz = native.get('compositor_hz', 60)
                seconds = native.get('return_after_seconds', 600)
                sync = native.get('sync_frame_clock', False)
                if hz not in (60, 120) or seconds not in (0, 600) or type(sync) is not bool:
                    raise ValueError('Unsupported compositor/session profile')
                extra = ('fb_hz=120 ' if hz == 120 else '') + ('ca_vbl=1 glwait=1 ' if sync else '')
                verbose = native.get('verbose_console', False)
                if type(verbose) is not bool:
                    raise ValueError('verbose_console must be boolean')
                te = native.get('display_te', False)
                if type(te) is not bool or (te and not json.loads(source_report).get('display_te_staging')):
                    raise ValueError('TE requires the reviewed staging kernel')
                extra += 'munch_te=1 ' if te else ''
                scan = native.get('display_scan', False)
                if type(scan) is not bool or (scan and (te or hz != 60 or
                        not json.loads(source_report).get('display_command_staging'))):
                    raise ValueError('Scan sync requires command-staging sources, 60 Hz and display_te=false')
                extra += 'fb_scan=1 ' if scan else ''
                hud = native.get('display_scan_hud', False)
                if type(hud) is not bool or (hud and (not scan or
                        not json.loads(source_report).get('display_status_hud'))):
                    raise ValueError('Scan HUD requires scan sync and matching diagnostic sources')
                extra += 'fb_scan_hud=1 ' if hud else ''
                compositor = native.get('compositor_profile', 'baseline')
                if compositor not in ('baseline', 'umi-fast'):
                    raise ValueError('Unsupported compositor profile')
                if compositor == 'umi-fast':
                    if not scan or not json.loads(source_report).get('surface_cache_sources'):
                        raise ValueError('umi-fast requires staged scan sync and surface cache handoff')
                    # Match UMI composition settings. Absence of glwait=1 lets
                    # the bridge defer GPU completion to HakiosWaitsForGPU;
                    # unlike glwait=0 it retains the bridge fallback wait.
                    extra = extra.replace('glwait=1 ', '') + 'fb_threads=6 fb_filter=0 '
                cpu_profile = native.get('cpu_frequency_profile', 'firmware')
                if cpu_profile not in ('firmware', 'stock-max'):
                    raise ValueError('Unsupported CPU frequency profile')
                if cpu_profile == 'stock-max':
                    if not scan:
                        raise ValueError('Stock CPU profile is reviewed only with the native scan profile')
                    check_munch_epss(dtb_path)
                    # CPU EPSS matches munch DT. DDR BCM votes remain
                    # unvalidated on munch, so never enable them here.
                    extra += 'cpufreq=1 ddr=0 '
                wifi_probe = native.get('wifi_probe', 'disabled')
                if wifi_probe not in ('disabled', 'survey-return', 'power-enumeration-return'):
                    raise ValueError('Unsupported munch Wi-Fi diagnostic')
                if wifi_probe in ('survey-return', 'power-enumeration-return'):
                    wifi_sources = json.loads(source_report)
                    wifi_include = root / 'devices/poco-f4/wifi/munch-wifi-survey.inc'
                    if (not (wifi_sources.get('wifi_survey_only') or wifi_sources.get('wifi_diagnostic_only')) or
                            wifi_sources.get('wifi_survey_sha256') != sha(wifi_include.read_bytes()) or
                            b'MUNCH Wi-Fi: survey %s; returning to loader' not in kernel):
                        raise ValueError('Wi-Fi survey requires the matching gated diagnostic kernel')
                    wifi_step = native.get('wifi_survey_step', 1)
                    if type(wifi_step) is not int or wifi_step not in ((3, 4) if wifi_probe == 'power-enumeration-return' else (1, 2)):
                        raise ValueError('Unsupported Wi-Fi survey step')
                    if wifi_step >= 2:
                        check_munch_wifi_power_layout(dtb_path)
                    if wifi_step >= 3:
                        if sha(dtb_path.read_bytes()) != 'da0ef458ed5b563b7c0e444aeb923e2360f82b345673153c9996bd20c1c9603c':
                            raise ValueError('Power enumeration requires the exact captured munch DT')
                        for name in ('munch-wifi-enumeration.inc', 'munch-wifi-dma-survey.inc', 'munch-wifi-memory.inc', 'munch-wifi-memory-ranges.h', 'munch-wifi-pcie.inc',
                                     'munch-wifi-pcie.h', 'munch-wifi-phy.h', 'munch-wifi-rpmh.inc'):
                            if wifi_sources.get('wifi_diagnostic_sources', {}).get(name) != sha((wifi_include.parent / name).read_bytes()):
                                raise ValueError('Power diagnostic source hash mismatch: ' + name)
                    extra += f'munch_wifi_step={wifi_step} munch_wifi_return=1 '
                    if native.get('wifi_memory_preflight', False):
                        if native['wifi_memory_preflight'] is not True or wifi_step != 4:
                            raise ValueError('Memory preflight requires step 4 return diagnostic')
                        extra += 'munch_wifi_memory=1 '

                cmdline = (f'cpus=8 small_cores_after=0 munch_touch=1 gpu=1 adreno_speed={speed} '
                           + extra + f'loader_return_after={seconds}')
                if verbose:
                    cmdline = '-v ' + cmdline
            else:
                cmdline = ('-v cpus=8 small_cores_after=0 munch_touch=1 adreno=22 adreno_speed=1 '
                           'adreno_pause=0 adreno_watch=20 adreno_return=1 loader_return_after=120')
    fd = fd_path.read_bytes()
    metadata = json.loads(fd_path.with_suffix(fd_path.suffix + '.munch.json').read_text())
    recovery_fd = (ios is not None and recovered is not None and
                   metadata.get('recovered_from_reference') == recovered['reference_sha256'])
    if (metadata.get('target') != 'munch' or
            (not recovery_fd and metadata.get('diagnostic_revision') != (3 if checkpoint == 58 else 2)) or
            metadata.get('fd_sha256') != sha(fd) or len(fd) != 0x300000):
        raise ValueError('Expected the reviewed revision-2 F4 FD')
    model = subprocess.check_output(['fdtget', str(dtb_path), '/', 'model'], text=True).strip()
    if model != 'Qualcomm Technologies, Inc. xiaomi munch':
        raise ValueError('Expected the captured F4 live DTB')
    with tempfile.TemporaryDirectory(prefix='munch-checkpoint-') as temporary:
        work = Path(temporary)
        shutil.copy2(source, work / 'loader.c')
        shutil.copy2(root / 'xnuloader/efi.h', work / 'efi.h')
        subprocess.run(['patch', '--batch', '--forward', '-p1', '-i', str(here / 'checkpoint-loader.patch')],
                       cwd=work, check=True)
        if native is not None and native.get('wifi_probe') in ('survey-return', 'power-enumeration-return'):
            if native.get('loader_quiet', False):
                raise ValueError('Wi-Fi survey requires the visible loader console')
            loader_source = (work / 'loader.c').read_text()
            full_log = '\t\tfor (uint64_t i = 0; i < klog_len; i++) put_char(klog[i]);'
            compact_log = '''\t\t/* USB commands are disabled in this one-shot loader. Keep
\t\t * the hardware survey visible for a photograph instead. */
\t\tstatic const char tag[] = "MUNCH Wi-Fi:";
\t\tunsigned found = 0;
\t\tprint("\\nMUNCH WIFI SURVEY (screen log; USB disabled)\\n");
\t\tfor (uint64_t line = 0; line < klog_len;) {
\t\t\tuint64_t end = line;
\t\t\twhile (end < klog_len && klog[end] != '\\n') end++;
\t\t\tfor (uint64_t pos = line; pos + sizeof(tag) - 1 <= end; pos++) {
\t\t\t\tif (memcmp(klog + pos, tag, sizeof(tag) - 1)) continue;
\t\t\t\tfor (uint64_t i = pos; i < end; i++) put_char(klog[i]);
\t\t\t\tput_char('\\n'); found++; break;
\t\t\t}
\t\t\tline = end < klog_len ? end + 1 : end;
\t\t}
\t\tif (!found) {
\t\t\tprint("No Wi-Fi survey lines; last kernel log follows:\\n");
\t\t\tuint64_t start = klog_len > 1800 ? klog_len - 1800 : 0;
\t\t\tfor (uint64_t i = start; i < klog_len; i++) put_char(klog[i]);
\t\t}'''
            if loader_source.count(full_log) != 1:
                raise ValueError('Unexpected checkpoint log printer')
            summary = native.get('wifi_screen_summary', False)
            if type(summary) is not bool:
                raise ValueError('wifi_screen_summary must be boolean')
            if summary:
                helper = here / 'wifi-screen-summary.inc'
                shutil.copy2(helper, work / helper.name)
                hook = 'static void\nwrite_logs(const char *klog, uint64_t klog_len)'
                if loader_source.count(hook) != 1:
                    raise ValueError('Unexpected Wi-Fi screen summary hook')
                loader_source = loader_source.replace(hook, '#include "wifi-screen-summary.inc"\n\n' + hook, 1)
                compact_log = '\t\tif (!munch_wifi_show_summary(klog, klog_len)) {\n' + compact_log + '\n\t\t}'
            (work / 'loader.c').write_text(loader_source.replace(full_log, compact_log, 1))
        if native is not None and native.get('wifi_usb_log', False):
            if native.get('wifi_usb_log') is not True or native.get('wifi_probe') not in ('survey-return', 'power-enumeration-return'):
                raise ValueError('USB log export requires an explicit Wi-Fi return diagnostic')
            loader_source = (work / 'loader.c').read_text()
            header = 'static int64_t\nusb_wait(uint8_t endpoint)'
            loader_source = loader_source.replace(header,
                'static int munch_usb_log_active;\nstatic uint64_t munch_usb_log_deadline;\n\n' + header, 1)
            wait_guard = '\t\tif (running_live != NULL && READ_SYSREG(CNTVCT_EL0) - began > 3 * freq) {'
            if loader_source.count(wait_guard) != 1:
                raise ValueError('Unexpected USB wait guard')
            loader_source = loader_source.replace(wait_guard,
                '\t\tif ((running_live != NULL || munch_usb_log_active) &&\n'
                '\t\t    (READ_SYSREG(CNTVCT_EL0) - began > 3 * freq ||\n'
                '\t\t     (munch_usb_log_active && READ_SYSREG(CNTVCT_EL0) >= munch_usb_log_deadline))) {\n'
                '\t\t\tif (munch_usb_log_active && USB->abort_xfer) USB->abort_xfer(endpoint);', 1)
            disconnect = '\t\t\tusb_connected = (data.device_state == UsbDeviceStateConnected);\n\t\t\treturn -1;'
            if loader_source.count(disconnect) != 1:
                raise ValueError('Unexpected USB disconnect wait path')
            loader_source = loader_source.replace(disconnect,
                '\t\t\tusb_connected = (data.device_state == UsbDeviceStateConnected);\n'
                '\t\t\tif (!munch_usb_log_active || !usb_connected) return -1;\n\t\t\tcontinue;', 1)
            chunk = '#define USB_CHUNK    (1024 * 1024)'
            if loader_source.count(chunk) != 1:
                raise ValueError('Unexpected USB transfer chunk')
            loader_source = loader_source.replace(chunk, '#define USB_CHUNK    (64 * 1024) /* bounded log export */', 1)
            # Include after usb_start/usb_tx and before efi_main. Retain the
            # existing pi.usb_mode rejection; the general server stays unused.
            hook = 'efi_status EFIAPI\nefi_main(efi_handle image, efi_system_table *st)'
            if loader_source.count(hook) != 1:
                raise ValueError('Unexpected EFI entry for USB log export')
            loader_source = loader_source.replace(hook, '#include "wifi-usb-log.inc"\n\n' + hook, 1)
            run = '\t\trun_kernel(1, pi.checkpoint);'
            if loader_source.count(run) != 1:
                raise ValueError('Unexpected one-shot Wi-Fi return hook')
            loader_source = loader_source.replace(run, run +
                '\n\t\tif (munch_usb_log_server()) {\n\t\t\tBS->stall(200000);\n'
                '\t\t\tST->rt->reset_system(0, 0, 0, NULL);\n\t\t}', 1)
            helper = (here / 'wifi-usb-log.inc').read_text()
            helper = helper.replace('static int munch_usb_log_active;\nstatic uint64_t munch_usb_log_deadline;\n', '')
            (work / 'wifi-usb-log.inc').write_text(helper)
            (work / 'loader.c').write_text(loader_source)
        if native is not None and native.get('loader_quiet', False):
            if type(native['loader_quiet']) is not bool or native.get('loader_bottom_console', False):
                raise ValueError('Quiet loader requires a boolean flag and no bottom console')
            loader_source = (work / 'loader.c').read_text()
            if loader_source.count('static int screen_quiet;') != 1:
                raise ValueError('Unexpected loader console gate')
            (work / 'loader.c').write_text(loader_source.replace('static int screen_quiet;',
                'static int screen_quiet = 1; /* munch: retain RAM log without screen output */', 1))
        if native is not None and native.get('loader_bottom_console', False):
            if type(native['loader_bottom_console']) is not bool:
                raise ValueError('loader_bottom_console must be boolean')
            loader_source = (work / 'loader.c').read_text()
            loader_source = loader_source.replace('static int screen_quiet;',
                'static int screen_quiet;\n#include "bottom-console.inc"', 1)
            loader_source = loader_source.replace('if (ST->con_out != NULL && !screen_quiet) {',
                'if (munch_console && !screen_quiet) { munch_console_char(c); return; }\n'
                '\tif (ST->con_out != NULL && !screen_quiet) {', 1)
            loader_source = loader_source.replace('ST = st;', 'ST = st;\n\tmunch_console_init();', 1)
            (work / 'loader.c').write_text(loader_source)
            shutil.copy2(here / 'bottom-console.inc', work / 'bottom-console.inc')
        if cpus == 8:
            loader_source = (work / 'loader.c').read_text()
            old = '\t\t/* This probe publishes only the current CPU, even with cpus=1 present. */\n\t\tmore_cores = 0;'
            new = '\t\tmore_cores |= (starts_with(c, "cpus=") || starts_with(c, "cpumask=")) && (c == pi.cmdline || c[-1] == 32);'
            guard = '"-v cpus=1 loader_return_after=600"'
            if loader_source.count(old) != 1 or loader_source.count(guard) != 1:
                raise ValueError('Unexpected single-core publication or payload guard')
            loader_source = loader_source.replace(old, new, 1).replace(guard, '"' + cmdline + '"', 1)
            if native is not None:
                allocation = '\tuint64_t rd_pa = kern_hi;'
                reservation = ('\t/* Dedicated low metadata page, inside the kernel static extent. */\n'
                               '\tuint64_t scm_pa = kern_hi, scm_size = 4 * PAGE_SIZE_MAX;\n'
                               '\tif (scm_pa >= 0x100000000ULL || scm_pa + scm_size > 0x100000000ULL ||\n'
                               '\t    scm_pa + scm_size > phys_base + RESIDENT_RD_OFFSET) {\n'
                               '\t\tprint("MUNCH: no low SCM reservation fits\\n"); return 0;\n\t}\n'
                               '\tmemset((void *)scm_pa, 0, scm_size);\n'
                               '\tprint("MUNCH SCM reserved page: %x size %x\\n", scm_pa, scm_size);\n'
                               '\tuint64_t rd_pa = kern_hi + scm_size;')
                chosen = '\tdt_begin(&w, "chosen");'
                if loader_source.count(allocation) != 1 or loader_source.count(chosen) != 1:
                    raise ValueError('Unexpected native metadata reservation hook')
                loader_source = loader_source.replace(allocation, reservation, 1)
                loader_source = loader_source.replace('if (kern_hi + rd_size > res_pa)', 'if (rd_pa + rd_size > res_pa)', 1)
                loader_source = loader_source.replace(chosen, chosen + '\n\tuint64_t scm_metadata[2] = {scm_pa, scm_size};\n\tdt_prop(&w, "munch-scm-metadata", scm_metadata, sizeof(scm_metadata));', 1)
            (work / 'loader.c').write_text(loader_source)
        if ios is not None:
            identity = Path(ios['identity']['path']).read_bytes()
            system = ios['system']
            include = (f"#define MUNCH_SYSTEM_BYTES {system['size']}ULL\n"
                       f"#define MUNCH_SYSTEM_CRC32 0x{system['crc32']}u\n")
            (work / 'ios-root.inc').write_text(include + (here / 'ios-root.inc').read_text())
            loader_source = (work / 'loader.c').read_text()
            loader_source = loader_source.replace('static int\nrun_kernel(', '#include "ios-root.inc"\n\nstatic int\nrun_kernel(', 1)
            needle = '\t\trun_kernel(1, pi.checkpoint);'
            if loader_source.count(needle) != 1:
                raise ValueError('Unexpected one-shot entry for iOS load hook')
            loader_source = loader_source.replace(needle,
                '\t\tif (!munch_load_iosroot(&pi)) { reboot(); return 0; }\n' + needle)
            if touch:
                adapter = ('#define system_table ST\n'
                           'static void text(const char *s) { print("%s", s); }\n'
                           'static void hex(uint64_t x) { print("%x", x); }\n'
                           'static void field(const char *s, uint64_t x) { print("%s%x\\n", s, x); }\n'
                           '#define MUNCH_TOUCH_RELEASE_RESET\n#define MUNCH_TOUCH_POWER_ENABLE\n'
                           '#define MUNCH_TOUCH_HANDOFF\nstatic unsigned touch_handoff_ready;\n')
                for name in ('touch-power-probe.inc', 'touch-power-enable.inc', 'touch-probe.inc'):
                    adapter += '#include "' + str(uefi / name) + '"\n'
                loader_source = loader_source.replace('#include "ios-root.inc"', adapter + '\n#include "ios-root.inc"', 1)
                hook = '\t\trun_kernel(1, pi.checkpoint);'
                replacement = ('\t\tif (!touch_power_enable_begin()) { touch_power_enable_end(); reboot(); return 0; }\n'
                               '\t\ttouch_probe(ST, NULL);\n'
                               '\t\tif (!touch_handoff_ready) { touch_power_enable_end(); reboot(); return 0; }\n'
                               + hook + '\n\t\ttouch_restore();\n\t\ttouch_power_enable_end();')
                if loader_source.count(hook) != 1:
                    raise ValueError('Unexpected touch handoff hook')
                loader_source = loader_source.replace(hook, replacement, 1)
            (work / 'loader.c').write_text(loader_source)
        else:
            identity = subprocess.check_output([sys.executable, str(root / 'xnuloader/mkidentity.py'), '--placeholder'])
        (work / 'identity.h').write_bytes(identity)
        subprocess.run(['clang', '--target=aarch64-unknown-windows', '-ffreestanding', '-fno-builtin',
                        '-fno-stack-protector', '-fshort-wchar', '-mgeneral-regs-only', '-O1',
                        '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function',
                        '-Wno-unused-const-variable', '-Wno-unused-variable', '-Wno-unused-but-set-variable', '-c', 'loader.c', '-o', 'loader.obj'], cwd=work, check=True)
        subprocess.run(['lld-link', '/subsystem:efi_application', '/entry:efi_main', '/nodefaultlib',
                        '/base:0xA1000000', '/out:loader.efi', 'loader.obj'], cwd=work, check=True)
        loader = (work / 'loader.efi').read_bytes()
        body = bytearray()
        entries = [('loader.efi', loader), ('kernel', kernel),
                           ('cmdline', cmdline.encode() + b'\0'),
                           ('checkpoint', str(checkpoint).encode() + b'\0')]
        if hello is not None:
            entries.append(('ramdisk', hello))
        for name, data in entries:
            body += struct.pack('<16sII', name.encode(), len(data), 0)
            body += data + b'\0' * (-len(data) % 16)
        body += b'\0' * 24
        payload = struct.pack('<II', 0x50554e58, len(body)) + body
        if len(payload) > 0x03fff000:
            raise ValueError('Payload exceeds the reserved RAM capacity')
        subprocess.run(['clang', '--target=aarch64-linux-gnu', '-c', str(uefi / 'bootshim.S'),
                        '-DFD_BASE=0x9fc00000', '-DFD_SIZE=0x300000', '-DPAYLOAD_BASE=0xf0000000',
                        '-o', str(work / 'shim.o')], check=True)
        subprocess.run(['aarch64-linux-gnu-objcopy', '-O', 'binary', str(work / 'shim.o'),
                        str(work / 'shim.bin')], check=True)
        shim = (work / 'shim.bin').read_bytes()
        blob = shim + fd + payload
        blob += b'\0' * (-len(blob) % 16)
        image_kernel = work / 'kernel'
        image_kernel.write_bytes(gzip.compress(blob, mtime=0) + dtb_path.read_bytes())
        name = b'TRAILER!!!\0'
        cpio = b'070701' + b''.join(f'{n:08x}'.encode() for n in
                                  [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, len(name), 0]) + name
        cpio += b'\0' * (-len(cpio) % 4)
        (work / 'ramdisk').write_bytes(gzip.compress(cpio, mtime=0))
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(root / 'devices/poco-x5-pro/prebuilt/mkbootimg.py'),
                        '--header_version', '1', '--board', f'MUNCHCP{checkpoint}', '--kernel', str(image_kernel),
                        '--ramdisk', str(work / 'ramdisk'), '-o', str(output)], check=True)
        report = {**metadata, 'mode': 'ios165-read-only-ramboot' if ios is not None else ('xnu-hello-ramroot' if hello is not None else 'xnu-entry-checkpoint'), 'checkpoint': checkpoint, 'cmdline': cmdline,
                  'loader_overlay_sha256': sha((here / 'checkpoint-loader.patch').read_bytes()),
                  'published_cpu_count': 1 if cpus == 1 else None, 'requested_cpu_count': cpus,
                  'cpu_publication': 'current-core-only' if cpus == 1 else 'PSCI-affinity-filtered-up-to-eight',
                  'verbose_console': '-v' in cmdline.split(), 'skip_early_bootcpu_query': no_early_bootargs,
                  'loader_source_sha256': LOADER_SHA, 'kernel_baseline_sha256': KERNEL_SHA, 'kernel_sha256': sha(kernel),
                  'loader_sha256': sha(loader), 'image_sha256': sha(output.read_bytes()),
                  'image_size': output.stat().st_size, 'shim_size': len(shim),
                  'bootshim_revision': 2, 'shim_source_sha256': sha((uefi / 'bootshim.S').read_bytes()), 'dtb_sha256': sha(dtb_path.read_bytes()),
                  'ramroot_sha256': sha(hello) if hello is not None else None,
                  'contains_xnu': True, 'contains_ios': ios is not None,
                  'identity': 'recovered-reference' if recovered is not None else ('local-ipsw' if ios is not None else 'placeholder'),
                  'recovered_inputs': ({'reference': ios['reference_ramboot'], 'system': recovered['system'],
                                        'identity_sha256': sha(recovered['identity']),
                                        'reference_loader_sha256': recovered['loader_sha256']}
                                       if recovered is not None else None),
                  'device_tested': False, 'touch_prototype': touch_report, 'native_gpu': native}
        if native is not None and native.get('wifi_usb_log', False):
            report['wifi_usb_log_sha256'] = sha((here / 'wifi-usb-log.inc').read_bytes())
        if native is not None and native.get('wifi_screen_summary', False):
            report['wifi_screen_summary_sha256'] = sha((here / 'wifi-screen-summary.inc').read_bytes())
        output.with_suffix(output.suffix + '.json').write_text(json.dumps(report, indent=2) + '\n')
        print(f'Built XNU checkpoint-{checkpoint} probe: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fd', type=Path, required=True)
    parser.add_argument('--dtb', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--checkpoint', type=int, choices=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 21, 31, 32, 34, 35, 40, 41, 50, 51, 52, 53, 54, 55, 56, 57, 58], default=1)
    parser.add_argument('--no-early-bootargs', action='store_true',
                        help='Checkpoints 10, 11, 12, 21, 31, 32, 34, 35, 40, 41, 50, 51, 52, 53, 54, 55, 56, 57 and 58: bypass the pre-PE bootcpu query')
    parser.add_argument('--ios-config', type=Path)
    parser.add_argument('--cpus', type=int, choices=[1, 8], default=1, help='iOS only: request all eight CPUs through the existing PSCI path; disable verbose framebuffer console')
    parser.add_argument('--touch', action='store_true', help='Experimental ABI-pinned FT3658U driver with verified UEFI power handoff')
    parser.add_argument('--native-gpu-config', type=Path, help='Source-built A650 diagnostic or iOS GPU kernel manifest')
    args = parser.parse_args()
    pack(args.fd.resolve(), args.dtb.resolve(), args.output.resolve(), args.checkpoint, args.no_early_bootargs, args.ios_config, args.touch, args.cpus, args.native_gpu_config)
