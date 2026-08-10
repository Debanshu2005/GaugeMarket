from datetime import datetime, timedelta
import sqlite3
import qrcode
from PIL import Image
import cv2
import numpy as np
from typing import Dict, Optional, Union, List, Tuple, Any
import logging
import logging.handlers
import re
import time
import os
import tempfile
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline
from sklearn.exceptions import NotFittedError
import joblib
from contextlib import contextmanager

# Configure logging with more detailed format and rotation
RUNTIME_DIR = tempfile.gettempdir() if os.environ.get("VERCEL") else os.getcwd()
LOG_FILE = os.path.join(RUNTIME_DIR, "fittings_ai.log")
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10MB
BACKUP_COUNT = 3

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=MAX_LOG_SIZE,
            backupCount=BACKUP_COUNT
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# AI Model Constants
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
    ("operating normally", "Low")
]

# Enhanced Risk assessment constants with more keywords and severity levels
RISK_KEYWORDS: Dict[str, str] = {
    "leak": "High",
    "corrosion": "High",
    "crack": "High",
    "wear": "Medium",
    "loose": "Medium",
    "ok": "Low",
    "good": "Low",
    "fine": "Low",
    "perfect": "Low",
    "perfect fit": "Low",
    "perfect fittings": "Low",
    "damage": "High",
    "defect": "Medium",
    "issue": "Medium",
    "problem": "Medium",
    "fault": "High",
    "failure": "High",
    "broken": "High",
    "working": "Low",
    "functional": "Low",
    "operational": "Low",
    "rust": "Medium",
    "pitting": "Medium",
    "erosion": "Medium",
    "deformation": "High",
    "cracking": "High",
    "severe": "High",
    "minor": "Medium",
    "significant": "High",
    "major": "High"
}

@contextmanager
def database_connection(db_path: str = "fittings.db", max_retries: int = 3, timeout: float = 10.0):
    """Context manager for SQLite database connections with enhanced error handling and validation"""
    conn = None
    try:
        retries = 0
        last_error = None

        while retries < max_retries:
            try:
                conn = sqlite3.connect(
                    db_path,
                    timeout=timeout,
                    isolation_level=None  # Enable autocommit mode
                )
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA journal_mode = WAL")  # Better concurrency
                conn.execute("PRAGMA synchronous = NORMAL")  # Balance between safety and speed

                # Validate connection by executing a simple query
                conn.execute("SELECT 1")
                conn.fetchone()
                yield conn
                return
            except sqlite3.Error as e:
                retries += 1
                last_error = e
                logger.warning(f"Database connection attempt {retries} failed: {e}")
                if retries < max_retries:
                    time.sleep(1)  # Wait before retrying

        logger.error(f"Failed to connect to database after {max_retries} attempts")
        raise last_error or sqlite3.Error("Unknown database connection error")
    except Exception as e:
        logger.error(f"Unexpected error in database connection: {e}")
        raise
    finally:
        if conn:
            try:
                if conn:
                    conn.rollback()  # Ensure no pending transactions
                    conn.close()
            except sqlite3.Error as e:
                logger.error(f"Error closing database connection: {e}")

# Enhanced Risk assessment constants with more keywords and severity levels
RISK_KEYWORDS: Dict[str, str] = {
    "leak": "High",
    "corrosion": "High",
    "crack": "High",
    "wear": "Medium",
    "loose": "Medium",
    "ok": "Low",
    "good": "Low",
    "fine": "Low",
    "perfect": "Low",
    "perfect fit": "Low",
    "perfect fittings": "Low",
    "damage": "High",
    "defect": "Medium",
    "issue": "Medium",
    "problem": "Medium",
    "fault": "High",
    "failure": "High",
    "broken": "High",
    "working": "Low",
    "functional": "Low",
    "operational": "Low"
}

