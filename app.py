from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, session
import secrets
import sqlite3
import os
import shutil
import tempfile
import threading
import webbrowser
import time
import socket
import json
from datetime import datetime, timedelta
import io
import base64
import qrcode
import requests
import hashlib
# Try to import AI-stylized QR generator; fall back if unavailable
try:
    import qrcode_artistic
    from qrcode_artistic import qr_art
    HAS_AI_QR = True
except Exception:
    qrcode_artistic = None
    qr_art = None
    HAS_AI_QR = False

# OpenCV / numpy / PIL
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import asyncio
import websockets

# External modules (assumed available)
from udm import push_to_udm
from tms import push_to_tms
from ai_module import get_risk_level, update_all_risks, QRAnomalyDetector

app = Flask(__name__)
# Use a stable fallback so sessions survive restarts during demo
app.secret_key = os.environ.get("SECRET_KEY", "railqr-hackathon-stable-key-2025")
# Ensure cookies work on Vercel (HTTPS, cross-request)
app.config.update(
    SESSION_COOKIE_SECURE=False,   # works on HTTP and HTTPS
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=86400,  # 24 hours
)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin1234")

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login', next=request.path))
        return f(*args, **kwargs)
    return decorated

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IS_VERCEL = bool(os.environ.get("VERCEL"))
RUNTIME_DIR = tempfile.gettempdir() if IS_VERCEL else BASE_DIR

def runtime_path(filename):
    return os.path.join(RUNTIME_DIR, filename)

def prepare_runtime_file(filename):
    source = os.path.join(BASE_DIR, filename)
    target = runtime_path(filename)
    if IS_VERCEL and os.path.exists(source) and not os.path.exists(target):
        shutil.copyfile(source, target)
    return target

DB = os.environ.get("FITTINGS_DB_PATH", prepare_runtime_file("fittings.db"))
VENDOR_DB = os.environ.get("VENDOR_DB_PATH", prepare_runtime_file("vendors.db"))

# QR code directory. Vercel functions can write to /tmp, not the bundled source tree.
qr_dir = os.environ.get(
    "QR_OUTPUT_DIR",
    os.path.join(RUNTIME_DIR, "qrcodes") if IS_VERCEL else os.path.join(BASE_DIR, "static", "qrcodes")
)
os.makedirs(qr_dir, exist_ok=True)
vendor_qr_dir = os.environ.get(
    "VENDOR_QR_OUTPUT_DIR",
    os.path.join(RUNTIME_DIR, "vendor_qrcodes") if IS_VERCEL else os.path.join(BASE_DIR, "static", "vendor_qrcodes")
)
os.makedirs(vendor_qr_dir, exist_ok=True)
vendor_gcode_dir = os.environ.get(
    "VENDOR_GCODE_OUTPUT_DIR",
    os.path.join(RUNTIME_DIR, "vendor_gcode") if IS_VERCEL else os.path.join(BASE_DIR, "static", "vendor_gcode")
)
os.makedirs(vendor_gcode_dir, exist_ok=True)

# ESP32 endpoint (update if you want)
ESP32_IP = "192.168.29.109"
ESP32_WS = f"ws://{ESP32_IP}:81"

# QR Anomaly Detector instance
qr_detector = QRAnomalyDetector()

# === Configuration: enable/disable AI-stylized QR ===
USE_AI_QR = True

# Path for a logo/background to embed into QR (user insisted logo is mandatory)
AI_QR_EMBED_IMAGE = os.environ.get("AI_QR_EMBED_IMAGE", os.path.join(BASE_DIR, "static", "image", "rail.png"))

# === Database Connection Helper ===
def get_db_connection():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def get_vendor_db_connection():
    conn = sqlite3.connect(VENDOR_DB)
    conn.row_factory = sqlite3.Row  # This makes rows behave like dictionaries
    return conn

def fetch_order_with_items(order_identifier):
    """Load an order by public order number, with numeric id as a convenience fallback."""
    order_identifier = str(order_identifier or '').strip()
    if not order_identifier:
        return None, []

    conn = get_db_connection()
    try:
        order_row = conn.execute(
            "SELECT * FROM marketplace_orders WHERE order_no=?",
            (order_identifier,)
        ).fetchone()
        if not order_row and order_identifier.isdigit():
            order_row = conn.execute(
                "SELECT * FROM marketplace_orders WHERE id=?",
                (int(order_identifier),)
            ).fetchone()
        if not order_row:
            return None, []

        order = {k: order_row[k] for k in order_row.keys()}
        item_rows = conn.execute(
            "SELECT * FROM marketplace_order_items WHERE order_id=?",
            (order['id'],)
        ).fetchall()
        items = [{k: row[k] for k in row.keys()} for row in item_rows]
        return order, items
    finally:
        conn.close()

def remember_recent_invoice(order, items):
    session['recent_invoice'] = {'order': order, 'items': items}
    session.modified = True

def get_recent_invoice(order_identifier):
    order_identifier = str(order_identifier or '').strip()
    snapshot = session.get('recent_invoice') or {}
    order = snapshot.get('order') or {}
    if (
        str(order.get('order_no', '')).strip() == order_identifier
        or str(order.get('id', '')).strip() == order_identifier
    ):
        return order, snapshot.get('items') or []
    return None, []

def current_buyer():
    buyer_id = session.get('buyer_id')
    if not buyer_id:
        return None
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM buyers WHERE id=?", (buyer_id,)).fetchone()
        return {k: row[k] for k in row.keys()} if row else None
    finally:
        conn.close()

