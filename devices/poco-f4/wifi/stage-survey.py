#!/usr/bin/env python3
"""Add the gated munch Wi-Fi diagnostics to an already prepared GPU XNU tree."""
import argparse
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('tree', type=Path)
p.add_argument('--gpu-report', required=True, type=Path)
p.add_argument('--report', required=True, type=Path)
a = p.parse_args()
here = Path(__file__).resolve().parent
root = here.parents[2]
sha = lambda b: hashlib.sha256(b).hexdigest()
base = (root / 'xnu/files/bsd/dev/hakios.wifi.c').read_bytes()
needle = b'void\nhakios_wifi_init(void)\n{\n'
if base.count(needle) != 1:
    raise ValueError('Unexpected Wi-Fi init entry')
data = base.replace(needle, b'#include "munch-wifi-survey.inc"\n#include "munch-wifi-enumeration.inc"\n\n' + needle +
                    b'\tif (munch_wifi_init()) return;\n', 1)
target = a.tree / 'bsd/dev/hakios.wifi.c'
old_data = base.replace(needle, b'#include "munch-wifi-survey.inc"\n\n' + needle +
                        b'\tif (munch_wifi_init()) return;\n', 1)
if target.read_bytes() not in (base, old_data, data):
    raise ValueError('Refusing to replace independently modified Wi-Fi source')
source = (here / 'munch-wifi-survey.inc').read_bytes()
report = json.loads(a.gpu_report.read_text())
dependencies = {name: (here / name).read_bytes() for name in (
    "munch-wifi-enumeration.inc", "munch-wifi-dma-survey.inc", "munch-wifi-memory.inc", "munch-wifi-memory-ranges.h", "munch-wifi-pcie.inc", "munch-wifi-pcie.h",
    "munch-wifi-phy.h", "munch-wifi-rpmh.inc")}
report.update(wifi_survey_only=False, wifi_diagnostic_only=True,
              wifi_diagnostic_sources={n: sha(b) for n, b in dependencies.items()}, wifi_source_sha256=sha(data),
              wifi_survey_sha256=sha(source), hardware_tested=False)
target.write_bytes(data)
(target.parent / 'munch-wifi-survey.inc').write_bytes(source)
for name, content in dependencies.items():
    (target.parent / name).write_bytes(content)
a.report.parent.mkdir(parents=True, exist_ok=True)
a.report.write_text(json.dumps(report, indent=2) + '\n')
print('Staged gated munch diagnostics (steps 1/2 read-only, step 3 power/PCIe, step 4 SID/SMMU reads); normal Wi-Fi remains disabled')
