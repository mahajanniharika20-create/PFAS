"""Constraint validation and feasibility verification for faculty allocation.

Rules:
1. Hard constraints (workload cap exceeded, confirmed unavailable, active leave,
   schedule collision, qualification mismatch) mark a candidate as INELIGIBLE.
2. Missing evidence (unspecified workload cap, unspecified current hours,
   unconfirmed availability, unverified qualification) marks a candidate as
   NEEDS_REVIEW. Unverified candidates are NEVER marked as confirmed eligible.
3. Only candidates with all required evidence present and satisfied are marked as ELIGIBLE.
"""
import math
import re
from typing import Any
from ml.config import STATUS_ELIGIBLE, STATUS_NEEDS_REVIEW, STATUS_INELIGIBLE
from ml.features import normalize_text

# Approved canonical qualification aliases
QUALIFICATION_ALIASES = {
    "phd": "phd",
    "ph.d": "phd",
    "ph.d.": "phd",
    "doctorate": "phd",
    "mtech": "mtech",
    "m.tech": "mtech",
    "m.tech.": "mtech",
    "btech": "btech",
    "b.tech": "btech",
    "b.tech.": "btech",
    "ms": "ms",
    "m.s": "ms",
    "m.s.": "ms",
    "ba": "ba",
    "b.a": "ba",
    "b.a.": "ba",
    "msc": "msc",
    "m.sc": "msc",
    "m.sc.": "msc",
    "bsc": "bsc",
    "b.sc": "bsc",
    "b.sc.": "bsc",
    "mba": "mba",
    "m.b.a": "mba",
    "m.b.a.": "mba",
}


def normalize_qualification(qual: Any) -> str:
    """Normalize a qualification token using approved canonical aliases."""
    cleaned = str(qual).strip().lower().replace(" ", "")
    cleaned = cleaned.strip(".,;:()[]'\"")
    return QUALIFICATION_ALIASES.get(cleaned, cleaned)


DAY_MAP = {
    "mon": "mon",
    "monday": "mon",
    "tue": "tue",
    "tues": "tue",
    "tuesday": "tue",
    "wed": "wed",
    "wednesday": "wed",
    "thu": "thu",
    "thur": "thu",
    "thurs": "thu",
    "thursday": "thu",
    "fri": "fri",
    "friday": "fri",
    "sat": "sat",
    "saturday": "sat",
    "sun": "sun",
    "sunday": "sun",
}


def parse_time_to_minutes(time_str: Any) -> int | None:
    """Parse a 12-hour or 24-hour time string into minutes from midnight (0–1439)."""
    if time_str is None:
        return None
    clean = str(time_str).strip().lower()
    m = re.match(r"^(\d{1,2}):(\d{2})(?:\s*([ap]m))?$", clean)
    if not m:
        return None
    h = int(m.group(1))
    minute = int(m.group(2))
    ampm = m.group(3)
    if minute < 0 or minute > 59:
        return None
    if ampm:
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        if h < 0 or h > 23:
            return None
    else:
        if h < 0 or h > 23:
            return None
    return h * 60 + minute


def inherit_meridian(s_str: str, e_str: str) -> str:
    """Infer and attach AM/PM meridian to start time if missing and unambiguous from end time."""
    s_clean = s_str.strip()
    e_clean = e_str.strip()
    s_has_meridian = bool(re.search(r"[ap]m", s_clean, re.I))
    e_has_meridian = bool(re.search(r"[ap]m", e_clean, re.I))
    if not s_has_meridian and e_has_meridian:
        s_m = re.match(r"^(\d{1,2}):(\d{2})", s_clean)
        e_m = re.match(r"^(\d{1,2}):(\d{2})", e_clean)
        if s_m and e_m:
            s_h = int(s_m.group(1))
            e_h = int(e_m.group(1))
            e_is_pm = bool(re.search(r"pm", e_clean, re.I))
            e_is_am = bool(re.search(r"am", e_clean, re.I))
            if 1 <= s_h <= 12 and 1 <= e_h <= 12:
                if e_is_am:
                    return f"{s_clean} am"
                elif e_is_pm:
                    if e_h == 12:
                        return f"{s_clean} am" if s_h != 12 else f"{s_clean} pm"
                    elif s_h == 12:
                        return f"{s_clean} pm"
                    elif s_h <= e_h:
                        return f"{s_clean} pm"
                    else:
                        return f"{s_clean} am"
    return s_clean


