/*
 * Munch/Kona command-mode scan synchronization, adapted from
 * JopangJovan/hackios-xnu-kernel-umi devices/xiaomi-mi-10/overlays/xnu/files/
 * bsd/dev/hakios.display.c. Same license as the upstream XNU driver (APSL 2.0).
 * Included in MiniIOMobileFramebuffer.cpp to keep the device overlay isolated.
 * Only register-bus clock enables are written; panel timing is inherited.
 */
#ifndef MUNCH_SCAN_TEST
#include <machine/machine_routines.h>
extern "C" vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
static inline uint32_t rd(vm_offset_t base, uint32_t offset)
{
    return *(volatile uint32_t *)(base + offset);
}
static inline void wr(vm_offset_t base, uint32_t offset, uint32_t value)
{
    __asm__ volatile("dmb oshst" ::: "memory");
    *(volatile uint32_t *)(base + offset) = value;
    __asm__ volatile("dsb osh" ::: "memory");
}
#endif
/* COPY HISTORY: recent measured costs, never a lifetime high-water floor. */
static struct { uint64_t at, ns; } gCopyHistory[128];
static uint32_t gCopyHistoryNext;
static void munch_copy_observe(uint64_t now_ns, uint64_t copy_ns)
{
    if (!copy_ns) return;
    uint32_t i = gCopyHistoryNext++ % 128;
    gCopyHistory[i].at = now_ns;
    gCopyHistory[i].ns = copy_ns;
}
static uint64_t munch_copy_budget(uint64_t now_ns)
{
    uint64_t peak = 0;
    for (unsigned i = 0; i < 128; i++)
        if (gCopyHistory[i].ns && now_ns >= gCopyHistory[i].at &&
            now_ns - gCopyHistory[i].at < 1000000000ULL && gCopyHistory[i].ns > peak)
            peak = gCopyHistory[i].ns;
    return peak;
}
/* END COPY HISTORY */

/* Actual newly composed frames copied to the front, excluding probes/HUD. */
static uint64_t gPresentedAt[128];
static uint32_t gPresentedNext;
static void munch_presented(uint64_t now_ns)
{
    gPresentedAt[gPresentedNext++ % 128] = now_ns;
}
static uint32_t munch_present_fps(uint64_t now_ns)
{
    uint32_t count = 0;
    for (unsigned i = 0; i < 128; i++)
        if (gPresentedAt[i] && now_ns >= gPresentedAt[i] &&
            now_ns - gPresentedAt[i] < 1000000000ULL) count++;
    return count;
}

#define GCC_DISP_AHB_CBCR 0x0b00c
#define DISPCC_MDSS_CORE_GDSCR 0x3000
#define DISPCC_MDSS_AHB_CBCR 0x2080
#define CBCR_ENABLE 1u
#define CBCR_OFF (1u << 31)
#define GDSC_POWER_ON (1u << 31)
struct hakios_display_scan {
    int ok;
    uint32_t lines, shown, millihz;
    uint64_t line_ns, frame_ns;
};
static vm_offset_t munch_intf, gcc_map, dispcc_map;
static struct hakios_display_scan scan;
static uint32_t te_initial, te_idle_start;
/* Reason codes are shown by the opt-in HUD even on a quiet boot. */
static uint32_t gScanReason = 1, gTEConfig, gTEAuto, gPhaseMax;
static int hakios_display_command_mode(void) { return scan.ok; }
static const struct hakios_display_scan *hakios_display_scan(void) { return scan.ok ? &scan : NULL; }

static int
regs_alive(void)
{
	uint32_t v = rd(gcc_map, GCC_DISP_AHB_CBCR & 0xfffu);
	if (v == UINT32_MAX || !(v & CBCR_ENABLE) || (v & CBCR_OFF)) return 0;
	v = rd(dispcc_map, DISPCC_MDSS_CORE_GDSCR);
	if (v == UINT32_MAX || !(v & GDSC_POWER_ON)) return 0;
	v = rd(dispcc_map, DISPCC_MDSS_AHB_CBCR);
	return v != UINT32_MAX && (v & CBCR_ENABLE) && !(v & CBCR_OFF);
}

