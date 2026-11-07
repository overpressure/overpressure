# Entry points for a Linux or macOS shell; on Windows, run the Python commands directly
# ("python -m overpressure ...", "python -m unittest"). The builds' Linux side runs in the build
# container on every host (docs/tool.md#linux-root-steps).
PYTHON ?= python3
UV ?= uv
DEVICE ?= phoenix

.PHONY: help test container-test lint format gui

help: ## list the targets
	@grep -hE '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

test: ## host tests of the package and every headset: no hardware, seconds
	$(PYTHON) -m unittest
	@for t in devices/*/tests/test_*.sh; do echo "$$t"; bash "$$t" || exit 1; done

container-test: ## the same tests in the build container, as root (any host with Docker or Podman)
	$(PYTHON) -m overpressure container run -- make test

lint: ## formatting, lint and types: ruff and ty, through uv
	$(UV) run ruff format --check .
	$(UV) run ruff check .
	$(UV) run ty check

format: ## format the Python code and apply ruff's safe fixes
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

gui: ## the window: the installer, and every step in its Advanced mode
	$(UV) run --extra gui python -m overpressure gui

# The headset's own targets (DEVICE=codename).
-include devices/$(DEVICE)/device.mk
