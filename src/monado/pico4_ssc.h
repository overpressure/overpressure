// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  Minimal client of the Qualcomm Sensor Core (SSC) SNS client service.
 *
 * The PICO 4's IMU sits behind the sensor DSP (SLPI). Clients reach it through
 * the QMI service 0x190 over QRTR sockets; requests and events inside the QMI
 * messages are protobuf-encoded. This client only implements what the head
 * tracker needs: service lookup, sensor lookup by data type, enable/disable
 * and the sensor events (msg 1025). Same protocol as userspace/rootfs/usr/bin/pico4-imu.
 *
 * @ingroup drv_pico4
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

//! 128-bit sensor UID, as the SSC splits it.
struct pico4_ssc_suid
{
	uint64_t low;
	uint64_t high;
};

#define PICO4_SSC_RAW_MAX 200

/*!
 * One sensor sample: DSP timestamp in QTimer ticks (19.2 MHz) and up to 3
 * values. Sensors that report opaque bytes instead (ble_mcu: the controller
 * radio's packets) get them in @ref raw.
 */
struct pico4_ssc_sample
{
	struct pico4_ssc_suid suid;
	uint64_t ticks;
	float values[3];
	int count;
	uint8_t raw[PICO4_SSC_RAW_MAX];
	uint16_t raw_len;
};

struct pico4_ssc;

/*!
 * Find the SNS client service through the QRTR name service and open a client.
 * Returns 0 or a negative errno.
 */
int
pico4_ssc_open(struct pico4_ssc **out_ssc, int timeout_ms);

void
pico4_ssc_close(struct pico4_ssc **ssc_ptr);

//! First sensor of @p data_type ("accel", "gyro", ...). Returns 0, -ENOENT or a negative errno.
int
pico4_ssc_find(struct pico4_ssc *ssc, const char *data_type, struct pico4_ssc_suid *out_suid, int timeout_ms);

//! Request continuous reports at @p rate_hz; the DSP picks the next supported rate.
int
pico4_ssc_enable(struct pico4_ssc *ssc, const struct pico4_ssc_suid *suid, float rate_hz);

int
pico4_ssc_disable(struct pico4_ssc *ssc, const struct pico4_ssc_suid *suid);

/*!
 * Wait up to @p timeout_ms for one QMI packet and decode the sensor samples in it.
 * Returns the number of samples written to @p out (at most @p max), 0 on timeout
 * or for packets without samples, or a negative errno.
 */
/*!
 * Samples that arrived within @p timeout_ms: their count, 0, or a negative errno.
 * -ECONNRESET: the service went away (the sensor DSP restarted); open a new client.
 */
int
pico4_ssc_read(struct pico4_ssc *ssc, int timeout_ms, struct pico4_ssc_sample *out, int max);

/*!
 * Whether a QRTR control packet from the name service reports that the SNS client
 * service at @p node:@p port is gone (DEL_SERVER for it, or DEL_CLIENT of its port).
 */
bool
pico4_ssc_control_gone(const uint8_t *packet, size_t len, uint32_t node, uint32_t port);


/*
 * Encoding helpers, exposed for the unit test.
 */

struct pico4_pb
{
	uint8_t *data;
	size_t len;
	size_t cap;
	bool overflow;
};

void
pico4_pb_varint(struct pico4_pb *pb, uint32_t field, uint64_t value);

void
pico4_pb_fixed32(struct pico4_pb *pb, uint32_t field, uint32_t value);

void
pico4_pb_fixed64(struct pico4_pb *pb, uint32_t field, uint64_t value);

void
pico4_pb_float(struct pico4_pb *pb, uint32_t field, float value);

void
pico4_pb_bytes(struct pico4_pb *pb, uint32_t field, const void *data, size_t len);

//! sns_client_request_msg for @p suid / @p msg_id with an optional payload.
size_t
pico4_ssc_encode_request(uint8_t *buf,
                         size_t cap,
                         const struct pico4_ssc_suid *suid,
                         uint32_t msg_id,
                         const uint8_t *payload,
                         size_t payload_len);

//! QMI control request (msg 0x20) carrying @p request; returns the frame length or 0.
size_t
pico4_ssc_encode_qmi(uint8_t *buf, size_t cap, uint16_t txn, const uint8_t *request, size_t request_len);

/*!
 * Decode a QMI indication (0x21/0x22) into samples of sensor event messages
 * (msg 1025); @p out_suid_event receives SUID lookup results (msg 768) when not NULL.
 * Returns the number of samples, or -EINVAL for malformed input.
 */
int
pico4_ssc_decode_indication(const uint8_t *packet,
                            size_t len,
                            struct pico4_ssc_sample *out,
                            int max,
                            const char *suid_data_type,
                            struct pico4_ssc_suid *out_suid_event,
                            bool *out_suid_found);

#ifdef __cplusplus
}
#endif
