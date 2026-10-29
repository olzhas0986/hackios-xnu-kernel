/* Clean full-screen diagnostic summary. Full logs stay in the RAM payload.
 * Uses UEFI text output only; does not touch GOP mode or kernel graphics. */
struct munch_wifi_text_mode { int32_t max_mode, mode, attribute, column, row; uint8_t visible; };
struct munch_wifi_text_output {
    void *reset;
    efi_status (EFIAPI *output_string)(efi_text_output *, const char16 *);
    void *test_string;
    efi_status (EFIAPI *query_mode)(efi_text_output *, uint64_t, uint64_t *, uint64_t *);
    void *set_mode, *set_attribute;
    efi_status (EFIAPI *clear_screen)(efi_text_output *);
    efi_status (EFIAPI *set_cursor)(efi_text_output *, uint64_t, uint64_t);
    void *enable_cursor;
    struct munch_wifi_text_mode *mode;
};
struct munch_wifi_line { const char *text; uint64_t length; };
static struct munch_wifi_text_output *munch_wifi_out;
static unsigned munch_wifi_row, munch_wifi_limit, munch_wifi_columns, munch_wifi_omitted;

static int munch_wifi_prefix(struct munch_wifi_line line, const char *prefix)
{
    uint64_t n = 0;
    while (prefix[n]) n++;
    return line.length >= n && !memcmp(line.text, prefix, n);
}
static int munch_wifi_next_line(const char *log, uint64_t length, uint64_t *at, struct munch_wifi_line *line)
{
    static const char tag[] = "MUNCH Wi-Fi:";
    while (*at < length) {
        uint64_t start = *at, end = start;
        while (end < length && log[end] != '\n') end++;
        *at = end < length ? end + 1 : end;
        for (uint64_t i = start; i < end && end - i >= sizeof(tag) - 1; i++) {
            if (memcmp(log + i, tag, sizeof(tag) - 1)) continue;
            i += sizeof(tag) - 1;
            while (i < end && log[i] == ' ') i++;
            line->text = log + i; line->length = end - i;
            return 1;
        }
    }
    return 0;
}
static void munch_wifi_screen_line(struct munch_wifi_line line)
{
    if (munch_wifi_row >= munch_wifi_limit) { munch_wifi_omitted++; return; }
    char16 text[161];
    unsigned n = line.length < munch_wifi_columns ? (unsigned)line.length : munch_wifi_columns;
    for (unsigned i = 0; i < n; i++) {
        unsigned char c = (unsigned char)line.text[i];
        text[i] = c >= 32 && c < 127 ? c : ' ';
    }
    text[n] = 0;
    if (line.length > n && n >= 3) text[n-1] = text[n-2] = text[n-3] = '.';
    munch_wifi_out->set_cursor(ST->con_out, 0, munch_wifi_row++);
    munch_wifi_out->output_string(ST->con_out, text);
}
static void munch_wifi_literal(const char *text)
{
    struct munch_wifi_line line = {text, 0};
    while (text[line.length]) line.length++;
    munch_wifi_screen_line(line);
}
static void munch_wifi_last_field(const char *log, uint64_t length, const char *prefix)
{
    uint64_t at = 0;
    struct munch_wifi_line line, last = {0, 0};
    while (munch_wifi_next_line(log, length, &at, &line))
        if (munch_wifi_prefix(line, prefix)) last = line;
    if (last.text) munch_wifi_screen_line(last);
}
static int munch_wifi_show_summary(const char *log, uint64_t length)
{
    struct munch_wifi_text_output *out = (void *)ST->con_out;
    uint64_t cols = 0, rows = 0;
    if (!log || !out || !out->mode || !out->query_mode || !out->set_cursor ||
        !out->clear_screen || !out->output_string ||
        EFI_ERROR(out->query_mode(ST->con_out, out->mode->mode, &cols, &rows)) ||
        cols < 32 || cols > 1024 || rows < 20 || rows > 1024) return 0;
    uint64_t first_at = 0;
    struct munch_wifi_line first;
    if (!munch_wifi_next_line(log, length, &first_at, &first)) return 0;
    if (EFI_ERROR(out->clear_screen(ST->con_out))) return 0;
    munch_wifi_out = out;
    munch_wifi_columns = cols - 1 > 160 ? 160 : (unsigned)cols - 1;
    munch_wifi_limit = (unsigned)rows - 9; /* reserve restoration/footer rows and avoid firmware scroll */
    unsigned full_limit = munch_wifi_limit;
    munch_wifi_limit -= 2; /* last stage must stay visible even on a short console */
    munch_wifi_row = munch_wifi_omitted = 0;
    munch_wifi_literal("MUNCH WIFI / COMPACT SCREEN v1 / NO DMA");
    munch_wifi_literal("CB registers below. Photograph this screen.");
    munch_wifi_last_field(log, length, "PCIe endpoint PCI ID=");
    munch_wifi_last_field(log, length, "PCIe endpoint command");
    munch_wifi_last_field(log, length, "CB page=");
    /* CB raw values first; omit power transactions and routine inspect/map chatter. */
    uint64_t at = 0;
    struct munch_wifi_line line, last = {0, 0};
    while (munch_wifi_next_line(log, length, &at, &line)) {
        last = line;
        if (!munch_wifi_prefix(line, "CB") || line.length < 4 || line.text[2] < '0' || line.text[2] > '9') continue;
        uint64_t n = 2;
        while (n < line.length && line.text[n] >= '0' && line.text[n] <= '9') n++;
        while (n < line.length && line.text[n] == ' ') n++;
        struct munch_wifi_line tail = {line.text + n, line.length - n};
        if (munch_wifi_prefix(tail, "pa=") || munch_wifi_prefix(tail, "CBAR=") ||
            munch_wifi_prefix(tail, "TCR=") || munch_wifi_prefix(tail, "mapping failed") ||
            munch_wifi_prefix(tail, "contains all-ones")) munch_wifi_screen_line(line);
    }
    munch_wifi_last_field(log, length, "SMMU SCR0=");
    munch_wifi_last_field(log, length, "SMMU groups=");
    munch_wifi_last_field(log, length, "SID route found=");
    munch_wifi_last_field(log, length, "SID/SMMU survey ");
    munch_wifi_last_field(log, length, "enumeration ");
    /* SMRs are secondary to the CB values and result. */
    at = 0;
    while (munch_wifi_next_line(log, length, &at, &line))
        if (munch_wifi_prefix(line, "SMR")) munch_wifi_screen_line(line);
    munch_wifi_limit = full_limit;
    if (munch_wifi_row > full_limit - 2) munch_wifi_row = full_limit - 2;
    munch_wifi_literal("LAST KERNEL WIFI STAGE:");
    if (last.text) munch_wifi_screen_line(last);
    else munch_wifi_literal("No Wi-Fi-tagged lines. Consult full RAM log.");
    return 1;
}
