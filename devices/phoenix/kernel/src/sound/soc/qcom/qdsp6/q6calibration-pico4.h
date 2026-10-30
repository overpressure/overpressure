/* SPDX-License-Identifier: GPL-2.0 */
#ifndef __Q6_CALIBRATION_PICO4_H__
#define __Q6_CALIBRATION_PICO4_H__

struct device;
struct q6copp;

int q6calibration_topology(int afe_port, int path);
void q6calibration_apply(struct device *dev, struct q6copp *copp, int afe_port, int path);

#endif /* __Q6_CALIBRATION_PICO4_H__ */
