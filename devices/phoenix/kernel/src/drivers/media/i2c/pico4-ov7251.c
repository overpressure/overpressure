// SPDX-License-Identifier: GPL-2.0
/*
 * The PICO 4's tracking cameras (docs/kernel-feasibility.md): four OV7251 modules whose control
 * port belongs to the controller radio (the station's nRF52832), not to the SoC. The station
 * configures them over its own I2C and triggers each frame on FSIN; PICO's stock kernel accepts a
 * tracking camera's probe without reading its chip ID and never sends its register writes. The SoC
 * powers the modules, clocks them and receives their frames.
 *
 * So this is a sensor subdevice without a control port: a fixed format for CAMSS, the stock power
 * sequence while streaming. The sensors share their switched supplies (standby on GPIO 42, reset
 * on GPIO 115), each has its own MCLK. Starting the station's frame sync is up to the client, once
 * all its cameras stream, as on the stock kernel.
 */
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/module.h>
#include <linux/mod_devicetable.h>
#include <linux/platform_device.h>
#include <linux/pm_runtime.h>
#include <linux/regulator/consumer.h>
#include <media/media-entity.h>
#include <media/v4l2-async.h>
#include <media/v4l2-ctrls.h>
#include <media/v4l2-subdev.h>

#define OV7251_WIDTH		640
#define OV7251_HEIGHT		481	/* the sensor's metadata line and 480 scene rows */
#define OV7251_CODE		MEDIA_BUS_FMT_Y10_1X10
#define OV7251_LINK_FREQ	240000000LL	/* 480 Mbit/s on one lane */
#define OV7251_PIXEL_RATE	48000000LL	/* 480 Mbit/s of 10-bit samples */
#define OV7251_MCLK_SETTLE_US	1000		/* the stock power sequence's wait after the MCLK */

struct pico4_ov7251 {
	struct device *dev;
	struct v4l2_subdev sd;
	struct media_pad pad;
	struct v4l2_ctrl_handler ctrls;
	struct clk *mclk;
	struct regulator *standby;
	struct regulator *reset;
	bool streaming;
};

static const s64 pico4_ov7251_link_freqs[] = { OV7251_LINK_FREQ };

static int pico4_ov7251_power_on(struct pico4_ov7251 *cam)
{
	int ret;

	ret = pm_runtime_resume_and_get(cam->dev);
	if (ret < 0)
		return ret;

	ret = regulator_enable(cam->standby);
	if (ret)
		goto err_pm;

	ret = clk_prepare_enable(cam->mclk);
	if (ret)
		goto err_standby;
	usleep_range(OV7251_MCLK_SETTLE_US, 2 * OV7251_MCLK_SETTLE_US);

	ret = regulator_enable(cam->reset);
	if (ret)
		goto err_mclk;

	return 0;

err_mclk:
	clk_disable_unprepare(cam->mclk);
err_standby:
	regulator_disable(cam->standby);
err_pm:
	pm_runtime_put(cam->dev);
	return ret;
}

static void pico4_ov7251_power_off(struct pico4_ov7251 *cam)
{
	clk_disable_unprepare(cam->mclk);
	regulator_disable(cam->reset);
	regulator_disable(cam->standby);
	pm_runtime_put(cam->dev);
}

static int pico4_ov7251_s_stream(struct v4l2_subdev *sd, int enable)
{
	struct pico4_ov7251 *cam = container_of(sd, struct pico4_ov7251, sd);
	int ret = 0;

	if (enable == cam->streaming)
		return 0;

	if (enable)
		ret = pico4_ov7251_power_on(cam);
	else
		pico4_ov7251_power_off(cam);
	if (!ret)
		cam->streaming = enable;

	return ret;
}

static void pico4_ov7251_fill_fmt(struct v4l2_mbus_framefmt *fmt)
{
	*fmt = (struct v4l2_mbus_framefmt) {
		.width = OV7251_WIDTH,
		.height = OV7251_HEIGHT,
		.code = OV7251_CODE,
		.field = V4L2_FIELD_NONE,
		.colorspace = V4L2_COLORSPACE_RAW,
	};
}

static int pico4_ov7251_init_state(struct v4l2_subdev *sd, struct v4l2_subdev_state *state)
{
	pico4_ov7251_fill_fmt(v4l2_subdev_state_get_format(state, 0));

	return 0;
}

static int pico4_ov7251_set_fmt(struct v4l2_subdev *sd, struct v4l2_subdev_state *state,
				struct v4l2_subdev_format *format)
{
	/* The station sets the sensors' mode: there is only this one. */
	pico4_ov7251_fill_fmt(&format->format);

	return 0;
}

static int pico4_ov7251_enum_mbus_code(struct v4l2_subdev *sd, struct v4l2_subdev_state *state,
				       struct v4l2_subdev_mbus_code_enum *code)
{
	if (code->index)
		return -EINVAL;
	code->code = OV7251_CODE;

	return 0;
}

static int pico4_ov7251_enum_frame_size(struct v4l2_subdev *sd, struct v4l2_subdev_state *state,
					struct v4l2_subdev_frame_size_enum *fse)
{
	if (fse->index || fse->code != OV7251_CODE)
		return -EINVAL;
	fse->min_width = OV7251_WIDTH;
	fse->max_width = OV7251_WIDTH;
	fse->min_height = OV7251_HEIGHT;
	fse->max_height = OV7251_HEIGHT;

