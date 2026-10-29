/* POCO F4 PCIe0 bootstrap. Register values and MPC_PHY data are from the
 * merged munch stock DT and Kona register definitions. This diagnostic stops
 * with QCA6390 I/O, memory decoding and bus mastering disabled.
 */
#include "munch-wifi-pcie.h"
#include <sys/errno.h>
#include "munch-wifi-phy.h"

#define MPC_GCC  0x00100000u
#define MPC_GPIO 0x0f900000u /* Kona NORTH tile: TLMM base + 0x900000. */
#define MPC_PARF 0x01c00000u
#define MPC_PHY  0x01c06000u
#define MPC_DBI  0x60000000u
#define MPC_ATU  0x60001000u
#define MPC_CONF 0x60100000u
#define MPC_BIT(n) (1u << (n))
#define MPC_TRY(expr) do { error = (expr); if (error) goto fail; } while (0)

static uint32_t mpc_rd(struct munch_pcie *p, uint32_t addr)
{
	return p->io->read32(p->io->context, addr);
}
static void mpc_wr(struct munch_pcie *p, uint32_t addr, uint32_t value)
{
	p->io->write32(p->io->context, addr, value);
}
static void mpc_pause_us(struct munch_pcie *p, unsigned us)
{
	p->io->delay_us(p->io->context, us);
}
static void mpc_report(struct munch_pcie *p, const char *name, uint32_t value)
{
	if (p->io->report) p->io->report(p->io->context, name, value);
}
static int mpc_update(struct munch_pcie *p, uint32_t addr, uint32_t clear, uint32_t set)
{
	uint32_t value = mpc_rd(p, addr);
	if (value == UINT32_MAX) return EIO;
	uint32_t wanted = (value & ~clear) | set;
	mpc_wr(p, addr, wanted);
	uint32_t now = mpc_rd(p, addr);
	return now == UINT32_MAX || ((now ^ wanted) & (clear | set)) ? EIO : 0;
}
static int mpc_wait_bits(struct munch_pcie *p, uint32_t addr, uint32_t mask,
    uint32_t wanted, unsigned timeout_us)
{
	uint32_t value = 0;
	for (unsigned us = 0; us < timeout_us; us += 10) {
		value = mpc_rd(p, addr);
		if (value == UINT32_MAX) return EIO;
		if ((value & mask) == wanted) return 0;
		mpc_pause_us(p, 10);
	}
	mpc_report(p, "poll timeout address", addr);
	mpc_report(p, "poll timeout value", value);
	return ETIMEDOUT;
}
static int mpc_gpio_output(struct munch_pcie *p, unsigned pin, bool high, uint32_t config)
{
	/* Set output latch before output-enable/mux to avoid a reset glitch. */
	int error = mpc_update(p, MPC_GPIO + pin * 0x1000 + 4, MPC_BIT(1), high ? MPC_BIT(1) : 0);
	if (error) return error;
	return mpc_update(p, MPC_GPIO + pin * 0x1000, 0x3ff, config | MPC_BIT(9));
}
static int mpc_source_xo(struct munch_pcie *p, uint32_t cmd)
{
	/* 19.2 MHz XO, divide by 1. This is the stock Gen1/Gen2 refgen rate. */
	int error = mpc_update(p, cmd + 4, 0x371f, 1);
	if (error) return error;
	uint32_t value = mpc_rd(p, cmd);
	if (value == UINT32_MAX) return EIO;
	mpc_wr(p, cmd, value | 1); /* self-clearing update strobe */
	return mpc_wait_bits(p, cmd, 1, 0, 10000);
}
static int mpc_branch(struct munch_pcie *p, uint32_t offset)
{
	int error = mpc_update(p, MPC_GCC + offset, 0, 1);
	return error ? error : mpc_wait_bits(p, MPC_GCC + offset, MPC_BIT(31), 0, 10000);
}
static int mpc_clocks(struct munch_pcie *p)
{
	int error;
	/* GDSC status is checked before accessing the PCIe controller/MPC_PHY. */
	MPC_TRY(mpc_update(p, MPC_GCC + 0x6b004, 7, 0));
	mpc_pause_us(p, 1);
	MPC_TRY(mpc_wait_bits(p, MPC_GCC + 0x6b004, MPC_BIT(31), MPC_BIT(31), 10000));
	MPC_TRY(mpc_source_xo(p, MPC_GCC + 0x6b038));
	MPC_TRY(mpc_source_xo(p, MPC_GCC + 0x6f014));
	MPC_TRY(mpc_branch(p, 0x8c004)); /* QCA6390 reference clock/LDO. */
	MPC_TRY(mpc_branch(p, 0x6f02c));
	MPC_TRY(mpc_branch(p, 0x9000c));
	MPC_TRY(mpc_branch(p, 0x8d058));
	/* The AXI and PIPE clocks must not retain memory/peripheral state. */
	MPC_TRY(mpc_update(p, MPC_GCC + 0x6b01c, 0x6000, 0));
	/* Force diagnostic AHB/SLV clocks on. In HWCG mode the halt bit can
	 * assert merely because the bus is idle, so it is not a readiness test. */
	MPC_TRY(mpc_update(p, MPC_GCC + 0x6b014, 0x6002, 0));
	MPC_TRY(mpc_update(p, MPC_GCC + 0x6b024, MPC_BIT(1), 0));
	MPC_TRY(mpc_update(p, MPC_GCC + 0x6b02c, 0x6000, 0));
	MPC_TRY(mpc_update(p, MPC_GCC + 0x52008, 0, 0x2f)); /* All PCIe0 votes except PIPE. */
	static const uint32_t gates[] = {0x6b010, 0x6b014, 0x6b01c, 0x6b024, 0x6b028};
	for (unsigned i = 0; i < sizeof(gates) / sizeof(gates[0]); i++)
		MPC_TRY(mpc_wait_bits(p, MPC_GCC + gates[i], MPC_BIT(31), 0, 10000));
	static const uint32_t resets[] = {0x6b000, 0x6c01c};
	for (unsigned i = 0; i < sizeof(resets) / sizeof(resets[0]); i++) {
		MPC_TRY(mpc_update(p, MPC_GCC + resets[i], 0, 1));
		mpc_pause_us(p, 1000);
		MPC_TRY(mpc_update(p, MPC_GCC + resets[i], 1, 0));
	}
	return 0;
fail:
	return error;
}
static int mpc_outbound(struct munch_pcie *p, unsigned index, uint32_t type,
    uint32_t start, uint32_t end, uint32_t target)
{
	uint32_t a = MPC_ATU + index * 0x200;
	mpc_wr(p, a + 4, 0);
	mpc_wr(p, a, type);
	mpc_wr(p, a + 8, start);
	mpc_wr(p, a + 12, 0);
	mpc_wr(p, a + 16, end);
	mpc_wr(p, a + 20, target);
	mpc_wr(p, a + 24, 0);
	mpc_wr(p, a + 4, MPC_BIT(31));
	return mpc_wait_bits(p, a + 4, MPC_BIT(31), MPC_BIT(31), 1000);
}

