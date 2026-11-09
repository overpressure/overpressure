// SPDX-License-Identifier: GPL-2.0-only
/*
 * The PICO 4's battery on a mainline kernel, from PM8150B's registers as the stock kernel reads
 * them: the fuel gauge's monotonic state of charge and the battery's temperature, the charger's
 * state and temperature status, and whether USB powers the headset, as the "battery" and "usb"
 * power supplies the stock kernel has. The fuel gauge and the charger run on their own (PICO's
 * boot loader configures them), except for what the stock kernel does about the battery's
 * temperature: the charger's temperature window, which the boot loader leaves wider, and the float
 * voltage by temperature (the stock charger driver's software JEITA and its soft-zone handling),
 * all from the device tree, and the end of a charge (termination current, recharge, safety timer).
 * The charge current stays the boot loader's, below the stock one.
 */
#include <linux/bitfield.h>
#include <linux/bitops.h>
#include <linux/bits.h>
#include <linux/delay.h>
#include <linux/limits.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/power_supply.h>
#include <linux/regmap.h>
#include <linux/string.h>
#include <linux/workqueue.h>

/* Fuel gauge, battery-SOC block: the monotonic state of charge, 0-255. */
#define BATT_SOC_MONOTONIC_SOC		0x09
#define FULL_SOC_RAW			255

/* Charger block: its state in the low three bits of the first status register. */
#define CHGR_BATTERY_CHARGER_STATUS_1	0x06
#define CHGR_STATE			GENMASK(2, 0)

enum chgr_state {
	CHGR_TRICKLE,
	CHGR_PRECHARGE,
	CHGR_FAST,
	CHGR_FULLON,
	CHGR_TAPER,
	CHGR_TERMINATE,
	CHGR_INHIBIT,
	CHGR_DISABLE,
};

/* Charger block: the battery's temperature status, as the stock driver maps it to health. */
#define CHGR_BATTERY_CHARGER_STATUS_7	0x0d
#define BAT_TEMP_HOT_SOFT		BIT(5)
#define BAT_TEMP_COLD_SOFT		BIT(4)
#define BAT_TEMP_TOO_HOT		BIT(3)
#define BAT_TEMP_TOO_COLD		BIT(2)

/*
 * Charger block: the JEITA thresholds, ADC codes of the battery thermistor (lower is hotter), soft
 * at 0x94 and hard at 0x98, each the hot code then the cold one, most significant byte first.
 */
#define CHGR_JEITA_SOFT			0x94
#define CHGR_JEITA_HARD			0x98

/*
 * Charger block: charge inhibit, the float voltage (3.6 V and 10 mV steps) and the charger's own
 * compensation of voltage and current in the soft zones, which the stock driver turns off.
 */
#define CHGR_CFG2			0x51
#define CHARGER_INHIBIT			BIT(0)
#define CHGR_FLOAT_VOLTAGE_CFG		0x70
#define FV_MIN_UV			3600000
#define FV_STEP_UV			10000
#define CHGR_JEITA_EN_CFG		0x90
#define JEITA_EN_COMPENSATION		GENMASK(3, 0)

/*
 * Charger block: the end of a charge as the stock driver sets it. Termination by the ADC's charge
 * current, its upper threshold signed and most significant byte first, 32767 for 10 A, judged on a
 * count of samples; recharge by the gauge's state of charge (0-255), on one sample; the fast
 * charge's safety timer.
 */
#define CHGR_RECHG			GENMASK(2, 1)
#define RECHG_BY_VBAT			BIT(2)
#define RECHG_BY_SOC			GENMASK(2, 1)
#define CHGR_ADC_ITERM_UP_THD		0x67
#define ITERM_FULL_RAW			32767
#define ITERM_FULL_MA			10000
#define CHGR_NO_SAMPLE_TERM_RCHG_CFG	0x6b
#define RCHG_SAMPLES			GENMASK(3, 2)
#define CHGR_ADC_TERM_CFG		0x6c
#define TERM_BY_SAMPLE_COUNT		BIT(0)
#define CHGR_RCHG_SOC_THRESHOLD		0x7d
#define CHGR_FAST_CHARGE_SAFETY_TIMER	0xa2
#define SAFETY_TIMER_768_MIN		0x02
#define ITERM_SRC_ADC			1

