"""Shared settings for the PFAS ML demonstration."""
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "ml" / "data"
ARTIFACT_DIR = ROOT_DIR / "ml" / "artifacts"
REPORT_DIR = ROOT_DIR / "ml" / "reports"

DATASET_PATH = DATA_DIR / "synthetic_faculty_subject_suitability.csv"
MODEL_PATH = ARTIFACT_DIR / "suitability_pipeline.joblib"
METRICS_PATH = REPORT_DIR / "evaluation_metrics.json"

RANDOM_STATE = 42

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
