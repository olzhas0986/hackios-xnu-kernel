#!/usr/bin/env python3
"""Exercise the actual compact UEFI renderer with bounded mock text protocols."""
from pathlib import Path
import subprocess
import tempfile
import os
here=Path(__file__).resolve().parent
source=r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint16_t char16;
typedef uint64_t efi_status;
#define EFIAPI
#define EFI_ERROR(x) ((x)!=0)
typedef struct efi_text_output efi_text_output;
static struct { efi_text_output *con_out; } system_table;
#define ST (&system_table)
#include "wifi-screen-summary.inc"
static char screen[100][161];
static unsigned cols,rows,x,y,clears,cursor_calls;
static efi_status cursor(efi_text_output *p,uint64_t c,uint64_t r) {
    (void)p;assert(c<cols && r<rows-9);x=c;y=r;cursor_calls++;return 0;
}
static efi_status output(efi_text_output *p,const char16 *s) {
    (void)p;for(unsigned i=0;s[i];i++) { assert(x<cols-1 && x<160);screen[y][x++]=(char)s[i]; }
    return 0;
}
static efi_status query(efi_text_output *p,uint64_t mode,uint64_t *c,uint64_t *r) {
    (void)p;(void)mode;*c=cols;*r=rows;return 0;
}
static efi_status clear(efi_text_output *p) { (void)p;memset(screen,0,sizeof(screen));clears++;return 0; }
static int contains(const char *s) { for(unsigned r=0;r<rows;r++) if(strstr(screen[r],s)) return 1;return 0; }
int main(void) {
    struct munch_wifi_text_mode mode={0};
    struct munch_wifi_text_output out={.output_string=output,.query_mode=query,.clear_screen=clear,.set_cursor=cursor,.mode=&mode};
    ST->con_out=(void *)&out;
    const char log[]="PANIC irrelevant background\nMUNCH Wi-Fi: RPMh vote minimum=950 mV\n"
        "MUNCH Wi-Fi: PCIe endpoint PCI ID=0x110117cb\n"
        "MUNCH Wi-Fi: PCIe endpoint command (DMA disabled)=0x400\n"
        "MUNCH Wi-Fi: CB page=0x1000 base=0x15080000 VM-page=0x4000\n"
        "MUNCH Wi-Fi: CB3 inspect GR1 offsets=0x100c/0x180c\n"
        "MUNCH Wi-Fi: CB3 map pa=0x15083000 CBAR=0 CBA2R=0\n"
        "MUNCH Wi-Fi: CB3 pa=0x15083000 SCTLR=0 FSR=0\n"
        "MUNCH Wi-Fi: CB3 CBAR=0 CBA2R=0 TTBR0=0:0\n"
        "MUNCH Wi-Fi: CB3 TCR=0 TCR2=0; observation only\n"
        "MUNCH Wi-Fi: SMR0=0x800004e0 S2CR=0\n"
        "MUNCH Wi-Fi: SID route found=0 cycle=1; DMA NOT APPROVED\n"
        "MUNCH Wi-Fi: CB9 inspect GR1";
    unsigned widths[]={32,80,140},heights[]={20,40,80};
    for(unsigned i=0;i<3;i++) for(unsigned j=0;j<3;j++) {
        cols=widths[i];rows=heights[j];clears=cursor_calls=0;
        assert(munch_wifi_show_summary(log,sizeof(log)-1));
        assert(clears==1 && cursor_calls>0);
        assert(contains("CB3 pa="));assert(contains("CB9 inspect GR1"));
        assert(!contains("minimum=") && !contains("PANIC irrelevant"));
        assert(!contains("CB3 inspect") && !contains("CB3 map"));
        if(rows>=40) assert(contains("TTBR0=0:0") && contains("SID route found="));
    }
    cols=80;rows=40;clears=0;
    assert(!munch_wifi_show_summary("untagged panic",14) && clears==0);
    assert(!munch_wifi_show_summary(NULL,0));
    cols=10;assert(!munch_wifi_show_summary(log,sizeof(log)-1));
    cols=80;out.clear_screen=NULL;assert(!munch_wifi_show_summary(log,sizeof(log)-1));out.clear_screen=clear;
    char large[3000];memset(large,'x',sizeof(large));
    memcpy(large,"MUNCH Wi-Fi: CB3 pa=",19);
    assert(munch_wifi_show_summary(large,sizeof(large)));
    puts("PASS: compact screen clearing, nine geometries, bounded columns/rows, CB priority, last-stage retention, noise removal and firmware fallback (ASan/UBSan)");
}
'''
with tempfile.TemporaryDirectory(prefix='munch-wifi-screen-') as temp:
    work=Path(temp);(work/'test.c').write_text(source)
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                    '-I'+str(here),str(work/'test.c'),'-o',str(work/'test')],check=True)
    subprocess.run([str(work/'test')],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