/* The stock software JEITA: up to eight temperature ranges, 1 °C hysteresis between them. */
#define JEITA_RANGES			8
#define JEITA_HYSTERESIS		10

/*
 * Fuel gauge, memory interface block: the arbitration and access request for its memory, the grant,
 * and the direct (DMA) window's error log. Its memory is read as the stock driver does on PM8150B:
 * through the direct window, whose partition for words 300-405 starts at 0x4820.
 */
#define MEM_IF_INT_RT_STS		0x10
#define MEM_GNT				BIT(3)
#define MEM_IF_MEM_ARB_CFG		0x40
#define MEM_ARB_LO_LATENCY_EN		BIT(1)
#define MEM_CLR_LOG			BIT(2)
#define MEM_ARB_REQ			BIT(0)
#define MEM_IF_MEM_INTF_CFG		0x50
#define MEM_ACCESS_REQ			BIT(7)
#define IACS_SLCT			BIT(5)
#define MEM_IF_DMA_STS			0x70
#define DMA_READ_ERROR			BIT(2)
#define DMA_WRITE_ERROR			BIT(1)
#define MEM_IF_DMA_CTL			0x71
#define ADDR_KIND			BIT(1)
#define DMA_CLEAR_LOG			BIT(0)
#define FG_DMA4_WINDOW			0x4820
#define FG_DMA4_FIRST_WORD		300
#define FG_WORD_BYTES			2
#define MEM_GNT_TRIES			50
#define MEM_GNT_WAIT_US			10000

/* The battery's temperature, word 328 of the gauge's memory: 10 bits, signed, 0.25 °C each. */
#define BATT_TEMP_WORD			328

/* USB input block: its real-time interrupt status, with the plug-in bit. */
#define USBIN_INT_RT_STS		0x10
#define USBIN_PLUGIN			BIT(4)

/* No interrupts are taken: the supplies are read on demand and checked for changes this often. */
#define POLL_INTERVAL			(30 * HZ)

struct pico4_battery {
	struct device *dev;
	struct regmap *map;
	u32 soc_base, chgr_base, usbin_base, memif_base;
	struct power_supply *battery, *usb;
	struct delayed_work poll;
	int last_capacity, last_status, last_online, last_health;
	int temp;		/* tenths of °C, from the last poll */
	bool temp_valid;
	/* From the device tree: the windows as <cold hot> codes, voltages in µV, temperatures in 0.1 °C. */
	u32 soft[2], soft_hys[2], soft_fv[2], fv_ceiling;
	struct { u32 low, high, value; } fv_ranges[JEITA_RANGES];
	int num_fv_ranges;
	bool jeita;		/* the float voltage by temperature is configured */
	int fv_index;		/* the range in use, -1 for none */
	bool soft_zone;		/* in a soft zone: the hysteresis window and its voltage */
	u32 soft_zone_fv;
	unsigned int fv_code;	/* the float voltage last written */
};

static int pico4_read(struct pico4_battery *b, u32 reg, unsigned int *val)
{
	int ret = regmap_read(b->map, reg, val);

	if (ret)
		dev_err(b->dev, "reading 0x%04x: %d\n", reg, ret);
	return ret;
}

static int pico4_update(struct pico4_battery *b, u32 reg, unsigned int mask, unsigned int val)
{
	int ret = regmap_update_bits(b->map, reg, mask, val);

	if (ret)
		dev_err(b->dev, "writing 0x%04x: %d\n", reg, ret);
	return ret;
}

/* A write of a charger register, read back to check it took. */
static int pico4_write_checked(struct pico4_battery *b, u32 reg, unsigned int mask, unsigned int val)
{
	unsigned int got;
	int ret = pico4_update(b, reg, mask, val);

	if (!ret)
		ret = pico4_read(b, reg, &got);
	if (!ret && (got & mask) != (val & mask)) {
		dev_err(b->dev, "0x%04x reads 0x%02x after writing 0x%02x\n", reg, got, val);
		ret = -EIO;
	}
	return ret;
}

