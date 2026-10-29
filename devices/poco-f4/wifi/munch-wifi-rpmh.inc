/* Bounded ACTIVE transactions adapted from the local UMI reference.
 * No SLEEP/WAKE, CPR override, mode change or shared-rail shutdown. */
#define RH_ACTIVE_N     2u       /* DT order: ACTIVE 2, SLEEP 3, WAKE 3 */
#define RH_NORMAL_N     8u       /* hidden CONTROL TCS is not in this bank */
#define RH_TCS_STRIDE   0x2a0u
#define RH_CMD_STRIDE   0x14u
#define RH_IRQ_EN       0x00u
#define RH_IRQ_STATUS   0x04u
#define RH_IRQ_CLEAR    0x08u
#define RH_WAIT         0x10u
#define RH_CONTROL      0x14u
#define RH_STATUS       0x18u
#define RH_ENABLE       0x1cu
#define RH_MSGID        0x30u
#define RH_ADDR         0x34u
#define RH_DATA         0x38u
#define RH_CMD_STATUS   0x3cu
#define RH_AMC_EN       (1u << 16)
#define RH_TRIGGER      (1u << 24)
#define RH_ISSUED       (1u << 8)
#define RH_COMPLETE     (1u << 16)
#define RH_WRITE_ACK    (8u | (1u << 8) | (1u << 16))
#define RH_DB_MAGIC     0x0c0330dbu
#define RH_DB_HEADER    144u
#define RH_DB_ENTRY     24u
#define RH_DB_MAX       0x100000u /* policy bound, not a DT size */
#define RH_VRM          4u
#define RH_TIMEOUT_US   100000u

static vm_offset_t rh_tcs;
static unsigned rh_ncpt;
static bool rh_poisoned;
static uint32_t rh_read(vm_offset_t base, uint32_t off) { return munch_wifi_read(base, off); }
static void rh_write(vm_offset_t base, uint32_t off, uint32_t value) { munch_wifi_store32(base, off, value); }

static bool
rh_sync(uint32_t off, uint32_t value)
{
	rh_write(rh_tcs, off, value);
	for (unsigned i = 0; i < 1000; ++i) {
		if (rh_read(rh_tcs, off) == value) {
			return true;
		}
		delay(1);
	}
	hakios_tl("MUNCH Wi-Fi: RPMh readback timeout off=%#x want=%#x got=%#x\n",
	    off, value, rh_read(rh_tcs, off));
	return false;
}

static bool
rh_free(unsigned t)
{
	uint32_t b = t * RH_TCS_STRIDE;
	/* UEFI leaves AMC_EN set on an empty idle ACTIVE slot. No trigger,
	 * commands, wait mask or pending completion may remain. */
	return rh_read(rh_tcs, b + RH_STATUS) != 0 &&
	    rh_read(rh_tcs, b + RH_STATUS) != 0xffffffffu &&
	    (rh_read(rh_tcs, b + RH_CONTROL) & ~RH_AMC_EN) == 0 &&
	    rh_read(rh_tcs, b + RH_ENABLE) == 0 &&
	    rh_read(rh_tcs, b + RH_WAIT) == 0 &&
	    !(rh_read(rh_tcs, RH_IRQ_STATUS) & (1u << t));
}

