#!/usr/bin/env python3
"""Stage the four QCA6390 files captured from stock munch, without redistributing them."""
import argparse
import hashlib
import json
from pathlib import Path

FILES = {
    'amss20.bin': (4364432, '739ea31e62a6127bd06fcdd5e62301c9a343d220b8eccf8328a2380d0e82157e'),
    'm3.bin': (266684, '140e8eaa93cb8d7eb8051887f848a69ce82da8c60de7bd81dc78b039b6c837db'),
    'bd_l11rgl.elf': (58000, '15ad28880f1f6461dd1b0bd9d502a2a1af8a5ebe9b005052a6a6cbffb742b5da'),
    'regdb.bin': (19348, '108943027fd252c3104e716f43c7b5aea07b81200e170160f423b5281688d478'),
}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('capture', type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    source = a.capture / 'firmware/vendor/firmware_mnt/image/qca6390'
    data = {}
    # Verify the complete set before creating any output.
    for name, (size, digest) in FILES.items():
        b = (source / name).read_bytes()
        if len(b) != size or hashlib.sha256(b).hexdigest() != digest:
            raise ValueError('Unexpected stock munch firmware: ' + name)
        data[name] = b
    if 'qca6390/bd_l11rgl.elf' not in (a.capture / 'wifi-dmesg.txt').read_text():
        raise ValueError('Capture does not confirm the selected board file')
    target = a.output / 'fw/qca6390'
    target.mkdir(parents=True, exist_ok=False)
    for name, b in data.items():
        (target / name).write_bytes(b)
    report = {'device': 'munch', 'pci': '17cb:1101', 'board_file': 'bd_l11rgl.elf',
              'hardware_tested_in_xnu': False,
              'files': {n: {'size': s, 'sha256': h} for n, (s, h) in FILES.items()}}
    (a.output / 'firmware.json').write_text(json.dumps(report, indent=2) + '\n')
    print(target)

if __name__ == '__main__':
    main()