def init_vendor_db():
    conn = sqlite3.connect(VENDOR_DB)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            contact_person TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            phone TEXT,
            address TEXT,
            railway_zone TEXT DEFAULT 'South Eastern Railway',
            railway_division TEXT DEFAULT 'HWH Division',
            supply_region TEXT DEFAULT 'West Bengal, India',
            registration_date TEXT,
            vendor_risk TEXT DEFAULT 'Low',
            failure_count INTEGER DEFAULT 0
        )
    ''')
    c.execute("PRAGMA table_info(vendors)")
    existing_cols = {row[1] for row in c.fetchall()}
    wanted = {
        "railway_zone": "TEXT DEFAULT 'South Eastern Railway'",
        "railway_division": "TEXT DEFAULT 'HWH Division'",
        "supply_region": "TEXT DEFAULT 'West Bengal, India'",
    }
    for col, coltype in wanted.items():
        if col not in existing_cols:
            c.execute(f"ALTER TABLE vendors ADD COLUMN {col} {coltype}")
    c.execute("""
        CREATE TABLE IF NOT EXISTS vendor_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            reviewer_name TEXT NOT NULL,
            railway_unit TEXT,
            rating INTEGER NOT NULL,
            comment TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(vendor_id) REFERENCES vendors(id)
        )
    """)
    conn.commit()
    conn.close()

# Initialize vendor database at startup
init_vendor_db()
# === Ensure table has required columns (adds missing columns automatically) ===
def ensure_table_columns():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS fittings (
            uid TEXT PRIMARY KEY,
            item_type TEXT,
            vendor TEXT,
            vendor_id TEXT,
            lot TEXT,
            supply_date TEXT,
            warranty TEXT,
            warranty_end TEXT,
            manufactor_date TEXT,
            manufactor_number TEXT,
            notes TEXT,
            vendor_email TEXT,
            udm_synced INTEGER DEFAULT 0,
            tms_synced INTEGER DEFAULT 0,
            risk_flag INTEGER DEFAULT 0,
            risk TEXT DEFAULT 'Low',
            vendor_risk TEXT DEFAULT 'Low',
            category TEXT DEFAULT 'Rail Components',
            price REAL DEFAULT 0,
            discount REAL DEFAULT 0,
            stock INTEGER DEFAULT 0,
            image_url TEXT
        )
    """)
    c.execute("PRAGMA table_info(fittings)")
    existing_cols = {row[1] for row in c.fetchall()}

    wanted = {
        "inspection_date": "TEXT",
        "repair_date": "TEXT",
        "failure_count": "INTEGER DEFAULT 0",
        "manufactor_date": "TEXT",
        "vendor_email":"TEXT",
        "manufactor_number": "TEXT",
        "vendor_risk": "TEXT",
        "vendor_id": "TEXT",
        "category": "TEXT DEFAULT 'Rail Components'",
        "price": "REAL DEFAULT 0",
        "discount": "REAL DEFAULT 0",
        "stock": "INTEGER DEFAULT 0",
        "image_url": "TEXT"
    }

    for col, coltype in wanted.items():
        if col not in existing_cols:
            try:
                c.execute(f"ALTER TABLE fittings ADD COLUMN {col} {coltype}")
                print(f"[DB] Added missing column: {col}")
            except Exception as e:
                print(f"[DB] Failed adding column {col}: {e}")

    c.execute("""
        CREATE TABLE IF NOT EXISTS marketplace_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT UNIQUE NOT NULL,
            customer_name TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            customer_phone TEXT,
            shipping_address TEXT NOT NULL,
            payment_method TEXT NOT NULL,
            status TEXT DEFAULT 'Placed',
            subtotal REAL DEFAULT 0,
            discount_total REAL DEFAULT 0,
            tax_total REAL DEFAULT 0,
            shipping_total REAL DEFAULT 0,
            grand_total REAL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS marketplace_order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            uid TEXT NOT NULL,
            vendor_id TEXT,
            product_name TEXT NOT NULL,
            vendor TEXT,
            unit_price REAL DEFAULT 0,
            quantity INTEGER DEFAULT 1,
            line_total REAL DEFAULT 0,
            FOREIGN KEY(order_id) REFERENCES marketplace_orders(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            discount_type TEXT NOT NULL DEFAULT 'percentage',
            discount_value REAL NOT NULL DEFAULT 0,
            min_order_value REAL DEFAULT 0,
            max_uses INTEGER DEFAULT 100,
            used_count INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS marketplace_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT,
            created_at TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS buyers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            phone TEXT,
            railway_unit TEXT,
            shipping_address TEXT,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

# Run schema check at startup
ensure_table_columns()


def init_extended_tables():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS traceability_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            event_type TEXT NOT NULL,
            description TEXT,
            actor TEXT,
            location TEXT,
            order_no TEXT,
            event_time TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS component_inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            inspector_name TEXT,
            inspection_date TEXT NOT NULL,
            status TEXT DEFAULT 'PENDING',
            findings TEXT,
            notes TEXT,
            risk_level TEXT DEFAULT 'Low',
            next_inspection_date TEXT,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS risk_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            risk_score INTEGER DEFAULT 0,
            factors TEXT,
            recommendation TEXT,
            assessed_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS shipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            order_no TEXT NOT NULL,
            vendor_id TEXT,
            courier TEXT DEFAULT 'Indian Railways Logistics',
            tracking_number TEXT,
            status TEXT DEFAULT 'PENDING',
            estimated_delivery TEXT,
            shipped_at TEXT,
            delivered_at TEXT,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            entity_type TEXT,
            entity_id TEXT,
            actor TEXT,
            details TEXT,
            ip_address TEXT,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS inventory_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            change_type TEXT NOT NULL,
            quantity_before INTEGER DEFAULT 0,
            quantity_change INTEGER DEFAULT 0,
            quantity_after INTEGER DEFAULT 0,
            reason TEXT,
            order_no TEXT,
            actor TEXT,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("PRAGMA table_info(fittings)")
    existing = {row[1] for row in c.fetchall()}
    for col, coltype in [
        ("reserved_stock", "INTEGER DEFAULT 0"),
        ("lifecycle_status", "TEXT DEFAULT 'REGISTERED'"),
        ("qr_active", "INTEGER DEFAULT 1"),
    ]:
        if col not in existing:
            c.execute(f"ALTER TABLE fittings ADD COLUMN {col} {coltype}")
    conn.commit()
    conn.close()
    vconn = get_vendor_db_connection()
    vc = vconn.cursor()
    vc.execute("""
        CREATE TABLE IF NOT EXISTS railway_divisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL,
            zone TEXT NOT NULL,
            region TEXT,
            hq_location TEXT,
            status TEXT DEFAULT 'ACTIVE',
            contact_email TEXT,
            created_at TEXT NOT NULL
        )
    """)
    vc.execute("SELECT COUNT(*) FROM railway_divisions")
    if vc.fetchone()[0] == 0:
        from datetime import datetime as _dt
        now = _dt.now().isoformat(timespec='seconds')
        default_divisions = [
            ('Howrah Division','HWH','South Eastern Railway','West Bengal','Howrah','ACTIVE','hwh@ser.railnet.gov.in'),
            ('Kharagpur Division','KGP','South Eastern Railway','West Bengal','Kharagpur','ACTIVE','kgp@ser.railnet.gov.in'),
            ('Adra Division','ADRA','South Eastern Railway','West Bengal','Adra','ACTIVE','adra@ser.railnet.gov.in'),
            ('Chakradharpur Division','CKP','South Eastern Railway','Jharkhand','Chakradharpur','ACTIVE','ckp@ser.railnet.gov.in'),
            ('Mumbai Division','CSTM','Central Railway','Maharashtra','Mumbai','ACTIVE','cstm@cr.railnet.gov.in'),
            ('Pune Division','PUNE','Central Railway','Maharashtra','Pune','ACTIVE','pune@cr.railnet.gov.in'),
            ('Delhi Division','DLI','Northern Railway','Delhi','New Delhi','ACTIVE','dli@nr.railnet.gov.in'),
            ('Ambala Division','UMB','Northern Railway','Haryana','Ambala','ACTIVE','umb@nr.railnet.gov.in'),
            ('Chennai Division','MAS','Southern Railway','Tamil Nadu','Chennai','ACTIVE','mas@sr.railnet.gov.in'),
            ('Vijayawada Division','BZA','South Central Railway','Andhra Pradesh','Vijayawada','ACTIVE','bza@scr.railnet.gov.in'),
        ]
        vc.executemany(
            "INSERT INTO railway_divisions (name,code,zone,region,hq_location,status,contact_email,created_at) VALUES (?,?,?,?,?,?,?,?)",
            [(d[0],d[1],d[2],d[3],d[4],d[5],d[6],now) for d in default_divisions]
        )
    vconn.commit()
    vconn.close()

init_extended_tables()


def record_traceability_event(uid, event_type, description, actor=None, location=None, order_no=None):
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO traceability_events (uid,event_type,description,actor,location,order_no,event_time) VALUES (?,?,?,?,?,?,?)",
            (uid, event_type, description, actor, location, order_no, datetime.now().isoformat(timespec='seconds'))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Traceability] {e}")


def record_audit(action, entity_type=None, entity_id=None, actor=None, details=None):
    try:
        ip = request.remote_addr
    except Exception:
        ip = None
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO audit_log (action,entity_type,entity_id,actor,details,ip_address,created_at) VALUES (?,?,?,?,?,?,?)",
            (action, entity_type, str(entity_id) if entity_id else None, actor, details, ip,
             datetime.now().isoformat(timespec='seconds'))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Audit] {e}")


def record_inventory_change(uid, change_type, qty_before, qty_change, reason=None, order_no=None, actor=None):
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO inventory_history (uid,change_type,quantity_before,quantity_change,quantity_after,reason,order_no,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, change_type, qty_before, qty_change, qty_before + qty_change,
             reason, order_no, actor, datetime.now().isoformat(timespec='seconds'))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Inventory] {e}")


def compute_warranty_status(warranty_end_str):
    if not warranty_end_str:
        return 'UNKNOWN'
    try:
        end = datetime.strptime(warranty_end_str, "%Y-%m-%d").date()
        today = datetime.today().date()
        if end < today:
            return 'EXPIRED'
        if (end - today).days <= 90:
            return 'EXPIRING_SOON'
        return 'ACTIVE'
    except Exception:
        return 'UNKNOWN'


def build_structured_risk(component):
    uid = component.get('uid')
    risk_level = component.get('risk', 'Low')
    warranty_status = compute_warranty_status(component.get('warranty_end'))
    score = 0
    factors = []
    if risk_level == 'High':
        score += 50
        factors.append('High-risk keywords detected in inspection notes')
    elif risk_level == 'Medium':
        score += 25
        factors.append('Medium-risk indicators in notes')
    if warranty_status == 'EXPIRED':
        score += 25
        factors.append('Warranty has expired')
    elif warranty_status == 'EXPIRING_SOON':
        score += 10
        factors.append('Warranty expiring within 90 days')
    inspection_date_str = component.get('inspection_date')
    if inspection_date_str:
        try:
            if datetime.strptime(inspection_date_str, "%Y-%m-%d").date() < datetime.today().date():
                score += 15
                factors.append('Scheduled inspection date has passed')
        except Exception:
            pass
    vendor_risk = component.get('vendor_risk', 'Low')
    if vendor_risk == 'High':
        score += 10
        factors.append('Vendor has high aggregate risk rating')
    elif vendor_risk == 'Medium':
        score += 5
        factors.append('Vendor has medium aggregate risk rating')
    score = min(score, 100)
    if score >= 70:
        final_level = 'CRITICAL'
        recommendation = 'Immediate inspection and removal from service recommended.'
    elif score >= 40:
        final_level = 'High'
        recommendation = 'Schedule inspection within 30 days. Monitor closely.'
    elif score >= 20:
        final_level = 'Medium'
        recommendation = 'Routine inspection recommended within 90 days.'
    else:
        final_level = 'Low'
        recommendation = 'Component is within normal operating parameters.'
    if not factors:
        factors.append('No significant risk indicators detected')
    assessed_at = datetime.now().isoformat(timespec='seconds')
    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM risk_assessments WHERE uid=?", (uid,))
        conn.execute(
            "INSERT INTO risk_assessments (uid,risk_level,risk_score,factors,recommendation,assessed_at) VALUES (?,?,?,?,?,?)",
            (uid, final_level, score, json.dumps(factors), recommendation, assessed_at)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[RiskAssessment] {e}")
    return {'risk_level': final_level, 'risk_score': score, 'factors': factors,
            'recommendation': recommendation, 'assessed_at': assessed_at}


def get_latest_risk_assessment(uid):
    try:
        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM risk_assessments WHERE uid=? ORDER BY assessed_at DESC LIMIT 1", (uid,)
        ).fetchone()
        conn.close()
        if row:
            d = {k: row[k] for k in row.keys()}
            try:
                d['factors'] = json.loads(d.get('factors') or '[]')
            except Exception:
                d['factors'] = []
            return d
    except Exception:
        pass
    return None


def get_component_traceability(uid):
    try:
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT * FROM traceability_events WHERE uid=? ORDER BY event_time ASC", (uid,)
        ).fetchall()
        conn.close()
        return [{k: row[k] for k in row.keys()} for row in rows]
    except Exception:
        return []


def get_component_inspections(uid):
    try:
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT * FROM component_inspections WHERE uid=? ORDER BY inspection_date DESC", (uid,)
        ).fetchall()
        conn.close()
        return [{k: row[k] for k in row.keys()} for row in rows]
    except Exception:
        return []


def get_vendor_meta(vendor_id):
    if not vendor_id:
        return {}
    try:
        conn = get_vendor_db_connection()
        row = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
        conn.close()
        return {k: row[k] for k in row.keys()} if row else {}
    except Exception:
        return {}


def determine_lifecycle_status(component):
    explicit = component.get('lifecycle_status')
    if explicit and explicit not in ('REGISTERED', None, ''):
        return explicit
    try:
        conn = get_db_connection()
        row = conn.execute(
            """SELECT o.status FROM marketplace_order_items i
               JOIN marketplace_orders o ON o.id=i.order_id
               WHERE i.uid=? ORDER BY o.created_at DESC LIMIT 1""",
            (component.get('uid'),)
        ).fetchone()
        conn.close()
        if row:
            return {'Placed':'PURCHASED','Accepted':'PURCHASED','Packed':'PACKED',
                    'Shipped':'SHIPPED','Out for Delivery':'IN_TRANSIT',
                    'Delivered':'DELIVERED','Completed':'DELIVERED'}.get(row[0], 'LISTED')
    except Exception:
        pass
    if parse_money(component.get('price')) > 0 and parse_int(component.get('stock')) > 0:
        return 'LISTED'
    return 'REGISTERED'


def hash_password(password):
    """Hash a password for storing."""
    salt = secrets.token_hex(16)
    return f"{salt}${hashlib.sha256((salt + password).encode()).hexdigest()}"

def verify_password(stored_password, provided_password):
    """Verify a stored password against one provided by user"""
    if not stored_password or '$' not in stored_password:
        return False
    salt, hashed = stored_password.split('$', 1)
    return hashed == hashlib.sha256((salt + provided_password).encode()).hexdigest()

def parse_money(value, default=0.0):
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return default

def parse_int(value, default=0):
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default

def selling_price(product):
    price = parse_money(product.get('price'))
    discount = min(parse_money(product.get('discount')), 100.0)
    return round(price * (1 - discount / 100), 2)

def get_cart():
    cart = session.get('cart', {})
    return {str(uid): parse_int(qty, 1) for uid, qty in cart.items() if parse_int(qty, 0) > 0}

def save_cart(cart):
    session['cart'] = cart
    session.modified = True

def load_cart_items():
    cart = get_cart()
    if not cart:
        return [], {
            "subtotal": 0,
            "discount_total": 0,
            "tax_total": 0,
            "shipping_total": 0,
            "grand_total": 0,
            "item_count": 0,
        }

    placeholders = ",".join("?" for _ in cart)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"SELECT * FROM fittings WHERE uid IN ({placeholders})", tuple(cart.keys()))
    rows = c.fetchall()
    conn.close()

    items = []
    subtotal = 0.0
    discount_total = 0.0
    item_count = 0
    for row in rows:
        product = {key: row[key] for key in row.keys()}
        price = parse_money(product.get('price'))
        sale_price = selling_price(product)
        quantity = min(cart.get(product['uid'], 1), max(parse_int(product.get('stock')), 1))
        line_total = round(sale_price * quantity, 2)
        item_count += quantity
        subtotal += line_total
        discount_total += round((price - sale_price) * quantity, 2)
        product.update({
            "quantity": quantity,
            "sale_price": sale_price,
            "line_total": line_total,
        })
        items.append(product)

    tax_total = round(subtotal * 0.05, 2)
    shipping_total = 0 if subtotal >= 5000 or subtotal == 0 else 149
    totals = {
        "subtotal": round(subtotal, 2),
        "discount_total": round(discount_total, 2),
        "tax_total": tax_total,
        "shipping_total": shipping_total,
        "grand_total": round(subtotal + tax_total + shipping_total, 2),
        "item_count": item_count,
    }
    return items, totals

def marketplace_categories():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT DISTINCT category FROM fittings WHERE COALESCE(category, '') <> '' ORDER BY category")
    categories = [row[0] for row in c.fetchall()]
    conn.close()
    return categories

def product_review_summary(uid):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT AVG(rating), COUNT(*) FROM marketplace_reviews WHERE uid=?", (uid,))
    avg_rating, count = c.fetchone()
    conn.close()
    return {
        "avg": round(avg_rating or 0, 1),
        "count": count or 0,
    }

def vendor_review_summary(vendor_id):
    conn = get_vendor_db_connection()
    c = conn.cursor()
    c.execute("SELECT AVG(rating), COUNT(*) FROM vendor_reviews WHERE vendor_id=?", (vendor_id,))
    avg_rating, count = c.fetchone()
    c.execute("SELECT * FROM vendor_reviews WHERE vendor_id=? ORDER BY created_at DESC LIMIT 8", (vendor_id,))
    reviews = [{key: row[key] for key in row.keys()} for row in c.fetchall()]
    conn.close()
    return {
        "avg": round(avg_rating or 0, 1),
        "count": count or 0,
        "reviews": reviews,
    }

def vendor_revenue_series(vendor_id, vendor_name):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT substr(o.created_at, 1, 7) AS month,
               COALESCE(SUM(i.line_total), 0) AS revenue,
               COALESCE(SUM(i.quantity), 0) AS units
        FROM marketplace_order_items i
        JOIN marketplace_orders o ON o.id = i.order_id
        WHERE i.vendor_id=? OR i.vendor=?
        GROUP BY substr(o.created_at, 1, 7)
        ORDER BY month ASC
        LIMIT 12
    """, (str(vendor_id), vendor_name))
    rows = [{key: row[key] for key in row.keys()} for row in c.fetchall()]
    conn.close()

    if not rows:
        return []

    max_revenue = max(parse_money(row.get("revenue")) for row in rows) or 1
    for row in rows:
        row["revenue"] = round(parse_money(row.get("revenue")), 2)
        row["units"] = parse_int(row.get("units"))
        row["bar_pct"] = max(round((row["revenue"] / max_revenue) * 100), 4)
    return rows

# === Date calculation helpers ===
def calculate_dates(manufactor_date, supply_date, warranty_end_str, risk):
    today = datetime.today().date()
    base_date = None
    for d in (manufactor_date, supply_date):
        if d:
            try:
                base_date = datetime.strptime(d, "%Y-%m-%d").date()
                break
            except Exception:
                base_date = None
    if base_date is None:
        base_date = today

    warranty_end = None
    if warranty_end_str:
        try:
            warranty_end = datetime.strptime(warranty_end_str, "%Y-%m-%d").date()
        except Exception:
            warranty_end = None

    if risk == "High":
        inspection_date = base_date + timedelta(days=30)
    elif risk == "Medium":
        inspection_date = base_date + timedelta(days=90)
    else:
        inspection_date = base_date + timedelta(days=180)

    if risk == "High":
        repair_date = today + timedelta(days=60)
    elif risk == "Medium":
        repair_date = today + timedelta(days=120)
    else:
        repair_date = warranty_end if warranty_end else (today + timedelta(days=365))

    if warranty_end:
        if inspection_date > warranty_end:
            inspection_date = warranty_end
        if repair_date > warranty_end:
            repair_date = warranty_end

    return inspection_date.isoformat(), repair_date.isoformat()

def compute_next_inspection(inspection_date_str, repair_date_str, risk):
    today = datetime.today().date()
    if inspection_date_str:
        try:
            dt = datetime.strptime(inspection_date_str, "%Y-%m-%d").date()
            return dt.isoformat()
        except Exception:
            pass

    if repair_date_str:
        try:
            rd = datetime.strptime(repair_date_str, "%Y-%m-%d").date()
            if risk == "High":
                delta = timedelta(days=90)
            elif risk == "Medium":
                delta = timedelta(days=180)
            else:
                delta = timedelta(days=365)
            next_dt = rd + delta
            return next_dt.isoformat()
        except Exception:
            pass

    return "Not scheduled"

# === Vendor Risk Calculation Helper ===
def calculate_vendor_risk(vendor):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM fittings WHERE vendor=? AND risk_flag=1", (vendor,))
    failures = c.fetchone()[0]
    conn.close()
    if failures >= 5:
        return "High"
    elif failures >= 2:
        return "Medium"
    else:
        return "Low"

# === QR Content Generation ===
def generate_qr_content(uid, item_type=None, vendor=None, lot=None, supply_date=None,
                        warranty_end=None, manufactor_date=None, manufactor_number=None,
                        notes=None, risk=None, vendor_risk=None, vendor_email=""):
    """Generate a secure public passport URL as QR content - no sensitive data embedded."""
    base_url = os.environ.get("PUBLIC_BASE_URL", "https://rail-qr-marketplace.vercel.app")
    return f"{base_url}/component/{uid}"

def generate_vendor_qr_content(vendor):
    """Generate a public vendor profile URL as QR content."""
    base_url = os.environ.get("PUBLIC_BASE_URL", "https://rail-qr-marketplace.vercel.app")
    return f"{base_url}/vendor/{vendor.get('id')}"


def generate_qr_image_base64(qr_content):
    """Generate a QR code and return it as a base64-encoded PNG string."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=8,
        border=4,
    )
    qr.add_data(qr_content)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def save_qr_image(uid, qr_content):
    """Generate display + engrave QR images for a component. Returns (display_path, engrave_path)."""
    display_path = os.path.join(qr_dir, f"{uid}_display.png")
    engrave_path = os.path.join(qr_dir, f"{uid}_engrave.png")

    # --- Display QR (coloured, with logo if available) ---
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_content)
        qr.make(fit=True)
        img_display = qr.make_image(fill_color="#1a2744", back_color="white").convert("RGBA")

        # Embed railway logo if available
        if os.path.exists(AI_QR_EMBED_IMAGE):
            try:
                logo = Image.open(AI_QR_EMBED_IMAGE).convert("RGBA")
                qr_w, qr_h = img_display.size
                logo_size = qr_w // 5
                logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
                pos = ((qr_w - logo_size) // 2, (qr_h - logo_size) // 2)
                img_display.paste(logo, pos, logo)
            except Exception:
                pass

        img_display.convert("RGB").save(display_path)
    except Exception as e:
        print(f"[QR Display] {e}")
        # Fallback: plain QR
        qr = qrcode.make(qr_content)
        qr.save(display_path)

    # --- Engrave QR (strict 1-bit B/W) ---
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_content)
        qr.make(fit=True)
        img_engrave = qr.make_image(fill_color="black", back_color="white").convert("L")
        img_engrave = img_engrave.point(lambda p: 0 if p < 128 else 255, "1")
        img_engrave.save(engrave_path)
    except Exception as e:
        print(f"[QR Engrave] {e}")
        import shutil as _sh
        if os.path.exists(display_path):
            _sh.copy(display_path, engrave_path)

    return display_path, engrave_path


def save_vendor_qr_image(vendor_id, qr_content):
    """Save vendor QR image for engraving"""
    qr_path_engrave = os.path.join(vendor_qr_dir, f"vendor_{vendor_id}_engrave.png")
    
    # Generate QR code (simple black/white for engraving)
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_content)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    
    # Convert to 1-bit B/W for engraving
    img = img.convert("L").point(lambda p: 0 if p < 128 else 255, "1")
    img.save(qr_path_engrave)
    
    return qr_path_engrave

# === QR -> G-code functions ===
def qr_to_gcode_final(image_path, laser_power=255, travel_speed=5000, engrave_speed=1500, target_size_mm=25.0):
    """
    Vector-like approach: contour-following. Good for fewer G-lines but may produce complex paths.
    """
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return "G21\nG90\nM5\nG0 X0 Y0\n;(Error: Failed to load image)"
    height, width = img.shape
    scale_factor = target_size_mm / max(width, height)
    _, thresh = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_contour_area = 5
    significant_contours = [cnt for cnt in contours if cv2.contourArea(cnt) > min_contour_area]
    gcode_lines = ["G21", "G90", f"G0 F{travel_speed}", f"G1 F{engrave_speed}", "M3 S0", "G0 X0 Y0"]
    for contour in significant_contours:
        epsilon = 0.002 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)
        if len(approx) < 2:
            continue
        first_point = approx[0][0]
        x_start = round(first_point[0] * scale_factor, 3)
        y_start = round(first_point[1] * scale_factor, 3)
        gcode_lines.append(f"G0 X{x_start} Y{y_start}")
        gcode_lines.append(f"M3 S{laser_power}")
        for point in approx[1:]:
            x = round(point[0][0] * scale_factor, 3)
            y = round(point[0][1] * scale_factor, 3)
            gcode_lines.append(f"G1 X{x} Y{y}")
        gcode_lines.append(f"G1 X{x_start} Y{y_start}")
        gcode_lines.append("M3 S0")
    gcode_lines.append("G0 X0 Y0")
    gcode_lines.append("M5")
    return "\n".join(gcode_lines)

def qr_to_gcode_raster(img_path, laser_power=255, travel_speed=5000,
                       engrave_speed=1500, target_size_mm=20.0):
    """
    Raster engraving: line-by-line (zig-zag) scan producing many lines but simpler control.
    Produces denser G-code appropriate for raster engravers.
    """
    img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"Cannot load image: {img_path}")

    # Binarize (black=0, white=255)
    _, bw = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)

    h, w = bw.shape
    px_per_mm = w / target_size_mm
    if px_per_mm == 0:
        raise ValueError("Invalid scale: px_per_mm == 0")
    mm_per_px = 1.0 / px_per_mm

    gcode = []
    gcode.append("G21 ; mm mode")
    gcode.append("G90 ; absolute positioning")
    gcode.append("M5  ; laser off")
    gcode.append(f"G0 F{travel_speed}")

    # iterate rows, zigzag pattern
    for row in range(h):
        y_mm = round(row * mm_per_px, 3)
        # choose forward/backwards scanning
        if row % 2 == 0:
            col_iter = range(w)
        else:
            col_iter = range(w-1, -1, -1)

        laser_on = False
        for col in col_iter:
            pixel = bw[row, col]
            x_mm = round(col * mm_per_px, 3)
            if pixel == 0:  # black pixel to engrave
                if not laser_on:
                    gcode.append(f"G0 X{x_mm} Y{y_mm} F{travel_speed}")
                    gcode.append(f"M3 S{laser_power}")
                    laser_on = True
                gcode.append(f"G1 X{x_mm} Y{y_mm} F{engrave_speed}")
            else:
                if laser_on:
                    gcode.append("M5")
                    laser_on = False

        if laser_on:
            gcode.append("M5")
            laser_on = False

    gcode.append("M5 ; ensure laser off")
    gcode.append("G0 X0 Y0 ; go home")
    return "\n".join(gcode)

def qr_to_gcode_fallback(image_path, laser_power=255, scale=1.0):
    # Simple horizontal-run fallback scanning
    img = Image.open(image_path).convert("L")
    width, height = img.size
    pixels = img.load()
    gcode_lines = ["G21 ; Set units to mm", "G90 ; Absolute positioning", "M3 S0 ; Laser off at start"]
    for y in range(height):
        x = 0
        while x < width:
            while x < width and pixels[x, y] >= 128:
                x += 1
            if x >= width:
                break
            start_x = x
            while x < width and pixels[x, y] < 128:
                x += 1
            end_x = x - 1
            gx_start = round(start_x * scale, 3)
            gy = round(y * scale, 3)
            gx_end = round(end_x * scale, 3)
            gcode_lines.append(f"G0 X{gx_start} Y{gy}")
            gcode_lines.append(f"M3 S{laser_power}")
            gcode_lines.append(f"G1 X{gx_end} Y{gy}")
            gcode_lines.append("M3 S0")
    gcode_lines.append("M5 ; Laser off at end")
    gcode_lines.append("G0 X0 Y0 ; Return to origin")
    return "\n".join(gcode_lines)

# === Send G-code to ESP32 over WebSocket ===
async def send_gcode_websocket(gcode_text, command_delay=0.02):
    try:
        async with websockets.connect(ESP32_WS) as websocket:
            # Optionally read an initial greeting from ESP32
            try:
                first_msg = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                print(f"ESP32 says: {first_msg}")
            except Exception:
                pass

            lines = [line.strip() for line in gcode_text.splitlines() if line.strip() and not line.lstrip().startswith(';')]
            total = len(lines)
            success_count = 0

            for i, line in enumerate(lines):
                await websocket.send(line)
                try:
                    ack = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    if "ok" in ack.lower() or "ready" in ack.lower():
                        success_count += 1
                    else:
                        print(f"Unexpected ACK: {ack}")
                except asyncio.TimeoutError:
                    # no ack — we still proceed but log
                    print(f"No ACK for: {line[:80]}")
                if i % 100 == 0:
                    print(f"Progress: {i}/{total} lines sent")
                await asyncio.sleep(command_delay)

            rate = (success_count / total) * 100 if total else 100.0
            return rate > 90, f"Sent {success_count}/{total} ({rate:.1f}%)"
    except Exception as e:
        return False, f"WebSocket error: {e}"

def send_gcode_to_esp32_enhanced(gcode_text):
    """Wrapper so Flask can call the async WebSocket sender and returns (success_bool, message)."""
    try:
        return asyncio.run(send_gcode_websocket(gcode_text))
    except Exception as e:
        print(f"[send_gcode_to_esp32_enhanced] Exception: {e}")
        return False, f"Async send failed: {e}"


def vendor_qr_to_gcode_raster(img_path, laser_power=255, travel_speed=5000,
                              engrave_speed=1500, target_size_mm=25.0):
    """
    Raster engraving for vendor QR codes: line-by-line (zig-zag) scan.
    """
    img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"Cannot load image: {img_path}")

    # Binarize (black=0, white=255)
    _, bw = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)

    h, w = bw.shape
    px_per_mm = w / target_size_mm
    if px_per_mm == 0:
        raise ValueError("Invalid scale: px_per_mm == 0")
    mm_per_px = 1.0 / px_per_mm

    gcode = []
    gcode.append("G21 ; mm mode")
    gcode.append("G90 ; absolute positioning")
    gcode.append("M5  ; laser off")
    gcode.append(f"G0 F{travel_speed}")

    # iterate rows, zigzag pattern
    for row in range(h):
        y_mm = round(row * mm_per_px, 3)
        # choose forward/backwards scanning
        if row % 2 == 0:
            col_iter = range(w)
        else:
            col_iter = range(w-1, -1, -1)

        laser_on = False
        for col in col_iter:
            pixel = bw[row, col]
            x_mm = round(col * mm_per_px, 3)
            if pixel == 0:  # black pixel to engrave
                if not laser_on:
                    gcode.append(f"G0 X{x_mm} Y{y_mm} F{travel_speed}")
                    gcode.append(f"M3 S{laser_power}")
                    laser_on = True
                gcode.append(f"G1 X{x_mm} Y{y_mm} F{engrave_speed}")
            else:
                if laser_on:
                    gcode.append("M5")
                    laser_on = False

        if laser_on:
            gcode.append("M5")
            laser_on = False

    gcode.append("M5 ; ensure laser off")
    gcode.append("G0 X0 Y0 ; go home")
    return "\n".join(gcode)

def vendor_qr_to_gcode_vector(image_path, laser_power=255, travel_speed=5000, 
                              engrave_speed=1500, target_size_mm=25.0):
    """
    Vector-like approach for vendor QR codes: contour-following.
    """
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return "G21\nG90\nM5\nG0 X0 Y0\n;(Error: Failed to load image)"
    height, width = img.shape
    scale_factor = target_size_mm / max(width, height)
    _, thresh = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_contour_area = 5
    significant_contours = [cnt for cnt in contours if cv2.contourArea(cnt) > min_contour_area]
    gcode_lines = ["G21", "G90", f"G0 F{travel_speed}", f"G1 F{engrave_speed}", "M3 S0", "G0 X0 Y0"]
    for contour in significant_contours:
        epsilon = 0.002 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)
        if len(approx) < 2:
            continue
        first_point = approx[0][0]
        x_start = round(first_point[0] * scale_factor, 3)
        y_start = round(first_point[1] * scale_factor, 3)
        gcode_lines.append(f"G0 X{x_start} Y{y_start}")
        gcode_lines.append(f"M3 S{laser_power}")
        for point in approx[1:]:
            x = round(point[0][0] * scale_factor, 3)
            y = round(point[0][1] * scale_factor, 3)
            gcode_lines.append(f"G1 X{x} Y{y}")
        gcode_lines.append(f"G1 X{x_start} Y{y_start}")
        gcode_lines.append("M3 S0")
    gcode_lines.append("G0 X0 Y0")
    gcode_lines.append("M5")
    return "\n".join(gcode_lines)

@app.route('/buyer/register', methods=['GET', 'POST'])
def buyer_register():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        phone = request.form.get('phone', '').strip()
        railway_unit = request.form.get('railway_unit', '').strip()
        shipping_address = request.form.get('shipping_address', '').strip()

        if not full_name or not email or not password:
            return render_template('buyer_registration.html', error="Name, email, and password are required.")

        conn = None
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute(
                """INSERT INTO buyers
                   (full_name, email, password, phone, railway_unit, shipping_address, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    full_name, email, hash_password(password), phone, railway_unit,
                    shipping_address, datetime.now().isoformat(timespec='seconds')
                )
            )
            conn.commit()
            buyer_id = c.lastrowid
            session['buyer_id'] = buyer_id
            session['buyer_name'] = full_name
            return redirect(url_for('shop'))
        except sqlite3.IntegrityError:
            return render_template('buyer_registration.html', error="Email already registered.")
        finally:
            if conn:
                conn.close()

    return render_template('buyer_registration.html')

@app.route('/buyer/login', methods=['GET', 'POST'])
def buyer_login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        conn = get_db_connection()
        row = conn.execute("SELECT * FROM buyers WHERE email=?", (email,)).fetchone()
        conn.close()

        if row and verify_password(row['password'], password):
            session['buyer_id'] = row['id']
            session['buyer_name'] = row['full_name']
            from urllib.parse import urlparse
            next_url = request.args.get('next') or request.form.get('next')
            if next_url and urlparse(next_url).netloc:
                next_url = None
            return redirect(next_url or url_for('buyer_account'))

        return render_template('buyer_login.html', error="Invalid credentials")

    return render_template('buyer_login.html')

@app.route('/buyer/logout')
def buyer_logout():
    session.pop('buyer_id', None)
    session.pop('buyer_name', None)
    return redirect(url_for('landing'))

@app.route('/buyer/account')
def buyer_account():
    buyer = current_buyer()
    if not buyer:
        return redirect(url_for('buyer_login', next=url_for('buyer_account')))

    conn = get_db_connection()
    rows = conn.execute(
        """SELECT order_no, customer_name, customer_email, status, grand_total, created_at
           FROM marketplace_orders
           WHERE lower(customer_email)=?
           ORDER BY created_at DESC""",
        (buyer['email'].lower(),)
    ).fetchall()
    conn.close()
    orders = [{k: row[k] for k in row.keys()} for row in rows]
    return render_template('buyer_account.html', buyer=buyer, orders=orders)

@app.route('/vendor/login', methods=['GET', 'POST'])
def vendor_login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        conn = get_vendor_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM vendors WHERE email=?", (email,))
        vendor = c.fetchone()
        conn.close()
        
        if vendor:
            # Convert sqlite3.Row to dictionary properly
            vendor_dict = {key: vendor[key] for key in vendor.keys()}
            
            if verify_password(vendor_dict['password'], password):
                # Set session variables
                session['vendor_id'] = vendor_dict['id']
                session['vendor_name'] = vendor_dict['company_name']
                from urllib.parse import urlparse
                next_url = request.args.get('next') or request.form.get('next')
                if next_url and urlparse(next_url).netloc:
                    next_url = None
                return redirect(next_url or url_for('vendor_dashboard'))
        
        return render_template('vendor_login.html', error="Invalid credentials")
    
    return render_template('vendor_login.html')

@app.route('/vendor/register', methods=['GET', 'POST'])
def vendor_register():
    if request.method == 'POST':
        company_name = request.form['company_name']
        contact_person = request.form['contact_person']
        email = request.form['email']
        password = request.form['password']
        phone = request.form.get('phone', '')
        address = request.form.get('address', '')
        railway_zone = request.form.get('railway_zone', 'South Eastern Railway').strip() or 'South Eastern Railway'
        railway_division = request.form.get('railway_division', 'HWH Division').strip() or 'HWH Division'
        supply_region = request.form.get('supply_region', 'West Bengal, India').strip() or 'West Bengal, India'
        
        hashed_pw = hash_password(password)
        registration_date = datetime.now().strftime("%Y-%m-%d")
        
        try:
            conn = sqlite3.connect(VENDOR_DB)
            c = conn.cursor()
            c.execute('''INSERT INTO vendors 
                        (company_name, contact_person, email, password, phone, address,
                         railway_zone, railway_division, supply_region, registration_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (company_name, contact_person, email, hashed_pw, phone, address,
                      railway_zone, railway_division, supply_region, registration_date))
            conn.commit()
            conn.close()
            
            return redirect(url_for('vendor_login'))
        except sqlite3.IntegrityError:
            return render_template('vendor_registration.html', error="Email already registered")
    
    return render_template('vendor_registration.html')

