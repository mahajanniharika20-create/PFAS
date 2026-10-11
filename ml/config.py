"""Shared configuration for the PFAS recommendation and allocation engine."""
from pathlib import Path

# --- File and Directory Paths ---
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "ml" / "data"
ARTIFACT_DIR = ROOT_DIR / "ml" / "artifacts"
REPORT_DIR = ROOT_DIR / "ml" / "reports"

DATASET_PATH = DATA_DIR / "synthetic_faculty_subject_suitability.csv"
MODEL_PATH = ARTIFACT_DIR / "suitability_pipeline.joblib"
METRICS_PATH = REPORT_DIR / "evaluation_metrics.json"

RANDOM_STATE = 42

# --- Historical / Reference Feature Definitions ---
NUMERIC_FEATURES = [
    "experience_years",
    "related_subject_experience_years",
    "current_weekly_hours",
    "weekly_hour_cap",
]
CATEGORICAL_FEATURES = [
    "faculty_domain",
    "subject_domain",
    "primary_preference",
    "secondary_preference",
]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES
TARGET_COLUMN = "suitability_score"

# --- Eligibility Status Constants ---
STATUS_ELIGIBLE = "ELIGIBLE"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_INELIGIBLE = "INELIGIBLE"

# ==============================================================================
# INITIAL INSTITUTIONAL POLICY WEIGHTS
# ------------------------------------------------------------------------------
# NOTE: These weights represent initial administrative policy preferences,
# NOT parameters learned from empirical machine learning model training.
# They are fully configurable to reflect institutional priorities.
# ==============================================================================
DEFAULT_POLICY_WEIGHTS = {
    "domain_match": 40.0,       # Alignment between faculty domain and subject domain
    "subject_preference": 30.0,  # Alignment with faculty's explicit preferred subject
    "slot_preference": 15.0,     # Alignment with faculty's preferred time slot
    "capacity_balance": 15.0,    # Preference for faculty with unallocated weekly hours
}

# ==============================================================================
# APPROVED DOMAIN ALIASES
# ------------------------------------------------------------------------------
# Explicit, institution-approved syntactic aliases only.
# NOTE: Distinct domains (such as Data Science and Database) are strictly separate
# and must NEVER be treated as equivalent aliases.
# ==============================================================================
APPROVED_DOMAIN_ALIASES = {
    "ai/ml": "ai",
    "ai": "ai",
    "artificial intelligence": "ai",
    "machine learning": "ai",
    "deep learning": "ai",
    "ds": "data science",
    "data science": "data science",
    "database": "database",
    "db": "database",
    "dbms": "database",
    "database systems": "database",
    "networks": "networks",
    "computer networks": "networks",
    "networking": "networks",
    "web development": "web development",
    "web dev": "web development",
    "algorithms": "algorithms",
    "data structures": "algorithms",
    "security": "security",
    "cybersecurity": "security",
}
