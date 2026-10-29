#!/usr/bin/env python3
"""Audit captured PCIe/SMMU identities and memory reservations; approve no DMA mappings."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('wifi_profile', here / 'inspect-profile.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('dtb', type=Path)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
tree = profile.dt_nodes(a.dtb)
handles = {int.from_bytes(v['phandle'], 'big'): k for k, v in tree.items() if 'phandle' in v}

def cells(raw):
    if len(raw) % 4:
        raise ValueError('Invalid cell array')
    return list(struct.unpack('>' + 'I' * (len(raw)//4), raw))

def ranges(node):
    parent = node.rsplit('/', 1)[0] or '/'
    count = lambda key, default: int.from_bytes(tree[parent].get(key, default.to_bytes(4, 'big')), 'big')
    ac, sc = count('#address-cells', 2), count('#size-cells', 1)
    values = cells(tree[node].get('reg', b''))
    if ac not in (1, 2) or sc not in (1, 2) or len(values) % (ac+sc):
        raise ValueError('Unsupported reg geometry: ' + node)
    result = []
    for i in range(0, len(values), ac+sc):
        addr = int.from_bytes(struct.pack('>'+'I'*ac, *values[i:i+ac]), 'big')
        size = int.from_bytes(struct.pack('>'+'I'*sc, *values[i+ac:i+ac+sc]), 'big')
        result.append({'address': hex(addr), 'size': hex(size)})
    return result

pcie = tree['/soc/qcom,pcie@1c00000']
raw = cells(pcie['iommu-map'])
if len(raw) != 8:
    raise ValueError('Unexpected PCIe IOMMU map')
entries = [{'rid_base':raw[i], 'smmu_node':handles[raw[i+1]], 'sid_base':raw[i+2], 'length':raw[i+3]}
           for i in range(0, len(raw), 4)]
assert [(e['rid_base'],e['sid_base'],e['length']) for e in entries] == [(0,0x1c00,1),(0x100,0x1c01,1)]
assert entries[0]['smmu_node'] == entries[1]['smmu_node']
smmu_node = entries[1]['smmu_node']; smmu = tree[smmu_node]
assert any(r['address']=='0x15000000' for r in ranges(smmu_node))
reserved = [{'node':n, 'reg':ranges(n), 'no_map':'no-map' in v,
             'compatible':v.get('compatible',b'').rstrip(b'\0').decode('ascii')}
            for n,v in tree.items() if n.startswith('/reserved-memory/') and 'reg' in v]
cnss = tree['/soc/qcom,cnss-qca6390@b0000000']
report = {'dtb_sha256':hashlib.sha256(a.dtb.read_bytes()).hexdigest(), 'iommu_map':entries,
          'smmu_sid_base':cells(pcie['qcom,smmu-sid-base']), 'smmu_reg':ranges(smmu_node),
          'smmu_compatible':smmu['compatible'].rstrip(b'\0').decode('ascii'),
          'smmu_iommu_cells':cells(smmu['#iommu-cells']),
          'smmu_flags':[k for k in smmu if k.startswith('qcom,') and not smmu[k]],
          'cnss_reg_names':cnss['reg-names'].rstrip(b'\0').decode('ascii').split('\0'),
          'cnss_reg':ranges('/soc/qcom,cnss-qca6390@b0000000'),
          'cnss_memory_region':cells(cnss.get('memory-region',b'')),
          'cnss_dynamic_ramdump':cells(cnss.get('qcom,wlan-ramdump-dynamic',b'')),
          'reserved_memory':reserved,
          'memory_nodes':{n:ranges(n) for n,v in tree.items() if v.get('device_type')==b'memory\0'},
          'hardware_tested':False, 'dma_approved':False,
          'notes':['DT maps 01:00.0 RID 0x100 to apps SMMU SID 0x1c01; runtime SID table and SMMU ownership still need reading.',
                   'CNSS smmu_iova_ipa is not approval for firmware memory or a physical RAM allocation.',
                   'No UMI fixed IOVA or reserved physical memory is adopted by this audit.']}
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(report, indent=2)+'\n')
print('Recorded PCIe RID/SID and memory audit:',a.output)
