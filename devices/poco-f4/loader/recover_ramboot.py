#!/usr/bin/env python3
"""Recover build inputs from the one inspected, user-supplied F4 software-rendered image.

Offsets below are specific to its SHA-pinned EFI loader, not a generic firmware parser.
Generated identity/helper files stay local; no Apple-derived bytes are in this script.
"""
import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path

REFERENCE_SHA = '86c028bfa606e296e4e3e0e35121f88e2032009aba30d95a94a2f6b182b939eb'
LOADER_SHA = 'ecbe2cfd807bdaf3a71bacacf664a832cf6d4a3ef661e37f30cb1a9dcc66354c'
FD_SHA = '57c7437160ba819ca219ccc7245cafc4aee7a462f150650595ae8a9f06ee14db'
sha = lambda b: hashlib.sha256(b).hexdigest()


def recover(image):
    b = Path(image).read_bytes()
    if sha(b) != REFERENCE_SHA:
        raise ValueError('Recovery supports only the inspected munch-ios165-ramboot.img SHA256')
    assert b[:8] == b'ANDROID!' and struct.unpack_from('<I', b, 40)[0] == 1
    size, = struct.unpack_from('<I', b, 8)
    page, = struct.unpack_from('<I', b, 36)
    z = zlib.decompressobj(31)
    blob = z.decompress(b[page:page + size]) + z.flush()
    assert z.eof and z.unused_data[:4] == b'\xd0\x0d\xfe\xed'
    shim = 208
    fd = blob[shim:shim + 0x300000]
    assert sha(fd) == FD_SHA
    at = shim + len(fd)
    magic, length = struct.unpack_from('<II', blob, at)
    assert magic == 0x50554e58
    end = at + 8 + length
    at += 8
    entries = {}
    while at + 24 <= end:
        name, size, reserved = struct.unpack_from('<16sII', blob, at)
        at += 24
        if name == bytes(16):
            assert not size and not reserved and at == end
            break
        name = name.rstrip(b'\0').decode('ascii')
        assert name in ('loader.efi', 'kernel', 'cmdline', 'checkpoint', 'ramdisk')
        assert name not in entries and not reserved and at + size <= end
        entries[name] = blob[at:at + size]
        at += (size + 15) & ~15
    assert set(entries) == {'loader.efi', 'kernel', 'cmdline', 'checkpoint', 'ramdisk'}
    assert entries['checkpoint'] == b'58\0'
    loader = entries['loader.efi']
    assert sha(loader) == LOADER_SHA
    assert entries['ramdisk'][32:36] == b'NXSB'
    # PE32+ section mappings, so name pointers in identity_prop can be checked.
    pe, = struct.unpack_from('<I', loader, 0x3c)
    count, = struct.unpack_from('<H', loader, pe + 6)
    optlen, = struct.unpack_from('<H', loader, pe + 20)
    opt = pe + 24
    base, = struct.unpack_from('<Q', loader, opt + 24)
    sections = []
    for i in range(count):
        at = opt + optlen + 40 * i
        _, va, size, off = struct.unpack_from('<IIII', loader, at + 8)
        sections.append((base + va, off, size))

    def file_at(va, length):
        for start, off, size in sections:
            if start <= va and va + length <= start + size:
                return off + va - start
        raise ValueError('Identity pointer outside a file-backed PE section')

    def name_at(va):
        at = file_at(va, 1)
        name = loader[at:at + 128].split(b'\0', 1)[0]
        assert name and len(name) < 128 and all(32 <= c < 127 for c in name)
        return name.decode('ascii')

    header = ['/* Recovered from the SHA-pinned local F4 reference loader. */',
              'struct identity_prop { const char *name; unsigned offset, length; };']
    # Table/data addresses and counts from the actual ARM64 call sites at
    # 0xa1003ee4 (root), 0xa10043fc (product), 0xa1004428 (haptics),
    # and 0xa10074f8 (camera). Include zero-length properties in each count.
    specs = [('root', 0xa10091b0, 6, 0xa1009160),
             ('product', 0xa10093e0, 61, 0xa1009260),
             ('haptics', 0xa10097b8, 2, 0xa10097b0),
             ('camera', 0xa1009890, 43, 0xa10097e0)]
    groups = {}
    for label, table, count, data in specs:
        rows, extent = [], 0
        for i in range(count):
            ptr, offset, length = struct.unpack_from('<QII', loader, file_at(table + i * 16, 16))
            assert offset == extent and length < 4096
            extent += length
            rows.append((name_at(ptr), offset, length))
        start = file_at(data, extent)
        values = loader[start:start + extent]
        groups[label] = {name: values[o:o+n] for name, o, n in rows}
        header.append(f'static const struct identity_prop identity_{label}[] = {{')
        header.extend(f'    {{ {json.dumps(name)}, {o}, {n} }},' for name, o, n in rows)
        header.append('};')
        header.append(f'static const unsigned char identity_{label}_data[] = {{')
        header.extend('    ' + ', '.join(f'0x{c:02x}' for c in values[i:i+20]) + ','
                      for i in range(0, extent, 20))
        header.append('};')
    assert groups['root']['model'] == b'iPhone10,3\0'
    assert groups['product']['product-name'] == b'iPhone X\0'
    assert groups['product']['artwork-scale-factor'] == struct.pack('<I', 3)
    # The loader's system-length and CRC32 instructions; preserve these exact
    # installed-system checks when rebuilding, without needing its 2.5 GB file.
    for va, word in [(0xa1002d50, 0x529e001a), (0xa1002d54, 0x72b273fa),
                     (0xa1002ed8, 0x5287a728), (0xa1002edc, 0x72b03d88)]:
        assert struct.unpack_from('<I', loader, file_at(va, 4))[0] == word
    return dict(fd=fd, dtb=z.unused_data, helper=entries['ramdisk'],
                identity=('\n'.join(header) + '\n').encode(),
                system={'size': 0x939ff000, 'crc32': '81ec3d39'},
                reference_sha256=REFERENCE_SHA, loader_sha256=LOADER_SHA)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    a = parser.parse_args()
    recovered = recover(a.image)
    a.output.mkdir(parents=True, exist_ok=False)
    files = {}
    for key, name in [('fd', 'SILICIUM_UEFI.fd'), ('dtb', 'live.dtb'),
                      ('helper', 'root.apfs'), ('identity', 'identity.h')]:
        path = (a.output / name).resolve()
        path.write_bytes(recovered[key])
        files[key] = dict(path=str(path), size=path.stat().st_size, sha256=sha(recovered[key]))
    fd = Path(files['fd']['path'])
    fd.with_suffix('.fd.munch.json').write_text(json.dumps({
        'target': 'munch', 'fd_sha256': FD_SHA, 'recovered_from_reference': REFERENCE_SHA,
    }, indent=2) + '\n')
    config = {'version': '16.5', 'build': '20F66',
              'reference_ramboot': {'path': str(a.image.resolve()), 'sha256': REFERENCE_SHA},
              'system': recovered['system'], 'root': files['helper'], 'identity': files['identity']}
    (a.output / 'ios.json').write_text(json.dumps(config, indent=2) + '\n')
    (a.output / 'recovery.json').write_text(json.dumps({
        'reference_sha256': REFERENCE_SHA, 'loader_sha256': LOADER_SHA,
        'system': recovered['system'], 'files': files,
        'device_tested': False, 'system_partition_read_by_host': False,
    }, indent=2) + '\n')
    print('Recovered FD, DTB, helper, identity and installed-system checks:', a.output)


if __name__ == '__main__':
    main()
