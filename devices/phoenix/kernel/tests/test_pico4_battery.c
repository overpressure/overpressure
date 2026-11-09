// SPDX-License-Identifier: Unlicense
/*
 * The PICO's battery driver on a fake PM8150B: the stock temperature window, the battery's
 * temperature through the gauge's direct window, and the float voltage by temperature with the
 * stock software JEITA's ranges and soft zones. These act only on a hot or cold battery, which a
 * test on the headset does not reach. The device tree's values are the tree's (stock overlay 7).
 */
#include "../src/drivers/power/supply/pico4-pm8150b-battery.c"

#include <assert.h>

/* The PMIC: a register file, with registers that ignore writes to fake a failure. */
struct regmap {
	u8 regs[0x10000];
	unsigned int stuck;
};
static struct regmap pmic;

int regmap_read(struct regmap *map, unsigned int reg, unsigned int *val)
{
	*val = map->regs[reg];
	return 0;
}

int regmap_update_bits(struct regmap *map, unsigned int reg, unsigned int mask, unsigned int val)
{
	if (reg != map->stuck)
		map->regs[reg] = (map->regs[reg] & ~mask) | (val & mask);
	return 0;
}

int regmap_bulk_read(struct regmap *map, unsigned int reg, void *val, size_t count)
{
	memcpy(val, &map->regs[reg], count);
	return 0;
}

int regmap_bulk_write(struct regmap *map, unsigned int reg, const void *val, size_t count)
{
	memcpy(&map->regs[reg], val, count);
	return 0;
}

struct regmap *dev_get_regmap(struct device *dev, const char *name)
{
	(void)dev;
	(void)name;
	return &pmic;
}

/* The device tree: the battery node's properties, as sm8250-pico4-phoenix.dts has them. */
struct prop {
	const char *name;
	u32 values[16];
	int count;
};
static struct prop props[] = {
	{ "reg", { 0x4000, 0x1000, 0x1300, 0x4300 }, 4 },
	{ "qcom,jeita-hard-thresholds", { 0x58cd, 0x1c22 }, 2 },
	{ "qcom,jeita-soft-thresholds", { 0x4ccc, 0x20b8 }, 2 },
	{ "qcom,jeita-soft-hys-thresholds", { 0x48d4, 0x23c0 }, 2 },
	{ "qcom,jeita-fv-ranges",
	  { 0, 100, 4400000, 101, 200, 4400000, 201, 450, 4400000, 451, 500, 4200000 }, 12 },
	{ "qcom,jeita-soft-fv-uv", { 4400000, 4200000 }, 2 },
	{ "pico,float-voltage-max-uv", { 4350000 }, 1 },
};
static const char *missing;	/* a property the test leaves out */

static struct prop *find(const char *name)
{
	size_t i;

	if (missing && !strcmp(name, missing))
		return NULL;
	for (i = 0; i < ARRAY_SIZE(props); i++)
		if (!strcmp(props[i].name, name))
			return &props[i];
	return NULL;
}

int of_property_read_u32_array(const struct device_node *np, const char *name, u32 *out, size_t n)
{
	struct prop *p = find(name);

	(void)np;
	if (!p)
		return -EINVAL;
	if ((int)n > p->count)
		return -EOVERFLOW;
	memcpy(out, p->values, n * sizeof(*out));
	return 0;
}

int of_property_read_u32_index(const struct device_node *np, const char *name, u32 index, u32 *out)
{
	struct prop *p = find(name);

	(void)np;
	if (!p || (int)index >= p->count)
		return -EINVAL;
	*out = p->values[index];
	return 0;
}

int of_property_count_u32_elems(const struct device_node *np, const char *name)
{
	struct prop *p = find(name);

	(void)np;
	return p ? p->count : -EINVAL;
}

static struct power_supply supplies[2];
static int supply_count;

struct power_supply *devm_power_supply_register(struct device *dev, const struct power_supply_desc *desc,
						const struct power_supply_config *cfg)
{
	(void)dev;
	(void)desc;
	supplies[supply_count].drv_data = cfg->drv_data;
	return &supplies[supply_count++];
}

void power_supply_changed(struct power_supply *psy)
{
	(void)psy;
}

