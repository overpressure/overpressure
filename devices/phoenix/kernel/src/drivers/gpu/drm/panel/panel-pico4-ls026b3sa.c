// SPDX-License-Identifier: GPL-2.0-only
/*
 * The PICO 4's display: two Sharp LS026B3SA LCD panels of 2160x2160, one on each of the
 * SM8250's DSI links, driven as one bonded 4320x2160 output with DSC (four 540x16 slices per
 * panel line, 8 bits per component, 8 bits per pixel), as the stock kernel drives them.
 *
 * The panels' command sequences come from the device tree (pico4,on-command and
 * pico4,off-command), which the build copies from the stock board overlay, in the downstream
 * format: per command its data type, a "last" flag, the virtual channel, an acknowledge flag, the
 * delay after it in ms, the payload length (big endian) and the payload. Every command goes to
 * both panels. The power-on sequence carries the panels' DSC picture parameter set in a vendor
 * command (0xe6); probe compares it with the configuration given to the DSI host.
 */

#include <linux/backlight.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_graph.h>
#include <linux/regulator/consumer.h>
#include <linux/unaligned.h>
#include <linux/workqueue.h>

#include <drm/display/drm_dsc.h>
#include <drm/display/drm_dsc_helper.h>
#include <drm/drm_mipi_dsi.h>
#include <drm/drm_modes.h>
#include <drm/drm_panel.h>
#include <drm/drm_probe_helper.h>

#define PANEL_SIZE	2160
#define PPS_COMMAND	0xe6

static bool keep_boot_state;
module_param(keep_boot_state, bool, 0444);
MODULE_PARM_DESC(keep_boot_state,
		 "leave the panels as the bootloader set them up: no power changes, no reset, no commands");

static char *keep_supplies = "";
module_param(keep_supplies, charp, 0444);
MODULE_PARM_DESC(keep_supplies,
		 "supplies to leave as the bootloader set them (comma-separated: vddio)");

/* The stock timings of one panel at 90 Hz; the bonded mode puts two side by side. */
static const struct drm_display_mode panel_mode = {
	.clock = (PANEL_SIZE + 54 + 20 + 33) * (PANEL_SIZE + 57 + 4 + 4) * 90 / 1000,
	.hdisplay = PANEL_SIZE,
	.hsync_start = PANEL_SIZE + 54,
	.hsync_end = PANEL_SIZE + 54 + 20,
	.htotal = PANEL_SIZE + 54 + 20 + 33,
	.vdisplay = PANEL_SIZE,
	.vsync_start = PANEL_SIZE + 57,
	.vsync_end = PANEL_SIZE + 57 + 4,
	.vtotal = PANEL_SIZE + 57 + 4 + 4,
	.width_mm = 46,
	.height_mm = 46,
};

/*
 * The stock display's one switched supply. Its ±5.8 V bias stays as the bootloader set it; the
 * GD32 MCU, which drives the backlight, has its own supply.
 */
static const char * const supply_names[] = { "vddio" };

struct pico4_panel {
	struct drm_panel panel;
	struct device *dev;
	struct mipi_dsi_device *dsi[2];
	struct drm_dsc_config dsc;
	struct regulator_bulk_data supplies[ARRAY_SIZE(supply_names)];
	struct gpio_desc *reset;
	struct gpio_desc *vio;
	struct gpio_desc *backlight;
	struct backlight_device *bl;
	bool enabled;
	bool dimmed;
	bool lit;
	struct delayed_work first_light;
	const u8 *on_cmds, *off_cmds;
	int on_len, off_len;
};

static struct pico4_panel *to_pico4_panel(struct drm_panel *panel)
{
	return container_of(panel, struct pico4_panel, panel);
}

