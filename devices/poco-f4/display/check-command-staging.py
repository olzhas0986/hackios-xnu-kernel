#!/usr/bin/env python3
"""Check overlay switching and execute the actual command scan reader with mocked MMIO."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from types import SimpleNamespace

here = Path(__file__).resolve().parent
root = here.parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--cxx', default=shutil.which('c++') or shutil.which('clang++'))
a = p.parse_args()

# Execute the real framebuffer staging section against an isolated prepared tree.
# This exercises both patches, switching back to baseline and refusal to clobber edits.
source = (here.parent / 'gpu/stage-kernel.py').read_text()
section = source[source.index('fb_base = '):source.index('report = ')]
with tempfile.TemporaryDirectory(prefix='munch-stage-check-') as directory:
    tree = Path(directory)
    target = tree / 'iokit/Kernel/MiniIOMobileFramebuffer.cpp'
    target.parent.mkdir(parents=True)
    baseline = (root / 'xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp').read_bytes()
    target.write_bytes(baseline)
    sha = lambda data: hashlib.sha256(data).hexdigest()
    for te, command in ((False, True), (True, False), (False, False), (False, True)):
        scope = dict(root=root, here=here.parent / 'gpu', Path=Path, subprocess=subprocess,
                     tempfile=tempfile, sha=sha,
                     a=SimpleNamespace(tree=tree, te_staging=te, command_staging=command))
        exec(compile(section, str(here.parent / 'gpu/stage-kernel.py'), 'exec'), scope)
        if command:
            assert b'#include "munch-command-scan.inc"' in target.read_bytes()
            assert (target.parent / 'munch-command-scan.inc').read_bytes() == (here / 'munch-command-scan.inc').read_bytes()
        elif not te:
            assert target.read_bytes() == baseline
    target.write_bytes(b'independent framebuffer edits')
    try:
        exec(compile(section, '<staging>', 'exec'), scope)
    except ValueError:
        assert target.read_bytes() == b'independent framebuffer edits'
    else:
        raise AssertionError('Independent changes were overwritten')
print('PASS: baseline/TE/command overlay switching and preservation of independent edits')

# Exercise the packer's actual flag checks and compare to the independent
# verifier's whitelist, without needing an iOS image or native toolchain.
packer = (here.parent / 'loader/pack-checkpoint.py').read_text()
flag_checks = textwrap.dedent(packer[packer.index("                te = native.get('display_te'"):
                                  packer.index("                cmdline = (f'cpus=8", packer.index("                te = native.get('display_te'"))])
verifier = (here.parent / 'touch/verify-image.py').read_text()
whitelist = verifier[verifier.index('allowed_gpu = {'):verifier.index("assert report['cmdline'] in allowed_gpu")]
allowed = {}
exec(whitelist, allowed)
for config, hz, manifest, valid in (
    ({'display_scan': True}, 60, {'display_command_staging': True}, True),
    ({'display_scan': False}, 60, {}, True),
    ({'display_scan': True, 'compositor_profile': 'umi-fast'}, 60, {'display_command_staging': True, 'surface_cache_sources': {'consumer': 'tested'}}, True),
    ({'display_scan': False, 'compositor_profile': 'umi-fast'}, 60, {'surface_cache_sources': {'consumer': 'tested'}}, False),
    ({'compositor_profile': 'invalid'}, 60, {}, False),
    ({'display_scan': True, 'display_scan_hud': True}, 60, {'display_command_staging': True, 'display_status_hud': True}, True),
    ({'display_scan': False, 'display_scan_hud': True}, 60, {'display_status_hud': True}, False),
    ({'display_scan': True}, 120, {'display_command_staging': True}, False),
    ({'display_scan': True, 'display_te': True}, 60,
     {'display_command_staging': True, 'display_te_staging': True}, False),
    ({'display_scan': True}, 60, {}, False),
    ({'display_scan': 1}, 60, {'display_command_staging': True}, False),
):
    scope = dict(native=config, hz=hz, source_report=json.dumps(manifest), json=json, extra='')
    try:
        exec(flag_checks, scope)
    except ValueError:
        assert not valid
    else:
        assert valid
        cmd = ('cpus=8 small_cores_after=0 munch_touch=1 gpu=1 adreno_speed=5 '
               + scope['extra'] + 'loader_return_after=0')
        assert cmd in allowed['allowed_gpu']
print('PASS: packer/verifier agree on scan flags; conflicting modes, wrong rate and missing provenance rejected')

# Mock time and register reads, not the admission/calibration functions themselves.
stub = r'''
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define MUNCH_SCAN_TEST 1
#define NSEC_PER_USEC 1000
#define NSEC_PER_MSEC 1000000
#define NSEC_PER_SEC 1000000000ULL
typedef uintptr_t vm_offset_t;
typedef int intf; /* libkern zlib headers also define this name in XNU. */
static uint64_t now, longest_delay, sim_period_ns=16500000, wake_late_ns;
static uint32_t gcc, ahb, power=0x80000000;
static bool enabled=true, moving=true, force_phase=false;
static uint32_t phase_value=0, written_value=2400, initial=2400, line_step_ns=6000, busy_until=2000;
static bool wrap_on_read=false, stall_on_read=false, deny_ahb=false;
static unsigned phase_reads, mmio_reads, intf_reads, writes;
static uint64_t mach_absolute_time(void) { return now; }
static void absolutetime_to_nanoseconds(uint64_t t, uint64_t *ns) { *ns=t; }
static void delay_for_interval(uint32_t a, uint32_t b) {
    uint64_t ns=(uint64_t)a*b; now+=ns;
    if (ns>200000) now+=wake_late_ns;
    if (ns>longest_delay) longest_delay=ns;
}
static void hakios_tl(const char *, ...) {}
static int PE_parse_boot_argn(const char *, void *v, int) { *(uint32_t *)v=enabled; return enabled; }
static vm_offset_t ml_io_map(vm_offset_t at, size_t) { return at; }
static uint32_t rd(vm_offset_t base, uint32_t offset) {
    mmio_reads++;
    if (base==0x10b000) { assert(offset==0xc); return gcc; }
    assert(gcc==1);
    if (base==0xaf00000) {
        if (offset==0x3000) return power;
        assert(offset==0x2080); return deny_ahb && (ahb & 1) ? ahb | 0x80000000 : ahb;
    }
    assert(base==0xae6b800 && (power&0x80000000) && ahb==1);
    intf_reads++;
    uint32_t phase=moving ? (now%sim_period_ns)/line_step_ns : 0;
    switch(offset) {
    case 0: return 0;
    case 0x284: return 1;
    case 0x288: return 0x180000;
    case 0x294: return initial;
    case 0x2b4: return 0x80000001;
    case 0x298:
        if (force_phase) {
            phase=phase_value;
            if (phase_reads++==0) {
                if (wrap_on_read) phase_value=1250;
                if (stall_on_read) now+=16500000;
            }
        }
        return initial+phase;
    case 0x2b0: return force_phase ? written_value : (phase<busy_until ? phase+1 : initial);
    default: assert(false); return 0;
    }
}
static void wr(vm_offset_t base, uint32_t offset, uint32_t value) {
    writes++;
    if (base==0x10b000) { assert(offset==0xc); gcc=value; }
    else { assert(base==0xaf00000 && offset==0x2080 && gcc==1); ahb=value; }
}
'''
checks = r'''
static void reset(void) {
    now=longest_delay=wake_late_ns=0; sim_period_ns=16500000; gcc=ahb=0; power=0x80000000; enabled=moving=true;
    force_phase=wrap_on_read=stall_on_read=deny_ahb=false;
    phase_reads=mmio_reads=intf_reads=writes=0;
    scan.ok=0; initial=2400; line_step_ns=6000; busy_until=2000; gPhaseMax=0;
}
int main(void) {
    reset(); enabled=false;
    assert(!hakios_display_scan_start() && !mmio_reads && !writes);
    reset(); power=0;
    assert(!hakios_display_scan_start() && !intf_reads && gcc==0 && ahb==0);
    reset(); deny_ahb=true;
    assert(!hakios_display_scan_start() && !intf_reads && gcc==0 && ahb==0);
    reset(); initial=2340;
    assert(!hakios_display_scan_start() && gcc==0 && ahb==0);
    reset(); moving=false;
    assert(!hakios_display_scan_start() && now<180000000 && gcc==0 && ahb==0);
    reset(); line_step_ns=1000;
    assert(!hakios_display_scan_start() && gScanReason==17 && !scan.ok && gcc==0 && ahb==0);
    reset(); line_step_ns=7000;
    assert(hakios_display_scan_start() && scan.ok && scan.lines < scan.shown);
    assert(scan.lines>2300 && te_idle_start<scan.lines);
    force_phase=true; phase_value=2100; written_value=2400;
    assert(hakios_display_wait_copy(500000,20000000));
    assert(hakios_display_next_frame_ns()>0);
    phase_value=scan.lines+1;
    assert(hakios_display_next_frame_ns()==0); /* no unsigned underflow near TE */
    assert(!hakios_display_wait_copy(500000,0));
    reset(); line_step_ns=7000; busy_until=2340;
    assert(!hakios_display_scan_start() && gScanReason==18 && !scan.ok && gcc==0 && ahb==0);
    assert(scan.millihz>60000 && scan.millihz<61000); /* retained on failure */
    reset();
    assert(hakios_display_scan_start() && scan.ok && gcc==1 && ahb==1);
    assert(scan.shown==2400 && scan.millihz>60000 && scan.millihz<61000);
    assert(scan.line_ns>=5900 && scan.line_ns<=6100);
    assert(hakios_display_scan()!=NULL && hakios_display_next_frame_ns()>0);
    force_phase=true; phase_value=2100; written_value=2400;
    assert(hakios_display_wait_copy(1000000,20000000));
    phase_value=2700; now=0;
    assert(!hakios_display_wait_copy(1000000,20000000));
    phase_value=2100; written_value=1000; now=0;
    assert(!hakios_display_wait_copy(1000000,20000000));
    phase_value=100; written_value=2400; now=longest_delay=0;
    assert(!hakios_display_wait_copy(1000000,20000000));
    assert(longest_delay>400000 && now>=20000000 && now<20001000);
    phase_value=2200; phase_reads=0; wrap_on_read=true;
    assert(!hakios_display_wait_copy(1000000,0));
    wrap_on_read=false; phase_value=2100; phase_reads=0; stall_on_read=true;
    assert(!hakios_display_wait_copy(1000000,0));
    stall_on_read=false;
    assert(!hakios_display_wait_copy(UINT64_MAX,0));
    assert(!hakios_display_wait_copy(0,0));
    /* User's measured munch geometry: an outlier budget cannot ever fit,
     * while the last admitted 4775-us budget still fits the DMA idle gap. */
    scan.frame_ns=16485000; scan.line_ns=6928; scan.lines=2380;
    scan.shown=te_initial=2400; te_idle_start=1225; gPhaseMax=2376;
    phase_value=1230; written_value=2400; phase_reads=0;
    assert(!hakios_display_wait_copy(12000000,0));
    assert(hakios_display_wait_copy(4775000,0));
    /* Photo: a ~179-us admissible start interval. A 400-us scheduler
     * oversleep misses it with the old 200-us wake-ahead policy. */
    reset(); gcc=ahb=1; scan.ok=1;
    scan.frame_ns=sim_period_ns=16469000; scan.line_ns=line_step_ns=6927;
    scan.lines=2378; scan.shown=te_initial=2400; te_idle_start=1280;
    gPhaseMax=2378; busy_until=1248; wake_late_ns=400000;
    now=1000*6927; uint64_t began=now;
#ifdef MUNCH_OLD_WAIT
    assert(!hakios_display_wait_copy(5539000,25000000));
#else
    assert(hakios_display_wait_copy(5539000,25000000));
    assert(now-began<sim_period_ns);
#endif
    (void)began;
    assert(!hakios_display_wait_copy(6200000,0)); /* no room even at idle start */
    wake_late_ns=0;
    munch_copy_observe(2000000000ULL, 12000000);
    munch_copy_observe(2500000000ULL, 4000000);
    assert(munch_copy_budget(2600000000ULL)==12000000);
    assert(munch_copy_budget(3100000000ULL)==4000000);
    assert(munch_copy_budget(3600000000ULL)==0);
    munch_presented(2000000000ULL);
    munch_presented(2500000000ULL);
    assert(munch_present_fps(2600000000ULL)==2);
    assert(munch_present_fps(3100000000ULL)==1);
    assert(munch_present_fps(3600000000ULL)==0);
    uint32_t pixels[720*288+16];
    for (unsigned i=0;i<720*288+16;i++) pixels[i]=0x12345678;
    now=1000000000ULL; scan.millihz=60718;
    munch_scan_hud((uint8_t *)pixels,720,288,720*4,1000000,50,3,true);
    assert(pixels[0]==0x12345678 && pixels[80*720+8]!=0x12345678);
    assert(pixels[104*720+8]==0x12345678); /* all former HUD rows untouched */
    assert(pixels[80*720+170]==0x12345678); /* compact horizontal bounds */
    assert(pixels[288*720]==0x12345678 && pixels[272*720]==0x12345678);
    unsigned saved=intf_reads; gcc=0;
    assert(hakios_display_line(NULL)==0 && !scan.ok && saved==intf_reads && gScanReason==13);
    return 0;
}
'''
if not a.cxx:
    print('SKIP: native MMIO/calibration tests require c++ or clang++; no compiler installed')
else:
    with tempfile.TemporaryDirectory(prefix='munch-scan-check-') as directory:
        path=Path(directory)
        source = (here/'munch-command-scan.inc').read_text()
        for old_wait in (False, True):
            tested = source
            if old_wait:
                tested = tested.replace('nap = 10000;', 'nap = 50000;').replace(
                    'if (ahead > 1250000) nap = ahead - 1000000;',
                    'if (ahead > 400000) nap = ahead - 200000;')
                assert tested != source
            (path/'test.cpp').write_text(('#define MUNCH_OLD_WAIT 1\n' if old_wait else '') + stub + tested + checks)
            subprocess.run([a.cxx, '-std=c++14', '-O1', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', str(path/'test.cpp'), '-o', str(path/'test')], check=True)
            subprocess.run([str(path/'test')], check=True)
    print('PASS: actual MMIO guards, TE phases shorter than physical height, invalid span/idle rejection, copy budget, wrap/stall rejection, HUD bounds; old wake policy misses the photo window under 400-us oversleep, new policy admits (ASan/UBSan)')