def extract_day_from_text(text: Any) -> str | None:
    """Extract a canonical 3-letter lowercase day abbreviation from text."""
    if not text:
        return None
    clean = str(text).strip().lower()
    m = re.search(
        r"\b(mon|monday|tue|tues|tuesday|wed|wednesday|thu|thur|thurs|thursday|fri|friday|sat|saturday|sun|sunday)\b",
        clean,
    )
    if m:
        return DAY_MAP.get(m.group(1))
    return None


def parse_slot_interval(slot: Any) -> dict[str, Any]:
    """Parse a timetable slot into structured interval, day, section, and faculty metadata."""
    if slot is None:
        return {
            "day": None,
            "start_min": None,
            "end_min": None,
            "raw_str": "",
            "section": None,
            "faculty_id": None,
            "faculty_name": None,
            "subject": None,
        }

    day = None
    start_min = None
    end_min = None
    section = None
    faculty_id = None
    faculty_name = None
    subject = None
    raw_str = ""

    if isinstance(slot, dict):
        raw_str = str(
            slot.get("time_slot")
            or slot.get("time")
            or slot.get("slot")
            or slot.get("preferred_time_slot")
            or ""
        )
        if slot.get("day"):
            day = extract_day_from_text(slot["day"])
        section = slot.get("section")
        faculty_id = slot.get("faculty_id", slot.get("id"))
        faculty_name = slot.get("faculty_name", slot.get("name"))
        subject = slot.get("subject", slot.get("assigned_subject"))

        start_raw = slot.get("start_time") or slot.get("start")
        end_raw = slot.get("end_time") or slot.get("end")
        if start_raw and end_raw:
            s_adj = inherit_meridian(str(start_raw), str(end_raw))
            start_min = parse_time_to_minutes(s_adj)
            end_min = parse_time_to_minutes(end_raw)
    elif hasattr(slot, "__dict__"):
        raw_str = str(
            getattr(slot, "time_slot", None)
            or getattr(slot, "time", None)
            or getattr(slot, "slot", None)
            or getattr(slot, "preferred_time_slot", None)
            or ""
        )
        if getattr(slot, "day", None):
            day = extract_day_from_text(getattr(slot, "day"))
        section = getattr(slot, "section", None)
        faculty_id = getattr(slot, "faculty_id", getattr(slot, "id", None))
        faculty_name = getattr(slot, "faculty_name", getattr(slot, "name", None))
        subject = getattr(slot, "subject", getattr(slot, "assigned_subject", None))
        start_raw = getattr(slot, "start_time", getattr(slot, "start", None))
        end_raw = getattr(slot, "end_time", getattr(slot, "end", None))
        if start_raw and end_raw:
            s_adj = inherit_meridian(str(start_raw), str(end_raw))
            start_min = parse_time_to_minutes(s_adj)
            end_min = parse_time_to_minutes(end_raw)
    else:
        raw_str = str(slot).strip()

    if not day and raw_str:
        day = extract_day_from_text(raw_str)

    if (start_min is None or end_min is None) and raw_str:
        # Match time ranges: e.g. "09:00 AM - 10:00 AM", "09:00 - 10:00", "01:15 PM - 02:15 PM"
        m = re.search(
            r"(\d{1,2}:\d{2}(?:\s*[ap]m)?)\s*(?:-|to)\s*(\d{1,2}:\d{2}(?:\s*[ap]m)?)",
            raw_str,
            re.I,
        )
        if m:
            s_adj = inherit_meridian(m.group(1), m.group(2))
            s_parsed = parse_time_to_minutes(s_adj)
            e_parsed = parse_time_to_minutes(m.group(2))
            if s_parsed is not None and e_parsed is not None:
                start_min = s_parsed
                end_min = e_parsed

    norm_section = str(section).strip() if section is not None and str(section).strip() != "" else None
    return {
        "day": day,
        "start_min": start_min,
        "end_min": end_min,
        "raw_str": raw_str,
        "section": norm_section,
        "faculty_id": faculty_id,
        "faculty_name": faculty_name,
        "subject": subject,
    }


