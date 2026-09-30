#!/usr/bin/env python3
"""Recalculate cost_usd for all existing rows using the updated pricing table.
Run as: sudo python3 scripts/recalc_costs.py
(needs root because dashboard.db is owned by root)
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.pricing import calculate_cost

DB = Path(__file__).resolve().parent.parent / "dashboard.db"

db = sqlite3.connect(str(DB))
rows = db.execute(
    "SELECT id, model, input_tokens, output_tokens, "
    "cache_creation_tokens, cache_read_tokens, cost_usd "
    "FROM api_requests"
).fetchall()

updated = 0
old_total = 0.0
new_total = 0.0

for row in rows:
    rid, model, inp, out, cc, cr, old_cost = row
    new_cost = calculate_cost(model, inp, out, cc, cr)
    old_total += old_cost
    new_total += new_cost

    if abs(new_cost - old_cost) > 0.000001:
        db.execute("UPDATE api_requests SET cost_usd = ? WHERE id = ?", (new_cost, rid))
        updated += 1

db.commit()
db.close()

print(f"Rows checked:  {len(rows)}")
print(f"Rows updated:  {updated}")
print(f"Old total:    ${old_total:.4f}")
print(f"New total:    ${new_total:.4f}")
print(f"Difference:   ${old_total - new_total:.4f}")
