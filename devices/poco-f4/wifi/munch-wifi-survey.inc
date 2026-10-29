/* First munch QCA6390 checkpoint, adapted from UMI's GCC/TLMM survey.
 * No endpoint, PHY, RPMh, SMMU, DMA or PCIe register access at this stage.
 */
#include <pexpert/device_tree.h>
extern void hakios_tl(const char *, ...) __printflike(1, 2);

#include <mach/vm_param.h>
#include <arm/memory_types.h>

/* XNU io_map(false) uses permanent io_submap allocations. Use the
 * explicitly unmappable kernel_map API and unmap the exact host-page span,
 * including a 4 KiB SMMU bank offset when the XNU VM page is 16 KiB. */
static vm_offset_t
munch_wifi_map(uint32_t pa, unsigned size)
{
	vm_offset_t mask = PAGE_SIZE - 1, offset = pa & mask;
	if (!size || size > 0x100000) return 0;
	unsigned span = (size + offset + mask) & ~mask;
	vm_offset_t va = ml_io_map_unmappable(pa & ~mask, span, VM_WIMG_IO);
	return va ? va + offset : 0;
}

static void
munch_wifi_unmap(vm_offset_t va, unsigned size)
{
	vm_offset_t mask = PAGE_SIZE - 1, offset = va & mask;
	unsigned span = (size + offset + mask) & ~mask;
	ml_io_unmap(va & ~mask, span);
}

#ifndef MUNCH_WIFI_MMIO_READ32
#define MUNCH_WIFI_MMIO_READ32(address) (*(volatile uint32_t *)(address))
#endif

static uint32_t
munch_wifi_read(vm_offset_t base, uint32_t offset)
{
	uint32_t value = MUNCH_WIFI_MMIO_READ32(base + offset);
#if defined(__aarch64__)
	__asm__ volatile("dmb oshld" ::: "memory");
#endif
	return value;
}

static bool
munch_wifi_survey(void)
{
	static const struct { const char *name; uint32_t offset; } clocks[] = {
		{ "pcie0_reset", 0x6b000 }, { "pcie0_gdsc", 0x6b004 },
		{ "pcie0_clock_votes", 0x52008 }, { "pcie0_slv_q2a", 0x6b010 },
		{ "pcie0_slv_axi", 0x6b014 }, { "pcie0_mstr_axi", 0x6b01c },
		{ "pcie0_cfg_ahb", 0x6b024 }, { "pcie0_aux", 0x6b028 },
		{ "pcie0_pipe", 0x6b02c }, { "pcie0_aux_cmd", 0x6b038 },
		{ "pcie0_aux_cfg", 0x6b03c }, { "pcie0_phy_reset", 0x6c01c },
		{ "pcie0_phy_refgen", 0x6f02c },
	};
	static const struct { const char *name; uint32_t pin; } pins[] = {
		{ "wlan_enable", 20 }, { "bluetooth_enable", 21 },
		{ "pcie0_perst", 79 }, { "pcie0_clkreq", 80 },
		{ "pcie0_wake", 81 }, { "wlan_sw_ctrl", 124 },
	};
	vm_offset_t gcc = munch_wifi_map(0x00100000, 0x80000);
	vm_offset_t gpio = munch_wifi_map(0x0f900000, 0x80000);
	if (!gcc || !gpio) {
		hakios_tl("MUNCH Wi-Fi: GCC/TLMM mapping failed\n");
		if (gpio) munch_wifi_unmap(gpio, 0x80000);
		if (gcc) munch_wifi_unmap(gcc, 0x80000);
		return false;
	}
	hakios_tl("MUNCH Wi-Fi: QCA6390 PCIe0; read-only GCC/TLMM survey\n");
	for (unsigned i = 0; i < sizeof(clocks) / sizeof(clocks[0]); i++)
		hakios_tl("MUNCH Wi-Fi: GCC %s (%#x) = %#x\n", clocks[i].name,
		    0x00100000 + clocks[i].offset, munch_wifi_read(gcc, clocks[i].offset));
	for (unsigned i = 0; i < sizeof(pins) / sizeof(pins[0]); i++)
		hakios_tl("MUNCH Wi-Fi: GPIO%u %s cfg=%#x io=%#x\n", pins[i].pin, pins[i].name,
		    munch_wifi_read(gpio, pins[i].pin * 0x1000),
		    munch_wifi_read(gpio, pins[i].pin * 0x1000 + 4));
	munch_wifi_unmap(gpio, 0x80000);
	munch_wifi_unmap(gcc, 0x80000);
	return true;
}


