"""Test all new routes and helpers."""
import json, sys
import app as a

client = a.app.test_client()
a.app.config['TESTING'] = True
a.app.config['SECRET_KEY'] = 'test'

errors = []

def check(label, condition, detail=''):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        errors.append(label)

print("\n=== Helpers ===")
check("warranty EXPIRED", a.compute_warranty_status('2020-01-01') == 'EXPIRED')
check("warranty ACTIVE", a.compute_warranty_status('2099-01-01') == 'ACTIVE')
check("warranty EXPIRING_SOON", a.compute_warranty_status(
    (__import__('datetime').datetime.today() + __import__('datetime').timedelta(days=30)).strftime('%Y-%m-%d')
) == 'EXPIRING_SOON')
check("warranty UNKNOWN", a.compute_warranty_status(None) == 'UNKNOWN')

print("\n=== API Routes ===")
r = client.get('/api/divisions')
divs = json.loads(r.data)
check("/api/divisions 200", r.status_code == 200)
check("/api/divisions has data", len(divs) >= 10, f"got {len(divs)}")

r2 = client.get('/admin/analytics')
check("/admin/analytics 200", r2.status_code == 200)
data2 = json.loads(r2.data)
check("/admin/analytics has keys", 'high_risk_components' in data2 and 'lifecycle_breakdown' in data2)

r3 = client.get('/admin/high-risk')
check("/admin/high-risk 200", r3.status_code == 200)

r_audit = client.get('/admin/audit')
check("/admin/audit 200", r_audit.status_code == 200)

print("\n=== Component Routes ===")
conn = a.get_db_connection()
row = conn.execute('SELECT uid FROM fittings LIMIT 1').fetchone()
conn.close()

if row:
    uid = row['uid']
    r4 = client.get(f'/api/component/{uid}/traceability')
    check(f"/api/component/{uid}/traceability 200", r4.status_code == 200)
    d4 = json.loads(r4.data)
    check("traceability has uid", d4.get('uid') == uid)

    r5 = client.get(f'/component/{uid}')
    check(f"/component/{uid} passport 200", r5.status_code == 200)
    check("passport contains uid", uid.encode() in r5.data)

    r6 = client.get(f'/api/component/{uid}/risk')
    check(f"/api/component/{uid}/risk 200", r6.status_code == 200)
    d6 = json.loads(r6.data)
    check("risk has score", 'risk_score' in d6)
    check("risk has factors", 'factors' in d6)
    check("risk has recommendation", 'recommendation' in d6)

    r7 = client.get(f'/api/component/{uid}/warranty')
    check(f"/api/component/{uid}/warranty 200", r7.status_code == 200)
    d7 = json.loads(r7.data)
    check("warranty has status", 'status' in d7)

    r8 = client.get(f'/api/component/{uid}/inspections')
    check(f"/api/component/{uid}/inspections 200", r8.status_code == 200)
else:
    print("  SKIP  No fittings in DB")

print("\n=== Structured Risk ===")
conn = a.get_db_connection()
row = conn.execute('SELECT * FROM fittings LIMIT 1').fetchone()
conn.close()
if row:
    comp = {k: row[k] for k in row.keys()}
    ra = a.build_structured_risk(comp)
    check("risk_level present", ra.get('risk_level') in ('Low','Medium','High','CRITICAL'))
    check("risk_score 0-100", 0 <= ra.get('risk_score', -1) <= 100)
    check("factors is list", isinstance(ra.get('factors'), list))
    check("recommendation present", bool(ra.get('recommendation')))

    # Verify it was persisted
    saved = a.get_latest_risk_assessment(comp['uid'])
    check("risk assessment persisted", saved is not None)
    check("persisted factors is list", isinstance(saved.get('factors'), list))

print("\n=== Traceability Recording ===")
conn = a.get_db_connection()
row = conn.execute('SELECT uid FROM fittings LIMIT 1').fetchone()
conn.close()
if row:
    uid = row['uid']
    before = len(a.get_component_traceability(uid))
    a.record_traceability_event(uid, 'TEST_EVENT', 'Automated test event', actor='test')
    after = len(a.get_component_traceability(uid))
    check("traceability event recorded", after == before + 1)

print("\n=== DB Tables ===")
conn = a.get_db_connection()
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
conn.close()
for t in ['traceability_events','component_inspections','risk_assessments',
          'shipments','audit_log','inventory_history']:
    check(f"table {t} exists", t in tables)

vconn = a.get_vendor_db_connection()
vtables = {r[0] for r in vconn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
vconn.close()
check("table railway_divisions exists", 'railway_divisions' in vtables)

print(f"\n{'='*40}")
if errors:
    print(f"FAILED: {len(errors)} test(s): {errors}")
    sys.exit(1)
else:
    print(f"ALL TESTS PASSED")
