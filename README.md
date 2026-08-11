# GaugeMarket — Railway Supply Chain & Multi-Vendor Marketplace

**Live Demo:** https://rail-qr-marketplace.vercel.app  
**GitHub:** https://github.com/Debanshu2005/GaugeMarket

A production-ready multi-vendor e-commerce platform built for the Indian Railways supply chain. Vendors register railway components, generate laser-engravable QR codes, list products on the marketplace, and fulfil orders — while buyers get full component traceability from manufacture to delivery.

---

## Test Credentials

| Role | Email / Access | Password |
|---|---|---|
| **Admin** | `/admin/login` | `admin1234` |
| **Vendor / Seller** | `seller@railtrust.local` | `seller123` |
| **Buyer** | Register at `/buyer/register` or checkout as guest | your chosen password |

**Demo Coupon Codes:** `RAIL10` (10% off), `FLAT500` (₹500 off on orders ≥₹2,000), `WELCOME` (15% off on orders ≥₹500)

> Seed the database first: `python scripts/create_demo_data.py`

---

## Screenshots

### Landing Page
<img width="1920" height="1020" alt="Landing" src="https://github.com/user-attachments/assets/8b1a14f6-f125-4d22-9cd1-08677d3c6603" />

### Vendor Dashboard
<img width="1920" height="1020" alt="Vendor Dashboard" src="https://github.com/user-attachments/assets/047047c1-9eb5-4094-8109-4b0ee1d1c0e5" />

### Component Registration
<img width="1920" height="1020" alt="Data Entry" src="https://github.com/user-attachments/assets/9b6d844b-79c9-46e0-a651-b2db22393524" />

### QR Engraving Simulation
<img width="1280" height="680" alt="QR Engraving" src="https://github.com/user-attachments/assets/e23b7309-2e42-41c0-b828-992afef1cd7e" />

### ESP32 Hardware Pipeline
<img width="1280" height="680" alt="ESP32 Pipeline" src="https://github.com/user-attachments/assets/ebafc741-d78e-411a-bdcd-8fd3fa5d8ae0" />

### Marketplace
<img width="1920" height="1020" alt="Marketplace" src="https://github.com/user-attachments/assets/7ac098f9-65c8-4354-9182-89b93c026c5b" />

### Admin Dashboard
<img width="1920" height="1020" alt="Admin Dashboard" src="https://github.com/user-attachments/assets/9a7d4138-9b73-4cc9-a65a-f35dbca6e55d" />

---

## Features

### Multi-Vendor Marketplace
- Vendor registration and login with secure password hashing (salt + SHA-256)
- Each vendor manages their own product catalogue, inventory, and orders
- Vendor isolation — sellers cannot access each other's products, orders, or analytics
- Vendor public profile with reviews and ratings
- Revenue analytics with monthly breakdown chart

### Component Registration & QR Identity
- Register railway components with full metadata: UID, type, lot, warranty, manufacturer details
- Each component gets a **unique digital identity** — two QR variants generated automatically:
  - **Display QR** — styled with railway logo for web UI
  - **Engrave QR** — strict 1-bit black/white for laser engraving
- QR encodes a public passport URL — no sensitive data embedded
- On-demand QR regeneration

### Digital Component Passport
- Public passport page per component: `/component/<uid>`
- Shows identity, vendor, warranty status, risk assessment, inspection history
- Full traceability timeline (REGISTERED → PURCHASED → SHIPPED → DELIVERED)
- Warranty status calculated live: ACTIVE / EXPIRING_SOON / EXPIRED

### AI Risk Assessment
- Hybrid keyword + scikit-learn model scores each component
- Risk factors: inspection notes, warranty status, vendor aggregate risk, overdue inspections
- Structured output: Risk Level, Risk Score (0–100), Risk Factors, Recommendation
- Risk persisted to database and displayed on passport and admin intelligence

### G-Code Generation & ESP32 Engraving
- Three G-code generation modes: Raster (zigzag), Vector (contour), Fallback (horizontal run)
- G-code streamed to ESP32 over WebSocket (`ws://<ESP32_IP>:81`)
- Vendor QR codes also engravable with the same pipeline

