"""Verify the RSA signature of an Alpine APKINDEX.tar.gz with the standard library.

An APKINDEX.tar.gz is two concatenated gzip streams. The first holds a tar entry
``.SIGN.RSA.<key>`` (SHA-1) or ``.SIGN.RSA256.<key>`` (SHA-256) whose content is an
RSASSA-PKCS1-v1_5 signature over the raw compressed bytes of the second stream, the
tar with APKINDEX and DESCRIPTION. ``<key>`` names one of Alpine's signing keys.

HTTPS only authenticates the mirror; the signature ties the index (and through its
"C:" checksums every package) to Alpine's own keys, as apk-tools does.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import tarfile
import zlib
from dataclasses import dataclass


class SignatureError(ValueError):
    """The index is unsigned, signed by an unknown key, or the signature is wrong."""


# Alpine's signing public keys for the architectures the build uses, as shipped in
# the alpine-keys package (/usr/share/apk/keys/<arch>/ in the minirootfs) and published
# at https://alpinelinux.org/keys/. Both aarch64 keys are pinned; 616ae350 signs the
# v3.24 indexes.
ALPINE_KEYS: dict[str, dict[str, str]] = {
    "aarch64": {
        "alpine-devel@lists.alpinelinux.org-58199dcc.rsa.pub": """\
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA3v8/ye/V/t5xf4JiXLXa
hWFRozsnmn3hobON20GdmkrzKzO/eUqPOKTpg2GtvBhK30fu5oY5uN2ORiv2Y2ht
eLiZ9HVz3XP8Fm9frha60B7KNu66FO5P2o3i+E+DWTPqqPcCG6t4Znk2BypILcit
wiPKTsgbBQR2qo/cO01eLLdt6oOzAaF94NH0656kvRewdo6HG4urbO46tCAizvCR
CA7KGFMyad8WdKkTjxh8YLDLoOCtoZmXmQAiwfRe9pKXRH/XXGop8SYptLqyVVQ+
tegOD9wRs2tOlgcLx4F/uMzHN7uoho6okBPiifRX+Pf38Vx+ozXh056tjmdZkCaV
aQIDAQAB
-----END PUBLIC KEY-----
""",
        "alpine-devel@lists.alpinelinux.org-616ae350.rsa.pub": """\
