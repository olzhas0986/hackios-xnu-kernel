#!/usr/bin/env python3
"""Wait for the post-return munch USB log server and atomically fetch its frozen log."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import subprocess
import tempfile
import time
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('output',nargs='?',type=Path)
p.add_argument('--serial',default='xnuloader')
p.add_argument('--timeout',type=int,default=300)
a=p.parse_args()
if a.timeout<1 or a.timeout>3600:p.error('--timeout must be 1..3600 seconds')
output=a.output or Path('out/munch-wifi/logs')/('wifi-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.txt')
base=['fastboot','-s',a.serial]
deadline=time.monotonic()+a.timeout
print('Waiting for read-only munch USB log server, serial='+a.serial,flush=True)
while time.monotonic()<deadline:
    try:
        devices=subprocess.run(['fastboot','devices'],capture_output=True,text=True,timeout=5,check=True).stdout
        if not any(line.split() and line.split()[0]==a.serial for line in devices.splitlines()):
            time.sleep(1);continue
        result=subprocess.run(base+['getvar','product'],capture_output=True,text=True,timeout=5)
        if result.returncode==0 and 'munch-wifi-log' in result.stdout+result.stderr:break
    except (subprocess.TimeoutExpired,subprocess.CalledProcessError):pass
    time.sleep(1)
else:raise SystemExit('USB log server not found before timeout; keep the screen result. No partition writes were attempted.')
result=subprocess.run(base+['getvar','result'],capture_output=True,text=True,timeout=5,check=True)
print((result.stdout+result.stderr).strip())
output.parent.mkdir(parents=True,exist_ok=True)
fd,name=tempfile.mkstemp(prefix=output.name+'.',suffix='.part',dir=output.parent)
os.close(fd)
try:
    subprocess.run(base+['get_staged',name],timeout=30,check=True)
    data=Path(name).read_bytes()
    if not data or len(data)>2*1024*1024:raise RuntimeError('Empty/oversized log; previous output preserved')
    os.replace(name,output)
finally:
    Path(name).unlink(missing_ok=True)
print('Saved full RAM log:',output,'('+str(len(data))+' bytes)')
lines=data.decode('utf-8',errors='replace').splitlines()
wifi=[line for line in lines if 'MUNCH Wi-Fi:' in line]
print('\n'.join(wifi[-80:] if wifi else lines[-40:]))
