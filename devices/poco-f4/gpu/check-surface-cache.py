#!/usr/bin/env python3
"""Run the patched MiniIOSurfaceDrawn fence/cache handoff with simulated submissions."""
from pathlib import Path
import subprocess
import tempfile

here = Path(__file__).resolve().parent
root = here.parents[2]
with tempfile.TemporaryDirectory(prefix='munch-cache-test-') as directory:
    work = Path(directory)
    for name in ('hakios.gpu.cpp', 'MiniIOSurface.h'):
        target = work / 'iokit/Kernel' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / 'xnu/files/iokit/Kernel' / name).read_bytes())
    subprocess.run(['git', 'apply', str(here / 'umi-surface-cache.patch')], cwd=work, check=True)
    source = (work / 'iokit/Kernel/hakios.gpu.cpp').read_text()
    start = source.index('bool\nMiniIOSurfaceDrawn(')
    end = source.index('\n}', start) + 2
    function = source[start:end]
    program = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
struct MiniIOSurface {
    uint32_t drawn;
    bool gpu_written;
    uint8_t *kpixels;
    struct { uint64_t allocSize; } info;
};
static int lock;
static int *gGPULock=&lock;
static bool ready=true, finished=true;
static unsigned waits, invalidated, nesting, rewrite;
static MiniIOSurface *current;
static uint8_t pixels[256];
static bool hakios_gpu_ready(void) { return ready; }
static void IOLockLock(int *) { assert(nesting++==0); }
static void IOLockUnlock(int *) { assert(nesting--==1); }
static bool gpuWait(uint32_t, uint64_t) {
    waits++;
    if (rewrite==2 || (rewrite==1 && waits==1)) current->drawn++;
    return finished;
}
static void hakios_gpu_from_memory(void *p, uint64_t n) {
    assert(p==pixels && n==sizeof(pixels) && nesting==1); invalidated++;
}
'''+function+r'''
static void reset(MiniIOSurface &s) {
    s.drawn=42; s.gpu_written=true; s.kpixels=pixels; s.info.allocSize=sizeof(pixels);
    current=&s; waits=invalidated=nesting=rewrite=0; ready=finished=true;
}
int main(void) {
    MiniIOSurface s;
    reset(s); finished=false;
    assert(!MiniIOSurfaceDrawn(&s,500) && waits==1 && !invalidated && s.gpu_written);
    reset(s);
    assert(MiniIOSurfaceDrawn(&s,500) && waits==1 && invalidated==1 && !s.gpu_written);
    assert(MiniIOSurfaceDrawn(&s,500) && invalidated==1 && !nesting);
    reset(s); rewrite=1;
    assert(MiniIOSurfaceDrawn(&s,500) && waits==2 && invalidated==1 && !s.gpu_written);
    reset(s); rewrite=2;
    assert(!MiniIOSurfaceDrawn(&s,500) && waits==4 && !invalidated && s.gpu_written);
    reset(s); ready=false;
    assert(MiniIOSurfaceDrawn(&s,500) && !waits && !invalidated && s.gpu_written);
    reset(s); s.drawn=0; s.gpu_written=false;
    assert(MiniIOSurfaceDrawn(&s,500) && !waits && !invalidated);
    assert(!nesting);
}
'''
    (work / 'test.cpp').write_text(program)
    subprocess.run(['c++', '-std=c++14', '-O1', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', str(work/'test.cpp'), '-o', str(work/'test')], check=True)
    subprocess.run([str(work/'test')], check=True)
print('PASS: GPU timeout, invalidate exactly once after stable fence, resubmission retry/bound, CPU-only surface (ASan/UBSan)')
