# Vercel Deployment

This Flask app is configured for Vercel's Python runtime.

## Deploy

```bash
npm i -g vercel
vercel
vercel --prod
```

Vercel detects the top-level `app` object in `app.py` and installs Python dependencies from `requirements.txt`.

## Environment Variables

Set these in Vercel Project Settings:

```text
SECRET_KEY=replace-with-a-long-random-secret
```

Optional local/runtime overrides:

```text
FITTINGS_DB_PATH=/tmp/fittings.db
VENDOR_DB_PATH=/tmp/vendors.db
QR_OUTPUT_DIR=/tmp/qrcodes
VENDOR_QR_OUTPUT_DIR=/tmp/vendor_qrcodes
VENDOR_GCODE_OUTPUT_DIR=/tmp/vendor_gcode
RISK_MODEL_PATH=/tmp/risk_model.pkl
```

## Important Storage Note

Vercel functions can write only to temporary runtime storage. This app copies bundled SQLite files to `/tmp` on Vercel and writes generated QR/G-code files there, so it is deployable for demos and previews.

For production durability, move the SQLite databases to Postgres and generated QR/G-code assets to Blob/S3/Cloudinary.
