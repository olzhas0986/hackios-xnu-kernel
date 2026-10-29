#!/usr/bin/env python3
"""Independently inspect the experimental touch boot image before publication."""
import argparse
import hashlib
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('image', type=Path)
p.add_argument('--fd', required=True, type=Path)
p.add_argument('--dtb', required=True, type=Path)
a = p.parse_args()
root = Path(__file__).resolve().parents[3]
sha = lambda b: hashlib.sha256(b).hexdigest()
image = a.image.read_bytes()
report = json.loads(a.image.with_suffix('.img.json').read_text())
assert len(image) == report['image_size'] < 120 * 1024 * 1024
assert sha(image) == report['image_sha256']
assert image[:8] == b'ANDROID!' and struct.unpack_from('<I', image, 40)[0] == 1
page, kernel_size = struct.unpack_from('<I', image, 36)[0], struct.unpack_from('<I', image, 8)[0]
z = zlib.decompressobj(31)
blob = z.decompress(image[page:page + kernel_size]) + z.flush()
assert z.eof and z.unused_data == a.dtb.read_bytes()
assert sha(z.unused_data) == report['dtb_sha256']
shim_size = report.get('shim_size', 208)
assert shim_size % 16 == 0 and 64 <= shim_size <= 4096
assert blob[56:60] == b'ARMd'
assert blob[shim_size:shim_size + 0x300000] == a.fd.read_bytes()
assert sha(a.fd.read_bytes()) == report['fd_sha256']
if report.get('recovered_inputs') is not None:
    sys.path.insert(0, str(root / 'devices/poco-f4/loader'))
    from recover_ramboot import recover, REFERENCE_SHA
    recovery = report['recovered_inputs']
    assert recovery['reference']['sha256'] == REFERENCE_SHA
    reference = recover(Path(recovery['reference']['path']))
    assert reference['fd'] == a.fd.read_bytes() and reference['dtb'] == a.dtb.read_bytes()
    assert recovery['system'] == reference['system']
    assert recovery['identity_sha256'] == sha(reference['identity'])
    assert recovery['reference_loader_sha256'] == reference['loader_sha256']
    assert report['identity'] == 'recovered-reference'
at = shim_size + 0x300000
magic, length = struct.unpack_from('<II', blob, at)
assert magic == 0x50554e58
end, at = at + 8 + length, at + 8
entries = {}
while at + 24 <= end:
    name, size, reserved = struct.unpack_from('<16sII', blob, at); at += 24
    if name == bytes(16):
        assert not size and not reserved and at == end
        break
    name = name.rstrip(b'\0').decode()
    assert name not in entries and not reserved and at + size <= end
    entries[name] = blob[at:at + size]; at += (size + 15) & ~15
assert set(entries) == {'loader.efi', 'kernel', 'cmdline', 'checkpoint', 'ramdisk'}
native_cmdline = ('-v cpus=8 small_cores_after=0 munch_touch=1 adreno=22 adreno_speed=1 '
                  'adreno_pause=0 adreno_watch=20 adreno_return=1 loader_return_after=120')
ios_gpu_cmdline = 'cpus=8 small_cores_after=0 munch_touch=1 gpu=1 adreno_speed=1 loader_return_after=600'
ios_gpu_fast_cmdline = ios_gpu_cmdline.replace('adreno_speed=1', 'adreno_speed=5')
allowed_gpu = {
    f'cpus=8 small_cores_after=0 munch_touch=1 gpu=1 adreno_speed={speed} '
    + ('fb_hz=120 ' if hz == 120 else '') + ('ca_vbl=1 glwait=1 ' if sync else '')
    + ('munch_te=1 ' if te else '') + ('fb_scan=1 ' if scan else '')
    + ('fb_scan_hud=1 ' if hud else '') + f'loader_return_after={seconds}'
    for speed in (1, 5) for hz in (60, 120) for sync in (False, True) for seconds in (0, 600)
    for te in (False, True) for scan in (False, True) for hud in (False, True)
    if (not scan or (not te and hz == 60)) and (not hud or scan)
}
allowed_gpu |= {'-v ' + c for c in allowed_gpu}
allowed_gpu |= {c.replace('glwait=1 ', '').replace('loader_return_after=',
                'fb_threads=6 fb_filter=0 loader_return_after=')
                for c in list(allowed_gpu) if 'fb_scan=1 ' in c}
allowed_gpu |= {c.replace('loader_return_after=', 'cpufreq=1 ddr=0 loader_return_after=')
                for c in list(allowed_gpu) if 'fb_scan=1 ' in c}
