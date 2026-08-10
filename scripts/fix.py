import os

folder = "static/qrcodes"

for filename in os.listdir(folder):
    if filename.endswith(".png"):
        newname = ''.join(c for c in filename if c.isdigit()) + ".png"
        oldpath = os.path.join(folder, filename)
        newpath = os.path.join(folder, newname)
        if oldpath != newpath:
            if os.path.exists(newpath):
                print(f"Skipping {filename} -> {newname} (already exists)")
            else:
                os.rename(oldpath, newpath)
                print(f"Renamed {filename} -> {newname}")
