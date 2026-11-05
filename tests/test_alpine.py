# SPDX-License-Identifier: Unlicense
"""APKINDEX signature verification (overpressure/apksign.py) and its use in overpressure/alpine.py.

The golden indexes are fake APKINDEX.tar.gz files whose index stream was signed once
with a throwaway 2048-bit key by OpenSSL (``openssl dgst -sha1|-sha256 -sign``), an
implementation independent of ours. Only the public half of that key is kept.
"""
import base64
import gzip
import io
import os
import shutil
import tarfile
import tempfile
import unittest
from unittest import mock

from overpressure import alpine, apksign

from overpressure import REPO, stage1

TEST_KEY_NAME = "pico4-test@example.invalid-00000001.rsa.pub"
TEST_KEY_PEM = """\
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0QtluqGfabcwuxCI/oNn
3JrEniGI03wGNOC2+zUGzumb22yfVf2tUfAIHxK+CKjMxbj8Gw8ZyiW02XKJAste
qh9lTMRxyuxn0Pcq/PzU1JgtgT0eE53OQ8ffFsfFAFu/vWuhuk+jzgXurNK8jDYm
6cy8x+Hsq6hWhEhDck1CRNTUf3NP2Vh/DNGsHnSFgptVSM0DSZQ3WXBgSpWxDIs/
eF7ikppe/Zq9AQMbtL6iIGcE+NfdnG5U+JOBMfOfsoT+e2xdh8HDdvngN0fM5Lxd
j9R/JGqbNBOdpBDQI1L9dj4xDHYuwpgNS+ZY9NzKyfQec3MgCwAzl04oLV8xL89a
zwIDAQAB
-----END PUBLIC KEY-----
"""
TEST_KEYS = {TEST_KEY_NAME: TEST_KEY_PEM}

# .SIGN.RSA.<TEST_KEY_NAME> (SHA-1) + index stream with DESCRIPTION and APKINDEX.
INDEX_SHA1 = base64.b64decode(
    "H4sIAAAAAAACCtML9nT30wsKdtQryEzON9EtSS0ucUitSMwtyEnVy8wrS8zJTNE1gABDvaLiRL2C0iQG0gBIr5mJCQPUGHTa"
    "wATINjQxNTIxNwezDQzNTA0NGBQMGOgASotLEouATmEYmWBS8b9jO/PP3z8r/PZFQliigNLCB5paz96nGv7IY/BecW/5j9tz"
    "3vktFbogwH9x/4udV279vXNL8NAfpV9zPtUH3apuyA0RiS3bfuGsdbf+Mx/mpyvCw8x8Kzb2MGk2Lb14bl99hkbnz8zWl78d"
    "xRIkonvajXNv1bbznkvt7/ZQNLHPuVlxd/XDpo3p+gvcWOc7va+0OX8v3XK5SGC0pe+vH4LbOGdN2+avWrWd3SL5SvL5cPsH"
    "VSHdvzsNfx29qXk42YnjppDL3icFRcvF1h23dVdc93dbn77Ie5u/d5MWeP58nfJErH7Jvpi7s+t+r1DNe5P4Ke19uM8hjsYD"
    "29V+3Tee0Mzz0y3KcuMCo/MMIxwAAGgyDzYABAAAH4sIAAAAAAACCu3UwWrCQBSF4VnPU+QFLHfSMYEBF2JchIJNVcRtGtMa"
    "bdUmsdo+fdMs3bgyG/8PhguXgbs6JxrPRtM4mcfPE3Ur0gisbWfjcor4Rhnb920Yim33YV8C5YnqwLGq07I5qe7Tocj21qvz"
    "qvaK3So/a4V7Mkye4kk0Xt7yxrX8G99e5t88+uS/CyP3YtLXbJW/va+Lzfbjc7c/fJVVffw+nX9+ZaAT11ZE778i9MKZB+mV"
    "oocuTctsHVg9c0bHzZu79kfkNBUCAAAAAAAAAAAAAADQoT/pAadJACgAAA=="
)
# .SIGN.RSA256.<TEST_KEY_NAME> (SHA-256) + the same index stream.
INDEX_SHA256 = base64.b64decode(
    "H4sIAAAAAAACCtML9nT30wsKdjQyNdMryEzON9EtSS0ucUitSMwtyEnVy8wrS8zJTNE1gABDvaLiRL2C0iQGEgBIo5mJCQPU"
    "DHTawATINjQxNTIxNwezDQzNzE1MGRQMGOgASotLEouATmEYmWCi/a21+5QerLiyK+O/4a3Jn7Yuiv67qDvZhKe890PV0zT2"
    "Kbf6it+ffnfljnPlkZmBM2boJl1jZzicMqfk0fLqq/bNysusMm+oG062n3r7yfZZUR5nr5+ZcH1T/867+15cfS6w9lK5dcr0"
    "LW/8+hb/m2IxO2LipB1RXo9kHm51iKlm6NuqeOHLDiEOdt1TU1Ztri643ZDKxBk/5cXPuNRLbz9P0pzQET53sY7prQT9y8VL"
    "lm/xfxp71F3AiiX/56mYlZkT3XZ/ZX3Dbc+eoGBZuKRi24xpPJffObjPcu4vNv2oXflPyKryxosf1/bIz0oNeiP5ctt98RMd"
    "rDstXkRujGxQN93nfoVn/7uvb7Wj5zKMcAAAlOTC/QAEAAAfiwgAAAAAAAIK7dTBasJAFIXhWc9T5AUsd9IxgQEXYlyEgk1V"
    "xG0a0xpt1Sax2j590yzduDIb/w+GC5eBuzonGs9G0ziZx88TdSvSCKxtZ+NyivhGGdv3bRiKbfdhXwLlierAsarTsjmp7tOh"
    "yPbWq/Oq9ordKj9rhXsyTJ7iSTRe3vLGtfwb317m3zz65L8LI/di0tdslb+9r4vN9uNztz98lVV9/D6df35loBPXVkTvvyL0"
    "wpkH6ZWihy5Ny2wdWD1zRsfNm7v2R+Q0FQIAAAAAAAAAAAAAANChP+kBp0kAKAAA"
)
SIGNATURE_STREAM_SHA1 = 390  # length of the first gzip stream of INDEX_SHA1