@app.route('/vendor/dashboard')
def vendor_dashboard():
    if 'vendor_id' not in session:
        return redirect(url_for('vendor_login'))
    
    vendor_id = session['vendor_id']
    
    # Get vendor details
    conn = get_vendor_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,))
    vendor = c.fetchone()
    conn.close()
    
    if not vendor:
        return redirect(url_for('vendor_logout'))
    
    # Convert to dictionary properly
    vendor_dict = {key: vendor[key] for key in vendor.keys()}
    
    # Get vendor's products from fittings database
    conn_fittings = get_db_connection()
    c_fittings = conn_fittings.cursor()
    c_fittings.execute("SELECT * FROM fittings WHERE vendor_id=?", (vendor_id,))
    products = [{key: row[key] for key in row.keys()} for row in c_fittings.fetchall()]
    # Real total order count (not capped at 25)
    c_fittings.execute("""
        SELECT COUNT(DISTINCT o.id)
        FROM marketplace_order_items i
        JOIN marketplace_orders o ON o.id = i.order_id
        WHERE i.vendor_id=? OR i.vendor=?
    """, (str(vendor_id), vendor_dict['company_name']))
    total_order_count = c_fittings.fetchone()[0]

    # Real vendor revenue (non-cancelled orders only)
    c_fittings.execute("""
        SELECT COALESCE(SUM(i.line_total), 0)
        FROM marketplace_order_items i
        JOIN marketplace_orders o ON o.id = i.order_id
        WHERE (i.vendor_id=? OR i.vendor=?) AND o.status NOT IN ('Cancelled')
    """, (str(vendor_id), vendor_dict['company_name']))
    total_revenue = round(c_fittings.fetchone()[0], 2)

    c_fittings.execute("""
        SELECT o.order_no, o.customer_name, o.status, o.created_at,
               i.uid, i.product_name, i.quantity, i.line_total
        FROM marketplace_order_items i
        JOIN marketplace_orders o ON o.id = i.order_id
        WHERE i.vendor_id=? OR i.vendor=?
        ORDER BY o.created_at DESC
        LIMIT 25
    """, (str(vendor_id), vendor_dict['company_name']))
    orders = [{key: row[key] for key in row.keys()} for row in c_fittings.fetchall()]
    conn_fittings.close()
    review_data = vendor_review_summary(vendor_id)
    revenue_series = vendor_revenue_series(vendor_id, vendor_dict['company_name'])
    
    # Generate vendor QR content
    vendor_qr_content = generate_vendor_qr_content(vendor_dict)
    vendor_qr_b64 = generate_qr_image_base64(vendor_qr_content)
    
    return render_template('vendor_dashboard.html', 
                          vendor=vendor_dict, 
                          products=products,
                          orders=orders,
                          total_order_count=total_order_count,
                          total_revenue=total_revenue,
                          review_data=review_data,
                          revenue_series=revenue_series,
                          vendor_qr_code=vendor_qr_b64)