	return 0;
}

static const struct v4l2_subdev_video_ops pico4_ov7251_video_ops = {
	.s_stream = pico4_ov7251_s_stream,
};

static const struct v4l2_subdev_pad_ops pico4_ov7251_pad_ops = {
	.enum_mbus_code = pico4_ov7251_enum_mbus_code,
	.enum_frame_size = pico4_ov7251_enum_frame_size,
	.get_fmt = v4l2_subdev_get_fmt,
	.set_fmt = pico4_ov7251_set_fmt,
};

static const struct v4l2_subdev_ops pico4_ov7251_ops = {
	.video = &pico4_ov7251_video_ops,
	.pad = &pico4_ov7251_pad_ops,
};

static const struct v4l2_subdev_internal_ops pico4_ov7251_internal_ops = {
	.init_state = pico4_ov7251_init_state,
};

static int pico4_ov7251_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct pico4_ov7251 *cam;
	struct v4l2_ctrl *ctrl;
	u32 slot;
	int ret;

	cam = devm_kzalloc(dev, sizeof(*cam), GFP_KERNEL);
	if (!cam)
		return -ENOMEM;
	cam->dev = dev;

	ret = device_property_read_u32(dev, "pico,camera-slot", &slot);
	if (ret)
		return dev_err_probe(dev, ret, "no camera slot\n");

	cam->mclk = devm_clk_get(dev, "xclk");
	if (IS_ERR(cam->mclk))
		return dev_err_probe(dev, PTR_ERR(cam->mclk), "no MCLK\n");
	cam->standby = devm_regulator_get(dev, "standby");
	if (IS_ERR(cam->standby))
		return dev_err_probe(dev, PTR_ERR(cam->standby), "no standby switch\n");
	cam->reset = devm_regulator_get(dev, "reset");
	if (IS_ERR(cam->reset))
		return dev_err_probe(dev, PTR_ERR(cam->reset), "no reset switch\n");

	v4l2_subdev_init(&cam->sd, &pico4_ov7251_ops);
	cam->sd.internal_ops = &pico4_ov7251_internal_ops;
	cam->sd.dev = dev;
	cam->sd.owner = THIS_MODULE;
	cam->sd.fwnode = dev_fwnode(dev);
	cam->sd.flags |= V4L2_SUBDEV_FL_HAS_DEVNODE;
	cam->sd.entity.function = MEDIA_ENT_F_CAM_SENSOR;
	snprintf(cam->sd.name, sizeof(cam->sd.name), "pico4-ov7251 %u", slot);
	v4l2_set_subdevdata(&cam->sd, cam);

	v4l2_ctrl_handler_init(&cam->ctrls, 2);
	ctrl = v4l2_ctrl_new_int_menu(&cam->ctrls, NULL, V4L2_CID_LINK_FREQ,
				      ARRAY_SIZE(pico4_ov7251_link_freqs) - 1, 0,
				      pico4_ov7251_link_freqs);
	if (ctrl)
		ctrl->flags |= V4L2_CTRL_FLAG_READ_ONLY;
	v4l2_ctrl_new_std(&cam->ctrls, NULL, V4L2_CID_PIXEL_RATE, OV7251_PIXEL_RATE,
			  OV7251_PIXEL_RATE, 1, OV7251_PIXEL_RATE);
	ret = cam->ctrls.error;
	if (ret)
		goto err_ctrls;
	cam->sd.ctrl_handler = &cam->ctrls;

	cam->pad.flags = MEDIA_PAD_FL_SOURCE;
	ret = media_entity_pads_init(&cam->sd.entity, 1, &cam->pad);
	if (ret)
		goto err_ctrls;

	ret = v4l2_subdev_init_finalize(&cam->sd);
	if (ret)
		goto err_entity;

	pm_runtime_enable(dev);
	platform_set_drvdata(pdev, cam);

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

static void pico4_ov7251_remove(struct platform_device *pdev)
{
	struct pico4_ov7251 *cam = platform_get_drvdata(pdev);

	v4l2_async_unregister_subdev(&cam->sd);
	if (cam->streaming)
		pico4_ov7251_power_off(cam);
	pm_runtime_disable(&pdev->dev);
	v4l2_subdev_cleanup(&cam->sd);
	media_entity_cleanup(&cam->sd.entity);
	v4l2_ctrl_handler_free(&cam->ctrls);
}

static const struct of_device_id pico4_ov7251_of_match[] = {
	{ .compatible = "pico,phoenix-ov7251" },
	{ }
};
MODULE_DEVICE_TABLE(of, pico4_ov7251_of_match);

static struct platform_driver pico4_ov7251_driver = {
	.probe = pico4_ov7251_probe,
	.remove = pico4_ov7251_remove,
	.driver = {
		.name = "pico4-ov7251",
		.of_match_table = pico4_ov7251_of_match,
	},
};
module_platform_driver(pico4_ov7251_driver);

MODULE_DESCRIPTION("The PICO 4's tracking cameras: OV7251 modules controlled by the controller radio");
MODULE_LICENSE("GPL");
