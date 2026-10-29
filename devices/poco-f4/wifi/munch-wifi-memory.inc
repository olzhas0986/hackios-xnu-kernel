/* CPU-only allocation preflight. No IOVA assignment or SMMU publication. */
#include "munch-wifi-memory-ranges.h"

static bool
munch_wifi_memory_range(uint64_t pa, uint64_t size)
{
	if (!size || (pa & 0xfff) || size > UINT64_MAX - pa) return false;
	bool ram = false;
	for (unsigned i = 0; i < sizeof(munch_wifi_ram)/sizeof(munch_wifi_ram[0]); i++)
		if (pa >= munch_wifi_ram[i][0] && pa + size <= munch_wifi_ram[i][1]) ram = true;
	if (!ram) return false;
	for (unsigned i = 0; i < sizeof(munch_wifi_reserved)/sizeof(munch_wifi_reserved[0]); i++)
		if (pa < munch_wifi_reserved[i][1] && pa + size > munch_wifi_reserved[i][0]) return false;
	return true;
}

static bool
munch_wifi_memory_preflight(void)
{
	uint32_t requested = 0;
	PE_parse_boot_argn("munch_wifi_memory", &requested, sizeof(requested));
	if (!requested) return true;
	if (requested != 1 || (PAGE_SIZE != 0x1000 && PAGE_SIZE != 0x4000)) return false;
	/* A single owned VM page holds a 4 KiB empty root table. Verify each
	 * hardware-sized subpage, without assuming VM pages are 4 KiB. */
	vm_offset_t va = 0;
	if (kmem_alloc(kernel_map, &va, PAGE_SIZE, KMA_KOBJECT | KMA_ZERO | KMA_DATA,
	    VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
		hakios_tl("MUNCH Wi-Fi: memory allocation failed; no SMMU writes\n");
		return false;
	}
	uint64_t pa = kvtophys(va);
	bool ok = munch_wifi_memory_range(pa, PAGE_SIZE);
	for (unsigned at = 0; at < PAGE_SIZE && ok; at += 0x1000)
		ok = kvtophys(va + at) == pa + at;
	hakios_tl("MUNCH Wi-Fi: owned memory pa=%#llx bytes=%#x RAM/reserved/contiguous=%u\n",
	    (unsigned long long)pa, (unsigned)PAGE_SIZE, ok);
	/* Allocation belongs to us even if its physical metadata fails checks.
	 * Do not touch its contents on that failure path. */
	if (ok) {
		volatile uint64_t *words = (volatile uint64_t *)va;
		for (unsigned i = 0; i < PAGE_SIZE/8; i++) if (words[i]) ok = false;
		if (ok) {
			for (unsigned i = 0; i < PAGE_SIZE/8; i++) words[i] = 0x5143413633393000ULL ^ i;
			for (unsigned i = 0; i < PAGE_SIZE/8; i++)
				if (words[i] != (0x5143413633393000ULL ^ i)) ok = false;
		}
		for (unsigned i = 0; i < PAGE_SIZE/8; i++) words[i] = 0;
		for (unsigned i = 0; i < PAGE_SIZE/8; i++) if (words[i]) ok = false;
	}
	kmem_free(kernel_map, va, PAGE_SIZE);
	hakios_tl("MUNCH Wi-Fi: memory preflight %s; allocation released; no IOVA or DMA\n", ok ? "passed" : "failed");
	return ok;
}