@app.route('/vendor/qr/<vendor_id>')
def download_vendor_qr(vendor_id):
    try:
        vid = int(vendor_id)
    except (ValueError, TypeError):
        return redirect(url_for('vendor_login'))
    if 'vendor_id' not in session or session['vendor_id'] != vid:
        return redirect(url_for('vendor_login'))
    
    conn = get_vendor_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,))
    vendor_row = c.fetchone()
    conn.close()
    
    if not vendor_row:
        return "Vendor not found", 404
    vendor = {key: vendor_row[key] for key in vendor_row.keys()}
    
    vendor_qr_content = generate_vendor_qr_content(vendor)
    qr_path = save_vendor_qr_image(vendor_id, vendor_qr_content)
    
    return send_file(qr_path, as_attachment=True, download_name=f"vendor_{vendor_id}_qr.png")

@app.route('/vendor/<vendor_id>')
def vendor_details(vendor_id):
    conn = get_vendor_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,))
    vendor = c.fetchone()
    conn.close()
    
    if not vendor:
        return "Vendor not found", 404
    
    # Convert to dictionary properly
    vendor_dict = {key: vendor[key] for key in vendor.keys()}
    
    # Get vendor's products
    conn_fittings = get_db_connection()
    c_fittings = conn_fittings.cursor()
    c_fittings.execute("SELECT * FROM fittings WHERE vendor_id=?", (vendor_id,))
    products = [{key: row[key] for key in row.keys()} for row in c_fittings.fetchall()]
    conn_fittings.close()
    review_data = vendor_review_summary(vendor_id)
    revenue_series = vendor_revenue_series(vendor_id, vendor_dict['company_name'])
    
    return render_template(
        'vendor_details.html',
        vendor=vendor_dict,
        products=products,
        review_data=review_data,
        revenue_series=revenue_series
    )

@app.route('/vendor/<int:vendor_id>/reviews', methods=['POST'])
def add_vendor_review(vendor_id):
    reviewer_name = request.form.get('reviewer_name', '').strip() or 'Railway Procurement Team'
    railway_unit = request.form.get('railway_unit', '').strip() or 'HWH Division'
    comment = request.form.get('comment', '').strip()
    rating = min(max(parse_int(request.form.get('rating'), 5), 1), 5)

    conn = get_vendor_db_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM vendors WHERE id=?", (vendor_id,))
    if not c.fetchone():
        conn.close()
        return "Vendor not found", 404
    c.execute("""
        INSERT INTO vendor_reviews (vendor_id, reviewer_name, railway_unit, rating, comment, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (vendor_id, reviewer_name, railway_unit, rating, comment, datetime.now().isoformat(timespec='seconds')))
    conn.commit()
    conn.close()
    return redirect(url_for('vendor_details', vendor_id=vendor_id))

@app.route('/vendor/gcode/<int:vendor_id>')
def download_vendor_gcode(vendor_id):
    """Download vendor QR G-code file (no login required)"""
    
    # Get vendor details
    conn = get_vendor_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,))
    vendor = c.fetchone()
    
    if not vendor:
        conn.close()
        return "Vendor not found", 404

    # Get column names from cursor description
    columns = [col[0] for col in c.description]

    # Build dict safely regardless of tuple or sqlite3.Row
    vendor_dict = {col: vendor[idx] for idx, col in enumerate(columns)}
    conn.close()

    # Generate QR content + image
    try:
        vendor_qr_content = generate_vendor_qr_content(vendor_dict)
        qr_path = save_vendor_qr_image(vendor_id, vendor_qr_content)
    except Exception as e:
        return f"QR generation failed: {e}", 500

    try:
        gcode_text = vendor_qr_to_gcode_raster(
            qr_path,
            laser_power=255,
            travel_speed=5000,
            engrave_speed=1500,
            target_size_mm=25.0
        )
    except Exception as e:
        return f"G-code generation failed: {e}", 500

    # Serve file
    mem_file = io.BytesIO()
    mem_file.write(gcode_text.encode('utf-8'))
    mem_file.seek(0)

    return send_file(
        mem_file,
        as_attachment=True,
        download_name=f"vendor_{vendor_id}_engrave.gcode",
        mimetype='text/plain'
    )




# === Flask routes (main app) ===
@app.route('/')
def landing():
    return render_template('landing.html')


@app.route('/entry', methods=['GET', 'POST'])
def index():
    error = None
    
    # Get all vendors for the dropdown using the proper connection function
    conn = get_vendor_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, company_name FROM vendors ORDER BY company_name")
    vendor_rows = c.fetchall()
    conn.close()
    
    # Convert sqlite3.Row objects to dictionaries
    vendors = [{key: row[key] for key in row.keys()} for row in vendor_rows] if vendor_rows else []
    
    if request.method == 'POST':
        # Require vendor login before registering a part
        if 'vendor_id' not in session:
            return redirect(url_for('vendor_login', next=url_for('index')))
        # Ensure submitted vendor_id matches the logged-in vendor
        submitted_vid = request.form.get('vendor_id', '')
        if submitted_vid and str(submitted_vid) != str(session['vendor_id']):
            error = 'Vendor mismatch — you can only register parts under your own account.'
            return render_template('index.html', error=error, request=request, vendors=vendors)
        uid = request.form['uid']
        item_type = request.form['item_type']
        vendor = request.form['vendor']
        vendor_id = request.form.get('vendor_id', '')
        lot = request.form['lot']
        supply_date = request.form['supply_date']
        warranty_end = request.form['warranty_end']
        manufactor_date = request.form.get('manufactor_date', '')
        manufactor_number = request.form.get('manufactor_number', '')
        notes = request.form.get('notes', '')
        vendor_email = request.form.get('vendor_email','')
        category = request.form.get('category', 'Rail Components').strip() or 'Rail Components'
        price = parse_money(request.form.get('price'))
        discount = min(parse_money(request.form.get('discount')), 100.0)
        stock = parse_int(request.form.get('stock'))

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM fittings WHERE uid=?", (uid,))
        if c.fetchone():
            conn.close()
            error = "UID already exists!"
            return render_template('index.html', error=error, request=request, vendors=vendors)

        inspection_date = None
        repair_date = None
        risk_level = "Low"
        vendor_risk = "Low"

        try:
            payload = {
                "uid": uid, "item_type": item_type, "vendor": vendor, "lot": lot,
                "supply_date": supply_date, "warranty_end": warranty_end, "notes": notes
            }
            risk_level = get_risk_level(payload)
            vendor_risk = calculate_vendor_risk(vendor)
        except Exception as e:
            print(f"[Risk Calculation] Exception: {e}")

        try:
            inspection_date, repair_date = calculate_dates(manufactor_date, supply_date, warranty_end, risk_level)
        except Exception as e:
            print(f"[Date Calculation] Exception: {e}")
            inspection_date = supply_date or datetime.today().strftime("%Y-%m-%d")
            repair_date = warranty_end or datetime.today().strftime("%Y-%m-%d")

        qr_content = generate_qr_content(
            uid, item_type, vendor, lot, supply_date, warranty_end,
            manufactor_date, manufactor_number, notes, risk_level, vendor_risk,vendor_email
        )

        # returns (display_path, engrave_path)
        qr_display_path, qr_engrave_path = save_qr_image(uid, qr_content)

        try:
            vendor_id_db = int(vendor_id) if vendor_id else None
            c.execute("""INSERT INTO fittings 
                (uid, item_type, vendor, vendor_id, lot, supply_date, warranty, warranty_end, 
                 manufactor_date, manufactor_number, notes, udm_synced, tms_synced, 
                 risk_flag, risk, vendor_risk, vendor_email, inspection_date, repair_date,
                 category, price, discount, stock)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uid, item_type, vendor, vendor_id_db, lot, supply_date, supply_date, warranty_end,
                 manufactor_date, manufactor_number, notes,
                 1 if risk_level == "High" else 0, risk_level, vendor_risk, vendor_email,
                 inspection_date, repair_date, category, price, discount, stock)
            )
            conn.commit()
        except ValueError:
            # Handle case where vendor_id is not a valid integer
            c.execute("""INSERT INTO fittings 
                (uid, item_type, vendor, vendor_id, lot, supply_date, warranty, warranty_end, 
                 manufactor_date, manufactor_number, notes, udm_synced, tms_synced, 
                 risk_flag, risk, vendor_risk, vendor_email, inspection_date, repair_date,
                 category, price, discount, stock)
                VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uid, item_type, vendor, lot, supply_date, supply_date, warranty_end,
                 manufactor_date, manufactor_number, notes,
                 1 if risk_level == "High" else 0, risk_level, vendor_risk, vendor_email,
                 inspection_date, repair_date, category, price, discount, stock)
            )
            conn.commit()
        except Exception as e:
            print(f"[DB Insert] Exception: {e}")
            conn.close()
            error = "Database insert failed."
            return render_template('index.html', error=error, request=request, vendors=vendors)
        conn.close()

        try:
            update_all_risks()
        except Exception as e:
            print(f"[Global Risk Update] Exception: {e}")

        # push to remote systems (best-effort)
        try:
            payload["repair_date"] = repair_date
            payload["inspection_date"] = inspection_date
            payload["risk"] = risk_level
            payload["vendor_risk"] = vendor_risk
            payload["vendor_email"] = vendor_email
            if push_to_udm(payload):
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("UPDATE fittings SET udm_synced=1 WHERE uid=?", (uid,))
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"[UDM Push] Exception: {e}")

        try:
            if push_to_tms(payload):
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("UPDATE fittings SET tms_synced=1 WHERE uid=?", (uid,))
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"[TMS Push] Exception: {e}")

        try:
            post_register_hooks(uid, item_type, vendor, risk_level,
                                vendor_name=session.get('vendor_name'))
        except Exception as _e:
            print(f'[Hooks] {_e}')

        return redirect(url_for('view_record', uid=uid))

    return render_template('index.html', error=error, request=request, vendors=vendors)

@app.route('/vendor/logout')
def vendor_logout():
    """Log out the vendor by clearing the session"""
    session.clear()
    return redirect(url_for('vendor_login'))

@app.route('/all')
def view_all():
    sort_by = request.args.get('sort_by', 'uid')
    valid_columns = ['uid', 'lot', 'supply_date', 'warranty_end', 'manufactor_date', 
                     'manufactor_number', 'vendor', 'risk', 'item_type', 'vendor_risk',
                     'category', 'price', 'stock']
    if sort_by not in valid_columns:
        sort_by = 'uid'
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"SELECT * FROM fittings ORDER BY {sort_by} ASC")
    rows = c.fetchall()
    conn.close()

    data = []
    for row in rows:
        # Convert sqlite3.Row to dictionary properly
        row_dict = {key: row[key] for key in row.keys()}
        row_dict['next_inspection'] = compute_next_inspection(
            row_dict.get('inspection_date'), 
            row_dict.get('repair_date'), 
            row_dict.get('risk')
        )
        data.append(row_dict)

    return render_template('all.html', rows=data, sort_by=sort_by)

@app.route('/products/<uid>/marketplace', methods=['POST'])
def update_marketplace_settings(uid):
    if 'vendor_id' not in session:
        return redirect(url_for('vendor_login', next=request.path))
    category = request.form.get('category', 'Rail Components').strip() or 'Rail Components'
    price = parse_money(request.form.get('price'))
    discount = min(parse_money(request.form.get('discount')), 100.0)
    stock = parse_int(request.form.get('stock'))

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT vendor_id FROM fittings WHERE uid=?", (uid,))
    row = c.fetchone()
    if not row:
        conn.close()
        return "Product not found", 404

    owner_id = row['vendor_id']
    if session.get('vendor_id') and owner_id and str(session['vendor_id']) != str(owner_id):
        conn.close()
        return "You cannot edit another seller's marketplace settings.", 403

    c.execute("""
        UPDATE fittings
        SET category=?, price=?, discount=?, stock=?
        WHERE uid=?
    """, (category, price, discount, stock, uid))
    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for('view_record', uid=uid, msg="Marketplace settings updated."))

@app.route('/shop')
def shop():
    query = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip()
    sort_by = request.args.get('sort', 'latest')

    where = ["COALESCE(price, 0) > 0", "COALESCE(stock, 0) > 0"]
    params = []
    if query:
        where.append("(uid LIKE ? OR item_type LIKE ? OR vendor LIKE ? OR category LIKE ?)")
        params.extend([f"%{query}%"] * 4)
    if category:
        where.append("category = ?")
        params.append(category)

    sort_options = {
        "price_low": "price ASC",
        "price_high": "price DESC",
        "stock": "stock DESC",
        "risk": "risk ASC",
        "latest": "supply_date DESC, uid DESC",
    }
    order_by = sort_options.get(sort_by, sort_options["latest"])

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"SELECT * FROM fittings WHERE {' AND '.join(where)} ORDER BY {order_by}", params)
    products = []
    for row in c.fetchall():
        product = {key: row[key] for key in row.keys()}
        product['sale_price'] = selling_price(product)
        product['review_summary'] = product_review_summary(product['uid'])
        product['vendor_review_summary'] = {"avg": 0, "count": 0}
        if product.get('vendor_id'):
            product['vendor_review_summary'] = vendor_review_summary(product['vendor_id'])
        products.append(product)
    conn.close()

    conn_v = get_vendor_db_connection()
    vendor_lookup = {
        str(row['id']): {key: row[key] for key in row.keys()}
        for row in conn_v.execute("SELECT id, railway_zone, railway_division, supply_region FROM vendors").fetchall()
    }
    conn_v.close()
    for product in products:
        vendor_meta = vendor_lookup.get(str(product.get('vendor_id')), {})
        product['railway_zone'] = vendor_meta.get('railway_zone', 'South Eastern Railway')
        product['railway_division'] = vendor_meta.get('railway_division', 'HWH Division')
        product['supply_region'] = vendor_meta.get('supply_region', 'West Bengal, India')

    return render_template(
        'shop.html',
        products=products,
        categories=marketplace_categories(),
        query=query,
        category=category,
        sort_by=sort_by,
        cart_count=sum(get_cart().values())
    )

@app.route('/cart')
def cart():
    items, totals = load_cart_items()
    return render_template('cart.html', items=items, totals=totals)

@app.route('/cart/add/<uid>', methods=['POST'])
def add_to_cart(uid):
    quantity = parse_int(request.form.get('quantity'), 1) or 1
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT stock FROM fittings WHERE uid=?", (uid,))
    row = c.fetchone()
    conn.close()
    if not row:
        return "Product not found", 404

    stock = parse_int(row['stock'])
    if stock <= 0:
        return redirect(url_for('shop'))

    cart_data = get_cart()
    cart_data[uid] = min(cart_data.get(uid, 0) + quantity, stock)
    save_cart(cart_data)
    return redirect(request.referrer or url_for('cart'))

@app.route('/cart/update', methods=['POST'])
def update_cart():
    cart_data = get_cart()
    for uid, quantity in request.form.items():
        if not uid.startswith('quantity_'):
            continue
        product_uid = uid.replace('quantity_', '', 1)
        next_qty = parse_int(quantity)
        if next_qty <= 0:
            cart_data.pop(product_uid, None)
        else:
            cart_data[product_uid] = next_qty
    save_cart(cart_data)
    return redirect(url_for('cart'))

@app.route('/cart/remove/<uid>', methods=['POST'])
def remove_from_cart(uid):
    cart_data = get_cart()
    cart_data.pop(uid, None)
    save_cart(cart_data)
    return redirect(url_for('cart'))

@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    items, totals = load_cart_items()
    if not items:
        return redirect(url_for('shop'))

    buyer = current_buyer()

    if request.method == 'POST':
        customer_name = request.form.get('customer_name', '').strip() or (buyer or {}).get('full_name', '')
        customer_email = request.form.get('customer_email', '').strip().lower() or (buyer or {}).get('email', '')
        customer_phone = request.form.get('customer_phone', '').strip() or (buyer or {}).get('phone', '')
        shipping_address = request.form.get('shipping_address', '').strip() or (buyer or {}).get('shipping_address', '')
        payment_method = request.form.get('payment_method', 'COD').strip()
        coupon_code = request.form.get('coupon_code', '').strip().upper()

        if not customer_name or not customer_email or not shipping_address:
            return render_template('checkout.html', items=items, totals=totals, buyer=buyer,
                                   error="Name, email, and shipping address are required.")

        # Server-side coupon validation
        coupon_discount = 0.0
        coupon_id_to_update = None
        if coupon_code:
            conn_c = get_db_connection()
            crow = conn_c.execute("SELECT * FROM coupons WHERE code=? AND active=1", (coupon_code,)).fetchone()
            if crow:
                cpn = {k: crow[k] for k in crow.keys()}
                if cpn['used_count'] < cpn['max_uses'] and totals['subtotal'] >= cpn['min_order_value']:
                    if cpn['discount_type'] == 'percentage':
                        coupon_discount = round(totals['subtotal'] * cpn['discount_value'] / 100, 2)
                    else:
                        coupon_discount = min(cpn['discount_value'], totals['subtotal'])
                    coupon_id_to_update = cpn['id']
            conn_c.close()

        # Recalculate totals with coupon
        adjusted_subtotal = totals['subtotal'] - coupon_discount
        adjusted_grand = round(adjusted_subtotal + totals['tax_total'] + totals['shipping_total'], 2)
        totals['discount_total'] = round(totals['discount_total'] + coupon_discount, 2)
        totals['grand_total'] = adjusted_grand

        conn = get_db_connection()
        try:
            c = conn.cursor()
            # Atomic stock check-and-reserve: use UPDATE with WHERE stock >= qty
            # to prevent overselling under concurrent requests
            for item in items:
                c.execute(
                    "UPDATE fittings SET stock = stock - ? WHERE uid=? AND stock >= ?",
                    (item['quantity'], item['uid'], item['quantity'])
                )
                if c.rowcount == 0:
                    conn.rollback()
                    conn.close()
                    return render_template('checkout.html', items=items, totals=totals, buyer=buyer,
                                           error=f"{item['item_type']} does not have enough stock.")

            if coupon_id_to_update:
                c.execute("UPDATE coupons SET used_count = used_count + 1 WHERE id=?", (coupon_id_to_update,))
            if buyer:
                c.execute(
                    """UPDATE buyers
                       SET full_name=?, phone=?, shipping_address=?
                       WHERE id=?""",
                    (customer_name, customer_phone, shipping_address, buyer['id'])
                )

            order_no = f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}"
            c.execute("""
                INSERT INTO marketplace_orders
                (order_no, customer_name, customer_email, customer_phone, shipping_address,
                 payment_method, status, subtotal, discount_total, tax_total, shipping_total,
                 grand_total, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'Placed', ?, ?, ?, ?, ?, ?)
            """, (
                order_no, customer_name, customer_email, customer_phone, shipping_address,
                payment_method, totals['subtotal'], totals['discount_total'], totals['tax_total'],
                totals['shipping_total'], totals['grand_total'], datetime.now().isoformat(timespec='seconds')
            ))
            order_id = c.lastrowid

            for item in items:
                c.execute("""
                    INSERT INTO marketplace_order_items
                    (order_id, uid, vendor_id, product_name, vendor, unit_price, quantity, line_total)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order_id, item['uid'], item.get('vendor_id'), item['item_type'], item.get('vendor'),
                    item['sale_price'], item['quantity'], item['line_total']
                ))

            conn.commit()
            remember_recent_invoice(
                {
                    'id': order_id,
                    'order_no': order_no,
                    'customer_name': customer_name,
                    'customer_email': customer_email,
                    'customer_phone': customer_phone,
                    'shipping_address': shipping_address,
                    'payment_method': payment_method,
                    'status': 'Placed',
                    'subtotal': totals['subtotal'],
                    'discount_total': totals['discount_total'],
                    'tax_total': totals['tax_total'],
                    'shipping_total': totals['shipping_total'],
                    'grand_total': totals['grand_total'],
                    'created_at': datetime.now().isoformat(timespec='seconds'),
                },
                [
                    {
                        'uid': item['uid'],
                        'vendor_id': item.get('vendor_id'),
                        'product_name': item['item_type'],
                        'vendor': item.get('vendor'),
                        'unit_price': item['sale_price'],
                        'quantity': item['quantity'],
                        'line_total': item['line_total'],
                    }
                    for item in items
                ]
            )
        except Exception as e:
            conn.rollback()
            conn.close()
            print(f"[Checkout] Exception: {e}")
            return render_template('checkout.html', items=items, totals=totals, buyer=buyer,
                                   error="Checkout failed. Please try again.")
        conn.close()

        save_cart({})
        try:
            # Attach stock_before for inventory history
            for _it in items:
                _it['stock_before'] = parse_int(_it.get('stock', 0)) + _it['quantity']
            post_purchase_hooks(order_no, items, customer_name)
        except Exception as _e:
            print(f'[Hooks] {_e}')
        return redirect(url_for('order_success', order_no=order_no))

    return render_template('checkout.html', items=items, totals=totals, buyer=buyer)

