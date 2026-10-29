#!/usr/bin/env python3
"""Test actual opt-in CPU memory preflight with both VM page sizes."""
import os
from pathlib import Path
import subprocess
import tempfile
here=Path(__file__).resolve().parent
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdio.h>
typedef uintptr_t vm_offset_t;
#define KMA_KOBJECT 1
#define KMA_ZERO 2
#define KMA_DATA 4
#define VM_KERN_MEMORY_IOKIT 0
#define KERN_SUCCESS 0
#define kernel_map 0
static unsigned requested=1,scenario,allocs,frees;
static uint64_t physical=0xc1000000;
static uintptr_t allocated;
static int kmem_alloc(int map,vm_offset_t*v,unsigned n,int f,int t){
 (void)map;(void)t;assert(n==PAGE_SIZE && f==7);allocs++;
 if(scenario==1)return 1;
 allocated=(uintptr_t)aligned_alloc(PAGE_SIZE,PAGE_SIZE);assert(allocated);
 memset((void*)allocated,scenario==4?0x55:0,PAGE_SIZE);*v=allocated;return 0;
}
static void kmem_free(int map,vm_offset_t v,unsigned n){
 (void)map;assert(v==allocated && n==PAGE_SIZE);frees++;free((void*)v);
}
static uint64_t kvtophys(vm_offset_t v){
 return physical+(v-allocated)+((scenario==3 && v!=allocated)?0x1000:0);
}
static void PE_parse_boot_argn(const char*k,void*v,unsigned n){assert(n==4&&!strcmp(k,"munch_wifi_memory"));*(uint32_t*)v=requested;}
static void hakios_tl(const char *fmt,...){(void)fmt;}
#include "munch-wifi-memory.inc"
int main(int argc,char**argv){
 assert(argc==2);scenario=atoi(argv[1]);
 assert(!munch_wifi_memory_range(UINT64_MAX-4095,8192));
 assert(!munch_wifi_memory_range(0xc1000001,4096));
 assert(!munch_wifi_memory_range(0xc1000000,0));
 assert(!munch_wifi_memory_range(0x15000000,4096));
 for(unsigned i=0;i<sizeof(munch_wifi_reserved)/sizeof(*munch_wifi_reserved);i++){
  uint64_t start=munch_wifi_reserved[i][0]&~4095ULL;
  assert(!munch_wifi_memory_range(start,4096));
 }
 if(scenario==2)physical=0x80860000;
 if(scenario==5)physical=0x15000000;
 if(scenario==6)physical=0x1fffff000ULL;
 if(scenario==7)requested=0;
 if(scenario==8)requested=2;
 if(scenario==9)physical=0xc1000001;
 bool ok=munch_wifi_memory_preflight();
 assert(ok==(scenario==0||scenario==7));
 assert(allocs==(scenario==7||scenario==8?0:1));
 assert(frees==(scenario==1||scenario==7||scenario==8?0:1));
 return 0;
}
'''
with tempfile.TemporaryDirectory() as d:
 src=Path(d)/'test.c';binary=Path(d)/'test';src.write_text(code)
 for size in (4096,16384):
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',f'-DPAGE_SIZE={size}','-I'+str(here),str(src),'-o',str(binary)],check=True)
  for scenario in range(10):
   # A 4K allocation has no second subpage, and fits at the RAM upper edge.
   if size==4096 and scenario in (3,6):continue
   subprocess.run([str(binary),str(scenario)],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
print('PASS: opt-in gates, allocation failure, reservations, RAM bounds, physical continuity, alignment, dirty pages and release (4/16K ASan/UBSan)')
