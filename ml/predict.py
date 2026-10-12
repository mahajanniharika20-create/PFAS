"""Faculty suitability prediction and candidate ranking interface.

This module provides backward-compatible ranking and scoring entry points,
delegating to the transparent rule-based allocation engine.
It does NOT load or depend on serialized model artifacts (.joblib).
"""
from typing import Any
from ml.engine import recommend_faculty, score_candidate
from ml.features import normalize_faculty_record


def predict_suitability(
    candidate: dict[str, Any],
    subject: dict[str, Any] | str | None = None,
) -> float:
    """Calculate the transparent suitability score (0–100) for a faculty candidate.

    Zero dependency on .joblib files.
    """
    if not candidate:
        return 0.0
    cand = normalize_faculty_record(candidate)

    # If subject is not provided, infer from candidate record
    if subject is None:
        subject_dict = {
            "name": cand.get("preferred_subject") or cand.get("assigned_subject") or "",
            "domain": cand.get("raw_data", {}).get("subject_domain", cand.get("domain", "")),
        }
    elif isinstance(subject, str):
        subject_dict = {"name": subject, "domain": ""}
    else:
        subject_dict = subject

    score, _, _ = score_candidate(cand, subject_dict)
    return round(max(0.0, min(100.0, float(score))), 2)


def rank_candidates(
    candidates: list[dict[str, Any]],
    required_hours: float = 0,
    subject: dict[str, Any] | str | None = None,
    existing_schedule: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Rank faculty candidates for allocation while preserving backward-compatible return schema.

    Infeasible candidates and candidates with unverified information (NEEDS_REVIEW)
    are clearly identified and not silently treated as confirmed eligible.
    """
    if not candidates:
        return []

    # If subject was not explicitly passed, infer from the first candidate's subject_domain
    if subject is None:
        first_record = candidates[0]
        subj_domain = first_record.get("subject_domain", "")
        subj_name = first_record.get("subject_name", first_record.get("assigned_subject", ""))
        subject = {
            "name": subj_name,
            "domain": subj_domain,
            "required_hours": required_hours,
        }

    return recommend_faculty(
        candidates=candidates,
        subject=subject,
        required_hours=required_hours,
        existing_schedule=existing_schedule,
    )



if __name__ == "__main__":
    sample_candidates = [
        {
            "faculty_id": 1,
            "faculty_name": "Faculty A",
            "faculty_domain": "AI",
            "subject_domain": "AI",
            "primary_preference": "AI",
            "secondary_preference": "Data Science",
            "experience_years": 8,
            "related_subject_experience_years": 5,
            "current_weekly_hours": 6,
            "weekly_hour_cap": 14,
            "available": True,
        },
        {
            "faculty_id": 2,
            "faculty_name": "Faculty B",
            "faculty_domain": "Networks",
            "subject_domain": "AI",
            "primary_preference": "Security",
            "secondary_preference": "AI",
            "experience_years": 12,
            "related_subject_experience_years": 2,
            "current_weekly_hours": 13,
            "weekly_hour_cap": 14,
            "available": True,
        },
    ]
    ranked = rank_candidates(sample_candidates, required_hours=2)
    for r in ranked:
        print(f"[{r['eligibility_status']}] {r['faculty_name']}: Score={r['suitability_score']} - {r['constraint_message']}")
