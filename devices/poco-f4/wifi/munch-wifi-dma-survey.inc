/* Step 4 adds observation only, after config-only enumeration. No SID/SMMU
 * writes, context selection, BAR mapping, DMA allocation or bus mastering. */
/* Context addresses come from IDR1, checked against the captured 1 MiB
 * aperture. Read at most four unreferenced banks; never infer ownership. */
static bool
munch_wifi_context_survey(vm_offset_t smmu, uint32_t id1, const uint32_t used[8])
{
	unsigned page = (id1 >> 31) ? 0x10000u : 0x1000u;
	unsigned pages = 2u << ((id1 >> 28) & 7);
	unsigned contexts = id1 & 255, s2 = (id1 >> 16) & 255;
	unsigned base = pages * page;
	if (page != 0x1000 || base != 0x80000 || 2u * base != 0x100000 ||
	    s2 > contexts || contexts > (0x100000u - base)/page) {
		hakios_tl("MUNCH Wi-Fi: context geometry unsupported; no CB reads\n");
		return false;
	}
	hakios_tl("MUNCH Wi-Fi: CB page=%#x base=%#x S2-only=%u VM-page=%#x\n", page, 0x15000000u + base, s2, (unsigned)PAGE_SIZE);
	unsigned observed = 0;
	for (unsigned cb = s2; cb < contexts && observed < 4; cb++) {
		if (used[cb/32] & (1u << (cb%32))) continue;
		hakios_tl("MUNCH Wi-Fi: CB%u inspect GR1 offsets=%#x/%#x\n", cb, page + 4*cb, page + 0x800 + 4*cb);
		uint32_t cbar = munch_wifi_read(smmu, page + 4*cb);
		uint32_t cba2r = munch_wifi_read(smmu, page + 0x800 + 4*cb);
		uint32_t pa = 0x15000000u + base + cb * page;
		hakios_tl("MUNCH Wi-Fi: CB%u map pa=%#x CBAR=%#x CBA2R=%#x\n", cb, pa, cbar, cba2r);
		vm_offset_t bank = munch_wifi_map(pa, page);
		if (!bank) { hakios_tl("MUNCH Wi-Fi: CB%u mapping failed\n", cb); return false; }
		uint32_t sctlr = munch_wifi_read(bank, 0), fsr = munch_wifi_read(bank, 0x58);
		uint32_t lo = munch_wifi_read(bank, 0x20), hi = munch_wifi_read(bank, 0x24);
		uint32_t tcr = munch_wifi_read(bank, 0x30), tcr2 = munch_wifi_read(bank, 0x10);
		hakios_tl("MUNCH Wi-Fi: CB%u pa=%#x SCTLR=%#x FSR=%#x\n", cb, pa, sctlr, fsr);
		hakios_tl("MUNCH Wi-Fi: CB%u CBAR=%#x CBA2R=%#x TTBR0=%#x:%#x\n", cb, cbar, cba2r, hi, lo);
		hakios_tl("MUNCH Wi-Fi: CB%u TCR=%#x TCR2=%#x; observation only\n", cb, tcr, tcr2);
		hakios_tl("MUNCH Wi-Fi: CB%u inactive=%u fault-clear=%u root-empty=%u; not claimed\n",
		    cb, !(sctlr & 1), fsr == 0, lo == 0 && hi == 0);
		munch_wifi_unmap(bank, page);
		if (cbar == UINT32_MAX || cba2r == UINT32_MAX || sctlr == UINT32_MAX ||
		    fsr == UINT32_MAX || lo == UINT32_MAX || hi == UINT32_MAX ||
		    tcr == UINT32_MAX || tcr2 == UINT32_MAX) {
			hakios_tl("MUNCH Wi-Fi: CB%u contains all-ones read; not eligible\n", cb);
			return false;
		}
		observed++;
	}
	hakios_tl("MUNCH Wi-Fi: CB observed=%u; no ownership or DMA approval\n", observed);
	return true;
}

