"""Add admin_intelligence HTML route and fix sidebar links."""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(BASE, "app.py")

with open(APP, "rb") as f:
    data = f.read()

# 1. Inject admin_intelligence route after admin_divisions
OLD = (
    b"def admin_divisions():\r\n"
    b"    conn = get_vendor_db_connection()\r\n"
    b"    rows = conn.execute(\"SELECT * FROM railway_divisions ORDER BY zone, name\").fetchall()\r\n"
    b"    conn.close()\r\n"
    b"    divisions = [{k: row[k] for k in row.keys()} for row in rows]\r\n"
    b"    return jsonify(divisions)"
)
assert OLD in data, "admin_divisions marker not found"

NEW = OLD + b"""\r\n\r\n\r\n@app.route('/admin/intelligence')\r\ndef admin_intelligence():\r\n    conn = get_db_connection()\r\n    c = conn.cursor()\r\n    today = datetime.today().date()\r\n    in_30 = (today + timedelta(days=30)).isoformat()\r\n\r\n    c.execute(\"SELECT uid, item_type, vendor, risk, inspection_date, warranty_end FROM fittings WHERE risk='High' ORDER BY uid\")\r\n    high_risk = []\r\n    for row in c.fetchall():\r\n        d = {k: row[k] for k in row.keys()}\r\n        d['warranty_status'] = compute_warranty_status(d.get('warranty_end'))\r\n        high_risk.append(d)\r\n\r\n    c.execute(\r\n        \"SELECT uid, item_type, vendor, inspection_date FROM fittings WHERE inspection_date BETWEEN ? AND ? ORDER BY inspection_date\",\r\n        (today.isoformat(), in_30)\r\n    )\r\n    upcoming_inspections = [{k: row[k] for k in row.keys()} for row in c.fetchall()]\r\n\r\n    c.execute(\r\n        \"SELECT uid, item_type, vendor, warranty_end FROM fittings WHERE warranty_end BETWEEN ? AND ? ORDER BY warranty_end\",\r\n        (today.isoformat(), in_30)\r\n    )\r\n    expiring_warranty = [{k: row[k] for k in row.keys()} for row in c.fetchall()]\r\n\r\n    c.execute(\"SELECT lifecycle_status, COUNT(*) as cnt FROM fittings GROUP BY lifecycle_status ORDER BY cnt DESC\")\r\n    lifecycle_counts = [{k: row[k] for k in row.keys()} for row in c.fetchall()]\r\n\r\n    c.execute(\"\"\"\r\n        SELECT i.vendor, COALESCE(SUM(i.line_total),0) AS revenue, COUNT(DISTINCT i.order_id) AS orders\r\n        FROM marketplace_order_items i\r\n        GROUP BY i.vendor ORDER BY revenue DESC LIMIT 10\r\n    \"\"\")\r\n    top_vendors = [{k: row[k] for k in row.keys()} for row in c.fetchall()]\r\n    conn.close()\r\n\r\n    vconn = get_vendor_db_connection()\r\n    divisions = [{k: row[k] for k in row.keys()} for row in\r\n                 vconn.execute(\"SELECT * FROM railway_divisions WHERE status='ACTIVE' ORDER BY zone, name\").fetchall()]\r\n    vconn.close()\r\n\r\n    return render_template(\r\n        'admin_intelligence.html',\r\n        high_risk=high_risk,\r\n        upcoming_inspections=upcoming_inspections,\r\n        expiring_warranty=expiring_warranty,\r\n        lifecycle_counts=lifecycle_counts,\r\n        top_vendors=top_vendors,\r\n        divisions=divisions,\r\n    )"""

data = data.replace(OLD, NEW, 1)

with open(APP, "wb") as f:
    f.write(data)

print("admin_intelligence route injected.")