# Database connection management with retry logic and connection validation
class DatabaseConnection:
    """Context manager for SQLite database connections with enhanced error handling and validation"""

    def __init__(self, db_path: str = "fittings.db", max_retries: int = 3, timeout: float = 10.0):
        self.db_path = db_path
        self.max_retries = max_retries
        self.timeout = timeout

    def __enter__(self):
        """Establish database connection with retry logic and validation"""
        retries = 0
        last_error = None

        while retries < self.max_retries:
            try:
                self.conn = sqlite3.connect(
                    self.db_path,
                    timeout=self.timeout,
                    isolation_level=None  # Enable autocommit mode
                )
                self.conn.execute("PRAGMA foreign_keys = ON")
                self.conn.execute("PRAGMA journal_mode = WAL")  # Better concurrency
                self.conn.execute("PRAGMA synchronous = NORMAL")  # Balance between safety and speed

                # Validate connection by executing a simple query
                self.conn.execute("SELECT 1")
                self.conn.fetchone()
                return self.conn
            except sqlite3.Error as e:
                retries += 1
                last_error = e
                logger.warning(f"Database connection attempt {retries} failed: {e}")
                if retries < self.max_retries:
                    time.sleep(1)  # Wait before retrying

        logger.error(f"Failed to connect to database after {self.max_retries} attempts")
        raise last_error or sqlite3.Error("Unknown database connection error")

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close database connection with proper error handling"""
        if hasattr(self, 'conn'):
            try:
                if self.conn:
                    self.conn.rollback()  # Ensure no pending transactions
                    self.conn.close()
            except sqlite3.Error as e:
                logger.error(f"Error closing database connection: {e}")

def initialize_ai_model() -> Optional[MultinomialNB]:
    """Initialize or load the AI risk assessment model"""
    if os.path.exists(MODEL_PATH):
        try:
            return joblib.load(MODEL_PATH)
        except Exception as e:
            logger.error(f"Error loading model: {e}")
            return None

    logger.info("Training new AI model")
    try:
        X, y = zip(*TRAINING_DATA)
        model = make_pipeline(TfidfVectorizer(), MultinomialNB())
        model.fit(X, y)
        joblib.dump(model, MODEL_PATH)
        return model
    except Exception as e:
        logger.error(f"Error training model: {e}")
        return None

# Global model instance
AI_MODEL = initialize_ai_model()

RISK_ORDER = {"Low": 0, "Medium": 1, "High": 2}

def highest_risk(*risks: str) -> str:
    return max((risk for risk in risks if risk in RISK_ORDER), key=lambda risk: RISK_ORDER[risk], default="Low")

def notes_risk_level(notes: Optional[str]) -> str:
    """
    Determine risk level based on inspection notes using both rule-based and AI approaches
    Returns the higher risk level between the two methods
    """
    if not notes:
        return "Low"

    # Rule-based approach
    rule_based_risk = "Low"
    for keyword, risk in RISK_KEYWORDS.items():
        if keyword in notes.lower():
            if risk == "High":
                return "High"
            elif risk == "Medium" and rule_based_risk != "High":
                rule_based_risk = "Medium"

    # AI-based approach
    if AI_MODEL:
        try:
            ai_risk = AI_MODEL.predict([notes])[0]
            return highest_risk(rule_based_risk, ai_risk)
        except Exception as e:
            logger.warning(f"AI model prediction failed: {e}")

    return rule_based_risk

class QRAnomalyDetector:
    """Compatibility detector used by the Flask app."""

    def assess(self, payload: Optional[Dict[str, Any]] = None) -> str:
        return get_risk_level(payload or {})

    def predict(self, payload: Optional[Dict[str, Any]] = None) -> str:
        return self.assess(payload)

def get_risk_level(payload: Optional[Union[Dict[str, Any], str]] = None) -> str:
    """Return Low, Medium, or High risk for a fitting payload or notes string."""
    if isinstance(payload, str):
        return notes_risk_level(payload)
    if not payload:
        return "Low"

    notes = str(payload.get("notes") or "")
    combined = " ".join(
        str(payload.get(key) or "")
        for key in ("uid", "item_type", "vendor", "lot", "notes")
    )
    return highest_risk(notes_risk_level(notes), notes_risk_level(combined))

def update_all_risks(db_path: str = "fittings.db") -> int:
    """Refresh risk columns for all fittings and return the number of updated rows."""
    updated = 0
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT uid, item_type, vendor, lot, notes FROM fittings").fetchall()
            for row in rows:
                payload = {key: row[key] for key in row.keys()}
                risk = get_risk_level(payload)
                conn.execute(
                    "UPDATE fittings SET risk=?, risk_flag=? WHERE uid=?",
                    (risk, 1 if risk == "High" else 0, row["uid"])
                )
                updated += 1
            conn.commit()
    except sqlite3.Error as e:
        logger.warning(f"Risk refresh skipped: {e}")
    return updated