static int
clock_on(vm_offset_t base, uint32_t cbcr, const char *name)
{
	uint32_t v = rd(base, cbcr);
	if (v == UINT32_MAX) return 0;

	if (!(v & CBCR_ENABLE) || (v & CBCR_OFF)) {
		wr(base, cbcr, v | CBCR_ENABLE);
		delay_for_interval(10, NSEC_PER_MSEC);
		v = rd(base, cbcr);
	}
	if (!(v & CBCR_ENABLE) || (v & CBCR_OFF)) {
		hakios_tl("display: the %s clock does not take (%#x): no scan to go by\n", name, v);
		return 0;
	}
	return 1;
}

static uint32_t
hakios_display_line(uint32_t *frame)
{
    if (frame != NULL) *frame = 0;
    if (!scan.ok) return 0;
    if (!regs_alive()) {
        scan.ok = 0; gScanReason = 13;
        hakios_tl("display: munch scan lost power/clock; presentation suspended\n");
        return 0;
    }
    uint32_t raw = rd(munch_intf, 0x298) & 0xffff;
    if (raw < te_initial || raw - te_initial > gPhaseMax + 32) {
        scan.ok = 0; gScanReason = 14;
        hakios_tl("display: munch TE phase out of calibration; presentation suspended\n");
        return 0;
    }
    return raw - te_initial;
}

static int
hakios_display_wait_copy(uint64_t copy_ns, uint64_t limit_ns)
{
	uint64_t start = mach_absolute_time(), elapsed;
	if (!hakios_display_command_mode()) return 0;
	if (!copy_ns || copy_ns >= scan.frame_ns) return 0;
	uint64_t budget = copy_ns + copy_ns / 4 + 500000;
	for (;;) {
		uint64_t sample_start = mach_absolute_time(), sample_ns;
		uint32_t before = hakios_display_line(NULL);
		if (!scan.ok) return 0;
		uint32_t written = rd(munch_intf, 0x2b0) & 0xffff;
		uint32_t phase = hakios_display_line(NULL);
		if (!scan.ok) return 0;
		absolutetime_to_nanoseconds(mach_absolute_time() - sample_start, &sample_ns);
		uint64_t used = (uint64_t)phase * scan.line_ns;
		/* A completed DMA stays idle until the next TE. Restricting admission
		 * to the first 128 lines of that idle interval made a frame arriving
		 * slightly later wait an entire refresh, even with ample copy time.
		 * Use the measured copy budget instead. Read phase around the DMA
		 * status so a TE wrap cannot pair an old completion with a new frame.
		 */
		if (sample_ns < scan.line_ns * 8 && phase >= before && phase >= te_idle_start && written >= scan.shown &&
		    used < scan.frame_ns && budget < scan.frame_ns - used) return 1;
		absolutetime_to_nanoseconds(mach_absolute_time() - start, &elapsed);
		if (elapsed >= limit_ns) return 0;
		/* Wake 1 ms before the measured boundary so scheduler wake latency
		 * cannot consume a ~180 us copy-admission interval. Poll at 10 us
		 * near that boundary; the DMA and copy-budget guards are unchanged.
		 * Most of the active interval is still spent asleep.
		 */
		uint64_t ahead = 0, nap = 10000;
		if (phase < te_idle_start) {
			ahead = (uint64_t)(te_idle_start - phase) * scan.line_ns;
		} else if (used >= scan.frame_ns || budget >= scan.frame_ns - used) {
			ahead = (used < scan.frame_ns ? scan.frame_ns - used : 0) +
			    (uint64_t)te_idle_start * scan.line_ns;
		}
		if (ahead > 1250000) nap = ahead - 1000000;
		if (nap > limit_ns - elapsed) nap = limit_ns - elapsed;
		delay_for_interval((uint32_t)((nap + 999) / 1000), NSEC_PER_USEC);
	}
}

