#!/usr/bin/env python3
"""Validate the step-3 constants against the captured munch DT and resolved supplies."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('dtb', type=Path)
p.add_argument('--umi', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
here = Path(__file__).resolve().parent
sha = lambda b: hashlib.sha256(b).hexdigest()
if sha(a.dtb.read_bytes()) != 'da0ef458ed5b563b7c0e444aeb923e2360f82b345673153c9996bd20c1c9603c':
    raise ValueError('Step 3 requires the captured munch merged DT')

def prop(node, name, strings=False):
    raw = subprocess.check_output(['fdtget', '-t', 's' if strings else 'x',
                                   str(a.dtb), node, name], text=True).split()
    return raw if strings else [int(x, 16) for x in raw]

with tempfile.TemporaryDirectory(prefix='munch-power-profile-') as temp:
    profile = Path(temp) / 'profile.json'
    subprocess.run(['python3', str(here / 'inspect-profile.py'), str(a.dtb),
                    '--umi', str(a.umi), '--output', str(profile)], check=True)
    data = json.loads(profile.read_text())['munch']
cnss = data['/soc/qcom,cnss-qca6390@b0000000']
pcie = data['/soc/qcom,pcie@1c00000']
expected = {
    'aon': ('smpa6', [950000, 950000, 0, 0, 1]),
    'dig': ('smpa6', [950000, 950000, 0, 0, 1]),
    'io': ('smpa4', [1800000, 1800000, 0, 0, 1]),
    'rfa1': ('smpa5', [1900000, 1900000, 0, 0, 1]),
    'rfa2': ('smpc8', [1350000, 1350000, 0, 0, 1]),
    '3antenna': ('ldoa16', [3024000, 3304000, 0, 0, 0]),
}
for suffix, (resource, config) in expected.items():
    assert cnss['resolved_supplies'][f'vdd-wlan-{suffix}-supply']['resource'] == resource
    assert cnss[f'qcom,vdd-wlan-{suffix}-config'] == config
assert cnss['vdd-wlan-aon-supply'] == cnss['vdd-wlan-dig-supply']
for suffix, resource, uv in [('1p8', 'ldoa9', 1200000), ('0p9', 'ldoa5', 880000)]:
    supply = pcie['resolved_supplies'][f'vreg-{suffix}-supply']
    assert supply['resource'] == resource
    assert supply['constraints']['regulator-min-microvolt'] == uv
    assert supply['constraints']['regulator-max-microvolt'] == uv
    assert pcie[f'qcom,vreg-{suffix}-voltage-level'][:2] == [uv, uv]
assert pcie['resolved_supplies']['vreg-cx-supply']['resource'] == 'cx.lvl'
assert pcie['qcom,vreg-cx-voltage-level'] == [65535, 256, 0]
node = '/soc/qcom,pcie@1c00000'
phy = [int(v, 16) for triple in re.findall(r'\{\s*(0x[0-9a-f]+),\s*(0x[0-9a-f]+),\s*([0-9]+)\s*\}',
                                         (here / 'munch-wifi-phy.h').read_text()) for v in triple]
assert phy and phy == prop(node, 'qcom,phy-sequence')
assert prop(node, 'qcom,pcie-phy-ver') == [1102]  # decimal DT value, not 0x1102
assert prop(node, 'qcom,ep-latency') == [10]
assert prop(node, 'qcom,phy-status-offset') == [0x814]
assert prop(node, 'qcom,phy-status-bit') == [6]
assert prop(node, 'qcom,phy-power-down-offset') == [0x840]
# Check pin mux, pull, drive and output constraints used by the core.
base = '/soc/pinctrl@f000000/'
for path, pin, function, drive, bias in (
    ('cnss_pins/cnss_wlan_en_active', 'gpio20', 'gpio', 16, 'bias-pull-up'),
    ('cnss_pins/cnss_wlan_en_sleep', 'gpio20', 'gpio', 2, 'bias-pull-down'),
    ('pcie0/pcie0_clkreq_default', 'gpio80', 'pci_e0', 2, 'bias-pull-up'),
    ('pcie0/pcie0_perst_default', 'gpio79', 'gpio', 2, 'bias-pull-down'),
    ('pcie0/pcie0_wake_default', 'gpio81', 'gpio', 2, 'bias-pull-up'),
):
    assert prop(base + path + '/mux', 'pins', True) == [pin]
    assert prop(base + path + '/mux', 'function', True) == [function]
    assert prop(base + path + '/config', 'drive-strength') == [drive]
    assert prop(base + path + '/config', bias) == []
report = {'dtb_sha256': sha(a.dtb.read_bytes()), 'supplies': expected,
          'shared_aon_dig': True, 'phy_table_entries': len(phy)//3,
          'phy_table_matches_munch_dt': True, 'pinctrl_checked': True,
          'cx_requested_abstract_level': 256, 'cx_payload': 'runtime CMD DB ARC level index',
          'hardware_tested': False,
          'sequencing': 'bounded diagnostic; exact stock binary regulator order is not established'}
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(report, indent=2) + '\n')
print('PASS: munch supply constraints, shared smpa6, CX level, complete PHY table and pinctrl')
