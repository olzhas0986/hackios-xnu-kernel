#!/usr/bin/env python3
"""Exercise the actual survey's board/boot gates and map failures with fake MMIO."""
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
#include <stdarg.h>
#include <string.h>
typedef uintptr_t vm_offset_t;
typedef int DTEntry;
#define PAGE_SIZE 0x4000u
#define VM_WIMG_IO 7u
#define kSuccess 0
#define __printflike(a,b)
static unsigned maps, unmaps, returns, step, back, enabled, failure;
static unsigned board_size = 6;
static const char *board_name = "munch";
static int dt_error;
static uint32_t gcc[0x80000/4] __attribute__((aligned(0x4000))), gpio[0x80000/4] __attribute__((aligned(0x4000)));
static uint32_t db[0x20000/4] __attribute__((aligned(0x4000))), rsc[0x10000/4] __attribute__((aligned(0x4000)));
static int SecureDTLookupEntry(void *p, const char *path, DTEntry *e) {
    (void)p; assert(!strcmp(path,"/arm-io")); *e=1; return dt_error;
}
static int SecureDTGetProperty(DTEntry e, const char *key, const void **b, unsigned *s) {
    assert(e==1 && !strcmp(key,"device_type")); *b=board_name; *s=board_size; return dt_error;
}
static void PE_parse_boot_argn(const char *key, void *value, unsigned n) {
    assert(n==4);
    *(uint32_t *)value = !strcmp(key,"munch_wifi_step") ? step :
        !strcmp(key,"munch_wifi_return") ? back : !strcmp(key,"wifi") ? enabled : 0;
}
static vm_offset_t ml_io_map_unmappable(vm_offset_t address, unsigned size, unsigned flags) {
    assert(flags==VM_WIMG_IO);maps++;
    vm_offset_t result=0;
    if(address==0x100000) { assert(size==sizeof(gcc)); result=(uintptr_t)gcc; }
    if(address==0xf900000) { assert(size==sizeof(gpio)); result=(uintptr_t)gpio; }
    if(address==0x80860000) { assert(size==sizeof(db)); result=(uintptr_t)db; }
    if(address==0x18220000) { assert(size==sizeof(rsc)); result=(uintptr_t)rsc; }
    assert(result);
    return maps==failure ? 0 : result;
}
static void ml_io_unmap(vm_offset_t address, unsigned size) {
    assert((address==(uintptr_t)gcc && size==sizeof(gcc)) ||
           (address==(uintptr_t)gpio && size==sizeof(gpio)) ||
           (address==(uintptr_t)db && size==sizeof(db)) ||
           (address==(uintptr_t)rsc && size==sizeof(rsc))); unmaps++;
}
void hakios_tl(const char *fmt, ...) { (void)fmt; }
static void loader_return(void) { returns++; }
static unsigned enum_calls;
static bool munch_wifi_enumeration(void) { enum_calls++; return false; }
#include "munch-wifi-survey.inc"
static void reset(void) {
    maps=unmaps=returns=step=back=enabled=failure=0;
    board_name="munch";board_size=6;dt_error=0;
    memset(gcc,0xa5,sizeof(gcc)); memset(gpio,0x5a,sizeof(gpio));
}

