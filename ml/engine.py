"""Transparent, rule-based recommendation and allocation engine for PFAS.

This module scores and ranks candidate faculty members for subject allocation based on
explicit institutional policy weights and verifiable constraints.

It does NOT use synthetic training data or serialized ML artifacts.
Suitability scoring is kept strictly distinct from eligibility determinations.
Candidates with unverified evidence are marked as NEEDS_REVIEW, never as confirmed eligible.
"""
import math
from typing import Any
from ml.config import (
    DEFAULT_POLICY_WEIGHTS,
    STATUS_ELIGIBLE,
    STATUS_NEEDS_REVIEW,
    STATUS_INELIGIBLE,
)
from ml.constraints import evaluate_constraints, slots_collide, parse_slot_interval
from ml.features import normalize_text, normalize_domain, normalize_faculty_record


def score_candidate(
    candidate: dict[str, Any],
    subject: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> tuple[float, dict[str, float], list[str]]:
    """Compute an explainable suitability score (0–100 scale) for a faculty candidate.

    Returns:
        (total_score, score_breakdown, warnings)
    """
    w = dict(DEFAULT_POLICY_WEIGHTS)
    if weights:
        w.update(weights)

    breakdown: dict[str, float] = {}
    warnings: list[str] = []

    # 1. Domain Relevance Score (Exact normalized match or approved aliases only)
    w_domain = float(w.get("domain_match", 40.0))
    subject_domain = normalize_domain(
        subject.get("domain") or subject.get("subject_domain") or subject.get("department") or ""
    )
    cand_domain = normalize_domain(
        candidate.get("domain") or candidate.get("faculty_domain") or ""
    )

    if not cand_domain:
        domain_score = 0.0
        warnings.append("Faculty domain is unspecified; awarded 0 domain points.")
    elif not subject_domain:
        domain_score = 0.0
        warnings.append("Subject domain is unspecified; awarded 0 domain points.")
    elif cand_domain == subject_domain:
        domain_score = w_domain
    else:
        domain_score = 0.0

    breakdown["domain_match"] = round(domain_score, 2)

    # 2. Subject Preference Score (Exact match or entry match in preference list; NO arbitrary substring)
    w_subj_pref = float(w.get("subject_preference", 30.0))
    subject_name = normalize_text(
        subject.get("name") or subject.get("subject") or subject.get("assigned_subject") or ""
    )
    raw_cand_pref = candidate.get("preferred_subject") or candidate.get("primary_preference") or ""

    if isinstance(raw_cand_pref, (list, tuple)):
        cand_prefs = [normalize_text(p) for p in raw_cand_pref if normalize_text(p)]
    elif isinstance(raw_cand_pref, str):
        cand_prefs = [normalize_text(p) for p in raw_cand_pref.split(",") if normalize_text(p)]
    else:
        cand_prefs = [normalize_text(raw_cand_pref)] if normalize_text(raw_cand_pref) else []

    if not cand_prefs:
        subj_pref_score = 0.0
    elif subject_name and subject_name in cand_prefs:
        subj_pref_score = w_subj_pref
    else:
        subj_pref_score = 0.0

    breakdown["subject_preference"] = round(subj_pref_score, 2)

    # 3. Time Slot Preference Score
    w_slot_pref = float(w.get("slot_preference", 15.0))
    subject_slot = normalize_text(
        subject.get("time_slot") or subject.get("preferred_time_slot") or subject.get("time") or ""
    )
    cand_slot = normalize_text(candidate.get("preferred_time_slot") or "")

    if "no preference" in cand_slot:
        slot_pref_score = round(w_slot_pref * 0.7, 2)
    elif subject_slot and cand_slot and subject_slot == cand_slot:
        slot_pref_score = w_slot_pref
    elif subject_slot and cand_slot:
        # Check interval overlap or daylight alignment
        collides, _ = slots_collide(subject_slot, cand_slot)
        if collides:
            slot_pref_score = w_slot_pref
        elif "morning" in cand_slot and any(
            h in subject_slot for h in ["08:", "09:", "10:", "11:", "12:00", "morning"]
        ):
            slot_pref_score = w_slot_pref
        elif "afternoon" in cand_slot and any(
            h in subject_slot for h in ["01:", "02:", "03:", "04:", "13:", "14:", "15:", "afternoon"]
        ):
            slot_pref_score = w_slot_pref
        else:
            slot_pref_score = 0.0
    else:
        slot_pref_score = 0.0

    breakdown["slot_preference"] = round(slot_pref_score, 2)

    # 4. Workload Capacity Balance Score
    w_capacity = float(w.get("capacity_balance", 15.0))
    cap_val = candidate.get("weekly_hour_cap")
    current_raw = candidate.get("current_weekly_hours", candidate.get("weekly_hours"))

    if cap_val is not None and current_raw is not None:
        try:
            cap = float(cap_val)
            current = float(current_raw)
            if not (math.isfinite(cap) and math.isfinite(current)):
                capacity_score = 0.0
                warnings.append("Non-finite workload numbers; capacity balance score set to 0.")
            elif cap <= 0:
                capacity_score = 0.0
                warnings.append("Workload cap is <= 0; capacity balance score set to 0.")
            elif current < 0:
                capacity_score = 0.0
                warnings.append("Current workload is negative; capacity balance score set to 0.")
            elif current >= cap:
                capacity_score = 0.0
            else:
                remaining_ratio = max(0.0, min(1.0, (cap - current) / cap))
                capacity_score = round(remaining_ratio * w_capacity, 2)
        except (ValueError, TypeError):
            capacity_score = 0.0
            warnings.append("Invalid workload numbers; capacity balance score set to 0.")
    else:
        capacity_score = 0.0
        if cap_val is None:
            warnings.append("Workload cap unknown; capacity balance score set to 0.")
        if current_raw is None:
            warnings.append("Current workload unknown; capacity balance score set to 0.")

    breakdown["capacity_balance"] = round(capacity_score, 2)

    total_score = round(max(0.0, min(100.0, float(sum(breakdown.values())))), 2)
    return total_score, breakdown, warnings


def resolve_duplicate_records(records: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """Conservatively resolve duplicate records for a faculty member.

    Ensures duplicate rows cannot silently discard leave status, higher workload,
    unavailability, or conflicting eligibility evidence.
    """
    if len(records) == 1:
        return records[0], []

    resolved = dict(records[0])
    warnings: list[str] = []

    # 1. Leave status: If ANY record indicates on_leave, conservative verdict is on_leave = True
    leave_statuses = [r.get("on_leave") for r in records if "on_leave" in r and r.get("on_leave") is not None]
    if any(str(lv).strip().lower() in ["true", "1", "yes"] or lv is True for lv in leave_statuses):
        resolved["on_leave"] = True
        if any(str(lv).strip().lower() in ["false", "0", "no"] or lv is False for lv in leave_statuses):
            warnings.append(
                "Notice: Conflicting on-leave status across duplicate records; conservatively enforced on-leave."
            )
    elif leave_statuses:
        resolved["on_leave"] = False

    # 2. Availability: If ANY record indicates unavailable, conservative verdict is available = False
    avail_statuses = [r.get("available") for r in records if "available" in r and r.get("available") is not None]
    if any(str(av).strip().lower() in ["false", "0", "no"] or av is False for av in avail_statuses):
        resolved["available"] = False
        if any(str(av).strip().lower() in ["true", "1", "yes"] or av is True for av in avail_statuses):
            warnings.append(
                "Notice: Conflicting availability status across duplicate records; conservatively enforced unavailable."
            )
    elif avail_statuses:
        resolved["available"] = True

    # 3. Workload: Take maximum current workload if conflicting
    hours_vals = []
    for r in records:
        h = r.get("current_weekly_hours")
        if h is None:
            h = r.get("weekly_hours")
        if h is not None:
            try:
                hf = float(h)
                if math.isfinite(hf):
                    hours_vals.append(hf)
            except (ValueError, TypeError):
                pass
    if hours_vals:
        max_h = max(hours_vals)
        resolved["current_weekly_hours"] = max_h
        resolved["weekly_hours"] = max_h
        if max(hours_vals) != min(hours_vals):
            warnings.append(
                f"Notice: Conflicting current weekly workload hours across duplicate records ({hours_vals}); "
                f"conservatively enforced maximum ({max_h}h)."
            )

    # 4. Workload cap: Take minimum workload cap if conflicting
    cap_vals = []
    for r in records:
        c = r.get("weekly_hour_cap")
        if c is not None:
            try:
                cf = float(c)
                if math.isfinite(cf):
                    cap_vals.append(cf)
            except (ValueError, TypeError):
                pass
    if cap_vals:
        min_c = min(cap_vals)
        resolved["weekly_hour_cap"] = min_c
        if max(cap_vals) != min(cap_vals):
            warnings.append(
                f"Notice: Conflicting weekly workload caps across duplicate records ({cap_vals}); "
                f"conservatively enforced minimum ({min_c}h)."
            )

    # 5. Assigned/busy slots: Conservative union of busy slots
    combined_busy = []
    for r in records:
        b = r.get("assigned_slots") or r.get("busy_slots") or []
        if isinstance(b, str):
            combined_busy.extend([s.strip() for s in b.split(",") if s.strip()])
        elif isinstance(b, (list, tuple)):
            combined_busy.extend([str(s).strip() for s in b if str(s).strip()])
    if combined_busy:
        seen_b = set()
        dedup_b = []
        for s in combined_busy:
            if s.lower() not in seen_b:
                seen_b.add(s.lower())
                dedup_b.append(s)
        resolved["assigned_slots"] = dedup_b

    # 6. Qualifications: Combine unique qualifications
    combined_quals = []
    for r in records:
        q = r.get("qualification") or r.get("qualifications")
        if q:
            if isinstance(q, (list, tuple)):
                combined_quals.extend([str(item).strip() for item in q if str(item).strip()])
            else:
                combined_quals.append(str(q).strip())
    if combined_quals:
        resolved["qualification"] = list(dict.fromkeys(combined_quals))

    # 7. Domain conflict check: withhold domain match on conflicting domain evidence
    domains = list({str(r.get("domain")).strip() for r in records if r.get("domain") and str(r.get("domain")).strip()})
    if len(domains) > 1:
        resolved["domain"] = None
        warnings.append(
            f"Notice: Conflicting domains across duplicate records ({domains}) for faculty member; domain matching withheld."
        )

    # 8. Section conflict check: invalidate inferred section on conflicting section evidence
    sections = list({str(r.get("section")).strip() for r in records if r.get("section") and str(r.get("section")).strip()})
    if len(sections) > 1:
        resolved["section"] = None
        warnings.append(
            f"Notice: Conflicting sections across duplicate records ({sections}) for faculty member; candidate section unverified."
        )

    # 9. Assigned subject conflict check
    assigned_subjs = list({str(r.get("assigned_subject")).strip() for r in records if r.get("assigned_subject") and str(r.get("assigned_subject")).strip()})
    if len(assigned_subjs) > 1:
        resolved["assigned_subject"] = None
        warnings.append(
            f"Notice: Conflicting assigned subjects across duplicate records ({assigned_subjs}) for faculty member."
        )

    return resolved, warnings


def recommend_faculty(
    candidates: list[Any],
    subject: dict[str, Any] | str,
    required_hours: float = 0,
    policy_weights: dict[str, float] | None = None,
    existing_schedule: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Recommend and rank faculty candidates for a given subject.

    Args:
        candidates: List of faculty records (dictionaries or ORM objects).
        subject: Subject details dictionary or subject name string.
        required_hours: Teaching hours required for the subject.
        policy_weights: Optional overrides for default institutional policy weights.
        existing_schedule: Optional existing schedule entries to enforce institutional timetable constraints.

    Returns:
        List of candidate ranking dictionaries ordered deterministically:
        1. Verified ELIGIBLE candidates (highest score first)
        2. Unverified NEEDS_REVIEW candidates (highest score first)
        3. Disqualified INELIGIBLE candidates (highest score first)
    """
    if not candidates:
        return []

    # Harmonize subject representation
    if isinstance(subject, str):
        subject_dict = {
            "name": subject,
            "subject": subject,
            "domain": "",
            "required_hours": required_hours,
        }
    elif isinstance(subject, dict):
        subject_dict = dict(subject)
        subj_name = (
            subject_dict.get("name")
            or subject_dict.get("subject")
            or subject_dict.get("assigned_subject")
            or ""
        )
        subject_dict["name"] = subj_name
        if "required_hours" not in subject_dict:
            subject_dict["required_hours"] = required_hours
    else:
        raise TypeError(f"Subject must be dict or str, got {type(subject)}")

    try:
        effective_hours = float(subject_dict.get("required_hours", required_hours))
    except (ValueError, TypeError):
        effective_hours = 0.0

    # Group candidate records by identity key (faculty_id or faculty_name)
    grouped_candidates: dict[tuple, list[dict[str, Any]]] = {}
    for raw_cand in candidates:
        cand_norm = normalize_faculty_record(raw_cand)
        fid = cand_norm.get("faculty_id")
        fname = cand_norm.get("faculty_name")
        cand_key = (
            ("id", str(fid).strip())
            if fid is not None
            else ("name", str(fname).strip() if fname else str(id(raw_cand)))
        )
        grouped_candidates.setdefault(cand_key, []).append(cand_norm)

    results: list[dict[str, Any]] = []

    for cand_key, group in grouped_candidates.items():
        cand, dup_warnings = resolve_duplicate_records(group)

        # 1. Evaluate verifiable constraints with optional timetable
        constraint_res = evaluate_constraints(
            cand,
            required_hours=effective_hours,
            subject=subject_dict,
            existing_schedule=existing_schedule,
        )

        # 2. Compute transparent suitability score
        score, breakdown, score_warnings = score_candidate(
            cand,
            subject=subject_dict,
            weights=policy_weights,
        )

        all_warnings = dup_warnings + score_warnings + [
            f"Missing evidence: {field}" for field in constraint_res["missing_fields"]
        ]
        if existing_schedule is None:
            all_warnings.append(
                "Global timetable not supplied; cross-faculty section collisions could not be verified."
            )

        proposed_slot_val = (
            subject_dict.get("preferred_time_slot")
            or subject_dict.get("time_slot")
            or subject_dict.get("slot")
            or subject_dict.get("time")
        )
        parsed_prop = parse_slot_interval(proposed_slot_val) if proposed_slot_val else {}
        has_usable_slot = (
            bool(proposed_slot_val and str(proposed_slot_val).strip())
            and parsed_prop.get("start_min") is not None
            and parsed_prop.get("end_min") is not None
            and parsed_prop["end_min"] > parsed_prop["start_min"]
        )
        if not has_usable_slot:
            all_warnings.append(
                "Warning: Proposed class has no usable time slot; timetable checks cannot be performed."
            )
            all_warnings.append(
                "Notice: Proposed subject has no scheduled time slot; timetable conflict checks could not be performed."
            )

        # Section resolution:
        # Priority 1: Explicitly requested section from the subject/allocation input
        req_section = subject_dict.get("section")
        if req_section is not None and str(req_section).strip() != "":
            allocated_section = str(req_section).strip()
        # Priority 2: Verified candidate intended section allocation
        elif cand.get("allocated_section") is not None and str(cand["allocated_section"]).strip() != "":
            allocated_section = str(cand["allocated_section"]).strip()
        elif cand.get("target_section") is not None and str(cand["target_section"]).strip() != "":
            allocated_section = str(cand["target_section"]).strip()

        # Priority 3: Candidate's section only if existing data establishes that the allocation matches their assigned subject
        elif (
            cand.get("assigned_subject")
            and normalize_text(cand.get("assigned_subject")) == normalize_text(subject_dict.get("name", ""))
            and cand.get("section") is not None
            and str(cand["section"]).strip() != ""
        ):
            allocated_section = str(cand["section"]).strip()
        else:
            allocated_section = None
            all_warnings.append(
                "Allocated section not specified in subject input or verified in candidate allocation data."
            )

        # Explicit safety: eligible_under_basic_checks is strictly True ONLY if confirmed ELIGIBLE
        is_fully_eligible = constraint_res["status"] == STATUS_ELIGIBLE

        # Safe non-NaN current hours for sorting
        try:
            ch_val = cand.get("current_weekly_hours")
            if ch_val is None:
                ch_val = cand.get("weekly_hours")
            ch = float(ch_val) if ch_val is not None else 0.0
            clean_curr_hours = ch if math.isfinite(ch) else 999999.0
        except (ValueError, TypeError):
            clean_curr_hours = 999999.0

        results.append({
            "faculty_id": cand["faculty_id"],
            "faculty_name": cand["faculty_name"],
            "allocated_section": allocated_section,
            "suitability_score": score,
            "eligible_under_basic_checks": is_fully_eligible,
            "eligibility_status": constraint_res["status"],
            "constraint_message": constraint_res["summary_message"],
            "score_breakdown": breakdown,
            "missing_evidence": constraint_res["missing_fields"],
            "warnings": all_warnings,
            "_current_hours": clean_curr_hours,
        })

    # Status priority hierarchy: ELIGIBLE (2) > NEEDS_REVIEW (1) > INELIGIBLE (0)
    status_priority = {
        STATUS_ELIGIBLE: 2,
        STATUS_NEEDS_REVIEW: 1,
        STATUS_INELIGIBLE: 0,
    }

    # Deterministic sorting key:
    # 1. Eligibility status priority (descending)
    # 2. Suitability score (descending)
    # 3. Current workload (ascending - favoring lighter loaded faculty as tie-breaker)
    # 4. Faculty ID / Name (ascending - stable numeric/alphabetical tie-breaker)
    def sort_key(item: dict[str, Any]):
        prio = status_priority.get(item["eligibility_status"], 0)
        score = item["suitability_score"]
        curr_hours = item["_current_hours"]
        fid = item["faculty_id"]
        if isinstance(fid, int):
            id_key = (0, fid)
        elif isinstance(fid, str) and fid.isdigit():
            id_key = (0, int(fid))
        else:
            id_key = (1, str(fid if fid is not None else item["faculty_name"]))
        return (-prio, -score, curr_hours, id_key)

    results.sort(key=sort_key)

    # Clean up internal sorting helper keys before returning
    for item in results:
        item.pop("_current_hours", None)

    return results