/* The boot loader's state, as read on the headset. */
static void boot_loader(void)
{
	static const u8 window[] = { 0x20, 0xc4, 0x45, 0xe2, 0x18, 0x11, 0x58, 0x1c };

	memset(&pmic, 0, sizeof(pmic));
	pmic.stuck = UINT_MAX;
	pmic.regs[0x1051] = 0x2c;
	pmic.regs[0x1070] = 0x4b;
	pmic.regs[0x1090] = 0xdb;
	memcpy(&pmic.regs[0x1094], window, sizeof(window));
	pmic.regs[0x4340] = 0x04;
	pmic.regs[0x4352] = 0x90;
	pmic.regs[0x4370] = 0x04;
	pmic.regs[0x4371] = 0x02;
	pmic.regs[0x4310] = MEM_GNT;	/* the gauge grants at once */
	missing = NULL;
	supply_count = 0;
}

static struct pico4_battery *probe(void)
{
	static struct platform_device pdev;
	static struct device parent;
	static struct device_node node;
	int ret;

	pdev.dev.of_node = &node;
	pdev.dev.parent = &parent;
	ret = pico4_battery_probe(&pdev);
	assert(ret == 0);
	return power_supply_get_drvdata(&supplies[0]);
}

/* The battery's temperature in the gauge's memory: 10 bits, signed, 0.25 °C each. */
static void gauge_temp(int tenths)
{
	u16 raw = (u16)((tenths * 4 / 10) & 0x3ff);

	pmic.regs[0x4858] = raw & 0xff;
	pmic.regs[0x4859] = raw >> 8;
}

static unsigned int fv_mv(void)
{
	return 3600 + pmic.regs[0x1070] * 10;
}

/* One poll at this temperature and charger status. */
static void poll_at(struct pico4_battery *b, int tenths, u8 status7)
{
	gauge_temp(tenths);
	pmic.regs[0x100d] = status7;
	pico4_poll(&b->poll.work);
}

static void test_probe_writes_the_stock_window_and_turns_compensation_off(void)
{
	static const u8 stock[] = { 0x20, 0xb8, 0x4c, 0xcc, 0x1c, 0x22, 0x58, 0xcd };
	struct pico4_battery *b;

	boot_loader();
	pmic.regs[0x1051] |= CHARGER_INHIBIT;
	b = probe();
	assert(!memcmp(&pmic.regs[0x1094], stock, sizeof(stock)));
	assert(pmic.regs[0x1090] == 0xd0);
	assert(!(pmic.regs[0x1051] & CHARGER_INHIBIT));
	assert(b->jeita);
	/* The direct window: error log cleared, partition addressing, released, low latency. */
	assert(pmic.regs[0x4371] == (ADDR_KIND | DMA_CLEAR_LOG));
	assert(pmic.regs[0x4340] == MEM_ARB_LO_LATENCY_EN);
	assert(pmic.regs[0x4350] == 0);
}

static void test_the_temperature_through_the_window(void)
{
	union power_supply_propval val;
	struct pico4_battery *b;

	boot_loader();
	b = probe();
	assert(pico4_battery_get(b->battery, POWER_SUPPLY_PROP_TEMP, &val) == -ENODATA);
	poll_at(b, 240, 0);
	assert(pico4_battery_get(b->battery, POWER_SUPPLY_PROP_TEMP, &val) == 0 && val.intval == 240);
	/* Released after the read. */
	assert(!(pmic.regs[0x4340] & MEM_ARB_REQ) && !(pmic.regs[0x4350] & MEM_ACCESS_REQ));
	poll_at(b, -100, 0);
	assert(pico4_battery_get(b->battery, POWER_SUPPLY_PROP_TEMP, &val) == 0 && val.intval == -100);
	/* No grant: no temperature, and still released. */
	pmic.regs[0x4310] = 0;
	poll_at(b, 240, 0);
	assert(pico4_battery_get(b->battery, POWER_SUPPLY_PROP_TEMP, &val) == -ENODATA);
	assert(!(pmic.regs[0x4340] & MEM_ARB_REQ) && !(pmic.regs[0x4350] & MEM_ACCESS_REQ));
}