/* Captured munch DT: qcom,cmd-db reserved at 0x80860000/0x20000;
 * apps RSC DRV2 at 0x18220000, TCS offset 0xd00. Observation only:
 * no resource votes, IRQ acknowledgements, controller or endpoint access.
 */
static unsigned
munch_wifi_db16(vm_offset_t db, unsigned off)
{
	return (munch_wifi_read(db, off & ~3u) >> ((off & 2u) * 8)) & 0xffffu;
}

static bool
munch_wifi_db_entry(vm_offset_t db, unsigned size, const char *name,
    uint32_t *address, unsigned *aux_start, unsigned *aux_length)
{
	uint32_t key[2] = {0, 0}, found = 0;
	unsigned matches = 0, found_aux = 0, found_length = 0;
	if (size < 144 || munch_wifi_read(db, 0) != 1 ||
	    munch_wifi_read(db, 4) != 0x0c0330db ||
	    (munch_wifi_read(db, 140) & 1u)) return false;
	unsigned len = 0;
	while (name[len]) {
		if (len == 8) return false;
		key[len / 4] |= (uint32_t)(uint8_t)name[len] << (8 * (len % 4));
		len++;
	}
	if (!len) return false;
	for (unsigned bank = 0; bank < 8; bank++) {
		unsigned h = 8 + bank * 16, slave = munch_wifi_db16(db, h);
		if (!slave) break;
		unsigned entries = 144 + munch_wifi_db16(db, h + 2);
		unsigned aux = 144 + munch_wifi_db16(db, h + 4);
		unsigned count = munch_wifi_db16(db, h + 6);
		if (slave < 3 || slave > 5 || (entries & 3u) || count > 2048 ||
		    entries > size || count > (size - entries) / 24 || aux > size)
			return false;
		for (unsigned i = 0; i < count; i++) {
			unsigned e = entries + i * 24;
			if (munch_wifi_read(db, e) != key[0] ||
			    munch_wifi_read(db, e + 4) != key[1]) continue;
			uint32_t value = munch_wifi_read(db, e + 16);
			unsigned offset = munch_wifi_db16(db, e + 22);
			unsigned length = munch_wifi_db16(db, e + 20);
			if (!value || (value >> 16) != slave || (value & 3u) ||
			    offset > size - aux || length > size - aux - offset ||
			    (matches && (value != found || (aux_start &&
		    (length != found_length || aux + offset != found_aux))))) return false;
			found = value;
			found_aux = aux + offset; found_length = length;
			matches++;
		}
	}
	if (!matches) return false;
	*address = found;
	if (aux_start) *aux_start = found_aux;
	if (aux_length) *aux_length = found_length;
	return true;
}

static bool
munch_wifi_db_lookup(vm_offset_t db, unsigned size, const char *name,
    uint32_t *address)
{
	return munch_wifi_db_entry(db, size, name, address, NULL, NULL);
}

static bool munch_wifi_enumeration(void);

