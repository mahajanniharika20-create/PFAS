"""Feature validation and construction shared by training and inference."""
import pandas as pd
from ml.config import FEATURE_COLUMNS

def build_feature_frame(records) -> pd.DataFrame:
    """Convert a dict or list of dicts into a validated feature DataFrame."""
    if isinstance(records, dict):
        records = [records]
    frame = pd.DataFrame(records)
    missing = [col for col in FEATURE_COLUMNS if col not in frame.columns]
    if missing:
        raise ValueError(f"Missing required feature(s): {', '.join(missing)}")

    frame = frame[FEATURE_COLUMNS].copy()
    numeric = [
        "experience_years",
        "related_subject_experience_years",
        "current_weekly_hours",
        "weekly_hour_cap",
    ]
    for col in numeric:
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    if (frame[numeric] < 0).any().any():
        raise ValueError("Numeric features cannot be negative.")
    if (frame["current_weekly_hours"] > frame["weekly_hour_cap"]).any():
        raise ValueError("Current weekly hours cannot exceed the weekly cap.")
    return frame