/*
 * The stock charger driver's threshold write (smblib_update_jeita), a <cold hot> pair, read back
 * to check it took.
 */
static int pico4_write_jeita(struct pico4_battery *b, const char *what, u32 offset, const u32 *codes)
{
	u8 want[4], got[4];
	int ret;

	want[0] = codes[1] >> 8;
	want[1] = codes[1];
	want[2] = codes[0] >> 8;
	want[3] = codes[0];
	ret = regmap_bulk_write(b->map, b->chgr_base + offset, want, sizeof(want));
	if (!ret)
		ret = regmap_bulk_read(b->map, b->chgr_base + offset, got, sizeof(got));
	if (!ret && memcmp(want, got, sizeof(want)))
		ret = -EIO;
	if (ret)
		dev_err(b->dev, "%s thresholds: writing 0x%04x: %d\n", what, b->chgr_base + offset, ret);
	else
		dev_info(b->dev, "%s thresholds: hot 0x%04x, cold 0x%04x\n", what, codes[1], codes[0]);
	return ret;
}

/*
 * The stock software JEITA's range (step-chg-jeita.c, get_val): none below the first range, the
 * last one above the last, and a neighbouring range only once the temperature is 1 °C into it.
 */
static int pico4_fv_range(const struct pico4_battery *b, int temp, u32 *value)
{
	int i, n = b->num_fv_ranges, cur = b->fv_index;

	if (!n || temp < (int)b->fv_ranges[0].low)
		return -1;
	for (i = 0; i < n; i++)
		if (temp >= (int)b->fv_ranges[i].low && temp <= (int)b->fv_ranges[i].high)
			break;
	if (i == n)
		i = n - 1;
	if (cur >= 0 && i == cur + 1 && temp < (int)b->fv_ranges[i].low + JEITA_HYSTERESIS)
		i = cur;
	else if (cur >= 0 && i == cur - 1 && temp > (int)b->fv_ranges[i].high - JEITA_HYSTERESIS)
		i = cur;
	*value = b->fv_ranges[i].value;
	return i;
}

/*
 * The stock charger driver's soft zones (smblib_soft_jeita_arb_wa): entering one from a normal
 * temperature, charge inhibit on, the window widened to the hysteresis codes and the zone's
 * voltage; leaving it, all back. Nothing changes at the hard limits, where the charger stops.
 */
static void pico4_soft_zone(struct pico4_battery *b, int health)
{
	bool soft = health == POWER_SUPPLY_HEALTH_COOL || health == POWER_SUPPLY_HEALTH_WARM;

	if (health == POWER_SUPPLY_HEALTH_COLD || health == POWER_SUPPLY_HEALTH_OVERHEAT ||
	    soft == b->soft_zone)
		return;
	b->soft_zone = soft;
	pico4_write_checked(b, b->chgr_base + CHGR_CFG2, CHARGER_INHIBIT, soft ? CHARGER_INHIBIT : 0);
	pico4_write_jeita(b, soft ? "soft-zone hysteresis" : "soft", CHGR_JEITA_SOFT,
			  soft ? b->soft_hys : b->soft);
	b->soft_zone_fv = health == POWER_SUPPLY_HEALTH_COOL ? b->soft_fv[0] : b->soft_fv[1];
	dev_info(b->dev, "%s the soft zone\n", soft ? "in" : "out of");
}

/*
 * The float voltage as the stock driver votes it, the lowest of the ceiling, the temperature's
 * range and the soft zone's voltage. Unchanged while there is no temperature, as in the stock driver.
 */
