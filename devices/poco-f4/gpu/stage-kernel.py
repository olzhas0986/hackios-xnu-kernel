#!/usr/bin/env python3
"""Stage the UMI A650 adaptation and native munch touch in a prepared XNU tree."""
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('tree', type=Path)
display_mode = p.add_mutually_exclusive_group()
display_mode.add_argument('--te-staging', action='store_true', help='Stage the opt-in passive-TE framebuffer experiment')
display_mode.add_argument('--command-staging', action='store_true', help='Stage UMI-derived command DMA synchronization (fb_scan=1)')
p.add_argument('--touch-poll-ms', type=int, choices=(4, 16), default=16, help='Delay between native touch reads; 4 ms is experimental')
p.add_argument('--report', required=True, type=Path)
a = p.parse_args()
here = Path(__file__).resolve().parent
root = here.parents[2]
sha = lambda b: hashlib.sha256(b).hexdigest()
original = (root / 'xnu/files/bsd/dev/hakios.adreno.c').read_bytes()
if sha(original) != '6dfa464cf9be005f6520257721ab468fc7c33189be11d57078d36dc5faa33e77':
    raise ValueError('Upstream GPU source differs from the reviewed port base')
base = subprocess.check_output(['git', '-C', str(a.tree), 'rev-parse', 'HEAD'], text=True).strip()
if base != 'c54f35ca767986246321eb901baf8f5ff7923f6a':
    raise ValueError('Expected prepared XNU 8796.121.2')
with tempfile.TemporaryDirectory(prefix='munch-native-stage-') as directory:
    work = Path(directory)
    path = work / 'bsd/dev/hakios.adreno.c'
    path.parent.mkdir(parents=True)
    path.write_bytes(original)
    subprocess.run(['patch', '--batch', '--forward', '-p1', '-i', str(here / 'umi-a650.patch')], cwd=work, check=True)
    gpu = path.read_bytes()
existing = a.tree / 'bsd/dev/hakios.adreno.c'
if existing.read_bytes() not in (original, gpu):
    raise ValueError('Refusing to overwrite independently modified GPU source')
# The C invalidation routine needs the matching IOSurface writer tracking and
# fence-stability checks in the IOKit consumer, not just the Adreno C driver.
cache_paths = ('iokit/Kernel/hakios.gpu.cpp', 'iokit/Kernel/MiniIOSurface.h')
cache_hashes = {}
with tempfile.TemporaryDirectory(prefix='munch-surface-cache-') as directory:
    work = Path(directory)
    for name in cache_paths:
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((root / 'xnu/files' / name).read_bytes())
    subprocess.run(['git', 'apply', str(here / 'umi-surface-cache.patch')], cwd=work, check=True)
    for name in cache_paths:
        target = a.tree / name
        data = (work / name).read_bytes()
        if target.read_bytes() not in ((root / 'xnu/files' / name).read_bytes(), data):
            raise ValueError('Refusing to overwrite independent surface cache changes: ' + name)
        cache_hashes[name] = sha(data)
    for name in cache_paths:
        (a.tree / name).write_bytes((work / name).read_bytes())
touch = (here.parent / 'touch/xnu-ft3658u.c').read_text()
touch = touch.replace('extern struct state munch_state;', 'static struct state munch_state;')
touch = touch.replace('__attribute__((section(".text.entry")))', '')
touch = touch.replace('void munch_touch_init(void)', 'void hakios_goodix_init(void)')
needle = '    void *thread = 0;'
if touch.count(needle) != 1:
    raise ValueError('Unexpected touch entry')
touch = touch.replace(needle, '    extern int PE_parse_boot_argn(const char *, void *, int);\n'
                      '    unsigned enabled = 0;\n'
                      '    if (!PE_parse_boot_argn("munch_touch", &enabled, sizeof(enabled)) || !enabled) return;\n' + needle, 1)
if touch.count('IOSleep(16);') != 1:
    raise ValueError('Unexpected touch polling loop')
