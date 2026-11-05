// SPDX-License-Identifier: GPL-2.0
/*
 * The PICO 4's RGB camera (docs/kernel.md): an IMX471 on CCI 0, four lanes of CSIPHY 1,
 * in the one mode PICO OS's camera module uses for its see-through: 2328x1748 RAW10 (the array 2x2
 * binned) free-running at 50 fps. PICO OS only loads the sensor while its see-through runs, and so
 * does this: it powers up when a stream starts and an absent sensor fails only that stream, because
 * a probe error would keep CAMSS's notifier, and with it the tracking cameras, from completing.
 */
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/mod_devicetable.h>
#include <linux/pm_runtime.h>
#include <linux/regulator/consumer.h>
#include <linux/unaligned.h>
#include <media/media-entity.h>
#include <media/v4l2-async.h>
#include <media/v4l2-ctrls.h>
#include <media/v4l2-subdev.h>

#define IMX471_WIDTH		2328
#define IMX471_HEIGHT		1748
#define IMX471_CODE		MEDIA_BUS_FMT_SRGGB10_1X10
#define IMX471_LINK_FREQ	1032424000LL	/* 2064.848 Mbit/s on each lane */
#define IMX471_PIXEL_RATE	825939200LL	/* four lanes of 10-bit samples */
#define IMX471_MAX_LINES	4510		/* the frame length less 10 */
#define IMX471_LINES		2260		/* 1/100 s */
#define IMX471_GAIN_MAX		960		/* 16x: gain = 1024 / (1024 - register) */
#define IMX471_GAIN		768		/* 4x */

#define IMX471_REG_CHIP_ID	0x0016
#define IMX471_CHIP_ID		0x0471
#define IMX471_REG_STREAM	0x0100
#define IMX471_REG_HOLD		0x0104
#define IMX471_REG_EXPOSURE	0x0202
#define IMX471_REG_GAIN		0x0204

/* Stops the sensor's stream and leaves the pipeline as it is, as PICO OS's see-through does. */
#define V4L2_CID_PICO4_PAUSED	(V4L2_CID_USER_BASE | 0x1000)

struct imx471_reg {
	u16 reg;
	u8 val;
};

/*
 * imx471_mode[]: the settings of PICO OS's camera module for this mode (2x2-binned readout, frame
 * length 4520, line length 2560), generated at build time from the stock vendor image by
 * kernel/imx471_mode.py; exposure and gain are left out, they are this driver's controls.
 */
#include "pico4-imx471-mode.h"

struct pico4_imx471 {
	struct device *dev;
	struct i2c_client *client;
	struct v4l2_subdev sd;
	struct media_pad pad;
	struct v4l2_ctrl_handler ctrls;
	struct v4l2_ctrl *exposure;
	struct v4l2_ctrl *gain;
	struct clk *mclk;
	struct regulator *vio;
	struct regulator *vdig;
	struct gpio_desc *reset;
	struct gpio_desc *standby;
	bool streaming;
};

static const s64 pico4_imx471_link_freqs[] = { IMX471_LINK_FREQ };

static int pico4_imx471_write(struct pico4_imx471 *cam, u16 reg, u8 val)
{
	u8 buf[3] = { reg >> 8, reg & 0xff, val };
	int ret = i2c_master_send(cam->client, buf, sizeof(buf));

	return ret < 0 ? ret : ret == sizeof(buf) ? 0 : -EIO;
}

static int pico4_imx471_write16(struct pico4_imx471 *cam, u16 reg, u16 val)
{
	int ret = pico4_imx471_write(cam, reg, val >> 8);

	return ret ?: pico4_imx471_write(cam, reg + 1, val & 0xff);
}

static int pico4_imx471_read16(struct pico4_imx471 *cam, u16 reg, u16 *val)
{
	u8 addr[2] = { reg >> 8, reg & 0xff };
	u8 data[2];
	struct i2c_msg msgs[] = {
		{ .addr = cam->client->addr, .len = sizeof(addr), .buf = addr },
		{ .addr = cam->client->addr, .flags = I2C_M_RD, .len = sizeof(data), .buf = data },
	};
	int ret = i2c_transfer(cam->client->adapter, msgs, ARRAY_SIZE(msgs));

	if (ret != ARRAY_SIZE(msgs))
		return ret < 0 ? ret : -EIO;
	*val = get_unaligned_be16(data);

	return 0;
}

