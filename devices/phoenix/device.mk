# The PICO 4's targets of the root Makefile (make DEVICE=phoenix ...; it is the default).
FIRMWARE ?= 5.13.7-global-seko
BUILD_ROOT ?= /var/tmp/pico4-userspace-build
USERSPACE ?= out/userspace
USERSPACE_FLAGS ?= --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7 \
	--wlan assets/wlan-5.13.7 --audio assets/audio-5.13.7 --stock-slam assets/stock-slam-5.13.7
PHOENIX := devices/phoenix

.PHONY: image userspace qemu-stage1 qemu-userspace

image: ## PICO 4: stage-1 boot image for FIRMWARE in out/
	$(PYTHON) -m overpressure --device phoenix build --firmware $(FIRMWARE)

userspace: ## PICO 4: userspace archive in USERSPACE/ (as root; USERSPACE_FLAGS+=--refresh for new packages)
	$(PHOENIX)/scripts/build-userspace.sh $(BUILD_ROOT) $(USERSPACE) $(USERSPACE_FLAGS)

qemu-stage1: ## PICO 4, optional: stage 1 on the QEMU test kernel, about 3 min
	$(PHOENIX)/tests/qemu/run-selftest.sh out/pico4-poc-$(FIRMWARE)-initramfs.cpio.gz

qemu-userspace: ## PICO 4, optional: the userspace on the 4.19 QEMU kernel, 10-20 min, often fails
	$(PHOENIX)/tests/qemu/run-userspace.sh out/pico4-poc-$(FIRMWARE)-initramfs.cpio.gz $(USERSPACE)/pico4-userspace.tar.zst