allowed_gpu |= {c.replace('loader_return_after=',
                f'munch_wifi_step={step} munch_wifi_return=1 loader_return_after=')
                for c in list(allowed_gpu) for step in (1, 2, 3, 4)}
allowed_gpu |= {c.replace('munch_wifi_step=4 munch_wifi_return=1 ',
                'munch_wifi_step=4 munch_wifi_return=1 munch_wifi_memory=1 ')
                for c in list(allowed_gpu) if 'munch_wifi_step=4 munch_wifi_return=1 ' in c}
assert report['cmdline'] in allowed_gpu | {'-v cpus=1 loader_return_after=600', 'cpus=8 small_cores_after=0 loader_return_after=600', native_cmdline}
assert entries['cmdline'] == report['cmdline'].encode() + b'\0'
assert entries['checkpoint'] == b'58\0'
for name, key in [('loader.efi', 'loader_sha256'), ('kernel', 'kernel_sha256'), ('ramdisk', 'ramroot_sha256')]:
    assert sha(entries[name]) == report[key]
assert entries['loader.efi'][:2] == b'MZ' and entries['ramdisk'][32:36] == b'NXSB'
if report.get('native_gpu') is not None:
    native = report['native_gpu']
    if native['profile'] == 'munch-a650-ios-gpu':
        assert native.get('gpu_speed_index', 1) in (1, 5)
        speed = native.get('gpu_speed_index', 1)
        hz = native.get('compositor_hz', 60)
        seconds = native.get('return_after_seconds', 600)
        sync = native.get('sync_frame_clock', False)
        assert hz in (60, 120) and seconds in (0, 600) and type(sync) is bool
        te = native.get('display_te', False)
        assert type(te) is bool
        scan = native.get('display_scan', False)
        assert type(scan) is bool and (not scan or (not te and hz == 60))
        hud = native.get('display_scan_hud', False)
        assert type(hud) is bool and (not hud or scan)
        expected = (f'cpus=8 small_cores_after=0 munch_touch=1 gpu=1 adreno_speed={speed} '
                    + ('fb_hz=120 ' if hz == 120 else '') + ('ca_vbl=1 glwait=1 ' if sync else '')
                    + ('munch_te=1 ' if te else '') + ('fb_scan=1 ' if scan else '')
                    + ('fb_scan_hud=1 ' if hud else '') + f'loader_return_after={seconds}')
        compositor = native.get('compositor_profile', 'baseline')
        assert compositor in ('baseline', 'umi-fast')
        if compositor == 'umi-fast':
            assert scan
            expected = expected.replace('glwait=1 ', '').replace('loader_return_after=',
                        'fb_threads=6 fb_filter=0 loader_return_after=')
        cpu_profile = native.get('cpu_frequency_profile', 'firmware')
        assert cpu_profile in ('firmware', 'stock-max')
        if cpu_profile == 'stock-max':
            expected = expected.replace('loader_return_after=', 'cpufreq=1 ddr=0 loader_return_after=')
            def prop(name, kind):
                return subprocess.check_output(['fdtget', '-t', kind, str(a.dtb),
                    '/soc/qcom,cpufreq-hw', name], text=True).split()
            assert prop('compatible', 's') == ['qcom,cpufreq-hw-epss']
            assert prop('reg-names', 's') == ['freq-domain0', 'freq-domain1', 'freq-domain2']
            assert [int(x,16) for x in prop('reg', 'x')] == [0x18591000,0x1000,0x18592000,0x1000,0x18593000,0x1000]
            assert [int(x,16) for x in prop('qcom,lut-row-size', 'x')] == [4]
        wifi_probe = native.get('wifi_probe', 'disabled')
        assert wifi_probe in ('disabled', 'survey-return', 'power-enumeration-return')
        if wifi_probe in ('survey-return', 'power-enumeration-return'):
            wifi_step = native.get('wifi_survey_step', 1)
            assert type(wifi_step) is int and wifi_step in ((3, 4) if wifi_probe == 'power-enumeration-return' else (1, 2))
            if wifi_step == 4:
                assert b'SID/SMMU observation; DMA NOT APPROVED' in entries['kernel']
            if wifi_step >= 2:
                for node, prop, values in (
                    ('/reserved-memory/reserved-memory@80860000', 'reg', [0, 0x80860000, 0, 0x20000]),
                    ('/soc/rsc@18200000', 'reg', [0x18200000, 0x10000, 0x18210000, 0x10000, 0x18220000, 0x10000]),
                    ('/soc/rsc@18200000', 'qcom,drv-id', [2]),
                    ('/soc/rsc@18200000', 'qcom,tcs-offset', [0xd00]),
                    ('/soc/rsc@18200000', 'qcom,tcs-config', [2, 2, 0, 3, 1, 3, 3, 1]),
                ):
                    raw = subprocess.check_output(['fdtget', '-t', 'x', str(a.dtb), node, prop], text=True)
                    assert [int(v, 16) for v in raw.split()] == values
                assert b'MUNCH Wi-Fi: CMD DB version=' in entries['kernel']
            expected = expected.replace('loader_return_after=',
                f'munch_wifi_step={wifi_step} munch_wifi_return=1 loader_return_after=')
        if native.get('wifi_memory_preflight', False):
            assert native['wifi_memory_preflight'] is True and wifi_step == 4
            expected = expected.replace('loader_return_after=', 'munch_wifi_memory=1 loader_return_after=')
        verbose = native.get('verbose_console', False)
        assert type(verbose) is bool
        if verbose: expected = '-v ' + expected
        assert report['verbose_console'] == verbose
        assert report['cmdline'] == expected and native['revision'] >= 4
        info = native['graphics_report']
        graphics_bytes = Path(info['path']).read_bytes()
        assert sha(graphics_bytes) == info['sha256']
        graphics = json.loads(graphics_bytes)
        assert graphics['driver'] == 'freedreno' and graphics['service'] == 'HakiosGPU'
        assert graphics['mesa_version'] == '25.0.7'
        assert sha(entries['ramdisk']) == graphics['helper']['sha256']
        assert entries['ramdisk'] == Path(graphics['helper']['path']).read_bytes()
    else:
        assert native['profile'] == 'munch-a650-diagnostic-22' and report['cmdline'] == native_cmdline
    assert entries['kernel'] == Path(native['kernel']['path']).read_bytes()
    assert sha(entries['kernel']) == native['kernel']['sha256']
    assert len(entries['kernel']) == native['kernel']['size']
    assert b'XNULDRST' in entries['kernel']
    if native.get('revision', 1) >= 3:
        assert native['loader_scm_reservation_bytes'] == 65536
        assert b'munch-scm-metadata' in entries['loader.efi']
        assert b'munch-scm-metadata' in entries['kernel']
        assert b'MUNCH SCM reserved page' in entries['loader.efi']
    if native.get('revision', 1) >= 4:
        assert native['scm_resume_timeout_ns'] == 5000000000
        assert native['scm_resume_max_calls'] == 1048576
        assert b'LOCAL SCM RESUME TIMEOUT' in entries['kernel']
        assert b'SCM INTERRUPTED count' in entries['kernel']
    source = Path(native['source_report']['path']).read_bytes()
    assert sha(source) == native['source_report']['sha256']
    if native.get('wifi_screen_summary', False):
        assert b'MUNCH WIFI / COMPACT SCREEN v1 / NO DMA' in entries['loader.efi']
        assert report['wifi_screen_summary_sha256'] == sha((root / 'devices/poco-f4/loader/wifi-screen-summary.inc').read_bytes())
    if native.get('wifi_usb_log', False):
        assert b'MUNCH USB LOG READY: read-only fastboot' in entries['loader.efi']
        assert b'read-only log export; command disabled' in entries['loader.efi']
        assert report['wifi_usb_log_sha256'] == sha((root / 'devices/poco-f4/loader/wifi-usb-log.inc').read_bytes())
    provenance = json.loads(source)
    if native.get('compositor_profile') == 'umi-fast':
        assert provenance['surface_cache_sources']
    assert provenance['xnu_base'] == 'c54f35ca767986246321eb901baf8f5ff7923f6a'
    if native.get('wifi_probe') in ('survey-return', 'power-enumeration-return'):
        assert provenance.get('wifi_diagnostic_only') or provenance.get('wifi_survey_only')
        wifi = root / 'devices/poco-f4/wifi'
        assert provenance['wifi_survey_sha256'] == sha((wifi / 'munch-wifi-survey.inc').read_bytes())
        base = (root / 'xnu/files/bsd/dev/hakios.wifi.c').read_bytes()
        needle = b'void\nhakios_wifi_init(void)\n{\n'
        staged = base.replace(needle, b'#include "munch-wifi-survey.inc"\n#include "munch-wifi-enumeration.inc"\n\n' + needle +
                              b'\tif (munch_wifi_init()) return;\n', 1)
        assert provenance['wifi_source_sha256'] == sha(staged)
        if native.get('wifi_survey_step', 0) >= 3:
            assert sha(a.dtb.read_bytes()) == 'da0ef458ed5b563b7c0e444aeb923e2360f82b345673153c9996bd20c1c9603c'
            assert b'MUNCH Wi-Fi: step 3 power/PCIe; no DMA, SID, BAR or MHI' in entries['kernel']
            for name in ('munch-wifi-enumeration.inc', 'munch-wifi-dma-survey.inc', 'munch-wifi-memory.inc', 'munch-wifi-memory-ranges.h', 'munch-wifi-pcie.inc',
                         'munch-wifi-pcie.h', 'munch-wifi-phy.h', 'munch-wifi-rpmh.inc'):
                assert provenance['wifi_diagnostic_sources'][name] == sha((wifi / name).read_bytes())
        if native.get('wifi_memory_preflight', False):
            assert native['wifi_survey_step'] == 4
            assert 'munch_wifi_memory=1' in report['cmdline']
            assert b'MUNCH Wi-Fi: memory preflight %s;' in entries['kernel']
        assert b'MUNCH Wi-Fi: survey %s; returning to loader' in entries['kernel']
    if native.get('display_te'):
        assert provenance['display_te_staging'] is True
        assert b'MUNCH TE' in entries['kernel']
    if native.get('display_scan'):
        assert provenance['display_command_staging'] is True
        assert b'munch command TE' in entries['kernel']
        display = root / 'devices/poco-f4/display'
        assert provenance['display_scan_source_sha256'] == sha((display / 'munch-command-scan.inc').read_bytes())
        assert provenance['display_command_patch_sha256'] == sha((display / 'munch-command-staging.patch').read_bytes())
    if native.get('display_scan_hud'):
        assert provenance['display_status_hud'] is True
    if native.get('display_copy_recovery'):
        assert provenance['display_copy_remeasurement'] is True
        assert b'identical-front copy remeasured' in entries['kernel']
        if not native.get('display_fps_only'):
            assert b'BEAT %u S%u AGE %u MS' in entries['kernel']
    if native.get('display_fps_counters'):
        assert provenance['display_presented_fps'] is True
        assert provenance['display_recent_copy_peak'] is True
        if native.get('display_fps_only'):
            assert provenance['display_hud_fps_only'] is True
            assert b'FPS %u' in entries['kernel']
            assert b'BEAT %u S%u AGE %u MS' not in entries['kernel']
            assert b'FPS %u HZ %u.%02u' not in entries['kernel']
        else:
            assert b'FPS %u HZ %u.%02u' in entries['kernel']
    if native.get('surface_cache_fix'):
        assert len(provenance['surface_cache_sources']) == 2
        assert provenance['surface_cache_patch_sha256'] == sha((root / 'devices/poco-f4/gpu/umi-surface-cache.patch').read_bytes())
    assert provenance['native_touch_bootarg'] == 'munch_touch=1'
    print('PASS: native A650 boot header, size, FD, DTB, payload, helper APFS, full kernel hash and source manifest')
    raise SystemExit(0)
