import os, app as a

client = a.app.test_client()
a.app.config['TESTING'] = True

conn = a.get_db_connection()
row = conn.execute('SELECT uid FROM fittings LIMIT 1').fetchone()
conn.close()

assert row, "No fittings in DB"
uid = row['uid']

# Remove cached files to simulate cold Vercel start
for suffix in ('_display.png', '_engrave.png'):
    p = os.path.join(a.qr_dir, uid + suffix)
    if os.path.exists(p):
        os.remove(p)

r = client.get(f'/generated/qrcodes/{uid}_display.png')
assert r.status_code == 200, f"Expected 200, got {r.status_code}"
assert r.content_type == 'image/png', f"Expected image/png, got {r.content_type}"
print(f"PASS  on-demand QR for {uid}: {r.status_code} {r.content_type}")

# Unknown UID should 404
r2 = client.get('/generated/qrcodes/NONEXISTENT_display.png')
assert r2.status_code == 404, f"Expected 404, got {r2.status_code}"
print(f"PASS  unknown UID returns 404")

print("ALL PASS")
