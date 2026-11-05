// SPDX-License-Identifier: GPL-2.0
/*
 * PICO OS's audio calibration on mainline's q6 drivers (docs/userspace.md#audio-on-a-mainline-kernel).
 *
 * PICO's audio HAL hands the ADSP its custom topologies once, and for each stream the device
 * (ADM) topology of the stream's path and the path's processing and volume parameters, all from
 * its calibration database (ACDB); the stock kernel registers the topologies with the ADSP core,
 * opens the path's COPP with its topology and sends the parameters after the open. Here they come
 * from one file that pico4-audiocal builds from the same database: the topologies are registered
 * when the ADM service comes up, q6routing opens a path with its topology and hands its
 * parameters on after the open. A path without calibration keeps mainline's null topology.
 *
 * The file (little endian): "P4AC", version 2, the topologies' size, the number of paths; the
 * topologies as the database holds them; per path its AFE port id (u16), ADM path (u16), ADM
 * topology, AFE topology, parameter size and parameters (ADM set-parameter v3 entries, as the
 * database packs them), each part padded to 4 bytes.
 */
#include <linux/dma-mapping.h>
#include <linux/firmware.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/sizes.h>
#include <linux/unaligned.h>
#include "q6adm.h"
#include "q6afe.h"
#include "q6calibration-pico4.h"
#include "q6core.h"

#define CAL_FIRMWARE	"qcom/sm8250/pico4/audio-calibration.bin"
#define CAL_MAGIC	0x43413450	/* "P4AC" */
#define CAL_VERSION	2
#define CAL_MAX_PATHS	8
#define SID_MASK	0xf

struct q6cal_path {
	u16 afe_port;
	u16 path;
	u32 topology;
	u32 afe_topology;
	u32 size;
	const u8 *params;
};

struct q6cal {
	const struct firmware *fw;
	const u8 *topologies;
	u32 topologies_size;
	u32 map_handle;
	int npaths;
	struct q6cal_path paths[CAL_MAX_PATHS];
};

static DEFINE_MUTEX(q6cal_lock);
static struct q6cal *q6cal;

static int q6cal_parse(struct q6cal *cal, const u8 *d, size_t n)
{
	size_t off = 16;
	u32 npaths;
	int i;

	if (n < off || get_unaligned_le32(d) != CAL_MAGIC || get_unaligned_le32(d + 4) != CAL_VERSION)
		return -EINVAL;
	cal->topologies_size = get_unaligned_le32(d + 8);
	npaths = get_unaligned_le32(d + 12);
	if (npaths > CAL_MAX_PATHS || cal->topologies_size > n - off)
		return -EINVAL;
	cal->topologies = d + off;
	off += ALIGN(cal->topologies_size, 4);

	for (i = 0; i < npaths; i++) {
		struct q6cal_path *p = &cal->paths[i];

		if (off > n || n - off < 16)
			return -EINVAL;
		p->afe_port = get_unaligned_le16(d + off);
		p->path = get_unaligned_le16(d + off + 2);
		p->topology = get_unaligned_le32(d + off + 4);
		p->afe_topology = get_unaligned_le32(d + off + 8);
		p->size = get_unaligned_le32(d + off + 12);
		off += 16;
		if (p->size > n - off)
			return -EINVAL;
		p->params = d + off;
		off += ALIGN(p->size, 4);
	}
	cal->npaths = npaths;

	return 0;
}

static const struct q6cal_path *q6cal_find(int afe_port, int path)
{
	int i;

	if (!q6cal)
		return NULL;
	for (i = 0; i < q6cal->npaths; i++)
		if (q6cal->paths[i].afe_port == afe_port && q6cal->paths[i].path == path)
			return &q6cal->paths[i];

	return NULL;
}

/**
 * q6calibration_topology() - the ADM topology PICO OS opens a path with
 * @afe_port: AFE port id of the path's backend
 * @path: ADM_PATH_PLAYBACK or ADM_PATH_LIVE_REC
 *
 * Return: the topology id, or -ENOENT for a path without calibration.
 */
int q6calibration_topology(int afe_port, int path)
{
	const struct q6cal_path *p;
	int topology;

	mutex_lock(&q6cal_lock);
	p = q6cal_find(afe_port, path);
	topology = p ? p->topology : -ENOENT;
	mutex_unlock(&q6cal_lock);

	return topology;
}
EXPORT_SYMBOL_GPL(q6calibration_topology);

/**
 * q6calibration_apply() - send a path's parameters to the COPP just opened for it
 * @dev: the ADM child device the COPP was opened through
 * @copp: the COPP
 * @afe_port: AFE port id of the path's backend
 * @path: ADM_PATH_PLAYBACK or ADM_PATH_LIVE_REC
 */