/* Each command with its exact data type, on both links, then its delay. */
static int pico4_send(struct pico4_panel *ctx, const u8 *seq, int len, bool lpm)
{
	int pos = 0, i;

	while (pos + 7 <= len) {
		u16 size = get_unaligned_be16(&seq[pos + 5]);
		struct mipi_dsi_msg msg = {
			.type = seq[pos],
			.flags = lpm ? MIPI_DSI_MSG_USE_LPM : 0,
			.tx_len = size,
			.tx_buf = &seq[pos + 7],
		};

		if (pos + 7 + size > len)
			return -EINVAL;
		for (i = 0; i < 2; i++) {
			ssize_t ret = ctx->dsi[i]->host->ops->transfer(ctx->dsi[i]->host, &msg);

			if (ret < 0) {
				dev_err(ctx->dev, "command %02x %02x on link %d: %zd\n",
					seq[pos], seq[pos + 7], i, ret);
				return ret;
			}
		}
		if (seq[pos + 4])
			msleep(seq[pos + 4]);
		pos += 7 + size;
	}
	return pos == len ? 0 : -EINVAL;
}

static bool supply_kept(const char *name)
{
	const char *pos = keep_supplies;
	size_t len = strlen(name);

	while ((pos = strstr(pos, name))) {
		if ((pos == keep_supplies || pos[-1] == ',') && (pos[len] == ',' || !pos[len]))
			return true;
		pos += len;
	}
	return false;
}

static void pico4_supplies_off(struct pico4_panel *ctx, int count)
{
	while (--count >= 0)
		if (!supply_kept(ctx->supplies[count].supply))
			regulator_disable(ctx->supplies[count].consumer);
}

static int pico4_prepare(struct drm_panel *panel)
{
	struct pico4_panel *ctx = to_pico4_panel(panel);
	int ret, i;

	/* Each step is logged: a bring-up that stalls the SoC leaves only the RAM console. */
	dev_info(ctx->dev, "prepare%s\n", keep_boot_state ? ": keeping the boot state" : "");
	if (keep_boot_state)
		return 0;

	/* One after the other in the stock order, each logged, not the bulk call's parallel ones. */
	for (i = 0; i < ARRAY_SIZE(ctx->supplies); i++) {
		if (supply_kept(ctx->supplies[i].supply)) {
			dev_info(ctx->dev, "keeping %s as the bootloader set it\n", ctx->supplies[i].supply);
			continue;
		}
		dev_info(ctx->dev, "enabling %s\n", ctx->supplies[i].supply);
		ret = regulator_enable(ctx->supplies[i].consumer);
		if (ret) {
			pico4_supplies_off(ctx, i);
			return ret;
		}
	}
	/* vddio settles 20 ms (the stock supply table) */
	msleep(20);
	dev_info(ctx->dev, "supplies on, VIO enable\n");
	/*
	 * The lines were taken as the bootloader left them, and it leaves VIO an input: a plain
	 * set would only latch the value, so each line becomes an output on its first use.
	 */
	gpiod_direction_output(ctx->vio, 1);

	/* The stock reset sequence: released 20 ms, held 20 ms, released 30 ms. */
	dev_info(ctx->dev, "reset\n");
	gpiod_direction_output(ctx->reset, 0);
	msleep(20);
	gpiod_set_value_cansleep(ctx->reset, 1);
	msleep(20);
	gpiod_set_value_cansleep(ctx->reset, 0);
	msleep(30);

	dev_info(ctx->dev, "power-on commands\n");
	ret = pico4_send(ctx, ctx->on_cmds, ctx->on_len, true);
	dev_info(ctx->dev, "power-on commands: %d\n", ret);
	/* The panels' own answer: power mode bit 4 is sleep out, bit 2 display on. */
	for (i = 0; !ret && i < 2; i++) {
		u8 mode = 0;
		int err = mipi_dsi_dcs_get_power_mode(ctx->dsi[i], &mode);

		dev_info(ctx->dev, "panel %d power mode %#04x (%d)\n", i, mode, err);
	}
	if (ret) {
		gpiod_set_value_cansleep(ctx->reset, 1);
		gpiod_set_value_cansleep(ctx->vio, 0);
		pico4_supplies_off(ctx, ARRAY_SIZE(ctx->supplies));
	}
	return ret;
}

/*
 * A boot's panels stay dark after their first power-on, whatever they are shown, until a resume or
 * until the power-on commands go out again over the running video, with the enable line low. Why is
 * not known: the display's GPIOs, supplies and clocks read the same in both states.
 */