static void pico4_float_voltage(struct pico4_battery *b, int temp)
{
	u32 fv = b->fv_ceiling;
	unsigned int code;
	u32 value = 0;

	b->fv_index = pico4_fv_range(b, temp, &value);
	if (b->fv_index >= 0 && value < fv)
		fv = value;
	if (b->soft_zone && b->soft_zone_fv < fv)
		fv = b->soft_zone_fv;
	code = (fv - FV_MIN_UV) / FV_STEP_UV;
	if (code == b->fv_code)
		return;
	if (!pico4_write_checked(b, b->chgr_base + CHGR_FLOAT_VOLTAGE_CFG, 0xff, code)) {
		dev_info(b->dev, "float voltage %u mV at %s%d.%d C\n", FV_MIN_UV / 1000 + code * 10,
			 temp < 0 ? "-" : "", abs(temp) / 10, abs(temp) % 10);
		b->fv_code = code;
	}
}

/*
 * The stock temperature handling from the tree. Without every property the boot loader's
 * float voltage and the charger's compensation stay as they are.
 */
static void pico4_jeita_init(struct pico4_battery *b)
{
	struct device_node *np = b->dev->of_node;
	u32 hard[2], ranges[JEITA_RANGES * 3];
	unsigned int fv;
	int n, i;

	if (!of_property_read_u32_array(np, "qcom,jeita-hard-thresholds", hard, 2))
		pico4_write_jeita(b, "hard", CHGR_JEITA_HARD, hard);
	if (of_property_read_u32_array(np, "qcom,jeita-soft-thresholds", b->soft, 2))
		return;
	pico4_write_jeita(b, "soft", CHGR_JEITA_SOFT, b->soft);

	n = of_property_count_u32_elems(np, "qcom,jeita-fv-ranges");
	if (n <= 0 || n % 3 || n > JEITA_RANGES * 3 ||
	    of_property_read_u32_array(np, "qcom,jeita-fv-ranges", ranges, n) ||
	    of_property_read_u32_array(np, "qcom,jeita-soft-hys-thresholds", b->soft_hys, 2) ||
	    of_property_read_u32_array(np, "qcom,jeita-soft-fv-uv", b->soft_fv, 2) ||
	    of_property_read_u32(np, "pico,float-voltage-max-uv", &b->fv_ceiling)) {
		dev_err(b->dev, "no float voltage by temperature: incomplete JEITA properties\n");
		return;
	}
	b->num_fv_ranges = n / 3;
	for (i = 0; i < b->num_fv_ranges; i++) {
		b->fv_ranges[i].low = ranges[3 * i];
		b->fv_ranges[i].high = ranges[3 * i + 1];
		b->fv_ranges[i].value = ranges[3 * i + 2];
	}
	if (!pico4_read(b, b->chgr_base + CHGR_FLOAT_VOLTAGE_CFG, &fv))
		dev_info(b->dev, "boot loader's float voltage %u mV, ceiling %u mV\n",
			 FV_MIN_UV / 1000 + fv * 10, b->fv_ceiling / 1000);
	b->fv_index = -1;
	b->fv_code = UINT_MAX;
	/* The stock driver's start: no soft zone, the charger's compensation off (software JEITA). */
	if (pico4_write_checked(b, b->chgr_base + CHGR_CFG2, CHARGER_INHIBIT, 0) ||
	    pico4_write_checked(b, b->chgr_base + CHGR_JEITA_EN_CFG, JEITA_EN_COMPENSATION, 0))
		return;
	b->jeita = true;
}

/*
 * The end of a charge as the stock driver sets it up (smb5_init_hw): termination by the ADC's
 * charge current (qcom,chg-term-src 1, qcom,chg-term-current-ma, negative while charging) judged
 * on a count of samples, the fast charge's 768-minute safety timer, and recharge by the state of
 * charge (qcom,auto-recharge-soc) on one sample. The boot loader leaves -100 mA, 384 minutes and a
 * recharge by voltage. Without the properties its settings stay.
 */