static void test_the_float_voltage_follows_the_stock_ranges(void)
{
	struct pico4_battery *b;

	boot_loader();
	b = probe();
	poll_at(b, 240, 0);
	assert(fv_mv() == 4350);		/* the ceiling, below the profile's 4.40 V */
	poll_at(b, 455, 0);
	assert(fv_mv() == 4350);		/* not yet 1 °C into 45.1-50 °C */
	poll_at(b, 460, 0);
	assert(fv_mv() == 4350);		/* 46.1 °C is the step: 45.1 °C and 1 °C hysteresis */
	poll_at(b, 465, 0);
	assert(fv_mv() == 4200);
	poll_at(b, 445, 0);
	assert(fv_mv() == 4200);		/* not yet 1 °C below 45 °C */
	poll_at(b, 440, 0);
	assert(fv_mv() == 4350);
	poll_at(b, 620, 0);
	assert(fv_mv() == 4200);		/* above the last range: the last range */
	poll_at(b, -50, 0);
	assert(fv_mv() == 4350);		/* below the first: no range */
	poll_at(b, 455, 0);
	assert(fv_mv() == 4200);		/* from no range there is no hysteresis */
}

static void test_no_temperature_leaves_the_voltage(void)
{
	struct pico4_battery *b;

	boot_loader();
	b = probe();
	poll_at(b, 470, 0);
	assert(fv_mv() == 4200);
	pmic.regs[0x4310] = 0;
	poll_at(b, 240, 0);
	assert(fv_mv() == 4200);
}

static void test_the_warm_zone(void)
{
	static const u8 hysteresis[] = { 0x23, 0xc0, 0x48, 0xd4 };
	static const u8 normal[] = { 0x20, 0xb8, 0x4c, 0xcc };
	struct pico4_battery *b;

	boot_loader();
	b = probe();
	poll_at(b, 430, 0);
	assert(fv_mv() == 4350);
	/* The charger sees its soft hot zone before the gauge's range changes. */
	poll_at(b, 450, BAT_TEMP_HOT_SOFT);
	assert(fv_mv() == 4200);
	assert(pmic.regs[0x1051] & CHARGER_INHIBIT);
	assert(!memcmp(&pmic.regs[0x1094], hysteresis, sizeof(hysteresis)));
	/* The hard limit changes nothing; the charger stops by itself. */
	poll_at(b, 510, BAT_TEMP_TOO_HOT | BAT_TEMP_HOT_SOFT);
	assert(fv_mv() == 4200 && (pmic.regs[0x1051] & CHARGER_INHIBIT));
	/* Out of the zone: all back. */
	poll_at(b, 420, 0);
	assert(fv_mv() == 4350);
	assert(!(pmic.regs[0x1051] & CHARGER_INHIBIT));
	assert(!memcmp(&pmic.regs[0x1094], normal, sizeof(normal)));
}

static void test_the_cool_zone_keeps_the_ceiling(void)
{
	struct pico4_battery *b;

	boot_loader();
	b = probe();
	poll_at(b, 80, BAT_TEMP_COLD_SOFT);
	assert(fv_mv() == 4350);		/* its 4.40 V is above the ceiling */
	assert(pmic.regs[0x1051] & CHARGER_INHIBIT);
}

static void test_a_write_that_does_not_take_is_tried_again(void)
{
	struct pico4_battery *b;

	boot_loader();
	b = probe();
	poll_at(b, 240, 0);
	pmic.stuck = 0x1070;
	poll_at(b, 470, 0);
	assert(fv_mv() == 4350);
	pmic.stuck = UINT_MAX;
	poll_at(b, 470, 0);
	assert(fv_mv() == 4200);
}

static void test_without_every_property_the_boot_loader_keeps_it(void)
{
	struct pico4_battery *b;

	boot_loader();
	missing = "pico,float-voltage-max-uv";
	b = probe();
	assert(!b->jeita);
	assert(pmic.regs[0x1090] == 0xdb);
	poll_at(b, 470, BAT_TEMP_HOT_SOFT);
	assert(fv_mv() == 4350 && !(pmic.regs[0x1051] & CHARGER_INHIBIT));
	/* The window is still the stock one. */
	assert(pmic.regs[0x1098] == 0x1c && pmic.regs[0x1099] == 0x22);
}

int main(void)
{
	test_probe_writes_the_stock_window_and_turns_compensation_off();
	test_the_temperature_through_the_window();
	test_the_float_voltage_follows_the_stock_ranges();
	test_no_temperature_leaves_the_voltage();
	test_the_warm_zone();
	test_the_cool_zone_keeps_the_ceiling();
	test_a_write_that_does_not_take_is_tried_again();
	test_without_every_property_the_boot_loader_keeps_it();
	printf("test_pico4_battery: passed\n");
	return 0;
}
