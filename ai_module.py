from datetime import datetime, timedelta
import sqlite3
from typing import Dict, Optional, Union, Any
import logging
import logging.handlers
import time
import os
import tempfile
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline
import joblib

RUNTIME_DIR = tempfile.gettempdir() if os.environ.get("VERCEL") else os.getcwd()
LOG_FILE = os.path.join(RUNTIME_DIR, "fittings_ai.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

MODEL_PATH = Path(os.environ.get("RISK_MODEL_PATH", os.path.join(RUNTIME_DIR, "risk_model.pkl")))

TRAINING_DATA = [
    ("leak detected", "High"),
    ("corrosion visible", "High"),
    ("crack found", "High"),
    ("wear and tear", "Medium"),
    ("loose connection", "Medium"),
    ("looking good", "Low"),
    ("perfect condition", "Low"),
    ("minor wear", "Medium"),
    ("severe damage", "High"),
    ("no issues", "Low"),
    ("minor corrosion", "Medium"),
    ("small crack", "Medium"),
    ("significant wear", "High"),
    ("major defect", "High"),
    ("operating normally", "Low"),
]

RISK_KEYWORDS: Dict[str, str] = {
    "leak": "High",
    "corrosion": "High",
    "crack": "High",
    "cracking": "High",
    "deformation": "High",
    "damage": "High",
    "fault": "High",
    "failure": "High",
    "broken": "High",
    "severe": "High",
    "significant": "High",
    "major": "High",
    "wear": "Medium",
    "loose": "Medium",
    "defect": "Medium",
    "issue": "Medium",
    "problem": "Medium",
    "rust": "Medium",
    "pitting": "Medium",
    "erosion": "Medium",
    "minor": "Medium",
    "ok": "Low",
    "good": "Low",
    "fine": "Low",
    "perfect": "Low",
    "working": "Low",
    "functional": "Low",
    "operational": "Low",
}

RISK_ORDER = {"Low": 0, "Medium": 1, "High": 2}


def highest_risk(*risks: str) -> str:
    return max(
        (r for r in risks if r in RISK_ORDER),
        key=lambda r: RISK_ORDER[r],
        default="Low",
    )


def _initialize_ai_model() -> Optional[object]:
    if MODEL_PATH.exists():
        try:
            return joblib.load(MODEL_PATH)
        except Exception as e:
            logger.error(f"Error loading risk model: {e}")

    logger.info("Training new AI risk model")
    try:
        X, y = zip(*TRAINING_DATA)
        model = make_pipeline(TfidfVectorizer(), MultinomialNB())
        model.fit(X, y)
        joblib.dump(model, MODEL_PATH)
        return model
    except Exception as e:
        logger.error(f"Error training risk model: {e}")
        return None


AI_MODEL = _initialize_ai_model()


def notes_risk_level(notes: Optional[str]) -> str:
    """Determine risk level from inspection notes using rule-based + AI hybrid."""
    if not notes or not notes.strip():
        return "Low"

    lower = notes.lower()
    rule_risk = "Low"
    for keyword, risk in RISK_KEYWORDS.items():
        if keyword in lower:
            if risk == "High":
                return "High"
            if risk == "Medium":
                rule_risk = "Medium"

    if AI_MODEL:
        try:
            ai_risk = AI_MODEL.predict([notes])[0]
            return highest_risk(rule_risk, ai_risk)
        except Exception as e:
            logger.warning(f"AI model prediction failed: {e}")

    return rule_risk


def get_risk_level(payload: Optional[Union[Dict[str, Any], str]] = None) -> str:
    """Return Low / Medium / High risk for a fitting payload dict or a notes string."""
    if isinstance(payload, str):
        return notes_risk_level(payload)
    if not payload:
        return "Low"

    notes = str(payload.get("notes") or "")
    combined = " ".join(
        str(payload.get(k) or "")
        for k in ("uid", "item_type", "vendor", "lot", "notes")
    )
    return highest_risk(notes_risk_level(notes), notes_risk_level(combined))


def update_all_risks(db_path: str = "fittings.db") -> int:
    """Refresh risk columns for all fittings. Returns number of rows updated."""
    updated = 0
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT uid, item_type, vendor, lot, notes FROM fittings"
            ).fetchall()
            for row in rows:
                payload = {k: row[k] for k in row.keys()}
                risk = get_risk_level(payload)
                conn.execute(
                    "UPDATE fittings SET risk=?, risk_flag=? WHERE uid=?",
                    (risk, 1 if risk == "High" else 0, row["uid"]),
                )
                updated += 1
            conn.commit()
    except sqlite3.Error as e:
        logger.warning(f"Risk refresh skipped: {e}")
    return updated


class QRAnomalyDetector:
    """Compatibility shim used by the Flask app."""

    def assess(self, payload: Optional[Dict[str, Any]] = None) -> str:
        return get_risk_level(payload or {})

    def predict(self, payload: Optional[Dict[str, Any]] = None) -> str:
        return self.assess(payload)