def slots_collide(slot1: Any, slot2: Any) -> tuple[bool, str]:
    """Check if two timetable slots collide.

    Rules:
    1. If both specify scheduled days and days are different (e.g. Mon vs Tue) -> False.
    2. If both have valid start and end minute intervals:
       - Collision occurs if either interval is malformed (end <= start).
       - Collision occurs if intervals strictly overlap: max(start1, start2) < min(end1, end2).
       - Adjacent intervals (e.g. 09:00-10:00 and 10:00-11:00) do NOT overlap.
    3. If intervals cannot be parsed, fallback to case- and whitespace-normalized string match.
    """
    p1 = slot1 if isinstance(slot1, dict) and "start_min" in slot1 else parse_slot_interval(slot1)
    p2 = slot2 if isinstance(slot2, dict) and "start_min" in slot2 else parse_slot_interval(slot2)

    # 1. Day check: different days do not collide
    d1 = extract_day_from_text(p1.get("day")) or (str(p1["day"]).strip().lower() if p1.get("day") else None)
    d2 = extract_day_from_text(p2.get("day")) or (str(p2["day"]).strip().lower() if p2.get("day") else None)
    if d1 and d2 and d1 != d2:
        return False, "Different scheduled days"

    # 2. Structured interval overlap check
    s1, e1 = p1["start_min"], p1["end_min"]
    s2, e2 = p2["start_min"], p2["end_min"]

    if s1 is not None and e1 is not None and s2 is not None and e2 is not None:
        if e1 <= s1 or e2 <= s2:
            return True, "Collision/Violation: Malformed or zero-duration time interval (end time must be strictly after start time)"
        if max(s1, s2) < min(e1, e2):
            overlap_start = max(s1, s2)
            overlap_end = min(e1, e2)
            t_str = f"{overlap_start // 60:02d}:{overlap_start % 60:02d} - {overlap_end // 60:02d}:{overlap_end % 60:02d}"
            return True, f"Overlapping time interval ({t_str})"
        return False, "Non-overlapping time intervals"

    # If interval syntax was present on either slot but failed to produce valid start/end minutes
    has_interval_syntax1 = bool(re.search(r"[-–—]|(?:\bto\b)", str(p1.get("raw_str", "")), re.I))
    has_interval_syntax2 = bool(re.search(r"[-–—]|(?:\bto\b)", str(p2.get("raw_str", "")), re.I))
    if (has_interval_syntax1 and (s1 is None or e1 is None)) or (has_interval_syntax2 and (s2 is None or e2 is None)):
        return True, "Collision/Violation: Malformed time interval could not be parsed"

    # 3. Fallback to normalized string comparison if intervals cannot be parsed
    r1 = re.sub(r"\s+", " ", p1["raw_str"]).strip().lower()
    r2 = re.sub(r"\s+", " ", p2["raw_str"]).strip().lower()
    if r1 and r2 and r1 == r2:
        return True, f"Identical timetable slot '{r1}'"

    return False, "No collision detected"



