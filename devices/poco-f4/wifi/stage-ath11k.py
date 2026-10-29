#!/usr/bin/env python3
"""Prepare an isolated ath11k build with the UMI QCA6390 LinuxKPI adaptation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
ARCHIVES = {
    'linux-7.0.14.tar.xz': 'de9999b784d2293f00d39c62d8f92a08ab8a54bc4e80ffd250a0c09cb07a0f98',
    'freebsd-15.1-src.txz': 'cf5762da53fd52e1eaf0f9ceee9bf58cbe314c821031d0d9ffa76823185a89e1',
}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--umi', type=Path, default=ROOT.parent / 'umi-reference')
    p.add_argument('--output', type=Path, default=ROOT / 'out/munch-wifi/ath11k-build')
    p.add_argument('--downloads', type=Path, default=ROOT / 'out/munch-wifi/downloads')
    a = p.parse_args()
    overlay = a.umi / 'devices/xiaomi-mi-10/overlays/wifi'
    required = ('lx_bus.c', 'lx_base.c', 'lx_80211.c', 'lx_wpa.c', 'lx_wpa.h')
    for name in required:
        if not (overlay / 'lx/src' / name).is_file():
            raise ValueError('Missing UMI QCA6390 overlay: ' + name)
    if a.output.exists():
        raise ValueError('Use a fresh output directory: ' + str(a.output))
    # Check existing downloads before creating a build tree. Missing archives are
    # reported, never fetched implicitly from the restricted build environment.
    available, missing = {}, []
    for name, digest in ARCHIVES.items():
        path = a.downloads / name
        if not path.is_file():
            missing.append(name)
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Source archive checksum mismatch: ' + name)
        available[name] = path.resolve()
    a.output.mkdir(parents=True)
    for name in ('wifi', 'tools'):
        shutil.copytree(ROOT / name, a.output / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(overlay, a.output / 'wifi', dirs_exist_ok=True)
    shutil.copy2(ROOT / 'xnu-env.sh', a.output / 'xnu-env.sh')
    deps = a.output / 'xnu-deps'
    deps.mkdir()
    for name in ('host', 'hostinc', 'sdk'):
        (deps / name).symlink_to(ROOT / 'xnu-deps' / name, target_is_directory=True)
    build = deps / 'build/ath11k'
    build.mkdir(parents=True)
    # UMI's LinuxKPI bus needs four additional transport declarations. Keep
    # these headers private until the corresponding XNU transport is ported.
    headers = a.output / 'kernel-headers/bsd/sys'
    headers.mkdir(parents=True)
    for name in ('hakios.lx.h', 'hakios.qmi.h'):
        shutil.copy2(a.umi / 'devices/xiaomi-mi-10/overlays/xnu/files/bsd/sys' / name,
                     headers / name)
    shutil.copy2(ROOT / 'xnu/files/bsd/sys/hakios.sta.h', headers / 'hakios.sta.h')
    for name, path in available.items():
        (build / name).symlink_to(path)
    hashes = {str(f.relative_to(overlay)): hashlib.sha256(f.read_bytes()).hexdigest()
              for f in sorted(overlay.rglob('*')) if f.is_file()}
    report = {'device': 'munch', 'transport': 'QCA6390 PCIe/MHI',
              'overlay': str(overlay.resolve()), 'overlay_sha256': hashes,
              'missing_archives': missing, 'kernel_link_installed': False,
              'hardware_tested': False}
    (a.output / 'stage-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(a.output)
    if missing:
        print('Download prerequisites: bash devices/poco-f4/wifi/fetch-deps.sh')
        print('Missing: ' + ', '.join(missing))

if __name__ == '__main__':
    main()
