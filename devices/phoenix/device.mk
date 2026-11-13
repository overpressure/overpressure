# The PICO 4's targets of the root Makefile (make DEVICE=phoenix ...; it is the default).
# tooling/__init__.py's FIRMWARE (pinned by tests/test_stock.py).
FIRMWARE ?= 5.13.7-global-seko
BUILD_ROOT ?= /var/tmp/pico4-userspace-build
USERSPACE ?= out/userspace
USERSPACE_FLAGS ?=
PHOENIX := devices/phoenix

.PHONY: image userspace qemu-stage1

image: ## PICO 4: stage-1 boot image for FIRMWARE in out/
	$(PYTHON) -m overpressure --device phoenix build --firmware $(FIRMWARE)

userspace: ## PICO 4: userspace archive in USERSPACE/, in the build container (USERSPACE_FLAGS+=--refresh for new packages)
	$(PYTHON) -m overpressure container run -- bash $(PHOENIX)/scripts/build-userspace.sh $(BUILD_ROOT) $(USERSPACE) $(USERSPACE_FLAGS)

qemu-stage1: ## PICO 4, optional: stage 1 on the QEMU test kernel, about 3 min
	$(PHOENIX)/tests/qemu/run-selftest.sh out/pico4-stage1-$(FIRMWARE)-initramfs.cpio.gz

