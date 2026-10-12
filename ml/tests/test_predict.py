"""Unit tests for the PFAS recommendation engine and constraint validation."""
import unittest
from ml.config import (
    STATUS_ELIGIBLE,
    STATUS_NEEDS_REVIEW,
    STATUS_INELIGIBLE,
    DEFAULT_POLICY_WEIGHTS,
)
from ml.constraints import (
    evaluate_constraints,
    passes_basic_constraints,
    slots_collide,
    parse_slot_interval,
    parse_time_to_minutes,
    inherit_meridian,
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


class RecommendationEngineTests(unittest.TestCase):
    """Test suite covering transparent scoring, strict constraints, and edge cases."""

    def test_valid_runtime_records(self):
        """Verify ranking on realistic records mirroring app.py structures."""
        candidates = [
            {
                "id": 1,
                "name": "Dr. Sharma",
                "department": "CSE",
                "domain": "AI",
                "weekly_hours": 10,
                "weekly_hour_cap": 16,
                "available": True,
                "preferred_subject": "Machine Learning",
                "preferred_time_slot": "Morning (09:00 AM - 12:15 PM)",
            },
            {
                "id": 2,
                "name": "Prof. Rao",
                "department": "CSE",
                "domain": "Networks",
                "weekly_hours": 14,
                "weekly_hour_cap": 16,
                "available": True,
                "preferred_subject": "Computer Networks",
                "preferred_time_slot": "Afternoon (01:15 PM - 04:15 PM)",
            },
        ]
        subject = {
            "name": "Machine Learning",
            "domain": "AI",
            "time_slot": "Morning (09:00 AM - 12:15 PM)",
            "required_hours": 3,
        }

        results = recommend_faculty(candidates, subject)
        self.assertEqual(len(results), 2)
        # Dr. Sharma matches domain, subject preference, slot, and fits workload
        self.assertEqual(results[0]["faculty_name"], "Dr. Sharma")
        self.assertEqual(results[0]["eligibility_status"], STATUS_ELIGIBLE)
        self.assertTrue(results[0]["eligible_under_basic_checks"])
        self.assertGreater(results[0]["suitability_score"], results[1]["suitability_score"])

    def test_missing_workload_cap_yields_needs_review(self):
        """Missing workload cap must yield NEEDS_REVIEW, never confirmed eligible."""
        cand = {
            "name": "Dr. Gupta",
            "domain": "Database",
            "weekly_hours": 8,
            # weekly_hour_cap omitted
            "available": True,
        }
        res = evaluate_constraints(cand, required_hours=3)
        self.assertEqual(res["status"], STATUS_NEEDS_REVIEW)
        self.assertFalse(res["is_eligible"])
        self.assertIn("weekly_hour_cap", res["missing_fields"])

        # Also verify via passes_basic_constraints backward-compatible helper
        allowed, msg = passes_basic_constraints(cand, required_hours=3)
        self.assertFalse(allowed)
        self.assertIn("Needs review", msg)

    def test_missing_availability_yields_needs_review(self):
        """Missing availability evidence must yield NEEDS_REVIEW, never confirmed eligible."""
        cand = {
            "name": "Dr. Gupta",
            "domain": "Database",
            "weekly_hours": 8,
            "weekly_hour_cap": 16,
            # available omitted
        }
        res = evaluate_constraints(cand, required_hours=3)
        self.assertEqual(res["status"], STATUS_NEEDS_REVIEW)
        self.assertFalse(res["is_eligible"])
        self.assertIn("available", res["missing_fields"])

    def test_missing_required_qualification_yields_needs_review(self):
        """Missing required qualification evidence must yield NEEDS_REVIEW."""
        cand = {
            "name": "Lecturer A",
            "domain": "AI",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
            # qualification omitted
        }
        subject = {
            "name": "Advanced AI",
            "domain": "AI",
            "required_qualification": "Ph.D.",
        }
        res = evaluate_constraints(cand, required_hours=3, subject=subject)
        self.assertEqual(res["status"], STATUS_NEEDS_REVIEW)
        self.assertFalse(res["is_eligible"])
        self.assertIn("qualification", res["missing_fields"])

    def test_distinct_domains_no_inferred_equivalence(self):
        """Data Science and Database are distinct; no equivalence is inferred."""
        cand = {
            "faculty_name": "Faculty DB",
            "domain": "Database",
            "weekly_hour_cap": 16,
            "weekly_hours": 6,
            "available": True,
        }
        subject = {
            "name": "Data Science Foundations",
            "domain": "Data Science",
        }
        score, breakdown, _ = score_candidate(cand, subject)
        self.assertEqual(breakdown["domain_match"], 0.0)

    def test_approved_domain_aliases(self):
        """Approved syntactic aliases match correctly with case/whitespace normalization."""
        self.assertEqual(normalize_domain("  AI/ML  "), "ai")
        self.assertEqual(normalize_domain("Artificial Intelligence"), "ai")
        self.assertEqual(normalize_domain("DBMS"), "database")

        cand = {"domain": "AI/ML", "weekly_hour_cap": 16, "weekly_hours": 4, "available": True}
        subject = {"name": "Intro to AI", "domain": "AI"}
        score, breakdown, _ = score_candidate(cand, subject)
        self.assertEqual(breakdown["domain_match"], DEFAULT_POLICY_WEIGHTS["domain_match"])

    def test_workload_limit_exceeded(self):
        """Workload cap exceeded must mark candidate as INELIGIBLE."""
        cand = {
            "name": "Overloaded Prof",
            "weekly_hours": 14,
            "weekly_hour_cap": 16,
            "available": True,
        }
        res = evaluate_constraints(cand, required_hours=4)
        self.assertEqual(res["status"], STATUS_INELIGIBLE)
        self.assertFalse(res["is_eligible"])
        self.assertTrue(any("cap exceeded" in r for r in res["reasons"]))

    def test_timetable_slot_conflict(self):
        """Overlapping timetable slots flag candidate as INELIGIBLE."""
        cand = {
            "name": "Busy Prof",
            "weekly_hours": 4,
            "weekly_hour_cap": 16,
            "available": True,
            "assigned_slots": ["Mon 09:00 AM - 10:00 AM", "Wed 10:00 AM - 11:00 AM"],
        }
        subject = {
            "name": "OS",
            "domain": "Systems",
            "time_slot": "Mon 09:00 AM - 10:00 AM",
        }
        res = evaluate_constraints(cand, required_hours=2, subject=subject)
        self.assertEqual(res["status"], STATUS_INELIGIBLE)
        self.assertFalse(res["is_eligible"])
        self.assertTrue(any("Timetable collision" in r for r in res["reasons"]))

    def test_empty_candidate_list(self):
        """Empty input list returns an empty list without fabricating candidates."""
        self.assertEqual(recommend_faculty([], "AI"), [])
        self.assertEqual(rank_candidates([], required_hours=3), [])

    def test_deterministic_tie_breaking(self):
        """Identical candidate scores break ties deterministically."""
        candidates = [
            {
                "faculty_id": 102,
                "name": "Faculty Z",
                "domain": "AI",
                "weekly_hours": 8,
                "weekly_hour_cap": 16,
                "available": True,
            },
            {
                "faculty_id": 101,
                "name": "Faculty A",
                "domain": "AI",
                "weekly_hours": 8,
                "weekly_hour_cap": 16,
                "available": True,
            },
        ]
        subject = {"name": "AI Basics", "domain": "AI"}
        results = recommend_faculty(candidates, subject)
        # Since scores and hours are identical, numeric faculty_id 101 sorts ahead of 102
        self.assertEqual(results[0]["faculty_id"], 101)
        self.assertEqual(results[1]["faculty_id"], 102)

    def test_zero_joblib_dependency(self):
        """Recommendation engine works without loading .joblib files."""
        cand = {
            "faculty_id": 1,
            "faculty_name": "Dr. Test",
            "domain": "AI",
            "weekly_hours": 6,
            "weekly_hour_cap": 14,
            "available": True,
        }
        score = predict_suitability(cand, subject={"name": "AI", "domain": "AI"})
        self.assertIsInstance(score, float)
        self.assertGreater(score, 0.0)

        ranked = rank_candidates([cand], required_hours=2)
        self.assertEqual(len(ranked), 1)
        self.assertIn("eligible_under_basic_checks", ranked[0])
        self.assertIn("suitability_score", ranked[0])

    def test_legacy_passes_basic_constraints_signature(self):
        """passes_basic_constraints must return tuple[bool, str]."""
        cand = {"weekly_hour_cap": 16, "current_weekly_hours": 8, "available": True}
        allowed, msg = passes_basic_constraints(cand, required_hours=2)
        self.assertIsInstance(allowed, bool)
        self.assertIsInstance(msg, str)
        self.assertTrue(allowed)

    def test_legacy_build_feature_frame(self):
        """build_feature_frame works on valid synthetic records and rejects missing columns."""
        valid_synthetic = {
            "faculty_domain": "AI",
            "subject_domain": "AI",
            "primary_preference": "AI",
            "secondary_preference": "Data Science",
            "experience_years": 8,
            "related_subject_experience_years": 5,
            "current_weekly_hours": 6,
            "weekly_hour_cap": 14,
        }
        df = build_feature_frame(valid_synthetic)
        self.assertEqual(len(df), 1)

        bad = dict(valid_synthetic)
        del bad["faculty_domain"]
        with self.assertRaises(ValueError):
            build_feature_frame(bad)

    # --------------------------------------------------------------------------
    # FOCUSED REGRESSION TESTS FOR VERIFIED CORRECTNESS BUGS
    # --------------------------------------------------------------------------

    def test_on_leave_independent_of_available(self):
        """Regression test for Bug 1: Active leave must mark candidate as INELIGIBLE even if available is True."""
        cand = {
            "name": "Prof. OnLeave",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
            "on_leave": True,
        }
        res = evaluate_constraints(cand, required_hours=2)
        self.assertEqual(res["status"], STATUS_INELIGIBLE)
        self.assertFalse(res["is_eligible"])
        self.assertTrue(any("on approved leave" in r for r in res["reasons"]))

        # Check through legacy helper as well
        allowed, msg = passes_basic_constraints(cand, required_hours=2)
        self.assertFalse(allowed)
        self.assertIn("approved leave", msg)

    def test_missing_current_workload_yields_needs_review(self):
        """Regression test for Bug 2: Missing current workload hours must yield NEEDS_REVIEW, never assumed 0."""
        cand = {
            "name": "Dr. UnknownWorkload",
            "weekly_hour_cap": 16,
            "available": True,
            # Neither current_weekly_hours nor weekly_hours provided
        }
        res = evaluate_constraints(cand, required_hours=2)
        self.assertEqual(res["status"], STATUS_NEEDS_REVIEW)
        self.assertFalse(res["is_eligible"])
        self.assertIn("current_weekly_hours", res["missing_fields"])

    def test_domain_exact_match_no_substring_containment(self):
        """Regression test for Bug 3: Substring containment must NOT match unrelated domains (e.g. AI != Blockchain)."""
        cand = {
            "name": "Dr. AI",
            "domain": "AI",
            "weekly_hours": 4,
            "weekly_hour_cap": 16,
            "available": True,
        }
        subject = {
            "name": "Blockchain Architecture",
            "domain": "Blockchain",
        }
        score, breakdown, _ = score_candidate(cand, subject)
        # 'ai' is inside 'blockchain', but must score 0.0 domain points
        self.assertEqual(breakdown["domain_match"], 0.0)

    def test_subject_preference_no_substring_containment(self):
        """Regression test for Bug 4: Substring containment must NOT match preferences (e.g. Algorithms != OS)."""
        cand = {
            "name": "Dr. Algo",
            "domain": "Algorithms",
            "preferred_subject": "Algorithms",
            "weekly_hours": 4,
            "weekly_hour_cap": 16,
            "available": True,
        }
        subject = {
            "name": "OS",
            "domain": "Systems",
        }
        score, breakdown, _ = score_candidate(cand, subject)
        # 'os' is inside 'algorithms', but must score 0.0 subject preference points
        self.assertEqual(breakdown["subject_preference"], 0.0)

    def test_qualification_exact_alias_no_substring_containment(self):
        """Regression test for Bug 5: Exact alias matching for qualifications; no unsafe substring matches."""
        cand_db = {
            "name": "Dr. DB",
            "domain": "Database",
            "qualification": "Database Administrator",
            "weekly_hours": 4,
            "weekly_hour_cap": 16,
            "available": True,
        }
        subject_ba = {
            "name": "Intro to Arts",
            "domain": "Humanities",
            "required_qualification": "BA",
        }
        # 'ba' is inside 'database', but must be INELIGIBLE
        res = evaluate_constraints(cand_db, required_hours=2, subject=subject_ba)
        self.assertEqual(res["status"], STATUS_INELIGIBLE)
        self.assertFalse(res["is_eligible"])

        # Legitimate alias: 'Ph.D.' satisfies required 'PhD'
        cand_phd = {
            "name": "Dr. Phd",
            "domain": "AI",
            "qualification": "Ph.D.",
            "weekly_hours": 4,
            "weekly_hour_cap": 16,
            "available": True,
        }
        subject_phd = {
            "name": "Advanced Research",
            "domain": "AI",
            "required_qualification": "PhD",
        }
        res_phd = evaluate_constraints(cand_phd, required_hours=2, subject=subject_phd)
        self.assertEqual(res_phd["status"], STATUS_ELIGIBLE)
        self.assertTrue(res_phd["is_eligible"])

    def test_non_finite_workload_values_rejected(self):
        """Regression test: Reject non-finite workload values (NaN, +inf, -inf) as INELIGIBLE."""
        base_valid = {
            "name": "Dr. Numbers",
            "domain": "AI",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }

        # 1. Non-finite current workload values
        for bad_val in [float("nan"), float("inf"), float("-inf")]:
            cand = dict(base_valid, weekly_hours=bad_val)
            res = evaluate_constraints(cand, required_hours=2)
            self.assertEqual(res["status"], STATUS_INELIGIBLE)
            self.assertFalse(res["is_eligible"])
            self.assertTrue(any("finite numbers" in r for r in res["reasons"]))

        # 2. Non-finite workload cap values
        for bad_val in [float("nan"), float("inf"), float("-inf")]:
            cand = dict(base_valid, weekly_hour_cap=bad_val)
            res = evaluate_constraints(cand, required_hours=2)
            self.assertEqual(res["status"], STATUS_INELIGIBLE)
            self.assertFalse(res["is_eligible"])
            self.assertTrue(any("finite numbers" in r for r in res["reasons"]))

        # 3. Non-finite required hours
        for bad_val in [float("nan"), float("inf"), float("-inf")]:
            res = evaluate_constraints(base_valid, required_hours=bad_val)
            self.assertEqual(res["status"], STATUS_INELIGIBLE)
            self.assertFalse(res["is_eligible"])
            self.assertTrue(any("finite numbers" in r for r in res["reasons"]))

    def test_normalize_faculty_record_preserves_section(self):
        """Verify section is preserved as a top-level field in normalized faculty records."""
        rec = normalize_faculty_record({"name": "Dr. Rao", "section": "B", "department": "CSE"})
        self.assertEqual(rec["section"], "B")

        rec_none = normalize_faculty_record({"name": "Dr. Rao", "department": "CSE"})
        self.assertIsNone(rec_none["section"])

    def test_explicit_subject_section_allocation(self):
        """Priority 1: Explicit subject section is carried into allocated_section."""
        cands = [{
            "id": 1,
            "name": "Prof. X",
            "domain": "AI",
            "section": "A",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }]
        # Subject specifies section B; candidate currently has section A
        subj = {"name": "AI", "domain": "AI", "section": "B"}
        ranked = recommend_faculty(cands, subj)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["allocated_section"], "B")

    def test_valid_candidate_section_fallback(self):
        """Priority 2/3: Valid candidate section used only when allocation matches assigned subject or target."""
        # Case A: Candidate specifies target_section for this allocation
        cands_target = [{
            "id": 1,
            "name": "Prof. Y",
            "domain": "AI",
            "target_section": "C",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }]
        ranked_target = recommend_faculty(cands_target, "AI")
        self.assertEqual(ranked_target[0]["allocated_section"], "C")

        # Case B: Allocation matches candidate's assigned subject, so section applies
        cands_match = [{
            "id": 2,
            "name": "Prof. Z",
            "domain": "AI",
            "assigned_subject": "Deep Learning",
            "section": "A",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }]
        ranked_match = recommend_faculty(cands_match, {"name": "Deep Learning", "domain": "AI"})
        self.assertEqual(ranked_match[0]["allocated_section"], "A")

    def test_missing_and_unverified_section_returns_none_never_invents(self):
        """When section is not specified by subject and cannot be verified from candidate, return None."""
        # Candidate has section A for 'Data Structures', but allocation is for 'Machine Learning'
        cands = [{
            "id": 1,
            "name": "Dr. Sharma",
            "domain": "AI",
            "assigned_subject": "Data Structures",
            "section": "A",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }]
        ranked = recommend_faculty(cands, {"name": "Machine Learning", "domain": "AI"})
        self.assertEqual(len(ranked), 1)
        # Must NOT assume Section A of Data Structures applies to Machine Learning
        self.assertIsNone(ranked[0]["allocated_section"])
        self.assertTrue(any("Allocated section not specified" in w for w in ranked[0]["warnings"]))


    # --------------------------------------------------------------------------
    # TIMETABLE, SECTION CONFLICT, AND WORKLOAD REGRESSION TESTS
    # --------------------------------------------------------------------------

    def test_two_faculty_assigned_to_same_section_overlapping_times(self):
        """Two faculty assigned to the same section at overlapping times marks candidate as INELIGIBLE."""
        cand = {
            "id": 10,
            "name": "Dr. Candidate",
            "domain": "AI",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }
        subject = {
            "name": "AI Systems",
            "domain": "AI",
            "section": "A",
            "time_slot": "Mon 09:00 AM - 10:00 AM",
            "required_hours": 2,
        }
        # Institutional schedule already has Prof. Existing in Section A at the same time
        existing_schedule = [{
            "faculty_id": 99,
            "faculty_name": "Prof. Existing",
            "section": "A",
            "time_slot": "Mon 09:00 AM - 10:00 AM",
        }]
        results = recommend_faculty([cand], subject, existing_schedule=existing_schedule)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["eligibility_status"], STATUS_INELIGIBLE)
        self.assertFalse(results[0]["eligible_under_basic_checks"])
        self.assertIn("Section 'A' has an overlapping class", results[0]["constraint_message"])

    def test_same_section_non_overlapping_classes_eligible(self):
        """Same section with non-overlapping classes on the same day is ELIGIBLE."""
        cand = {
            "id": 10,
            "name": "Dr. Candidate",
            "domain": "AI",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }
        subject = {
            "name": "AI Systems",
            "domain": "AI",
            "section": "A",
            "time_slot": "Mon 10:00 AM - 11:00 AM",
            "required_hours": 2,
        }
        existing_schedule = [{
            "faculty_id": 99,
            "faculty_name": "Prof. Existing",
            "section": "A",
            "time_slot": "Mon 09:00 AM - 10:00 AM",
        }]
        results = recommend_faculty([cand], subject, existing_schedule=existing_schedule)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["eligibility_status"], STATUS_ELIGIBLE)
        self.assertTrue(results[0]["eligible_under_basic_checks"])

    def test_different_sections_simultaneous_different_faculty_eligible(self):
        """Different sections taught simultaneously by different faculty are ELIGIBLE."""
        cand = {
            "id": 10,
            "name": "Dr. Candidate",
            "domain": "AI",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }
        subject = {
            "name": "AI Systems",
            "domain": "AI",
            "section": "B",
            "time_slot": "Mon 09:00 AM - 10:00 AM",
            "required_hours": 2,
        }
        # Prof. Existing teaches Section A at 09:00 - 10:00 AM
        existing_schedule = [{
            "faculty_id": 99,
            "faculty_name": "Prof. Existing",
            "section": "A",
            "time_slot": "Mon 09:00 AM - 10:00 AM",
        }]
        results = recommend_faculty([cand], subject, existing_schedule=existing_schedule)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["eligibility_status"], STATUS_ELIGIBLE)
        self.assertTrue(results[0]["eligible_under_basic_checks"])

    def test_same_faculty_overlapping_different_sections_ineligible(self):
        """Same faculty member assigned to different sections at overlapping times is INELIGIBLE."""
        cand = {
            "id": 10,
            "name": "Dr. MultiSection",
            "domain": "AI",
            "weekly_hours": 4,
            "weekly_hour_cap": 16,
            "available": True,
        }
        subject = {
            "name": "Machine Learning Lab",
            "domain": "AI",
            "section": "B",
            "time_slot": "Mon 09:30 AM - 10:30 AM",
            "required_hours": 2,
        }
        # Faculty 10 already has a class in Section A from 09:00 AM to 10:00 AM
        existing_schedule = [{
            "faculty_id": 10,
            "faculty_name": "Dr. MultiSection",
            "section": "A",
            "time_slot": "Mon 09:00 AM - 10:00 AM",
        }]
        results = recommend_faculty([cand], subject, existing_schedule=existing_schedule)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["eligibility_status"], STATUS_INELIGIBLE)
        self.assertFalse(results[0]["eligible_under_basic_checks"])
        self.assertIn("already has an overlapping class", results[0]["constraint_message"])

    def test_multi_section_allocation_within_workload_cap(self):
        """Multiple sections for one faculty member within workload cap are ELIGIBLE."""
        cand = {
            "id": 10,
            "name": "Dr. Hardworking",
            "domain": "AI",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }
        subject = {
            "name": "Advanced AI",
            "domain": "AI",
            "section": "B",
            "time_slot": "Tue 11:00 AM - 12:00 PM",
            "required_hours": 4,
        }
        # Faculty 10 already teaches Section A on Monday
        existing_schedule = [{
            "faculty_id": 10,
            "faculty_name": "Dr. Hardworking",
            "section": "A",
            "time_slot": "Mon 09:00 AM - 10:00 AM",
        }]
        results = recommend_faculty([cand], subject, existing_schedule=existing_schedule)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["eligibility_status"], STATUS_ELIGIBLE)
        self.assertTrue(results[0]["eligible_under_basic_checks"])
        self.assertEqual(results[0]["allocated_section"], "B")

    def test_multi_section_allocation_exceeding_workload_cap(self):
        """Multiple sections for one faculty member exceeding workload cap are INELIGIBLE."""
        cand = {
            "id": 10,
            "name": "Dr. HighLoad",
            "domain": "AI",
            "weekly_hours": 14,
            "weekly_hour_cap": 16,
            "available": True,
        }
        subject = {
            "name": "Advanced AI",
            "domain": "AI",
            "section": "B",
            "time_slot": "Tue 11:00 AM - 12:00 PM",
            "required_hours": 4,  # 14 + 4 = 18 > 16
        }
        results = recommend_faculty([cand], subject)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["eligibility_status"], STATUS_INELIGIBLE)
        self.assertFalse(results[0]["eligible_under_basic_checks"])
        self.assertIn("cap exceeded", results[0]["constraint_message"])

    def test_adjacent_time_slots_do_not_collide(self):
        """Adjacent slots (e.g. 09:00-10:00 and 10:00-11:00) must NOT collide."""
        collides, desc = slots_collide("Mon 09:00 AM - 10:00 AM", "Mon 10:00 AM - 11:00 AM")
        self.assertFalse(collides)

    def test_partial_overlap_time_slots_collide(self):
        """Partially overlapping slots (e.g. 09:00-10:30 and 10:00-11:00) MUST collide."""
        collides, desc = slots_collide("Mon 09:00 AM - 10:30 AM", "Mon 10:00 AM - 11:00 AM")
        self.assertTrue(collides)
        self.assertIn("Overlapping time interval", desc)

    def test_different_days_same_time_do_not_collide(self):
        """Slots on different days (e.g. Mon vs Tue) must NOT collide."""
        collides, desc = slots_collide("Mon 09:00 AM - 10:00 AM", "Tue 09:00 AM - 10:00 AM")
        self.assertFalse(collides)
        self.assertIn("Different scheduled days", desc)

    def test_capacity_score_with_nan_workload_awarded_zero(self):
        """Candidate with NaN workload must receive 0 capacity points, not 15 free points."""
        cand = {
            "faculty_id": 1,
            "domain": "AI",
            "weekly_hour_cap": 16,
            "weekly_hours": float("nan"),
            "available": True,
        }
        subject = {"name": "AI", "domain": "AI"}
        score, breakdown, warnings = score_candidate(cand, subject)
        self.assertEqual(breakdown["capacity_balance"], 0.0)
        self.assertTrue(any("Non-finite workload numbers" in w for w in warnings))

    def test_candidate_deduplication_in_recommend_faculty(self):
        """Duplicate candidate records in input are deduplicated deterministically."""
        cand1 = {"id": 1, "name": "Prof. A", "domain": "AI", "weekly_hours": 6, "weekly_hour_cap": 16, "available": True}
        cand2 = {"id": 1, "name": "Prof. A", "domain": "AI", "weekly_hours": 6, "weekly_hour_cap": 16, "available": True}
        results = recommend_faculty([cand1, cand2], "AI")
        self.assertEqual(len(results), 1)

    def test_global_timetable_omitted_diagnostic_warning(self):
        """When existing_schedule is None, engine outputs explicit diagnostic warning."""
        cand = {"id": 1, "name": "Prof. A", "domain": "AI", "weekly_hours": 6, "weekly_hour_cap": 16, "available": True}
        results = recommend_faculty([cand], "AI", existing_schedule=None)
        self.assertEqual(len(results), 1)
        self.assertTrue(any("Global timetable not supplied" in w for w in results[0]["warnings"]))

    def test_time_slot_preference_window_alignment(self):
        """Candidate with 'Morning (09:00 AM - 12:15 PM)' matches morning class slot."""
        cand = {
            "id": 1,
            "domain": "AI",
            "preferred_time_slot": "Morning (09:00 AM - 12:15 PM)",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }
        subject = {"name": "AI", "domain": "AI", "time_slot": "09:00 AM - 10:00 AM"}
        _, breakdown, _ = score_candidate(cand, subject)
        self.assertEqual(breakdown["slot_preference"], DEFAULT_POLICY_WEIGHTS["slot_preference"])

    def test_orm_like_object_attribute_extraction(self):
        """Normalize faculty record extracts attributes via getattr for ORM models."""
        class MockFaculty:
            def __init__(self):
                self.id = 55
                self.name = "Prof. ORM"
                self.department = "CSE"
                self.domain = "AI"
                self.weekly_hours = 8
                self.weekly_hour_cap = 16
                self.available = True
                self.section = "C"

        rec = normalize_faculty_record(MockFaculty())
        self.assertEqual(rec["faculty_id"], 55)
        self.assertEqual(rec["faculty_name"], "Prof. ORM")
        self.assertEqual(rec["section"], "C")

    def test_subject_dict_key_harmonization(self):
        """Engine normalizes 'subject' or 'name' interchangeably without failing match."""
        cand = {
            "id": 1,
            "domain": "AI",
            "assigned_subject": "Operating Systems",
            "section": "B",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }
        subj = {"subject": "Operating Systems", "domain": "AI"}
        results = recommend_faculty([cand], subj)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["allocated_section"], "B")

    # --- REGRESSION TESTS FOR CONFIRMED FIXES ---

    def test_fix1_reject_malformed_and_zero_duration_proposed_intervals(self):
        """Fix 1: Reject malformed or zero-duration proposed intervals; do not treat as conflict-free."""
        cand = {
            "id": 1,
            "name": "Prof. Smith",
            "domain": "AI",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }

        # Zero-duration interval (09:00 AM - 09:00 AM)
        res_zero = evaluate_constraints(
            cand,
            required_hours=2,
            subject={"name": "AI", "time_slot": "Mon 09:00 AM - 09:00 AM"},
        )
        self.assertEqual(res_zero["status"], STATUS_INELIGIBLE)
        self.assertFalse(res_zero["is_eligible"])
        self.assertTrue(any("malformed or zero-duration" in r for r in res_zero["reasons"]))

        # Inverted interval (10:00 AM - 09:00 AM)
        res_inv = evaluate_constraints(
            cand,
            required_hours=2,
            subject={"name": "AI", "time_slot": "Mon 10:00 AM - 09:00 AM"},
        )
        self.assertEqual(res_inv["status"], STATUS_INELIGIBLE)
        self.assertFalse(res_inv["is_eligible"])
        self.assertTrue(any("malformed or zero-duration" in r for r in res_inv["reasons"]))

        # Malformed interval syntax (unparseable end time)
        res_malformed = evaluate_constraints(
            cand,
            required_hours=2,
            subject={"name": "AI", "time_slot": "Mon 09:00 AM - invalid"},
        )
        self.assertEqual(res_malformed["status"], STATUS_INELIGIBLE)
        self.assertFalse(res_malformed["is_eligible"])

        # slots_collide must NOT treat malformed or zero-duration slots as conflict-free
        c_zero, _ = slots_collide("09:00 AM - 09:00 AM", "09:00 AM - 10:00 AM")
        self.assertTrue(c_zero)
        c_malf, _ = slots_collide("09:00 AM - invalid", "09:00 AM - 10:00 AM")
        self.assertTrue(c_malf)

    def test_fix2_warning_when_proposed_class_has_no_usable_time_slot(self):
        """Fix 2: Add a clear warning when a proposed class has no usable time slot and timetable checks cannot be performed."""
        cand = {
            "id": 1,
            "name": "Prof. Jones",
            "domain": "AI",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }

        # Subject without time slot
        results_no_slot = recommend_faculty([cand], {"name": "AI", "domain": "AI"})
        self.assertEqual(len(results_no_slot), 1)
        expected_warning = "Warning: Proposed class has no usable time slot; timetable checks cannot be performed."
        self.assertTrue(any(expected_warning in w for w in results_no_slot[0]["warnings"]))

        # Subject with non-interval unusable text
        results_tbd = recommend_faculty([cand], {"name": "AI", "domain": "AI", "time_slot": "TBD"})
        self.assertEqual(len(results_tbd), 1)
        self.assertTrue(any(expected_warning in w for w in results_tbd[0]["warnings"]))

        # Check evaluate_constraints directly
        eval_res = evaluate_constraints(cand, subject={"name": "AI", "time_slot": ""})
        self.assertTrue(any(expected_warning in r for r in eval_res["reasons"]))

    def test_fix3_duplicate_candidate_conservative_evidence_handling(self):
        """Fix 3: Duplicate rows cannot silently discard leave status, higher workload, or conflicting eligibility evidence."""
        # 3a. Conflicting leave status: on_leave=True must prevail conservatively
        cand1_leave = {"id": 10, "name": "Dr. Dual", "domain": "AI", "weekly_hours": 6, "weekly_hour_cap": 16, "available": True, "on_leave": False}
        cand2_leave = {"id": 10, "name": "Dr. Dual", "domain": "AI", "weekly_hours": 6, "weekly_hour_cap": 16, "available": True, "on_leave": True}
        results_leave = recommend_faculty([cand1_leave, cand2_leave], "AI")
        self.assertEqual(len(results_leave), 1)
        self.assertEqual(results_leave[0]["eligibility_status"], STATUS_INELIGIBLE)
        self.assertFalse(results_leave[0]["eligible_under_basic_checks"])
        self.assertIn("approved leave", results_leave[0]["constraint_message"])

        # 3b. Conflicting workload: higher workload (14h) must prevail conservatively over lower (4h)
        cand1_load = {"id": 11, "name": "Dr. Load", "domain": "AI", "weekly_hours": 4, "weekly_hour_cap": 16, "available": True}
        cand2_load = {"id": 11, "name": "Dr. Load", "domain": "AI", "weekly_hours": 14, "weekly_hour_cap": 16, "available": True}
        # Required hours = 4: 4 + 4 = 8 <= 16 (eligible), but 14 + 4 = 18 > 16 (ineligible)
        results_load = recommend_faculty([cand1_load, cand2_load], "AI", required_hours=4)
        self.assertEqual(len(results_load), 1)
        self.assertEqual(results_load[0]["eligibility_status"], STATUS_INELIGIBLE)
        self.assertFalse(results_load[0]["eligible_under_basic_checks"])
        self.assertIn("Weekly hour cap exceeded", results_load[0]["constraint_message"])

        # 3c. Conflicting availability: available=False must prevail conservatively over available=True
        cand1_avail = {"id": 12, "name": "Dr. Avail", "domain": "AI", "weekly_hours": 6, "weekly_hour_cap": 16, "available": True}
        cand2_avail = {"id": 12, "name": "Dr. Avail", "domain": "AI", "weekly_hours": 6, "weekly_hour_cap": 16, "available": False}
        results_avail = recommend_faculty([cand1_avail, cand2_avail], "AI")
        self.assertEqual(len(results_avail), 1)
        self.assertEqual(results_avail[0]["eligibility_status"], STATUS_INELIGIBLE)
        self.assertIn("marked unavailable", results_avail[0]["constraint_message"])

        # 3d. Conflicting domains: do not blindly match domain when duplicate rows provide conflicting domain evidence
        cand1_dom = {"id": 13, "name": "Dr. Domain", "domain": "AI", "weekly_hours": 6, "weekly_hour_cap": 16, "available": True}
        cand2_dom = {"id": 13, "name": "Dr. Domain", "domain": "Mechanical", "weekly_hours": 6, "weekly_hour_cap": 16, "available": True}
        results_dom = recommend_faculty([cand1_dom, cand2_dom], {"name": "AI", "domain": "AI"})
        self.assertEqual(len(results_dom), 1)
        self.assertEqual(results_dom[0]["score_breakdown"]["domain_match"], 0.0)
        self.assertTrue(any("Conflicting domains" in w for w in results_dom[0]["warnings"]))

    def test_fix4_am_pm_inheritance_and_edge_cases(self):
        """Fix 4: Correct AM/PM inheritance for intervals such as 01:00 - 02:00 PM while preserving noon, midnight, and 24h."""
        # 01:00 - 02:00 PM should inherit PM for start time -> 13:00 to 14:00 (780 to 840 min)
        self.assertEqual(inherit_meridian("01:00", "02:00 PM"), "01:00 pm")
        p = parse_slot_interval("01:00 - 02:00 PM")
        self.assertEqual(p["start_min"], 780)
        self.assertEqual(p["end_min"], 840)

        # 11:00 - 01:00 PM should infer 11:00 AM -> 11:00 to 13:00 (660 to 780 min)
        self.assertEqual(inherit_meridian("11:00", "01:00 PM"), "11:00 am")
        p_span = parse_slot_interval("11:00 - 01:00 PM")
        self.assertEqual(p_span["start_min"], 660)
        self.assertEqual(p_span["end_min"], 780)

        # Noon edge cases
        self.assertEqual(inherit_meridian("11:00", "12:00 PM"), "11:00 am")
        p_noon1 = parse_slot_interval("11:00 - 12:00 PM")
        self.assertEqual(p_noon1["start_min"], 660)
        self.assertEqual(p_noon1["end_min"], 720)

        self.assertEqual(inherit_meridian("12:00", "01:00 PM"), "12:00 pm")
        p_noon2 = parse_slot_interval("12:00 - 01:00 PM")
        self.assertEqual(p_noon2["start_min"], 720)
        self.assertEqual(p_noon2["end_min"], 780)

        # Midnight edge case
        self.assertEqual(inherit_meridian("12:00", "01:00 AM"), "12:00 am")
        p_mid = parse_slot_interval("12:00 - 01:00 AM")
        self.assertEqual(p_mid["start_min"], 0)
        self.assertEqual(p_mid["end_min"], 60)

        # Explicit meridians preserved
        self.assertEqual(inherit_meridian("01:00 AM", "02:00 AM"), "01:00 AM")
        p_exp = parse_slot_interval("01:00 AM - 02:00 AM")
        self.assertEqual(p_exp["start_min"], 60)
        self.assertEqual(p_exp["end_min"], 120)

        # 24-hour times preserved
        p_24 = parse_slot_interval("13:00 - 14:00")
        self.assertEqual(p_24["start_min"], 780)
        self.assertEqual(p_24["end_min"], 840)

        # Overlap collision correctly identified between inherited PM and explicit PM
        c_overlap, _ = slots_collide("01:00 - 02:00 PM", "01:30 PM - 02:30 PM")
        self.assertTrue(c_overlap)

        # No collision between afternoon class and morning class
        c_no_overlap, _ = slots_collide("01:00 - 02:00 PM", "01:00 AM - 02:00 AM")
        self.assertFalse(c_no_overlap)

    def test_fix5_prevent_false_self_collisions_using_reliable_assignment_identifier(self):
        """Fix 5: Exclude existing record ONLY when reliable assignment identifier establishes it is the same assignment."""
        cand = {
            "id": 1,
            "name": "Prof. Alan",
            "domain": "AI",
            "weekly_hours": 6,
            "weekly_hour_cap": 16,
            "available": True,
        }

        # 5a. Re-evaluating existing assignment with MATCHING assignment_id does NOT self-collide
        subject_reeval = {
            "name": "Algorithms",
            "domain": "AI",
            "time_slot": "Mon 09:00 AM - 10:00 AM",
            "assignment_id": "assign_101",
        }
        existing_schedule_same = [{
            "assignment_id": "assign_101",
            "faculty_id": 1,
            "subject": "Algorithms",
            "time_slot": "Mon 09:00 AM - 10:00 AM",
        }]
        res_same = recommend_faculty([cand], subject_reeval, existing_schedule=existing_schedule_same)
        self.assertEqual(len(res_same), 1)
        self.assertEqual(res_same[0]["eligibility_status"], STATUS_ELIGIBLE)
        self.assertTrue(res_same[0]["eligible_under_basic_checks"])

        # 5b. Re-evaluating against a DIFFERENT assignment with assignment_id="assign_999" MUST collide
        existing_schedule_diff = [{
            "assignment_id": "assign_999",
            "faculty_id": 1,
            "subject": "Algorithms",
            "time_slot": "Mon 09:00 AM - 10:00 AM",
        }]
        res_diff = recommend_faculty([cand], subject_reeval, existing_schedule=existing_schedule_diff)
        self.assertEqual(len(res_diff), 1)
        self.assertEqual(res_diff[0]["eligibility_status"], STATUS_INELIGIBLE)
        self.assertFalse(res_diff[0]["eligible_under_basic_checks"])
        self.assertIn("overlapping class", res_diff[0]["constraint_message"])

        # 5c. Without assignment identifier, do NOT ignore other class merely because subject/faculty/time match
        subject_no_id = {
            "name": "Algorithms",
            "domain": "AI",
            "time_slot": "Mon 09:00 AM - 10:00 AM",
        }
        res_no_id = recommend_faculty([cand], subject_no_id, existing_schedule=existing_schedule_same)
        self.assertEqual(len(res_no_id), 1)
        self.assertEqual(res_no_id[0]["eligibility_status"], STATUS_INELIGIBLE)
        self.assertFalse(res_no_id[0]["eligible_under_basic_checks"])


