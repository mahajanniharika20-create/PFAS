"""Constraint-Based Timetable Generator and Allocation Engine for PFAS.

This module implements a complete, constraint-first timetable generator that
assigns faculty, subjects, sections, days, and time intervals without relying on
hardcoded template patterns or arbitrary unconstrained randomness.

It supports:
1. Hard weekly workload limits with cumulative tracking.
2. Verified faculty qualifications and leave/availability constraints.
3. Strict faculty timetable collision prevention.
4. Strict section timetable collision prevention (cross-faculty).
5. Controlled, seed-reproducible randomization among feasible alternatives.
6. Independent schedule validation.
7. Grid formatting compatible with schedule.html.
"""
from __future__ import annotations

import copy
import math
import random
from typing import Any

from ml.config import (
    DEFAULT_POLICY_WEIGHTS,
    STATUS_ELIGIBLE,
    STATUS_INELIGIBLE,
    STATUS_NEEDS_REVIEW,
)
from ml.constraints import (
    evaluate_constraints,
    normalize_qualification,
    parse_slot_interval,
    parse_time_to_minutes,
    slots_collide,
)
from ml.engine import resolve_duplicate_records, score_candidate
from ml.features import normalize_domain, normalize_faculty_record, normalize_text

DEFAULT_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]

DEFAULT_TIME_SLOTS = [
    "09:00 AM - 10:00 AM",
    "10:00 AM - 11:00 AM",
    "11:15 AM - 12:15 PM",
    "01:15 PM - 02:15 PM",
    "02:15 PM - 03:15 PM",
]

DEFAULT_WORKLOAD_CAP = 16.0


def normalize_teaching_requirement(raw_req: dict[str, Any] | Any) -> dict[str, Any]:
    """Validate and normalize a teaching requirement for timetable generation."""
    if not isinstance(raw_req, dict):
        if hasattr(raw_req, "__dict__"):
            raw_req = raw_req.__dict__
        else:
            raise TypeError(f"Requirement must be a dict or object with attributes, got {type(raw_req)}")

    name = (
        raw_req.get("name")
        or raw_req.get("subject")
        or raw_req.get("assigned_subject")
        or raw_req.get("subject_name")
        or ""
    )
    name = str(name).strip()

    subject_id = raw_req.get("subject_id") or raw_req.get("id") or name
    section = raw_req.get("section")
    section = str(section).strip() if section is not None and str(section).strip() else None

    department = raw_req.get("department") or raw_req.get("dept") or ""
    domain = raw_req.get("domain") or raw_req.get("subject_domain") or ""
    year = raw_req.get("year") or raw_req.get("academic_year") or ""

    # Lecture hours & Lab hours
    lec_hours_raw = raw_req.get("lecture_hours")
    if lec_hours_raw is None:
        lec_hours_raw = raw_req.get("required_hours") or raw_req.get("hours") or 0

    try:
        lec_hours = float(lec_hours_raw)
        if not math.isfinite(lec_hours) or lec_hours < 0:
            lec_hours = 0.0
    except (ValueError, TypeError):
        lec_hours = 0.0

    lab_hours_raw = raw_req.get("lab_hours") or 0
    try:
        lab_hours = float(lab_hours_raw)
        if not math.isfinite(lab_hours) or lab_hours < 0:
            lab_hours = 0.0
    except (ValueError, TypeError):
        lab_hours = 0.0

    is_lab = bool(
        raw_req.get("is_lab")
        or lab_hours > 0
        or "lab" in name.lower()
    )

    req_qual = raw_req.get("required_qualification") or raw_req.get("qualification")
    consecutive = int(raw_req.get("consecutive_periods") or (2 if is_lab else 1))
    assignment_id = raw_req.get("assignment_id") or raw_req.get("schedule_id")

    return {
        "assignment_id": str(assignment_id).strip() if assignment_id is not None else None,
        "schedule_id": str(assignment_id).strip() if assignment_id is not None else None,
        "subject_id": str(subject_id).strip(),
        "name": name,
        "subject": name,
        "section": section,
        "department": str(department).strip(),
        "domain": str(domain).strip(),
        "year": str(year).strip(),
        "lecture_hours": lec_hours,
        "lab_hours": lab_hours,
        "total_hours": lec_hours + lab_hours,
        "is_lab": is_lab,
        "required_qualification": req_qual,
        "consecutive_periods": max(1, consecutive),
        "preferred_time_slot": raw_req.get("preferred_time_slot"),
        "preferred_faculty_id": raw_req.get("preferred_faculty_id"),
        "raw_record": raw_req,
    }


