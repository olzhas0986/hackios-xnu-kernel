#!/usr/bin/env python3
"""Run actual step 3 with simulated MMIO completion, faults and forbidden-write checks."""
import os
import json
from pathlib import Path
import subprocess
import tempfile

here = Path(__file__).resolve().parent
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <errno.h>
typedef uintptr_t vm_offset_t;
typedef int DTEntry;
#ifndef PAGE_SIZE
#define PAGE_SIZE 0x4000u
#endif
#define VM_WIMG_IO 7u
#define kSuccess 0
#define __printflike(a,b)
static unsigned maps, unmaps, writes, triggers, returns, fail_map, fault, smmu_reads, sid_reads, cb_maps, sid_passed, first_cb_read_pa;
static unsigned step=3, back=1, enum_passed, expected_cx, cx_requests;
static uint32_t mem[8][0x100000/4] __attribute__((aligned(0x4000)));
static const uint32_t pas[]={0x100000,0xf900000,0x1c00000,0x60000000,0x60100000,0x80860000,0x18220000,0x15000000};
static const unsigned sizes[]={0x100000,0x80000,0x10000,0x2000,0x1000,0x20000,0x10000,0x100000};
static uint32_t pa_of(uintptr_t va) {
    for(unsigned i=0;i<8;i++) if(va>=(uintptr_t)mem[i] && va<(uintptr_t)mem[i]+sizes[i])
        return pas[i]+va-(uintptr_t)mem[i];
    assert(!"MMIO outside mapped regions"); return 0;
}
static uint32_t read32(uintptr_t va) {
    uint32_t pa=pa_of(va);
    if(pa>=0x1c02000 && pa<0x1c02400) { assert(step==4);sid_reads++; }
    if(pa>=0x15000000 && pa<0x15100000) { assert(step==4);smmu_reads++; }
    if(pa>=0x15080000 && pa<0x15100000 && !first_cb_read_pa) first_cb_read_pa=pa;
    if(fault==3 && pa==0x60000f28) return 0; /* no link */
    return *(uint32_t *)va;
}
static void write32(uintptr_t va,uint32_t value) {
    uint32_t pa=pa_of(va); writes++;
    assert(!(pa&3));
    assert(pa<0x15000000 || pa>=0x15100000); /* SMMU is read-only */
    assert(pa<0x1c02000 || pa>=0x1c02400); /* no SID writes */
    assert(pa<0x60100000 || pa>=0x60200000); /* endpoint config: 16-bit only */
    assert(pa!=0x60000004 && pa!=0x60000078);
    if(pa>=0x18220d00 && pa<0x182210f0) {
        unsigned off=pa-0x18220d00;
        assert(off<2*0x2a0);
        assert(off<0xc || off>=0x2a0); /* never modify inherited TCS0 */
        if(off==8) { /* W1C, only the owned TCS1 bit */
            assert(value==2); mem[6][0xd04/4]&=~value; return;
        }
        if(off==0x2a0+0x14 && (value&(1u<<24))) {
            triggers++;
            unsigned en=mem[6][(0xd00+0x2a0+0x1c)/4];
            assert(en==1 || en==3);
            for(unsigned c=0;c<(en==1?1u:2u);c++) {
                unsigned cmd=(0xd00+0x2a0+0x30+c*0x14)/4;
                assert(mem[6][cmd]==0x10108);
                if(mem[6][cmd+1]==0x30000) {
                    cx_requests++;
                    if(expected_cx) assert(mem[6][cmd+2]==expected_cx);
                }
                mem[6][cmd+3]=(1u<<8)|(1u<<16);
            }
            if(fault!=1) mem[6][0xd04/4]|=2;
            else mem[6][(0xd00+0x2a0+0x18)/4]=0;
        }
    }
    if(fault==28 && pa==0xf94f004) mem[6][(0xd00+0x38)/4]=3;
    if(fault==39 && pa==0xf94f004) mem[6][(0xd00+0x4c)/4]=3;
    if(fault==40 && pa==0x18220d00+0x2a0+0x1c) mem[6][(0xd00+0x30)/4]=0x10000;
    *(uint32_t *)va=value;
    if(pa==0x16b004) *(uint32_t *)va|=1u<<31;
    if(pa==0x16b038 || pa==0x16f014) *(uint32_t *)va&=~1u;
}
static void write16(uintptr_t va,uint16_t value) {
    uint32_t pa=pa_of(va); writes++;
    assert(pa==0x60100004 || pa==0x60000004 || pa==0x60000078);
    if(pa!=0x60000078) assert(!(value&7)); /* bus mastering/decode always off */
    *(uint16_t *)va=value;
}
#define MUNCH_WIFI_MMIO_READ32(a) read32(a)
#define MUNCH_WIFI_MMIO_WRITE32(a,v) write32(a,v)
#define MUNCH_WIFI_MMIO_WRITE16(a,v) write16(a,v)
static int SecureDTLookupEntry(void *p,const char *path,DTEntry *e) { (void)p;(void)path;*e=1;return 0; }
static int SecureDTGetProperty(DTEntry e,const char *key,const void **b,unsigned *s) {
    (void)e;(void)key;*b="munch";*s=6;return 0;
}
static void PE_parse_boot_argn(const char *k,void *v,unsigned n) {
    assert(n==4);*(uint32_t *)v=!strcmp(k,"munch_wifi_step")?step:!strcmp(k,"munch_wifi_return")?back:0;
}
static vm_offset_t ml_io_map_unmappable(vm_offset_t pa,unsigned size,unsigned flags) {
    assert(flags==VM_WIMG_IO);maps++;
    for(unsigned i=0;i<8;i++) if(pa==pas[i]) { assert(!(pa&(PAGE_SIZE-1)) && size==((i==7?0x2000:sizes[i])+PAGE_SIZE-1)/PAGE_SIZE*PAGE_SIZE);return maps==fail_map?0:(uintptr_t)mem[i]; }
    if(pa>=0x15080000 && pa<0x15100000) {
        assert(size==PAGE_SIZE && !(pa&(PAGE_SIZE-1)));cb_maps++;
        return maps==fail_map?0:(uintptr_t)mem[7]+pa-0x15000000;
    }
    assert(0);return 0;
}
static void ml_io_unmap(vm_offset_t va,unsigned size) { assert(!(va&(PAGE_SIZE-1)) && size && !(size&(PAGE_SIZE-1)));(void)pa_of(va);unmaps++; }
void hakios_tl(const char *fmt,...) {
    char line[256]; va_list ap; va_start(ap,fmt);vsnprintf(line,sizeof(line),fmt,ap);va_end(ap);
    if(strstr(line,"enumeration passed;")) enum_passed++;
    if(strstr(line,"SID/SMMU survey passed;")) sid_passed++;
}
static void loader_return(void) { returns++; }
static void delay(unsigned us) { assert(us<=10000); }
#define panic(...) abort()
#define KMA_KOBJECT 1
#define KMA_ZERO 2
#define KMA_DATA 4
#define VM_KERN_MEMORY_IOKIT 0
#define KERN_SUCCESS 0
#define kernel_map 0
static int kmem_alloc(int map,vm_offset_t*v,unsigned n,int f,int t) {
    (void)map;(void)v;(void)n;(void)f;(void)t;assert(!"memory opt-in is off");return 1;
}
static void kmem_free(int map,vm_offset_t v,unsigned n){(void)map;(void)v;(void)n;abort();}
static uint64_t kvtophys(vm_offset_t v){(void)v;abort();}
#include "munch-wifi-survey.inc"
#include "munch-wifi-enumeration.inc"
static void init(void) {
    memset(mem,0,sizeof(mem));
    uint32_t *db=mem[5],*rsc=mem[6];
    db[0]=1;db[1]=0xc0330db;db[2]=4;db[3]=(7u<<16)|0x400;
    const char *names[]={"smpa6","smpa4","smpa5","smpc8","ldoa9","ldoa5","ldoa16"};
    uint32_t addrs[]={0x40200,0x40000,0x40100,0x40400,0x41b00,0x41800,0x40b00};
    for(unsigned i=0;i<7;i++) {
        memcpy((char *)db+144+i*24,names[i],strlen(names[i]));
        db[(144+i*24+16)/4]=addrs[i];
    }
    db[6]=(0x200u<<16)|3;db[7]=(1u<<16)|0x400;
    memcpy((char *)db+144+0x200,"cx.lvl",6); db[(144+0x200+16)/4]=0x30000;
    db[(144+0x200+20)/4]=12;
    uint16_t levels[]={0,16,64,128,256,384};
    memcpy((char *)db+144+0x400,levels,sizeof(levels));
    rsc[0]=0x20000;rsc[3]=0x800c8104;
    rsc[(0xd00+0x14)/4]=0x1010000;rsc[(0xd00+0x18)/4]=1;rsc[(0xd00+0x1c)/4]=3;
    rsc[(0xd00+0x34)/4]=0x50000;rsc[(0xd00+0x48)/4]=0x50004;
    rsc[(0xd00+0x2a0+0x14)/4]=0x10000;rsc[(0xd00+0x2a0+0x18)/4]=1;
    rsc[0xd00/4]=0x83; /* unrelated IRQ bits must survive */
    mem[3][0xf28/4]=1u<<10;mem[3][0x80/4]=1u<<29;
    mem[3][4/4]=0xa5a50000;mem[3][0x78/4]=0x5a5a0000;
    mem[4][0]=0x110117cb;mem[4][0x2c/4]=0x010817cb;mem[4][4/4]=0xdead0000;
}
int main(int argc,char **argv) {
    assert(argc==2);unsigned scenario=atoi(argv[1]);init();
    if(scenario>=10 && scenario<=16) fail_map=scenario-9;
    if(scenario==1) fault=1; /* transaction timeout */
    if(scenario==2) mem[6][(0xd00+0x34)/4]=0x40200; /* inherited conflict */
    if(scenario==3) fault=3;
    if(scenario==4) mem[4][0]=0xffffffff;
    if(scenario==5) mem[5][(144+0x200+20)/4]=3; /* malformed ARC aux */
    if(scenario==6) mem[6][3]=0xffffffff;
    if(scenario==7) mem[1][(21*0x1000+4)/4]=1; /* BT already on */
    if(scenario==8) mem[5][(144+16)/4]=0; /* required rail missing */
    if(scenario==9) mem[6][(0xd00+0x2a0+0x1c)/4]=1; /* no free slot */
    if(scenario==17) mem[4][0x2c/4]=0x999917cb;
    if(scenario==18) back=0;
    if(scenario==19) { mem[6][0xd04/4]=2; } /* pending IRQ on candidate */
    if(scenario>=20) {
        mem[6][(0xd00+0x34)/4]=0x30000;
        mem[6][(0xd00+0x38)/4]=4;
        mem[6][(0xd00+0x30)/4]=0x10108;
        mem[6][(0xd00+0x3c)/4]=0x10100;
    }
    if(scenario==20) { /* observed index 2 with a runtime table mapping it to SVS */
        uint16_t levels[]={0,16,256,384,416,448};
        memcpy((char *)mem[5]+144+0x400,levels,sizeof(levels));
        mem[6][(0xd00+0x38)/4]=2;
    }
    if(scenario==22) mem[6][(0xd00+0x38)/4]=5; /* higher level */
    if(scenario==23) mem[6][(0xd00+0x38)/4]=2; /* below SVS in this table */
    if(scenario==24) mem[6][(0xd00+0x38)/4]=99;
    if(scenario==25) mem[6][0xd04/4]=1;
    if(scenario==26) mem[6][(0xd00+0x3c)/4]=0;
    if(scenario==27) mem[6][(0xd00+0x30)/4]=0; /* not acknowledged write */
    if(scenario==28) fault=28; /* inherited data changes after preflight */
    if(scenario==29) mem[6][(0xd00+0x48)/4]=0x40200; /* second command conflict */
    if(scenario>=30 && scenario<42) {
        /* Synthetic table constrained by photo: count 9, index 2=64, SVS index 5. */
        uint16_t levels[]={0,16,64,128,192,256,384,416,448};
        memcpy((char *)mem[5]+144+0x400,levels,sizeof(levels));
        mem[5][(144+0x200+20)/4]=sizeof(levels);
        mem[6][(0xd00+0x38)/4]=2;
        mem[6][(0xd00+0x30)/4]=0x10008;
        mem[6][(0xd00+0x3c)/4]=0x101;
        mem[6][(0xd00+0x48)/4]=0x30010;
        mem[6][(0xd00+0x4c)/4]=2;
        mem[6][(0xd00+0x44)/4]=0x10008;
        mem[6][(0xd00+0x50)/4]=0x101;
        expected_cx=5;
    }
    if(scenario==31) { mem[6][(0xd00+0x38)/4]=7;expected_cx=7; }
    if(scenario==32) fault=1;
    if(scenario==33) mem[6][(0xd00+0x18)/4]=0;
    if(scenario==34) mem[6][0xd04/4]=1;
    if(scenario==35) mem[6][(0xd00+0x50)/4]=1;
    if(scenario==36) mem[6][(0xd00+0x30)/4]=8;
    if(scenario==37) mem[6][(0xd00+0x38)/4]=99;
    if(scenario==38) mem[6][(0xd00+0x48)/4]=0x40200;
    if(scenario==39) fault=39;
    if(scenario==40) fault=40;
    if(scenario==41) mem[6][(0xd00+0x3c)/4]=0x10100;
    if(scenario>=42) {
        step=4;
        /* Ordinary fully acknowledged inherited CX, no posted override needed. */
        mem[7][0]=0x400;mem[7][0x20/4]=(1u<<27)|16;mem[7][0x24/4]=0x60000008;
        mem[7][0x800/4]=0x80001c01;mem[7][0xc00/4]=2;
        mem[7][0x804/4]=0x80001c00;mem[7][0xc04/4]=0x10000;
    }
    uint8_t route_hash=0;
    if(scenario>=42) {
        route_hash=1;
        for(unsigned b=0;b<8;b++) route_hash=(route_hash<<1)^((route_hash&0x80)?7:0);
        for(unsigned b=0;b<8;b++) route_hash=(route_hash<<1)^((route_hash&0x80)?7:0);
        mem[2][(0x2000+4*route_hash)/4]=0x01000100;
    }
    if(scenario==43) mem[7][0x20/4]=0xffffffff;
    if(scenario==44) mem[7][0]=8;
    if(scenario==45) fail_map=8;
    if(scenario==46) { /* masked stream match covers root and endpoint */
        mem[7][0x800/4]=0x80011c00;mem[7][0x804/4]=0;
    }
    if(scenario==47) mem[2][(0x2000+4*route_hash)/4]=0;
    if(scenario==48) { /* two-entry cyclic collision chain */
        mem[2][(0x2000+4*route_hash)/4]=0x02000000|77;
        mem[2][(0x2000+4*77)/4]=0x03000000|route_hash;
    }
    if(scenario==49) mem[7][0xc00/4]=0x30000;
    if(scenario==50) mem[7][0xc00/4]=8;
    if(scenario==51) mem[2][0x2c00/4]=0xffffffff;
    if(scenario==52) { /* chained valid BDF, observed differing SID is not approval */
        mem[2][(0x2000+4*route_hash)/4]=0x02000000|77;
        mem[2][(0x2000+4*77)/4]=0x01000200;
    }
    if(scenario==53) mem[7][0x20/4]=(1u<<27); /* zero group count */
    if(scenario==54) fail_map=9;
    if(scenario==55) mem[7][0x24/4]=0x80000008;
    if(scenario==56) mem[7][0x24/4]=0x60090008;
    if(scenario==57) mem[7][0x80000/4]=1; /* enabled, unreferenced bank is observed, not claimed */
    if(scenario==58) mem[7][0x80000/4]=0xffffffff;
    if(scenario==59) mem[7][(0x80000+0x58)/4]=2; /* fault state must not be cleared */
    if(scenario==60) fail_map=10;
    if(scenario==61) {
        /* Header and SID chain from the photo; nonmatching owners are synthetic,
         * constrained only by 5 valid SMRs and 4 referenced CBs in the photo. */
        memset(mem[7],0,sizeof(mem[7]));
        mem[7][0]=PHOTO_SCR0;mem[7][0x20/4]=PHOTO_IDR0;
        mem[7][0x24/4]=PHOTO_IDR1;mem[7][0x28/4]=PHOTO_IDR2;
        for(unsigned i=0;i<5;i++) { mem[7][(0x800+4*i)/4]=0x80000100+i*16;mem[7][(0xc00+4*i)/4]=i<4?i:3; }
        const uint32_t chain[][2]=PHOTO_CHAIN;
        for(unsigned i=0;i<sizeof(chain)/sizeof(chain[0]);i++)
            mem[2][(0x2000+4*chain[i][0])/4]=chain[i][1];
    }
    if(scenario>=62 && scenario<64) {
        /* Visible chain from r2 photograph; index 255 is synthetic because
         * the screen printed only its first eight entries. */
        const uint32_t chain[][2]=CYCLE_CAPTURE_VALUES;
        for(unsigned i=0;i<sizeof(chain)/sizeof(chain[0]);i++)
            mem[2][(0x2000+4*chain[i][0])/4]=chain[i][1];
        mem[2][(0x2000+4*255)/4]=0x02000015;
    }
    if(scenario==63) fail_map=8;
    if(scenario==64) {
        mem[7][0]=PHOTO_SCR0;mem[7][0x20/4]=PHOTO_IDR0;
        mem[7][0x24/4]=PHOTO_IDR1;mem[7][0x28/4]=PHOTO_IDR2;
        memset((char *)mem[7]+0x800,0,0x800);
        const unsigned cb[]={0,1,2,2,4};
        for(unsigned i=0;i<5;i++) { mem[7][(0x800+4*i)/4]=0x80000100+i*16;mem[7][(0xc00+4*i)/4]=cb[i]; }
    }
    static uint32_t initial_smmu[0x100000/4],initial_sid[0x1000/4];
    memcpy(initial_smmu,mem[7],sizeof(initial_smmu));
    memcpy(initial_sid,(char *)mem[2]+0x2000,sizeof(initial_sid));
    static uint32_t initial_tcs0[0x2a0/4];memcpy(initial_tcs0,mem[6]+0xd00/4,sizeof(initial_tcs0));
    /* Preserve global registers separately; only own IRQ bit may change. */
    assert(munch_wifi_init());
    assert(enum_passed==((scenario==0 || (scenario>=20 && scenario<=22) || scenario==30 || scenario==31 || scenario>=42)?1u:0u));
    if(scenario>=20 && scenario<=22) assert(triggers==7);
    if(scenario==30 || scenario==31) assert(triggers==8 && cx_requests==1);
    if(scenario==32) assert(triggers==1 && cx_requests==1 && rh_poisoned);
    if(scenario==39 || scenario==40) assert(triggers==0 && cx_requests==0);
    if(scenario==40) assert(rh_poisoned);
    if(scenario==0) { assert(triggers==8 && writes>0);assert(!rh_poisoned); }
    if(scenario==1) { assert(triggers==1 && rh_poisoned);assert(!(mem[6][0xd00/4]&2)); }
    if(scenario==3 || scenario==4 || scenario==17) assert(triggers==8);
    if((scenario>=2 && scenario<20 && scenario!=3 && scenario!=4 && scenario!=17 && scenario!=18) || scenario==18 || (scenario>=23 && scenario<30 && scenario!=28) || (scenario>=33 && scenario<42 && scenario!=39 && scenario!=40))
        assert(writes==0 && triggers==0);
    bool cb_path=scenario==48 || scenario==42 || scenario==46 || scenario==47 || scenario==52 || scenario>=54;
    unsigned expected_maps=scenario>=42?(scenario==51?7u:8u):7u;
    if(cb_path) expected_maps+=cb_maps;
    assert(sid_passed==((scenario==42 || scenario==46 || scenario==47 || scenario==52 || scenario==57 || scenario==59 || scenario==61 || scenario==48 || scenario==62 || scenario==64)?1u:0u));
    if(scenario==64) assert(first_cb_read_pa==0x15083000 && cb_maps==4);
    if(back) assert(returns==1 && maps==(fail_map?fail_map:expected_maps) && unmaps==(fail_map?fail_map-1:expected_maps));
    else assert(returns==0 && maps==0);
    if(scenario<42) assert(smmu_reads==0 && sid_reads==0);
    else { assert(triggers==7);assert(scenario==51 || sid_reads>0); }
    assert(!memcmp(initial_smmu,mem[7],sizeof(initial_smmu)));
    assert(!memcmp(initial_sid,(char *)mem[2]+0x2000,sizeof(initial_sid)));
    assert(!(mem[1][(79*0x1000+4)/4]&2));assert(!(mem[1][(20*0x1000+4)/4]&2));
    assert(mem[3][4/4]==(triggers>=7?0xa5a50400u:0xa5a50000u));
    assert((mem[4][4/4]&0xffff0000u)==0xdead0000u); /* W1C status preserved */
    assert((mem[6][0xd00/4]&~2u)==0x81);
    if(scenario==28) { assert(triggers==0); initial_tcs0[0x38/4]=3; }
    if(scenario==39) initial_tcs0[0x4c/4]=3;
    if(scenario==40) initial_tcs0[0x30/4]=0x10000;
    assert(!memcmp((char *)initial_tcs0+0xc,(char *)mem[6]+0xd00+0xc,sizeof(initial_tcs0)-0xc));
    puts("PASS");
}
'''
photo = json.loads((here / 'results/smmu-survey-r1.json').read_text())
for key in ('scr0','idr0','idr1','idr2'):
    harness = harness.replace('PHOTO_' + key.upper(), photo['smmu'][key])
harness = harness.replace('PHOTO_CHAIN', '{' + ','.join(
    '{' + str(row['index']) + ',' + row['entry'] + '}' for row in photo['sid_chain']) + '}')
cycle_photo = json.loads((here / 'results/smmu-survey-r2.json').read_text())
harness = harness.replace('CYCLE_CAPTURE_VALUES', '{' + ','.join(
    '{' + str(row['index']) + ',' + row['entry'] + '}' for row in cycle_photo['sid_chain_visible']) + '}')
with tempfile.TemporaryDirectory(prefix='munch-enum-test-') as temp:
    work = Path(temp)
    (work / 'mach').mkdir(); (work / 'arm').mkdir()
    (work / 'mach/vm_param.h').write_text('/* harness PAGE_SIZE */\n')
    (work / 'arm/memory_types.h').write_text('/* harness WIMG */\n')
    (work / 'pexpert').mkdir()
    (work / 'pexpert/device_tree.h').write_text('/* harness types */\n')
    (work / 'test.c').write_text(harness)
    for vm_page_size in (4096, 16384):
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-DPAGE_SIZE=' + str(vm_page_size), '-I' + str(work), '-I' + str(here),
                        str(work / 'test.c'), '-o', str(work / 'test')], check=True)
        for scenario in range(65):
            result = subprocess.run([str(work / 'test'), str(scenario)], text=True, capture_output=True,
                                    env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
            if result.returncode:
                raise RuntimeError(f'scenario {scenario}: {result.stdout}{result.stderr}')
    print('PASS: 65 diagnostic scenarios at both 4/16-KiB VM pages with aligned reclaimable mapping/unmapping and CB3 offset coverage incl. SID cycle with continued SMMU/context observation including photographed SMMU headers/SID-chain replay, bounded context-bank reads/geometry/map failures and immutable fault/register state, read-only SID/SMMU geometry/masks/routes/map faults, including posted CX refresh/ack/timeout/snapshot races, retained CX with runtime level validation/recheck, RPMh completion/timeout, inherited conflicts, ARC aux, map failures, link/identity faults, no DMA/SID/BAR writes and reset cleanup (ASan/UBSan)')