static int
command_scan_start(void)
{
	uint32_t config = gTEConfig = rd(munch_intf, 0x288), refresh = gTEAuto = rd(munch_intf, 0x2b4);
	gScanReason = 7;
	te_initial = rd(munch_intf, 0x294) & 0xffff;
	if (!(rd(munch_intf, 0x284) & 1) || (config & 0x180000) != 0x180000 ||
	    refresh != 0x80000001) return 0;
	gScanReason = 8;
	if (te_initial != 2400) return 0;
	gScanReason = 9;
	uint64_t start = mach_absolute_time(), previous_time = start, first_wrap = 0, last_wrap = 0;
	uint64_t line_time = 0, line_count = 0, elapsed;
	uint32_t previous = rd(munch_intf, 0x298) & 0xffff, busy_last = 0;
	unsigned wraps = 0, busy_samples = 0, idle_samples = 0;
	while (wraps < 4) {
		delay_for_interval(50, NSEC_PER_USEC);
		if (!regs_alive()) return 0;
		uint32_t raw = rd(munch_intf, 0x298) & 0xffff;
		uint32_t written = rd(munch_intf, 0x2b0) & 0xffff;
		uint64_t now = mach_absolute_time(), interval;
		absolutetime_to_nanoseconds(now - previous_time, &interval);
		if (interval > 1000000) return 0;
		absolutetime_to_nanoseconds(now - start, &elapsed);
		if (elapsed > 150000000 || raw < te_initial) return 0;
		/* Do not pair the previous frame's read phase with a new DMA transfer. */
		if ((rd(munch_intf, 0x298) & 0xffff) < raw) continue;
		if (raw < previous) {
			if (wraps++ == 0) first_wrap = now;
			last_wrap = now;
		} else if (raw > previous) {
			absolutetime_to_nanoseconds(now - previous_time, &interval);
			line_time += interval;
			line_count += raw - previous;
		}
		uint32_t phase = raw - te_initial;
		if (phase > gPhaseMax) gPhaseMax = phase;
		if (written > 0 && written < te_initial) {
			if (phase > busy_last) busy_last = phase;
			busy_samples++;
		} else if (written >= te_initial) idle_samples++;
		previous = raw;
		previous_time = now;
	}
	gScanReason = 10;
	if (!line_count || !busy_samples || !idle_samples) return 0;
	absolutetime_to_nanoseconds(last_wrap - first_wrap, &elapsed);
	scan.frame_ns = elapsed / (wraps - 1);
	scan.line_ns = line_time / line_count;
	gScanReason = 11;
	if (!scan.line_ns || scan.frame_ns < NSEC_PER_SEC / 65 ||
	    scan.frame_ns > NSEC_PER_SEC / 55) return 0;
	/* The TE timer phase and DMA write pointer are different counters.
	 * Only the write pointer is compared with the physical panel height.
	 * A command-mode TE cycle can have fewer ticks than shown pixel rows.
	 */
	scan.lines = (uint32_t)((scan.frame_ns + scan.line_ns - 1) / scan.line_ns);
	scan.shown = te_initial;
	te_idle_start = busy_last + 32; /* margin after the last observed active DMA line */
	/* Publish measurements even when calibration rejects them. */
	scan.millihz = (uint32_t)(1000000000000ULL / scan.frame_ns);
	gScanReason = 17;
	if (!scan.lines || scan.lines > 8192 || scan.lines + te_initial > 65535) return 0;
	gScanReason = 18;
	if (te_idle_start >= scan.lines) return 0;
	gScanReason = 0;
	scan.ok = 1;
	hakios_tl("display: munch command TE: %u.%03u Hz, %u ns/line, %u phase lines; DMA idle after %u; staged presentation enabled\n",
	    scan.millihz / 1000, scan.millihz % 1000, (unsigned)scan.line_ns, scan.lines, te_idle_start);
	return 1;
}

static uint64_t
hakios_display_next_frame_ns(void)
{
    uint32_t phase = hakios_display_line(NULL);
    uint64_t used = (uint64_t)phase * scan.line_ns;
    return scan.ok && used < scan.frame_ns ? scan.frame_ns - used : 0;
}

