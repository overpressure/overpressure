# Entry points for Linux and WSL. On Windows, run the Python commands directly
# ("python -m pico4 ...", "python -m unittest discover -s tests"); docs/development.md.
PYTHON ?= python3
FIRMWARE ?= 5.13.7-global-seko
BUILD_ROOT ?= /var/tmp/pico4-userspace-build
USERSPACE ?= out/userspace
USERSPACE_FLAGS ?= --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7 \
	--wlan assets/wlan-5.13.7

.PHONY: help test image userspace qemu-stage1 qemu-userspace

help: ## list the targets
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

test: ## host tests: no hardware, a few seconds
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py'
	bash tests/test_recovery_policy.sh

image: ## stage-1 boot image for FIRMWARE in out/
	$(PYTHON) -m pico4 build --firmware $(FIRMWARE)

userspace: ## userspace archive in USERSPACE/ (as root; add USERSPACE_FLAGS+=--refresh for new packages)
	scripts/build-userspace.sh $(BUILD_ROOT) $(USERSPACE) $(USERSPACE_FLAGS)

qemu-stage1: ## optional: stage 1 on the QEMU test kernel, about 3 min
	tests/qemu/run-selftest.sh out/pico4-poc-$(FIRMWARE)-initramfs.cpio.gz

qemu-userspace: ## optional: the userspace on the 4.19 QEMU kernel, 10-20 min, often fails
	tests/qemu/run-userspace.sh out/pico4-poc-$(FIRMWARE)-initramfs.cpio.gz $(USERSPACE)/pico4-userspace.tar.zst