static bool
munch_wifi_dma_survey(void)
{
	uint32_t ep = munch_wifi_pcie_read(NULL, 0x60100004);
	uint32_t rc = munch_wifi_pcie_read(NULL, 0x60000004);
	if (ep == UINT32_MAX || rc == UINT32_MAX || (ep & 7) || (rc & 7)) return false;
	hakios_tl("MUNCH Wi-Fi: step 4 SID/SMMU observation; DMA NOT APPROVED\n");
	uint32_t cfg = munch_wifi_pcie_read(NULL, 0x01c02c00);
	hakios_tl("MUNCH Wi-Fi: SID cfg=%#x bypass=%u DT endpoint SID=0x1c01\n", cfg, cfg & 1);
	if (cfg == UINT32_MAX) return false;
	/* SM8250 table: CRC8/0x07 of BE16 RID. Entry BDF[31:16], SID low
	 * byte[15:8], NEXT[7:0]. Observe existing chain, never initialize it. */
	uint8_t hash = 0;
	const uint8_t rid[2] = {1, 0};
	for (unsigned i = 0; i < 2; i++) {
		hash ^= rid[i];
		for (unsigned b = 0; b < 8; b++) hash = (hash << 1) ^ ((hash & 0x80) ? 7 : 0);
	}
	uint32_t seen[8] = {0};
	bool found = false, cycle = false;
	for (unsigned n = 0; n < 256; n++) {
		if (seen[hash/32] & (1u << (hash%32))) { cycle = true; break; }
		seen[hash/32] |= 1u << (hash%32);
		uint32_t entry = munch_wifi_pcie_read(NULL, 0x01c02000 + 4*hash);
		if (entry == UINT32_MAX) return false;
		if (n < 8 || (entry >> 16) == 0x100)
			hakios_tl("MUNCH Wi-Fi: SID table[%u]=%#x\n", hash, entry);
		if (!entry) break;
		if ((entry >> 16) == 0x100) {
			unsigned sid = 0x1c00 + ((entry >> 8) & 255);
			hakios_tl("MUNCH Wi-Fi: observed RID 0x100 route SID=%#x expected=0x1c01\n", sid);
			found = true;
			break;
		}
		unsigned next = entry & 255;
		if (!next) break;
		if (seen[next/32] & (1u << (next%32))) { cycle = true; break; }
		hash = next;
	}
	hakios_tl("MUNCH Wi-Fi: SID route found=%u cycle=%u; no route changes\n", found, cycle);
	if (cycle) hakios_tl("MUNCH Wi-Fi: cyclic SID route; continuing independent SMMU reads only\n");
	vm_offset_t smmu = munch_wifi_map(0x15000000, 0x2000);
	if (!smmu) return false;
	bool ok = false;
	uint32_t scr0 = munch_wifi_read(smmu, 0), id0 = munch_wifi_read(smmu, 0x20);
	uint32_t id1 = munch_wifi_read(smmu, 0x24), id2 = munch_wifi_read(smmu, 0x28);
	hakios_tl("MUNCH Wi-Fi: SMMU SCR0=%#x IDR0=%#x IDR1=%#x IDR2=%#x\n", scr0, id0, id1, id2);
	unsigned groups = id0 & 255, contexts = id1 & 255;
	if (scr0 == UINT32_MAX || id0 == UINT32_MAX || id1 == UINT32_MAX || id2 == UINT32_MAX ||
	    !(id0 & (1u << 27)) || (scr0 & (1u << 3)) || !groups || !contexts) {
		hakios_tl("MUNCH Wi-Fi: unsupported SMMU geometry/extended SID mode; no SMR scan\n");
		goto out;
	}
	unsigned invalid = 0, root_matches = 0, ep_matches = 0;
	uint32_t used[8] = {0};
	for (unsigned i = 0; i < groups; i++) {
		uint32_t smr = munch_wifi_read(smmu, 0x800 + i*4);
		uint32_t s2cr = munch_wifi_read(smmu, 0xc00 + i*4);
		if (smr == UINT32_MAX || s2cr == UINT32_MAX) goto out;
		if (!(smr & (1u << 31))) { invalid++; continue; }
		unsigned sid = smr & 0x7fff, mask = (smr >> 16) & 0x7fff;
		bool root = ((sid ^ 0x1c00) & ~mask) == 0;
		bool endpoint = ((sid ^ 0x1c01) & ~mask) == 0;
		unsigned type = (s2cr >> 16) & 3, cb = s2cr & 255;
		if (type == 3 || (type == 0 && cb >= contexts)) goto out;
		if (type == 0) used[cb/32] |= 1u << (cb%32);
		root_matches += root; ep_matches += endpoint;
		/* Log all valid owners, including non-Wi-Fi streams. */
		hakios_tl("MUNCH Wi-Fi: SMR%u=%#x S2CR=%#x type=%u cb=%u root=%u ep=%u\n",
			    i, smr, s2cr, type, cb, root, endpoint);
	}
	unsigned referenced = 0;
	for (unsigned i = 0; i < contexts; i++) referenced += !!(used[i/32] & (1u << (i%32)));
	hakios_tl("MUNCH Wi-Fi: SMMU groups=%u invalid=%u contexts=%u referenced=%u\n", groups, invalid, contexts, referenced);
	hakios_tl("MUNCH Wi-Fi: SMMU matches root=%u endpoint=%u; no ownership claimed\n", root_matches, ep_matches);
	if (!munch_wifi_context_survey(smmu, id1, used)) goto out;
	ep = munch_wifi_pcie_read(NULL, 0x60100004);
	rc = munch_wifi_pcie_read(NULL, 0x60000004);
	ok = ep != UINT32_MAX && rc != UINT32_MAX && !(ep & 7) && !(rc & 7);
	hakios_tl("MUNCH Wi-Fi: SID route found=%u cycle=%u; DMA NOT APPROVED\n", found, cycle);
	hakios_tl("MUNCH Wi-Fi: SID/SMMU reads %s; DMA NOT APPROVED\n", ok ? "finished" : "failed command guard");
 out:
	munch_wifi_unmap(smmu, 0x2000);
	return ok;
}
