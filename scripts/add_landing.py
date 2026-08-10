import os

APP = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'app.py'))

with open(APP, 'rb') as f:
    src = f.read()

old_route = b"@app.route('/', methods=['GET', 'POST'])\r\ndef index():"
new_route = b"@app.route('/entry', methods=['GET', 'POST'])\r\ndef index():"
assert old_route in src, "Could not find index route"
src = src.replace(old_route, new_route, 1)

landing_code = (
    b"@app.route('/')\r\n"
    b"def landing():\r\n"
    b"    return render_template('landing.html')\r\n"
    b"\r\n"
    b"\r\n"
)
src = src.replace(new_route, landing_code + new_route, 1)

with open(APP, 'wb') as f:
    f.write(src)

print("Patched OK")