### Marketplace & Shopping
- Browse and filter by category, risk level, stock availability
- Search by product name, UID, vendor, category
- Buyer registration and login for saved checkout details and order history
- Shopping cart with quantity management and atomic stock reservation
- Coupon system: flat and percentage discounts
- Checkout with shipping details and payment method selection
- Order confirmation with downloadable PDF invoice

### Order Management
- Controlled order state machine: Placed → Accepted → Packed → Shipped → Out for Delivery → Delivered → Completed
- Impossible transitions blocked server-side
- Automatic shipment record creation on dispatch
- Order tracking by order number + email

### Inventory
- Stock reduced atomically at checkout (prevents overselling)
- Inventory history log per component
- Low-stock alerts on admin dashboard

### Admin Dashboard
- Revenue, orders, vendors, components, low-stock, high-risk counts — all from live data
- Order status breakdown chart
- Category inventory breakdown
- Vendor revenue table
- Railway division statistics

### Intelligence & Audit
- High-risk components, upcoming inspections, expiring warranties
- Component lifecycle breakdown
- Full audit log: every registration, QR scan, order change, inspection, shipment

### Railway-Specific Features
- Components assigned to railway zones and divisions
- 10 pre-seeded Indian Railways divisions (HWH, KGP, MAS, DLI, etc.)
- TMS (Track Management System) and UDM (Unified Data Module) sync with retry
- Inspection scheduling based on risk level and warranty

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.12, Flask 3.1 |
| **Frontend** | HTML5, custom CSS design system (`rail.css`), vanilla JavaScript |
| **Database** | SQLite (`fittings.db` — components/orders, `vendors.db` — vendors/divisions) |
| **QR Generation** | `qrcode`, `Pillow`, OpenCV |
| **AI / ML** | scikit-learn (Naive Bayes + TF-IDF), numpy |
| **Hardware** | ESP32 via WebSocket, G-code (raster/vector/fallback) |
| **PDF** | ReportLab |
| **Deployment** | Vercel (serverless) |

---

## Database Schema

### fittings.db

**fittings** — core component registry  
`uid, item_type, vendor, vendor_id, lot, supply_date, warranty, warranty_end, manufactor_date, manufactor_number, notes, vendor_email, udm_synced, tms_synced, risk_flag, risk, vendor_risk, category, price, discount, stock, inspection_date, repair_date, failure_count, reserved_stock, lifecycle_status, qr_active, image_url`

**marketplace_orders**  
`id, order_no, customer_name, customer_email, customer_phone, shipping_address, payment_method, status, subtotal, discount_total, tax_total, shipping_total, grand_total, created_at`

**marketplace_order_items**  
`id, order_id, uid, vendor_id, product_name, vendor, unit_price, quantity, line_total`

**marketplace_reviews**  
`id, uid, customer_name, rating, comment, created_at`

**coupons**  
`id, code, discount_type, discount_value, min_order_value, max_uses, used_count, active, created_at`

**traceability_events**  
`id, uid, event_type, description, actor, location, order_no, event_time`

**component_inspections**  
`id, uid, inspector_name, inspection_date, status, findings, notes, risk_level, next_inspection_date, created_at`

**risk_assessments**  
`id, uid, risk_level, risk_score, factors, recommendation, assessed_at`

**shipments**  
`id, order_id, order_no, vendor_id, courier, tracking_number, status, estimated_delivery, shipped_at, delivered_at, created_at`

**audit_log**  
`id, action, entity_type, entity_id, actor, details, ip_address, created_at`

**inventory_history**  
`id, uid, change_type, quantity_before, quantity_change, quantity_after, reason, order_no, actor, created_at`

### vendors.db

**vendors**  
`id, company_name, contact_person, email, password, phone, address, railway_zone, railway_division, supply_region, registration_date, vendor_risk, failure_count`

**vendor_reviews**  
`id, vendor_id, reviewer_name, railway_unit, rating, comment, created_at`

**railway_divisions**  
`id, name, code, zone, region, hq_location, status, contact_email, created_at`

---

