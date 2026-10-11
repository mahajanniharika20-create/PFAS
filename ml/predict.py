"""Load the saved model and rank faculty candidates for a subject."""
import joblib
import pandas as pd
from ml.config import MODEL_PATH
from ml.features import build_feature_frame
from ml.constraints import passes_basic_constraints

def predict_suitability(candidate: dict) -> float:
    if not MODEL_PATH.exists():
        raise FileNotFoundError("Trained model not found. Run: python ml/train_model.py")
    model = joblib.load(MODEL_PATH)
    features = build_feature_frame(candidate)
    score = float(model.predict(features)[0])
    return round(max(0.0, min(100.0, score)), 2)

def rank_candidates(candidates: list[dict], required_hours: float = 0) -> list[dict]:
    """Return candidates sorted by model score; infeasible candidates are marked, not silently removed."""
    if not MODEL_PATH.exists():
        raise FileNotFoundError("Trained model not found. Run: python ml/train_model.py")
    model = joblib.load(MODEL_PATH)
    feature_frame = build_feature_frame(candidates)
    scores = model.predict(feature_frame)
    results = []
    for candidate, raw_score in zip(candidates, scores):
        allowed, reason = passes_basic_constraints(candidate, required_hours)
        results.append({
            "faculty_id": candidate.get("faculty_id"),
            "faculty_name": candidate.get("faculty_name", "Unknown"),
            "suitability_score": round(max(0.0, min(100.0, float(raw_score))), 2),
            "eligible_under_basic_checks": allowed,
            "constraint_message": reason,
        })
    return sorted(results, key=lambda item: (item["eligible_under_basic_checks"], item["suitability_score"]), reverse=True)

if __name__ == "__main__":
    sample_candidates = [
        {
            "faculty_id": 1, "faculty_name": "Faculty A",
            "faculty_domain": "AI", "subject_domain": "AI",
            "primary_preference": "AI", "secondary_preference": "Data Science",
            "experience_years": 8, "related_subject_experience_years": 5,
            "current_weekly_hours": 6, "weekly_hour_cap": 14, "available": True,
        },
        {
            "faculty_id": 2, "faculty_name": "Faculty B",
            "faculty_domain": "Networks", "subject_domain": "AI",
            "primary_preference": "Security", "secondary_preference": "AI",
            "experience_years": 12, "related_subject_experience_years": 2,
            "current_weekly_hours": 13, "weekly_hour_cap": 14, "available": True,
        },
    ]
    print(pd.DataFrame(rank_candidates(sample_candidates, required_hours=2)).to_string(index=False))