static void pico4_charge_end_init(struct pico4_battery *b)
{
	struct device_node *np = b->dev->of_node;
	u32 src, term_u32, soc;
	u8 want[2], got[2];
	s32 term_ma, raw;
	int ret;

	if (!of_property_read_u32(np, "qcom,chg-term-src", &src) && src == ITERM_SRC_ADC &&
	    !of_property_read_u32(np, "qcom,chg-term-current-ma", &term_u32)) {
		term_ma = (s32)term_u32;
		if (term_ma < -ITERM_FULL_MA || term_ma > ITERM_FULL_MA) {
			dev_err(b->dev, "termination current %d mA out of range\n", term_ma);
		} else if (!pico4_write_checked(b, b->chgr_base + CHGR_ADC_TERM_CFG, TERM_BY_SAMPLE_COUNT,
					       TERM_BY_SAMPLE_COUNT)) {
			/* The stock conversion, truncated towards zero. */
			raw = (s32)((s64)term_ma * ITERM_FULL_RAW / ITERM_FULL_MA);
			want[0] = (u16)raw >> 8;
			want[1] = (u16)raw & 0xff;
			ret = regmap_bulk_write(b->map, b->chgr_base + CHGR_ADC_ITERM_UP_THD, want, sizeof(want));
			if (!ret)
				ret = regmap_bulk_read(b->map, b->chgr_base + CHGR_ADC_ITERM_UP_THD, got, sizeof(got));
			if (!ret && memcmp(want, got, sizeof(want)))
				ret = -EIO;
			if (ret)
				dev_err(b->dev, "termination current: %d\n", ret);
			else
				dev_info(b->dev, "charge ends at %d mA\n", term_ma);
		}
	}
	if (pico4_write_checked(b, b->chgr_base + CHGR_FAST_CHARGE_SAFETY_TIMER, 0xff, SAFETY_TIMER_768_MIN))
		dev_err(b->dev, "the fast charge's safety timer stays the boot loader's\n");
	if (of_property_read_u32(np, "qcom,auto-recharge-soc", &soc))
		return;
	if (soc > 100) {
		dev_err(b->dev, "recharge at %u %% out of range\n", soc);
		return;
	}
	/* The stock order: the mode, its threshold, then one sample. */
	if (!pico4_write_checked(b, b->chgr_base + CHGR_CFG2, CHGR_RECHG, RECHG_BY_SOC) &&
	    !pico4_write_checked(b, b->chgr_base + CHGR_RCHG_SOC_THRESHOLD, 0xff,
				 DIV_ROUND_CLOSEST(soc * 255, 100)) &&
	    !pico4_write_checked(b, b->chgr_base + CHGR_NO_SAMPLE_TERM_RCHG_CFG, RCHG_SAMPLES, 0))
		dev_info(b->dev, "recharge below %u %%\n", soc);
}

/*
 * The stock gauge driver's set-up of the direct window (fg_dma_init): clear a logged window error,
 * address by partition, release any access, low latency on and the arbiter's log clear off. The
 * peek multiplexer it also sets serves workarounds this driver does not run.
 */
static int pico4_memif_init(struct pico4_battery *b)
{
	unsigned int sts;
	int ret;

	ret = pico4_read(b, b->memif_base + MEM_IF_DMA_STS, &sts);
	if (!ret)
		ret = pico4_update(b, b->memif_base + MEM_IF_DMA_CTL, DMA_CLEAR_LOG,
				   sts & (DMA_READ_ERROR | DMA_WRITE_ERROR) ? DMA_CLEAR_LOG : 0);
	if (!ret)
		ret = pico4_update(b, b->memif_base + MEM_IF_DMA_CTL, ADDR_KIND, ADDR_KIND);
	if (!ret)
		ret = pico4_update(b, b->memif_base + MEM_IF_MEM_INTF_CFG, MEM_ACCESS_REQ | IACS_SLCT, 0);
	if (!ret)
		ret = pico4_update(b, b->memif_base + MEM_IF_MEM_ARB_CFG, MEM_ARB_REQ, 0);
	if (!ret)
		ret = pico4_update(b, b->memif_base + MEM_IF_MEM_ARB_CFG,
				   MEM_ARB_LO_LATENCY_EN | MEM_CLR_LOG, MEM_ARB_LO_LATENCY_EN);
	return ret;
}

/*
 * One word of the gauge's memory as the stock driver reads it (fg_direct_mem_read): request the
 * arbiter and access, wait for the grant, read the window, and always release.
 */