@app.route('/orders/<order_no>')
def order_success(order_no):
    order_data, items = fetch_order_with_items(order_no)
    if not order_data:
        order_data, items = get_recent_invoice(order_no)
    if not order_data:
        return "Order not found", 404
    return render_template('order_success.html', order=order_data, items=items)

@app.route('/orders/id/<int:order_id>')
def order_success_by_id(order_id):
    order_data, items = fetch_order_with_items(order_id)
    if not order_data:
        order_data, items = get_recent_invoice(order_id)
    if not order_data:
        return "Order not found", 404
    return redirect(url_for('order_success', order_no=order_data['order_no']))

@app.route('/track', methods=['GET', 'POST'])
def track_order():
    order = None
    items = []
    shipment = None
    error = None
    if request.method == 'POST':
        order_no = request.form.get('order_no', '').strip()
        email = request.form.get('customer_email', '').strip()
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "SELECT * FROM marketplace_orders WHERE order_no=? AND customer_email=?",
            (order_no, email)
        )
        row = c.fetchone()
        if row:
            order = {key: row[key] for key in row.keys()}
            c.execute("SELECT * FROM marketplace_order_items WHERE order_id=?", (order['id'],))
            items = [{key: item[key] for key in item.keys()} for item in c.fetchall()]
            ship_row = c.execute(
                "SELECT * FROM shipments WHERE order_no=? ORDER BY created_at DESC LIMIT 1",
                (order_no,)
            ).fetchone()
            if ship_row:
                shipment = {key: ship_row[key] for key in ship_row.keys()}
        else:
            error = "No order found for that order number and email."
        conn.close()
    return render_template('track.html', order=order, items=items, shipment=shipment, error=error)

@app.route('/vendor/order/<order_no>/status', methods=['POST'])
def update_order_status(order_no):
    """Delegates to the guarded v2 handler with state machine enforcement."""
    return update_order_status_v2(order_no)

@app.route('/reviews/<uid>', methods=['POST'])
def add_review(uid):
    customer_name = request.form.get('customer_name', '').strip()
    comment = request.form.get('comment', '').strip()
    rating = parse_int(request.form.get('rating'), 5)
    rating = min(max(rating, 1), 5)

    if not customer_name:
        customer_name = "Customer"

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT uid FROM fittings WHERE uid=?", (uid,))
    if not c.fetchone():
        conn.close()
        return "Product not found", 404
    c.execute("""
        INSERT INTO marketplace_reviews (uid, customer_name, rating, comment, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (uid, customer_name, rating, comment, datetime.now().isoformat(timespec='seconds')))
    conn.commit()
    conn.close()
    return redirect(url_for('view_record', uid=uid, msg="Review added."))

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session.permanent = True
            session['admin_logged_in'] = True
            from urllib.parse import urlparse
            next_url = request.args.get('next') or request.form.get('next')
            if next_url and urlparse(next_url).netloc:
                next_url = None
            return redirect(next_url or url_for('admin_dashboard'))
        error = 'Invalid password'
    return render_template('admin_login.html', error=error)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))
@app.route('/admin/orders/list')
@admin_required
def admin_orders_list():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT o.order_no, o.customer_name, o.customer_email, o.status,
               o.grand_total, o.created_at,
               GROUP_CONCAT(i.product_name, ', ') AS products
        FROM marketplace_orders o
        LEFT JOIN marketplace_order_items i ON i.order_id = o.id
        GROUP BY o.id ORDER BY o.created_at DESC LIMIT 100
    """)
    orders = [{key: row[key] for key in row.keys()} for row in c.fetchall()]
    conn.close()
    return jsonify(orders)

@app.route('/admin/vendors/list')
@admin_required
def admin_vendors_list():
    conn = get_vendor_db_connection()
    rows = conn.execute(
        "SELECT id, company_name, contact_person, email, phone, railway_zone, "
        "railway_division, registration_date, vendor_risk FROM vendors ORDER BY registration_date DESC"
    ).fetchall()
    conn.close()
    return jsonify([{k: row[k] for k in row.keys()} for row in rows])


@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    c = conn.cursor()

    # --- Core metrics: all derived from the same marketplace_orders table ---
    c.execute("""
        SELECT
            COUNT(*) AS order_count,
            COALESCE(SUM(grand_total), 0) AS revenue,
            COALESCE(SUM(CASE WHEN status NOT IN ('Cancelled') THEN grand_total ELSE 0 END), 0) AS confirmed_revenue
        FROM marketplace_orders
    """)
    row = c.fetchone()
    order_count = row['order_count']
    revenue = row['revenue']
    confirmed_revenue = row['confirmed_revenue']

    c.execute("SELECT COUNT(*) FROM fittings")
    product_count = c.fetchone()[0]

    # Low stock: products with price > 0 (listed) and stock <= 5
    c.execute("SELECT COUNT(*) FROM fittings WHERE COALESCE(stock, 0) <= 5 AND COALESCE(price, 0) > 0")
    low_stock_count = c.fetchone()[0]

    # Status breakdown — same orders as order_count above
    c.execute("SELECT status, COUNT(*) FROM marketplace_orders GROUP BY status ORDER BY status")
    status_counts = c.fetchall()

    # Recent orders — same table, consistent with order_count
    c.execute("""
        SELECT o.order_no, o.customer_name, o.status, o.grand_total, o.created_at
        FROM marketplace_orders o
        ORDER BY o.created_at DESC
        LIMIT 20
    """)
    recent_orders = [{key: row[key] for key in row.keys()} for row in c.fetchall()]

    # Category inventory — all products, consistent with product_count
    c.execute("""
        SELECT COALESCE(category, 'Uncategorized') AS category,
               COUNT(*) AS product_count,
               COALESCE(SUM(stock), 0) AS stock_count
        FROM fittings
        GROUP BY COALESCE(category, 'Uncategorized')
        ORDER BY product_count DESC
        LIMIT 8
    """)
    categories = [{key: row[key] for key in row.keys()} for row in c.fetchall()]

    # Vendor analytics: revenue per vendor from order items
    c.execute("""
        SELECT i.vendor, i.vendor_id,
               COALESCE(SUM(i.line_total), 0) AS revenue,
               COUNT(DISTINCT i.order_id) AS order_count,
               COALESCE(SUM(i.quantity), 0) AS units_sold
        FROM marketplace_order_items i
        JOIN marketplace_orders o ON o.id = i.order_id
        WHERE o.status NOT IN ('Cancelled')
        GROUP BY i.vendor_id, i.vendor
        ORDER BY revenue DESC
        LIMIT 10
    """)
    vendor_analytics = [{key: row[key] for key in row.keys()} for row in c.fetchall()]

    # High-risk component count
    c.execute("SELECT COUNT(*) FROM fittings WHERE risk = 'High'")
    high_risk_count = c.fetchone()[0]

    conn.close()

    conn_v = get_vendor_db_connection()
    seller_count = conn_v.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]

    # Divisions: group vendors by their declared division
    division_rows = conn_v.execute("""
        SELECT railway_zone, railway_division, supply_region, COUNT(*) AS seller_count
        FROM vendors
        GROUP BY railway_zone, railway_division, supply_region
        ORDER BY seller_count DESC
    """).fetchall()
    divisions = [{key: row[key] for key in row.keys()} for row in division_rows]
    conn_v.close()

    return render_template(
        'admin.html',
        order_count=order_count,
        revenue=revenue,
        confirmed_revenue=confirmed_revenue,
        product_count=product_count,
        low_stock_count=low_stock_count,
        seller_count=seller_count,
        status_counts=status_counts,
        recent_orders=recent_orders,
        categories=categories,
        divisions=divisions,
        vendor_analytics=vendor_analytics,
        high_risk_count=high_risk_count,
    )

