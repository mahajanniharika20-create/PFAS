"""Feature extraction, text normalization, and record harmonization for PFAS."""
import re
from typing import Any
import pandas as pd
from ml.config import FEATURE_COLUMNS, APPROVED_DOMAIN_ALIASES


def normalize_text(text: Any) -> str:
    """Normalize text by trimming whitespace, lowercasing, and collapsing spaces."""
    if text is None:
        return ""
    cleaned = re.sub(r"\s+", " ", str(text).strip().lower())
    return cleaned


def normalize_domain(domain: Any) -> str:
    """Normalize a domain string using strict, institution-approved aliases.

    NOTE: Distinct domains (e.g. Data Science vs Database) are strictly separate.
    No cross-domain equivalence is inferred.
    """
    clean = normalize_text(domain)
    return APPROVED_DOMAIN_ALIASES.get(clean, clean)


def normalize_faculty_record(record: Any) -> dict[str, Any]:
    """Convert an arbitrary faculty representation (dict or ORM model) into a clean dictionary."""
    if hasattr(record, "__dict__"):
        # Handle SQLAlchemy model instance or general object
        data = {
            k: v
            for k, v in record.__dict__.items()
            if not k.startswith("_")
        }
        # Fallback to getattr for properties or non-dict model attributes
        for prop in [
            "id",
            "name",
            "faculty_id",
            "faculty_name",
            "department",
            "domain",
            "faculty_domain",
            "section",
            "weekly_hours",
            "weekly_hour_cap",
            "current_weekly_hours",
            "assigned_subject",
            "available",
            "on_leave",
            "preferred_subject",
            "preferred_time_slot",
            "qualification",
            "assigned_slots",
            "busy_slots",
            "target_section",
            "allocated_section",
        ]:
            if prop not in data and hasattr(record, prop):
                data[prop] = getattr(record, prop)
    elif isinstance(record, dict):
        data = dict(record)
    else:
        raise TypeError(f"Unsupported record type: {type(record)}")

    # Harmonize common field name variations
    faculty_id = data.get("faculty_id", data.get("id"))
    faculty_name = data.get("faculty_name", data.get("name", "Unknown"))
    raw_domain = data.get("faculty_domain", data.get("domain", ""))
    norm_domain = normalize_domain(raw_domain)

    current_hours = data.get("current_weekly_hours", data.get("weekly_hours"))
    cap_hours = data.get("weekly_hour_cap")
    section_val = data.get("section")
    section = str(section_val).strip() if section_val is not None and str(section_val).strip() != "" else None
    alloc_sec_val = data.get("allocated_section", data.get("target_section"))
    allocated_section = str(alloc_sec_val).strip() if alloc_sec_val is not None and str(alloc_sec_val).strip() != "" else None
    target_sec_val = data.get("target_section")
    target_section = str(target_sec_val).strip() if target_sec_val is not None and str(target_sec_val).strip() != "" else None

    return {
        "faculty_id": faculty_id,
        "faculty_name": str(faculty_name).strip(),
        "department": str(data.get("department", "")).strip(),
        "domain": norm_domain,
        "raw_domain": str(raw_domain).strip(),
        "section": section,
        "allocated_section": allocated_section,
        "target_section": target_section,
        "current_weekly_hours": current_hours,
        "weekly_hour_cap": cap_hours,
        "available": data.get("available"),
        "on_leave": data.get("on_leave"),
        "assigned_subject": data.get("assigned_subject"),
        "preferred_subject": data.get("preferred_subject", data.get("primary_preference")),
        "preferred_time_slot": data.get("preferred_time_slot"),
        "qualification": data.get("qualification", data.get("qualifications")),
        "assigned_slots": data.get("assigned_slots", data.get("busy_slots", [])),
        "raw_data": data,
    }



def build_feature_frame(records: Any) -> pd.DataFrame:
    """Convert a dict or list of dicts into a validated feature DataFrame.

    Maintained for compatibility with historical offline tools and tests.
    """
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
