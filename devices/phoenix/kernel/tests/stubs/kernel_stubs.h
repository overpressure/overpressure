/* SPDX-License-Identifier: Unlicense */
/*
 * Just enough of the kernel's interfaces to compile the PICO's battery driver on the host: every
 * linux/ header the driver includes is this one. The PMIC's registers, the device tree and the
 * power supply core are the test's fakes (test_pico4_battery.c).
 */
#ifndef PICO4_KERNEL_STUBS_H
#define PICO4_KERNEL_STUBS_H

#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int32_t s32;
typedef int64_t s64;

#define BIT(n)			(1U << (n))
#define GENMASK(h, l)		(((~0U) << (l)) & (~0U >> (31 - (h))))
#define FIELD_GET(mask, val)	(((val) & (mask)) / ((mask) & -(mask)))
#define ARRAY_SIZE(a)		(sizeof(a) / sizeof((a)[0]))
#define DIV_ROUND_CLOSEST(x, d)	(((x) + (d) / 2) / (d))
#define READ_ONCE(x)		(x)
#define WRITE_ONCE(x, v)	((x) = (v))
#define container_of(p, type, member) ((type *)((char *)(p) - offsetof(type, member)))
#define HZ			100

static inline s32 sign_extend32(u32 value, int index)
{
	int shift = 31 - index;

	return (s32)(value << shift) >> shift;
}

static inline void usleep_range(unsigned long min, unsigned long max)
{
	(void)min;
	(void)max;
}

/* Devices and the device tree. */
struct device_node {
	int unused;
};
struct fwnode_handle;
struct device {
	struct device_node *of_node;
	struct device *parent;
};
struct platform_device {
	struct device dev;
};
struct of_device_id {
	const char *compatible;
};
struct platform_driver {
	int (*probe)(struct platform_device *pdev);
	struct {
		const char *name;
		const struct of_device_id *of_match_table;
	} driver;
};

#define MODULE_DEVICE_TABLE(type, name)
#define MODULE_DESCRIPTION(text)
#define MODULE_LICENSE(text)
#define module_platform_driver(drv) static void *__attribute__((used)) pico4_stub_##drv = &drv

#define dev_err(dev, ...)		((void)(dev), fprintf(stderr, "err: " __VA_ARGS__))
#define dev_err_ratelimited(dev, ...)	dev_err(dev, __VA_ARGS__)
#define dev_info(dev, ...)		((void)(dev), fprintf(stderr, "info: " __VA_ARGS__))
#define dev_err_probe(dev, err, ...)	(dev_err(dev, __VA_ARGS__), (err))

struct regmap;
int regmap_read(struct regmap *map, unsigned int reg, unsigned int *val);
int regmap_update_bits(struct regmap *map, unsigned int reg, unsigned int mask, unsigned int val);
int regmap_bulk_read(struct regmap *map, unsigned int reg, void *val, size_t count);
int regmap_bulk_write(struct regmap *map, unsigned int reg, const void *val, size_t count);
struct regmap *dev_get_regmap(struct device *dev, const char *name);

int of_property_read_u32_array(const struct device_node *np, const char *name, u32 *out, size_t n);
int of_property_read_u32_index(const struct device_node *np, const char *name, u32 index, u32 *out);
int of_property_count_u32_elems(const struct device_node *np, const char *name);
static inline int of_property_read_u32(const struct device_node *np, const char *name, u32 *out)
{
	return of_property_read_u32_array(np, name, out, 1);
}

static inline void *devm_kzalloc(struct device *dev, size_t size, int flags)
{
	(void)dev;
	(void)flags;
	return calloc(1, size);
}
#define GFP_KERNEL 0
static inline struct fwnode_handle *dev_fwnode(struct device *dev)
{
	(void)dev;
	return NULL;
}
static inline int devm_add_action_or_reset(struct device *dev, void (*action)(void *), void *data)
{
	(void)dev;
	(void)action;
	(void)data;
	return 0;
}

/* Work. */
struct work_struct {
	int unused;
};
struct delayed_work {
	struct work_struct work;
};
#define INIT_DELAYED_WORK(w, fn) ((void)(w), (void)(fn))
static inline bool schedule_delayed_work(struct delayed_work *work, unsigned long delay)
{
	(void)work;
	(void)delay;
	return true;
}
static inline bool cancel_delayed_work_sync(struct delayed_work *work)
{
	(void)work;
	return true;
}

/* Power supplies. */
enum power_supply_property {
	POWER_SUPPLY_PROP_STATUS,
	POWER_SUPPLY_PROP_HEALTH,
	POWER_SUPPLY_PROP_PRESENT,
	POWER_SUPPLY_PROP_ONLINE,
	POWER_SUPPLY_PROP_TECHNOLOGY,
	POWER_SUPPLY_PROP_CAPACITY,
	POWER_SUPPLY_PROP_TEMP,
};
enum {
	POWER_SUPPLY_STATUS_CHARGING,
	POWER_SUPPLY_STATUS_DISCHARGING,
	POWER_SUPPLY_STATUS_NOT_CHARGING,
	POWER_SUPPLY_STATUS_FULL,
};
enum {
	POWER_SUPPLY_HEALTH_GOOD,
	POWER_SUPPLY_HEALTH_OVERHEAT,
	POWER_SUPPLY_HEALTH_COLD,
	POWER_SUPPLY_HEALTH_WARM,
	POWER_SUPPLY_HEALTH_COOL,
};
#define POWER_SUPPLY_TECHNOLOGY_LION	2
#define POWER_SUPPLY_TYPE_BATTERY	1
#define POWER_SUPPLY_TYPE_USB		4
union power_supply_propval {
	int intval;
};
struct power_supply {
	void *drv_data;
};
struct power_supply_desc {
	const char *name;
	int type;
	const enum power_supply_property *properties;
	size_t num_properties;
	int (*get_property)(struct power_supply *psy, enum power_supply_property psp,
			    union power_supply_propval *val);
};
struct power_supply_config {
	void *drv_data;
	struct fwnode_handle *fwnode;
};
struct power_supply *devm_power_supply_register(struct device *dev, const struct power_supply_desc *desc,
						const struct power_supply_config *cfg);
static inline void *power_supply_get_drvdata(struct power_supply *psy)
{
	return psy->drv_data;
}
void power_supply_changed(struct power_supply *psy);
#define IS_ERR(p)	((p) == NULL)
#define PTR_ERR(p)	(-ENOMEM)

#endif
