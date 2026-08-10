"""Create local demo SQLite databases for RailTrust Exchange."""

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

from app import (
    DB,
    VENDOR_DB,
    calculate_dates,
    ensure_table_columns,
    get_risk_level,
    hash_password,
    init_vendor_db,
)


def reset_databases():
    for db_path in (Path(DB), Path(VENDOR_DB)):
        if db_path.exists():
            db_path.unlink()


def seed():
    init_vendor_db()
    ensure_table_columns()

    vendor_conn = sqlite3.connect(VENDOR_DB)
    vendor_cur = vendor_conn.cursor()
    vendor_cur.execute(
        """
        INSERT INTO vendors
        (company_name, contact_person, email, password, phone, address,
         railway_zone, railway_division, supply_region, registration_date, vendor_risk)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Howrah Rail Components",
            "Ananya Sen",
            "seller@railtrust.local",
            hash_password("seller123"),
            "+91 90000 10001",
            "Howrah Industrial Estate, West Bengal",
            "South Eastern Railway",
            "HWH Division",
            "West Bengal, India",
            datetime.now().date().isoformat(),
            "Low",
        ),
    )
    vendor_id = vendor_cur.lastrowid
    vendor_cur.execute(
        """
        INSERT INTO vendor_reviews
        (vendor_id, reviewer_name, railway_unit, rating, comment, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            vendor_id,
            "HWH Procurement Cell",
            "HWH Division",
            5,
            "Consistent dispatch quality and clear QR traceability.",
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    vendor_conn.commit()
    vendor_conn.close()

    products = [
        ("SER-HWH-BPAD-001", "Composite Brake Pad", "Brake Systems", 1450, 8, 32, "operational normally"),
        ("SER-HWH-CLIP-002", "Elastic Rail Clip", "Track Fittings", 220, 0, 180, "minor wear acceptable"),
        ("SER-HWH-PAD-003", "Rail Rubber Pad", "Track Fittings", 380, 5, 96, "good condition"),
    ]

    conn = sqlite3.connect(DB)
    for uid, item_type, category, price, discount, stock, notes in products:
        risk = get_risk_level({"uid": uid, "item_type": item_type, "notes": notes})
        supply_date = datetime.now().date().isoformat()
        warranty_end = (datetime.now().date() + timedelta(days=365)).isoformat()
        inspection, repair = calculate_dates(supply_date, supply_date, warranty_end, risk)
        conn.execute(
            """
            INSERT INTO fittings
            (uid, item_type, vendor, vendor_id, lot, supply_date, warranty, warranty_end,
             manufactor_date, manufactor_number, notes, vendor_email, udm_synced, tms_synced,
             risk_flag, risk, vendor_risk, inspection_date, repair_date, category, price,
             discount, stock)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, 'Low', ?, ?, ?, ?, ?, ?)
            """,
            (
                uid,
                item_type,
                "Howrah Rail Components",
                str(vendor_id),
                "HWH-LOT-26",
                supply_date,
                supply_date,
                warranty_end,
                supply_date,
                f"MFG-{uid[-3:]}",
                notes,
                "seller@railtrust.local",
                1 if risk == "High" else 0,
                risk,
                inspection,
                repair,
                category,
                price,
                discount,
                stock,
            ),
        )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    reset_databases()
    seed()
    print("Demo data created.")
    print("Seller login: seller@railtrust.local / seller123")
