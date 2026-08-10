"""Hook traceability calls into existing registration and checkout routes."""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(BASE, "app.py")

with open(APP, "rb") as f:
    data = f.read()

# 1. Hook post_register_hooks after TMS push, before redirect to view_record
OLD_REG = (
    b"        return redirect(url_for('view_record', uid=uid))\r\n\r\n    retu"
)
NEW_REG = (
    b"        try:\r\n"
    b"            post_register_hooks(uid, item_type, vendor, risk_level,\r\n"
    b"                                vendor_name=session.get('vendor_name'))\r\n"
    b"        except Exception as _e:\r\n"
    b"            print(f'[Hooks] {_e}')\r\n"
    b"\r\n"
    b"        return redirect(url_for('view_record', uid=uid))\r\n\r\n    retu"
)
assert OLD_REG in data, "Registration hook marker not found"
data = data.replace(OLD_REG, NEW_REG, 1)

# 2. Hook post_purchase_hooks after save_cart({}) in checkout
OLD_CHECKOUT = (
    b"        save_cart({})\n"
    b"        return redirect(url_for('order_success', order_no=order_no))\n"
)
NEW_CHECKOUT = (
    b"        save_cart({})\n"
    b"        try:\n"
    b"            # Attach stock_before for inventory history\n"
    b"            for _it in items:\n"
    b"                _it['stock_before'] = parse_int(_it.get('stock', 0)) + _it['quantity']\n"
    b"            post_purchase_hooks(order_no, items, customer_name)\n"
    b"        except Exception as _e:\n"
    b"            print(f'[Hooks] {_e}')\n"
    b"        return redirect(url_for('order_success', order_no=order_no))\n"
)
assert OLD_CHECKOUT in data, "Checkout hook marker not found"
data = data.replace(OLD_CHECKOUT, NEW_CHECKOUT, 1)

with open(APP, "wb") as f:
    f.write(data)

print("Hooks injected successfully.")