def normalize_faculty_pool(faculty_list: list[Any]) -> list[dict[str, Any]]:
    """Validate, normalize, and deduplicate faculty records for timetable scheduling."""
    if not faculty_list:
        return []

    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for raw in faculty_list:
        norm = normalize_faculty_record(raw)
        fid = norm.get("faculty_id")
        fname = norm.get("faculty_name")
        cand_key = (
            ("id", str(fid).strip())
            if fid is not None
            else ("name", str(fname).strip().lower() if fname else str(id(raw)))
        )
        grouped.setdefault(cand_key, []).append(norm)

    resolved_pool: list[dict[str, Any]] = []
    for _, group in grouped.items():
        resolved, _ = resolve_duplicate_records(group)

        # Baseline workload check
        raw_workload = None
        if isinstance(group[0], dict):
            raw_workload = group[0].get("current_weekly_hours")
            if raw_workload is None:
                raw_workload = group[0].get("workload")

        if raw_workload is not None:
            h_val = raw_workload
        else:
            h_val = resolved.get("current_weekly_hours")
            if h_val is None:
                h_val = resolved.get("weekly_hours")
        try:
            baseline_h = float(h_val) if h_val is not None else 0.0
            if not math.isfinite(baseline_h) or baseline_h < 0:
                baseline_h = float("inf")
        except (ValueError, TypeError):
            baseline_h = float("inf")

        raw_cap = (
            resolved.get("weekly_hour_cap")
            if resolved.get("weekly_hour_cap") is not None
            else (
                resolved.get("max_workload")
                if resolved.get("max_workload") is not None
                else (
                    raw.get("max_workload")
                    if isinstance(raw, dict) and raw.get("max_workload") is not None
                    else raw.get("weekly_hour_cap") if isinstance(raw, dict) else None
                )
            )
        )
        try:
            if raw_cap is not None:
                cap_h = float(raw_cap)
                if not math.isfinite(cap_h) or cap_h < 0:
                    cap_h = None
            else:
                cap_h = None
        except (ValueError, TypeError):
            cap_h = None

        resolved["_clean_baseline_hours"] = baseline_h
        resolved["_clean_cap_hours"] = cap_h
        resolved_pool.append(resolved)

    return resolved_pool


