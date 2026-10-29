/* Explicit step 3: munch rail votes and config-only PCIe enumeration.
 * This include follows munch-wifi-survey.inc in the native wrapper. */
#include <sys/errno.h>
#include "munch-wifi-pcie.inc"

struct munch_wifi_region { uint32_t pa, size; vm_offset_t va; };
static struct munch_wifi_region munch_wifi_regions[] = {
	{ 0x00100000, 0x100000, 0 }, { 0x0f900000, 0x80000, 0 },
	{ 0x01c00000, 0x10000, 0 }, { 0x60000000, 0x2000, 0 },
	{ 0x60100000, 0x1000, 0 }, { 0x80860000, 0x20000, 0 },
	{ 0x18220000, 0x10000, 0 },
};

static vm_offset_t
munch_wifi_address(uint32_t pa, unsigned width)
{
	for (unsigned i = 0; i < sizeof(munch_wifi_regions)/sizeof(munch_wifi_regions[0]); i++) {
		struct munch_wifi_region *r = &munch_wifi_regions[i];
		if (r->va && pa >= r->pa && pa - r->pa <= r->size - width && !(pa & (width-1)))
			return r->va + pa - r->pa;
	}
	panic("munch Wi-Fi MMIO outside diagnostic whitelist: %#x", pa);
}

static uint32_t
munch_wifi_pcie_read(void *context, uint32_t pa)
{
	(void)context;
	return munch_wifi_read(munch_wifi_address(pa, 4), 0);
}

#ifndef MUNCH_WIFI_MMIO_WRITE32
#define MUNCH_WIFI_MMIO_WRITE32(address, value) (*(volatile uint32_t *)(address) = (value))
#endif
#ifndef MUNCH_WIFI_MMIO_WRITE16
#define MUNCH_WIFI_MMIO_WRITE16(address, value) (*(volatile uint16_t *)(address) = (value))
#endif

static void
munch_wifi_store32(vm_offset_t base, uint32_t off, uint32_t value)
{
#if defined(__aarch64__)
	__asm__ volatile("dmb oshst" ::: "memory");
#endif
	MUNCH_WIFI_MMIO_WRITE32(base + off, value);
#if defined(__aarch64__)
	__asm__ volatile("dsb osh" ::: "memory");
#endif
}

static void
munch_wifi_pcie_write(void *context, uint32_t pa, uint32_t value)
{
	(void)context;
	munch_wifi_store32(munch_wifi_address(pa, 4), 0, value);
}

static void
munch_wifi_pcie_write16(void *context, uint32_t pa, uint16_t value)
{
	(void)context;
	vm_offset_t va = munch_wifi_address(pa, 2);
#if defined(__aarch64__)
	__asm__ volatile("dmb oshst" ::: "memory");
#endif
	MUNCH_WIFI_MMIO_WRITE16(va, value);
#if defined(__aarch64__)
	__asm__ volatile("dsb osh" ::: "memory");
#endif
}

static void munch_wifi_pause(void *context, unsigned us) { (void)context; delay(us); }
static void munch_wifi_report(void *context, const char *name, uint32_t value)
{
	(void)context;
	hakios_tl("MUNCH Wi-Fi: PCIe %s=%#x\n", name, value);
}

static bool munch_wifi_allow_posted_cx(uint32_t, unsigned, unsigned, unsigned);
static bool munch_wifi_posted_guard(uint32_t, unsigned);
#include "munch-wifi-rpmh.inc"

/* Exact captured munch constraints. AON/DIG are aggregated into one smpa6.
 * Include the present 3antenna supply; need_unvote=0 is not disable.
 * Keep shared rail mode and leave completed ACTIVE votes until hardware reset. */
