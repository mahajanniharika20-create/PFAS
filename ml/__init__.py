"""Machine Learning and Allocation Recommendation Module for PFAS.

Provides transparent, rule-based faculty recommendation and constraint evaluation.
"""
from ml.config import (
    DEFAULT_POLICY_WEIGHTS,
    STATUS_ELIGIBLE,
    STATUS_NEEDS_REVIEW,
    STATUS_INELIGIBLE,
)
from ml.constraints import (
    evaluate_constraints,
    passes_basic_constraints,
    slots_collide,
    parse_slot_interval,
    parse_time_to_minutes,
)
from ml.features import (
    normalize_domain,
    normalize_faculty_record,
    normalize_text,
    build_feature_frame,
)
from ml.engine import recommend_faculty, score_candidate
from ml.predict import predict_suitability, rank_candidates
from ml.generator import (
    generate_timetable,
    validate_timetable,
    format_master_grid,
    format_section_grid,
)

__all__ = [
    "DEFAULT_POLICY_WEIGHTS",
    "STATUS_ELIGIBLE",
    "STATUS_NEEDS_REVIEW",
    "STATUS_INELIGIBLE",
    "evaluate_constraints",
    "passes_basic_constraints",
    "slots_collide",
    "parse_slot_interval",
    "parse_time_to_minutes",
    "normalize_domain",
    "normalize_faculty_record",
    "normalize_text",
    "build_feature_frame",
    "recommend_faculty",
    "score_candidate",
    "predict_suitability",
    "rank_candidates",
    "generate_timetable",
    "validate_timetable",
    "format_master_grid",
    "format_section_grid",
]

