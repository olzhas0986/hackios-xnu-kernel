/* munch PCIe0 endpoint enumeration. The platform owns mappings and RPMh votes. */
#ifndef _MUNCH_WIFI_PCIE_H_
#define _MUNCH_WIFI_PCIE_H_
#include <stdbool.h>
#include <stdint.h>

struct munch_pcie_io {
	void *context;
	uint32_t (*read32)(void *, uint32_t physical_address);
	void (*write32)(void *, uint32_t physical_address, uint32_t value);
	void (*write16)(void *, uint32_t physical_address, uint16_t value);
	int (*power_on)(void *); /* Board-specific rail votes; no endpoint DMA. */
	void (*delay_us)(void *, unsigned);
	void (*report)(void *, const char *, uint32_t);
};
struct munch_pcie {
	const struct munch_pcie_io *io;
	bool failed, enumerated;
};
/* Explicit diagnostic only, on a zero-initialized host. Bus mastering stays
 * disabled. No SMMU, MSI or MHI context is installed by this component.
 */
static int munch_pcie_enumerate(struct munch_pcie *, const struct munch_pcie_io *);

static int munch_pcie_quiesce(struct munch_pcie *);
#endif