/* PICO OS's sequence: reset low, VIO, VDIG, standby, MCLK, reset, and 18 ms for the sensor. */
static int pico4_imx471_power_on(struct pico4_imx471 *cam)
{
	int ret;

	ret = pm_runtime_resume_and_get(cam->dev);
	if (ret < 0)
		return ret;
	gpiod_set_value_cansleep(cam->reset, 0);
	ret = regulator_enable(cam->vio);
	if (ret)
		goto err_pm;
	usleep_range(1000, 1200);
	ret = regulator_enable(cam->vdig);
	if (ret)
		goto err_vio;
	usleep_range(2000, 2200);
	gpiod_set_value_cansleep(cam->standby, 1);
	usleep_range(1000, 1200);
	ret = clk_prepare_enable(cam->mclk);
	if (ret)
		goto err_vdig;
	usleep_range(1000, 1200);
	gpiod_set_value_cansleep(cam->reset, 1);
	msleep(18);

	return 0;

err_vdig:
	gpiod_set_value_cansleep(cam->standby, 0);
	regulator_disable(cam->vdig);
err_vio:
	regulator_disable(cam->vio);
err_pm:
	pm_runtime_put(cam->dev);
	return ret;
}

static void pico4_imx471_power_off(struct pico4_imx471 *cam)
{
	clk_disable_unprepare(cam->mclk);
	gpiod_set_value_cansleep(cam->reset, 0);
	gpiod_set_value_cansleep(cam->standby, 0);
	regulator_disable(cam->vdig);
	regulator_disable(cam->vio);
	pm_runtime_put(cam->dev);
}

static int pico4_imx471_start(struct pico4_imx471 *cam)
{
	u16 id = 0;
	size_t i;
	int ret;

	ret = pico4_imx471_read16(cam, IMX471_REG_CHIP_ID, &id);
	if (ret) {
		dev_err(cam->dev, "no answer at 0x%02x: %d\n", cam->client->addr, ret);
		return ret;
	}
	if (id != IMX471_CHIP_ID) {
		dev_err(cam->dev, "chip ID 0x%04x, not the IMX471\n", id);
		return -ENODEV;
	}
	for (i = 0; i < ARRAY_SIZE(imx471_mode); i++) {
		ret = pico4_imx471_write(cam, imx471_mode[i].reg, imx471_mode[i].val);
		if (ret)
			return ret;
	}
	cam->streaming = true;
	ret = v4l2_ctrl_handler_setup(&cam->ctrls);
	if (ret)
		return ret;

	return pico4_imx471_write(cam, IMX471_REG_STREAM, 1);
}

static int pico4_imx471_s_stream(struct v4l2_subdev *sd, int enable)
{
	struct pico4_imx471 *cam = container_of(sd, struct pico4_imx471, sd);
	int ret;

	if (!!enable == cam->streaming)
		return 0;
	if (!enable) {
		pico4_imx471_write(cam, IMX471_REG_STREAM, 0);
		cam->streaming = false;
		pico4_imx471_power_off(cam);
		return 0;
	}
	ret = pico4_imx471_power_on(cam);
	if (ret)
		return ret;
	ret = pico4_imx471_start(cam);
	if (ret) {
		cam->streaming = false;
		pico4_imx471_power_off(cam);
	}

	return ret;
}

static int pico4_imx471_s_ctrl(struct v4l2_ctrl *ctrl)
{
	struct pico4_imx471 *cam = container_of(ctrl->handler, struct pico4_imx471, ctrls);
	int ret;

	if (!cam->streaming)
		return 0;
	if (ctrl->id == V4L2_CID_PICO4_PAUSED)
		return pico4_imx471_write(cam, IMX471_REG_STREAM, !ctrl->val);

	/* As PICO OS's module updates them: under group hold, so that a frame takes both or neither. */
	ret = pico4_imx471_write(cam, IMX471_REG_HOLD, 1);
	if (!ret)
		ret = pico4_imx471_write16(cam, IMX471_REG_EXPOSURE, cam->exposure->val);
	if (!ret)
		ret = pico4_imx471_write16(cam, IMX471_REG_GAIN, cam->gain->val);
	if (!ret)
		ret = pico4_imx471_write(cam, IMX471_REG_HOLD, 0);

	return ret;
}