static void valid_db(void) {
    memset(db,0,sizeof(db));
    db[0]=1;db[1]=0x0c0330db;
    /* VRM bank, entries offset 0, aux offset 0x100, two entries. */
    db[2]=4;db[3]=0x00020100;
    memcpy((char *)db+144,"smpa6",5); db[(144+16)/4]=0x40000;
    memcpy((char *)db+168,"smpa6",5); db[(168+16)/4]=0x40000;
}
static void power_tests(void) {
    uint32_t address=0;
    valid_db(); assert(munch_wifi_db_lookup((uintptr_t)db,sizeof(db),"smpa6",&address));
    assert(address==0x40000); /* identical aliases accepted */
    assert(!munch_wifi_db_lookup((uintptr_t)db,sizeof(db),"unknown",&address));
    assert(!munch_wifi_db_lookup((uintptr_t)db,143,"smpa6",&address));
    assert(!munch_wifi_db_lookup((uintptr_t)db,sizeof(db),"123456789",&address));
    db[(168+16)/4]=0x40010; /* conflicting alias */
    assert(!munch_wifi_db_lookup((uintptr_t)db,sizeof(db),"smpa6",&address));
    valid_db();db[1]=0;assert(!munch_wifi_db_lookup((uintptr_t)db,sizeof(db),"smpa6",&address));
    valid_db();db[0]=2;assert(!munch_wifi_db_lookup((uintptr_t)db,sizeof(db),"smpa6",&address));
    valid_db();db[140/4]=1;assert(!munch_wifi_db_lookup((uintptr_t)db,sizeof(db),"smpa6",&address));
    valid_db();db[3]=0xffff0100;assert(!munch_wifi_db_lookup((uintptr_t)db,sizeof(db),"smpa6",&address));
    valid_db();db[2]=0x00010004;assert(!munch_wifi_db_lookup((uintptr_t)db,sizeof(db),"smpa6",&address));
    valid_db();db[(144+16)/4]=0x30000;assert(!munch_wifi_db_lookup((uintptr_t)db,sizeof(db),"smpa6",&address));
    valid_db();db[(144+20)/4]=0xffffffff;assert(!munch_wifi_db_lookup((uintptr_t)db,sizeof(db),"smpa6",&address));
    for(unsigned f=0;f<=2;f++) {
        reset();valid_db();memset(rsc,0,sizeof(rsc));
        rsc[0]=1<<16;rsc[3]=(8<<12)|(4u<<27);
        static uint32_t saved_db[sizeof(db)/4],saved_rsc[sizeof(rsc)/4];
        memcpy(saved_db,db,sizeof(db));memcpy(saved_rsc,rsc,sizeof(rsc));
        step=2;back=1;failure=f;assert(munch_wifi_init());
        assert(maps==2 && unmaps==(f?1u:2u) && returns==1);
        assert(!memcmp(saved_db,db,sizeof(db)) && !memcmp(saved_rsc,rsc,sizeof(rsc)));
    }
    reset();memset(db,0,sizeof(db));memset(rsc,0,sizeof(rsc));
    db[0]=1;db[1]=0x0c0330db;db[2]=4;db[3]=(7u<<16)|0x400;
    const char *names[]={"smpa6","smpa4","smpa5","smpc8","ldoa9","ldoa5","ldoa16"};
    for(unsigned i=0;i<7;i++) {
        memcpy((char *)db+144+i*24,names[i],strlen(names[i]));
        db[(144+i*24+16)/4]=0x40000+i*16;
    }
    db[6]=(0x200u<<16)|3;db[7]=(1u<<16)|0x400;
    memcpy((char *)db+144+0x200,"cx.lvl",6);db[(144+0x200+16)/4]=0x30000;
    rsc[0]=1<<16;rsc[3]=(8<<12)|(4u<<27);
    assert(munch_wifi_power_survey() && maps==2 && unmaps==2);
    db[1]=0;assert(!munch_wifi_power_survey());
    reset();step=2;assert(munch_wifi_init() && maps==0 && returns==0);
    reset();valid_db();rsc[0]=0xffffffff;step=2;back=1;
    assert(munch_wifi_init() && maps==2 && unmaps==2 && returns==1);
}
/* Replay photographed values in a synthetic DB. Entry ordering and bank
 * metadata are fabricated: the photograph contains resolved addresses only. */