## API Reference

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| GET | `/` | Landing page | — |
| GET | `/shop` | Marketplace listing | — |
| GET | `/component/<uid>` | Digital passport | — |
| GET | `/cart` | Shopping cart | — |
| GET/POST | `/buyer/register` | Create buyer account | — |
| GET/POST | `/buyer/login` | Buyer login | — |
| GET | `/buyer/account` | Buyer order history | Buyer |
| POST | `/cart/add/<uid>` | Add to cart | — |
| POST | `/cart/update` | Update quantities | — |
| POST | `/cart/remove/<uid>` | Remove from cart | — |
| POST | `/checkout` | Place order | — |
| GET | `/orders/<order_no>` | Order confirmation | — |
| POST | `/track` | Track order by number + email | — |
| POST | `/reviews/<uid>` | Submit product review | — |
| GET | `/wishlist` | View wishlist | — |
| POST | `/wishlist/toggle/<uid>` | Add/remove wishlist | — |
| GET | `/invoice/<order_no>` | Download PDF invoice | — |
| POST | `/apply-coupon` | Validate coupon code | — |
| GET | `/vendor/<id>` | Vendor public profile | — |
| POST | `/vendor/<id>/reviews` | Submit vendor review | — |
| GET | `/vendor/dashboard` | Vendor dashboard | Vendor |
| POST | `/entry` | Register component | Vendor |
| POST | `/products/<uid>/marketplace` | Update listing settings | Vendor |
| POST | `/vendor/order/<order_no>/status` | Update order status | Vendor |
| POST | `/component/<uid>/inspect` | Record inspection | Vendor |
| GET | `/admin` | Admin dashboard | Admin |
| GET | `/admin/intelligence` | Intelligence dashboard | Admin |
| GET | `/admin/audit` | Audit log | Admin |
| GET | `/api/component/<uid>/traceability` | Traceability JSON | — |
| GET | `/api/component/<uid>/risk` | Risk assessment JSON | — |
| GET | `/api/component/<uid>/warranty` | Warranty status JSON | — |
| GET | `/api/divisions` | Railway divisions JSON | — |

---

## Getting Started

### Prerequisites
- Python 3.10+
- ESP32 with Wi-Fi firmware (optional, for engraving)

### Installation

```bash
git clone https://github.com/Debanshu2005/GaugeMarket.git
cd GaugeMarket

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### Seed Demo Data

```bash
python scripts/create_demo_data.py
```

### Run

```bash
python app.py
```

Open http://localhost:5000

### Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

---

## Project Structure

```
GaugeMarket/
├── app.py                      # Flask application — all routes and business logic
├── ai_module.py                # Risk model (scikit-learn + keyword hybrid)
├── tms.py                      # Track Management System sync
├── udm.py                      # Unified Data Module sync
├── requirements.txt
├── vercel.json
├── .env.example
├── templates/
│   ├── landing.html            # Landing page
│   ├── index.html              # Component registration form
│   ├── all.html                # Inventory table
│   ├── view.html               # Component detail + marketplace settings
│   ├── component_passport.html # Digital passport (public)
│   ├── shop.html               # Marketplace
│   ├── cart.html               # Shopping cart
│   ├── checkout.html           # Checkout flow
│   ├── order_success.html      # Order confirmation
│   ├── track.html              # Order tracking
│   ├── wishlist.html           # Wishlist
│   ├── vendor_registration.html
│   ├── vendor_login.html
│   ├── vendor_dashboard.html
│   ├── vendor_details.html
│   ├── admin.html              # Admin dashboard
│   ├── admin_intelligence.html # Intelligence & risk overview
│   └── admin_audit.html        # Audit log
├── static/
│   ├── rail.css                # Design system
│   └── image/
│       └── rail.png            # Railway logo
└── scripts/
    └── create_demo_data.py     # Demo data seed script
```

---

## Deployment

Deployed on **Vercel** serverless.  
See [VERCEL_DEPLOYMENT.md](VERCEL_DEPLOYMENT.md) for full instructions.

---

## License

MIT License © 2025 [Debanshu2005](https://github.com/Debanshu2005)