static const struct { const char *name; unsigned mv; } munch_wifi_rails[] = {
	{ "smpa4", 1800 }, { "smpa6", 950 }, { "smpa5", 1900 },
	{ "smpc8", 1350 }, { "ldoa16", 3024 },
	{ "ldoa9", 1200 }, { "ldoa5", 880 },
};
static uint32_t munch_wifi_rail_addresses[7], munch_wifi_cx_address;
static unsigned munch_wifi_cx_corner, munch_wifi_cx_levels[16], munch_wifi_cx_count;
static bool munch_wifi_cx_inherited, munch_wifi_cx_seen[2][16];
static uint32_t munch_wifi_cx_data[2][16], munch_wifi_cx_msg[2][16];
static bool munch_wifi_cx_refresh;
static unsigned munch_wifi_cx_target;
static uint32_t munch_wifi_posted_state[12];

/* ARC payload is the index into CMD DB's LE16 levels, not a voltage or the
 * abstract DT level 256 itself. Require a bounded monotonic table and an
 * exact SVS level; reject unknown encodings rather than approximate. */
static bool
munch_wifi_cx_lookup(vm_offset_t db)
{
	unsigned start = 0, length = 0;
	if (!munch_wifi_db_entry(db, 0x20000, "cx.lvl", &munch_wifi_cx_address,
	    &start, &length) || (munch_wifi_cx_address >> 16) != 3 || (munch_wifi_cx_address & 15) ||
	    (start & 1) || (length & 1) || length < 4 || length > 32) return false;
	unsigned previous = 0, selected = 0;
	munch_wifi_cx_count = 0;
	bool ended = false;
	for (unsigned i = 0; i < length/2; i++) {
		unsigned level = munch_wifi_db16(db, start + i*2);
		if (i == 0 && level) return false;
		if (i && !level) ended = true;
		if (level && (ended || level <= previous)) return false;
		munch_wifi_cx_levels[i] = level;
		if (!ended) munch_wifi_cx_count = i + 1;
		if (level == 256) selected = i;
		if (level) previous = level;
	}
	if (!selected) return false;
	munch_wifi_cx_corner = selected;
	return true;
}

/* Borrow only a completed, unchanged ACTIVE CX request with a validated
 * level >= SVS. Its slot/payload/IRQ are never modified. This describes a
 * retained request, not a measurement of physical voltage. */
static bool
munch_wifi_cx_retained_valid(unsigned t, unsigned c)
{
	uint32_t b = t * RH_TCS_STRIDE, cmd = b + c * RH_CMD_STRIDE;
	uint32_t data = rh_read(rh_tcs, cmd + RH_DATA);
	uint32_t msg = rh_read(rh_tcs, cmd + RH_MSGID);
	uint32_t status = rh_read(rh_tcs, cmd + RH_CMD_STATUS);
	return rh_read(rh_tcs, b + RH_STATUS) == 1 &&
	    !(rh_read(rh_tcs, RH_IRQ_STATUS) & (1u << t)) &&
	    rh_read(rh_tcs, cmd + RH_ADDR) == munch_wifi_cx_address &&
	    data < munch_wifi_cx_count && munch_wifi_cx_levels[data] >= 256 &&
	    (msg & 0x1010fu) == RH_WRITE_ACK && status != UINT32_MAX &&
	    (status & (RH_ISSUED | RH_COMPLETE)) == (RH_ISSUED | RH_COMPLETE);
}

/* Narrow handoff policy for the observed UEFI posted ARC batch. Hardware
 * idle plus ISSUED is the transport completion condition for no-response
 * writes; it is NOT resource-ready. Submit a new acknowledged CX request in
 * an empty other ACTIVE slot and wait for its completion before any rails.
 * Never reclaim, retrigger, clear or rewrite the inherited slot. */
