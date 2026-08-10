import sqlite3

DB = "fittings.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

# Add new columns if not already present




try: c.execute("ALTER TABLE fittings ADD COLUMN vendor_email TEXT DEFAULT 'Low'")
except: print("vendor_email already exists")

try: c.execute("ALTER TABLE fittings ADD COLUMN manufactor_date TEXT")
except: print("manufactor_date already exists")

c.execute("PRAGMA table_info(fittings)")
print(c.fetchall())
conn.close()


c.execute("SELECT * FROM fittings")
print(c.fetchall())


conn.commit()
conn.close()
