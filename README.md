# QR Demo Project

This is a Python Flask web application designed for generating, scanning, and managing QR codes associated with vendor items and fittings. It includes an integrated AI module to calculate vendor risks and automate assessments based on supply details.

## Project Structure

- **`app.py`**: The main Flask application, containing routes and core business logic.
- **`ai_module.py`**: Handles risk model predictions and calculations.
- **`tms.py` & `udm.py`**: Integration scripts for third-party syncs (TMS/UDM).
- **`scripts/`**: Utility and one-off Python scripts.
- **`templates/`**: HTML templates for the web interface.
- **`static/`**: Static assets, including generated QR codes and G-code outputs.
- **Local DBs**: Local SQLite databases (`fittings.db`, `vendors.db`) are ignored in Git and generated at runtime if missing.

## Getting Started

### Requirements
- Python 3.9+
- See `requirements.txt` for dependencies.

### Installation

1. Create a virtual environment and activate it:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the app:
   ```bash
   flask run
   # Or directly:
   python app.py
   ```
4. Access the web UI at `https://rail-qr-marketplace.vercel.app/`.

## Deployment

This repository is pre-configured for Vercel deployment. See [VERCEL_DEPLOYMENT.md](VERCEL_DEPLOYMENT.md) for detailed deployment instructions.
