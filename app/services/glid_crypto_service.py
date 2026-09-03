"""GLID decryption.

The buyer's glid arrives encrypted in BuyLead responses (e.g. the
`FK_GLUSR_USR_ID` field, base64 over an RC4 keystream). This decrypts it back
to the plain numeric glid so it can be passed to the buyer-activity API.

Mirrors the standalone glid_decryption_algo.py reference script.
"""

import base64
import urllib.parse

_DEFAULT_KEY = "1996c39iil"


def _rc4(data: bytes, key: str) -> bytes:
    """RC4 keystream XOR (symmetric — same routine encrypts and decrypts)."""
    s = list(range(256))
    key_bytes = key.encode()

    # KSA
    j = 0
    for i in range(256):
        j = (j + s[i] + key_bytes[i % len(key_bytes)]) % 256
        s[i], s[j] = s[j], s[i]

    # PRGA
    i = j = 0
    out = bytearray()
    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        k = s[(s[i] + s[j]) % 256]
        out.append(byte ^ k)
    return bytes(out)


def decrypt_glid(encrypted_glid: str, key: str = _DEFAULT_KEY) -> str:
    """Decrypt an encrypted glid (url-encoded base64 RC4) to the plain glid string.

    e.g. "3qcAnJzculw=" -> "15425992". Raises on malformed input.
    """
    url_decoded = urllib.parse.unquote(encrypted_glid)
    encrypted_bytes = base64.b64decode(url_decoded)
    return _rc4(encrypted_bytes, key).decode("utf-8")
