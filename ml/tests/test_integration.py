import os
import shutil
import tempfile
import unittest

from app import (
    app,
    db,
    default_db_path,
    Faculty,
    LeaveRequest,
    Preference,
    get_or_generate_timetable,
    invalidate_timetable_cache,
)
from ml.generator import generate_timetable, format_master_grid, validate_timetable


class RouteAndGeneratorIntegrationTests(unittest.TestCase):
    """Integration and route-level tests for the timetable generator and Flask views."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.temp_dir, "test_pfas.db")
        if os.path.exists(default_db_path):
            shutil.copyfile(default_db_path, self.test_db)

        app.config["TESTING"] = True
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{self.test_db}"
        self.orig_engine = app.extensions["sqlalchemy"]._app_engines[app][None]
        from sqlalchemy import create_engine
        self.test_engine = create_engine(f"sqlite:///{self.test_db}")
        app.extensions["sqlalchemy"]._app_engines[app][None] = self.test_engine
        self.client = app.test_client()

        with app.app_context():
            db.create_all()
            invalidate_timetable_cache()

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            self.test_engine.dispose()
            app.extensions["sqlalchemy"]._app_engines[app][None] = self.orig_engine
            invalidate_timetable_cache()

        shutil.rmtree(self.temp_dir, ignore_errors=True)
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{default_db_path}"

    def test_live_schedule_route_renders_generated_timetable_not_hardcoded(self):
        """Verify /schedule route renders validated dynamic timetable output, not hardcoded strings."""
        res = self.client.get("/schedule")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")

        # Must display dynamic validation banner
        self.assertIn("Validated Dynamic Timetable", html)

        # Must NOT display legacy hardcoded string patterns
        self.assertNotIn("Meeting", html)

        # Must display allocated sections in occupied cells
        self.assertIn("Data Structures (A)", html)
        self.assertIn("Database Systems (A)", html)

    def test_allocated_section_displayed_in_class_cells(self):
        """Preserve and display target section alongside subject in occupied cells (e.g. 'Subject (A)')."""
        requirements = [
            {"subject_id": "CS101", "subject": "Database Systems", "domain": "Database", "section": "A", "lecture_hours": 3, "lab_hours": 0},
            {"subject_id": "CS102", "subject": "Data Structures", "domain": "Data Structures", "section": "B", "lecture_hours": 3, "lab_hours": 0},
            {"subject_id": "CS103", "subject": "Deep Learning", "domain": "AI/ML", "section": "C", "lecture_hours": 3, "lab_hours": 0},
            {"subject_id": "CS104", "subject": "Database Systems", "domain": "Database", "section": "A", "lecture_hours": 0, "lab_hours": 2, "is_lab": True},
        ]
        faculty = [
            {"id": 1, "name": "Dr. Alice", "domain": "Database", "workload": 0, "max_workload": 14, "available": True},
            {"id": 2, "name": "Prof. Bob", "domain": "Data Structures", "workload": 0, "max_workload": 14, "available": True},
            {"id": 3, "name": "Dr. Carol", "domain": "AI/ML", "workload": 0, "max_workload": 14, "available": True},
        ]

        res = generate_timetable(requirements, faculty, seed=42)
        self.assertEqual(res["status"], "SUCCESS")
        master_grid = format_master_grid(res["assignments"], faculty)

        rendered_cells = []
        for fac_item in master_grid:
            for slot_row in fac_item["slots"]:
                for d in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
                    if slot_row[d] != "Free":
                        rendered_cells.append(slot_row[d])

        self.assertIn("Database Systems (A)", rendered_cells)
        self.assertIn("Data Structures (B)", rendered_cells)
        self.assertIn("Deep Learning (C)", rendered_cells)
        self.assertIn("Database Systems Lab (A)", rendered_cells)

    def test_different_target_sections_taught_by_same_faculty_member(self):
        """A faculty member teaching multiple sections displays the correct section for each class."""
        requirements = [
            {"subject_id": "CS201_A", "subject": "Algorithms", "domain": "Algorithms", "section": "A", "lecture_hours": 2, "lab_hours": 0},
            {"subject_id": "CS201_B", "subject": "Algorithms", "domain": "Algorithms", "section": "B", "lecture_hours": 2, "lab_hours": 0},
        ]
        faculty = [
            {"id": 1, "name": "Dr. MultiSection", "domain": "Algorithms", "section": "DefaultSection", "workload": 0, "max_workload": 12, "available": True}
        ]

        res = generate_timetable(requirements, faculty, seed=123)
        self.assertEqual(res["status"], "SUCCESS")
        master_grid = format_master_grid(res["assignments"], faculty)

        fac_slots = master_grid[0]["slots"]
        all_classes = [s[d] for s in fac_slots for d in ["Mon", "Tue", "Wed", "Thu", "Fri"] if s[d] != "Free"]

        self.assertIn("Algorithms (A)", all_classes)
        self.assertIn("Algorithms (B)", all_classes)
        # Confirm it did NOT fall back to the faculty's default section
        self.assertNotIn("Algorithms (DefaultSection)", all_classes)

    def test_faculty_dashboard_route_renders_generated_schedule(self):
        """The /faculty dashboard renders the logged-in faculty member's dynamic validated schedule."""
        with self.client.session_transaction() as sess:
            sess["user"] = "sharma@icfaitech.in"
            sess["role"] = "faculty"

        res = self.client.get("/faculty")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Data Structures", html)
        self.assertNotIn("Departmental Meeting", html)

    def test_faculty_on_leave_excluded_from_live_schedule(self):
        """Faculty on approved leave are not allocated classes in live schedule generation."""
        with app.app_context():
            f2 = Faculty.query.filter_by(name="Prof. R. V. Rao").first()
            if not f2:
                f2 = Faculty(name="Prof. R. V. Rao", email="rao@test.com", department="CSE", weekly_hours=14, assigned_subject="Machine Learning", domain="AI/ML", section="B")
                db.session.add(f2)
                db.session.commit()

            leave = LeaveRequest(faculty_id=f2.id, faculty_name=f2.name, leave_date="2026-10-15", reason="Medical", substitute_name="Dr. S. K. Gupta", status="Approved")
            db.session.add(leave)
            db.session.commit()
            invalidate_timetable_cache()

            gen_res = get_or_generate_timetable(force_refresh=True)

        assigned_fids = {a["faculty_id"] for a in gen_res["assignments"]}
        self.assertNotIn(f2.id, assigned_fids)

    def test_section_collision_prevention_on_live_route(self):
        """Two classes in the same section are never assigned to overlapping time intervals."""
        with app.app_context():
            gen_res = get_or_generate_timetable(force_refresh=True)

        sec_a_assignments = [a for a in gen_res["assignments"] if a["section"] == "A"]
        # Pairwise overlap check for Section A
        for i in range(len(sec_a_assignments)):
            for j in range(i + 1, len(sec_a_assignments)):
                a1 = sec_a_assignments[i]
                a2 = sec_a_assignments[j]
                if a1["day"].lower() == a2["day"].lower():
                    # Times must not overlap
                    overlap = (a1["start_min"] < a2["end_min"]) and (a2["start_min"] < a1["end_min"])
                    self.assertFalse(overlap, f"Section A collision between {a1['subject']} and {a2['subject']} on {a1['day']}")

    def test_workload_hard_limit_enforced_in_live_integration(self):
        """Faculty cannot exceed their confirmed weekly workload cap."""
        with app.app_context():
            gen_res = get_or_generate_timetable(force_refresh=True)

        workloads = gen_res.get("faculty_workloads", {})
        for fid, load in workloads.items():
            cap = load.get("weekly_hour_cap")
            if cap is not None:
                self.assertLessEqual(load["total_weekly_hours"], cap)
            else:
                # Unconfirmed cap: allocation stopped
                self.assertEqual(load["total_weekly_hours"], 0.0)

    def test_missing_unverified_course_hours_not_fabricated(self):
        """Courses not in verified catalog are not assigned fake hours; they remain unscheduled."""
        with app.app_context():
            f_unknown = Faculty(name="Dr. Unknown", email="unique_unknown@icfaitech.in", department="Physics", weekly_hours=12, assigned_subject="Quantum Physics", domain="Physics", section="X")
            db.session.add(f_unknown)
            db.session.commit()
            invalidate_timetable_cache()

            gen_res = get_or_generate_timetable(force_refresh=True)

        # Quantum Physics must not be fabricated
        unscheduled_subjs = [u["subject"] for u in gen_res["unscheduled_requirements"]]
        self.assertIn("Quantum Physics", unscheduled_subjs)

    def test_reproducibility_with_seed_and_cache(self):
        """Repeated calls return identical timetable without random shifting on every refresh."""
        with app.app_context():
            t1 = get_or_generate_timetable()
            t2 = get_or_generate_timetable()

        self.assertEqual(t1["assignments"], t2["assignments"])
        self.assertEqual(t1["master_grid"], t2["master_grid"])

    def test_no_fallback_to_hardcoded_template_on_infeasible(self):
        """When requirements cannot be scheduled, an error/partial status is reported rather than hardcoded template."""
        # Single faculty with 2 hours cap, requirement needs 10 hours
        req = [{"subject_id": "IMPOSSIBLE", "subject": "Huge Subject", "domain": "Database", "section": "A", "lecture_hours": 10, "lab_hours": 0}]
        fac = [{"id": 1, "name": "Dr. LowCap", "domain": "Database", "workload": 0, "max_workload": 2, "available": True}]

        res = generate_timetable(req, fac, seed=42)
        self.assertNotEqual(res["status"], "SUCCESS")
        self.assertGreater(res["unscheduled_count"], 0)


if __name__ == "__main__":
    unittest.main()
