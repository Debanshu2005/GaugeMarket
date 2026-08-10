# GaugeMarket — Railway QR Vendor & Marketplace Portal

This repository contains the full-stack **Vendor Portal + Marketplace** for the Railway QR Project.  
Vendors can register, manage fittings, generate AI-styled QR codes, and transmit G-codes to an ESP32 for laser engraving. Buyers can browse the marketplace, add products to cart, place orders, track shipments, and leave reviews.

---

## Demo Access

If you have seeded the database using `python scripts/create_demo_data.py`, you can use the following credentials to explore the application:

- **Admin Login**: Located in the top-right navigation menu.
  - Password: `admin1234` (configurable via `ADMIN_PASSWORD` environment variable)
- **Vendor Login**:
  - Email: `seller@railtrust.local`
  - Password: `seller123`

---

## Screenshots

### Vendor Registration Page
<img width="1920" height="1020" alt="image" src="https://github.com/user-attachments/assets/8b1a14f6-f125-4d22-9cd1-08677d3c6603" />


### Vendor Login Page
<img width="1920" height="1020" alt="image" src="https://github.com/user-attachments/assets/3e867776-35bf-401c-b376-974be1bc97ef" />


### Vendor Dashboard
<img width="1920" height="1020" alt="image" src="https://github.com/user-attachments/assets/047047c1-9eb5-4094-8109-4b0ee1d1c0e5" />

### Data Entry Form
<img width="1920" height="1020" alt="image" src="https://github.com/user-attachments/assets/9b6d844b-79c9-46e0-a651-b2db22393524" />

### QR Code Engraving Simulation
<img width="1280" height="680" alt="image" src="https://github.com/user-attachments/assets/e23b7309-2e42-41c0-b828-992afef1cd7e" />

 ### Hardware (esp32) -Sofware pipeline
<img width="1280" height="680" alt="image" src="https://github.com/user-attachments/assets/ebafc741-d78e-411a-bdcd-8fd3fa5d8ae0" />

### Marketplace Shop
<img width="1920" height="1020" alt="image" src="https://github.com/user-attachments/assets/7ac098f9-65c8-4354-9182-89b93c026c5b" />

### Admin Dashboard
<img width="1920" height="1020" alt="image" src="https://github.com/user-attachments/assets/9a7d4138-9b73-4cc9-a65a-f35dbca6e55d" />

---

## Features

### 🔐 Vendor Registration & Login
- Secure password hashing (salt + SHA-256).
- Each vendor gets a personalized dashboard with revenue analytics and order management.

### 📊 Vendor Dashboard & QR Management
- View, add, and manage fittings.
- Each vendor receives a unique identification QR code.
- Revenue chart with monthly breakdown.
- Incoming order management with status updates.

### 📝 Data Entry for Fittings
- Enter details: UID, item type, vendor, lot, supply date, warranty, manufacturer date/number.
- Data stored in SQLite databases for inspections and tracking.
- Marketplace settings (price, discount, stock, category) can be configured per product.

### 🤖 AI-Styled QR Code Generation
- Anime-themed QR codes with railway logo embedding.
- Two variants generated per fitting:
  - **Display QR** — colorful, themed for the web UI.
  - **Engrave QR** — strict 1-bit black/white for laser engraving.

### ⚙️ G-Code Generation & ESP32 Transmission
- Three G-code generation methods:
  - **Raster** — line-by-line zigzag scan (default).
  - **Vector** — contour-following for smoother paths.
  - **Fallback** — simple horizontal-run scanning.
- G-codes transmitted to an ESP32 over WebSocket for physical engraving.

### 🛡️ AI-Based Risk Assessment
- Keyword-based risk detection from fitting notes (`leak`, `corrosion`, `crack` → High risk).
- scikit-learn model for predictive risk scoring.
- Anomaly detection on QR scan patterns.

### 📈 Vendor Risk Assessment
- Aggregate risk calculation based on failure count across all products a vendor ships.
- Risk levels: Low / Medium / High.