static bool
munch_wifi_posted_snapshot(bool capture)
{
	uint32_t state[12];
	state[0] = rh_read(rh_tcs, RH_CONTROL);
	state[1] = rh_read(rh_tcs, RH_STATUS);
	state[2] = rh_read(rh_tcs, RH_ENABLE);
	state[3] = rh_read(rh_tcs, RH_WAIT);
	if (state[0] != (RH_AMC_EN | RH_TRIGGER) || state[1] != 1 ||
	    state[2] != 3 || state[3] != 0 ||
	    (rh_read(rh_tcs, RH_IRQ_STATUS) & 1)) return false;
	for (unsigned c = 0; c < 2; c++) {
		unsigned cmd = c * RH_CMD_STRIDE;
		state[4+c*4] = rh_read(rh_tcs, cmd + RH_MSGID);
		state[5+c*4] = rh_read(rh_tcs, cmd + RH_ADDR);
		state[6+c*4] = rh_read(rh_tcs, cmd + RH_DATA);
		state[7+c*4] = rh_read(rh_tcs, cmd + RH_CMD_STATUS);
		if (state[4+c*4] != 0x10008 || state[7+c*4] != 0x101 ||
		    (state[5+c*4] >> 16) != 3 || (state[5+c*4] & 15) ||
		    state[6+c*4] == UINT32_MAX) return false;
	}
	if (state[5] != munch_wifi_cx_address || state[9] == munch_wifi_cx_address ||
	    state[6] >= munch_wifi_cx_count) return false;
	if (capture) {
		memcpy(munch_wifi_posted_state, state, sizeof(state));
		munch_wifi_cx_target = state[6] > munch_wifi_cx_corner ? state[6] : munch_wifi_cx_corner;
		return true;
	}
	return !memcmp(state, munch_wifi_posted_state, sizeof(state));
}

static bool
munch_wifi_allow_posted_cx(uint32_t address, unsigned value, unsigned t, unsigned c)
{
	return munch_wifi_cx_refresh && address == munch_wifi_cx_address &&
	    value == munch_wifi_cx_target && t == 0 && c == 0 &&
	    munch_wifi_posted_snapshot(false);
}

static bool
munch_wifi_posted_guard(uint32_t address, unsigned value)
{
	return !munch_wifi_cx_refresh || address != munch_wifi_cx_address ||
	    (value == munch_wifi_cx_target && munch_wifi_posted_snapshot(false));
}

static bool
munch_wifi_cx_recheck(void)
{
	if (munch_wifi_cx_refresh) return munch_wifi_posted_snapshot(false);
	bool found = false;
	for (unsigned t = 0; t < RH_ACTIVE_N; t++) {
		uint32_t en = rh_read(rh_tcs, t * RH_TCS_STRIDE + RH_ENABLE);
		if (en & ~((1u << rh_ncpt) - 1)) return false;
		for (unsigned c = 0; c < rh_ncpt; c++) {
			if (munch_wifi_cx_seen[t][c] && !(en & (1u << c))) return false;
			if (!(en & (1u << c))) continue;
			uint32_t a = rh_read(rh_tcs, t * RH_TCS_STRIDE + c * RH_CMD_STRIDE + RH_ADDR);
			if (munch_wifi_cx_seen[t][c] && a != munch_wifi_cx_address) return false;
			if ((a & ~15u) != munch_wifi_cx_address) continue;
			uint32_t cmd = t * RH_TCS_STRIDE + c * RH_CMD_STRIDE;
			if (!munch_wifi_cx_seen[t][c] || !munch_wifi_cx_retained_valid(t, c) ||
			    rh_read(rh_tcs, cmd + RH_DATA) != munch_wifi_cx_data[t][c] ||
			    rh_read(rh_tcs, cmd + RH_MSGID) != munch_wifi_cx_msg[t][c]) return false;
			found = true;
		}
	}
	return found;
}

