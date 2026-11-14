# Third-party material and other licenses

The project's own code is under the Unlicense ([LICENSE](LICENSE)). This file lists what in this
repository is not. Each source file names its license in its `SPDX-License-Identifier` header;
`tests/test_licenses.py` checks the headers. Each headset's repository has its own list
([overpressure/phoenix](https://github.com/overpressure/phoenix/blob/main/THIRD-PARTY.md)).

## From other projects

| Path | What it is | From | Pinned | License |
| --- | --- | --- | --- | --- |
| `overpressure/apksign.py`: `ALPINE_KEYS` | Alpine's two aarch64 signing public keys, to check its package index | Alpine Linux (`alpine-keys`) | the two keys as in the file | public keys, published at alpinelinux.org/keys |

## Not in the repository

The host tools fetch adb, fastboot and edl-ng; none of them is in the repository or published by the
project. Each names its upstream, pinned version and license: `overpressure/tools.py` and the build image's
recipe ([overpressure/builder](https://github.com/overpressure/builder)).