### 📅 AI-Based Inspection Date Generation
- Automatic inspection and repair date scheduling based on:
  - Risk level (High → 30 days, Medium → 90 days, Low → 180 days).
  - Warranty end dates.
  - Manufacturing and supply dates.

### 🛒 Marketplace
- Browse and search fittings by category, price, and vendor.
- Shopping cart with quantity management and stock validation.
- Full checkout flow with order placement.
- Order tracking by order number and email.

### ⭐ Reviews & Ratings
- Product reviews with star ratings and customer comments.
- Vendor reviews with average rating and recent review listing.

### 🏢 Admin Dashboard
- Total orders, revenue, product count, and low-stock alerts.
- Order status distribution and recent orders table.
- Product category breakdown with stock counts.
- Railway division/zone statistics.

### 🔄 Background Services
- **Periodic risk update** — recalculates all fitting risks every hour.
- **QR validation** — ensures all fittings have display + engrave QR images.
- **Pending sync retry** — retries failed UDM/TMS syncs every 10 seconds.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | HTML, CSS (custom `rail.css` design system), JavaScript |
| **Backend** | Flask (Python) |
| **Database** | SQLite (`fittings.db`, `vendors.db`) |
| **QR Generation** | `qrcode` + `Pillow` + OpenCV (anime-style effects) |
| **AI / ML** | scikit-learn, numpy (risk model, anomaly detection) |
| **Hardware** | ESP32 via WebSocket (G-code laser engraving) |
| **Deployment** | Vercel (serverless) |

---

## Getting Started

### Prerequisites
- Python 3.10+
- ESP32 with Wi-Fi-enabled firmware (for engraving features)
- Engraver/printer hardware connected to ESP32

### Installation

```bash
# Clone the repository
git clone https://github.com/Debanshu2005/GaugeMarket.git
cd GaugeMarket

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Run the Application

```bash
python app.py
```

Access the web UI at **https://rail-qr-marketplace.vercel.app/**

- Register/login as a vendor from the portal.
- Browse fittings in the marketplace shop.
- Access the admin dashboard at `/admin`.

---

## ESP32 Integration

1. Once a QR code is generated, it is converted into G-code paths (raster, vector, or fallback).
2. The G-code is streamed to the ESP32 over WebSocket (`ws://<ESP32_IP>:81`).
3. The ESP32 executes the engraving on the selected material.

---

## Project Structure

```
GaugeMarket/
├── app.py                     # Main Flask application (routes + logic)
├── ai_module.py               # AI risk model, anomaly detection
├── tms.py                     # TMS (Track Management System) sync
├── udm.py                     # UDM (Unified Data Module) sync
├── requirements.txt           # Python dependencies
├── vercel.json                # Vercel deployment config
├── VERCEL_DEPLOYMENT.md       # Deployment instructions
├── README.md
│
├── templates/
│   ├── index.html             # Data entry form (main page)
│   ├── all.html               # View all fittings
│   ├── view.html              # Single fitting detail view
│   ├── scan.html              # QR scan result page
│   ├── shop.html              # Marketplace product listing
│   ├── cart.html               # Shopping cart
│   ├── checkout.html           # Checkout flow
│   ├── order_success.html      # Order confirmation
│   ├── track.html              # Order tracking
│   ├── admin.html              # Admin analytics dashboard
│   ├── vendor_registration.html
│   ├── vendor_login.html
│   ├── vendor_dashboard.html
│   └── vendor_details.html
│
├── static/
│   ├── rail.css               # Design system (industrial dark theme)
│   ├── image/
│   │   ├── rail.png           # Railway logo for QR embedding
│   │   └── azadi.png          # Decorative asset
│   ├── qrcodes/               # Generated QR code images
│   ├── vendor_qrcodes/        # Vendor identification QR codes
│   └── vendor_gcode/          # Generated G-code files
│
├── scripts/
│   └── create_demo_data.py    # Seed demo data
```

---

## Deployment

This project is pre-configured for **Vercel** serverless deployment.  
See [VERCEL_DEPLOYMENT.md](VERCEL_DEPLOYMENT.md) for detailed instructions.

---

## License

MIT License © 2025 [Debanshu2005](https://github.com/Debanshu2005)
