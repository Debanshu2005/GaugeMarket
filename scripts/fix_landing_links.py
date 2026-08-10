import os, re

TMPL = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'templates'))

# Patterns where url_for('index') means "go home / brand logo" -> change to landing
HOME_PATTERNS = [
    # brand anchor
    b"href=\"{{ url_for('index') }}\" class=\"rq-brand\"",
    # topbar Home button in vendor_login / vendor_registration / scan
    b"href=\"{{ url_for('index') }}\" class=\"btn btn-outline-light btn-sm\"><i class=\"bi bi-house\"></i> Home",
    # footer "Home" link
    b"href=\"{{ url_for('index') }}\">Home",
]

for fname in os.listdir(TMPL):
    if not fname.endswith('.html'):
        continue
    path = os.path.join(TMPL, fname)
    with open(path, 'rb') as f:
        src = f.read()
    original = src
    for pat in HOME_PATTERNS:
        replacement = pat.replace(b"url_for('index')", b"url_for('landing')")
        src = src.replace(pat, replacement)
    if src != original:
        with open(path, 'wb') as f:
            f.write(src)
        print(f"Updated: {fname}")

print("Done")