static bool
munch_wifi_power_preflight(void)
{
	vm_offset_t db = munch_wifi_regions[5].va, rsc = munch_wifi_regions[6].va;
	uint32_t id = munch_wifi_read(rsc, 0), config = munch_wifi_read(rsc, 0xc);
	unsigned major = (id >> 16) & 255;
	rh_ncpt = (config >> 27) & 31;
	if ((major != 1 && major != 2) || ((config >> 12) & 63) != 8 ||
	    rh_ncpt < 2 || rh_ncpt > 16) return false;
	rh_tcs = rsc + 0xd00;
	for (unsigned i = 0; i < 7; i++) {
		if (!munch_wifi_db_lookup(db, 0x20000, munch_wifi_rails[i].name,
		    &munch_wifi_rail_addresses[i]) || (munch_wifi_rail_addresses[i] >> 16) != 4 ||
		    (munch_wifi_rail_addresses[i] & 15))
			return false;
		for (unsigned j = 0; j < i; j++)
			if (munch_wifi_rail_addresses[i] == munch_wifi_rail_addresses[j]) return false;
	}
	if (!munch_wifi_cx_lookup(db)) {
		hakios_tl("MUNCH Wi-Fi: invalid cx.lvl ARC level table; no writes\n");
		return false;
	}
	bool free = false, conflict = false;
	munch_wifi_cx_inherited = false;
	munch_wifi_cx_refresh = false;
	memset(munch_wifi_cx_seen, 0, sizeof(munch_wifi_cx_seen));
	hakios_tl("MUNCH Wi-Fi: CX SVS index=%u levels=%u\n", munch_wifi_cx_corner, munch_wifi_cx_count);
	uint32_t irq = rh_read(rh_tcs, RH_IRQ_STATUS);
	if (irq == UINT32_MAX || rh_read(rh_tcs, RH_IRQ_EN) == UINT32_MAX) return false;
	for (unsigned t = 0; t < RH_ACTIVE_N; t++) {
		uint32_t b = t * RH_TCS_STRIDE, en = rh_read(rh_tcs, b + RH_ENABLE);
		if (en & ~((1u << rh_ncpt) - 1)) return false;
		for (unsigned c = 0; c < rh_ncpt; c++) {
			if (!(en & (1u << c))) continue;
			uint32_t a = rh_read(rh_tcs, b + RH_ADDR + c * RH_CMD_STRIDE);
			uint32_t data = rh_read(rh_tcs, b + RH_DATA + c * RH_CMD_STRIDE);
			uint32_t msg = rh_read(rh_tcs, b + RH_MSGID + c * RH_CMD_STRIDE);
			uint32_t status = rh_read(rh_tcs, b + RH_CMD_STATUS + c * RH_CMD_STRIDE);
			hakios_tl("MUNCH Wi-Fi: inherited ACTIVE TCS%u cmd%u addr=%#x data=%#x\n", t, c, a, data);
			hakios_tl("MUNCH Wi-Fi: TCS%u cmd%u msg=%#x status=%#x\n", t, c, msg, status);
			if (a == UINT32_MAX || data == UINT32_MAX) conflict = true;
			if ((a & ~15u) == munch_wifi_cx_address) {
				bool valid = munch_wifi_cx_retained_valid(t, c);
				if (!valid && t == 0 && c == 0 && munch_wifi_posted_snapshot(true)) {
					munch_wifi_cx_refresh = true;
					valid = true;
				}
				hakios_tl("MUNCH Wi-Fi: retained CX index=%u level=%u %s\n", data,
				    data < munch_wifi_cx_count ? munch_wifi_cx_levels[data] : 0,
				    munch_wifi_cx_refresh ? "posted; acknowledged refresh required" :
				    valid ? "usable; preserve" : "unproven/insufficient; stop");
				if (!valid) conflict = true;
				munch_wifi_cx_seen[t][c] = true;
				munch_wifi_cx_data[t][c] = data; munch_wifi_cx_msg[t][c] = msg;
				munch_wifi_cx_inherited = true;
			}
			for (unsigned i = 0; i < 7; i++)
				if ((a & ~15u) == munch_wifi_rail_addresses[i]) {
					hakios_tl("MUNCH Wi-Fi: inherited vote conflicts with %s; no writes\n", munch_wifi_rails[i].name);
					conflict = true;
				}
		}
		free |= rh_free(t);
	}
	/* This experiment requires both radio halves off, with PERST low. */
	vm_offset_t gpio = munch_wifi_regions[1].va;
	if ((munch_wifi_read(gpio, 20*0x1000+4) & 3) ||
	    (munch_wifi_read(gpio, 21*0x1000+4) & 3) ||
	    (munch_wifi_read(gpio, 79*0x1000+4) & 3)) return false;
	return free && !conflict;
}