static int
hakios_display_scan_start(void)
{
    uint32_t on = 0;
    if (!PE_parse_boot_argn("fb_scan", &on, sizeof(on)) || on != 1) return 0;
    gScanReason = 5;
    gcc_map = ml_io_map(0x0010b000, 0x1000);
    if (!gcc_map) return 0;
    uint32_t gcc_saved = rd(gcc_map, 0x00c), ahb_saved = UINT32_MAX;
    bool ahb_touched = false;
    gScanReason = 4;
    if (!clock_on(gcc_map, 0x00c, "GCC display AHB")) goto fail;
    gScanReason = 5;
    dispcc_map = ml_io_map(0x0af00000, 0x4000);
    if (!dispcc_map) goto fail;
    {
        gScanReason = 3;
        uint32_t power = rd(dispcc_map, DISPCC_MDSS_CORE_GDSCR);
        if (power == UINT32_MAX || !(power & GDSC_POWER_ON)) goto fail;
    }
    ahb_saved = rd(dispcc_map, DISPCC_MDSS_AHB_CBCR);
    ahb_touched = ahb_saved != UINT32_MAX;
    gScanReason = 4;
    if (!clock_on(dispcc_map, DISPCC_MDSS_AHB_CBCR, "DISPCC AHB")) goto fail;
    gScanReason = 5;
    munch_intf = ml_io_map(0x0ae6b800, 0x1000);
    if (!munch_intf || !regs_alive()) goto fail;
    gScanReason = 6;
    /* Only the inherited command mode is supported by this munch experiment. */
    if (!(rd(munch_intf, 0) & 1) && command_scan_start()) return 1;
fail:
    scan.ok = 0;
    if (ahb_touched && !(ahb_saved & CBCR_ENABLE)) wr(dispcc_map, DISPCC_MDSS_AHB_CBCR, ahb_saved);
    if (gcc_saved != UINT32_MAX && !(gcc_saved & CBCR_ENABLE)) wr(gcc_map, 0x00c, gcc_saved);
    hakios_tl("display: munch command TE calibration failed E%u TE=%u config=%#x auto=%#x; original presentation retained\n",
        gScanReason, te_initial, gTEConfig, gTEAuto);
    return 0;
}

/* FPS-only overlay: one short line in the top-left, inside the private
 * compose buffer when active. It is not a display synchronization mechanism. */
static void
munch_scan_hud(uint8_t *base, uint32_t width, uint32_t height, uint32_t stride,
    uint64_t copy_ns, uint32_t frames, uint32_t missed, bool staged,
    uint32_t beat = 0, uint32_t stage = 0, uint32_t age_ms = 0)
{
    (void)copy_ns; (void)frames; (void)missed; (void)staged;
    (void)beat; (void)stage; (void)age_ms;
    if (!base || width < 176 || height < 112 || stride < width * 4) return;
    static const char alphabet[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ.";
    static const uint8_t font[][7] = {
        {14,17,19,21,25,17,14}, {4,12,4,4,4,4,14}, {14,17,1,2,4,8,31},
        {30,1,1,14,1,1,30}, {2,6,10,18,31,2,2}, {31,16,16,30,1,1,30},
        {14,16,16,30,17,17,14}, {31,1,2,4,8,8,8}, {14,17,17,14,17,17,14},
        {14,17,17,15,1,1,14},
        {14,17,17,31,17,17,17}, {30,17,17,30,17,17,30}, {14,17,16,16,16,17,14},
        {30,17,17,17,17,17,30}, {31,16,16,30,16,16,31}, {31,16,16,30,16,16,16},
        {14,17,16,23,17,17,15}, {17,17,17,31,17,17,17}, {14,4,4,4,4,4,14},
        {7,2,2,2,2,18,12}, {17,18,20,24,20,18,17}, {16,16,16,16,16,16,31},
        {17,27,21,21,17,17,17}, {17,25,21,19,17,17,17}, {14,17,17,17,17,17,14},
        {30,17,17,30,16,16,16}, {14,17,17,17,21,18,13}, {30,17,17,30,20,18,17},
        {15,16,16,14,1,1,30}, {31,4,4,4,4,4,4}, {17,17,17,17,17,17,14},
        {17,17,17,17,17,10,4}, {17,17,17,21,21,21,10}, {17,17,10,4,10,17,17},
        {17,17,10,4,4,4,4}, {31,1,2,4,8,16,31}, {0,0,0,0,0,4,4}
    };
    char lines[1][16];
    uint64_t now_ns;
    absolutetime_to_nanoseconds(mach_absolute_time(), &now_ns);
    snprintf(lines[0], sizeof(lines[0]), "FPS %u", munch_present_fps(now_ns));
    uint32_t color = 0xffffff;
    for (unsigned row = 0; row < 1; row++) {
        for (unsigned col = 0; col < 9; col++) {
            unsigned index = 37;
            char c = col < strlen(lines[row]) ? lines[row][col] : ' ';
            for (unsigned i = 0; i < 37; i++) if (alphabet[i] == c) { index = i; break; }
            for (unsigned y = 0; y < 24; y++) {
                uint32_t *to = (uint32_t *)(base + (size_t)(80 + row * 24 + y) * stride);
                for (unsigned x = 0; x < 18; x++) {
                    bool ink = index < 37 && y < 21 && x < 15 && (font[index][y / 3] & (16 >> (x / 3)));
                    to[8 + col * 18 + x] = ink ? color : 0;
                }
            }
        }
    }
}