def _tar(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _streams(raw: bytes) -> list[bytes]:
    return apksign.split_gzip_streams(raw)


class SplitTest(unittest.TestCase):
    def test_boundaries_are_the_real_stream_ends(self):
        sig, index = _streams(INDEX_SHA1)
        self.assertEqual(sig + index, INDEX_SHA1)
        self.assertEqual(len(sig), SIGNATURE_STREAM_SHA1)
        tar = gzip.decompress(index)
        self.assertIn(b"P:pico4-test", tar)

    def test_trailing_garbage_and_truncation_fail(self):
        with self.assertRaises(apksign.SignatureError):
            apksign.split_gzip_streams(INDEX_SHA1 + b"garbage")
        with self.assertRaisesRegex(apksign.SignatureError, "truncated"):
            apksign.split_gzip_streams(INDEX_SHA1[:-10])


class PublicKeyTest(unittest.TestCase):
    def test_pinned_alpine_keys_parse(self):
        keys = apksign.alpine_keys("aarch64")
        self.assertEqual(
            sorted(keys),
            ["alpine-devel@lists.alpinelinux.org-58199dcc.rsa.pub",
             "alpine-devel@lists.alpinelinux.org-616ae350.rsa.pub"],
        )
        bits = {name: apksign.parse_public_key(pem).n.bit_length() for name, pem in keys.items()}
        self.assertEqual(bits["alpine-devel@lists.alpinelinux.org-58199dcc.rsa.pub"], 2048)
        self.assertEqual(bits["alpine-devel@lists.alpinelinux.org-616ae350.rsa.pub"], 4096)
        for pem in keys.values():
            self.assertEqual(apksign.parse_public_key(pem).e, 65537)

    def test_unknown_arch_has_no_keys(self):
        with self.assertRaises(apksign.SignatureError):
            apksign.alpine_keys("x86_64")

    def test_malformed_keys_are_rejected(self):
        with self.assertRaises(apksign.SignatureError):
            apksign.parse_public_key("not a key")
        broken = TEST_KEY_PEM.replace("zwIDAQAB", "zwIDAQ==")  # truncated DER
        with self.assertRaises(apksign.SignatureError):
            apksign.parse_public_key(broken)


class VerifyIndexTest(unittest.TestCase):
    def test_valid_sha1_signature(self):
        self.assertEqual(apksign.verify_index(INDEX_SHA1, TEST_KEYS), TEST_KEY_NAME)

    def test_valid_sha256_signature(self):
        self.assertEqual(apksign.verify_index(INDEX_SHA256, TEST_KEYS), TEST_KEY_NAME)

    def test_tampered_index_fails(self):
        sig, index = _streams(INDEX_SHA1)
        tar = gzip.decompress(index).replace(b"V:1.0-r0", b"V:6.6-r6")
        tampered = sig + gzip.compress(tar)
        with self.assertRaisesRegex(apksign.SignatureError, "bad sha1 signature"):
            apksign.verify_index(tampered, TEST_KEYS)

    def test_sha1_signature_relabelled_as_sha256_fails(self):
        index = _streams(INDEX_SHA1)[1]
        relabelled = gzip.compress(_tar([(".SIGN.RSA256." + TEST_KEY_NAME, _signature_bytes(INDEX_SHA1))]))
        with self.assertRaisesRegex(apksign.SignatureError, "bad sha256 signature"):
            apksign.verify_index(relabelled + index, TEST_KEYS)

    def test_tampered_signature_fails(self):
        sig = bytearray(_signature_bytes(INDEX_SHA1))
        sig[100] ^= 0x01
        index = _streams(INDEX_SHA1)[1]
        stream = gzip.compress(_tar([(".SIGN.RSA." + TEST_KEY_NAME, bytes(sig))]))
        with self.assertRaisesRegex(apksign.SignatureError, "bad sha1 signature"):
            apksign.verify_index(stream + index, TEST_KEYS)

    def test_unknown_key_fails(self):
        with self.assertRaisesRegex(apksign.SignatureError, "unknown key"):
            apksign.verify_index(INDEX_SHA1, apksign.alpine_keys("aarch64"))

    def test_trusted_name_with_other_key_fails(self):
        alpine_pem = apksign.alpine_keys("aarch64")["alpine-devel@lists.alpinelinux.org-616ae350.rsa.pub"]
        with self.assertRaisesRegex(apksign.SignatureError, "bad sha1 signature"):
            apksign.verify_index(INDEX_SHA1, {TEST_KEY_NAME: alpine_pem})

    def test_missing_signature_stream_fails(self):
        index = _streams(INDEX_SHA1)[1]
        with self.assertRaisesRegex(apksign.SignatureError, "found 1 gzip stream"):
            apksign.verify_index(index, TEST_KEYS)

    def test_signature_stream_without_sign_entry_fails(self):
        index = _streams(INDEX_SHA1)[1]
        unsigned = gzip.compress(_tar([("APKINDEX", b"P:evil\n\n")]))
        with self.assertRaisesRegex(apksign.SignatureError, "not signed"):
            apksign.verify_index(unsigned + index, TEST_KEYS)

    def test_extra_stream_fails(self):
        with self.assertRaisesRegex(apksign.SignatureError, "found 3 gzip stream"):
            apksign.verify_index(INDEX_SHA1 + gzip.compress(b"extra"), TEST_KEYS)


class Pkcs1Test(unittest.TestCase):
    def setUp(self):
        self.key = apksign.parse_public_key(TEST_KEY_PEM)
        self.message = _streams(INDEX_SHA1)[1]
        self.sig = _signature_bytes(INDEX_SHA1)

    def test_valid(self):
        self.assertTrue(apksign.verify_pkcs1v15(self.key, self.message, self.sig, "sha1"))

    def test_wrong_length_or_range_rejected(self):
        self.assertFalse(apksign.verify_pkcs1v15(self.key, self.message, self.sig[1:], "sha1"))
        self.assertFalse(apksign.verify_pkcs1v15(self.key, self.message, b"\x00" + self.sig, "sha1"))
        too_big = self.key.n.to_bytes(self.key.size, "big")
        self.assertFalse(apksign.verify_pkcs1v15(self.key, self.message, too_big, "sha1"))

    def test_wrong_digest_rejected(self):
        self.assertFalse(apksign.verify_pkcs1v15(self.key, self.message, self.sig, "sha256"))


class LoadIndexTest(unittest.TestCase):
    """alpine.load_index verifies before parsing and drops a bad index from the cache."""

    def _cache(self, raw: bytes) -> tuple[str, str]:
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "v0", "main", "aarch64", "APKINDEX.tar.gz")
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as f:
            f.write(raw)
        return tmp, path

    def test_signed_index_loads(self):
        cache, _ = self._cache(INDEX_SHA1)
        with mock.patch.dict(apksign.ALPINE_KEYS, {"aarch64": TEST_KEYS}):
            index = alpine.load_index(cache, "v0", ["main"], "aarch64", log=lambda *_: None)
        self.assertEqual(index.packages["pico4-test"].version, "1.0-r0")

    def test_bad_index_fails_and_is_removed(self):
        cache, path = self._cache(INDEX_SHA1)
        with self.assertRaises(ValueError):
            alpine.load_index(cache, "v0", ["main"], "aarch64", log=lambda *_: None)
        self.assertFalse(os.path.exists(path))


SPEC = stage1.Spec(overlay=REPO)
REAL_CACHE = os.path.join(str(REPO), "cache", SPEC.alpine_branch)


class RealIndexTest(unittest.TestCase):
    """Alpine's real indexes verify against the pinned keys (when the build cached them)."""

    def test_cached_indexes(self):
        paths = [os.path.join(REAL_CACHE, repo, SPEC.arch, "APKINDEX.tar.gz") for repo in SPEC.alpine_repos]
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            self.skipTest("no cached Alpine APKINDEX (run a stage-1 build first)")
        for path in paths:
            with open(path, "rb") as f:
                raw = f.read()
            with self.subTest(path=path):
                self.assertTrue(apksign.verify_index(raw, apksign.alpine_keys(SPEC.arch)).startswith("alpine-devel@"))


def _signature_bytes(raw: bytes) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(_streams(raw)[0])), mode="r:") as tf:
        member = tf.next()
        return tf.extractfile(member).read()


if __name__ == "__main__":
    unittest.main()
