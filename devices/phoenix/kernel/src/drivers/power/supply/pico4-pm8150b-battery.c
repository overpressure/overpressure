// SPDX-License-Identifier: GPL-2.0-only
/*
 * The PICO 4's battery on a mainline kernel, from PM8150B's registers as the stock kernel reads
 * them: the fuel gauge's monotonic state of charge, the charger's state and whether USB powers the
 * headset. The fuel gauge and the charger run on their own (PICO's boot loader configures them);
 * this only reports, as the "battery" and "usb" power supplies the stock kernel has.
 */
#include <linux/bitfield.h>
#include <linux/bits.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/power_supply.h>
#include <linux/regmap.h>
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

/* USB input block: its real-time interrupt status, with the plug-in bit. */
#define USBIN_INT_RT_STS		0x10
#define USBIN_PLUGIN			BIT(4)

/* No interrupts are taken: the supplies are read on demand and checked for changes this often. */
#define POLL_INTERVAL			(30 * HZ)

struct pico4_battery {
	struct device *dev;
	struct regmap *map;
	u32 soc_base, chgr_base, usbin_base;
	struct power_supply *battery, *usb;
	struct delayed_work poll;
	int last_capacity, last_status, last_online;
};

static int pico4_read(struct pico4_battery *b, u32 reg, unsigned int *val)
{
	int ret = regmap_read(b->map, reg, val);

	if (ret)
		dev_err(b->dev, "reading 0x%04x: %d\n", reg, ret);
	return ret;
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

/* Tell userspace (UPower, the session) when the level, the status or the input changes. */
static void pico4_poll(struct work_struct *work)
{
	struct pico4_battery *b = container_of(work, struct pico4_battery, poll.work);
	int capacity, status, online;

	if (!pico4_capacity(b, &capacity) && !pico4_status(b, &status) && !pico4_online(b, &online)) {
		if (online != b->last_online)
			power_supply_changed(b->usb);
		if (capacity != b->last_capacity || status != b->last_status || online != b->last_online)
			power_supply_changed(b->battery);
		b->last_capacity = capacity;
		b->last_status = status;
		b->last_online = online;
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
	/* The fuel gauge's SOC block, the charger and the USB input, in that order. */
	if (of_property_read_u32_index(dev->of_node, "reg", 0, &b->soc_base) ||
	    of_property_read_u32_index(dev->of_node, "reg", 1, &b->chgr_base) ||
	    of_property_read_u32_index(dev->of_node, "reg", 2, &b->usbin_base))
		return dev_err_probe(dev, -EINVAL, "reg needs the SOC, charger and USB-in blocks\n");

	cfg.drv_data = b;
	cfg.fwnode = dev_fwnode(dev);
	b->battery = devm_power_supply_register(dev, &pico4_battery_desc, &cfg);
	if (IS_ERR(b->battery))
		return dev_err_probe(dev, PTR_ERR(b->battery), "registering the battery\n");
	cfg.fwnode = NULL;
	b->usb = devm_power_supply_register(dev, &pico4_usb_desc, &cfg);
	if (IS_ERR(b->usb))
		return dev_err_probe(dev, PTR_ERR(b->usb), "registering the USB input\n");

	b->last_capacity = b->last_status = b->last_online = -1;
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

MODULE_DESCRIPTION("PICO 4 battery level and charger state from PM8150B");
MODULE_LICENSE("GPL");
