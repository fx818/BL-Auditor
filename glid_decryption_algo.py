import base64
import urllib.parse


def get_encrypted_id(data: bytes, key: str = "1996c39iil") -> bytes:
    s = list(range(256))
    j = 0
    key_bytes = key.encode()

    # KSA
    for i in range(256):
        j = (j + s[i] + key_bytes[i % len(key_bytes)]) % 256
        s[i], s[j] = s[j], s[i]

    # PRGA
    i = 0
    j = 0
    result = bytearray()

    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        k = s[(s[i] + s[j]) % 256]
        result.append(byte ^ k)

    return bytes(result)


def decrypt_glid(encrypted_glid: str, key: str = "1996c39iil") -> str:
    # reverse urlencode
    url_decoded = urllib.parse.unquote(encrypted_glid)

    # reverse base64
    encrypted_bytes = base64.b64decode(url_decoded)

    # decrypt using same RC4-style algo
    original_bytes = get_encrypted_id(encrypted_bytes, key)

    return original_bytes.decode("utf-8")


if __name__ == "__main__":
    encrypted_glid = input("Enter encrypted GLID: ").strip()
    try:
        original_glid = decrypt_glid(encrypted_glid)
        print("Original GLID:", original_glid)
    except Exception as e:
        print("Error while decrypting:", e)