static int pico4_read_word(struct pico4_battery *b, unsigned int word, u16 *val)
{
	unsigned int sts = 0;
	u8 buf[FG_WORD_BYTES];
	int i, ret, release;

	ret = pico4_update(b, b->memif_base + MEM_IF_MEM_ARB_CFG, MEM_ARB_REQ, MEM_ARB_REQ);
	if (ret)
		return ret;
	ret = pico4_update(b, b->memif_base + MEM_IF_MEM_INTF_CFG, MEM_ACCESS_REQ | IACS_SLCT,
			   MEM_ACCESS_REQ);
	if (ret)
		goto release;
	/* The grant takes five cycles of the gauge's 200 kHz clock. */
	usleep_range(40, 41);
	for (i = 0; i < MEM_GNT_TRIES; i++) {
		ret = pico4_read(b, b->memif_base + MEM_IF_INT_RT_STS, &sts);
		if (ret || sts & MEM_GNT)
			break;
		usleep_range(MEM_GNT_WAIT_US, MEM_GNT_WAIT_US + 1);
	}
	if (!ret && !(sts & MEM_GNT)) {
		dev_err_ratelimited(b->dev, "no grant for the gauge's memory: 0x%02x\n", sts);
		ret = -ETIMEDOUT;
	}
	if (!ret)
		ret = regmap_bulk_read(b->map, FG_DMA4_WINDOW + (word - FG_DMA4_FIRST_WORD) * FG_WORD_BYTES,
				       buf, sizeof(buf));
	if (!ret)
		*val = buf[0] | buf[1] << 8;
release:
	release = pico4_update(b, b->memif_base + MEM_IF_MEM_INTF_CFG, MEM_ACCESS_REQ | IACS_SLCT, 0);
	if (!release)
		release = pico4_update(b, b->memif_base + MEM_IF_MEM_ARB_CFG, MEM_ARB_REQ, 0);
	return ret ? ret : release;
}

/* The stock gauge driver's battery temperature, in tenths of °C. */
static int pico4_temp(struct pico4_battery *b, int *temp)
{
	u16 raw = 0;
	int ret = pico4_read_word(b, BATT_TEMP_WORD, &raw);

	if (!ret)
		*temp = sign_extend32(raw, 9) * 100 / 40;
	return ret;
}

/* The stock charger driver's health, from the charger's view of the thermistor. */
static int pico4_health(struct pico4_battery *b, int *health)
{
	unsigned int sts;
	int ret = pico4_read(b, b->chgr_base + CHGR_BATTERY_CHARGER_STATUS_7, &sts);

	if (ret)
		return ret;
	if (sts & BAT_TEMP_TOO_COLD)
		*health = POWER_SUPPLY_HEALTH_COLD;
	else if (sts & BAT_TEMP_TOO_HOT)
		*health = POWER_SUPPLY_HEALTH_OVERHEAT;
	else if (sts & BAT_TEMP_COLD_SOFT)
		*health = POWER_SUPPLY_HEALTH_COOL;
	else if (sts & BAT_TEMP_HOT_SOFT)
		*health = POWER_SUPPLY_HEALTH_WARM;
	else
		*health = POWER_SUPPLY_HEALTH_GOOD;
	return 0;
}

/*
 * The stock fuel gauge driver's percentage: full and empty as they are, everything in between
 * spread over 1-99, so that a battery shows neither 0 % nor 100 % before it is empty or full.
 */
static int pico4_capacity(struct pico4_battery *b, int *capacity)
{
	unsigned int raw;
	int ret = pico4_read(b, b->soc_base + BATT_SOC_MONOTONIC_SOC, &raw);

	if (ret)
		return ret;
	if (raw >= FULL_SOC_RAW)
		*capacity = 100;
	else if (raw == 0)
		*capacity = 0;
	else
		*capacity = DIV_ROUND_CLOSEST((raw - 1) * 98, FULL_SOC_RAW - 2) + 1;
	return 0;
}

static int pico4_online(struct pico4_battery *b, int *online)
{
	unsigned int sts;
	int ret = pico4_read(b, b->usbin_base + USBIN_INT_RT_STS, &sts);

	if (!ret)
		*online = !!(sts & USBIN_PLUGIN);
	return ret;
}