class TimetableGeneratorTests(unittest.TestCase):
    """Integration and regression test suite for the constraint-first timetable generator."""

    def setUp(self):
        self.sample_faculty = [
            {"id": 1, "name": "Dr. M. K. Sharma", "department": "CSE", "weekly_hours": 0, "weekly_hour_cap": 16, "assigned_subject": "Data Structures", "domain": "Algorithms", "year": "2", "section": "A", "available": True, "on_leave": False},
            {"id": 2, "name": "Prof. R. V. Rao", "department": "CSE", "weekly_hours": 0, "weekly_hour_cap": 16, "assigned_subject": "Machine Learning", "domain": "AI/ML", "year": "3", "section": "B", "available": True, "on_leave": False},
            {"id": 3, "name": "Dr. S. K. Gupta", "department": "CSE", "weekly_hours": 0, "weekly_hour_cap": 16, "assigned_subject": "Database Systems", "domain": "Database", "year": "2", "section": "A", "available": True, "on_leave": False},
            {"id": 4, "name": "Prof. A. N. Reddy", "department": "AI_DS", "weekly_hours": 0, "weekly_hour_cap": 16, "assigned_subject": "Deep Learning", "domain": "AI/ML", "year": "4", "section": "A", "available": True, "on_leave": False},
            {"id": 5, "name": "Dr. P. C. Mehta", "department": "AI_DS", "weekly_hours": 0, "weekly_hour_cap": 16, "assigned_subject": "Computer Networks", "domain": "Networks", "year": "3", "section": "C", "available": True, "on_leave": False},
        ]

        self.sample_requirements = [
            {"name": "Data Structures", "domain": "Algorithms", "section": "A", "lecture_hours": 3, "lab_hours": 2, "department": "CSE"},
            {"name": "Database Systems", "domain": "Database", "section": "A", "lecture_hours": 3, "lab_hours": 0, "department": "CSE"},
            {"name": "Machine Learning", "domain": "AI/ML", "section": "B", "lecture_hours": 3, "lab_hours": 2, "department": "CSE"},
            {"name": "Deep Learning", "domain": "AI/ML", "section": "A", "lecture_hours": 2, "lab_hours": 0, "department": "AI_DS"},
            {"name": "Computer Networks", "domain": "Networks", "section": "C", "lecture_hours": 3, "lab_hours": 2, "department": "AI_DS"},
        ]

    def test_generator_creates_valid_schedule_without_hardcoded_template(self):
        """Generator creates a complete, valid timetable where Section A and faculty do not collide."""
        res = generate_timetable(
            requirements=self.sample_requirements,
            faculty_pool=self.sample_faculty,
            seed=42,
        )
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["unscheduled_count"], 0)
        self.assertTrue(res["validation"]["is_valid"])
        self.assertEqual(len(res["validation"]["errors"]), 0)

        # Check section collisions: Section A must NEVER have overlapping classes
        sec_a_classes = [a for a in res["assignments"] if a["section"] == "A"]
        for i, c1 in enumerate(sec_a_classes):
            for j, c2 in enumerate(sec_a_classes):
                if i != j and c1["day"] == c2["day"]:
                    collides, _ = slots_collide(c1["time_slot"], c2["time_slot"])
                    self.assertFalse(collides, f"Section A collision between {c1['subject']} and {c2['subject']} on {c1['day']}")

        # Check faculty collisions: Each faculty member has no overlaps
        for fac in self.sample_faculty:
            fid = fac["id"]
            fac_classes = [a for a in res["assignments"] if a["faculty_id"] == fid]
            for i, c1 in enumerate(fac_classes):
                for j, c2 in enumerate(fac_classes):
                    if i != j and c1["day"] == c2["day"]:
                        collides, _ = slots_collide(c1["time_slot"], c2["time_slot"])
                        self.assertFalse(collides, f"Faculty {fid} collision on {c1['day']}")

        # Verify master grid is rendered
        self.assertEqual(len(res["master_grid"]), 5)
        # Dr. Sharma and Dr. Gupta have distinct schedules
        sharma_slots = [s for item in res["master_grid"] if "Sharma" in str(item["faculty"]) for s in item["slots"]]
        gupta_slots = [s for item in res["master_grid"] if "Gupta" in str(item["faculty"]) for s in item["slots"]]
        self.assertNotEqual(sharma_slots, gupta_slots)

    def test_distinct_subjects_dbms_and_data_systems_not_conflated(self):
        """DBMS and Data Systems are kept distinct and mapped to their appropriate domains."""
        faculty = [
            {"id": 1, "name": "Dr. DB", "domain": "Database", "weekly_hours": 0, "weekly_hour_cap": 16, "available": True},
            {"id": 2, "name": "Dr. Sys", "domain": "Distributed Systems", "weekly_hours": 0, "weekly_hour_cap": 16, "available": True},
        ]
        requirements = [
            {"name": "DBMS", "domain": "Database", "section": "A", "lecture_hours": 2},
            {"name": "Data Systems", "domain": "Distributed Systems", "section": "B", "lecture_hours": 2},
        ]
        res = generate_timetable(requirements, faculty, seed=1)
        self.assertEqual(res["status"], "SUCCESS")
        db_asgn = [a for a in res["assignments"] if a["subject"] == "DBMS"]
        ds_asgn = [a for a in res["assignments"] if a["subject"] == "Data Systems"]
        self.assertTrue(all(a["faculty_id"] == 1 for a in db_asgn))
        self.assertTrue(all(a["faculty_id"] == 2 for a in ds_asgn))

    def test_reproducibility_with_seed(self):
        """Same seed yields identical schedules; different seeds produce valid alternative schedules."""
        res1 = generate_timetable(self.sample_requirements, self.sample_faculty, seed=123)
        res2 = generate_timetable(self.sample_requirements, self.sample_faculty, seed=123)
        self.assertEqual(res1["assignments"], res2["assignments"])

        res3 = generate_timetable(self.sample_requirements, self.sample_faculty, seed=777)
        self.assertEqual(res3["status"], "SUCCESS")
        self.assertTrue(res3["validation"]["is_valid"])

    def test_workload_hard_maximum_exact_limit_and_overflow(self):
        """Weekly workload limit is a hard cap: accepts at limit, rejects when exceeding by 1 hour."""
        faculty = [{
            "id": 1,
            "name": "Prof. Strict",
            "domain": "AI",
            "weekly_hours": 8,
            "weekly_hour_cap": 12,
            "available": True,
        }]

        # Exactly at limit: 8 + 4 = 12 <= 12
        res_exact = generate_timetable(
            requirements=[{"name": "AI Class", "domain": "AI", "section": "A", "lecture_hours": 4}],
            faculty_pool=faculty,
            seed=1,
        )
        self.assertEqual(res_exact["status"], "SUCCESS")
        self.assertEqual(len(res_exact["assignments"]), 4)
        self.assertEqual(res_exact["faculty_workloads"][1]["total_weekly_hours"], 12.0)

        # Exceeding limit by 1h: 8 + 5 = 13 > 12 -> cannot allocate
        res_overflow = generate_timetable(
            requirements=[{"name": "AI Class", "domain": "AI", "section": "A", "lecture_hours": 5}],
            faculty_pool=faculty,
            seed=1,
        )
        self.assertEqual(res_overflow["unscheduled_count"], 5)
        self.assertEqual(len(res_overflow["assignments"]), 0)

        # Baseline already at limit (12h) -> cannot allocate 1 additional hour
        faculty_maxed = [{
            "id": 2,
            "name": "Prof. Full",
            "domain": "AI",
            "weekly_hours": 12,
            "weekly_hour_cap": 12,
            "available": True,
        }]
        res_maxed = generate_timetable(
            requirements=[{"name": "AI Class", "domain": "AI", "section": "A", "lecture_hours": 1}],
            faculty_pool=faculty_maxed,
            seed=1,
        )
        self.assertEqual(res_maxed["unscheduled_count"], 1)

    def test_cumulative_workload_tracking_across_multiple_classes(self):
        """Cumulative workload is tracked across multiple subjects and sections."""
        faculty = [{
            "id": 1,
            "name": "Prof. Multi",
            "domain": "AI",
            "weekly_hours": 6,
            "weekly_hour_cap": 12,
            "available": True,
        }]
        # Three requirements of 2h, 3h, 2h (total 7h proposed; 6 + 7 = 13 > 12h cap)
        reqs = [
            {"name": "AI Lec", "domain": "AI", "section": "A", "lecture_hours": 2},
            {"name": "AI Seminar", "domain": "AI", "section": "B", "lecture_hours": 3},
            {"name": "AI Workshop", "domain": "AI", "section": "C", "lecture_hours": 2},
        ]
        res = generate_timetable(reqs, faculty, seed=1)
        # Should allocate 5 hours (6 + 5 = 11 <= 12), and leave the remaining 2 hours unscheduled
        self.assertEqual(len(res["assignments"]), 5)
        self.assertEqual(res["unscheduled_count"], 2)
        self.assertTrue(res["faculty_workloads"][1]["total_weekly_hours"] <= 12.0)
        self.assertTrue(res["validation"]["is_valid"])

    def test_ineligible_faculty_on_leave_or_unavailable_never_allocated(self):
        """Faculty on leave or marked unavailable are strictly rejected."""
        faculty = [
            {"id": 1, "name": "Dr. Leave", "domain": "AI", "weekly_hours": 0, "weekly_hour_cap": 16, "available": True, "on_leave": True},
            {"id": 2, "name": "Dr. Unavail", "domain": "AI", "weekly_hours": 0, "weekly_hour_cap": 16, "available": False, "on_leave": False},
        ]
        reqs = [{"name": "AI", "domain": "AI", "section": "A", "lecture_hours": 2}]
        res = generate_timetable(reqs, faculty)
        self.assertEqual(res["unscheduled_count"], 2)
        self.assertEqual(len(res["assignments"]), 0)

    def test_existing_schedule_collision_and_assignment_id_match(self):
        """Collision checks prevent overlap with existing institutional timetable."""
        faculty = [{"id": 1, "name": "Dr. Test", "domain": "AI", "weekly_hours": 0, "weekly_hour_cap": 16, "available": True}]
        existing = [{
            "assignment_id": "ext_99",
            "faculty_id": 1,
            "subject": "Existing Class",
            "section": "A",
            "day": "Mon",
            "time_slot": "09:00 AM - 10:00 AM",
        }]
        req = [{"name": "New AI", "domain": "AI", "section": "A", "lecture_hours": 1}]
        res = generate_timetable(req, faculty, existing_schedule=existing, days=["Mon"], time_slots=["09:00 AM - 10:00 AM"])
        # Mon 9am is occupied by existing schedule -> cannot allocate
        self.assertEqual(res["unscheduled_count"], 1)

        # But if re-evaluating the SAME assignment (matching assignment_id="ext_99"), self-collision is excluded
        req_same = [{
            "assignment_id": "ext_99",
            "name": "New AI",
            "domain": "AI",
            "section": "A",
            "lecture_hours": 1,
        }]
        res_same = generate_timetable(req_same, faculty, existing_schedule=existing, days=["Mon"], time_slots=["09:00 AM - 10:00 AM"])
        self.assertEqual(res_same["status"], "SUCCESS")
        self.assertEqual(len(res_same["assignments"]), 1)

    def test_independent_validator_detects_corrupted_schedule(self):
        """Independent validator correctly catches faculty overlaps, section overlaps, and cap breaches."""
        faculty = [{"id": 1, "name": "Dr. Corrupt", "domain": "AI", "_clean_baseline_hours": 10.0, "_clean_cap_hours": 12.0, "on_leave": False, "available": True}]

        # 1. Overlapping assignments for same faculty on same day
        bad_asgns_overlap = [
            {"assignment_id": "1", "subject": "AI 1", "faculty_id": 1, "faculty_name": "Dr. Corrupt", "section": "A", "day": "Mon", "time_slot": "09:00 AM - 10:00 AM", "duration_hours": 1.0},
            {"assignment_id": "2", "subject": "AI 2", "faculty_id": 1, "faculty_name": "Dr. Corrupt", "section": "B", "day": "Mon", "time_slot": "09:30 AM - 10:30 AM", "duration_hours": 1.0},
        ]
        val_res = validate_timetable(bad_asgns_overlap, faculty_pool=faculty)
        self.assertFalse(val_res["is_valid"])
        self.assertTrue(any("Faculty collision" in err for err in val_res["errors"]))

        # 2. Section collision across different faculty
        bad_asgns_sec = [
            {"assignment_id": "1", "subject": "AI 1", "faculty_id": 1, "faculty_name": "Dr. One", "section": "A", "day": "Mon", "time_slot": "09:00 AM - 10:00 AM", "duration_hours": 1.0},
            {"assignment_id": "2", "subject": "AI 2", "faculty_id": 2, "faculty_name": "Dr. Two", "section": "A", "day": "Mon", "time_slot": "09:00 AM - 10:00 AM", "duration_hours": 1.0},
        ]
        val_sec = validate_timetable(bad_asgns_sec)
        self.assertFalse(val_sec["is_valid"])
        self.assertTrue(any("Section collision" in err for err in val_sec["errors"]))

        # 3. Workload cap exceeded (baseline 10 + 3 = 13 > 12)
        bad_asgns_workload = [
            {"assignment_id": "1", "subject": "AI 1", "faculty_id": 1, "faculty_name": "Dr. Corrupt", "section": "A", "day": "Mon", "time_slot": "09:00 AM - 10:00 AM", "duration_hours": 3.0},
        ]
        val_wl = validate_timetable(bad_asgns_workload, faculty_pool=faculty)
        self.assertFalse(val_wl["is_valid"])
        self.assertTrue(any("exceeds weekly workload limit" in err for err in val_wl["errors"]))

    def test_different_lecture_and_lab_durations_accounted_accurately(self):
        """Lectures (1h) and Labs (2h) are accounted accurately in faculty workload."""
        faculty = [{"id": 1, "name": "Dr. Lab", "domain": "AI", "weekly_hours": 0, "weekly_hour_cap": 16, "available": True}]
        # 1 lecture (1h) + 1 lab (2h) = 3h total
        req = [{"name": "AI Lab Course", "domain": "AI", "section": "A", "lecture_hours": 1, "lab_hours": 2}]
        res = generate_timetable(req, faculty, seed=1)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(len(res["assignments"]), 2)
        lec_asgn = [a for a in res["assignments"] if not a["is_lab"]][0]
        lab_asgn = [a for a in res["assignments"] if a["is_lab"]][0]
        self.assertEqual(lec_asgn["duration_hours"], 1.0)
        self.assertEqual(lab_asgn["duration_hours"], 2.0)
        self.assertEqual(res["faculty_workloads"][1]["total_weekly_hours"], 3.0)

    def test_non_finite_nan_and_infinite_workloads_rejected_in_generator(self):
        """Non-finite, NaN, and negative workload values in faculty pool prevent allocation."""
        faculty_nan = [{"id": 1, "name": "Dr. NaN", "domain": "AI", "weekly_hours": float("nan"), "weekly_hour_cap": 16, "available": True}]
        req = [{"name": "AI", "domain": "AI", "section": "A", "lecture_hours": 2}]
        res_nan = generate_timetable(req, faculty_nan)
        self.assertEqual(res_nan["unscheduled_count"], 2)

    def test_grid_formatting_helpers(self):
        """Master grid and section grid formatting conform to frontend data contract."""
        faculty_subset = [self.sample_faculty[0], self.sample_faculty[2]]
        res = generate_timetable(self.sample_requirements[:2], faculty_subset, seed=42)
        self.assertEqual(res["status"], "SUCCESS")
        master_grid = format_master_grid(res["assignments"], faculty_subset)
        self.assertEqual(len(master_grid), 2)
        self.assertIn("faculty", master_grid[0])
        self.assertIn("slots", master_grid[0])
        self.assertEqual(len(master_grid[0]["slots"]), 5)
        for s in master_grid[0]["slots"]:
            self.assertIn("time", s)
            for d in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
                self.assertIn(d, s)

        sec_grid = format_section_grid(res["assignments"], ["A"])
        self.assertEqual(len(sec_grid), 1)
        self.assertEqual(sec_grid[0]["section"], "A")
        self.assertEqual(len(sec_grid[0]["slots"]), 5)


if __name__ == "__main__":
    unittest.main()