@app.route('/generated/qrcodes/<path:filename>')
def generated_qrcode(filename):
    safe_name = os.path.basename(filename)
    runtime_file = os.path.join(qr_dir, safe_name)
    bundled_file = os.path.join(BASE_DIR, "static", "qrcodes", safe_name)
    if os.path.exists(runtime_file):
        return send_file(runtime_file)
    if os.path.exists(bundled_file):
        return send_file(bundled_file)

    # On-demand generation: derive UID from filename like "<uid>_display.png"
    # Handles both _display.png and _engrave.png
    uid = None
    for suffix in ('_display.png', '_engrave.png', '.png'):
        if safe_name.endswith(suffix):
            uid = safe_name[:-len(suffix)]
            break
    if uid:
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM fittings WHERE uid=?", (uid,)).fetchone()
        conn.close()
        if row:
            row_dict = {k: row[k] for k in row.keys()}
            qr_content = generate_qr_content(
                row_dict.get('uid'), row_dict.get('item_type'), row_dict.get('vendor'),
                row_dict.get('lot'), row_dict.get('supply_date'), row_dict.get('warranty_end'),
                row_dict.get('manufactor_date', ''), row_dict.get('manufactor_number', ''),
                row_dict.get('notes', ''), row_dict.get('risk', 'Low'),
                row_dict.get('vendor_risk', 'Low'), row_dict.get('vendor_email', '')
            )
            try:
                save_qr_image(uid, qr_content)
                if os.path.exists(runtime_file):
                    return send_file(runtime_file)
            except Exception as e:
                print(f"[QR on-demand] {e}")
    return "QR image not found", 404

@app.route('/generated/vendor_qrcodes/<path:filename>')
def generated_vendor_qrcode(filename):
    safe_name = os.path.basename(filename)
    runtime_file = os.path.join(vendor_qr_dir, safe_name)
    bundled_file = os.path.join(BASE_DIR, "static", "vendor_qrcodes", safe_name)
    if os.path.exists(runtime_file):
        return send_file(runtime_file)
    if os.path.exists(bundled_file):
        return send_file(bundled_file)
    return "Vendor QR image not found", 404


@app.route('/view/<uid>')
def view_record(uid):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM fittings WHERE uid=?", (uid,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        return "Not found", 404
    
    # Convert sqlite3.Row to dictionary properly
    row_dict = {key: row[key] for key in row.keys()}
    c.execute("SELECT * FROM marketplace_reviews WHERE uid=? ORDER BY created_at DESC", (uid,))
    reviews = [{key: review[key] for key in review.keys()} for review in c.fetchall()]
    conn.close()
    
    return render_template(
        'view.html',
        row=row_dict,
        reviews=reviews,
        review_summary=product_review_summary(uid),
        message=request.args.get('msg')
    )

@app.route('/send_gcode/<uid>', methods=['POST'])
def send_gcode(uid):
    """
    send_gcode expects form data optionally containing:
      - method: 'raster' | 'vector' | 'fallback'  (default 'raster')
      - stream_delay: optional float seconds between lines (default 0.02)
    """
    method = request.form.get('method', 'raster').lower()
    try:
        command_delay = float(request.form.get('stream_delay', 0.02))
    except Exception:
        command_delay = 0.02

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM fittings WHERE uid=?", (uid,))
    row = c.fetchone()
    conn.close()
    if not row:
        return f"Fitting with UID {uid} not found.", 404

    row_dict = dict(row)
    qr_content = generate_qr_content(
        row_dict.get('uid'), row_dict.get('item_type'), row_dict.get('vendor'), row_dict.get('lot'),
        row_dict.get('supply_date'), row_dict.get('warranty_end'), row_dict.get('manufactor_date',''),
        row_dict.get('manufactor_number',''), row_dict.get('notes',''),
        row_dict.get('risk','Low'), row_dict.get('vendor_risk','Low'), row_dict.get('vendor_email','')
    )

    # Generate both display + engrave QR; use the engrave one for g-code generation
    _, qr_path_engrave = save_qr_image(uid, qr_content)

    # Choose generator
    try:
        if method == 'vector':
            gcode_text = qr_to_gcode_final(qr_path_engrave, laser_power=255, travel_speed=5000, engrave_speed=1500, target_size_mm=20.0)
            print(f"[Vector] Generated {len(gcode_text.splitlines())} lines of G-code")
        elif method == 'fallback':
            gcode_text = qr_to_gcode_fallback(qr_path_engrave, laser_power=255, scale=0.5)
            print(f"[Fallback] Generated {len(gcode_text.splitlines())} lines of G-code")
        else:  # default raster
            gcode_text = qr_to_gcode_raster(qr_path_engrave, laser_power=255, travel_speed=5000, engrave_speed=1500, target_size_mm=20.0)
            print(f"[Raster] Generated {len(gcode_text.splitlines())} lines of G-code")
    except Exception as e:
        print(f"[G-code generation] Failed: {e}")
        return f"G-code generation failed: {e}", 500

    # Save G-code file
    gcode_path = os.path.join(qr_dir, f"{uid}_engrave.gcode")
    with open(gcode_path, "w") as f:
        f.write(gcode_text)
    print(f"[GCODE] Saved at {gcode_path}")

    # Stream/send to ESP32 via websocket
    success, resp_text = send_gcode_to_esp32_enhanced(gcode_text)
    msg = f"G-code sent successfully! {resp_text}" if success else f"Failed: {resp_text}"
    return redirect(url_for('view_record', uid=uid, msg=msg))

@app.route('/regenerate_qr/<uid>', methods=['POST'])
def regenerate_qr(uid):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM fittings WHERE uid=?", (uid,))
    row = c.fetchone()
    conn.close()
    if not row:
        return f"Fitting with UID {uid} not found.", 404
    row_dict = dict(row)
    qr_content = generate_qr_content(
        row_dict.get('uid'), row_dict.get('item_type'), row_dict.get('vendor'), row_dict.get('lot'),
        row_dict.get('supply_date'), row_dict.get('warranty_end'), row_dict.get('manufactor_date',''),
        row_dict.get('manufactor_number',''), row_dict.get('notes',''),
        row_dict.get('risk','Low'), row_dict.get('vendor_risk','Low'), row_dict.get('vendor_email','')
    )
    save_qr_image(uid, qr_content)
    msg = f"QR regenerated for UID {uid}."
    return redirect(url_for('view_record', uid=uid, msg=msg))

@app.route('/vendor/send_gcode/<vendor_id>', methods=['POST'])
def send_vendor_gcode(vendor_id):
    """
    Send vendor QR G-code to ESP32
    """
    try:
        vid = int(vendor_id)
    except (ValueError, TypeError):
        return redirect(url_for('vendor_login'))
    if 'vendor_id' not in session or session['vendor_id'] != vid:
        return redirect(url_for('vendor_login'))
    
    method = request.form.get('method', 'raster').lower()
    try:
        command_delay = float(request.form.get('stream_delay', 0.02))
    except Exception:
        command_delay = 0.02

    # Get vendor details
    conn = get_vendor_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,))
    vendor = c.fetchone()
    conn.close()
    
    if not vendor:
        return f"Vendor with ID {vendor_id} not found.", 404

    # Convert sqlite3.Row to dictionary properly
    vendor_dict = {key: vendor[key] for key in vendor.keys()}
    
    # Generate vendor QR content and image
    vendor_qr_content = generate_vendor_qr_content(vendor_dict)
    qr_path = save_vendor_qr_image(vendor_id, vendor_qr_content)

    # Choose generator
    try:
        if method == 'vector':
            gcode_text = vendor_qr_to_gcode_vector(qr_path, laser_power=255, travel_speed=5000, engrave_speed=1500, target_size_mm=25.0)
            print(f"[Vector] Generated {len(gcode_text.splitlines())} lines of G-code for vendor QR")
        else:  # default raster
            gcode_text = vendor_qr_to_gcode_raster(qr_path, laser_power=255, travel_speed=5000, engrave_speed=1500, target_size_mm=25.0)
            print(f"[Raster] Generated {len(gcode_text.splitlines())} lines of G-code for vendor QR")
    except Exception as e:
        print(f"[Vendor G-code generation] Failed: {e}")
        return f"Vendor G-code generation failed: {e}", 500

    # Save G-code file
    gcode_path = os.path.join(vendor_gcode_dir, f"vendor_{vendor_id}_engrave.gcode")
    with open(gcode_path, "w") as f:
        f.write(gcode_text)
    print(f"[Vendor GCODE] Saved at {gcode_path}")

    # Stream/send to ESP32 via websocket
    success, resp_text = send_gcode_to_esp32_enhanced(gcode_text)
    msg = f"Vendor G-code sent successfully! {resp_text}" if success else f"Failed: {resp_text}"
    return redirect(url_for('vendor_dashboard', msg=msg))

@app.route('/scan/<uid>', methods=['GET'])
def scan(uid):
    """QR scan redirect — sends to the full digital passport."""
    conn = get_db_connection()
    row = conn.execute("SELECT uid FROM fittings WHERE uid=?", (uid,)).fetchone()
    conn.close()
    if not row:
        return render_template('scan.html', uid=uid, risk='Unknown',
                               vendor_risk='Unknown', inspection_date='N/A',
                               qr_code='', error="Component not found"), 404
    record_audit('QR_SCANNED', 'component', uid, 'public')
    return redirect(url_for('component_passport', uid=uid))


