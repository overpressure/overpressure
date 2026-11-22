# SPDX-License-Identifier: Unlicense
"""The headset interface, one module per major version: a headset imports only from overpressure.api.vMAJOR.

docs/adding-a-headset.md#the-headset-interface has the rules: a minor version only adds, a new major keeps
the old one's module as an adapter until that major is retired (RETIRED in overpressure/headset.py).
"""