static void pico4_first_light(struct work_struct *work)
{
	struct pico4_panel *ctx = container_of(to_delayed_work(work), struct pico4_panel, first_light);
	int ret;

	mutex_lock(&ctx->bl->update_lock);
	if (ctx->enabled && !ctx->dimmed) {
		gpiod_set_value_cansleep(ctx->backlight, 0);
		ret = pico4_send(ctx, ctx->on_cmds, ctx->on_len, true);
		gpiod_set_value_cansleep(ctx->backlight, 1);
		dev_info(ctx->dev, "first light: %d\n", ret);
	}
	mutex_unlock(&ctx->bl->update_lock);
}

static int pico4_enable(struct drm_panel *panel)
{
	struct pico4_panel *ctx = to_pico4_panel(panel);

	dev_info(ctx->dev, "backlight on\n");
	gpiod_direction_output(ctx->backlight, !backlight_is_blank(ctx->bl));
	ctx->enabled = true;
	ctx->dimmed = backlight_is_blank(ctx->bl);
	if (!ctx->lit && !keep_boot_state) {
		ctx->lit = true;
		schedule_delayed_work(&ctx->first_light, msecs_to_jiffies(300));
	}
	return 0;
}

static int pico4_disable(struct drm_panel *panel)
{
	struct pico4_panel *ctx = to_pico4_panel(panel);

	cancel_delayed_work_sync(&ctx->first_light);
	ctx->enabled = false;
	gpiod_set_value_cansleep(ctx->backlight, 0);
	return 0;
}

/*
 * A session's standby: its compositor keeps the panels on with black frames and holds the display,
 * and the MCU's brightness command showed no effect. The enable line darkens the lenses, but raising
 * it alone leaves them dark: with the power-on commands sent again while it is low, over the running
 * video, they light at once.
 */
static int pico4_bl_update_status(struct backlight_device *bl)
{
	struct pico4_panel *ctx = bl_get_data(bl);
	int ret;

	if (!ctx->enabled || ctx->dimmed == backlight_is_blank(bl))
		return 0;
	ctx->dimmed = backlight_is_blank(bl);
	if (ctx->dimmed) {
		gpiod_set_value_cansleep(ctx->backlight, 0);
		return 0;
	}
	ret = pico4_send(ctx, ctx->on_cmds, ctx->on_len, true);
	if (ret)
		dev_err(ctx->dev, "power-on commands: %d\n", ret);
	gpiod_set_value_cansleep(ctx->backlight, 1);
	return 0;
}

static const struct backlight_ops pico4_bl_ops = {
	.update_status = pico4_bl_update_status,
};

static int pico4_unprepare(struct drm_panel *panel)
{
	struct pico4_panel *ctx = to_pico4_panel(panel);

	if (keep_boot_state)
		return 0;
	/* The stock power-off commands go in high-speed mode. */
	pico4_send(ctx, ctx->off_cmds, ctx->off_len, false);
	gpiod_set_value_cansleep(ctx->reset, 1);
	gpiod_set_value_cansleep(ctx->vio, 0);
	pico4_supplies_off(ctx, ARRAY_SIZE(ctx->supplies));
	return 0;
}

static int pico4_get_modes(struct drm_panel *panel, struct drm_connector *connector)
{
	struct drm_display_mode *mode = drm_mode_duplicate(connector->dev, &panel_mode);

	if (!mode)
		return -ENOMEM;
	/* Both panels side by side: the DSI host splits the line between its two links. */
	mode->clock *= 2;
	mode->hdisplay *= 2;
	mode->hsync_start *= 2;
	mode->hsync_end *= 2;
	mode->htotal *= 2;
	mode->width_mm *= 2;
	mode->type = DRM_MODE_TYPE_DRIVER | DRM_MODE_TYPE_PREFERRED;
	drm_mode_set_name(mode);
	drm_mode_probed_add(connector, mode);

	connector->display_info.width_mm = mode->width_mm;
	connector->display_info.height_mm = mode->height_mm;
	return 1;
}

