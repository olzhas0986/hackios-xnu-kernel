/* Post-return, read-only fastboot log export. No general command dispatcher,
 * partition access, downloads, new kernel execution or kernel-side USB DMA. */
static int munch_usb_log_active;
static uint64_t munch_usb_log_deadline;
static int munch_usb_log_tx_failed;

static void munch_usb_log_stop(void)
{
    if (!USB) return;
    if (USB->abort_xfer) { USB->abort_xfer(USB_EP_OUT); USB->abort_xfer(USB_EP_IN); }
    if (USB->stop) USB->stop();
}

static int munch_usb_log_reply(const char *code, const char *text)
{
    unsigned n = 0;
    while (*code && n < 64) usb_tx_buf[n++] = (uint8_t)*code++;
    while (*text && n < 64) usb_tx_buf[n++] = (uint8_t)*text++;
    int ok = usb_tx(usb_tx_buf, n) == (int64_t)n;
    if (!ok) munch_usb_log_tx_failed = 1;
    return ok;
}
static void munch_usb_log_hex(char *text, uint64_t value)
{
    for (unsigned i = 0; i < 8; i++) text[i] = "0123456789abcdef"[(value >> (28-4*i)) & 15];
    text[8] = 0;
}
static int munch_usb_log_upload(const char *data, uint64_t length)
{
    char message[13] = "DATA00000000";
    munch_usb_log_hex(message+4, length);
    if (usb_tx((const uint8_t *)message, 12) != 12) { munch_usb_log_tx_failed=1;return 0; }
    for (uint64_t at = 0; at < length;) {
        uint64_t n = length-at > USB_CHUNK ? USB_CHUNK : length-at;
        if (usb_tx((const uint8_t *)data+at, n) != (int64_t)n) { munch_usb_log_tx_failed=1;return 0; }
        at += n;
    }
    return munch_usb_log_reply("OKAY", "");
}
/* Return 1 only after a successfully acknowledged reboot request. */
static int munch_usb_log_command(const char *cmd, const char *data, uint64_t length)
{
    if (str_eq(cmd,"getvar:product")) munch_usb_log_reply("OKAY","munch-wifi-log");
    else if (str_eq(cmd,"getvar:version")) munch_usb_log_reply("OKAY","0.1");
    else if (str_eq(cmd,"getvar:result")) munch_usb_log_reply("OKAY",last_result);
    else if (str_eq(cmd,"getvar:log-size")) {
        char size[11]="0x00000000";
        munch_usb_log_hex(size+2,length);
        munch_usb_log_reply("OKAY",size);
    } else if (str_eq(cmd,"getvar:max-download-size")) munch_usb_log_reply("OKAY","0");
    else if (str_eq(cmd,"upload")) munch_usb_log_upload(data,length);
    else if (str_eq(cmd,"oem log")) munch_usb_log_reply("OKAY","frozen kernel log staged");
    else if (str_eq(cmd,"reboot")) return munch_usb_log_reply("OKAY","");
    else munch_usb_log_reply("FAIL","read-only log export; command disabled");
    return 0;
}
static int munch_usb_log_server(void)
{
    /* write_logs() has already staged the full returned log in out_buf.
     * Never call compose_logs(NULL,0), which would discard the kernel log. */
    if (staged_data != out_buf || !staged_len || staged_len > sizeof(out_buf)) {
        print("MUNCH USB: no bounded RAM log staged; screen fallback\n");
        return 0;
    }
    const char *snapshot = staged_data;
    uint64_t length = staged_len;
    uint64_t freq = READ_SYSREG(CNTFRQ_EL0);
    if (!freq || freq > 1000000000ULL) return 0;
    munch_usb_log_deadline = READ_SYSREG(CNTVCT_EL0) + 300 * freq;
    munch_usb_log_active = 1;
    if (!usb_start()) { munch_usb_log_stop();munch_usb_log_active = 0; return 0; }
    print("MUNCH USB LOG READY: read-only fastboot, 300-second window\n");
    print("Host: fastboot -s xnuloader getvar product\n");
    print("Host: fastboot -s xnuloader get_staged wifi-log.txt\n");
    int pending = 0;
    uint64_t last_pet = READ_SYSREG(CNTVCT_EL0);
    munch_usb_log_tx_failed = 0;
    while (READ_SYSREG(CNTVCT_EL0) < munch_usb_log_deadline) {
        if (usb_connected && !pending) {
            if (EFI_ERROR(USB->send(USB_EP_OUT,511,usb_rx_buf))) break;
            pending = 1;
        }
        uint32_t msg = UsbDeviceEventNoEvent;
        uint64_t bytes = 0;
        usb_device_event_data event;
        if (EFI_ERROR(USB->handle_event(&msg,&bytes,&event))) break;
        if (msg == UsbDeviceEventDeviceStateChange) {
            usb_connected = event.device_state == UsbDeviceStateConnected;
            pending = 0;
        } else if (msg == UsbDeviceEventTransferNotification && event.transfer.endpoint_index == USB_EP_OUT) {
            pending = 0;
            uint64_t n = event.transfer.bytes_completed;
            if (event.transfer.status != UsbDeviceTransferStatusCompleteOK || !n || n > 511) continue;
            int embedded_zero = 0;
            for (uint64_t i=0;i<n;i++) embedded_zero |= usb_rx_buf[i] == 0;
            usb_rx_buf[n] = 0;
            if (embedded_zero) { munch_usb_log_reply("FAIL","invalid command bytes"); continue; }
            if (munch_usb_log_command((const char *)usb_rx_buf,snapshot,length)) {
                munch_usb_log_active = 0;
                return 1;
            }
            /* usb_wait() consumes disconnect events while transmitting. */
            if (!usb_connected) { pending = 0;munch_usb_log_tx_failed=0; }
            if (munch_usb_log_tx_failed) break;
        }
        if (READ_SYSREG(CNTVCT_EL0) - last_pet > freq) {
            last_pet = READ_SYSREG(CNTVCT_EL0);
            wdt_pet();
        }
        BS->stall(100);
    }
    munch_usb_log_stop();
    munch_usb_log_active = 0;
    print("MUNCH USB LOG: session ended; screen log retained\n");
    return 0;
}
