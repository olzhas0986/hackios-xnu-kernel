#!/usr/bin/env python3
"""Compare captured munch and UMI CNSS/PCIe identities without accessing MMIO."""
import argparse
import json
import subprocess
import struct
from pathlib import Path
nodes = ('/soc/qcom,cnss-qca6390@b0000000', '/soc/qcom,pcie@1c00000')
props = ('compatible', 'reg', 'reg-names', 'wlan-en-gpio', 'qcom,wlan-rc-num',
         'pinctrl-names', 'qcom,cmd_db_name', 'qcom,pcie-phy-ver', 'perst-gpio', 'wake-gpio', 'clkreq-gpio',
         'vdd-wlan-aon-supply', 'qcom,vdd-wlan-aon-config', 'vdd-wlan-dig-supply',
         'qcom,vdd-wlan-dig-config', 'vdd-wlan-io-supply', 'qcom,vdd-wlan-io-config',
         'vdd-wlan-rfa1-supply', 'qcom,vdd-wlan-rfa1-config',
         'vdd-wlan-rfa2-supply', 'qcom,vdd-wlan-rfa2-config',
         'vdd-wlan-3antenna-supply', 'qcom,vdd-wlan-3antenna-config',
         'qcom,vreg-1p8-voltage-level', 'qcom,vreg-0p9-voltage-level',
         'qcom,vreg-cx-voltage-level')

def dt_nodes(path):
    b = path.read_bytes()
    if len(b) < 40:
        raise ValueError('Truncated FDT header')
    magic,total,st,ss,_,version,_,_,strings_size,structure_size = struct.unpack_from('>10I',b)
    if magic != 0xd00dfeed or version < 17 or total > len(b) or st+structure_size > total or ss+strings_size > total:
        raise ValueError('Invalid FDT ranges')
    pos, limit, stack, nodes = st, st+structure_size, [], {}
    while pos+4 <= limit:
        token, = struct.unpack_from('>I',b,pos); pos += 4
        if token == 1:
            end = b.index(0,pos,limit)
            stack.append(b[pos:end].decode('ascii')); pos=(end+4)&~3
            nodes['/'+'/'.join(stack[1:])] = {}
        elif token == 2:
            if not stack: raise ValueError('Unbalanced FDT node')
            stack.pop()
        elif token == 3:
            if not stack or pos+8 > limit: raise ValueError('Invalid FDT property')
            n,off=struct.unpack_from('>II',b,pos); pos+=8
            if off>=strings_size or pos+n>limit: raise ValueError('Invalid FDT property range')
            end=b.index(0,ss+off,ss+strings_size)
            nodes['/'+'/'.join(stack[1:])][b[ss+off:end].decode('ascii')]=b[pos:pos+n]
            pos=(pos+n+3)&~3
        elif token == 4:
            pass
        elif token == 9:
            if stack: raise ValueError('Unclosed FDT node')
            return nodes
        else:
            raise ValueError('Unexpected FDT token')
    raise ValueError('No FDT terminator')

def read(path):
    result = {}
    tree = dt_nodes(path)
    handles = {int.from_bytes(p['phandle'],'big'):n for n,p in tree.items() if 'phandle' in p}
    for node in nodes:
        names = subprocess.check_output(['fdtget', '-p', str(path), node], text=True).split()
        row = {}
        for prop in props:
            if prop not in names:
                continue
            kind = 's' if prop in ('compatible','reg-names','pinctrl-names','qcom,cmd_db_name') else 'x'
            value = subprocess.check_output(['fdtget', '-t', kind, str(path), node, prop], text=True).split()
            # GPIO controller and regulator phandles are local to each tree.
            # Record them, but do not interpret equal/different phandle numbers
            # as equal/different electrical wiring.
            row[prop] = value if kind == 's' else [int(x,16) for x in value]
        supplies = {}
        for prop, raw in tree[node].items():
            if not prop.endswith('-supply'): continue
            target = handles[int.from_bytes(raw,'big')]
            parent = target.rsplit('/',1)[0]
            resource = tree[parent].get('qcom,resource-name',b'').rstrip(b'\0').decode('ascii')
            supplies[prop] = {'node':target, 'resource':resource,
                'regulator_name':tree[target].get('regulator-name',b'').rstrip(b'\0').decode('ascii')}
        for supply in supplies.values():
            target = tree[supply['node']]
            supply['constraints'] = {
                k: int.from_bytes(target[k], 'big') for k in
                ('regulator-min-microvolt', 'regulator-max-microvolt',
                 'qcom,init-voltage', 'qcom,init-mode') if k in target
            }
        row['resolved_supplies'] = supplies
        result[node] = row
    rsc = tree['/soc/rsc@18200000']
    result['apps_rsc'] = {
        k: list(struct.unpack('>' + 'I' * (len(rsc[k]) // 4), rsc[k]))
        for k in ('reg', 'qcom,drv-id', 'qcom,tcs-offset', 'qcom,tcs-config')
    }
    result['command_db'] = [
        {'node': n, 'reg': list(struct.unpack('>' + 'I' * (len(p['reg']) // 4), p['reg']))}
        for n, p in tree.items() if p.get('compatible') == b'qcom,cmd-db\0'
    ]
    return result

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('munch', type=Path)
    p.add_argument('--umi', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    munch, umi = read(a.munch), read(a.umi)
    cnss = munch[nodes[0]]
    if cnss['compatible'] != ['qcom,cnss-qca6390']:
        raise ValueError('Expected the captured QCA6390 CNSS transport')
    report = {'device':'munch','hardware_tested':False,'munch':munch,'umi':umi,
              'notes':['Regulator/GPIO phandles must be resolved in their own trees.',
                       'Matching controller addresses do not validate power, DMA or board firmware.',
                       'UMI hard-coded firmware IOVAs and board file must not be copied blindly.']}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report,indent=2)+'\n')
    print('Recorded CNSS/PCIe prerequisites:', a.output)

if __name__ == "__main__":
    main()
