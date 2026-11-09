// SPDX-License-Identifier: GPL-2.0-only
/*
 * The PICO 4's battery on a mainline kernel, from PM8150B's registers as the stock kernel reads
 * them: the fuel gauge's monotonic state of charge and the battery's temperature, the charger's
 * state and temperature status, and whether USB powers the headset, as the "battery" and "usb"
 * power supplies the stock kernel has. The fuel gauge and the charger run on their own (PICO's
 * boot loader configures them); the one setting written is the charger's temperature window, the
 * stock kernel's thresholds from the device tree, which the boot loader leaves wider.
 */
#include <linux/bitfield.h>
#include <linux/bitops.h>
#include <linux/bits.h>
#include <linux/delay.h>
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

/*
 * The stock charger driver's threshold write (smblib_update_jeita), from the property's <cold hot>
 * pair, read back to check it took.
 */
static int pico4_write_jeita(struct pico4_battery *b, const char *prop, u32 offset)
{
	u32 codes[2];
	u8 want[4], got[4];
	int ret;

	ret = of_property_read_u32_array(b->dev->of_node, prop, codes, 2);
	if (ret)
		return ret;
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
		dev_err(b->dev, "%s: writing 0x%04x: %d\n", prop, b->chgr_base + offset, ret);
	else
		dev_info(b->dev, "%s: hot 0x%04x, cold 0x%04x\n", prop, codes[1], codes[0]);
	return ret;
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
	u16 raw;
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
 * Read the temperature, and tell userspace (UPower, the session) when the level, the status, the
 * health or the input changes.
 */
static void pico4_poll(struct work_struct *work)
{
	struct pico4_battery *b = container_of(work, struct pico4_battery, poll.work);
	int capacity, status, online, health, temp;

	if (!pico4_temp(b, &temp)) {
		WRITE_ONCE(b->temp, temp);
		WRITE_ONCE(b->temp_valid, true);
	} else {
		WRITE_ONCE(b->temp_valid, false);
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
	 * The stock order, hard then soft. A failed write leaves the boot loader's window and is
	 * logged; the battery is still reported.
	 */
	pico4_write_jeita(b, "qcom,jeita-hard-thresholds", CHGR_JEITA_HARD);
	pico4_write_jeita(b, "qcom,jeita-soft-thresholds", CHGR_JEITA_SOFT);
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
