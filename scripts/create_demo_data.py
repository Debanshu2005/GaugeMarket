"""Create local demo SQLite databases for RailTrust Exchange.

Usage:
    python scripts/create_demo_data.py

This resets both databases and seeds realistic data for all demo flows.
"""

import secrets
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import (
    DB,
    VENDOR_DB,
    calculate_dates,
    ensure_table_columns,
    get_risk_level,
    hash_password,
    init_vendor_db,
    init_extended_tables,
    build_structured_risk,
)


def reset_databases():
    for db_path in (Path(DB), Path(VENDOR_DB)):
        if db_path.exists():
            db_path.unlink()
    print("[Reset] Databases deleted.")


def seed():
    init_vendor_db()
    ensure_table_columns()
    init_extended_tables()

    # ── Vendors ──────────────────────────────────────────────────────────────
    vendor_conn = sqlite3.connect(VENDOR_DB)
    vc = vendor_conn.cursor()

    vendors_data = [
        (
            "Howrah Rail Components", "Ananya Sen",
            "seller@railtrust.local", hash_password("seller123"),
            "+91 90000 10001", "Howrah Industrial Estate, West Bengal",
            "South Eastern Railway", "HWH Division", "West Bengal, India",
            (datetime.now() - timedelta(days=120)).date().isoformat(), "Low",
        ),
        (
            "Delhi Track Solutions", "Rajiv Mehta",
            "delhi@tracksol.local", hash_password("track123"),
            "+91 90000 20002", "Connaught Place, New Delhi",
            "Northern Railway", "Delhi Division", "Delhi, India",
            (datetime.now() - timedelta(days=90)).date().isoformat(), "Medium",
        ),
    ]

    vendor_ids = []
    for v in vendors_data:
        vc.execute(
            """INSERT INTO vendors
               (company_name, contact_person, email, password, phone, address,
                railway_zone, railway_division, supply_region, registration_date, vendor_risk)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            v,
        )
        vendor_ids.append(vc.lastrowid)

    # Vendor reviews
    reviews = [
        (vendor_ids[0], "HWH Procurement Cell", "HWH Division", 5,
         "Consistent dispatch quality and clear QR traceability."),
        (vendor_ids[0], "Eastern Railways HQ", "HWH Division", 4,
         "Good component quality, minor delay on last shipment."),
        (vendor_ids[1], "Delhi Metro Rail Corp", "Delhi Division", 4,
         "Reliable supplier, components passed all inspections."),
    ]
    for r in reviews:
        vc.execute(
            """INSERT INTO vendor_reviews
               (vendor_id, reviewer_name, railway_unit, rating, comment, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (*r, datetime.now().isoformat(timespec="seconds")),
        )

    vendor_conn.commit()
    vendor_conn.close()

    # ── Components / Fittings ─────────────────────────────────────────────────
    products = [
        # (uid, item_type, category, price, discount, stock, notes, vendor_idx)
        ("SER-HWH-BPAD-001", "Composite Brake Pad",    "Brake Systems",  1450, 8,  32,  "operational normally",          0),
        ("SER-HWH-CLIP-002", "Elastic Rail Clip",      "Track Fittings",  220, 0, 180,  "minor wear acceptable",         0),
        ("SER-HWH-PAD-003",  "Rail Rubber Pad",        "Track Fittings",  380, 5,  96,  "good condition",                0),
        ("SER-HWH-BOLT-004", "Heavy Duty Fish Bolt",   "Track Fittings",  150, 0, 500,  "no visible defects",            0),
        ("SER-HWH-SENSOR-005","Axle Counter Sensor",   "Signaling",      8500,10,  15,  "requires calibration",          0),
        ("NR-DLI-RAIL-001",  "Rail Joint Plate",       "Track Fittings",  890, 5,  60,  "slight surface rust",           1),
        ("NR-DLI-SWITCH-002","Point Machine Switch",   "Signaling",      12500,0,   8,  "crack found on housing",        1),
    ]

    conn = sqlite3.connect(DB)
    now_iso = datetime.now().isoformat(timespec="seconds")

    uid_list = []
    for uid, item_type, category, price, discount, stock, notes, vidx in products:
        vendor_id = vendor_ids[vidx]
        vendor_name = vendors_data[vidx][0]
        vendor_email = vendors_data[vidx][2]
        risk = get_risk_level({"uid": uid, "item_type": item_type, "notes": notes})
        supply_date = (datetime.now() - timedelta(days=random.randint(30, 200))).date().isoformat()
        warranty_end = (datetime.now().date() + timedelta(days=random.randint(180, 730))).isoformat()
        mfg_date = (datetime.strptime(supply_date, "%Y-%m-%d") - timedelta(days=30)).date().isoformat()
        inspection, repair = calculate_dates(mfg_date, supply_date, warranty_end, risk)

        conn.execute(
            """INSERT INTO fittings
               (uid, item_type, vendor, vendor_id, lot, supply_date, warranty, warranty_end,
                manufactor_date, manufactor_number, notes, vendor_email, udm_synced, tms_synced,
                risk_flag, risk, vendor_risk, inspection_date, repair_date, category, price,
                discount, stock)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, 'Low', ?, ?, ?, ?, ?, ?)""",
            (
                uid, item_type, vendor_name, str(vendor_id),
                f"LOT-{uid[-3:]}", supply_date, supply_date, warranty_end,
                mfg_date, f"MFG-{uid[-3:]}", notes, vendor_email,
                1 if risk == "High" else 0, risk,
                inspection, repair, category, price, discount, stock,
            ),
        )
        uid_list.append((uid, item_type, vendor_name, risk, vendor_id))

    # ── Traceability events ───────────────────────────────────────────────────
    for uid, item_type, vendor_name, risk, vendor_id in uid_list:
        base_time = datetime.now() - timedelta(days=random.randint(60, 180))

        def te(event_type, desc, delta_days=0, order_no=None):
            t = (base_time + timedelta(days=delta_days)).isoformat(timespec="seconds")
            conn.execute(
                """INSERT INTO traceability_events
                   (uid, event_type, description, actor, location, order_no, event_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (uid, event_type, desc, vendor_name, "HWH Division", order_no, t),
            )

        te("REGISTERED",    f"Component '{item_type}' registered by vendor '{vendor_name}'.", 0)
        te("QR_GENERATED",  "QR digital identity generated and linked to component passport.", 0)
        if risk in ("High", "Medium"):
            te("RISK_UPDATED", f"AI risk assessment completed. Risk level: {risk}.", 1)
        te("LISTED",        "Component listed on RailQR marketplace.", 2)

    # ── Risk assessments ──────────────────────────────────────────────────────
    for uid, item_type, vendor_name, risk, vendor_id in uid_list:
        row = conn.execute("SELECT * FROM fittings WHERE uid=?", (uid,)).fetchone()
        if row:
            d = {k: row[k] for k in row.keys()}
            build_structured_risk(d)

    # ── Inspections ───────────────────────────────────────────────────────────
    inspection_data = [
        ("SER-HWH-BPAD-001", "PASSED",     "No defects found. Brake pad within tolerance.",   "Low"),
        ("SER-HWH-CLIP-002", "CONDITIONAL","Minor wear detected. Monitor at next service.",    "Medium"),
        ("NR-DLI-SWITCH-002","FAILED",     "Crack found on housing. Immediate replacement.",  "High"),
        ("SER-HWH-SENSOR-005","PASSED",    "Calibration completed. Sensor operational.",      "Low"),
    ]
    for uid, status, findings, risk_level in inspection_data:
        insp_date = (datetime.now() - timedelta(days=random.randint(5, 30))).date().isoformat()
        next_insp = (datetime.now() + timedelta(days=90 if risk_level == "Low" else 30)).date().isoformat()
        conn.execute(
            """INSERT INTO component_inspections
               (uid, inspector_name, inspection_date, status, findings, notes,
                risk_level, next_inspection_date, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, "HWH Inspection Team", insp_date, status, findings,
             "Routine scheduled inspection.", risk_level, next_insp, now_iso),
        )
        conn.execute(
            """INSERT INTO traceability_events
               (uid, event_type, description, actor, location, order_no, event_time)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (uid, "INSPECTED",
             f"Inspection {status} by HWH Inspection Team. {findings}",
             "HWH Inspection Team", "HWH Division", None,
             (datetime.now() - timedelta(days=random.randint(1, 10))).isoformat(timespec="seconds")),
        )

    # ── Historical orders ─────────────────────────────────────────────────────
    buyable = [p for p in products if p[5] > 0]  # stock > 0
    months_back = 4
    for m in range(months_back, -1, -1):
        for i in range(3):
            order_date = datetime.now() - timedelta(days=(m * 30) + (i * 7))
            order_no = f"ORD-{order_date.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}"
            items_to_buy = random.sample(buyable, random.randint(1, min(3, len(buyable))))
            subtotal = 0.0
            order_items = []
            for itm in items_to_buy:
                sale_price = itm[3] * (1 - itm[4] / 100)
                qty = random.randint(5, 30)
                line_total = round(sale_price * qty, 2)
                subtotal += line_total
                order_items.append((itm[0], itm[1], sale_price, qty, line_total, itm[7]))

            tax_total = round(subtotal * 0.05, 2)
            grand_total = round(subtotal + tax_total, 2)
            status = random.choice(["Completed", "Delivered"] if m > 0 else ["Shipped", "Placed", "Packed"])

            conn.execute(
                """INSERT INTO marketplace_orders
                   (order_no, customer_name, customer_email, customer_phone, shipping_address,
                    payment_method, status, subtotal, discount_total, tax_total, shipping_total,
                    grand_total, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_no, "Eastern Railways HQ", "procurement@easternrailways.in",
                    "+91 80000 20002", "Fairlie Place, Kolkata", "NetBanking",
                    status, subtotal, 0, tax_total, 0, grand_total,
                    order_date.isoformat(timespec="seconds"),
                ),
            )
            order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            for uid, item_type, sale_price, qty, line_total, vidx in order_items:
                vendor_id = vendor_ids[vidx]
                vendor_name = vendors_data[vidx][0]
                conn.execute(
                    """INSERT INTO marketplace_order_items
                       (order_id, uid, vendor_id, product_name, vendor, unit_price, quantity, line_total)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (order_id, uid, str(vendor_id), item_type, vendor_name, sale_price, qty, line_total),
                )
                # Traceability: purchased
                conn.execute(
                    """INSERT INTO traceability_events
                       (uid, event_type, description, actor, location, order_no, event_time)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (uid, "PURCHASED",
                     f"Purchased by Eastern Railways HQ. Order: {order_no}",
                     "Eastern Railways HQ", "Kolkata", order_no,
                     order_date.isoformat(timespec="seconds")),
                )

            # Shipment for completed/delivered/shipped orders
            if status in ("Completed", "Delivered", "Shipped"):
                tracking = f"IRL-{order_no[-8:]}"
                shipped_at = (order_date + timedelta(days=2)).isoformat(timespec="seconds")
                delivered_at = (order_date + timedelta(days=7)).isoformat(timespec="seconds") if status in ("Completed", "Delivered") else None
                ship_status = "DELIVERED" if status in ("Completed", "Delivered") else "SHIPPED"
                est_delivery = (order_date + timedelta(days=7)).date().isoformat()
                conn.execute(
                    """INSERT INTO shipments
                       (order_id, order_no, vendor_id, courier, tracking_number, status,
                        estimated_delivery, shipped_at, delivered_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (order_id, order_no, str(vendor_ids[0]),
                     "Indian Railways Logistics", tracking, ship_status,
                     est_delivery, shipped_at, delivered_at,
                     order_date.isoformat(timespec="seconds")),
                )
                for uid, item_type, *_ in order_items:
                    conn.execute(
                        """INSERT INTO traceability_events
                           (uid, event_type, description, actor, location, order_no, event_time)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (uid, "SHIPPED" if ship_status == "SHIPPED" else "DELIVERED",
                         f"Dispatched via Indian Railways Logistics. Tracking: {tracking}",
                         "Howrah Rail Components", "HWH Division", order_no,
                         shipped_at),
                    )

    # ── Audit log seed ────────────────────────────────────────────────────────
    for uid, item_type, vendor_name, risk, _ in uid_list:
        conn.execute(
            """INSERT INTO audit_log
               (action, entity_type, entity_id, actor, details, ip_address, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("COMPONENT_REGISTERED", "component", uid, vendor_name,
             f"type={item_type} risk={risk}", "127.0.0.1",
             (datetime.now() - timedelta(days=random.randint(1, 60))).isoformat(timespec="seconds")),
        )

    # ── Demo coupons ───────────────────────────────────────────────────────────
    demo_coupons = [
        ("RAIL10",    "percentage", 10,   0,    100),   # 10% off, no min, 100 uses
        ("FLAT500",   "flat",       500,  2000, 50),    # ₹500 off on orders ≥₹2000
        ("WELCOME",   "percentage", 15,   500,  200),   # 15% off for new buyers ≥₹500
    ]
    for code, dtype, dval, min_order, max_uses in demo_coupons:
        conn.execute(
            """INSERT INTO coupons
               (code, discount_type, discount_value, min_order_value, max_uses, used_count, active, created_at)
               VALUES (?, ?, ?, ?, ?, 0, 1, ?)""",
            (code, dtype, dval, min_order, max_uses, now_iso),
        )

    conn.commit()
    conn.close()
    print("[Seed] Done.")


if __name__ == "__main__":
    reset_databases()
    seed()
    print("\n✅ Demo data created successfully.")
    print("   Vendor login : seller@railtrust.local / seller123")
    print("   Admin login  : /admin  password: admin1234")
    print("   Second vendor: delhi@tracksol.local / track123")
    print("   Demo coupons : RAIL10 (10% off), FLAT500 (₹500 off ≥₹2000), WELCOME (15% off ≥₹500)")
