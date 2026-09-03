"""Generate PNS query SQL in batches and as a combined file.

Usage:
    python PNS/generate_pns_query.py [csv_path]
Default csv_path: PNS/2500ofr_ids.csv
"""
import csv
import sys
from datetime import datetime

CSV_PATH = sys.argv[1] if len(sys.argv) > 1 else "PNS/2500ofr_ids.csv"
BATCH_SIZE = 4000

import os
_stem = os.path.splitext(os.path.basename(CSV_PATH))[0]  # e.g. "20k_offers"
_dir  = os.path.dirname(CSV_PATH)                         # e.g. "PNS"


def parse_date(raw: str) -> str:
    """Convert '29/07/26 00:00' → '2026-07-29'"""
    dt = datetime.strptime(raw.strip(), "%d/%m/%y %H:%M")
    return dt.strftime("%Y-%m-%d")


def make_sql(batch: list, batch_num: int, total_batches: int) -> str:
    from datetime import timedelta
    dates = [datetime.strptime(r["date"], "%Y-%m-%d") for r in batch]
    broad_min = (min(dates) - timedelta(days=1)).strftime("%Y-%m-%d")
    broad_max = (max(dates) + timedelta(days=1)).strftime("%Y-%m-%d")

    values_lines = []
    for i, r in enumerate(batch):
        comma = "," if i < len(batch) - 1 else ""
        values_lines.append(
            f"        ({r['offer_id']}, {r['mcat_id']}, {r['glid']}, '{r['date']}'){comma}"
        )
    values_block = "\n".join(values_lines)

    return f"""\
-- Batch {batch_num}/{total_batches} — rows {(batch_num-1)*BATCH_SIZE+1}–{(batch_num-1)*BATCH_SIZE+len(batch)}
-- Auto-generated from PNS/2500ofr_ids.csv
-- Broad date pre-filter ({broad_min} to {broad_max}) enables partition pruning.
-- Per-row date check in WHERE enforces the exact ±1 day window.

WITH offer_data AS (
    SELECT *
    FROM values(
        'eto_ofr_display_id Int64, eto_ofr_mcat_id Int32, fk_glusr_usr_id Int64, eto_ofr_approv_date_orig Date',
{values_block}
    )
)
SELECT
    p.user_glid,
    p.user_role,
    f.src_mcat_id,
    fel.llm_extracted_json_masked,
    fel.created_at,
    o.eto_ofr_display_id,
    o.eto_ofr_approv_date_orig
FROM pns_insight.participants p
INNER JOIN pns_insight.files f
    ON p.file_id = f.id
INNER JOIN pns_insight.file_extraction_logs fel
    ON f.id = fel.file_id
INNER JOIN offer_data o
    ON p.user_glid    = o.fk_glusr_usr_id
    AND f.src_mcat_id = o.eto_ofr_mcat_id
WHERE p.is_active   = TRUE
  AND f.is_active   = TRUE
  AND fel.is_active = TRUE
  AND p.user_role   = 'BUYER'
  AND toDate(fel.created_at) BETWEEN '{broad_min}' AND '{broad_max}'
  AND toDate(fel.created_at) BETWEEN (o.eto_ofr_approv_date_orig - 1)
                                  AND (o.eto_ofr_approv_date_orig + 1)
ORDER BY fel.created_at DESC;
"""


rows = []
with open(CSV_PATH, newline="") as f:
    reader = csv.DictReader(f)
    for r in reader:
        rows.append({
            "offer_id": int(r["eto_ofr_display_id"]),
            "mcat_id":  int(r["eto_ofr_mcat_id"]),
            "glid":     int(r["fk_glusr_usr_id"]),
            "date":     parse_date(r["eto_ofr_approv_date_orig"]),
        })

batches = [rows[i:i+BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
total = len(batches)

for idx, batch in enumerate(batches, start=1):
    out_path = f"{_dir}/{_stem}_batch{idx}.sql"
    with open(out_path, "w") as f:
        f.write(make_sql(batch, idx, total))
    print(f"Written: {out_path}  ({len(batch)} rows)")

# Combined single file
combined_path = f"{_dir}/{_stem}_combined.sql"
with open(combined_path, "w") as f:
    f.write(make_sql(rows, 1, 1).replace("-- Batch 1/1", f"-- Combined ({len(rows)} rows)"))
print(f"\nWritten: {combined_path}  ({len(rows)} rows)")
print(f"Done: {total} batch files + 1 combined")
