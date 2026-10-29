#!/usr/bin/env python3
"""Exercise the actual USB export command parser and transfer framing under sanitizers."""
from pathlib import Path
import subprocess
import os
import tempfile
here=Path(__file__).resolve().parent
helper=(here/'wifi-usb-log.inc').read_text()
commands=helper[helper.index('static int munch_usb_log_reply'):helper.index('static int munch_usb_log_server')]
pre=r'''
#include <stdint.h>
#include <string.h>
#include <assert.h>
#define USB_CHUNK 65536
static uint8_t buffer[USB_CHUNK], captured[3*USB_CHUNK];
static uint8_t *usb_tx_buf=buffer;
static unsigned used,calls,fail_at;
static int munch_usb_log_tx_failed;
static const char *last_result="returned";
static int str_eq(const char*a,const char*b){return !strcmp(a,b);}
static int64_t usb_tx(const uint8_t*p,uint64_t n){
 assert(n<=USB_CHUNK);calls++;if(calls==fail_at)return -1;
 assert(used+n<=sizeof(captured));memcpy(captured+used,p,n);used+=n;return n;
}
static void reset(void){used=calls=fail_at=munch_usb_log_tx_failed=0;}
'''
post=r'''
int main(void){
 static char log[USB_CHUNK+17];memset(log,'L',sizeof(log));
 const char *denied[]={"flash:boot","download:00001000","oem run","boot","erase:userdata","upload extra","getvar:product extra"};
 for(unsigned i=0;i<sizeof(denied)/sizeof(*denied);i++){
 reset();assert(!munch_usb_log_command(denied[i],log,sizeof(log)));assert(!memcmp(captured,"FAIL",4));assert(calls==1);
 }
 reset();munch_usb_log_command("getvar:product",log,sizeof(log));assert(used==18&&!memcmp(captured,"OKAYmunch-wifi-log",18));
 reset();munch_usb_log_command("oem log",log,sizeof(log));assert(log[0]=='L');assert(!memcmp(captured,"OKAY",4));
 reset();munch_usb_log_command("upload",log,sizeof(log));assert(calls==4);assert(!memcmp(captured,"DATA00010011",12));assert(!memcmp(captured+12,log,sizeof(log)));assert(!memcmp(captured+12+sizeof(log),"OKAY",4));
 for(unsigned i=1;i<=4;i++){reset();fail_at=i;munch_usb_log_command("upload",log,sizeof(log));assert(munch_usb_log_tx_failed);assert(calls==i);}
 reset();assert(munch_usb_log_command("reboot",log,sizeof(log))==1);
 reset();fail_at=1;assert(!munch_usb_log_command("reboot",log,sizeof(log)));
 return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
 src=Path(tmp)/'check.c';binary=Path(tmp)/'check';src.write_text(pre+commands+post)
 subprocess.run(['cc','-fsanitize=address,undefined','-fno-omit-frame-pointer','-g',str(src),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True,env={**os.environ,"ASAN_OPTIONS":"detect_leaks=0"})
print('PASS: USB allowlist, DATA framing, chunk boundaries, partial-transfer failures and reboot acknowledgement')