/* The stock charger driver's status: charging in every charging state, full once terminated. */
static int pico4_status(struct pico4_battery *b, int *status)
{
	unsigned int sts;
	int online, ret;

	ret = pico4_online(b, &online);
	if (ret)
		return ret;
	ret = pico4_read(b, b->chgr_base + CHGR_BATTERY_CHARGER_STATUS_1, &sts);
	if (ret)
		return ret;
	if (!online) {
		*status = POWER_SUPPLY_STATUS_DISCHARGING;
		return 0;
	}
	switch (FIELD_GET(CHGR_STATE, sts)) {
	case CHGR_TRICKLE:
	case CHGR_PRECHARGE:
	case CHGR_FAST:
	case CHGR_FULLON:
	case CHGR_TAPER:
		*status = POWER_SUPPLY_STATUS_CHARGING;
		break;
	case CHGR_TERMINATE:
		*status = POWER_SUPPLY_STATUS_FULL;
		break;
	default:
		*status = POWER_SUPPLY_STATUS_NOT_CHARGING;
		break;
	}
	return 0;
}

static int pico4_battery_get(struct power_supply *psy, enum power_supply_property psp,
			     union power_supply_propval *val)
{
	struct pico4_battery *b = power_supply_get_drvdata(psy);

	switch (psp) {
	case POWER_SUPPLY_PROP_PRESENT:
		val->intval = 1;
		return 0;
	case POWER_SUPPLY_PROP_TECHNOLOGY:
		val->intval = POWER_SUPPLY_TECHNOLOGY_LION;
		return 0;
	case POWER_SUPPLY_PROP_CAPACITY:
		return pico4_capacity(b, &val->intval);
	case POWER_SUPPLY_PROP_STATUS:
		return pico4_status(b, &val->intval);
	case POWER_SUPPLY_PROP_HEALTH:
		return pico4_health(b, &val->intval);
	case POWER_SUPPLY_PROP_TEMP:
		/* From the last poll: a read of the gauge's memory can wait half a second for its grant. */
		if (!READ_ONCE(b->temp_valid))
			return -ENODATA;
		val->intval = READ_ONCE(b->temp);
		return 0;
	default:
		return -EINVAL;
	}
}

static int pico4_usb_get(struct power_supply *psy, enum power_supply_property psp,
			 union power_supply_propval *val)
{
	struct pico4_battery *b = power_supply_get_drvdata(psy);

	if (psp != POWER_SUPPLY_PROP_ONLINE)
		return -EINVAL;
	return pico4_online(b, &val->intval);
}

static const enum power_supply_property pico4_battery_props[] = {
	POWER_SUPPLY_PROP_PRESENT,
	POWER_SUPPLY_PROP_TECHNOLOGY,
	POWER_SUPPLY_PROP_CAPACITY,
	POWER_SUPPLY_PROP_STATUS,
	POWER_SUPPLY_PROP_HEALTH,
	POWER_SUPPLY_PROP_TEMP,
};

static const enum power_supply_property pico4_usb_props[] = {
	POWER_SUPPLY_PROP_ONLINE,
};

static const struct power_supply_desc pico4_battery_desc = {
	.name = "battery",
	.type = POWER_SUPPLY_TYPE_BATTERY,
	.properties = pico4_battery_props,
	.num_properties = ARRAY_SIZE(pico4_battery_props),
	.get_property = pico4_battery_get,
};

static const struct power_supply_desc pico4_usb_desc = {
	.name = "usb",
	.type = POWER_SUPPLY_TYPE_USB,
	.properties = pico4_usb_props,
	.num_properties = ARRAY_SIZE(pico4_usb_props),
	.get_property = pico4_usb_get,
};

/*
 * Read the temperature, set the float voltage for it, and tell userspace (UPower, the session)
 * when the level, the status, the health or the input changes.
 */