static int
munch_wifi_power_on(void *context)
{
	(void)context;
	int error = 0;
	if (munch_wifi_cx_inherited) {
		if (!munch_wifi_cx_recheck()) return EBUSY;
		if (munch_wifi_cx_refresh) {
			hakios_tl("MUNCH Wi-Fi: refresh CX index=%u level=%u via empty ACTIVE; preserve TCS0\n",
			    munch_wifi_cx_target, munch_wifi_cx_levels[munch_wifi_cx_target]);
			error = rh_send(munch_wifi_cx_address, munch_wifi_cx_target, true);
		} else {
			hakios_tl("MUNCH Wi-Fi: preserve inherited CX; no ARC write\n");
		}
	} else {
		hakios_tl("MUNCH Wi-Fi: vote cx.lvl SVS index=%u\n", munch_wifi_cx_corner);
		error = rh_send(munch_wifi_cx_address, munch_wifi_cx_corner, true);
	}
	for (unsigned i = 0; !error && i < 7; i++) {
		if (munch_wifi_cx_inherited && !munch_wifi_cx_recheck()) return EBUSY;
		hakios_tl("MUNCH Wi-Fi: vote %s minimum=%u mV\n", munch_wifi_rails[i].name, munch_wifi_rails[i].mv);
		error = rh_send(munch_wifi_rail_addresses[i], munch_wifi_rails[i].mv, false);
	}
	return error;
}

#include "munch-wifi-dma-survey.inc"
#include "munch-wifi-memory.inc"

static bool
munch_wifi_enumeration(void)
{
	static bool started;
	if (started) return false;
	started = true;
	bool ok = false, enumerated = false;
	uint32_t requested_step = 0;
	PE_parse_boot_argn("munch_wifi_step", &requested_step, sizeof(requested_step));
	struct munch_pcie host = {0};
	const struct munch_pcie_io io = {
		.context = NULL, .read32 = munch_wifi_pcie_read, .write32 = munch_wifi_pcie_write,
		.write16 = munch_wifi_pcie_write16, .power_on = munch_wifi_power_on,
		.delay_us = munch_wifi_pause, .report = munch_wifi_report,
	};
	if (requested_step == 4)
		hakios_tl("MUNCH Wi-Fi: step 4 power/PCIe plus read-only SID/SMMU; no DMA\n");
	else
		hakios_tl("MUNCH Wi-Fi: step 3 power/PCIe; no DMA, SID, BAR or MHI\n");
	for (unsigned i = 0; i < sizeof(munch_wifi_regions)/sizeof(munch_wifi_regions[0]); i++) {
		struct munch_wifi_region *r = &munch_wifi_regions[i];
		r->va = munch_wifi_map(r->pa, r->size);
		if (!r->va) goto out;
	}
	if (!munch_wifi_power_preflight()) {
		hakios_tl("MUNCH Wi-Fi: power preflight rejected; no writes\n");
		goto out;
	}
	enumerated = munch_pcie_enumerate(&host, &io) == 0;
	ok = enumerated;
	if (ok && requested_step == 4) {
		ok = munch_wifi_dma_survey();
		hakios_tl("MUNCH Wi-Fi: SID/SMMU survey %s; no DMA approval\n", ok ? "passed" : "failed");
	}
	if (ok && requested_step == 4) ok = munch_wifi_memory_preflight();
	/* Even on success keep the endpoint off before entering the loader.
	 * This does not remove completed shared ACTIVE votes. */
	if (munch_pcie_quiesce(&host)) {
		hakios_tl("MUNCH Wi-Fi: reset cleanup readback failed\n");
		ok = false; enumerated = false;
	}
	hakios_tl("MUNCH Wi-Fi: enumeration %s; PERST asserted, WLAN_EN low\n", enumerated ? "passed" : "failed");
 out:
	for (unsigned i = 0; i < sizeof(munch_wifi_regions)/sizeof(munch_wifi_regions[0]); i++) {
		struct munch_wifi_region *r = &munch_wifi_regions[i];
		if (r->va) munch_wifi_unmap(r->va, r->size);
		r->va = 0;
	}
	return ok;
}