static const struct v4l2_ctrl_ops pico4_imx471_ctrl_ops = {
	.s_ctrl = pico4_imx471_s_ctrl,
};

static const struct v4l2_ctrl_config pico4_imx471_paused = {
	.ops = &pico4_imx471_ctrl_ops,
	.id = V4L2_CID_PICO4_PAUSED,
	.name = "Sensor stream paused",
	.type = V4L2_CTRL_TYPE_BOOLEAN,
	.min = 0,
	.max = 1,
	.step = 1,
};

static void pico4_imx471_fill_fmt(struct v4l2_mbus_framefmt *fmt)
{
	*fmt = (struct v4l2_mbus_framefmt) {
		.width = IMX471_WIDTH,
		.height = IMX471_HEIGHT,
		.code = IMX471_CODE,
		.field = V4L2_FIELD_NONE,
		.colorspace = V4L2_COLORSPACE_RAW,
	};
}

static int pico4_imx471_init_state(struct v4l2_subdev *sd, struct v4l2_subdev_state *state)
{
	pico4_imx471_fill_fmt(v4l2_subdev_state_get_format(state, 0));

	return 0;
}

static int pico4_imx471_set_fmt(struct v4l2_subdev *sd, struct v4l2_subdev_state *state,
				struct v4l2_subdev_format *format)
{
	pico4_imx471_fill_fmt(&format->format);

	return 0;
}

static int pico4_imx471_enum_mbus_code(struct v4l2_subdev *sd, struct v4l2_subdev_state *state,
				       struct v4l2_subdev_mbus_code_enum *code)
{
	if (code->index)
		return -EINVAL;
	code->code = IMX471_CODE;

	return 0;
}

static int pico4_imx471_enum_frame_size(struct v4l2_subdev *sd, struct v4l2_subdev_state *state,
					struct v4l2_subdev_frame_size_enum *fse)
{
	if (fse->index || fse->code != IMX471_CODE)
		return -EINVAL;
	fse->min_width = IMX471_WIDTH;
	fse->max_width = IMX471_WIDTH;
	fse->min_height = IMX471_HEIGHT;
	fse->max_height = IMX471_HEIGHT;

	return 0;
}

static const struct v4l2_subdev_video_ops pico4_imx471_video_ops = {
	.s_stream = pico4_imx471_s_stream,
};

static const struct v4l2_subdev_pad_ops pico4_imx471_pad_ops = {
	.enum_mbus_code = pico4_imx471_enum_mbus_code,
	.enum_frame_size = pico4_imx471_enum_frame_size,
	.get_fmt = v4l2_subdev_get_fmt,
	.set_fmt = pico4_imx471_set_fmt,
};

static const struct v4l2_subdev_ops pico4_imx471_ops = {
	.video = &pico4_imx471_video_ops,
	.pad = &pico4_imx471_pad_ops,
};

static const struct v4l2_subdev_internal_ops pico4_imx471_internal_ops = {
	.init_state = pico4_imx471_init_state,
};