static void pico4_poll(struct work_struct *work)
{
	struct pico4_battery *b = container_of(work, struct pico4_battery, poll.work);
	int capacity, status, online, health, temp = 0;
	bool have_temp = !pico4_temp(b, &temp);

	if (have_temp)
		WRITE_ONCE(b->temp, temp);
	WRITE_ONCE(b->temp_valid, have_temp);
	if (b->jeita && !pico4_health(b, &health)) {
		pico4_soft_zone(b, health);
		if (have_temp)
			pico4_float_voltage(b, temp);
	}
	if (!pico4_capacity(b, &capacity) && !pico4_status(b, &status) && !pico4_online(b, &online) &&
	    !pico4_health(b, &health)) {
		if (online != b->last_online)
			power_supply_changed(b->usb);
		if (capacity != b->last_capacity || status != b->last_status || online != b->last_online ||
		    health != b->last_health)
			power_supply_changed(b->battery);
		b->last_capacity = capacity;
		b->last_status = status;
		b->last_online = online;
		b->last_health = health;
	}
	schedule_delayed_work(&b->poll, POLL_INTERVAL);
}

static void pico4_cancel_poll(void *data)
{
	struct pico4_battery *b = data;

	cancel_delayed_work_sync(&b->poll);
}

static int pico4_battery_probe(struct platform_device *pdev)
{
	struct power_supply_config cfg = {};
	struct device *dev = &pdev->dev;
	struct pico4_battery *b;
	int ret;

	b = devm_kzalloc(dev, sizeof(*b), GFP_KERNEL);
	if (!b)
		return -ENOMEM;
	b->dev = dev;
	b->map = dev_get_regmap(dev->parent, NULL);
	if (!b->map)
		return dev_err_probe(dev, -ENODEV, "no regmap of the PMIC\n");
	/* The fuel gauge's SOC block, the charger, the USB input and the gauge's memory interface. */
	if (of_property_read_u32_index(dev->of_node, "reg", 0, &b->soc_base) ||
	    of_property_read_u32_index(dev->of_node, "reg", 1, &b->chgr_base) ||
	    of_property_read_u32_index(dev->of_node, "reg", 2, &b->usbin_base) ||
	    of_property_read_u32_index(dev->of_node, "reg", 3, &b->memif_base))
		return dev_err_probe(dev, -EINVAL,
				     "reg needs the SOC, charger, USB-in and memory interface blocks\n");

	/*
	 * The stock order, hard then soft. A failed write leaves the boot loader's setting and is
	 * logged; the battery is still reported.
	 */
	pico4_jeita_init(b);
	pico4_charge_end_init(b);
	if (pico4_memif_init(b))
		dev_err(dev, "the gauge's memory window: no battery temperature\n");

	cfg.drv_data = b;
	cfg.fwnode = dev_fwnode(dev);
	b->battery = devm_power_supply_register(dev, &pico4_battery_desc, &cfg);
	if (IS_ERR(b->battery))
		return dev_err_probe(dev, PTR_ERR(b->battery), "registering the battery\n");
	cfg.fwnode = NULL;
	b->usb = devm_power_supply_register(dev, &pico4_usb_desc, &cfg);
	if (IS_ERR(b->usb))
		return dev_err_probe(dev, PTR_ERR(b->usb), "registering the USB input\n");

	b->last_capacity = b->last_status = b->last_online = b->last_health = -1;
	INIT_DELAYED_WORK(&b->poll, pico4_poll);
	ret = devm_add_action_or_reset(dev, pico4_cancel_poll, b);
	if (ret)
		return ret;
	schedule_delayed_work(&b->poll, 0);
	return 0;
}

static const struct of_device_id pico4_battery_of_match[] = {
	{ .compatible = "pico,phoenix-pm8150b-battery" },
	{}
};
MODULE_DEVICE_TABLE(of, pico4_battery_of_match);

static struct platform_driver pico4_battery_driver = {
	.probe = pico4_battery_probe,
	.driver = {
		.name = "pico4-pm8150b-battery",
		.of_match_table = pico4_battery_of_match,
	},
};
module_platform_driver(pico4_battery_driver);

MODULE_DESCRIPTION("PICO 4 battery level, temperature and charger state from PM8150B");
MODULE_LICENSE("GPL");