static const struct drm_panel_funcs pico4_panel_funcs = {
	.prepare = pico4_prepare,
	.enable = pico4_enable,
	.disable = pico4_disable,
	.unprepare = pico4_unprepare,
	.get_modes = pico4_get_modes,
};

static int pico4_dsc_init(struct pico4_panel *ctx)
{
	struct drm_dsc_config *dsc = &ctx->dsc;
	int ret;

	*dsc = (struct drm_dsc_config) {
		.dsc_version_major = 1,
		.dsc_version_minor = 1,
		.slice_height = 16,
		.slice_width = 540,
		.slice_count = 4,
		.bits_per_component = 8,
		.bits_per_pixel = 8 << 4,
		.block_pred_enable = true,
		.convert_rgb = true,
		.pic_width = PANEL_SIZE,
		.pic_height = PANEL_SIZE,
	};
	drm_dsc_set_const_params(dsc);
	drm_dsc_set_rc_buf_thresh(dsc);
	ret = drm_dsc_setup_rc_params(dsc, DRM_DSC_1_1_PRE_SCR);
	if (ret)
		return ret;
	dsc->initial_scale_value = drm_dsc_initial_scale_value(dsc);
	dsc->line_buf_depth = dsc->bits_per_component + 1;
	return drm_dsc_compute_rc_parameters(dsc);
}

/* The panels decode with the parameter set of the stock sequence; the host must encode alike. */
static void pico4_dsc_check(struct pico4_panel *ctx)
{
	struct drm_dsc_picture_parameter_set pps;
	int pos = 0;

	drm_dsc_pps_payload_pack(&pps, &ctx->dsc);
	while (pos + 7 <= ctx->on_len) {
		u16 size = get_unaligned_be16(&ctx->on_cmds[pos + 5]);
		const u8 *payload = &ctx->on_cmds[pos + 7];

		if (size == 1 + sizeof(pps) && payload[0] == PPS_COMMAND) {
			if (memcmp(&payload[1], &pps, sizeof(pps))) {
				dev_warn(ctx->dev, "the DSC configuration differs from the panels' parameter set\n");
				print_hex_dump(KERN_WARNING, "stock pps: ", DUMP_PREFIX_OFFSET, 16, 1,
					       &payload[1], sizeof(pps), false);
				print_hex_dump(KERN_WARNING, "host pps:  ", DUMP_PREFIX_OFFSET, 16, 1,
					       &pps, sizeof(pps), false);
			} else {
				dev_info(ctx->dev, "DSC configuration matches the panels' parameter set\n");
			}
			return;
		}
		pos += 7 + size;
	}
	dev_warn(ctx->dev, "no DSC parameter set in the power-on sequence\n");
}