static void photographed_power_test(void) {
    reset(); memset(db,0,sizeof(db)); memset(rsc,0,sizeof(rsc));
    db[0]=PHOTO_VERSION; db[1]=PHOTO_MAGIC; db[140/4]=PHOTO_FLAGS;
    db[2]=4; db[3]=(7u<<16)|0x400;
    const char *names[]={"smpa6","smpa4","smpa5","smpc8","ldoa9","ldoa5","ldoa16"};
    const uint32_t addresses[]=PHOTO_ADDRESSES;
    for(unsigned i=0;i<7;i++) {
        memcpy((char *)db+144+i*24,names[i],strlen(names[i]));
        db[(144+i*24+16)/4]=addresses[i];
    }
    db[6]=(0x200u<<16)|3; db[7]=(1u<<16)|0x400;
    memcpy((char *)db+144+0x200,"cx.lvl",6);
    db[(144+0x200+16)/4]=PHOTO_CX;
    rsc[0]=PHOTO_ID; rsc[3]=PHOTO_CONFIG; rsc[0xd04/4]=PHOTO_IRQ;
    const uint32_t slots[8][4]=PHOTO_SLOTS;
    for(unsigned i=0;i<8;i++) {
        unsigned off=(0xd00+i*0x2a0)/4;
        rsc[off+0x14/4]=slots[i][0]; rsc[off+0x18/4]=slots[i][1];
        rsc[off+0x1c/4]=slots[i][2]; rsc[off+0x10/4]=slots[i][3];
    }
    static uint32_t saved_db[sizeof(db)/4], saved_rsc[sizeof(rsc)/4];
    memcpy(saved_db,db,sizeof(db)); memcpy(saved_rsc,rsc,sizeof(rsc));
    uint32_t address=0;
    assert(!munch_wifi_db_lookup((uintptr_t)db,sizeof(db),"smpf2",&address));
    assert(munch_wifi_power_survey());
    assert(maps==2 && unmaps==2);
    assert(!memcmp(saved_db,db,sizeof(db)) && !memcmp(saved_rsc,rsc,sizeof(rsc)));
    /* Every required resource must still be mandatory with this hardware state. */
    for(unsigned i=0;i<8;i++) {
        unsigned off=i<7 ? 144+i*24 : 144+0x200;
        uint32_t saved=db[off/4]; db[off/4]=0;
        assert(!munch_wifi_power_survey()); db[off/4]=saved;
    }
}
int main(void) {
    power_tests();
    photographed_power_test();
    reset(); board_name="umi";board_size=4; assert(!munch_wifi_init() && maps==0);
    reset(); dt_error=1; assert(!munch_wifi_init() && maps==0);
    reset(); board_size=5; assert(!munch_wifi_init() && maps==0);
    reset(); assert(munch_wifi_init() && maps==0);
    reset(); enabled=1; assert(munch_wifi_init() && maps==0);
    reset(); step=1; assert(munch_wifi_init() && maps==0);
    reset(); step=5;back=1; assert(munch_wifi_init() && maps==0);
    reset(); step=3; assert(munch_wifi_init() && enum_calls==0);
    back=1; assert(munch_wifi_init() && enum_calls==1 && returns==1);
    reset(); step=1;back=1; assert(munch_wifi_init());
    assert(maps==2 && unmaps==2 && returns==1);
    for(unsigned i=0;i<sizeof(gcc)/4;i++) {
        assert(gcc[i]==0xa5a5a5a5 && gpio[i]==0x5a5a5a5a);
    }
    for(unsigned f=1;f<=2;f++) {
        reset();step=1;back=1;failure=f;assert(munch_wifi_init());
        assert(maps==2 && unmaps==1 && returns==1);
    }
    puts("PASS: board and boot gates, invalid stage, read-only MMIO, CMD DB bounds/aliases, RSC guards and mapping failures");
}
'''
photo = json.loads((here / 'results/power-survey-r1.json').read_text())
values = {
    'PHOTO_VERSION': photo['cmd_db']['version'],
    'PHOTO_MAGIC': photo['cmd_db']['magic'],
    'PHOTO_FLAGS': photo['cmd_db']['flags'],
    'PHOTO_ID': photo['rsc']['id'], 'PHOTO_CONFIG': photo['rsc']['config'],
    'PHOTO_IRQ': photo['rsc']['irq_status'], 'PHOTO_CX': photo['resources']['cx.lvl'],
    'PHOTO_ADDRESSES': '{' + ','.join(photo['resources'][n] for n in
        ('smpa6','smpa4','smpa5','smpc8','ldoa9','ldoa5','ldoa16')) + '}',
    'PHOTO_SLOTS': '{' + ','.join('{' + ','.join(row[k] for k in
        ('ctl','idle','en','wait')) + '}' for row in photo['slots']) + '}',
}
for key, value in values.items():
    harness = harness.replace(key, value)
with tempfile.TemporaryDirectory(prefix='munch-wifi-test-') as temp:
    work = Path(temp)
    (work / 'mach').mkdir(); (work / 'arm').mkdir()
    (work / 'mach/vm_param.h').write_text('/* harness PAGE_SIZE */\n')
    (work / 'arm/memory_types.h').write_text('/* harness WIMG */\n')
    (work / 'pexpert').mkdir()
    (work / 'pexpert/device_tree.h').write_text('/* Types supplied by test harness. */\n')
    (work / 'munch-wifi-survey.inc').write_bytes((here / 'munch-wifi-survey.inc').read_bytes())
    (work / 'test.c').write_text(harness)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-I' + str(work),
                    str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True,
                   env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
