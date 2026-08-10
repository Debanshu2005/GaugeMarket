import app as a
client = a.app.test_client()
a.app.config['TESTING'] = True

r = client.get('/admin/intelligence')
assert r.status_code == 200, f"Expected 200, got {r.status_code}"
body = r.data
assert b'Intelligence' in body, "Missing 'Intelligence' heading"
assert b'High-Risk' in body, "Missing High-Risk section"
assert b'Railway Divisions' in body, "Missing Railway Divisions section"
assert b'Lifecycle' in body, "Missing Lifecycle section"
# Sidebar should NOT have raw API links anymore
assert b'/admin/analytics' not in body, "Raw analytics API link still present"
assert b'/admin/high-risk' not in body, "Raw high-risk API link still present"
assert b'/api/divisions' not in body, "Raw divisions API link still present"
print("PASS  /admin/intelligence renders correctly")

# Admin dashboard sidebar should also be fixed
r2 = client.get('/admin')
assert b'admin_intelligence' in r2.data or b'Intelligence' in r2.data, "Intelligence link missing from admin sidebar"
assert b'/admin/analytics' not in r2.data, "Raw analytics link still in admin sidebar"
print("PASS  /admin sidebar links fixed")

print("ALL PASS")