static int pico4_probe(struct mipi_dsi_device *dsi)
{
	struct device *dev = &dsi->dev;
	const struct mipi_dsi_device_info info = { .type = "pico4-ls026b3sa", .channel = 0 };
	struct mipi_dsi_host *host1;
	struct device_node *dsi1;
	struct pico4_panel *ctx;
	int ret, i;

	ctx = devm_drm_panel_alloc(dev, struct pico4_panel, panel, &pico4_panel_funcs,
				   DRM_MODE_CONNECTOR_DSI);
	if (IS_ERR(ctx))
		return PTR_ERR(ctx);
	ctx->dev = dev;
	/* The DSI links reach LP-11 before the panels are reset (the stock lp11-init). */
	ctx->panel.prepare_prev_first = true;

	ctx->on_cmds = of_get_property(dev->of_node, "pico4,on-command", &ctx->on_len);
	ctx->off_cmds = of_get_property(dev->of_node, "pico4,off-command", &ctx->off_len);
	if (!ctx->on_cmds || !ctx->off_cmds)
		return dev_err_probe(dev, -EINVAL, "missing the panels' command sequences\n");

	for (i = 0; i < ARRAY_SIZE(supply_names); i++)
		ctx->supplies[i].supply = supply_names[i];
	ret = devm_regulator_bulk_get(dev, ARRAY_SIZE(ctx->supplies), ctx->supplies);
	if (ret)
		return dev_err_probe(dev, ret, "supplies\n");
	/* As the bootloader left them: the panels show its splash until the first modeset. */
	ctx->reset = devm_gpiod_get(dev, "reset", GPIOD_ASIS);
	if (IS_ERR(ctx->reset))
		return dev_err_probe(dev, PTR_ERR(ctx->reset), "reset gpio\n");
	ctx->vio = devm_gpiod_get(dev, "vio", GPIOD_ASIS);
	if (IS_ERR(ctx->vio))
		return dev_err_probe(dev, PTR_ERR(ctx->vio), "vio gpio\n");
	INIT_DELAYED_WORK(&ctx->first_light, pico4_first_light);
	ctx->backlight = devm_gpiod_get(dev, "backlight", GPIOD_ASIS);
	if (IS_ERR(ctx->backlight))
		return dev_err_probe(dev, PTR_ERR(ctx->backlight), "backlight gpio\n");
	{
		const struct backlight_properties props = {
			.type = BACKLIGHT_RAW,
			.brightness = 1,
			.max_brightness = 1,
		};

		ctx->bl = devm_backlight_device_register(dev, "pico4-panel", dev, ctx, &pico4_bl_ops, &props);
		if (IS_ERR(ctx->bl))
			return dev_err_probe(dev, PTR_ERR(ctx->bl), "backlight device\n");
	}

	dsi1 = of_graph_get_remote_node(dev->of_node, 1, -1);
	if (!dsi1)
		return dev_err_probe(dev, -ENODEV, "no second DSI link\n");
	host1 = of_find_mipi_dsi_host_by_node(dsi1);
	of_node_put(dsi1);
	if (!host1)
		return dev_err_probe(dev, -EPROBE_DEFER, "second DSI host\n");
	ctx->dsi[0] = dsi;
	ctx->dsi[1] = devm_mipi_dsi_device_register_full(dev, host1, &info);
	if (IS_ERR(ctx->dsi[1]))
		return dev_err_probe(dev, PTR_ERR(ctx->dsi[1]), "second DSI device\n");

	ret = pico4_dsc_init(ctx);
	if (ret)
		return dev_err_probe(dev, ret, "DSC configuration\n");
	pico4_dsc_check(ctx);

	mipi_dsi_set_drvdata(dsi, ctx);
	drm_panel_add(&ctx->panel);

	for (i = 0; i < 2; i++) {
		struct mipi_dsi_device *link = ctx->dsi[i];

		link->lanes = 4;
		link->format = MIPI_DSI_FMT_RGB888;
		/* Non-burst with sync events; low power in the blanking, as the stock tree sets. */
		link->mode_flags = MIPI_DSI_MODE_VIDEO | MIPI_DSI_CLOCK_NON_CONTINUOUS;
		link->dsc = &ctx->dsc;
		link->dsc_slice_per_pkt = 2;
		ret = devm_mipi_dsi_attach(dev, link);
		if (ret) {
			drm_panel_remove(&ctx->panel);
			return dev_err_probe(dev, ret, "attaching DSI link %d\n", i);
		}
	}
	return 0;
}

static void pico4_remove(struct mipi_dsi_device *dsi)
{
	struct pico4_panel *ctx = mipi_dsi_get_drvdata(dsi);

	drm_panel_remove(&ctx->panel);
}

static const struct of_device_id pico4_of_match[] = {
	{ .compatible = "pico,phoenix-ls026b3sa" },
	{ }
};
MODULE_DEVICE_TABLE(of, pico4_of_match);

static struct mipi_dsi_driver pico4_panel_driver = {
	.driver = {
		.name = "panel-pico4-ls026b3sa",
		.of_match_table = pico4_of_match,
	},
	.probe = pico4_probe,
	.remove = pico4_remove,
};
module_mipi_dsi_driver(pico4_panel_driver);

MODULE_DESCRIPTION("PICO 4 display: two Sharp LS026B3SA panels on bonded DSI");
MODULE_LICENSE("GPL");