void q6calibration_apply(struct device *dev, struct q6copp *copp, int afe_port, int path)
{
	const struct q6cal_path *p;
	int ret;

	mutex_lock(&q6cal_lock);
	p = q6cal_find(afe_port, path);
	if (p && p->size) {
		ret = q6adm_set_pp_params(dev, copp, p->params, p->size);
		if (ret)
			dev_err(dev, "calibration of port 0x%x not taken: %d\n", afe_port, ret);
	}
	mutex_unlock(&q6cal_lock);
}
EXPORT_SYMBOL_GPL(q6calibration_apply);

static int q6cal_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct of_phandle_args args;
	dma_addr_t dma;
	struct q6cal *cal;
	u64 dsp_addr;
	size_t len;
	void *buf;
	int ret, i;

	cal = devm_kzalloc(dev, sizeof(*cal), GFP_KERNEL);
	if (!cal)
		return -ENOMEM;

	/* The ADSP reaches the buffer through the audio SMMU stream, as q6asm-dai's. */
	ret = of_parse_phandle_with_fixed_args(dev->of_node, "iommus", 1, 0, &args);
	if (ret)
		return dev_err_probe(dev, ret, "no audio SMMU stream\n");
	of_node_put(args.np);

	ret = request_firmware(&cal->fw, CAL_FIRMWARE, dev);
	if (ret)
		return dev_err_probe(dev, ret, "no %s\n", CAL_FIRMWARE);
	ret = q6cal_parse(cal, cal->fw->data, cal->fw->size);
	if (ret) {
		dev_err(dev, "%s is malformed\n", CAL_FIRMWARE);
		goto err_fw;
	}

	if (cal->topologies_size) {
		len = ALIGN(cal->topologies_size, SZ_4K);
		buf = dmam_alloc_coherent(dev, len, &dma, GFP_KERNEL);
		if (!buf) {
			ret = -ENOMEM;
			goto err_fw;
		}
		memcpy(buf, cal->topologies, cal->topologies_size);
		dsp_addr = dma | ((u64)(args.args[0] & SID_MASK) << 32);

		ret = q6core_map_memory(dsp_addr, len, &cal->map_handle);
		if (ret) {
			dev_err(dev, "ADSP did not map the topologies: %d\n", ret);
			goto err_fw;
		}
		ret = q6core_register_topologies(dsp_addr, cal->map_handle, cal->topologies_size);
		if (ret) {
			dev_err(dev, "ADSP did not take the custom topologies: %d\n", ret);
			goto err_unmap;
		}
	}

	for (i = 0; i < cal->npaths; i++)
		q6afe_set_port_topology(cal->paths[i].afe_port, cal->paths[i].afe_topology);

	platform_set_drvdata(pdev, cal);
	mutex_lock(&q6cal_lock);
	q6cal = cal;
	mutex_unlock(&q6cal_lock);
	dev_info(dev, "PICO OS's calibration: %d paths, %u bytes of custom topologies\n",
		 cal->npaths, cal->topologies_size);

	return 0;

err_unmap:
	q6core_unmap_memory(cal->map_handle);
err_fw:
	release_firmware(cal->fw);
	return ret;
}

static void q6cal_remove(struct platform_device *pdev)
{
	struct q6cal *cal = platform_get_drvdata(pdev);
	int i;

	for (i = 0; i < cal->npaths; i++)
		q6afe_set_port_topology(cal->paths[i].afe_port, 0);

	mutex_lock(&q6cal_lock);
	q6cal = NULL;
	mutex_unlock(&q6cal_lock);
	/* Gone with the ADSP when it restarts; the new instance registers them again. */
	if (cal->map_handle)
		q6core_unmap_memory(cal->map_handle);
	release_firmware(cal->fw);
}

static const struct of_device_id q6cal_of_match[] = {
	{ .compatible = "pico,phoenix-q6-calibration" },
	{}
};
MODULE_DEVICE_TABLE(of, q6cal_of_match);

static struct platform_driver q6cal_driver = {
	.probe = q6cal_probe,
	.remove = q6cal_remove,
	.driver = {
		.name = "pico4-q6-calibration",
		.of_match_table = q6cal_of_match,
	},
};
module_platform_driver(q6cal_driver);

MODULE_DESCRIPTION("PICO OS's audio calibration for the PICO 4 on the q6 drivers");
MODULE_LICENSE("GPL");
MODULE_FIRMWARE(CAL_FIRMWARE);
