import base64
import urllib.parse
from datetime import datetime, timedelta

_RC4_KEY = "1996c39iil"


def _rc4(data: bytes, key: str) -> bytes:
    s = list(range(256))
    kb = key.encode()
    j = 0
    for i in range(256):
        j = (j + s[i] + kb[i % len(kb)]) % 256
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        k = s[(s[i] + s[j]) % 256]
        out.append(byte ^ k)
    return bytes(out)


def _decrypt_glid(enc: str) -> str:
    return _rc4(base64.b64decode(urllib.parse.unquote(enc)), _RC4_KEY).decode()


def main(mcat: str, glid: str, approval_date: str) -> str:
    if len(approval_date) == 14:
        dt = datetime.strptime(approval_date, "%Y%m%d%H%M%S")
    else:
        dt = datetime.fromisoformat(approval_date)
    datetime_from = dt - timedelta(days=2)
    datetime_to = dt
    plain_glid = _decrypt_glid(glid)

    return f"""
SELECT p.user_glid, p.user_role, f.src_mcat_id, fel.llm_extracted_json_masked, fel.created_at
FROM pns_insight.participants p
INNER JOIN pns_insight.files f ON p.file_id = f.id
INNER JOIN pns_insight.file_extraction_logs fel ON f.id = fel.file_id
WHERE p.is_active = TRUE
  AND f.is_active = TRUE
  AND fel.is_active = TRUE
  AND p.user_role = 'BUYER'
  AND fel.created_at BETWEEN '{datetime_from}' AND '{datetime_to}'
  AND f.src_mcat_id = {mcat}
  AND p.user_glid = {plain_glid}
""".strip()