static int
rh_send(uint32_t address, unsigned value, bool arc)
{
	if (rh_poisoned || !munch_wifi_posted_guard(address, value)) return EBUSY;
	int chosen = -1;
	for (unsigned t = 0; t < RH_ACTIVE_N; ++t) {
		uint32_t b = t * RH_TCS_STRIDE;
		uint32_t enabled = rh_read(rh_tcs, b + RH_ENABLE);
		hakios_tl("MUNCH Wi-Fi: RPMh ACTIVE TCS%u idle=%#x control=%#x enable=%#x wait=%#x\n",
		    t, rh_read(rh_tcs, b + RH_STATUS), rh_read(rh_tcs, b + RH_CONTROL),
		    enabled, rh_read(rh_tcs, b + RH_WAIT));
		if (enabled & ~((1u << rh_ncpt) - 1u)) {
			return EIO;
		}
		for (unsigned c = 0; c < rh_ncpt; ++c) {
			if ((enabled & (1u << c)) &&
			    (rh_read(rh_tcs, b + RH_ADDR + c * RH_CMD_STRIDE) & ~15u) == address &&
			    !munch_wifi_allow_posted_cx(address, value, t, c)) {
				hakios_tl("MUNCH Wi-Fi: RPMh conflicting inherited VRM request in TCS%u\n", t);
				return EBUSY;
			}
		}
		if (chosen < 0 && rh_free(t)) {
			chosen = (int)t;
		}
	}
	if (chosen < 0) {
		hakios_tl("MUNCH Wi-Fi: RPMh no empty idle ACTIVE TCS; leaving inherited state intact\n");
		return EBUSY;
	}
	unsigned t = (unsigned)chosen;
	uint32_t b = t * RH_TCS_STRIDE, bit = 1u << t;
	uint32_t irq_en = rh_read(rh_tcs, RH_IRQ_EN);
	uint32_t inherited_control = rh_read(rh_tcs, b + RH_CONTROL);
	if (irq_en == 0xffffffffu || !rh_free(t)) {
		return EBUSY;
	}
	/* Only this TCS's source is masked. Other sources/CPU IRQs remain intact. */
	if (!rh_sync(RH_IRQ_EN, irq_en & ~bit)) {
		rh_poisoned = true;
		return EIO;
	}
	if (!rh_free(t)) {
		/* No payload was touched. Do not acknowledge somebody else's IRQ. */
		if (!rh_sync(RH_IRQ_EN, (rh_read(rh_tcs, RH_IRQ_EN) & ~bit) | (irq_en & bit))) {
			rh_poisoned = true;
		}
		return EBUSY;
	}
	/* From here an uncertain write/timeout quarantines our slot for this boot. */
	rh_poisoned = true;
	/* VRM: voltage in mV then enable. ARC: corner index only.
	 * Retain inherited regulator modes; no shared-rail disable. */
	const uint32_t offsets[3] = {0, 4, 0};
	const uint32_t values[3] = {value, 1, 0};
	unsigned commands = arc ? 1 : 2;
	uint32_t command_mask = (1u << commands) - 1;
	hakios_tl("MUNCH Wi-Fi: RPMh TCS%u ACTIVE data=%u commands=%u enable=1 irq-enable=%#x\n",
	    t, value, commands, irq_en);
	for (unsigned c = 0; c < commands; ++c) {
		uint32_t cmd = b + c * RH_CMD_STRIDE;
		if (!rh_sync(cmd + RH_MSGID, RH_WRITE_ACK) ||
		    !rh_sync(cmd + RH_ADDR, address + offsets[c]) ||
		    !rh_sync(cmd + RH_DATA, values[c])) {
			return EIO;
		}
	}
	if (!rh_sync(b + RH_WAIT, command_mask) || !rh_sync(b + RH_ENABLE, command_mask) ||
	    !rh_sync(b + RH_CONTROL, 0) || !rh_sync(b + RH_CONTROL, RH_AMC_EN)) {
		return EIO;
	}
	if (!munch_wifi_posted_guard(address, value)) return EBUSY;
	rh_write(rh_tcs, b + RH_CONTROL, RH_AMC_EN | RH_TRIGGER);
	uint32_t irq = 0, idle = 0, status[3] = {0, 0, 0};
	bool done = false, progress = false;
	for (unsigned us = 0; us < RH_TIMEOUT_US; ++us) {
		irq = rh_read(rh_tcs, RH_IRQ_STATUS);
		idle = rh_read(rh_tcs, b + RH_STATUS);
		bool complete = true;
		for (unsigned c = 0; c < commands; ++c) {
			status[c] = rh_read(rh_tcs, b + RH_CMD_STATUS + c * RH_CMD_STRIDE);
			complete &= status[c] != 0xffffffffu &&
			    (status[c] & (RH_ISSUED | RH_COMPLETE)) == (RH_ISSUED | RH_COMPLETE);
		}
		/* A fresh IRQ or observed execution transition excludes stale CMD_STATUS. */
		progress |= idle == 0 || !complete;
		if (idle && idle != 0xffffffffu && irq != 0xffffffffu &&
		    ((irq & bit) || progress) && complete) {
			done = true;
			break;
		}
		delay(1);
	}
	hakios_tl("MUNCH Wi-Fi: RPMh TCS%u done=%d irq=%#x idle=%#x control=%#x cmd-status=%#x/%#x/%#x\n",
	    t, done, irq, idle, rh_read(rh_tcs, b + RH_CONTROL), status[0], status[1], status[2]);
	if (!done) {
		hakios_tl("MUNCH Wi-Fi: RPMh timeout; TCS quarantined, IRQ remains masked; no reset/retry\n");
		return ETIMEDOUT;
	}
	/* Proven completion: clear trigger before mode, then free only our commands. */
	if (!rh_sync(b + RH_CONTROL, RH_AMC_EN) || !rh_sync(b + RH_CONTROL, inherited_control) ||
	    !rh_sync(b + RH_ENABLE, 0) || !rh_sync(b + RH_WAIT, 0)) {
		return EIO;
	}
	rh_write(rh_tcs, RH_IRQ_CLEAR, bit); /* W1C: never clear other TCS bits */
	if ((rh_read(rh_tcs, RH_IRQ_STATUS) & bit) ||
	    !rh_sync(RH_IRQ_EN, (rh_read(rh_tcs, RH_IRQ_EN) & ~bit) | (irq_en & bit))) {
		return EIO;
	}
	rh_poisoned = false;
	hakios_tl("MUNCH Wi-Fi: RPMh ACTIVE vote complete (rail voltage requires hardware verification)\n");
	return 0;
}