static bool
munch_wifi_power_survey(void)
{
	static const char *resources[] = {
		"smpa6", "smpa4", "smpa5", "smpc8", "ldoa9", "ldoa5",
		"ldoa16", "cx.lvl", "smpf2",
	};
	vm_offset_t db = munch_wifi_map(0x80860000, 0x20000);
	vm_offset_t rsc = munch_wifi_map(0x18220000, 0x10000);
	bool ok = false;
	if (!db || !rsc) {
		hakios_tl("MUNCH Wi-Fi: power survey mapping failed\n");
		goto out;
	}
	hakios_tl("MUNCH Wi-Fi: CMD DB version=%#x magic=%#x flags=%#x\n",
	    munch_wifi_read(db, 0), munch_wifi_read(db, 4), munch_wifi_read(db, 140));
	ok = true;
	for (unsigned i = 0; i < sizeof(resources) / sizeof(resources[0]); i++) {
		uint32_t address = 0;
		bool valid = munch_wifi_db_lookup(db, 0x20000, resources[i], &address);
		hakios_tl("MUNCH Wi-Fi: resource %s addr=%#x %s\n", resources[i],
		    address, valid ? "resolved" : "missing/invalid");
		/* smpf2 is observed only because CNSS names it separately. It is
		 * NOT the munch DIG supply and must not become a fallback vote. */
		if (!valid && i < 8) ok = false;
	}
	uint32_t id = munch_wifi_read(rsc, 0), config = munch_wifi_read(rsc, 0xc);
	unsigned major = (id >> 16) & 255u, count = (config >> 12) & 63u;
	unsigned commands = (config >> 27) & 31u;
	hakios_tl("MUNCH Wi-Fi: RSC DRV2 id=%#x config=%#x tcs=%u cmds=%u\n",
	    id, config, count, commands);
	if ((major != 1 && major != 2) || config == 0xffffffffu ||
	    count < 8 || count > 16 || !commands || commands > 16) {
		hakios_tl("MUNCH Wi-Fi: unsupported RSC geometry; no TCS reads\n");
		ok = false;
		goto out;
	}
	hakios_tl("MUNCH Wi-Fi: RSC IRQ status=%#x; observational slots 0..7\n",
	    munch_wifi_read(rsc, 0xd04));
	for (unsigned slot = 0; slot < 8; slot++) {
		unsigned off = 0xd00 + slot * 0x2a0;
		hakios_tl("MUNCH Wi-Fi: TCS%u ctl=%#x idle=%#x en=%#x wait=%#x\n",
		    slot, munch_wifi_read(rsc, off + 0x14), munch_wifi_read(rsc, off + 0x18),
		    munch_wifi_read(rsc, off + 0x1c), munch_wifi_read(rsc, off + 0x10));
	}
 out:
	if (rsc) munch_wifi_unmap(rsc, 0x10000);
	if (db) munch_wifi_unmap(db, 0x20000);
	return ok;
}

/* Claim munch even with Wi-Fi disabled, preventing the Redwood WPSS path. */
static bool
munch_wifi_init(void)
{
	DTEntry entry;
	const void *board;
	unsigned size;
	uint32_t step = 0, back = 0, enabled = 0;
	if (SecureDTLookupEntry(NULL, "/arm-io", &entry) != kSuccess ||
	    SecureDTGetProperty(entry, "device_type", &board, &size) != kSuccess ||
	    size != 6 || memcmp(board, "munch", 6) != 0) return false;
	PE_parse_boot_argn("munch_wifi_step", &step, sizeof(step));
	PE_parse_boot_argn("munch_wifi_return", &back, sizeof(back));
	PE_parse_boot_argn("wifi", &enabled, sizeof(enabled));
	if ((step == 1 || step == 2 || step == 3 || step == 4) && back == 1) {
		hakios_tl("MUNCH Wi-Fi: survey %s; returning to loader\n",
		    (step == 1 ? munch_wifi_survey() : step == 2 ?
		    munch_wifi_power_survey() : munch_wifi_enumeration()) ? "passed" : "failed");
		loader_return();
	} else if (step || back || enabled) {
		hakios_tl("MUNCH Wi-Fi: only munch_wifi_step=1/2/3/4 munch_wifi_return=1 is supported\n");
	}
	return true;
}