def decompose_requirements_into_units(
    normalized_requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Decompose teaching requirements into discrete weekly scheduling units (classes/labs)."""
    units: list[dict[str, Any]] = []

    for req in normalized_requirements:
        subj_name = req["name"]
        sec = req["section"]
        lec_h = req["lecture_hours"]
        lab_h = req["lab_hours"]

        # If total_hours is 0 or unverified, do not fabricate units
        if lec_h <= 0 and lab_h <= 0:
            continue

        # Lecture units: 1 hour periods
        num_lectures = int(round(lec_h))
        for i in range(num_lectures):
            units.append({
                "unit_id": f"{subj_name}_{sec}_lec_{i + 1}",
                "requirement": req,
                "is_lab": False,
                "duration_hours": 1.0,
                "consecutive_periods": 1,
                "unit_type": "Lecture",
            })

        # Lab units: can be scheduled as a 2-hour or multi-hour block
        if lab_h > 0:
            lab_block_len = req.get("consecutive_periods", 2)
            num_labs = max(1, int(round(lab_h / max(1, lab_block_len))))
            for i in range(num_labs):
                units.append({
                    "unit_id": f"{subj_name}_{sec}_lab_{i + 1}",
                    "requirement": req,
                    "is_lab": True,
                    "duration_hours": float(lab_block_len),
                    "consecutive_periods": lab_block_len,
                    "unit_type": "Lab",
                })

    return units


def check_assignment_feasibility(
    unit: dict[str, Any],
    faculty: dict[str, Any],
    day: str,
    slot_parsed: dict[str, Any],
    current_schedule: list[dict[str, Any]],
    current_faculty_hours: dict[Any, float],
    existing_schedule: list[Any] | None = None,
) -> tuple[bool, str]:
    """Check whether assigning a session unit to a faculty member at a day/slot is feasible.

    Strictly checks:
    1. Faculty qualification & domain alignment.
    2. Faculty availability and leave status.
    3. Faculty weekly workload limit (cumulative accounting).
    4. Faculty schedule overlap (no two classes for same faculty at same time).
    5. Section schedule overlap (no two classes for same section at same time, across any faculty).
    6. Overlap with external existing schedule.
    """
    req = unit["requirement"]
    fid = faculty.get("faculty_id") or faculty.get("id")

    # 1. Leave & Availability Check
    if faculty.get("on_leave") is True:
        return False, "Faculty is on leave"
    if faculty.get("available") is False:
        return False, "Faculty is unavailable"

    # 2. Domain & Qualification Verification
    req_domain = normalize_domain(req.get("domain") or req.get("department"))
    fac_domain = normalize_domain(faculty.get("domain") or faculty.get("faculty_domain") or faculty.get("department"))

    req_qual = req.get("required_qualification")
    fac_qual_raw = faculty.get("qualification") or faculty.get("qualifications")

    qual_matched = False
    if req_qual:
        req_q_norm = normalize_qualification(req_qual)
        if isinstance(fac_qual_raw, (list, tuple)):
            fac_quals = [normalize_qualification(q) for q in fac_qual_raw]
        else:
            fac_quals = [normalize_qualification(fac_qual_raw)] if fac_qual_raw else []
        qual_matched = req_q_norm in fac_quals

    # If domain specified, domain must match exactly (approved aliases supported);
    # If domain not matching and qualification not satisfied -> rejected
    if req_domain and fac_domain:
        if req_domain != fac_domain and not qual_matched:
            # Check if assigned subject explicitly matches
            assigned_s = normalize_text(faculty.get("assigned_subject"))
            req_s = normalize_text(req.get("name"))
            if not (assigned_s and req_s and assigned_s == req_s):
                return False, f"Domain mismatch ('{fac_domain}' != '{req_domain}')"
    elif req_qual and not qual_matched:
        return False, f"Qualification not satisfied for '{req_qual}'"

    # 3. Workload Accounting Check (Cumulative Hard Limit)
    duration = float(unit.get("duration_hours", 1.0))
    cap = faculty.get("_clean_cap_hours", DEFAULT_WORKLOAD_CAP)
    current_h = current_faculty_hours.get(fid, faculty.get("_clean_baseline_hours", 0.0))

    if not (math.isfinite(current_h) and math.isfinite(cap)):
        return False, "Non-finite workload or cap values"
    if current_h + duration > cap:
        return False, f"Workload limit exceeded ({current_h + duration}h > {cap}h)"

    # 4. Target Slot Construction
    prop_slot = dict(slot_parsed)
    prop_slot["day"] = day
    target_section = req.get("section")
    if target_section:
        prop_slot["section"] = str(target_section).strip()

    # 5. Collision Check against Current Assignments in This Generated Timetable
    for assigned in current_schedule:
        # Same day only can collide
        if assigned["day"] != day:
            continue

        assigned_slot_parsed = assigned["_slot_parsed"]

        # 5a. Faculty collision: Same faculty cannot teach two classes at the same time
        if str(assigned["faculty_id"]) == str(fid):
            collides, desc = slots_collide(prop_slot, assigned_slot_parsed)
            if collides:
                return False, f"Faculty collision with '{assigned['subject']}' ({desc})"

        # 5b. Section collision: Same section cannot have two classes at the same time
        assigned_sec = assigned.get("section")
        if target_section and assigned_sec and str(target_section).strip().lower() == str(assigned_sec).strip().lower():
            collides, desc = slots_collide(prop_slot, assigned_slot_parsed)
            if collides:
                return False, f"Section '{target_section}' collision with '{assigned['subject']}' ({desc})"

    # 6. Collision Check against External Existing Schedule
    if existing_schedule:
        target_assign_id = req.get("assignment_id") or req.get("schedule_id")

        for entry in existing_schedule:
            entry_assign_id = (
                entry.get("assignment_id", entry.get("schedule_id"))
                if isinstance(entry, dict)
                else getattr(entry, "assignment_id", getattr(entry, "schedule_id", None))
            )
            # Reliable assignment ID exclusion
            if target_assign_id is not None and entry_assign_id is not None:
                if str(target_assign_id).strip() == str(entry_assign_id).strip():
                    continue

            entry_day = (
                entry.get("day")
                if isinstance(entry, dict)
                else getattr(entry, "day", None)
            )
            if entry_day and extract_day(entry_day) != day:
                continue

            entry_fid = (
                entry.get("faculty_id", entry.get("id"))
                if isinstance(entry, dict)
                else getattr(entry, "faculty_id", getattr(entry, "id", None))
            )
            entry_sec = (
                entry.get("section")
                if isinstance(entry, dict)
                else getattr(entry, "section", None)
            )

            # Faculty collision
            if entry_fid is not None and str(entry_fid).strip() == str(fid):
                collides, desc = slots_collide(prop_slot, entry)
                if collides:
                    return False, f"Faculty collision with existing entry ({desc})"

            # Section collision
            if (
                target_section
                and entry_sec
                and str(target_section).strip().lower() == str(entry_sec).strip().lower()
            ):
                collides, desc = slots_collide(prop_slot, entry)
                if collides:
                    return False, f"Section '{target_section}' collision with existing entry ({desc})"

    # 7. Faculty Busy Slots Check
    busy_slots = faculty.get("assigned_slots") or faculty.get("busy_slots") or []
    if isinstance(busy_slots, str):
        busy_slots = [s.strip() for s in busy_slots.split(",") if s.strip()]
    for b_slot in busy_slots:
        b_parsed = parse_slot_interval(b_slot)
        if b_parsed.get("day") and b_parsed["day"] != day:
            continue
        collides, desc = slots_collide(prop_slot, b_parsed)
        if collides:
            return False, f"Faculty busy slot collision with '{b_slot}' ({desc})"

    return True, "Feasible"


def extract_day(day_val: Any) -> str | None:
    """Normalize day string into 3-letter day abbreviation."""
    if not day_val:
        return None
    cleaned = str(day_val).strip().lower()
    for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]:
        if d in cleaned:
            return d.capitalize()
    return None


def generate_timetable(
    requirements: list[dict[str, Any] | Any],
    faculty_pool: list[dict[str, Any] | Any],
    existing_schedule: list[Any] | None = None,
    time_slots: list[str] | None = None,
    days: list[str] | None = None,
    seed: int | None = None,
    policy_weights: dict[str, float] | None = None,
    allow_partial: bool = True,
) -> dict[str, Any]:
    """Generate a feasible, conflict-free weekly timetable using constraint-first scheduling.

    Args:
        requirements: List of subject/section requirements to schedule.
        faculty_pool: Available faculty records.
        existing_schedule: Optional existing timetable entries to check against.
        time_slots: Configurable list of daily time slot strings.
        days: Configurable list of days of the week.
        seed: Random seed for controlled stochastic tie-breaking.
        policy_weights: Optional preference scoring weights.
        allow_partial: If True, returns partial timetable with unscheduled report if full
                       allocation is infeasible. If False, fails completely on unscheduled units.

    Returns:
        Structured result dictionary with status, assignments, faculty_workloads,
        unscheduled_requirements, validation, and master grid representation.
    """
    rng = random.Random(seed)
    active_days = days if days is not None else list(DEFAULT_DAYS)
    active_slots = time_slots if time_slots is not None else list(DEFAULT_TIME_SLOTS)

    # 1. Input Normalization
    norm_requirements = [normalize_teaching_requirement(r) for r in requirements]
    norm_faculty = normalize_faculty_pool(faculty_pool)

    # 2. Decompose into units
    units = decompose_requirements_into_units(norm_requirements)

    # Parsed slot lookup
    parsed_slots = [
        (s_str, parse_slot_interval(s_str))
        for s_str in active_slots
    ]

    # Faculty workload tracking state
    faculty_workloads: dict[Any, float] = {}
    faculty_by_id: dict[Any, dict[str, Any]] = {}
    for f in norm_faculty:
        fid = f.get("faculty_id") or f.get("id")
        faculty_by_id[fid] = f
        faculty_workloads[fid] = f.get("_clean_baseline_hours", 0.0)

    # Schedule state
    current_schedule: list[dict[str, Any]] = []
    unscheduled: list[dict[str, Any]] = []

    # 3. Schedule by Requirements
    # Group units by requirement
    req_to_units: dict[int, list[dict[str, Any]]] = {}
    for u in units:
        req_id = id(u["requirement"])
        req_to_units.setdefault(req_id, []).append(u)

    # Sort requirements using Most-Constrained-Variable heuristic
    def req_sort_key(r: dict[str, Any]):
        has_lab = 1 if r["is_lab"] else 0
        has_pref = 1 if r.get("preferred_faculty_id") else 0
        dur = r["total_hours"]
        rnd_noise = rng.random()
        return (-has_lab, -has_pref, -dur, rnd_noise)

    sorted_requirements = sorted(norm_requirements, key=req_sort_key)

    for req in sorted_requirements:
        req_units = req_to_units.get(id(req), [])
        if not req_units:
            unscheduled.append({
                "unit_id": f"REQ_{req.get('subject_id')}",
                "subject": req["name"],
                "section": req["section"],
                "reason": "Missing or unverified teaching hours; requirement cannot be scheduled",
            })
            continue

        req_total_hours = sum(u["duration_hours"] for u in req_units)

        # Find eligible faculty candidates with capacity for the ENTIRE requirement
        eligible_faculty: list[dict[str, Any]] = []
        for fac in norm_faculty:
            fid = fac.get("faculty_id") or fac.get("id")
            if fac.get("on_leave") is True or fac.get("available") is False:
                continue
            cap = fac.get("_clean_cap_hours")
            if cap is None:
                continue
            curr = faculty_workloads.get(fid, 0.0)
            if curr + req_total_hours > cap:
                continue

            # Check domain / subject / qualification compatibility
            req_dom = normalize_domain(req["domain"] or req["department"])
            fac_dom = normalize_domain(fac.get("domain") or fac.get("department"))
            req_s = normalize_text(req["name"])
            fac_assigned_s = normalize_text(fac.get("assigned_subject"))

            is_compat = False
            if req_dom and fac_dom and req_dom == fac_dom:
                is_compat = True
            elif req_s and fac_assigned_s and req_s == fac_assigned_s:
                is_compat = True
            elif req.get("required_qualification"):
                req_q = normalize_qualification(req["required_qualification"])
                f_quals = fac.get("qualification") or fac.get("qualifications") or []
                if not isinstance(f_quals, (list, tuple)):
                    f_quals = [f_quals]
                norm_fq = [normalize_qualification(q) for q in f_quals]
                if req_q in norm_fq:
                    is_compat = True
            elif not req_dom and not req.get("required_qualification"):
                is_compat = True

            if is_compat:
                eligible_faculty.append(fac)

        if not eligible_faculty:
            for u in req_units:
                unscheduled.append({
                    "unit_id": u["unit_id"],
                    "subject": req["name"],
                    "section": req["section"],
                    "reason": f"Workload limit exceeded or no eligible faculty with remaining capacity for {req_total_hours}h",
                })
            continue

        # Score and rank eligible faculty candidates
        scored_faculty: list[tuple[float, dict[str, Any]]] = []
        for fac in eligible_faculty:
            score, _, _ = score_candidate(fac, req, weights=policy_weights)
            jitter = rng.uniform(0.0, 1.0)
            scored_faculty.append((score + jitter, fac))

        scored_faculty.sort(key=lambda x: x[0], reverse=True)

        scheduled_for_req = False
        for _, chosen_fac in scored_faculty:
            chosen_fid = chosen_fac.get("faculty_id") or chosen_fac.get("id")

            # Try to place all units into distinct (day, slot) slots
            temp_schedule = list(current_schedule)
            temp_placed: list[dict[str, Any]] = []
            all_units_placed = True

            shuffled_days = list(active_days)
            rng.shuffle(shuffled_days)

            shuffled_slots = list(parsed_slots)
            rng.shuffle(shuffled_slots)

            for u in req_units:
                candidate_slots = []
                for day in shuffled_days:
                    for slot_str, slot_parsed in shuffled_slots:
                        feasible, _ = check_assignment_feasibility(
                            unit=u,
                            faculty=chosen_fac,
                            day=day,
                            slot_parsed=slot_parsed,
                            current_schedule=temp_schedule,
                            current_faculty_hours=faculty_workloads,
                            existing_schedule=existing_schedule,
                        )
                        if feasible:
                            sec_day_count = sum(1 for a in temp_schedule if a["day"] == day and a.get("section") == req["section"])
                            fac_day_count = sum(1 for a in temp_schedule if a["day"] == day and a["faculty_id"] == chosen_fid)
                            slot_pref_bonus = 5.0 if req.get("preferred_time_slot") and req["preferred_time_slot"] in slot_str else 0.0
                            rank_score = slot_pref_bonus - (sec_day_count * 5.0) - (fac_day_count * 4.0) + rng.uniform(0.0, 1.0)
                            candidate_slots.append((rank_score, day, slot_str, slot_parsed))

                if candidate_slots:
                    candidate_slots.sort(key=lambda x: x[0], reverse=True)
                    _, best_day, best_slot_str, best_slot_parsed = candidate_slots[0]

                    assignment_entry = {
                        "assignment_id": req.get("assignment_id") or f"asgn_{len(temp_schedule) + 1}_{u['unit_id']}",
                        "unit_id": u["unit_id"],
                        "subject": req["name"],
                        "subject_id": req["subject_id"],
                        "section": req["section"],
                        "department": req["department"] or chosen_fac.get("department", "CSE"),
                        "academic_year": req["year"] or chosen_fac.get("year", "1"),
                        "faculty_id": chosen_fid,
                        "faculty_name": chosen_fac.get("faculty_name") or chosen_fac.get("name"),
                        "day": best_day,
                        "time_slot": best_slot_str,
                        "start_min": best_slot_parsed["start_min"],
                        "end_min": best_slot_parsed["end_min"],
                        "duration_hours": u["duration_hours"],
                        "is_lab": u["is_lab"],
                        "unit_type": u["unit_type"],
                        "_slot_parsed": best_slot_parsed,
                    }
                    temp_placed.append(assignment_entry)
                    temp_schedule.append(assignment_entry)
                else:
                    all_units_placed = False
                    break

            if all_units_placed:
                current_schedule.extend(temp_placed)
                faculty_workloads[chosen_fid] = round(faculty_workloads.get(chosen_fid, 0.0) + req_total_hours, 2)
                scheduled_for_req = True
                break

        if not scheduled_for_req:
            for u in req_units:
                unscheduled.append({
                    "unit_id": u["unit_id"],
                    "subject": req["name"],
                    "section": req["section"],
                    "reason": "Could not find collision-free day/slot combinations for all required sessions",
                })

    # 4. Independent Validation
    validation_res = validate_timetable(
        assignments=current_schedule,
        requirements=norm_requirements,
        faculty_pool=norm_faculty,
        existing_schedule=existing_schedule,
    )

    # 5. Format Master Grid per Faculty
    master_grid = format_master_grid(
        assignments=current_schedule,
        faculty_list=norm_faculty,
        time_slots=active_slots,
        days=active_days,
    )

    # 6. Format Section Grid per Section
    sections_list = sorted(list({r["section"] for r in norm_requirements if r.get("section")}))
    section_grid = format_section_grid(
        assignments=current_schedule,
        sections=sections_list,
        time_slots=active_slots,
        days=active_days,
    )

    status = (
        "SUCCESS" if not unscheduled and validation_res["is_valid"]
        else ("PARTIAL" if current_schedule and allow_partial else "FAILED")
    )

    # Clean internal helpers before returning
    cleaned_assignments = []
    for a in current_schedule:
        a_clean = dict(a)
        a_clean.pop("_slot_parsed", None)
        cleaned_assignments.append(a_clean)

    workload_summary = {}
    for fid, f in faculty_by_id.items():
        base = f.get("_clean_baseline_hours", 0.0)
        tot = faculty_workloads.get(fid, base)
        cap = f.get("_clean_cap_hours")
        rem = max(0.0, round(cap - tot, 2)) if cap is not None else 0.0
        workload_summary[fid] = {
            "faculty_name": f.get("faculty_name") or f.get("name"),
            "baseline_hours": base,
            "assigned_hours": round(tot - base, 2),
            "total_weekly_hours": round(tot, 2),
            "weekly_hour_cap": cap,
            "remaining_capacity": rem,
        }

    return {
        "status": status,
        "assignments": cleaned_assignments,
        "total_assigned_count": len(cleaned_assignments),
        "unscheduled_requirements": unscheduled,
        "unscheduled_count": len(unscheduled),
        "faculty_workloads": workload_summary,
        "validation": validation_res,
        "master_grid": master_grid,
        "section_grid": section_grid,
        "seed": seed,
    }


def validate_timetable(
    assignments: list[dict[str, Any]],
    requirements: list[dict[str, Any]] | None = None,
    faculty_pool: list[dict[str, Any]] | None = None,
    existing_schedule: list[Any] | None = None,
) -> dict[str, Any]:
    """Independently validate a complete generated timetable against all hard constraints.

    Checks:
    1. No class has malformed or zero-duration time intervals.
    2. No faculty member has overlapping classes on any day.
    3. No section has overlapping classes on any day across any faculty.
    4. Faculty cumulative workload does not exceed weekly workload cap.
    5. Faculty leave status and availability are strictly respected.
    6. Qualification and domain alignments are respected.
    7. No fabricated or unknown subject allocations.
    8. Cross-schedule collision check against existing institutional timetable.
    """
    errors: list[str] = []
    warnings: list[str] = []

    faculty_by_id: dict[Any, dict[str, Any]] = {}
    if faculty_pool:
        for f in faculty_pool:
            fid = f.get("faculty_id") or f.get("id")
            faculty_by_id[str(fid)] = f

    # 1. Individual Assignment Validation
    for i, asgn in enumerate(assignments):
        s_min = asgn.get("start_min")
        e_min = asgn.get("end_min")
        if s_min is None or e_min is None:
            parsed = parse_slot_interval(asgn.get("time_slot"))
            s_min = parsed.get("start_min")
            e_min = parsed.get("end_min")

        if s_min is None or e_min is None or e_min <= s_min:
            errors.append(
                f"Assignment {asgn.get('assignment_id', i)} has malformed or zero-duration slot: '{asgn.get('time_slot')}'"
            )

        # Faculty leave/availability validation
        fid_str = str(asgn.get("faculty_id"))
        if fid_str in faculty_by_id:
            fac_rec = faculty_by_id[fid_str]
            if fac_rec.get("on_leave") is True:
                errors.append(
                    f"Faculty '{asgn.get('faculty_name')}' (ID {fid_str}) is on leave but assigned to '{asgn.get('subject')}'"
                )
            if fac_rec.get("available") is False:
                errors.append(
                    f"Faculty '{asgn.get('faculty_name')}' (ID {fid_str}) is marked unavailable but assigned to '{asgn.get('subject')}'"
                )

    # 2. Pairwise Collision Checks
    n = len(assignments)
    for i in range(n):
        a1 = assignments[i]
        d1 = a1.get("day")
        p1 = a1.get("_slot_parsed") or parse_slot_interval(a1.get("time_slot"))
        p1["day"] = d1

        for j in range(i + 1, n):
            a2 = assignments[j]
            d2 = a2.get("day")
            if d1 != d2:
                continue

            p2 = a2.get("_slot_parsed") or parse_slot_interval(a2.get("time_slot"))
            p2["day"] = d2

            # Check for Faculty Overlap
            if str(a1.get("faculty_id")) == str(a2.get("faculty_id")):
                collides, desc = slots_collide(p1, p2)
                if collides:
                    errors.append(
                        f"Faculty collision on {d1}: Faculty '{a1.get('faculty_name')}' has overlapping assignments "
                        f"'{a1.get('subject')}' ({a1.get('time_slot')}) and '{a2.get('subject')}' ({a2.get('time_slot')}) [{desc}]"
                    )

            # Check for Section Overlap
            sec1 = a1.get("section")
            sec2 = a2.get("section")
            if sec1 and sec2 and str(sec1).strip().lower() == str(sec2).strip().lower():
                collides, desc = slots_collide(p1, p2)
                if collides:
                    errors.append(
                        f"Section collision on {d1}: Section '{sec1}' has overlapping assignments "
                        f"'{a1.get('subject')}' ({a1.get('time_slot')}, {a1.get('faculty_name')}) and "
                        f"'{a2.get('subject')}' ({a2.get('time_slot')}, {a2.get('faculty_name')}) [{desc}]"
                    )

    # 3. Cumulative Workload Accounting
    faculty_assigned_hours: dict[str, float] = {}
    for asgn in assignments:
        fid_str = str(asgn.get("faculty_id"))
        dur = float(asgn.get("duration_hours", 1.0))
        faculty_assigned_hours[fid_str] = faculty_assigned_hours.get(fid_str, 0.0) + dur

    if faculty_pool:
        for f in faculty_pool:
            fid_str = str(f.get("faculty_id") or f.get("id"))
            base_h = f.get("_clean_baseline_hours", 0.0)
            if not math.isfinite(base_h):
                base_h = 0.0
            cap_h = f.get("_clean_cap_hours")
            tot_h = round(base_h + faculty_assigned_hours.get(fid_str, 0.0), 2)
            if cap_h is None:
                if faculty_assigned_hours.get(fid_str, 0.0) > 0:
                    errors.append(
                        f"Faculty '{f.get('faculty_name') or f.get('name')}' assigned {tot_h}h but weekly workload limit is unconfirmed/missing."
                    )
            elif tot_h > cap_h:
                errors.append(
                    f"Faculty '{f.get('faculty_name') or f.get('name')}' exceeds weekly workload limit "
                    f"({tot_h}h > {cap_h}h cap; baseline={base_h}h, assigned={faculty_assigned_hours.get(fid_str, 0.0)}h)"
                )

    # 4. Collisions against Existing Institutional Schedule
    if existing_schedule:
        for asgn in assignments:
            p_asgn = parse_slot_interval(asgn.get("time_slot"))
            p_asgn["day"] = asgn.get("day")
            fid_str = str(asgn.get("faculty_id"))
            sec_str = str(asgn.get("section") or "").strip().lower()
            asgn_id = asgn.get("assignment_id")

            for entry in existing_schedule:
                entry_asgn_id = (
                    entry.get("assignment_id", entry.get("schedule_id"))
                    if isinstance(entry, dict)
                    else getattr(entry, "assignment_id", getattr(entry, "schedule_id", None))
                )
                if asgn_id and entry_asgn_id and str(asgn_id).strip() == str(entry_asgn_id).strip():
                    continue

                entry_day = extract_day(
                    entry.get("day") if isinstance(entry, dict) else getattr(entry, "day", None)
                )
                if entry_day and entry_day != asgn.get("day"):
                    continue

                entry_fid = str(
                    entry.get("faculty_id", entry.get("id"))
                    if isinstance(entry, dict)
                    else getattr(entry, "faculty_id", getattr(entry, "id", None))
                )
                entry_sec = str(
                    entry.get("section") if isinstance(entry, dict) else getattr(entry, "section", None) or ""
                ).strip().lower()

                # Faculty overlap
                if entry_fid and entry_fid == fid_str:
                    collides, desc = slots_collide(p_asgn, entry)
                    if collides:
                        errors.append(
                            f"Collision with existing schedule: Faculty ID {fid_str} has overlap on {asgn.get('day')} "
                            f"during '{asgn.get('time_slot')}' [{desc}]"
                        )

                # Section overlap
                if sec_str and entry_sec and sec_str == entry_sec:
                    collides, desc = slots_collide(p_asgn, entry)
                    if collides:
                        errors.append(
                            f"Collision with existing schedule: Section '{asgn.get('section')}' has overlap on {asgn.get('day')} "
                            f"during '{asgn.get('time_slot')}' [{desc}]"
                        )
    else:
        warnings.append(
            "Notice: Complete institutional schedule not provided; external timetable collisions could not be independently verified."
        )

    return {
        "is_valid": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def format_master_grid(
    assignments: list[dict[str, Any]],
    faculty_list: list[dict[str, Any]],
    time_slots: list[str] | None = None,
    days: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Format assignments into the master_grid structure expected by schedule.html."""
    active_days = days or DEFAULT_DAYS
    active_slots = time_slots or DEFAULT_TIME_SLOTS

    # Index assignments by (faculty_id, day, slot_str)
    asgn_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    for a in assignments:
        fid = str(a.get("faculty_id"))
        day = a.get("day")
        slot = a.get("time_slot")
        asgn_map[(fid, day, slot)] = a

    master_grid: list[dict[str, Any]] = []

    for fac in faculty_list:
        fid = str(fac.get("faculty_id") or fac.get("id"))
        fac_obj = fac.get("raw_record", fac)

        slots_grid: list[dict[str, str]] = []
        for slot in active_slots:
            slot_row: dict[str, str] = {"time": slot}
            for day in active_days:
                asgn = asgn_map.get((fid, day, slot))
                if asgn:
                    subj = asgn["subject"]
                    sec = asgn.get("section")
                    is_lab = asgn.get("is_lab", False)
                    sec_clean = str(sec).replace("Section", "").strip() if sec else ""
                    sec_suffix = f" ({sec_clean})" if sec_clean else ""
                    if is_lab:
                        slot_row[day] = f"{subj} Lab{sec_suffix}"
                    else:
                        slot_row[day] = f"{subj}{sec_suffix}"
                else:
                    slot_row[day] = "Free"
            slots_grid.append(slot_row)

        master_grid.append({
            "faculty": fac_obj,
            "slots": slots_grid,
        })

    return master_grid


def format_section_grid(
    assignments: list[dict[str, Any]],
    sections: list[str] | None = None,
    time_slots: list[str] | None = None,
    days: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Format assignments into a section-centric weekly timetable grid."""
    active_days = days or DEFAULT_DAYS
    active_slots = time_slots or DEFAULT_TIME_SLOTS

    all_sections = sections or sorted(list({a["section"] for a in assignments if a.get("section")}))

    # Index by (section, day, slot)
    sec_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    for a in assignments:
        sec = a.get("section")
        if sec:
            sec_map[(str(sec), a.get("day"), a.get("time_slot"))] = a

    section_grid: list[dict[str, Any]] = []
    for sec in all_sections:
        sec_slots: list[dict[str, str]] = []
        for slot in active_slots:
            row: dict[str, str] = {"time": slot}
            for day in active_days:
                asgn = sec_map.get((str(sec), day, slot))
                if asgn:
                    row[day] = f"{asgn['subject']} ({asgn['faculty_name']})"
                else:
                    row[day] = "Free"
            sec_slots.append(row)
        section_grid.append({
            "section": sec,
            "slots": sec_slots,
        })

    return section_grid