baseline = (root / 'prebuilt/mach.development.bcm2837').read_bytes()
assert sha(baseline) == report['kernel_baseline_sha256']
kernel = entries['kernel']; assert len(kernel) == len(baseline)
segments, at = [], 32
for _ in range(struct.unpack_from('<I', baseline, 16)[0]):
    cmd, size = struct.unpack_from('<II', baseline, at)
    if cmd == 0x19:
        va, _, off, n = struct.unpack_from('<QQQQ', baseline, at + 24)
        segments.append((va, off, n))
    at += size
def file_at(va, n):
    for base, off, size in segments:
        if base <= va and va + n <= base + size: return off + va - base
    raise ValueError('Unmapped VA')
touch = report['touch_prototype']
start, limit = int(touch['entry'], 16), int(touch['limit'], 16)
n = touch['code_bytes']; assert 0 < n <= limit - start
at = file_at(start, n)
assert sha(kernel[at:at + n]) == touch['code_sha256']
restored = bytearray(kernel); restored[at:at + n] = baseline[at:at + n]
at = file_at(0xfffffff007456160, 4)
assert kernel[at:at + 4] == struct.pack('<I', 0x52800000)
restored[at:at + 4] = baseline[at:at + 4]
assert bytes(restored) == baseline
print('PASS: boot header, size, FD, DTB, payload, APFS helper, hashes, bounded touch overlay and sole early-query patch')