@app.route('/test_qr/<uid>')
def test_qr(uid):
    """Return the display QR image for visual testing."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM fittings WHERE uid=?", (uid,))
    row = c.fetchone()
    conn.close()
    if not row:
        return "UID not found", 404

    row_dict = dict(row)
    qr_content = generate_qr_content(
        row_dict.get('uid'), row_dict.get('item_type'), row_dict.get('vendor'), row_dict.get('lot'),
        row_dict.get('supply_date'), row_dict.get('warranty_end'), row_dict.get('manufactor_date',''),
        row_dict.get('manufactor_number',''), row_dict.get('notes',''),
        row_dict.get('risk','Low'), row_dict.get('vendor_risk','Low'), row_dict.get('vendor_email','')
    )

    display_path, _ = save_qr_image(uid, qr_content)
    return send_file(display_path, mimetype='image/png')


# ── Coupon system ────────────────────────────────────────────────────────────

@app.route('/apply-coupon', methods=['POST'])
def apply_coupon():
    code = (request.get_json() or request.form).get('code', '').strip().upper()
    try:
        order_value = float((request.get_json() or request.form).get('order_value', 0))
    except (TypeError, ValueError):
        order_value = 0

    if not code:
        return jsonify({'valid': False, 'message': 'Enter a coupon code.'})

    conn = get_db_connection()
    row = conn.execute(
        "SELECT * FROM coupons WHERE code=? AND active=1", (code,)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({'valid': False, 'message': 'Invalid or expired coupon.'})

    coupon = {k: row[k] for k in row.keys()}

    if coupon['used_count'] >= coupon['max_uses']:
        return jsonify({'valid': False, 'message': 'Coupon usage limit reached.'})

    if order_value < coupon['min_order_value']:
        return jsonify({
            'valid': False,
            'message': f"Minimum order value ₹{coupon['min_order_value']:.0f} required."
        })

    if coupon['discount_type'] == 'percentage':
        discount = round(order_value * coupon['discount_value'] / 100, 2)
        label = f"{coupon['discount_value']:.0f}% off"
    else:
        discount = min(coupon['discount_value'], order_value)
        label = f"₹{coupon['discount_value']:.0f} off"

    return jsonify({
        'valid': True,
        'code': code,
        'discount': discount,
        'label': label,
        'message': f"Coupon applied: {label}"
    })


@app.route('/admin/coupons', methods=['GET', 'POST'])
@admin_required
def admin_coupons():
    conn = get_db_connection()
    if request.method == 'POST':
        code = request.form.get('code', '').strip().upper()
        discount_type = request.form.get('discount_type', 'percentage')
        discount_value = parse_money(request.form.get('discount_value'))
        min_order_value = parse_money(request.form.get('min_order_value'))
        max_uses = parse_int(request.form.get('max_uses'), 100)
        now = datetime.now().isoformat(timespec='seconds')
        try:
            conn.execute(
                """INSERT INTO coupons (code, discount_type, discount_value, min_order_value, max_uses, used_count, active, created_at)
                   VALUES (?, ?, ?, ?, ?, 0, 1, ?)""",
                (code, discount_type, discount_value, min_order_value, max_uses, now)
            )
            conn.commit()
            record_audit('COUPON_CREATED', 'coupon', code, 'admin', f"type={discount_type} value={discount_value}")
        except Exception as e:
            conn.close()
            return jsonify({'error': str(e)}), 400
        conn.close()
        return redirect(url_for('admin_coupons'))

    rows = conn.execute("SELECT * FROM coupons ORDER BY created_at DESC").fetchall()
    conn.close()
    coupons = [{k: row[k] for k in row.keys()} for row in rows]
    return jsonify(coupons)


@app.route('/admin/coupons/<int:coupon_id>/toggle', methods=['POST'])
@admin_required
def toggle_coupon(coupon_id):
    conn = get_db_connection()
    conn.execute("UPDATE coupons SET active = 1 - active WHERE id=?", (coupon_id,))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('admin_coupons'))


# ── PDF Invoice ───────────────────────────────────────────────────────────────

@app.route('/invoice/<path:order_no>')
def download_invoice(order_no):
    order, items = fetch_order_with_items(order_no)
    if not order:
        order, items = get_recent_invoice(order_no)
    if not order:
        return "Order not found", 404

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=15*mm, bottomMargin=15*mm,
                                leftMargin=20*mm, rightMargin=20*mm)
        styles = getSampleStyleSheet()
        story = []

        # Header
        story.append(Paragraph("<b>GaugeMarket — Railway Supply Chain</b>", styles['Title']))
        story.append(Paragraph("Indian Railways Fittings Department", styles['Normal']))
        story.append(Spacer(1, 6*mm))

        # Invoice meta
        meta = [
            ["Invoice / Order No:", order['order_no']],
            ["Date:", order['created_at'][:10] if order['created_at'] else ''],
            ["Status:", order['status']],
            ["Payment:", order['payment_method']],
        ]
        meta_table = Table(meta, colWidths=[50*mm, 110*mm])
        meta_table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 5*mm))

        # Customer info
        story.append(Paragraph("<b>Bill To</b>", styles['Normal']))
        story.append(Paragraph(order['customer_name'], styles['Normal']))
        story.append(Paragraph(order['customer_email'], styles['Normal']))
        if order.get('customer_phone'):
            story.append(Paragraph(order['customer_phone'], styles['Normal']))
        story.append(Paragraph(order.get('shipping_address', ''), styles['Normal']))
        story.append(Spacer(1, 5*mm))

        # Items table
        table_data = [["#", "Product", "UID", "Seller", "Qty", "Unit Price", "Total"]]
        for i, item in enumerate(items, 1):
            table_data.append([
                str(i),
                item['product_name'],
                item['uid'],
                item.get('vendor', ''),
                str(item['quantity']),
                f"Rs {item['unit_price']:.2f}",
                f"Rs {item['line_total']:.2f}",
            ])

        col_widths = [8*mm, 45*mm, 28*mm, 30*mm, 10*mm, 22*mm, 22*mm]
        items_table = Table(table_data, colWidths=col_widths)
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e3a5f')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#f5f5f5'), colors.white]),
            ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#cccccc')),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('TOPPADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(items_table)
        story.append(Spacer(1, 5*mm))

        # Totals
        totals_data = [
            ["Subtotal", f"Rs {order['subtotal']:.2f}"],
            ["Discount", f"- Rs {order['discount_total']:.2f}"],
            ["GST (5%)", f"Rs {order['tax_total']:.2f}"],
            ["Shipping", f"Rs {order['shipping_total']:.2f}"],
            ["Grand Total", f"Rs {order['grand_total']:.2f}"],
        ]
        totals_table = Table(totals_data, colWidths=[130*mm, 35*mm])
        totals_table.setStyle(TableStyle([
            ('ALIGN', (1,0), (1,-1), 'RIGHT'),
            ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('LINEABOVE', (0,-1), (-1,-1), 1, colors.black),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ]))
        story.append(totals_table)
        story.append(Spacer(1, 8*mm))
        story.append(Paragraph(
            "All components are QR-verified through the Indian Railways supply chain.",
            ParagraphStyle('footer', fontSize=8, textColor=colors.HexColor('#666666'))
        ))

        doc.build(story)
        pdf_bytes = buf.getvalue()
        from flask import Response
        return Response(
            pdf_bytes,
            status=200,
            mimetype='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="invoice_{order_no}.pdf"',
                     'Content-Length': str(len(pdf_bytes))}
        )

    except ImportError:
        # reportlab not available — return plain text invoice
        lines_out = [
            f"INVOICE — {order['order_no']}",
            f"Date: {order['created_at'][:10] if order['created_at'] else ''}",
            f"Customer: {order['customer_name']} <{order['customer_email']}>",
            f"Address: {order['shipping_address']}",
            f"Payment: {order['payment_method']}",
            "",
            f"{'Product':<30} {'Qty':>5} {'Unit':>10} {'Total':>10}",
            "-" * 60,
        ]
        for item in items:
            lines_out.append(
                f"{item['product_name']:<30} {item['quantity']:>5} "
                f"Rs{item['unit_price']:>9.2f} Rs{item['line_total']:>9.2f}"
            )
        lines_out += [
            "-" * 60,
            f"{'Subtotal':>46} Rs{order['subtotal']:>9.2f}",
            f"{'GST (5%)':>46} Rs{order['tax_total']:>9.2f}",
            f"{'Grand Total':>46} Rs{order['grand_total']:>9.2f}",
        ]
        txt_bytes = "\n".join(lines_out).encode()
        from flask import Response
        return Response(
            txt_bytes,
            status=200,
            mimetype='text/plain',
            headers={'Content-Disposition': f'attachment; filename="invoice_{order_no}.txt"',
                     'Content-Length': str(len(txt_bytes))}
        )



# === Background threads ===
def periodic_risk_update():
    while True:
        try:
            update_all_risks()
        except Exception as e:
            print("[Risk Update] Exception:", e)
        time.sleep(3600)

def validate_all_qr_codes():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM fittings")
    rows = c.fetchall()
    for row in rows:
        row_dict = dict(row)
        uid = row_dict.get('uid')
        display, engrave = os.path.join(qr_dir, f"{uid}_display.png"), os.path.join(qr_dir, f"{uid}_engrave.png")
        qr_content = generate_qr_content(
            row_dict.get('uid'), row_dict.get('item_type'), row_dict.get('vendor'), row_dict.get('lot'),
            row_dict.get('supply_date'), row_dict.get('warranty_end'), row_dict.get('manufactor_date',''),
            row_dict.get('manufactor_number',''), row_dict.get('notes',''),
            row_dict.get('risk','Low'), row_dict.get('vendor_risk','Low'), row_dict.get('vendor_email','')
        )
        try:
            if not os.path.exists(display) or not os.path.exists(engrave):
                save_qr_image(uid, qr_content)
                print(f"[QR Validation] Generated QR for UID {uid}")
        except Exception as e:
            print(f"[QR Validation] Error for UID {uid}: {e}")
    conn.close()
    print("[QR Validation] All QR codes checked.")

def retry_pending_sync():
    while True:
        conn = get_db_connection()
        c = conn.cursor()

        # --- Pending UDM Sync ---
        c.execute("SELECT * FROM fittings WHERE udm_synced=0")
        pending_udm = c.fetchall()

        for row in pending_udm:
            r = dict(row)

            # 🔎 Get vendor email from vendors table
            vendor_email = None
            try:
                with sqlite3.connect(VENDOR_DB) as v_conn:
                    v_conn.row_factory = sqlite3.Row
                    vc = v_conn.cursor()
                    vc.execute("SELECT email FROM vendors WHERE id=?", (r.get('vendor_id'),))
                    v_row = vc.fetchone()
                    if v_row:
                        vendor_email = v_row['email']
            except Exception as e:
                print(f"[Vendor Lookup Error] {e}")

            payload = {
                "uid": r.get('uid'),
                "item_type": r.get('item_type'),
                "vendor": r.get('vendor'),
                "email": vendor_email,  # ensure same key for UDM and TMS
                "lot": r.get('lot'),
                "supply_date": r.get('supply_date'),
                "warranty_end": r.get('warranty_end'),
                "manufactor_date": r.get('manufactor_date'),
                "manufactor_number": r.get('manufactor_number'),
                "repair_date": r.get('repair_date'),
                "inspection_date": r.get('inspection_date'),
                "risk": r.get('risk'),
                "vendor_risk": r.get('vendor_risk'),
                "notes": r.get('notes'),
                "vendor_email":r.get('vendor_email')
            }

            try:
                if push_to_udm(payload):
                    c.execute("UPDATE fittings SET udm_synced=1 WHERE uid=?", (r.get('uid'),))
                    conn.commit()
                    print(f"[UDM Retry] UID {r.get('uid')} synced successfully.")
            except Exception as e:
                print(f"[UDM Retry] error pushing {r.get('uid')}: {e}")

        # --- Pending TMS Sync ---
        c.execute("SELECT * FROM fittings WHERE tms_synced=0")
        pending_tms = c.fetchall()

        for row in pending_tms:
            r = dict(row)

            vendor_email = None
            try:
                with sqlite3.connect(VENDOR_DB) as v_conn:
                    v_conn.row_factory = sqlite3.Row
                    vc = v_conn.cursor()
                    vc.execute("SELECT email FROM vendors WHERE id=?", (r.get('vendor_id'),))
                    v_row = vc.fetchone()
                    if v_row:
                        vendor_email = v_row['email']
            except Exception as e:
                print(f"[Vendor Lookup Error] {e}")

            payload = {
                "uid": r.get('uid'),
                "item_type": r.get('item_type'),
                "vendor": r.get('vendor'),
                "email": vendor_email,
                "lot": r.get('lot'),
                "supply_date": r.get('supply_date'),
                "warranty_end": r.get('warranty_end'),
                "manufactor_date": r.get('manufactor_date'),
                "manufactor_number": r.get('manufactor_number'),
                "repair_date": r.get('repair_date'),
                "inspection_date": r.get('inspection_date'),
                "risk": r.get('risk'),
                "vendor_risk": r.get('vendor_risk'),
                "notes": r.get('notes'),
                "vendor_email":r.get('vendor_email')

            }

            try:
                if push_to_tms(payload):
                    c.execute("UPDATE fittings SET tms_synced=1 WHERE uid=?", (r.get('uid'),))
                    conn.commit()
                    print(f"[TMS Retry] UID {r.get('uid')} synced successfully.")
            except Exception as e:
                print(f"[TMS Retry] error pushing {r.get('uid')}: {e}")

        conn.close()
        time.sleep(10)



# ============================================================
# Digital Passport, Traceability, Inspections, Risk, Divisions
# ============================================================

@app.route('/component/<uid>')
def component_passport(uid):
    """Public digital passport for a railway component."""
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM fittings WHERE uid=?", (uid,)).fetchone()
    conn.close()
    if not row:
        return render_template('scan.html', uid=uid, risk='Unknown',
                               vendor_risk='Unknown', inspection_date='N/A',
                               qr_code='', error="Component not found"), 404
    component = {k: row[k] for k in row.keys()}

    # Check QR active
    if not component.get('qr_active', 1):
        return "This QR identity has been deactivated.", 410

    vendor_meta = get_vendor_meta(component.get('vendor_id'))
    warranty_status = compute_warranty_status(component.get('warranty_end'))
    lifecycle_status = determine_lifecycle_status(component)
    events = get_component_traceability(uid)
    inspections = get_component_inspections(uid)

    # Get or build risk assessment
    risk_assessment = get_latest_risk_assessment(uid)
    if not risk_assessment:
        risk_assessment = build_structured_risk(component)

    record_audit('QR_VERIFIED', 'component', uid, 'public')

    return render_template(
        'component_passport.html',
        component=component,
        vendor_meta=vendor_meta,
        warranty_status=warranty_status,
        lifecycle_status=lifecycle_status,
        events=events,
        inspections=inspections,
        risk_assessment=risk_assessment,
        verified_at=datetime.now().strftime('%Y-%m-%d %H:%M UTC'),
    )


@app.route('/api/component/<uid>/traceability')
def api_component_traceability(uid):
    conn = get_db_connection()
    row = conn.execute("SELECT uid, item_type, vendor, risk, lifecycle_status FROM fittings WHERE uid=?", (uid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Component not found'}), 404
    return jsonify({
        'uid': uid,
        'item_type': row['item_type'],
        'vendor': row['vendor'],
        'risk': row['risk'],
        'lifecycle_status': row['lifecycle_status'],
        'events': get_component_traceability(uid),
    })


@app.route('/api/component/<uid>/risk')
def api_component_risk(uid):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM fittings WHERE uid=?", (uid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Component not found'}), 404
    component = {k: row[k] for k in row.keys()}
    assessment = build_structured_risk(component)
    return jsonify(assessment)


@app.route('/api/component/<uid>/warranty')
def api_component_warranty(uid):
    conn = get_db_connection()
    row = conn.execute("SELECT uid, warranty_end, supply_date FROM fittings WHERE uid=?", (uid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Component not found'}), 404
    warranty_end = row['warranty_end']
    status = compute_warranty_status(warranty_end)
    days_remaining = None
    if warranty_end:
        try:
            end = datetime.strptime(warranty_end, "%Y-%m-%d").date()
            days_remaining = (end - datetime.today().date()).days
        except Exception:
            pass
    return jsonify({'uid': uid, 'warranty_end': warranty_end,
                    'status': status, 'days_remaining': days_remaining})


@app.route('/api/divisions')
def api_divisions():
    conn = get_vendor_db_connection()
    rows = conn.execute("SELECT * FROM railway_divisions WHERE status='ACTIVE' ORDER BY zone, name").fetchall()
    conn.close()
    return jsonify([{k: row[k] for k in row.keys()} for row in rows])


@app.route('/api/component/<uid>/inspections', methods=['GET', 'POST'])
def api_component_inspections(uid):
    if request.method == 'POST':
        if 'vendor_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        conn = get_db_connection()
        row = conn.execute("SELECT vendor_id FROM fittings WHERE uid=?", (uid,)).fetchone()
        conn.close()
        if not row:
            return jsonify({'error': 'Component not found'}), 404
        if row['vendor_id'] and str(row['vendor_id']) != str(session['vendor_id']):
            return jsonify({'error': 'Forbidden'}), 403

        data = request.get_json() or request.form
        inspector_name = str(data.get('inspector_name', '')).strip() or session.get('vendor_name', 'Inspector')
        inspection_date = str(data.get('inspection_date', datetime.today().strftime('%Y-%m-%d')))
        status = str(data.get('status', 'PASSED')).upper()
        if status not in ('PASSED', 'FAILED', 'PENDING', 'CONDITIONAL'):
            status = 'PENDING'
        findings = str(data.get('findings', '')).strip()
        notes = str(data.get('notes', '')).strip()
        risk_level = str(data.get('risk_level', 'Low'))
        next_inspection_date = str(data.get('next_inspection_date', '')).strip() or None
        now = datetime.now().isoformat(timespec='seconds')

        conn = get_db_connection()
        conn.execute(
            """INSERT INTO component_inspections
               (uid,inspector_name,inspection_date,status,findings,notes,risk_level,next_inspection_date,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (uid, inspector_name, inspection_date, status, findings, notes, risk_level, next_inspection_date, now)
        )
        conn.commit()
        conn.close()

        record_traceability_event(
            uid, 'INSPECTED',
            f"Inspection {status} by {inspector_name}. {findings}".strip(),
            actor=inspector_name
        )
        record_audit('INSPECTION_RECORDED', 'component', uid, session.get('vendor_name'))

        if request.is_json:
            return jsonify({'success': True, 'status': status})
        return redirect(url_for('component_passport', uid=uid))

    return jsonify(get_component_inspections(uid))