static int pico4_imx471_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	struct pico4_imx471 *cam;
	struct v4l2_ctrl *ctrl;
	int ret;

	cam = devm_kzalloc(dev, sizeof(*cam), GFP_KERNEL);
	if (!cam)
		return -ENOMEM;
	cam->dev = dev;
	cam->client = client;

	cam->mclk = devm_clk_get(dev, "xclk");
	if (IS_ERR(cam->mclk))
		return dev_err_probe(dev, PTR_ERR(cam->mclk), "no MCLK\n");
	cam->vio = devm_regulator_get(dev, "vio");
	if (IS_ERR(cam->vio))
		return dev_err_probe(dev, PTR_ERR(cam->vio), "no VIO\n");
	cam->vdig = devm_regulator_get(dev, "vdig");
	if (IS_ERR(cam->vdig))
		return dev_err_probe(dev, PTR_ERR(cam->vdig), "no VDIG\n");
	cam->reset = devm_gpiod_get(dev, "reset", GPIOD_OUT_LOW);
	if (IS_ERR(cam->reset))
		return dev_err_probe(dev, PTR_ERR(cam->reset), "no reset line\n");
	cam->standby = devm_gpiod_get(dev, "standby", GPIOD_OUT_LOW);
	if (IS_ERR(cam->standby))
		return dev_err_probe(dev, PTR_ERR(cam->standby), "no standby line\n");

	v4l2_i2c_subdev_init(&cam->sd, client, &pico4_imx471_ops);
	cam->sd.internal_ops = &pico4_imx471_internal_ops;
	cam->sd.flags |= V4L2_SUBDEV_FL_HAS_DEVNODE;
	cam->sd.entity.function = MEDIA_ENT_F_CAM_SENSOR;

	v4l2_ctrl_handler_init(&cam->ctrls, 5);
	ctrl = v4l2_ctrl_new_int_menu(&cam->ctrls, NULL, V4L2_CID_LINK_FREQ,
				      ARRAY_SIZE(pico4_imx471_link_freqs) - 1, 0,
				      pico4_imx471_link_freqs);
	if (ctrl)
		ctrl->flags |= V4L2_CTRL_FLAG_READ_ONLY;
	v4l2_ctrl_new_std(&cam->ctrls, NULL, V4L2_CID_PIXEL_RATE, IMX471_PIXEL_RATE,
			  IMX471_PIXEL_RATE, 1, IMX471_PIXEL_RATE);
	cam->exposure = v4l2_ctrl_new_std(&cam->ctrls, &pico4_imx471_ctrl_ops, V4L2_CID_EXPOSURE,
					  4, IMX471_MAX_LINES, 1, IMX471_LINES);
	cam->gain = v4l2_ctrl_new_std(&cam->ctrls, &pico4_imx471_ctrl_ops, V4L2_CID_ANALOGUE_GAIN,
				      0, IMX471_GAIN_MAX, 1, IMX471_GAIN);
	v4l2_ctrl_new_custom(&cam->ctrls, &pico4_imx471_paused, NULL);
	ret = cam->ctrls.error;
	if (ret)
		goto err_ctrls;
	v4l2_ctrl_cluster(2, &cam->exposure);
	cam->sd.ctrl_handler = &cam->ctrls;

	cam->pad.flags = MEDIA_PAD_FL_SOURCE;
	ret = media_entity_pads_init(&cam->sd.entity, 1, &cam->pad);
	if (ret)
		goto err_ctrls;

	ret = v4l2_subdev_init_finalize(&cam->sd);
	if (ret)
		goto err_entity;

	pm_runtime_enable(dev);

	ret = v4l2_async_register_subdev_sensor(&cam->sd);
	if (ret) {
		dev_err_probe(dev, ret, "cannot register the subdevice\n");
		goto err_pm;
	}

	return 0;

err_pm:
	pm_runtime_disable(dev);
	v4l2_subdev_cleanup(&cam->sd);
err_entity:
	media_entity_cleanup(&cam->sd.entity);
err_ctrls:
	v4l2_ctrl_handler_free(&cam->ctrls);
	return ret;
}

static void pico4_imx471_remove(struct i2c_client *client)
{
	struct v4l2_subdev *sd = i2c_get_clientdata(client);
	struct pico4_imx471 *cam = container_of(sd, struct pico4_imx471, sd);

	v4l2_async_unregister_subdev(sd);
	if (cam->streaming)
		pico4_imx471_power_off(cam);
	pm_runtime_disable(&client->dev);
	v4l2_subdev_cleanup(sd);
	media_entity_cleanup(&sd->entity);
	v4l2_ctrl_handler_free(&cam->ctrls);
}

static const struct of_device_id pico4_imx471_of_match[] = {
	{ .compatible = "pico,phoenix-imx471" },
	{ }
};
MODULE_DEVICE_TABLE(of, pico4_imx471_of_match);

static struct i2c_driver pico4_imx471_driver = {
	.probe = pico4_imx471_probe,
	.remove = pico4_imx471_remove,
	.driver = {
		.name = "pico4-imx471",
		.of_match_table = pico4_imx471_of_match,
	},
};
module_i2c_driver(pico4_imx471_driver);

MODULE_DESCRIPTION("The PICO 4's RGB camera: IMX471");
MODULE_LICENSE("GPL");
