#!/usr/bin/env python3
"""Run the actual identical-front remeasurement against simulated copy delays."""
from pathlib import Path
import subprocess
import tempfile

here = Path(__file__).resolve().parent
root = here.parents[2]
with tempfile.TemporaryDirectory(prefix='munch-copy-test-') as directory:
    work = Path(directory)
    path = work / 'iokit/Kernel/MiniIOMobileFramebuffer.cpp'
    path.parent.mkdir(parents=True)
    path.write_bytes((root / 'xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp').read_bytes())
    subprocess.run(['git', 'apply', str(here / 'munch-command-staging.patch')], cwd=work, check=True)
    source = path.read_text()
    start = source.index('bool\nIOMobileFramebuffer::remeasureCopy(')
    end = source.index('\n}', start) + 2
    method = source[start:end].replace('__asm__ volatile("dsb oshst" ::: "memory");', '__asm__ volatile("" ::: "memory");')
    policy = (here / 'munch-command-scan.inc').read_text()
    policy = policy[policy.index('/* COPY HISTORY'):policy.index('/* END COPY HISTORY */')]
    program = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define kernel_task 0
#define kIODirectionInOut 0
#define PAGE_SIZE 4096
static uint64_t now, cost=1000000;
static bool deny_allocation;
static unsigned allocations;
static uint64_t mach_absolute_time(void) { uint64_t old=now; now+=cost; return old; }
static void absolutetime_to_nanoseconds(uint64_t t, uint64_t *ns) { *ns=t; }
static void hakios_tl(const char *, ...) {}
struct IOBufferMemoryDescriptor {
    uint8_t data[256];
    static IOBufferMemoryDescriptor *inTaskWithOptions(int, int, size_t n, size_t) {
        allocations++; assert(n==256);
        static IOBufferMemoryDescriptor memory;
        return deny_allocation ? NULL : &memory;
    }
    void *getBytesNoCopy(void) { return data; }
};
class IOMobileFramebuffer {
public:
    uint32_t fRowBytes=64, fPanelHeight=4;
    uint8_t *fBase, *fProbeBase=NULL, *fComposeBase;
    IOBufferMemoryDescriptor *fProbeMemory=NULL;
    uint64_t fCopyNs=12000000;
    bool remeasureCopy(void);
};
'''+policy+method+r'''
int main(void) {
    uint8_t front[256], pending[256], original[256];
    for (unsigned i=0;i<256;i++) front[i]=original[i]=(uint8_t)(i*7);
    memset(pending,0xa5,sizeof(pending));
    IOMobileFramebuffer fb; fb.fBase=front; fb.fComposeBase=pending;
    deny_allocation=true;
    assert(!fb.remeasureCopy() && fb.fCopyNs==12000000);
    assert(!memcmp(front,original,256));
    deny_allocation=false;
    assert(fb.remeasureCopy() && fb.fCopyNs==1000000 && allocations==2);
    assert(!memcmp(front,original,256));
    for (unsigned i=0;i<256;i++) assert(pending[i]==0xa5);
    cost=15000000; /* one preempted copy poisons the high-water budget */
    assert(fb.remeasureCopy() && fb.fCopyNs==15000000 && allocations==2);
    cost=1200000;
    assert(fb.remeasureCopy() && fb.fCopyNs==15000000); /* recent outlier retained */
    now+=1000000001ULL;
    assert(fb.remeasureCopy() && fb.fCopyNs==1200000 && allocations==2);
    assert(!memcmp(front,original,256));
    for (unsigned i=0;i<256;i++) assert(pending[i]==0xa5);
    cost=0;
    assert(!fb.remeasureCopy() && fb.fCopyNs==1200000);
    return 0;
}
'''
    (work/'test.cpp').write_text(program)
    subprocess.run(['c++', '-std=c++14', '-O1', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', str(work/'test.cpp'), '-o', str(work/'test')], check=True)
    subprocess.run([str(work/'test')], check=True)
print('PASS: copy outlier recovery, unchanged front and pending composition, allocation failure and reuse, recent-outlier retention and expiry, zero-duration rejection (ASan/UBSan)')