touch = touch.replace('IOSleep(16);', f'IOSleep({a.touch_poll_ms});')
touch = '/* Native munch build: enabled only with munch_touch=1 and verified loader power handoff. */\n' + touch
early_path = a.tree / 'osfmk/arm64/loader_return.c'
early = early_path.read_text()
old_query = 'if (PE_parse_boot_argn("bootcpu", &own_core, sizeof(own_core))) {'
new_query = 'if (0) { /* munch current-core handoff: PE is not initialized here. */'
if early.count(old_query) == 1:
    early = early.replace(old_query, new_query, 1)
elif early.count(new_query) != 1:
    raise ValueError('Unexpected early bootcpu query')
early_path.write_text(early)
existing.write_bytes(gpu)
(a.tree / 'bsd/dev/hakios.goodix.c').write_text(touch)
header = (here.parent / 'touch/ft3658u-protocol.h').read_bytes()
(a.tree / 'bsd/dev/ft3658u-protocol.h').write_bytes(header)
fb_base = (root / 'xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp').read_bytes()
if sha(fb_base) != 'c6aa9da0a649ae6adf784a00327a60b7bd21e10549c9094093301951971f6ebe':
    raise ValueError('Unexpected framebuffer port base')
variants = {}
for mode in ('te', 'command'):
    with tempfile.TemporaryDirectory(prefix='munch-display-stage-') as directory:
        work = Path(directory)
        path = work / 'iokit/Kernel/MiniIOMobileFramebuffer.cpp'
        path.parent.mkdir(parents=True)
        path.write_bytes(fb_base)
        subprocess.run(['git', 'apply', '--unidiff-zero', '--verbose', str(here.parent / f'display/munch-{mode}-staging.patch')], cwd=work, check=True)
        variants[mode] = path.read_bytes()
fb_target = a.tree / 'iokit/Kernel/MiniIOMobileFramebuffer.cpp'
if (fb_target.read_bytes() not in (fb_base, *variants.values()) and
        sha(fb_target.read_bytes()) not in ('e9b8496eba392eaaeba2ab6b2cab38d3f29f50b877d363b1dc306360a9929142',
                                        '77554128ef40b10ad7be22c8fd4eba8197685faf9963431d467deb2dde3a4577',
                                        '17db9b4a603022f2e9138b3bc002e75c31a30c8d974ef320a4f4b2926de0e768')):
    raise ValueError('Refusing to overwrite independent framebuffer changes')
fb_target.write_bytes(variants['command'] if a.command_staging else variants['te'] if a.te_staging else fb_base)
(a.tree / 'iokit/Kernel/munch-te-frame.h').write_bytes((here.parent / 'display/te-frame.h').read_bytes())
scan_source = (here.parent / 'display/munch-command-scan.inc').read_bytes()
(a.tree / 'iokit/Kernel/munch-command-scan.inc').write_bytes(scan_source)
report = {'xnu_base': base, 'umi_reference': '6b6fb2b0c5a1720496ec71e88c95d2a45a0046a4',
          'early_return_source_sha256': sha(early.encode()), 'gpu_source_sha256': sha(gpu), 'touch_source_sha256': sha(touch.encode()),
          'touch_header_sha256': sha(header), 'gpu_patch_sha256': sha((here / 'umi-a650.patch').read_bytes()),
          'native_touch_bootarg': 'munch_touch=1', 'touch_poll_sleep_ms': a.touch_poll_ms, 'display_te_staging': a.te_staging,
          'display_command_staging': a.command_staging,
          'display_status_hud': a.command_staging,
          'display_copy_remeasurement': a.command_staging,
          'display_recent_copy_peak': a.command_staging,
          'display_presented_fps': a.command_staging,
          'display_hud_fps_only': a.command_staging,
          'surface_cache_sources': cache_hashes,
          'surface_cache_patch_sha256': sha((here / 'umi-surface-cache.patch').read_bytes()),
          'display_scan_source_sha256': sha(scan_source),
          'display_command_patch_sha256': sha((here.parent / 'display/munch-command-staging.patch').read_bytes()),
          'display_source_sha256': sha(fb_target.read_bytes()),
          'display_header_sha256': sha((here.parent / 'display/te-frame.h').read_bytes()), 'hardware_tested': False}
a.report.parent.mkdir(parents=True, exist_ok=True)
a.report.write_text(json.dumps(report, indent=2) + '\n')
print('Staged A650 GPU and native munch touch; base prebuilt and installed APFS unchanged')
