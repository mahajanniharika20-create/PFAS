"""Small illustrative constraints helper; real schedule checks need allocation records."""
def passes_basic_constraints(candidate: dict, required_hours: float = 0) -> tuple[bool, str]:
    cap = float(candidate["weekly_hour_cap"])
    current = float(candidate["current_weekly_hours"])
    if current + float(required_hours) > cap:
        return False, "Weekly hour cap would be exceeded."
    if candidate.get("available", True) is not True:
        return False, "Faculty member is unavailable."
    # This helper cannot check timetable overlaps or section conflicts without real schedule data.
    return True, "Basic checks passed."