static int munch_pcie_quiesce(struct munch_pcie *p)
{
	if (!p || !p->io) return EINVAL;
	int reset_error = mpc_gpio_output(p, 79, false, 1);
	mpc_pause_us(p, 1000); /* PERST propagation before the SMMU can be restored. */
	int wlan_error = mpc_gpio_output(p, 20, false, 1);
	p->enumerated = false;
	uint32_t reset_io = mpc_rd(p, MPC_GPIO + 79*0x1000 + 4);
	uint32_t wlan_io = mpc_rd(p, MPC_GPIO + 20*0x1000 + 4);
	return reset_error || wlan_error || reset_io == UINT32_MAX || wlan_io == UINT32_MAX ||
	    (reset_io & 2) || (wlan_io & 2) ? EIO : 0;
}

static int munch_pcie_enumerate(struct munch_pcie *p, const struct munch_pcie_io *io)
{
	if (!p || !io || !io->read32 || !io->write32 || !io->write16 || !io->power_on || !io->delay_us)
		return EINVAL;
	if (p->io) return EBUSY;
	p->io = io;
	int error;
	MPC_TRY(mpc_gpio_output(p, 79, false, 1));
	mpc_pause_us(p, 1000);
	MPC_TRY(io->power_on(io->context));
	/* Matching captured munch/UMI stock pinctrl: CLKREQ mux1/pull-up; WAKE input/pull-up. */
	MPC_TRY(mpc_update(p, MPC_GPIO + 80 * 0x1000, 0x3ff, 7));
	MPC_TRY(mpc_update(p, MPC_GPIO + 81 * 0x1000, 0x3ff, 3));
	MPC_TRY(mpc_gpio_output(p, 20, true, 3 | (7u << 6)));
	mpc_pause_us(p, 1000);
	MPC_TRY(mpc_clocks(p));
	mpc_report(p, "power and clocks ready", mpc_rd(p, MPC_GCC + 0x6b004));
	mpc_wr(p, MPC_PARF + 0x1000, 4); /* Root complex. */
	MPC_TRY(mpc_update(p, MPC_PARF + 0x40, 1, 0));
	mpc_wr(p, MPC_PARF + 0x350, 0);
	mpc_wr(p, MPC_PARF, 0x365e);
	MPC_TRY(mpc_update(p, MPC_PARF + 0x174, 0, MPC_BIT(4)));
	mpc_wr(p, MPC_PARF + 0x22c, 0); /* No unhandled PCIe interrupts during enumeration. */
	mpc_wr(p, MPC_PARF + 0x358, 0x04000000);
	MPC_TRY(mpc_update(p, MPC_PARF + 0x1a8, 0, MPC_BIT(31)));
	for (unsigned i = 0; i < sizeof(munch_pcie_phy) / sizeof(munch_pcie_phy[0]); i++) {
		mpc_wr(p, MPC_PHY + munch_pcie_phy[i].offset, munch_pcie_phy[i].value);
		if (munch_pcie_phy[i].delay_us) mpc_pause_us(p, munch_pcie_phy[i].delay_us);
	}
	mpc_pause_us(p, 1000);
	MPC_TRY(mpc_update(p, MPC_GCC + 0x52008, 0, MPC_BIT(4))); /* PIPE: vendor skips halt polling. */
	MPC_TRY(mpc_wait_bits(p, MPC_PHY + 0x814, MPC_BIT(6), 0, 100000));
	mpc_report(p, "MPC_PHY ready", mpc_rd(p, MPC_PHY + 0x814));
	mpc_pause_us(p, 10000); /* munch DT ep-latency=10 ms. */
	MPC_TRY(mpc_gpio_output(p, 79, true, 1));
	mpc_pause_us(p, 1000);
	uint32_t rc = mpc_rd(p, MPC_DBI + 4);
	if (rc == UINT32_MAX) { error = EIO; goto fail; }
	io->write16(io->context, MPC_DBI + 4, (rc & ~7u) | MPC_BIT(10));
	if (mpc_rd(p, MPC_DBI + 4) & 7u) { error = EIO; goto fail; }
	MPC_TRY(mpc_update(p, MPC_DBI + 0x80c, 0x1f00, 0x100));
	mpc_wr(p, MPC_DBI + 0x8a8, 0x20);
	MPC_TRY(mpc_update(p, MPC_DBI + 0x890, MPC_BIT(0), 0));
	MPC_TRY(mpc_update(p, MPC_DBI + 0x8bc, 0, MPC_BIT(0)));
	mpc_wr(p, MPC_DBI + 0x154, 0x55555555);
	MPC_TRY(mpc_update(p, MPC_DBI + 0x8bc, MPC_BIT(0), 0));
	MPC_TRY(mpc_update(p, MPC_DBI + 0xa0, 0xf, 2)); /* Gen2 x1: stock munch link rate. */
	/* Write only the control half of the PCIe capability (upper half is W1C). */
	uint32_t control = mpc_rd(p, MPC_DBI + 0x78);
	if (control == UINT32_MAX) { error = EIO; goto fail; }
	io->write16(io->context, MPC_DBI + 0x78, (control & 0xffff) & ~0x7000u);
	MPC_TRY(mpc_update(p, MPC_PARF + 0x1b0, 0, MPC_BIT(8)));
	MPC_TRY(mpc_wait_bits(p, MPC_DBI + 0xf28, MPC_BIT(10), MPC_BIT(10), 100000));
	MPC_TRY(mpc_wait_bits(p, MPC_DBI + 0x80, MPC_BIT(29), MPC_BIT(29), 100000));
	mpc_report(p, "link status", mpc_rd(p, MPC_DBI + 0x80));
	mpc_wr(p, MPC_DBI + 0x18, 0x00010100); /* Primary 0, secondary/subordinate 1. */
	mpc_wr(p, MPC_DBI + 0xb40, 0x14); /* AUX 19.2 MHz. */
	MPC_TRY(mpc_outbound(p, 0, 4, MPC_CONF, MPC_CONF + 0xfff, 0x01000000)); /* CFG0, 01:00.0 */
	uint32_t id = 0;
	for (unsigned i = 0; i < 1000; i++) {
		id = mpc_rd(p, MPC_CONF);
		if (id == 0x110117cb) break;
		mpc_pause_us(p, 1000);
	}
	mpc_report(p, "endpoint PCI ID", id);
	if (id != 0x110117cb) { error = ENODEV; goto fail; }
	uint32_t command = mpc_rd(p, MPC_CONF + 4);
	if (command == UINT32_MAX) { error = EIO; goto fail; }
	io->write16(io->context, MPC_CONF + 4, (command & ~7u) | MPC_BIT(10));
	uint32_t ep_command = mpc_rd(p, MPC_CONF + 4), rc_command = mpc_rd(p, MPC_DBI + 4);
	mpc_report(p, "endpoint command (DMA disabled)", ep_command & 0xffff);
	mpc_report(p, "root command (DMA disabled)", rc_command & 0xffff);
	if (ep_command == UINT32_MAX || rc_command == UINT32_MAX ||
	    (ep_command & 7) || (rc_command & 4)) { error = EIO; goto fail; }
	uint32_t subsystem = mpc_rd(p, MPC_CONF + 0x2c);
	mpc_report(p, "endpoint subsystem", subsystem);
	mpc_report(p, "endpoint class/revision", mpc_rd(p, MPC_CONF + 8));
	if (subsystem != 0x010817cb) { error = ENODEV; goto fail; }
	/* No SID table, BAR assignment, memory MPC_ATU, MSI, MHI or SMMU writes. */
	p->enumerated = true;
	return 0;
fail:
	p->failed = true;
	mpc_report(p, "enumeration failed errno", (uint32_t)error);
	munch_pcie_quiesce(p);
	return error;
}
