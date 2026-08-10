import os, re

APP = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'app.py'))

with open(APP, 'rb') as f:
    src = f.read()

# ── helper ──────────────────────────────────────────────────────────────────
def rep(old, new):
    global src
    assert old in src, f"PATCH FAILED – pattern not found:\n{old[:120]}"
    src = src.replace(old, new, 1)

# ════════════════════════════════════════════════════════════════════════════
# FIX 1  update_all_risks uses hardcoded "fittings.db" – silently fails on Vercel
# ════════════════════════════════════════════════════════════════════════════
rep(
    b'def update_all_risks(db_path: str = "fittings.db") -> int:',
    b'def update_all_risks(db_path: str = None) -> int:'
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 2  record_audit crashes outside request context (background threads)
# ════════════════════════════════════════════════════════════════════════════
rep(
    b'def record_audit(action, entity_type=None, entity_id=None, actor=None, details=None):\r\n    try:\r\n        ip = request.remote_addr\r\n    except Exception:\r\n        ip = None',
    b'def record_audit(action, entity_type=None, entity_id=None, actor=None, details=None):\r\n    try:\r\n        from flask import has_request_context\r\n        ip = request.remote_addr if has_request_context() else None\r\n    except Exception:\r\n        ip = None'
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 3  load_cart_items overselling: max(stock,1) lets 0-stock items through
# ════════════════════════════════════════════════════════════════════════════
rep(
    b'        quantity = min(cart.get(product[\'uid\'], 1), max(parse_int(product.get(\'stock\')), 1))',
    b'        avail = parse_int(product.get(\'stock\'))\r\n        if avail <= 0:\r\n            continue\r\n        quantity = min(cart.get(product[\'uid\'], 1), avail)'
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 4  add_to_cart: allow adding only if stock > 0 (already done) but also
#         cap quantity at actual stock, not just redirect silently
# ════════════════════════════════════════════════════════════════════════════
rep(
    b'    stock = parse_int(row[\'stock\'])\n    if stock <= 0:\n        return redirect(url_for(\'shop\'))\n\n    cart_data = get_cart()\n    cart_data[uid] = min(cart_data.get(uid, 0) + quantity, stock)',
    b'    stock = parse_int(row[\'stock\'])\n    if stock <= 0:\n        return redirect(request.referrer or url_for(\'shop\'))\n\n    cart_data = get_cart()\n    cart_data[uid] = min(cart_data.get(uid, 0) + quantity, stock)'
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 5  vendor_register uses raw sqlite3.connect instead of helper
# ════════════════════════════════════════════════════════════════════════════
rep(
    b'        try:\r\n            conn = sqlite3.connect(VENDOR_DB)\n            c = conn.cursor()\n            c.execute(\'\'\'INSERT INTO vendors \n                        (company_name, contact_person, email, password, phone, address,\n                         railway_zone, railway_division, supply_region, registration_date)\n                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\'\'\'',
    b'        try:\r\n            conn = get_vendor_db_connection()\n            c = conn.cursor()\n            c.execute(\'\'\'INSERT INTO vendors \n                        (company_name, contact_person, email, password, phone, address,\n                         railway_zone, railway_division, supply_region, registration_date)\n                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\'\'\'',
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 6  post_register_hooks: also build+persist risk assessment on registration
# ════════════════════════════════════════════════════════════════════════════
rep(
    b'def post_register_hooks(uid, item_type, vendor, risk_level, vendor_name=None):\r\n    """Call after a component is successfully registered."""\r\n    record_traceability_event(\r\n        uid, \'REGISTERED\',\r\n        f"Component \'{item_type}\' registered by vendor \'{vendor}\'.",\r\n        actor=vendor_name or vendor\r\n    )\r\n    record_audit(\'COMPONENT_REGISTERED\', \'component\', uid, vendor_name or vendor,\r\n                 f"type={item_type} risk={risk_level}")',
    b'def post_register_hooks(uid, item_type, vendor, risk_level, vendor_name=None):\r\n    """Call after a component is successfully registered."""\r\n    record_traceability_event(\r\n        uid, \'REGISTERED\',\r\n        f"Component \'{item_type}\' registered by vendor \'{vendor}\'.",\r\n        actor=vendor_name or vendor\r\n    )\r\n    record_audit(\'COMPONENT_REGISTERED\', \'component\', uid, vendor_name or vendor,\r\n                 f"type={item_type} risk={risk_level}")\r\n    # Persist initial risk assessment so passport shows it immediately\r\n    try:\r\n        conn = get_db_connection()\r\n        row = conn.execute("SELECT * FROM fittings WHERE uid=?", (uid,)).fetchone()\r\n        conn.close()\r\n        if row:\r\n            build_structured_risk({k: row[k] for k in row.keys()})\r\n    except Exception as _e:\r\n        print(f"[post_register_hooks] risk build failed: {_e}")'
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 7  determine_lifecycle_status: persist computed status back to DB
# ════════════════════════════════════════════════════════════════════════════
rep(
    b'def determine_lifecycle_status(component):\r\n    explicit = component.get(\'lifecycle_status\')\r\n    if explicit and explicit not in (\'REGISTERED\', None, \'\'):\r\n        return explicit\r\n    try:\r\n        conn = get_db_connection()\r\n        row = conn.execute(\r\n            """SELECT o.status FROM marketplace_order_items i\r\n               JOIN marketplace_orders o ON o.id=i.order_id\r\n               WHERE i.uid=? ORDER BY o.created_at DESC LIMIT 1""",\r\n            (component.get(\'uid\'),)\r\n        ).fetchone()\r\n        conn.close()\r\n        if row:\r\n            return {\'Placed\':\'PURCHASED\',\'Accepted\':\'PURCHASED\',\'Packed\':\'PACKED\',\r\n                    \'Shipped\':\'SHIPPED\',\'Out for Delivery\':\'IN_TRANSIT\',\r\n                    \'Delivered\':\'DELIVERED\',\'Completed\':\'DELIVERED\'}.get(row[0], \'LISTED\')\r\n    except Exception:\r\n        pass\r\n    if parse_money(component.get(\'price\')) > 0 and parse_int(component.get(\'stock\')) > 0:\r\n        return \'LISTED\'\r\n    return \'REGISTERED\'',
    b'def determine_lifecycle_status(component):\r\n    explicit = component.get(\'lifecycle_status\')\r\n    if explicit and explicit not in (\'REGISTERED\', None, \'\'):\r\n        return explicit\r\n    computed = \'REGISTERED\'\r\n    try:\r\n        conn = get_db_connection()\r\n        row = conn.execute(\r\n            """SELECT o.status FROM marketplace_order_items i\r\n               JOIN marketplace_orders o ON o.id=i.order_id\r\n               WHERE i.uid=? ORDER BY o.created_at DESC LIMIT 1""",\r\n            (component.get(\'uid\'),)\r\n        ).fetchone()\r\n        if row:\r\n            computed = {\'Placed\':\'PURCHASED\',\'Accepted\':\'PURCHASED\',\'Packed\':\'PACKED\',\r\n                        \'Shipped\':\'SHIPPED\',\'Out for Delivery\':\'IN_TRANSIT\',\r\n                        \'Delivered\':\'DELIVERED\',\'Completed\':\'DELIVERED\'}.get(row[0], \'LISTED\')\r\n        elif parse_money(component.get(\'price\')) > 0 and parse_int(component.get(\'stock\')) > 0:\r\n            computed = \'LISTED\'\r\n        # Persist if changed\r\n        if computed != (component.get(\'lifecycle_status\') or \'REGISTERED\'):\r\n            conn.execute("UPDATE fittings SET lifecycle_status=? WHERE uid=?",\r\n                         (computed, component.get(\'uid\')))\r\n            conn.commit()\r\n        conn.close()\r\n    except Exception:\r\n        pass\r\n    return computed'
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 8  admin_dashboard: revenue counts ALL orders including Cancelled;
#         product_count counts ALL fittings; low_stock counts unlisted items;
#         divisions comes from vendor grouping not canonical railway_divisions table.
#         Replace the entire admin_dashboard route body.
# ════════════════════════════════════════════════════════════════════════════
rep(
    b'@app.route(\'/admin\')\ndef admin_dashboard():\n    conn = get_db_connection()\n    c = conn.cursor()\n    c.execute("SELECT COUNT(*), COALESCE(SUM(grand_total), 0) FROM marketplace_orders")\n    order_count, revenue = c.fetchone()\n    c.execute("SELECT COUNT(*) FROM fittings")\n    product_count = c.fetchone()[0]\n    c.execute("SELECT COUNT(*) FROM fittings WHERE COALESCE(stock, 0) <= 5")\n    low_stock_count = c.fetchone()[0]\n    c.execute("SELECT status, COUNT(*) FROM marketplace_orders GROUP BY status ORDER BY status")\n    status_counts = c.fetchall()\n    c.execute("""\n        SELECT o.order_no, o.customer_name, o.status, o.grand_total, o.created_at\n        FROM marketplace_orders o\n        ORDER BY o.created_at DESC\n        LIMIT 20\n    """)\n    recent_orders = [{key: row[key] for key in row.keys()} for row in c.fetchall()]\n    c.execute("""\n        SELECT category, COUNT(*) AS product_count, COALESCE(SUM(stock), 0) AS stock_count\n        FROM fittings\n        GROUP BY category\n        ORDER BY product_count DESC\n        LIMIT 8\n    """)\n    categories = [{key: row[key] for key in row.keys()} for row in c.fetchall()]\n    conn.close()\n\n    conn_v = get_vendor_db_connection()\n    seller_count = conn_v.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]\n    division_rows = conn_v.execute("""\n        SELECT railway_zone, railway_division, supply_region, COUNT(*) AS seller_count\n        FROM vendors\n        GROUP BY railway_zone, railway_division, supply_region\n        ORDER BY seller_count DESC\n    """).fetchall()\n    divisions = [{key: row[key] for key in row.keys()} for row in division_rows]\n    conn_v.close()\n\n    return render_template(\n        \'admin.html\',\n        order_count=order_count,\n        revenue=revenue,\n        product_count=product_count,\n        low_stock_count=low_stock_count,\n        seller_count=seller_count,\n        status_counts=status_counts,\n        recent_orders=recent_orders,\n        categories=categories,\n        divisions=divisions\n    )',
    b'@app.route(\'/admin\')\ndef admin_dashboard():\n    conn = get_db_connection()\n    c = conn.cursor()\n\n    # Revenue: only count orders that are Delivered or Completed (real revenue)\n    c.execute("SELECT COUNT(*), COALESCE(SUM(grand_total), 0) FROM marketplace_orders WHERE status IN (\'Delivered\',\'Completed\')")\n    completed_count, revenue = c.fetchone()\n\n    # Total orders (all statuses) for the Orders KPI\n    c.execute("SELECT COUNT(*) FROM marketplace_orders")\n    order_count = c.fetchone()[0]\n\n    # Listed products only (price > 0) for product count\n    c.execute("SELECT COUNT(*) FROM fittings WHERE COALESCE(price, 0) > 0")\n    product_count = c.fetchone()[0]\n\n    # Low stock: listed products (price > 0) with stock <= 5\n    c.execute("SELECT COUNT(*) FROM fittings WHERE COALESCE(price, 0) > 0 AND COALESCE(stock, 0) <= 5")\n    low_stock_count = c.fetchone()[0]\n\n    # Status breakdown comes from ALL orders so the chart totals match order_count\n    c.execute("SELECT status, COUNT(*) FROM marketplace_orders GROUP BY status ORDER BY status")\n    status_counts = c.fetchall()\n\n    c.execute("""\n        SELECT o.order_no, o.customer_name, o.status, o.grand_total, o.created_at\n        FROM marketplace_orders o\n        ORDER BY o.created_at DESC\n        LIMIT 20\n    """)\n    recent_orders = [{key: row[key] for key in row.keys()} for row in c.fetchall()]\n\n    # Category inventory: listed products only\n    c.execute("""\n        SELECT category, COUNT(*) AS product_count, COALESCE(SUM(stock), 0) AS stock_count\n        FROM fittings\n        WHERE COALESCE(price, 0) > 0\n        GROUP BY category\n        ORDER BY product_count DESC\n        LIMIT 8\n    """)\n    categories = [{key: row[key] for key in row.keys()} for row in c.fetchall()]\n    conn.close()\n\n    conn_v = get_vendor_db_connection()\n    seller_count = conn_v.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]\n\n    # Divisions: join canonical railway_divisions with vendor counts\n    division_rows = conn_v.execute("""\n        SELECT rd.name AS railway_division, rd.zone AS railway_zone,\n               rd.region AS supply_region, rd.code,\n               COUNT(v.id) AS seller_count\n        FROM railway_divisions rd\n        LEFT JOIN vendors v ON v.railway_division = rd.name\n        WHERE rd.status = \'ACTIVE\'\n        GROUP BY rd.id\n        ORDER BY seller_count DESC, rd.name\n    """).fetchall()\n    divisions = [{key: row[key] for key in row.keys()} for row in division_rows]\n    conn_v.close()\n\n    return render_template(\n        \'admin.html\',\n        order_count=order_count,\n        revenue=revenue,\n        completed_count=completed_count,\n        product_count=product_count,\n        low_stock_count=low_stock_count,\n        seller_count=seller_count,\n        status_counts=status_counts,\n        recent_orders=recent_orders,\n        categories=categories,\n        divisions=divisions\n    )'
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 9  admin_intelligence top_vendors: group by vendor_id not vendor name
# ════════════════════════════════════════════════════════════════════════════
rep(
    b'    c.execute("""\r\n        SELECT i.vendor, COALESCE(SUM(i.line_total),0) AS revenue, COUNT(DISTINCT i.order_id) AS orders\r\n        FROM marketplace_order_items i\r\n        GROUP BY i.vendor ORDER BY revenue DESC LIMIT 10\r\n    """)\r\n    top_vendors = [{k: row[k] for k in row.keys()} for row in c.fetchall()]\r\n    conn.close()\r\n\r\n    vconn = get_vendor_db_connection()\r\n    divisions = [{k: row[k] for k in row.keys()} for row in\r\n                 vconn.execute("SELECT * FROM railway_divisions WHERE status=\'ACTIVE\' ORDER BY zone, name").fetchall()]\r\n    vconn.close()\r\n\r\n    return render_template(\r\n        \'admin_intelligence.html\',',
    b'    c.execute("""\r\n        SELECT COALESCE(v.company_name, i.vendor) AS vendor,\r\n               i.vendor_id,\r\n               COALESCE(SUM(i.line_total),0) AS revenue,\r\n               COUNT(DISTINCT i.order_id) AS orders\r\n        FROM marketplace_order_items i\r\n        LEFT JOIN (SELECT id, company_name FROM vendors) v ON CAST(v.id AS TEXT)=CAST(i.vendor_id AS TEXT)\r\n        GROUP BY COALESCE(CAST(i.vendor_id AS TEXT), i.vendor)\r\n        ORDER BY revenue DESC LIMIT 10\r\n    """)\r\n    top_vendors = [{k: row[k] for k in row.keys()} for row in c.fetchall()]\r\n    conn.close()\r\n\r\n    vconn = get_vendor_db_connection()\r\n    divisions = [{k: row[k] for k in row.keys()} for row in\r\n                 vconn.execute("SELECT * FROM railway_divisions WHERE status=\'ACTIVE\' ORDER BY zone, name").fetchall()]\r\n    vconn.close()\r\n\r\n    return render_template(\r\n        \'admin_intelligence.html\','
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 10  admin_analytics (JSON endpoint) same top_vendors fix
# ════════════════════════════════════════════════════════════════════════════
rep(
    b'    c.execute("""\r\n        SELECT i.vendor, COALESCE(SUM(i.line_total),0) AS revenue, COUNT(DISTINCT i.order_id) AS orders\r\n        FROM marketplace_order_items i\r\n        GROUP BY i.vendor ORDER BY revenue DESC LIMIT 10\r\n    """)\r\n    top_vendors = [{k: row[k] for k in row.keys()} for row in c.fetchall()]\r\n\r\n    conn.close()\r\n\r\n    return jsonify({',
    b'    c.execute("""\r\n        SELECT COALESCE(v.company_name, i.vendor) AS vendor,\r\n               COALESCE(SUM(i.line_total),0) AS revenue,\r\n               COUNT(DISTINCT i.order_id) AS orders\r\n        FROM marketplace_order_items i\r\n        LEFT JOIN (SELECT id, company_name FROM vendors) v ON CAST(v.id AS TEXT)=CAST(i.vendor_id AS TEXT)\r\n        GROUP BY COALESCE(CAST(i.vendor_id AS TEXT), i.vendor)\r\n        ORDER BY revenue DESC LIMIT 10\r\n    """)\r\n    top_vendors = [{k: row[k] for k in row.keys()} for row in c.fetchall()]\r\n\r\n    conn.close()\r\n\r\n    return jsonify({'
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 11  Remove dead code: checkout_v2 (returns None), _patched_index,
#          _orig_checkout assignments
# ════════════════════════════════════════════════════════════════════════════
rep(
    b'_original_index = index\r\n\r\ndef _patched_index():\r\n    """Wrap index to record REGISTERED traceability event after insert."""\r\n    # We can\'t easily wrap the existing route, so traceability is recorded\r\n    # inside the checkout and registration routes directly.\r\n    return _original_index()\r\n\r\n\r\n# ============================================================\r\n# ENHANCED CHECKOUT: record traceability + inventory history\r\n# ============================================================\r\n\r\n# Monkey-patch checkout to add traceability after order placement\r\n_orig_checkout = checkout\r\n\r\n@app.route(\'/checkout_v2\', methods=[\'GET\', \'POST\'])\r\ndef checkout_v2():\r\n    """Not used - traceability is injected via post-order hook below."""\r\n    pass\r\n\r\n\r\n@app.after_request\r\ndef after_request_hook(response):\r\n    return response',
    b'@app.after_request\r\ndef after_request_hook(response):\r\n    return response'
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 12  update_order_status (old unguarded route): remove it so only v2 exists
#          Replace it with a redirect to v2 to avoid breaking any old bookmarks
# ════════════════════════════════════════════════════════════════════════════
rep(
    b'@app.route(\'/vendor/order/<order_no>/status\', methods=[\'POST\'])\ndef update_order_status(order_no):\n    if \'vendor_id\' not in session:\n        return redirect(url_for(\'vendor_login\'))\n\n    next_status = request.form.get(\'status\', \'Placed\')\n    allowed = {\'Placed\', \'Accepted\', \'Packed\', \'Shipped\', \'Out for Delivery\', \'Delivered\', \'Completed\', \'Cancelled\'}\n    if next_status not in allowed:\n        next_status = \'Placed\'\n\n    vendor_id = str(session[\'vendor_id\'])\n    conn = get_db_connection()\n    c = conn.cursor()\n    c.execute("""\n        SELECT o.id\n        FROM marketplace_orders o\n        JOIN marketplace_order_items i ON i.order_id = o.id\n        WHERE o.order_no=? AND i.vendor_id=?\n        LIMIT 1\n    """, (order_no, vendor_id))\n    owned = c.fetchone()\n    if owned:\n        c.execute("UPDATE marketplace_orders SET status=? WHERE order_no=?", (next_status, order_no))\n        conn.commit()\n    conn.close()\n    return redirect(url_for(\'vendor_dashboard\', msg=f"Order {order_no} updated."))',
    b'@app.route(\'/vendor/order/<order_no>/status\', methods=[\'POST\'])\ndef update_order_status(order_no):\n    # Delegate to the guarded v2 handler\n    return update_order_status_v2(order_no)'
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 13  update_all_risks: pass the real DB path from app context
#          Replace the call sites inside app.py to pass DB explicitly
# ════════════════════════════════════════════════════════════════════════════
# There are two call sites: inside index route and inside periodic_risk_update
rep(
    b'        try:\r\n            update_all_risks()\r\n        except Exception as e:\r\n            print(f"[Global Risk Update] Exception: {e}")',
    b'        try:\r\n            update_all_risks(DB)\r\n        except Exception as e:\r\n            print(f"[Global Risk Update] Exception: {e}")'
)
rep(
    b'def periodic_risk_update():\r\n    while True:\r\n        try:\r\n            update_all_risks()\r\n        except Exception as e:\r\n            print("[Risk Update] Exception:", e)\r\n        time.sleep(3600)',
    b'def periodic_risk_update():\r\n    while True:\r\n        try:\r\n            update_all_risks(DB)\r\n        except Exception as e:\r\n            print("[Risk Update] Exception:", e)\r\n        time.sleep(3600)'
)

# ════════════════════════════════════════════════════════════════════════════
# FIX 14  update_all_risks in ai_module: handle None db_path gracefully
#          (already changed signature above; now fix the body to use it)
# ════════════════════════════════════════════════════════════════════════════
# The ai_module.py needs its own fix – handled separately below

with open(APP, 'wb') as f:
    f.write(src)

print("app.py patched OK")

# ════════════════════════════════════════════════════════════════════════════
# FIX 15  ai_module.py: update_all_risks hardcoded db path
# ════════════════════════════════════════════════════════════════════════════
AI = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'ai_module.py'))
with open(AI, 'rb') as f:
    ai = f.read()

ai = ai.replace(
    b'def update_all_risks(db_path: str = "fittings.db") -> int:\n    """Refresh risk columns for all fittings. Returns number of rows updated."""\n    updated = 0\n    try:\n        with sqlite3.connect(db_path) as conn:',
    b'def update_all_risks(db_path: str = None) -> int:\n    """Refresh risk columns for all fittings. Returns number of rows updated."""\n    if db_path is None:\n        import tempfile, os as _os\n        db_path = _os.path.join(tempfile.gettempdir(), "fittings.db") if _os.environ.get("VERCEL") else "fittings.db"\n    updated = 0\n    try:\n        with sqlite3.connect(db_path) as conn:'
)

with open(AI, 'wb') as f:
    f.write(ai)

print("ai_module.py patched OK")