def evaluate_constraints(
    candidate: dict[str, Any],
    required_hours: float = 0,
    subject: dict[str, Any] | None = None,
    existing_schedule: list[Any] | None = None,
) -> dict[str, Any]:
    """Perform comprehensive constraint evaluation on a faculty candidate record.

    Returns a structured dictionary with:
    - status: 'ELIGIBLE', 'NEEDS_REVIEW', or 'INELIGIBLE'
    - is_eligible: bool (True ONLY when status == 'ELIGIBLE')
    - can_allocate: bool (True ONLY when status == 'ELIGIBLE')
    - reasons: list of human-readable findings
    - missing_fields: list of unsupplied required fields
    - summary_message: consolidated status message
    """
    reasons: list[str] = []
    missing_fields: list[str] = []
    status = STATUS_ELIGIBLE

    # 1. Workload Cap and Current Hours Verification
    cap_val = candidate.get("weekly_hour_cap")
    current_val = candidate.get("current_weekly_hours")
    if current_val is None:
        current_val = candidate.get("weekly_hours")

    if cap_val is None or str(cap_val).strip() == "":
        status = STATUS_NEEDS_REVIEW
        missing_fields.append("weekly_hour_cap")
        reasons.append("Needs review: Maximum weekly workload cap not specified.")

    if current_val is None or str(current_val).strip() == "":
        if status != STATUS_INELIGIBLE:
            status = STATUS_NEEDS_REVIEW
        missing_fields.append("current_weekly_hours")
        reasons.append("Needs review: Current weekly workload hours not specified.")

    # Validate numeric types and limits
    try:
        req_hours = float(required_hours)
        if not math.isfinite(req_hours) or req_hours < 0:
            status = STATUS_INELIGIBLE
            reasons.append("Constraint violated: Workload hours must be valid finite numbers.")
    except (ValueError, TypeError):
        status = STATUS_INELIGIBLE
        reasons.append("Constraint violated: Invalid numeric value for required hours.")
        req_hours = 0.0

    if (
        cap_val is not None
        and str(cap_val).strip() != ""
        and current_val is not None
        and str(current_val).strip() != ""
    ):
        try:
            cap = float(cap_val)
            current = float(current_val)

            if not (math.isfinite(cap) and math.isfinite(current)):
                status = STATUS_INELIGIBLE
                reasons.append("Constraint violated: Workload hours must be valid finite numbers.")
            elif current < 0 or cap < 0:
                status = STATUS_INELIGIBLE
                reasons.append("Constraint violated: Workload hours cannot be negative.")
            elif current + req_hours > cap:
                status = STATUS_INELIGIBLE
                reasons.append(
                    f"Constraint violated: Weekly hour cap exceeded "
                    f"(Current: {current}h, Required: {req_hours}h, Cap: {cap}h)."
                )
            else:
                reasons.append(
                    f"Workload check passed: {current + req_hours}h / {cap}h."
                )
        except (ValueError, TypeError):
            status = STATUS_INELIGIBLE
            reasons.append("Constraint violated: Invalid numeric value for workload hours.")

    # 2. Availability & Leave Verification (evaluated independently)
    has_avail_info = False

    if "available" in candidate and candidate["available"] is not None:
        has_avail_info = True
        avail = candidate["available"]
        if avail is False or str(avail).lower() in ["false", "0", "no"]:
            status = STATUS_INELIGIBLE
            reasons.append("Constraint violated: Faculty member is marked unavailable.")
        else:
            reasons.append("Availability confirmed.")

    if "on_leave" in candidate and candidate["on_leave"] is not None:
        has_avail_info = True
        on_leave = candidate["on_leave"]
        if on_leave is True or str(on_leave).lower() in ["true", "1", "yes"]:
            status = STATUS_INELIGIBLE
            reasons.append("Constraint violated: Faculty member is on approved leave.")
        elif not ("available" in candidate and candidate["available"] is not None):
            reasons.append("Not on leave.")

    if not has_avail_info:
        if status != STATUS_INELIGIBLE:
            status = STATUS_NEEDS_REVIEW
        missing_fields.append("available")
        reasons.append("Needs review: Availability status not confirmed.")

    # 3. Schedule, Slot, and Section Conflict Verification
    proposed_slot_raw = None
    target_section = None
    if subject:
        proposed_slot_raw = (
            subject.get("preferred_time_slot")
            or subject.get("time_slot")
            or subject.get("slot")
            or subject.get("time")
        )
        target_section = subject.get("section")

    if not target_section and candidate:
        target_section = candidate.get("allocated_section") or candidate.get("target_section")
        if (
            not target_section
            and candidate.get("assigned_subject")
            and subject
            and (
                str(candidate.get("assigned_subject")).strip().lower()
                == str(
                    subject.get("name")
                    or subject.get("subject")
                    or subject.get("assigned_subject")
                    or ""
                ).strip().lower()
            )
        ):
            target_section = candidate.get("section")

    if proposed_slot_raw and str(proposed_slot_raw).strip():
        proposed_slot_parsed = parse_slot_interval(proposed_slot_raw)
        if subject and subject.get("day") and not proposed_slot_parsed.get("day"):
            proposed_slot_parsed["day"] = extract_day_from_text(subject["day"])
        if target_section:
            proposed_slot_parsed["section"] = str(target_section).strip()

        # Check for malformed or zero-duration proposed intervals
        is_interval_format = bool(
            re.search(r"[-–—]|(?:\bto\b)", str(proposed_slot_raw), re.I)
            or (isinstance(subject, dict) and (subject.get("start_time") or subject.get("end_time")))
        )
        s_min = proposed_slot_parsed.get("start_min")
        e_min = proposed_slot_parsed.get("end_min")

        is_malformed_or_zero = False
        if is_interval_format:
            if s_min is None or e_min is None or e_min <= s_min:
                is_malformed_or_zero = True
        elif s_min is not None and e_min is not None and e_min <= s_min:
            is_malformed_or_zero = True

        if is_malformed_or_zero:
            status = STATUS_INELIGIBLE
            reasons.append(
                f"Constraint violated: Proposed timetable slot interval '{proposed_slot_raw}' is malformed or zero-duration "
                f"(end time must be strictly after start time)."
            )
        elif s_min is None or e_min is None:
            reasons.append(
                "Warning: Proposed class has no usable time slot; timetable checks cannot be performed."
            )

        # 3a. Personal assigned / busy slots check
        busy_slots = candidate.get("assigned_slots") or candidate.get("busy_slots") or []
        if isinstance(busy_slots, str):
            busy_slots = [s.strip() for s in busy_slots.split(",") if s.strip()]

        for b_slot in busy_slots:
            collides, desc = slots_collide(proposed_slot_parsed, b_slot)
            if collides:
                status = STATUS_INELIGIBLE
                reasons.append(
                    f"Constraint violated: Timetable collision with slot '{b_slot}' ({desc})."
                )
                break

        # 3b. Existing institutional schedule verification (if provided)
        if existing_schedule is not None:
            cand_fid = candidate.get("faculty_id", candidate.get("id"))
            cand_name = candidate.get("faculty_name", candidate.get("name"))

            target_assign_id = None
            if subject:
                target_assign_id = (
                    subject.get("assignment_id")
                    or subject.get("schedule_id")
                )

            for entry in existing_schedule:
                # Check reliable assignment identifier to prevent false self-collisions
                entry_assign_id = (
                    entry.get("assignment_id", entry.get("schedule_id"))
                    if isinstance(entry, dict)
                    else getattr(entry, "assignment_id", getattr(entry, "schedule_id", None))
                )
                if entry_assign_id is None and target_assign_id is not None:
                    if isinstance(entry, dict) and "id" in entry:
                        entry_assign_id = entry.get("id")
                    elif hasattr(entry, "__dict__"):
                        entry_assign_id = getattr(entry, "id", None)

                is_same_assignment = False
                if target_assign_id is not None and entry_assign_id is not None:
                    is_same_assignment = str(target_assign_id).strip() == str(entry_assign_id).strip()

                if is_same_assignment:
                    # Exclude existing record ONLY when reliable assignment identifier establishes it is the same assignment
                    continue

                entry_fid = (
                    entry.get("faculty_id", entry.get("id"))
                    if isinstance(entry, dict)
                    else getattr(entry, "faculty_id", getattr(entry, "id", None))
                )
                entry_name = (
                    entry.get("faculty_name", entry.get("name"))
                    if isinstance(entry, dict)
                    else getattr(entry, "faculty_name", getattr(entry, "name", None))
                )
                entry_sec = (
                    entry.get("section")
                    if isinstance(entry, dict)
                    else getattr(entry, "section", None)
                )

                is_same_faculty = False
                if cand_fid is not None and entry_fid is not None:
                    is_same_faculty = str(cand_fid).strip() == str(entry_fid).strip()
                elif cand_name and entry_name:
                    is_same_faculty = (
                        str(cand_name).strip().lower() == str(entry_name).strip().lower()
                    )

                # 3b-1: Personal conflict in existing schedule (same faculty, overlapping times)
                if is_same_faculty:
                    collides, desc = slots_collide(proposed_slot_parsed, entry)
                    if collides:
                        status = STATUS_INELIGIBLE
                        entry_slot_str = (
                            entry.get("time_slot")
                            or entry.get("time")
                            or entry.get("slot")
                            or desc
                            if isinstance(entry, dict)
                            else getattr(entry, "time_slot", desc)
                        )
                        sec_info = f" in Section {entry_sec}" if entry_sec else ""
                        reasons.append(
                            f"Constraint violated: Faculty member already has an overlapping class '{entry_slot_str}'{sec_info}."
                        )
                        break

                # 3b-2: Section conflict in existing schedule (same section, overlapping times across ANY faculty)
                if (
                    target_section
                    and entry_sec
                    and str(target_section).strip().lower() == str(entry_sec).strip().lower()
                ):
                    collides, desc = slots_collide(proposed_slot_parsed, entry)
                    if collides:
                        status = STATUS_INELIGIBLE
                        fac_desc = (
                            entry_name
                            or (f"Faculty {entry_fid}" if entry_fid else "another faculty member")
                        )
                        entry_slot_str = (
                            entry.get("time_slot")
                            or entry.get("time")
                            or entry.get("slot")
                            or desc
                            if isinstance(entry, dict)
                            else getattr(entry, "time_slot", desc)
                        )
                        reasons.append(
                            f"Constraint violated: Section '{target_section}' has an overlapping class scheduled "
                            f"during '{entry_slot_str}' (assigned to {fac_desc})."
                        )
                        break

                # 3b-3: Consistent subject-faculty allocation per section
                # The same section must not receive multiple faculty allocations for the same subject.
                entry_subj = (
                    entry.get("subject") or entry.get("name") or entry.get("assigned_subject")
                    if isinstance(entry, dict)
                    else getattr(entry, "subject", getattr(entry, "name", getattr(entry, "assigned_subject", None)))
                )
                req_subj_name = (
                    subject.get("name") or subject.get("subject") or subject.get("assigned_subject")
                    if isinstance(subject, dict)
                    else getattr(subject, "name", getattr(subject, "subject", None))
                )
                is_sub = (
                    entry.get("is_substitution") or entry.get("is_substituted")
                    if isinstance(entry, dict)
                    else getattr(entry, "is_substitution", getattr(entry, "is_substituted", False))
                )
                if (
                    not is_sub
                    and target_section
                    and entry_sec
                    and str(target_section).strip().lower() == str(entry_sec).strip().lower()
                    and req_subj_name
                    and entry_subj
                    and normalize_text(req_subj_name) == normalize_text(entry_subj)
                ):
                    is_diff_fac = False
                    if cand_fid is not None and entry_fid is not None:
                        is_diff_fac = str(cand_fid).strip() != str(entry_fid).strip()
                    elif cand_name and entry_name:
                        is_diff_fac = str(cand_name).strip().lower() != str(entry_name).strip().lower()
                    if is_diff_fac:
                        status = STATUS_INELIGIBLE
                        fac_desc = entry_name or (f"Faculty ID {entry_fid}" if entry_fid else "another faculty member")
                        reasons.append(
                            f"Constraint violated: Subject '{req_subj_name}' in Section '{target_section}' is already allocated to {fac_desc}. Consistent faculty allocation required."
                        )
                        break
        else:
            reasons.append(
                "Notice: Complete institutional timetable not provided; cross-faculty section collisions could not be independently verified."
            )
    else:
        reasons.append(
            "Warning: Proposed class has no usable time slot; timetable checks cannot be performed."
        )
        reasons.append(
            "Notice: Proposed subject has no scheduled time slot; timetable conflict checks could not be performed."
        )
        # Check consistent subject-faculty allocation against existing schedule even without specific time slot
        if existing_schedule is not None and target_section:
            cand_fid = candidate.get("faculty_id", candidate.get("id"))
            cand_name = candidate.get("faculty_name", candidate.get("name"))
            req_subj_name = (
                subject.get("name") or subject.get("subject") or subject.get("assigned_subject")
                if isinstance(subject, dict)
                else getattr(subject, "name", getattr(subject, "subject", None))
            )
            if req_subj_name:
                for entry in existing_schedule:
                    is_sub = (
                        entry.get("is_substitution") or entry.get("is_substituted")
                        if isinstance(entry, dict)
                        else getattr(entry, "is_substitution", getattr(entry, "is_substituted", False))
                    )
                    if is_sub:
                        continue
                    entry_sec = entry.get("section") if isinstance(entry, dict) else getattr(entry, "section", None)
                    entry_subj = (
                        entry.get("subject") or entry.get("name") or entry.get("assigned_subject")
                        if isinstance(entry, dict)
                        else getattr(entry, "subject", getattr(entry, "name", getattr(entry, "assigned_subject", None)))
                    )
                    if (
                        entry_sec
                        and str(target_section).strip().lower() == str(entry_sec).strip().lower()
                        and entry_subj
                        and normalize_text(req_subj_name) == normalize_text(entry_subj)
                    ):
                        entry_fid = entry.get("faculty_id", entry.get("id")) if isinstance(entry, dict) else getattr(entry, "faculty_id", getattr(entry, "id", None))
                        entry_name = entry.get("faculty_name", entry.get("name")) if isinstance(entry, dict) else getattr(entry, "faculty_name", getattr(entry, "name", None))
                        is_diff = False
                        if cand_fid is not None and entry_fid is not None:
                            is_diff = str(cand_fid).strip() != str(entry_fid).strip()
                        elif cand_name and entry_name:
                            is_diff = str(cand_name).strip().lower() != str(entry_name).strip().lower()
                        if is_diff:
                            status = STATUS_INELIGIBLE
                            fac_desc = entry_name or (f"Faculty ID {entry_fid}" if entry_fid else "another faculty member")
                            reasons.append(
                                f"Constraint violated: Subject '{req_subj_name}' in Section '{target_section}' is already allocated to {fac_desc}. Consistent faculty allocation required."
                            )
                            break

    # 4. Qualification Verification (normalized exact alias matching)
    if subject and subject.get("required_qualification"):
        req_qual_raw = subject["required_qualification"]
        cand_qual_raw = candidate.get("qualification") or candidate.get("qualifications")

        if not cand_qual_raw or str(cand_qual_raw).strip() == "" or cand_qual_raw == []:
            if status != STATUS_INELIGIBLE:
                status = STATUS_NEEDS_REVIEW
            missing_fields.append("qualification")
            reasons.append(
                f"Needs review: Required qualification '{req_qual_raw}' missing in faculty record."
            )
        else:
            if isinstance(cand_qual_raw, (list, tuple)):
                cand_quals = [normalize_qualification(q) for q in cand_qual_raw if str(q).strip()]
            else:
                raw_items = [
                    q.strip()
                    for q in re.split(r"[,;/]|\band\b", str(cand_qual_raw))
                    if q.strip()
                ]
                cand_quals = [normalize_qualification(q) for q in raw_items]

            if isinstance(req_qual_raw, (list, tuple)):
                req_quals = [normalize_qualification(q) for q in req_qual_raw if str(q).strip()]
                matched = any(rq in cand_quals for rq in req_quals)
            else:
                req_qual_norm = normalize_qualification(req_qual_raw)
                matched = req_qual_norm in cand_quals

            if not matched:
                status = STATUS_INELIGIBLE
                reasons.append(
                    f"Constraint violated: Qualification '{cand_qual_raw}' does not satisfy required '{req_qual_raw}'."
                )
            else:
                reasons.append(f"Qualification verified: {cand_qual_raw}.")

    return {
        "status": status,
        "is_eligible": status == STATUS_ELIGIBLE,
        "can_allocate": status == STATUS_ELIGIBLE,
        "reasons": reasons,
        "missing_fields": missing_fields,
        "summary_message": "; ".join(reasons) if reasons else "All checks verified and passed.",
    }


def passes_basic_constraints(
    candidate: dict[str, Any],
    required_hours: float = 0,
    subject: dict[str, Any] | None = None,
    existing_schedule: list[Any] | None = None,
) -> tuple[bool, str]:
    """Backward-compatible constraint check helper.

    Returns:
        (allowed: bool, reason: str)
        where allowed is True ONLY if all constraints are verified and satisfied.
        If any required evidence is missing or a constraint is violated, allowed is False.
    """
    result = evaluate_constraints(
        candidate,
        required_hours=required_hours,
        subject=subject,
        existing_schedule=existing_schedule,
    )
    return result["is_eligible"], result["summary_message"]