-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAyduVzi1mWm+lYo2Tqt/0
XkCIWrDNP1QBMVPrE0/ZlU2bCGSoo2Z9FHQKz/mTyMRlhNqTfhJ5qU3U9XlyGOPJ
piM+b91g26pnpXJ2Q2kOypSgOMOPA4cQ42PkHBEqhuzssfj9t7x47ppS94bboh46
xLSDRff/NAbtwTpvhStV3URYkxFG++cKGGa5MPXBrxIp+iZf9GnuxVdST5PGiVGP
ODL/b69sPJQNbJHVquqUTOh5Ry8uuD2WZuXfKf7/C0jC/ie9m2+0CttNu9tMciGM
EyKG1/Xhk5iIWO43m4SrrT2WkFlcZ1z2JSf9Pjm4C2+HovYpihwwdM/OdP8Xmsnr
DzVB4YvQiW+IHBjStHVuyiZWc+JsgEPJzisNY0Wyc/kNyNtqVKpX6dRhMLanLmy+
f53cCSI05KPQAcGj6tdL+D60uKDkt+FsDa0BTAobZ31OsFVid0vCXtsbplNhW1IF
HwsGXBTVcfXg44RLyL8Lk/2dQxDHNHzAUslJXzPxaHBLmt++2COa2EI1iWlvtznk
Ok9WP8SOAIj+xdqoiHcC4j72BOVVgiITIJNHrbppZCq6qPR+fgXmXa+sDcGh30m6
9Wpbr28kLMSHiENCWTdsFij+NQTd5S47H7XTROHnalYDuF1RpS+DpQidT5tUimaT
JZDr++FjKrnnijbyNF8b98UCAwEAAQ==
-----END PUBLIC KEY-----
""",
    },
}

# DER of the DigestInfo header (AlgorithmIdentifier with NULL parameters, then the
# OCTET STRING tag and length) that precedes the hash in EMSA-PKCS1-v1_5 (RFC 8017, 9.2).
_DIGEST_INFO = {
    "sha1": bytes.fromhex("3021300906052b0e03021a05000414"),
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
}
# Signature entry prefixes and the hash each one implies.
_SIGN_PREFIXES = ((".SIGN.RSA256.", "sha256"), (".SIGN.RSA.", "sha1"))
_RSA_ENCRYPTION_OID = bytes.fromhex("2a864886f70d010101")  # 1.2.840.113549.1.1.1


@dataclass(frozen=True)
class RsaPublicKey:
    n: int
    e: int

    @property
    def size(self) -> int:
        """Modulus length in bytes: every signature and encoded message has this size."""
        return (self.n.bit_length() + 7) // 8


def _der_read(data: bytes, pos: int, tag: int) -> tuple[bytes, int]:
    """Read one DER element with ``tag`` at ``pos``; return (content, next position)."""
    if pos + 2 > len(data) or data[pos] != tag:
        raise SignatureError(f"public key: expected DER tag 0x{tag:02x} at offset {pos}")
    length, pos = data[pos + 1], pos + 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4 or pos + count > len(data):
            raise SignatureError("public key: unsupported DER length")
        length, pos = int.from_bytes(data[pos:pos + count], "big"), pos + count
    if pos + length > len(data):
        raise SignatureError("public key: truncated DER element")
    return data[pos:pos + length], pos + length


def _der_uint(content: bytes) -> int:
    if not content or content[0] & 0x80:
        raise SignatureError("public key: negative or empty INTEGER")
    return int.from_bytes(content, "big")


def parse_public_key(pem: str) -> RsaPublicKey:
    """Parse a PEM SubjectPublicKeyInfo holding an RSA key (what Alpine publishes)."""
    lines = [l.strip() for l in pem.strip().splitlines()]
    if len(lines) < 3 or lines[0] != "-----BEGIN PUBLIC KEY-----" or lines[-1] != "-----END PUBLIC KEY-----":
        raise SignatureError("public key: not a PEM PUBLIC KEY")
    try:
        der = base64.b64decode("".join(lines[1:-1]), validate=True)
    except binascii.Error as exc:
        raise SignatureError(f"public key: bad base64 ({exc})") from None
    spki, end = _der_read(der, 0, 0x30)
    if end != len(der):
        raise SignatureError("public key: trailing data")
    algorithm, pos = _der_read(spki, 0, 0x30)
    oid, _ = _der_read(algorithm, 0, 0x06)
    if oid != _RSA_ENCRYPTION_OID:
        raise SignatureError("public key: not an RSA key")
    bits, pos = _der_read(spki, pos, 0x03)
    if pos != len(spki) or not bits or bits[0] != 0:
        raise SignatureError("public key: malformed BIT STRING")
    rsa_key, end = _der_read(bits, 1, 0x30)
    if end != len(bits):
        raise SignatureError("public key: trailing data in RSAPublicKey")
    n_der, pos = _der_read(rsa_key, 0, 0x02)
    e_der, pos = _der_read(rsa_key, pos, 0x02)
    if pos != len(rsa_key):
        raise SignatureError("public key: trailing data in RSAPublicKey")
    n, e = _der_uint(n_der), _der_uint(e_der)
    if n.bit_length() < 1024 or e < 3 or e % 2 == 0:
        raise SignatureError("public key: implausible RSA parameters")
    return RsaPublicKey(n, e)


def verify_pkcs1v15(key: RsaPublicKey, message: bytes, signature: bytes, digest: str) -> bool:
    """RSASSA-PKCS1-v1_5 verification (RFC 8017, 8.2.2).

    Rebuilds the whole expected encoding and compares it, instead of parsing the
    decrypted block, so no lenient parsing of padding or DigestInfo can be abused.
    """
    k = key.size
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= key.n:
        return False
    encoded = pow(s, key.e, key.n).to_bytes(k, "big")
    t = _DIGEST_INFO[digest] + hashlib.new(digest, message).digest()
    if k < len(t) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(encoded, expected)


def split_gzip_streams(raw: bytes) -> list[bytes]:
    """Split concatenated gzip streams at their real ends (not at magic bytes)."""
    streams, pos = [], 0
    while pos < len(raw):
        d = zlib.decompressobj(wbits=31)
        try:
            d.decompress(raw[pos:])
        except zlib.error as exc:
            raise SignatureError(f"corrupt gzip stream at offset {pos}: {exc}") from None
        if not d.eof:
            raise SignatureError(f"truncated gzip stream at offset {pos}")
        end = len(raw) - len(d.unused_data)
        streams.append(raw[pos:end])
        pos = end
    return streams


def _signatures(stream: bytes) -> list[tuple[str, str, bytes]]:
    """Return (key name, digest, signature) for each .SIGN.* entry of the first stream."""
    found = []
    with tarfile.open(fileobj=io.BytesIO(zlib.decompress(stream, wbits=31)), mode="r:") as tf:
        try:
            for m in tf:
                for prefix, digest in _SIGN_PREFIXES:
                    if m.isreg() and m.name.startswith(prefix):
                        found.append((m.name[len(prefix):], digest, tf.extractfile(m).read()))
                        break
        except tarfile.ReadError:
            pass  # apk writes signature tars without the end-of-archive blocks
    return found


def verify_index(raw: bytes, keys: dict[str, str], what: str = "APKINDEX.tar.gz") -> str:
    """Verify a signed APKINDEX.tar.gz against ``keys`` (key name -> PEM).

    Every signature by a known key must verify and at least one must exist; signatures
    by other keys are ignored, as apk-tools does. Returns the name of the key used.
    """
    streams = split_gzip_streams(raw)
    if len(streams) != 2:
        raise SignatureError(f"{what}: expected a signature and an index stream, found {len(streams)} gzip stream(s)")
    try:
        sigs = _signatures(streams[0])
    except (tarfile.TarError, zlib.error) as exc:
        raise SignatureError(f"{what}: unreadable signature stream ({exc})") from None
    if not sigs:
        raise SignatureError(f"{what}: not signed (no .SIGN.RSA entry)")
    trusted = [(name, digest, sig) for name, digest, sig in sigs if name in keys]
    if not trusted:
        names = ", ".join(name for name, _, _ in sigs)
        raise SignatureError(f"{what}: signed by unknown key(s) {names}; pinned: {', '.join(sorted(keys))}")
    for name, digest, sig in trusted:
        if not verify_pkcs1v15(parse_public_key(keys[name]), streams[1], sig, digest):
            raise SignatureError(f"{what}: bad {digest} signature by {name}")
    return trusted[0][0]


def alpine_keys(arch: str) -> dict[str, str]:
    """The pinned Alpine signing keys for ``arch``."""
    try:
        return ALPINE_KEYS[arch]
    except KeyError:
        raise SignatureError(f"no pinned Alpine signing keys for {arch}") from None
