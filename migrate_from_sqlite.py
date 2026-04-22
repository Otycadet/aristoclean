"""
migrate_from_sqlite.py
──────────────────────
One-time script to copy data from the original inventory_management_system.db
into the new Django database.

Usage (run from the aristoclean/ project root):
    python migrate_from_sqlite.py path/to/inventory_management_system.db
"""

import os
import sys
import sqlite3
import django
from pathlib import Path

# ── Bootstrap Django ───────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aristoclean.settings")
django.setup()

from inventory.models import Item, Location, StockEntry, IssueBatch, DistributionLine
from django.db import transaction


def run(source_db_path: str):
    print(f"Opening source database: {source_db_path}")
    conn = sqlite3.connect(source_db_path)
    conn.row_factory = sqlite3.Row

    with transaction.atomic():
        # ── Items ──────────────────────────────────────────────────────────
        print("Migrating items …")
        item_map: dict[int, Item] = {}
        for row in conn.execute("SELECT * FROM items"):
            item, _ = Item.objects.get_or_create(
                name=row["name"],
                defaults={
                    "unit": row["unit"],
                    "reorder_level": row["reorder_level"] or 0,
                    "active": bool(row.get("active", 1)),
                },
            )
            item_map[row["id"]] = item
        print(f"  {len(item_map)} items done.")

        # ── Locations ──────────────────────────────────────────────────────
        print("Migrating locations …")
        loc_map: dict[int, Location] = {}
        for row in conn.execute("SELECT * FROM locations"):
            loc, _ = Location.objects.get_or_create(
                name=row["name"],
                defaults={"active": bool(row.get("active", 1))},
            )
            loc_map[row["id"]] = loc
        print(f"  {len(loc_map)} locations done.")

        # ── Stock entries ──────────────────────────────────────────────────
        print("Migrating stock entries …")
        se_count = 0
        for row in conn.execute("SELECT * FROM stock_entries"):
            item = item_map.get(row["item_id"])
            if not item:
                print(f"  WARNING: stock_entry {row['id']} references missing item {row['item_id']}, skipping.")
                continue
            StockEntry.objects.get_or_create(
                item=item,
                received_at=row["received_at"][:10],  # DATE portion
                quantity=row["quantity"],
                defaults={
                    "supplier": row.get("supplier") or "",
                    "reference": row.get("reference") or "",
                    "notes": row.get("notes") or "",
                },
            )
            se_count += 1
        print(f"  {se_count} stock entries done.")

        # ── Issue batches + distribution lines ─────────────────────────────
        print("Migrating issue batches …")
        batch_count = 0
        line_count = 0
        for row in conn.execute("SELECT * FROM issue_batches ORDER BY id"):
            loc = loc_map.get(row["location_id"])
            if not loc:
                # create a placeholder location
                loc, _ = Location.objects.get_or_create(name=f"Unknown-{row['location_id']}")

            issued_at = (row.get("issued_at") or row.get("created_at") or "")[:10]

            batch, created = IssueBatch.objects.get_or_create(
                receipt_number=row["receipt_number"],
                defaults={
                    "location": loc,
                    "issued_to": row.get("issued_to") or "",
                    "issued_by": row.get("issued_by") or "",
                    "notes": row.get("notes") or "",
                    "issued_at": issued_at,
                },
            )
            if not created:
                continue  # already migrated

            batch_count += 1

            for drow in conn.execute(
                "SELECT * FROM distribution_lines WHERE issue_batch_id = ?", (row["id"],)
            ):
                item = item_map.get(drow["item_id"])
                if not item:
                    print(f"  WARNING: distribution_line {drow['id']} references missing item, skipping.")
                    continue
                DistributionLine.objects.create(
                    batch=batch, item=item, quantity=drow["quantity"]
                )
                line_count += 1

        print(f"  {batch_count} batches, {line_count} distribution lines done.")

    conn.close()
    print("\nMigration complete ✓")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python migrate_from_sqlite.py path/to/old_database.db")
        sys.exit(1)
    run(sys.argv[1])