@app.route('/api/inventory/<uid>/history')
def api_inventory_history(uid):
    if 'vendor_id' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    conn = get_db_connection()
    row = conn.execute("SELECT vendor_id, stock, reserved_stock FROM fittings WHERE uid=?", (uid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    if row['vendor_id'] and str(row['vendor_id']) != str(session['vendor_id']):
        conn.close()
        return jsonify({'error': 'Forbidden'}), 403
    rows = conn.execute(
        "SELECT * FROM inventory_history WHERE uid=? ORDER BY created_at DESC LIMIT 50", (uid,)
    ).fetchall()
    conn.close()
    total = parse_int(row['stock'])
    reserved = parse_int(row['reserved_stock'])
    return jsonify({
        'uid': uid,
        'total_stock': total,
        'reserved_stock': reserved,
        'available_stock': max(total - reserved, 0),
        'history': [{k: r[k] for k in r.keys()} for r in rows],
    })


@app.route('/api/orders/<order_no>/shipment', methods=['POST'])
def create_shipment(order_no):
    if 'vendor_id' not in session:
        return jsonify({'error': 'Authentication required'}), 401
    vendor_id = str(session['vendor_id'])
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM marketplace_orders WHERE order_no=?", (order_no,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'error': 'Order not found'}), 404
    owned = conn.execute(
        "SELECT 1 FROM marketplace_order_items WHERE order_id=? AND vendor_id=?",
        (order['id'], vendor_id)
    ).fetchone()
    if not owned:
        conn.close()
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json() or request.form
    courier = str(data.get('courier', 'Indian Railways Logistics')).strip()
    tracking_number = str(data.get('tracking_number', '')).strip() or f"IRL-{order_no[-6:]}"
    estimated_delivery = str(data.get('estimated_delivery', '')).strip() or None
    now = datetime.now().isoformat(timespec='seconds')

    existing = conn.execute("SELECT id FROM shipments WHERE order_no=? AND vendor_id=?", (order_no, vendor_id)).fetchone()
    if existing:
        conn.close()
        return jsonify({'error': 'Shipment already exists for this order'}), 409

    conn.execute(
        """INSERT INTO shipments (order_id,order_no,vendor_id,courier,tracking_number,status,estimated_delivery,shipped_at,created_at)
           VALUES (?,?,?,?,?,'SHIPPED',?,?,?)""",
        (order['id'], order_no, vendor_id, courier, tracking_number, estimated_delivery, now, now)
    )
    conn.execute("UPDATE marketplace_orders SET status='Shipped' WHERE order_no=?", (order_no,))

    # Record traceability for each component in this order
    items = conn.execute(
        "SELECT uid FROM marketplace_order_items WHERE order_id=? AND vendor_id=?",
        (order['id'], vendor_id)
    ).fetchall()
    conn.commit()
    conn.close()

    for item in items:
        record_traceability_event(
            item['uid'], 'SHIPPED',
            f"Shipped via {courier}. Tracking: {tracking_number}",
            actor=session.get('vendor_name'), order_no=order_no
        )
    record_audit('SHIPMENT_CREATED', 'order', order_no, session.get('vendor_name'),
                 f"courier={courier} tracking={tracking_number}")

    if request.is_json:
        return jsonify({'success': True, 'tracking_number': tracking_number})
    return redirect(url_for('vendor_dashboard', msg=f"Shipment created. Tracking: {tracking_number}"))


@app.route('/api/orders/<order_no>/shipment')
def get_shipment(order_no):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM shipments WHERE order_no=? ORDER BY created_at DESC LIMIT 1", (order_no,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'No shipment found'}), 404
    return jsonify({k: row[k] for k in row.keys()})


@app.route('/admin/audit')
@admin_required
def admin_audit_log():
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 200"
    ).fetchall()
    conn.close()
    entries = [{k: row[k] for k in row.keys()} for row in rows]
    return render_template('admin_audit.html', entries=entries)


@app.route('/admin/analytics')
@admin_required
def admin_analytics():
    conn = get_db_connection()
    c = conn.cursor()

    # High-risk components
    c.execute("SELECT uid, item_type, vendor, risk, inspection_date FROM fittings WHERE risk='High' ORDER BY uid LIMIT 20")
    high_risk = [{k: row[k] for k in row.keys()} for row in c.fetchall()]

    # Upcoming inspections (next 30 days)
    today = datetime.today().date()
    in_30 = (today + timedelta(days=30)).isoformat()
    c.execute(
        "SELECT uid, item_type, vendor, inspection_date FROM fittings WHERE inspection_date BETWEEN ? AND ? ORDER BY inspection_date",
        (today.isoformat(), in_30)
    )
    upcoming_inspections = [{k: row[k] for k in row.keys()} for row in c.fetchall()]

    # Warranty expiring soon
    c.execute(
        "SELECT uid, item_type, vendor, warranty_end FROM fittings WHERE warranty_end BETWEEN ? AND ? ORDER BY warranty_end",
        (today.isoformat(), in_30)
    )
    expiring_warranty = [{k: row[k] for k in row.keys()} for row in c.fetchall()]

    # Lifecycle breakdown
    c.execute("SELECT lifecycle_status, COUNT(*) as cnt FROM fittings GROUP BY lifecycle_status ORDER BY cnt DESC")
    lifecycle_counts = [{k: row[k] for k in row.keys()} for row in c.fetchall()]

    # Top vendors by order revenue
    c.execute("""
        SELECT i.vendor, COALESCE(SUM(i.line_total),0) AS revenue, COUNT(DISTINCT i.order_id) AS orders
        FROM marketplace_order_items i
        GROUP BY i.vendor ORDER BY revenue DESC LIMIT 10
    """)
    top_vendors = [{k: row[k] for k in row.keys()} for row in c.fetchall()]

    conn.close()

    return jsonify({
        'high_risk_components': high_risk,
        'upcoming_inspections': upcoming_inspections,
        'expiring_warranty': expiring_warranty,
        'lifecycle_breakdown': lifecycle_counts,
        'top_vendors_by_revenue': top_vendors,
    })


@app.route('/vendor/order/<order_no>/shipment', methods=['POST'])
def vendor_create_shipment(order_no):
    """Dashboard form-based shipment creation."""
    return create_shipment(order_no)


@app.route('/component/<uid>/inspect', methods=['POST'])
def add_inspection(uid):
    """Form-based inspection submission from the passport page."""
    return api_component_inspections(uid)



# Wishlist (session-based, no login required)
@app.route('/wishlist')
def wishlist():
    wishlist_uids = session.get('wishlist', [])
    products = []
    if wishlist_uids:
        conn = get_db_connection()
        placeholders = ','.join('?' for _ in wishlist_uids)
        rows = conn.execute(f"SELECT * FROM fittings WHERE uid IN ({placeholders})", wishlist_uids).fetchall()
        conn.close()
        for row in rows:
            p = {k: row[k] for k in row.keys()}
            p['sale_price'] = selling_price(p)
            products.append(p)
    return render_template('wishlist.html', products=products)

@app.route('/wishlist/toggle/<uid>', methods=['POST'])
def wishlist_toggle(uid):
    wl = session.get('wishlist', [])
    if uid in wl:
        wl.remove(uid)
    else:
        wl.append(uid)
    session['wishlist'] = wl
    session.modified = True
    return redirect(request.referrer or url_for('shop'))

@app.route('/passport/<uid>')
def passport_redirect(uid):
    return redirect(url_for('component_passport', uid=uid))


# ============================================================
# ENHANCED: hook traceability into existing registration flow
# ============================================================

# ============================================================
# Order status state machine
# ============================================================

ALLOWED_TRANSITIONS = {
    'Placed':           {'Accepted', 'Cancelled'},
    'Accepted':         {'Packed', 'Cancelled'},
    'Packed':           {'Shipped', 'Cancelled'},
    'Shipped':          {'Out for Delivery'},
    'Out for Delivery': {'Delivered'},
    'Delivered':        {'Completed'},
    'Completed':        set(),
    'Cancelled':        set(),
}


@app.route('/vendor/order/<order_no>/status/v2', methods=['POST'])
def update_order_status_v2(order_no):
    """Status update with lifecycle guards and traceability."""
    if 'vendor_id' not in session:
        return redirect(url_for('vendor_login'))

    next_status = request.form.get('status', '').strip()
    vendor_id = str(session['vendor_id'])

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """SELECT o.id, o.status FROM marketplace_orders o
           JOIN marketplace_order_items i ON i.order_id=o.id
           WHERE o.order_no=? AND i.vendor_id=? LIMIT 1""",
        (order_no, vendor_id)
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return redirect(url_for('vendor_dashboard', msg="Order not found or access denied."))

    current_status = row['status']
    allowed = ALLOWED_TRANSITIONS.get(current_status, set())
    if next_status not in allowed:
        conn.close()
        return redirect(url_for('vendor_dashboard',
                                msg=f"Cannot move order from {current_status} to {next_status}."))

    c.execute("UPDATE marketplace_orders SET status=? WHERE order_no=?", (next_status, order_no))

    # If shipped, auto-create shipment record if not exists
    if next_status == 'Shipped':
        existing = c.execute("SELECT id FROM shipments WHERE order_no=? AND vendor_id=?",
                             (order_no, vendor_id)).fetchone()
        if not existing:
            tracking = f"IRL-{order_no[-8:]}"
            est_delivery = (datetime.today() + timedelta(days=7)).strftime('%Y-%m-%d')
            now = datetime.now().isoformat(timespec='seconds')
            c.execute(
                """INSERT INTO shipments (order_id,order_no,vendor_id,courier,tracking_number,status,estimated_delivery,shipped_at,created_at)
                   VALUES (?,?,?,'Indian Railways Logistics',?,'SHIPPED',?,?,?)""",
                (row['id'], order_no, vendor_id, tracking, est_delivery, now, now)
            )
            # Record traceability for each component
            items = c.execute(
                "SELECT uid FROM marketplace_order_items WHERE order_id=? AND vendor_id=?",
                (row['id'], vendor_id)
            ).fetchall()
            conn.commit()
            for item in items:
                record_traceability_event(
                    item['uid'], 'SHIPPED',
                    f"Dispatched via Indian Railways Logistics. Tracking: {tracking}",
                    actor=session.get('vendor_name'), order_no=order_no
                )

    if next_status == 'Delivered':
        c.execute("UPDATE shipments SET status='DELIVERED', delivered_at=? WHERE order_no=?",
                  (datetime.now().isoformat(timespec='seconds'), order_no))
        items = c.execute(
            "SELECT uid FROM marketplace_order_items WHERE order_id=?", (row['id'],)
        ).fetchall()
        conn.commit()
        for item in items:
            record_traceability_event(
                item['uid'], 'DELIVERED',
                "Component delivered to buyer.",
                actor=session.get('vendor_name'), order_no=order_no
            )

    conn.commit()
    conn.close()
    record_audit('ORDER_STATUS_CHANGED', 'order', order_no,
                 session.get('vendor_name'), f"{current_status} -> {next_status}")
    return redirect(url_for('vendor_dashboard', msg=f"Order {order_no} updated to {next_status}."))


# ============================================================
# Admin intelligence and division routes
# ============================================================

@app.route('/admin/divisions')
@admin_required
def admin_divisions():
    conn = get_vendor_db_connection()
    rows = conn.execute("SELECT * FROM railway_divisions ORDER BY zone, name").fetchall()
    conn.close()
    divisions = [{k: row[k] for k in row.keys()} for row in rows]
    return jsonify(divisions)


@app.route('/admin/intelligence')
@admin_required
def admin_intelligence():
    conn = get_db_connection()
    c = conn.cursor()
    today = datetime.today().date()
    in_30 = (today + timedelta(days=30)).isoformat()

    c.execute("SELECT uid, item_type, vendor, risk, inspection_date, warranty_end FROM fittings WHERE risk='High' ORDER BY uid")
    high_risk = []
    for row in c.fetchall():
        d = {k: row[k] for k in row.keys()}
        d['warranty_status'] = compute_warranty_status(d.get('warranty_end'))
        high_risk.append(d)

    c.execute(
        "SELECT uid, item_type, vendor, inspection_date FROM fittings WHERE inspection_date BETWEEN ? AND ? ORDER BY inspection_date",
        (today.isoformat(), in_30)
    )
    upcoming_inspections = [{k: row[k] for k in row.keys()} for row in c.fetchall()]

    c.execute(
        "SELECT uid, item_type, vendor, warranty_end FROM fittings WHERE warranty_end BETWEEN ? AND ? ORDER BY warranty_end",
        (today.isoformat(), in_30)
    )
    expiring_warranty = [{k: row[k] for k in row.keys()} for row in c.fetchall()]

    c.execute("SELECT lifecycle_status, COUNT(*) as cnt FROM fittings GROUP BY lifecycle_status ORDER BY cnt DESC")
    lifecycle_counts = [{k: row[k] for k in row.keys()} for row in c.fetchall()]

    c.execute("""
        SELECT i.vendor, COALESCE(SUM(i.line_total),0) AS revenue, COUNT(DISTINCT i.order_id) AS orders
        FROM marketplace_order_items i
        GROUP BY i.vendor ORDER BY revenue DESC LIMIT 10
    """)
    top_vendors = [{k: row[k] for k in row.keys()} for row in c.fetchall()]
    conn.close()

    vconn = get_vendor_db_connection()
    divisions = [{k: row[k] for k in row.keys()} for row in
                 vconn.execute("SELECT * FROM railway_divisions WHERE status='ACTIVE' ORDER BY zone, name").fetchall()]
    vconn.close()

    return render_template(
        'admin_intelligence.html',
        high_risk=high_risk,
        upcoming_inspections=upcoming_inspections,
        expiring_warranty=expiring_warranty,
        lifecycle_counts=lifecycle_counts,
        top_vendors=top_vendors,
        divisions=divisions,
    )


@app.route('/admin/high-risk')
@admin_required
def admin_high_risk():
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT uid, item_type, vendor, risk, inspection_date, warranty_end FROM fittings WHERE risk='High' ORDER BY uid"
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        d = {k: row[k] for k in row.keys()}
        d['warranty_status'] = compute_warranty_status(d.get('warranty_end'))
        result.append(d)
    return jsonify(result)


# ============================================================
# Post-action hooks
# ============================================================

def post_register_hooks(uid, item_type, vendor, risk_level, vendor_name=None):
    """Call after a component is successfully registered."""
    record_traceability_event(
        uid, 'REGISTERED',
        f"Component '{item_type}' registered by vendor '{vendor}'.",
        actor=vendor_name or vendor
    )
    record_audit('COMPONENT_REGISTERED', 'component', uid, vendor_name or vendor,
                 f"type={item_type} risk={risk_level}")
    # Persist initial risk assessment so passport/intelligence have data immediately
    try:
        _conn = get_db_connection()
        _row = _conn.execute("SELECT * FROM fittings WHERE uid=?", (uid,)).fetchone()
        _conn.close()
        if _row:
            build_structured_risk({k: _row[k] for k in _row.keys()})
    except Exception as _e:
        print(f"[RiskAssessment on register] {_e}")


def post_purchase_hooks(order_no, items, customer_name):
    """Call after a successful checkout."""
    for item in items:
        record_traceability_event(
            item['uid'], 'PURCHASED',
            f"Purchased by {customer_name}. Order: {order_no}",
            actor=customer_name, order_no=order_no
        )
        record_inventory_change(
            item['uid'], 'SOLD',
            item.get('stock_before', 0), -item['quantity'],
            reason=f"Order {order_no}", order_no=order_no, actor=customer_name
        )
    record_audit('ORDER_PLACED', 'order', order_no, customer_name,
                 f"{len(items)} items")


# === Run app ===
if __name__ == '__main__':
    threading.Thread(target=periodic_risk_update, daemon=True).start()
    threading.Thread(target=validate_all_qr_codes, daemon=True).start()
    threading.Thread(target=retry_pending_sync, daemon=True).start()

    def open_browser():
        webbrowser.open_new("http://127.0.0.1:5000")
    threading.Timer(1.0, open_browser).start()

    app.run(debug=True, host="0.0.0.0")
