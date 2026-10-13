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
    SubstituteAssignment,
    get_or_generate_timetable,
    invalidate_timetable_cache,
    find_eligible_substitutes,
    validate_substitute_for_class,
    get_eligible_substitutes_for_leave,
    validate_substitute_for_leave,
    auto_assign_substitutes_for_leave,
    handle_emergency_substitute_leave,
)
from ml.generator import (
    generate_timetable,
    format_master_grid,
    format_section_grid,
    validate_timetable,
    check_assignment_feasibility,
)
from ml.constraints import evaluate_constraints


class TimetableUpdatesTests(unittest.TestCase):
    """Test suite for PFAS timetable updates: student timetable, collision prevention, and substitutes."""

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
            import app as app_mod
            app_mod.TIMETABLE_SEED = 42
            app_mod.CURRENT_TIMETABLE_SEED = 42

        shutil.rmtree(self.temp_dir, ignore_errors=True)
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{default_db_path}"

    def test_student_section_timetable_route_and_content(self):
        """Verify student section-wise weekly timetable is present in /schedule with subject and faculty."""
        res = self.client.get("/schedule")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")

        # Student section tab and header must be present
        self.assertIn("Student Section Timetables", html)
        self.assertIn("Weekly Student Timetable", html)

        # Timetable must show subjects with allocated faculty
        self.assertIn("Section A", html)
        # Check that subject and allocated faculty are displayed together
        self.assertIn("Data Structures", html)
        self.assertIn("Database Systems", html)

    def test_format_section_grid_structure(self):
        """Verify format_section_grid organizes by day and period and shows subject & faculty."""
        assignments = [
            {
                "faculty_id": 1,
                "faculty_name": "Dr. Rao",
                "subject": "Database Systems",
                "section": "A",
                "day": "Mon",
                "time_slot": "09:00 AM - 10:00 AM",
                "start_min": 540,
                "end_min": 600,
                "is_lab": False,
            },
            {
                "faculty_id": 2,
                "faculty_name": "Prof. Sharma",
                "subject": "Data Structures",
                "section": "A",
                "day": "Tue",
                "time_slot": "10:00 AM - 11:00 AM",
                "start_min": 600,
                "end_min": 660,
                "is_lab": False,
            },
        ]
        sec_grid = format_section_grid(assignments, sections=["A"])
        self.assertEqual(len(sec_grid), 1)
        self.assertEqual(sec_grid[0]["section"], "A")

        # Find the Monday 09:00 slot and Tuesday 10:00 slot
        slot_09 = next(s for s in sec_grid[0]["slots"] if s["time"] == "09:00 AM - 10:00 AM")
        slot_10 = next(s for s in sec_grid[0]["slots"] if s["time"] == "10:00 AM - 11:00 AM")

        self.assertEqual(slot_09["Mon_subject"], "Database Systems")
        self.assertEqual(slot_09["Mon_faculty"], "Dr. Rao")
        self.assertIn("Database Systems", slot_09["Mon"])
        self.assertNotIn("Dr. Rao", slot_09["Mon"])

        self.assertEqual(slot_10["Tue_subject"], "Data Structures")
        self.assertEqual(slot_10["Tue_faculty"], "Prof. Sharma")
        self.assertIn("Data Structures", slot_10["Tue"])
        self.assertNotIn("Prof. Sharma", slot_10["Tue"])

        # Verify separate faculty table contains allocated faculty details
        self.assertIn("faculty", sec_grid[0])
        fac_names = {f["faculty_name"] for f in sec_grid[0]["faculty"]}
        self.assertIn("Dr. Rao", fac_names)
        self.assertIn("Prof. Sharma", fac_names)

    def test_consistent_subject_faculty_allocation_in_generator(self):
        """A subject assigned to a section must have consistent faculty across scheduled periods."""
        requirements = [
            {"subject_id": "CS101_1", "subject": "Database Systems", "domain": "Database", "section": "A", "lecture_hours": 2, "lab_hours": 0},
            {"subject_id": "CS101_2", "subject": "Database Systems", "domain": "Database", "section": "A", "lecture_hours": 2, "lab_hours": 0},
        ]
        faculty = [
            {"id": 1, "name": "Dr. Rao", "domain": "Database", "workload": 0, "max_workload": 14, "available": True},
            {"id": 2, "name": "Dr. Kumar", "domain": "Database", "workload": 0, "max_workload": 14, "available": True},
        ]

        res = generate_timetable(requirements, faculty, seed=42)
        self.assertEqual(res["status"], "SUCCESS")

        # Both requirements for Database Systems in Section A must be assigned to the SAME faculty member
        assigned_faculty = {a["faculty_name"] for a in res["assignments"] if a["subject"] == "Database Systems" and a["section"] == "A"}
        self.assertEqual(len(assigned_faculty), 1, f"Expected 1 consistent faculty, got: {assigned_faculty}")

    def test_feasibility_check_rejects_conflicting_faculty_for_same_subject_section(self):
        """Feasibility check must reject a different faculty for the same section & subject."""
        existing_schedule = [
            {
                "faculty_id": 1,
                "faculty_name": "Dr. Rao",
                "subject": "Database Systems",
                "section": "A",
                "day": "Mon",
                "time_slot": "09:00 AM - 10:00 AM",
                "start_min": 540,
                "end_min": 600,
            }
        ]

        unit = {
            "requirement": {
                "name": "Database Systems",
                "subject": "Database Systems",
                "domain": "Database",
                "section": "A",
                "is_lab": False,
                "duration_hours": 1.0,
            },
            "unit_id": "u1",
            "duration_hours": 1.0,
            "is_lab": False,
        }
        faculty_rao = {
            "id": 1,
            "faculty_id": 1,
            "name": "Dr. Rao",
            "domain": "Database",
            "available": True,
            "max_workload": 14,
            "_clean_cap_hours": 14.0,
        }
        faculty_kumar = {
            "id": 2,
            "faculty_id": 2,
            "name": "Dr. Kumar",
            "domain": "Database",
            "available": True,
            "max_workload": 14,
            "_clean_cap_hours": 14.0,
        }
        slot = {"time": "09:00 AM - 10:00 AM", "start_min": 540, "end_min": 600}

        # Candidate Dr. Kumar (ID 2) attempting to teach Database Systems to Section A
        feasible, reason = check_assignment_feasibility(
            unit=unit,
            faculty=faculty_kumar,
            day="Tue",
            slot_parsed=slot,
            current_schedule=[],
            current_faculty_hours={2: 0.0},
            existing_schedule=existing_schedule,
        )
        self.assertFalse(feasible)
        self.assertIn("consistent faculty allocation required", reason)

        # Candidate Dr. Rao (ID 1) should be feasible
        feasible_consistent, _ = check_assignment_feasibility(
            unit=unit,
            faculty=faculty_rao,
            day="Tue",
            slot_parsed=slot,
            current_schedule=[],
            current_faculty_hours={1: 0.0},
            existing_schedule=existing_schedule,
        )
        self.assertTrue(feasible_consistent)

    def test_constraint_evaluator_flags_subject_faculty_inconsistency(self):
        """evaluate_constraints flags a collision when candidate faculty differs from allocated faculty."""
        candidate = {
            "id": 20,
            "faculty_id": 20,
            "name": "Dr. Candidate",
            "weekly_hour_cap": 14,
            "current_weekly_hours": 0,
            "available": True,
        }
        subject = {
            "name": "Operating Systems",
            "section": "B",
            "preferred_time_slot": "Mon 09:00 AM - 10:00 AM",
        }
        existing_schedule = [
            {
                "faculty_id": 10,
                "faculty_name": "Dr. Primary",
                "subject": "Operating Systems",
                "section": "B",
                "day": "Wed",
                "time_slot": "Wed 09:00 AM - 10:00 AM",
                "start_min": 540,
                "end_min": 600,
            }
        ]

        eval_res = evaluate_constraints(
            candidate=candidate,
            required_hours=2,
            subject=subject,
            existing_schedule=existing_schedule,
        )
        self.assertFalse(eval_res["is_eligible"])
        self.assertIn("already allocated to Dr. Primary", " ".join(eval_res["reasons"]))

    def test_validate_timetable_flags_section_faculty_allocation_collision(self):
        """validate_timetable fails if two distinct faculty are assigned to same subject in a section."""
        conflicting_assignments = [
            {
                "faculty_id": 1,
                "faculty_name": "Dr. Rao",
                "subject": "Database Systems",
                "section": "A",
                "day": "Mon",
                "start_min": 540,
                "end_min": 600,
                "time_slot": "09:00 AM - 10:00 AM",
            },
            {
                "faculty_id": 2,
                "faculty_name": "Dr. Kumar",
                "subject": "Database Systems",
                "section": "A",
                "day": "Tue",
                "start_min": 540,
                "end_min": 600,
                "time_slot": "09:00 AM - 10:00 AM",
            },
        ]
        faculty_workloads = {
            1: {"total_weekly_hours": 1.0, "weekly_hour_cap": 14.0},
            2: {"total_weekly_hours": 1.0, "weekly_hour_cap": 14.0},
        }
        val = validate_timetable(conflicting_assignments, faculty_workloads)
        self.assertFalse(val["is_valid"])
        self.assertIn("Subject-faculty collision", " ".join(val["errors"]))

    def test_admin_appointed_substitute_displays_in_schedule_with_highlight(self):
        """Admin appointed substitute displays substitute name, original faculty, and highlight in schedule."""
        with app.app_context():
            # Select an active faculty member who has scheduled classes
            gen_init = get_or_generate_timetable(force_refresh=True)
            active_fids = {a["faculty_id"] for a in gen_init["assignments"]}
            f1 = Faculty.query.filter(Faculty.id.in_(active_fids)).first()
            f2 = Faculty.query.filter(Faculty.id != f1.id).first()

            # Create an admin-appointed substitute for f1
            sub = SubstituteAssignment(
                faculty_id=f1.id,
                faculty_name=f1.name,
                substitute_id=f2.id,
                substitute_name=f2.name,
                subject=f1.assigned_subject,
                section=f1.section,
                status="Approved",
            )
            db.session.add(sub)
            db.session.commit()
            invalidate_timetable_cache()

            # Fetch the generated timetable
            gen_res = get_or_generate_timetable(force_refresh=True)

        # Check section_grid has the substitute highlighted
        found_sub_in_sec_grid = False
        for sec_item in gen_res["section_grid"]:
            if sec_item["section"] != f1.section:
                continue
            for slot in sec_item["slots"]:
                for d in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
                    if slot.get(f"{d}_is_sub"):
                        found_sub_in_sec_grid = True
                        self.assertEqual(slot.get(f"{d}_substitute"), f2.name)
                        self.assertEqual(slot.get(f"{d}_original"), f1.name)
                        break
                if found_sub_in_sec_grid:
                    break
            if found_sub_in_sec_grid:
                break
        self.assertTrue(found_sub_in_sec_grid, "Substitute was not marked as is_sub in section_grid")

        # Verify HTML rendered via client has highlight styling and substitute info
        res = self.client.get("/schedule")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("bg-warning", html)
        self.assertIn(f2.name, html)

    def test_cancelled_substitution_restores_original_faculty(self):
        """Cancelling a substitution restores the original faculty display and removes the highlight."""
        with app.app_context():
            f1 = Faculty.query.first()
            f2 = Faculty.query.filter(Faculty.id != f1.id).first()
            f1_sec = f1.section
            f1_subj = f1.assigned_subject

            sub = SubstituteAssignment(
                faculty_id=f1.id,
                faculty_name=f1.name,
                substitute_id=f2.id,
                substitute_name=f2.name,
                subject=f1_subj,
                section=f1_sec,
                status="Approved",
            )
            db.session.add(sub)
            db.session.commit()
            sub_id = sub.id

        # Cancel the substitute appointment via the admin route
        cancel_res = self.client.get(f"/admin/substitute/cancel/{sub_id}", follow_redirects=True)
        self.assertEqual(cancel_res.status_code, 200)

        with app.app_context():
            cancelled_sub = db.session.get(SubstituteAssignment, sub_id)
            self.assertEqual(cancelled_sub.status, "Cancelled")
            gen_res = get_or_generate_timetable(force_refresh=True)

        # Check that no slot for this section is marked with is_sub
        for sec_item in gen_res["section_grid"]:
            if sec_item["section"] == f1_sec:
                for slot in sec_item["slots"]:
                    for d in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
                        if slot.get(f"{d}_is_sub"):
                            self.fail("Slot still has is_sub True after substitution cancellation")

    def test_direct_substitute_appointment_route(self):
        """Verify POST /admin/substitute/appoint creates an approved substitute appointment."""
        with app.app_context():
            f1 = Faculty.query.first()
            f2 = Faculty.query.filter(Faculty.id != f1.id).first()
            f1_id = f1.id
            f2_name = f2.name

        post_data = {
            "faculty_id": f1_id,
            "substitute_name": f2_name,
            "subject": "Database Systems",
            "section": "A",
            "day": "Mon",
            "date": "2026-10-15",
        }
        res = self.client.post("/admin/substitute/appoint", data=post_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        with app.app_context():
            sub = SubstituteAssignment.query.filter_by(faculty_id=f1_id, substitute_name=f2_name, status="Approved").first()
            self.assertIsNotNone(sub)
            self.assertEqual(sub.section, "A")
            self.assertEqual(sub.day, "Mon")


    def test_room_collision_prevention_in_feasibility_check(self):
        """Feasibility check must reject an assignment if the room is already booked at that time."""
        current_schedule = [
            {
                "faculty_id": 1,
                "faculty_name": "Dr. Sharma",
                "subject": "Data Structures",
                "section": "A",
                "day": "Mon",
                "time_slot": "09:00 AM - 10:00 AM",
                "start_min": 540,
                "end_min": 600,
                "room": "Room 101",
            }
        ]

        unit = {
            "requirement": {
                "name": "Database Systems",
                "subject": "Database Systems",
                "domain": "Database",
                "section": "B",
                "is_lab": False,
                "duration_hours": 1.0,
                "room": "Room 101",
            },
            "unit_id": "u2",
            "duration_hours": 1.0,
            "is_lab": False,
            "room": "Room 101",
        }
        faculty = {
            "id": 2,
            "name": "Dr. Gupta",
            "domain": "Database",
            "max_workload": 14,
            "available": True,
        }
        slot_parsed = {"time_slot": "09:00 AM - 10:00 AM", "start_min": 540, "end_min": 600}

        feasible, reason = check_assignment_feasibility(
            unit=unit,
            faculty=faculty,
            day="Mon",
            slot_parsed=slot_parsed,
            current_schedule=current_schedule,
            current_faculty_hours={2: 0.0},
        )
        self.assertFalse(feasible)
        self.assertIn("Room collision", reason)

    def test_room_collision_detection_in_validator(self):
        """Validator must flag an error when two different sections/faculty share the same room at the same time."""
        colliding_schedule = [
            {
                "faculty_id": 1,
                "faculty_name": "Dr. Sharma",
                "subject": "Data Structures",
                "section": "A",
                "day": "Mon",
                "time_slot": "09:00 AM - 10:00 AM",
                "start_min": 540,
                "end_min": 600,
                "room": "Room 101",
            },
            {
                "faculty_id": 2,
                "faculty_name": "Dr. Gupta",
                "subject": "Database Systems",
                "section": "B",
                "day": "Mon",
                "time_slot": "09:00 AM - 10:00 AM",
                "start_min": 540,
                "end_min": 600,
                "room": "Room 101",
            },
        ]
        val = validate_timetable(colliding_schedule)
        self.assertFalse(val["is_valid"])
        self.assertTrue(any("Room collision" in err for err in val["errors"]))

    def test_continuous_lab_session_slot_allocation(self):
        """Continuous 2-hour labs must populate both consecutive periods in formatted grids."""
        assignments = [
            {
                "faculty_id": 1,
                "faculty_name": "Dr. Sharma",
                "subject": "Data Structures",
                "section": "A",
                "day": "Mon",
                "time_slot": "09:00 AM - 10:00 AM",
                "start_min": 540,
                "end_min": 660,  # 2 hours: 09:00 to 11:00 AM
                "is_lab": True,
                "duration_hours": 2.0,
                "room": "Computing Lab 1",
            }
        ]
        sec_grid = format_section_grid(assignments, sections=["A"])
        slot_09 = next(s for s in sec_grid[0]["slots"] if s["time"] == "09:00 AM - 10:00 AM")
        slot_10 = next(s for s in sec_grid[0]["slots"] if s["time"] == "10:00 AM - 11:00 AM")

        # Both consecutive slots must be occupied by the continuous lab session without mentioning faculty in the cell
        self.assertIn("Data Structures Lab [Computing Lab 1]", slot_09["Mon"])
        self.assertIn("Data Structures Lab [Computing Lab 1]", slot_10["Mon"])
        self.assertNotIn("Dr. Sharma", slot_09["Mon"])
        self.assertNotIn("Dr. Sharma", slot_10["Mon"])
        self.assertNotEqual(slot_10["Mon"], "Free")

        # Faculty is provided in the separate section faculty table
        fac_names = {f["faculty_name"] for f in sec_grid[0]["faculty"]}
        self.assertIn("Dr. Sharma", fac_names)

    def test_synthetic_faculty_seeding_idempotency(self):
        """Seeding script must add synthetic faculty without duplicates or modifying real faculty."""
        from scripts.seed_synthetic_faculty import seed_synthetic_faculty

        with app.app_context():
            # Initial seed
            seed_synthetic_faculty()
            count_after_first = Faculty.query.count()
            synth_first = Faculty.query.filter(Faculty.email.like("%synth%")).count()

            # Second seed should be idempotent
            seed_synthetic_faculty()
            count_after_second = Faculty.query.count()
            synth_second = Faculty.query.filter(Faculty.email.like("%synth%")).count()

            self.assertEqual(count_after_first, count_after_second)
            self.assertEqual(synth_first, synth_second)
            self.assertGreater(synth_first, 0)

            # Ensure all synthetic faculty have [Synthetic] in name
            synths = Faculty.query.filter(Faculty.email.like("%synth%")).all()
            for s in synths:
                self.assertIn("[Synthetic]", s.name)
                self.assertTrue(s.email.endswith("synth@icfaitech.in"))

    def test_reasonable_free_periods_and_requirement_completion(self):
        """Timetable generation with full faculty must fulfill requirements with reasonable free periods."""
        from scripts.seed_synthetic_faculty import seed_synthetic_faculty

        with app.app_context():
            seed_synthetic_faculty()
            res = get_or_generate_timetable(force_refresh=True)

            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["unscheduled_count"], 0)
            self.assertTrue(res["validation"]["is_valid"])

            # Verify each section has reasonable free periods (<= 6 free periods out of 25 weekly periods)
            for sec_data in res["section_grid"]:
                free_slots = sum(
                    1 for slot in sec_data["slots"]
                    for d in ["Mon", "Tue", "Wed", "Thu", "Fri"]
                    if slot[d] == "Free"
                )
                self.assertLessEqual(
                    free_slots,
                    6,
                    f"Section {sec_data['section']} has excessive free periods: {free_slots}/25",
                )

    def test_section_timetable_omits_faculty_name_and_provides_separate_faculty_table(self):
        """Timetable cells must omit faculty names and each section must have a separate faculty details table."""
        from scripts.seed_synthetic_faculty import seed_synthetic_faculty

        with app.app_context():
            seed_synthetic_faculty()
            res = get_or_generate_timetable(force_refresh=True)

            for sec_item in res["section_grid"]:
                sec_name = sec_item["section"]
                # 1. Verify separate faculty list is present and populated
                self.assertIn("faculty", sec_item)
                faculty_list = sec_item["faculty"]
                self.assertGreater(len(faculty_list), 0, f"Section {sec_name} should have faculty details")
                all_sec_faculty_names = {f["faculty_name"] for f in faculty_list}

                # 2. Verify timetable cells do NOT mention the faculty names directly in the cell
                for slot in sec_item["slots"]:
                    for d in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
                        cell_text = slot[d]
                        if cell_text != "Free":
                            for fac_name in all_sec_faculty_names:
                                self.assertNotIn(
                                    fac_name,
                                    cell_text,
                                    f"Cell '{cell_text}' in Section {sec_name} ({d}) must not mention faculty name '{fac_name}'",
                                )

            # 3. Verify /schedule renders the separate Faculty Details table
            client_res = self.client.get("/schedule")
            self.assertEqual(client_res.status_code, 200)
            html = client_res.data.decode("utf-8")
            self.assertIn("Faculty Details (Section A)", html)
            self.assertIn("Allocated Faculty", html)

    def test_substitute_and_coverage_column_renders_complete_timetables(self):
        """Active Substitutions & Coverage view must render complete weekly timetables for assigned substitute faculty, faculty on leave, and covered sections."""
        from scripts.seed_synthetic_faculty import seed_synthetic_faculty

        with app.app_context():
            seed_synthetic_faculty()
            # Ensure an approved leave with an appointed substitute exists
            leave = LeaveRequest.query.filter_by(status="Approved").first()
            if not leave:
                leave = LeaveRequest(
                    faculty_id=1,
                    faculty_name="Dr. M. K. Sharma",
                    leave_date="2026-10-15",
                    reason="Conference",
                    substitute_name="Prof. R. V. Rao",
                    status="Approved",
                )
                db.session.add(leave)
                db.session.commit()
            invalidate_timetable_cache()

            res = self.client.get("/schedule")
            self.assertEqual(res.status_code, 200)
            html = res.data.decode("utf-8")

            # 1. Official substitute coverage table
            self.assertIn("Official Substitute Coverage Timetable", html)
            self.assertIn("Appointed Substitute", html)

            # 2. Complete weekly timetable for appointed substitute faculty
            self.assertIn("Complete Weekly Timetable: Appointed Substitute Faculty", html)
            self.assertIn("Prof. R. V. Rao", html)

            # 3. Complete weekly timetable for faculty on leave
            self.assertIn("Complete Weekly Timetable: Faculty on Leave", html)
            self.assertIn("Dr. M. K. Sharma", html)

            # 4. Covered section timetable with substitute in place of faculty on leave
            self.assertIn("Covered Section Timetables (Substitute In Place Of Faculty On Leave)", html)
            self.assertTrue("Section A Weekly Timetable (Coverage Active)" in html or "Section B Weekly Timetable (Coverage Active)" in html)

    def test_regenerate_timetable_redirect_and_flash_message(self):
        """Clicking regenerate timetable should re-solve timetable and flash confirmation banner with seed."""
        # 1. Regenerate without tab
        res = self.client.get("/schedule/regenerate")
        self.assertEqual(res.status_code, 302)
        self.assertTrue(res.location.endswith("/schedule"))

        # 2. Regenerate with tab=substitute-view preserves tab hash
        res_tab = self.client.get("/schedule/regenerate?tab=substitute-view")
        self.assertEqual(res_tab.status_code, 302)
        self.assertEqual(res_tab.location, "/schedule#substitute-view")

        # 3. Follow redirect to verify flash message is displayed
        follow_res = self.client.get(res_tab.location)
        self.assertEqual(follow_res.status_code, 200)
        html = follow_res.data.decode("utf-8")
        self.assertIn("Timetable regenerated successfully with a new schedule arrangement", html)


class SubstituteAllocationAndUIFixesTests(TimetableUpdatesTests):
    """Comprehensive test suite for PFAS substitute allocation fixes and UI simplifications."""

    def test_show_only_eligible_substitutes_filters_correctly(self):
        """Show only genuinely available faculty: exclude self, clashing faculty, on-leave faculty, and overloaded faculty."""
        with app.app_context():
            tt = get_or_generate_timetable()
            asgn = tt["assignments"][0]
            fac1 = Faculty.query.get(asgn["faculty_id"])
            fac2 = Faculty.query.filter(Faculty.id != fac1.id).first()
            self.assertIsNotNone(fac1)
            self.assertIsNotNone(fac2)

            class_day = asgn["day"]
            class_slot = asgn["time_slot"]

            # Mark fac2 on leave on this class's day
            leave_fac2 = LeaveRequest(
                faculty_id=fac2.id,
                faculty_name=fac2.name,
                leave_date=class_day,
                reason="Medical leave",
                status="Approved"
            )
            db.session.add(leave_fac2)
            db.session.commit()

            # Find eligible substitutes for fac1 on that exact day and time slot
            eligible = find_eligible_substitutes(
                original_faculty_id=fac1.id,
                day=class_day,
                time_slot=class_slot,
                subject=asgn.get("subject"),
                section=asgn.get("section"),
                leave_date=class_day,
            )
            eligible_ids = {e["id"] for e in eligible}

            # Original faculty must NEVER be in eligible list
            self.assertNotIn(fac1.id, eligible_ids)
            # Faculty already on leave must NEVER be in eligible list
            self.assertNotIn(fac2.id, eligible_ids)

            # Each eligible faculty must have valid load <= max cap
            for cand in eligible:
                self.assertLessEqual(cand["current_load"] + 1.0, cand["faculty"].weekly_hours or 14.0)

    def test_server_side_revalidation_rejects_ineligible_appointment(self):
        """Server-side revalidation prevents manual appointment of unavailable faculty."""
        with app.app_context():
            tt = get_or_generate_timetable()
            asgn = tt["assignments"][0]
            fac1 = Faculty.query.get(asgn["faculty_id"])
            fac2 = Faculty.query.filter(Faculty.id != fac1.id).first()
            class_day = asgn["day"]

            # Put fac2 on approved leave on that day
            lr_fac2 = LeaveRequest(
                faculty_id=fac2.id,
                faculty_name=fac2.name,
                leave_date=class_day,
                reason="Out of office",
                status="Approved"
            )
            db.session.add(lr_fac2)

            # fac1 applies for leave on same date
            lr_fac1 = LeaveRequest(
                faculty_id=fac1.id,
                faculty_name=fac1.name,
                leave_date=class_day,
                reason="Personal leave",
                status="Pending"
            )
            db.session.add(lr_fac1)
            db.session.commit()
            leave_id = lr_fac1.id

            # Admin tries to appoint fac2 (who is on leave)
            res = self.client.post(
                f"/admin/leave/{leave_id}/assign-substitute",
                data={"substitute_name": fac2.name},
                follow_redirects=True
            )
            self.assertEqual(res.status_code, 200)
            html = res.data.decode("utf-8")
            # Must display revalidation rejection warning
            self.assertIn(f"Cannot appoint {fac2.name}", html)

            # Verify appointment was rejected in DB
            reloaded = LeaveRequest.query.get(leave_id)
            self.assertNotEqual(reloaded.substitute_name, fac2.name)

    def test_automatic_substitute_assignment_on_leave_approval(self):
        """When leave is approved, system auto-identifies affected classes and assigns qualified available substitute."""
        with app.app_context():
            tt = get_or_generate_timetable()
            asgn = tt["assignments"][0]
            fac = Faculty.query.get(asgn["faculty_id"])
            class_day = asgn["day"]

            # Create pending leave for fac on class_day
            lr = LeaveRequest(
                faculty_id=fac.id,
                faculty_name=fac.name,
                leave_date=class_day,
                reason="Academic symposium",
                status="Pending"
            )
            db.session.add(lr)
            db.session.commit()
            leave_id = lr.id

            # Admin approves leave
            res = self.client.get(f"/admin/leave/{leave_id}/approve", follow_redirects=True)
            self.assertEqual(res.status_code, 200)

            # Verify leave status is Approved
            reloaded_lr = LeaveRequest.query.get(leave_id)
            self.assertEqual(reloaded_lr.status, "Approved")
            self.assertIsNotNone(reloaded_lr.substitute_name)

            # Verify substitute assignment is saved in database
            subs = SubstituteAssignment.query.filter_by(faculty_id=fac.id, status="Approved").all()
            self.assertGreater(len(subs), 0)
            sub_names = {s.substitute_name for s in subs}
            self.assertNotIn(fac.name, sub_names)

            # Verify timetable reflects the substitute assignment
            res_sched = self.client.get("/schedule")
            html = res_sched.data.decode("utf-8")
            for s in subs:
                self.assertIn(s.substitute_name, html)

    def test_unassigned_admin_action_required_when_no_substitute_eligible(self):
        """If no eligible substitute is available, mark class as Unassigned — Admin Action Required and notify admin."""
        with app.app_context():
            tt = get_or_generate_timetable()
            asgn = tt["assignments"][0]
            target_fac = Faculty.query.get(asgn["faculty_id"])
            class_day = asgn["day"]

            # Put ALL other faculty members on approved leave on that day
            other_facs = Faculty.query.filter(Faculty.id != target_fac.id).all()
            for of in other_facs:
                db.session.add(LeaveRequest(
                    faculty_id=of.id,
                    faculty_name=of.name,
                    leave_date=class_day,
                    reason="Department symposium",
                    status="Approved"
                ))
            db.session.commit()

            # Target faculty applies for leave on class_day
            lr = LeaveRequest(
                faculty_id=target_fac.id,
                faculty_name=target_fac.name,
                leave_date=class_day,
                reason="Medical emergency",
                status="Pending"
            )
            db.session.add(lr)
            db.session.commit()

            # Approve leave
            res = self.client.get(f"/admin/leave/{lr.id}/approve", follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            html = res.data.decode("utf-8")

            # Notification to admin must be present
            self.assertIn("Unassigned — Admin Action Required", html)

            # DB record must reflect Unassigned — Admin Action Required
            reloaded_lr = LeaveRequest.query.get(lr.id)
            self.assertEqual(reloaded_lr.substitute_name, "Unassigned — Admin Action Required")

            # Timetable must clearly display Unassigned — Admin Action Required
            res_sched = self.client.get("/schedule")
            html_sched = res_sched.data.decode("utf-8")
            self.assertIn("Unassigned — Admin Action Required", html_sched)

    def test_emergency_leave_by_already_assigned_substitute(self):
        """Dr. Rao is on leave, Dr. Kumar is assigned. Dr. Kumar takes emergency leave. System deactivates and reassigns."""
        with app.app_context():
            tt = get_or_generate_timetable()
            asgn = tt["assignments"][0]
            fac1 = Faculty.query.get(asgn["faculty_id"])
            fac2 = Faculty.query.filter(Faculty.id != fac1.id).first()
            class_day = asgn["day"]
            class_slot = asgn["time_slot"]

            # Create and approve leave for fac1 with fac2 assigned
            lr1 = LeaveRequest(
                faculty_id=fac1.id,
                faculty_name=fac1.name,
                leave_date=class_day,
                reason="Conference",
                substitute_name=fac2.name,
                status="Approved"
            )
            db.session.add(lr1)
            db.session.commit()

            # Assign substitute duties to fac2 for this slot
            sa1 = SubstituteAssignment(
                faculty_id=fac1.id,
                faculty_name=fac1.name,
                substitute_id=fac2.id,
                substitute_name=fac2.name,
                day=class_day,
                time_slot=class_slot,
                date=class_day,
                status="Approved"
            )
            db.session.add(sa1)
            db.session.commit()

            # fac2 now takes emergency leave on same day
            lr2 = LeaveRequest(
                faculty_id=fac2.id,
                faculty_name=fac2.name,
                leave_date=class_day,
                reason="Sudden medical emergency",
                status="Pending"
            )
            db.session.add(lr2)
            db.session.commit()

            # Admin approves emergency leave
            res = self.client.get(f"/admin/leave/{lr2.id}/approve", follow_redirects=True)
            self.assertEqual(res.status_code, 200)

            # Verify fac2's prior substitute assignment was cancelled/reassigned
            reloaded_sa1 = SubstituteAssignment.query.get(sa1.id)
            self.assertEqual(reloaded_sa1.status, "Cancelled")

            # Check that a new replacement assignment was created for fac1's slot
            active_replacement = SubstituteAssignment.query.filter_by(
                faculty_id=fac1.id,
                day=class_day,
                time_slot=class_slot,
                status="Approved"
            ).first()
            self.assertIsNotNone(active_replacement)
            self.assertNotEqual(active_replacement.substitute_name, fac2.name)

            # Timetable must show updated substitute name, not fac2
            res_sched = self.client.get("/schedule")
            html_sched = res_sched.data.decode("utf-8")
            self.assertIn(active_replacement.substitute_name, html_sched)

    def test_partial_day_leave_does_not_affect_unaffected_classes(self):
        """Partial day leave specifies time_slot; only that period is substituted, leaving others unchanged."""
        with app.app_context():
            tt = get_or_generate_timetable()
            asgn = tt["assignments"][0]
            fac = Faculty.query.get(asgn["faculty_id"])
            class_day = asgn["day"]
            class_slot = asgn["time_slot"]

            # Partial-day leave for that slot only
            lr = LeaveRequest(
                faculty_id=fac.id,
                faculty_name=fac.name,
                leave_date=class_day,
                time_slot=class_slot,
                reason="Medical appointment",
                status="Pending"
            )
            db.session.add(lr)
            db.session.commit()

            # Approve partial-day leave
            res = self.client.get(f"/admin/leave/{lr.id}/approve", follow_redirects=True)
            self.assertEqual(res.status_code, 200)

            # SubstituteAssignment should only be created for the affected time slot
            subs = SubstituteAssignment.query.filter_by(faculty_id=fac.id, status="Approved").all()
            for s in subs:
                self.assertEqual(s.time_slot, class_slot)

    def test_ui_simplifications_seed_removed_and_policy_box_removed(self):
        """Seed text is removed from schedule alert & messages, and policy box removed from faculty page."""
        # 1. Schedule page: seed info must NOT appear in success alert
        res_sched = self.client.get("/schedule")
        self.assertEqual(res_sched.status_code, 200)
        html_sched = res_sched.data.decode("utf-8")
        self.assertNotIn("Seed:", html_sched)
        self.assertNotIn("Seed: 80237", html_sched)

        # 2. Schedule regenerate flash message must NOT contain seed info
        res_regen = self.client.get("/schedule/regenerate", follow_redirects=True)
        html_regen = res_regen.data.decode("utf-8")
        self.assertNotIn("Seed:", html_regen)

        # 3. Faculty page: policy alert box must be removed
        res_fac = self.client.get("/faculty")
        self.assertEqual(res_fac.status_code, 200)
        html_fac = res_fac.data.decode("utf-8")
        self.assertNotIn("Substitute appointments are managed exclusively by Administration/HOD", html_fac)
        self.assertNotIn("Administrative Policy:", html_fac)

        # 4. Backend admin-only authorization: regular faculty application cannot self-appoint
        with app.app_context():
            fac1 = Faculty.query.get(1)
            fac2 = Faculty.query.get(2)
            post_res = self.client.post("/faculty/apply-leave", data={
                "faculty_id": fac1.id,
                "leave_date": "2026-10-20",
                "reason": "Personal work",
                "substitute_name": fac2.name  # Unauthorized attempt to self-appoint
            }, follow_redirects=True)
            self.assertEqual(post_res.status_code, 200)

            # In DB, substitute_name must NOT be set to self-chosen fac2
            latest_leave = LeaveRequest.query.filter_by(faculty_id=fac1.id).order_by(LeaveRequest.id.desc()).first()
            self.assertNotEqual(latest_leave.substitute_name, fac2.name)

    def test_substitute_timetable_contains_actual_and_substitute_sessions_with_different_colors(self):
        """Substitute faculty timetable contains their regular courses (blue) and substitute courses (gold is_sub)."""
        with app.app_context():
            # Pick two distinct faculty members
            f_orig = Faculty.query.filter_by(name="Dr. M. K. Sharma").first() or Faculty.query.get(1)
            f_sub = Faculty.query.filter_by(name="Prof. R. V. Rao").first() or Faculty.query.get(2)

            # Assign f_sub as substitute for f_orig on Tuesday
            sub_asgn = SubstituteAssignment(
                faculty_id=f_orig.id,
                faculty_name=f_orig.name,
                substitute_id=f_sub.id,
                substitute_name=f_sub.name,
                subject=f_orig.assigned_subject,
                section=f_orig.section,
                day="Tue",
                date="2026-10-20",
                status="Approved",
            )
            db.session.add(sub_asgn)
            db.session.commit()
            invalidate_timetable_cache()

            gen_res = get_or_generate_timetable(force_refresh=True)

            # Locate f_sub in master_grid
            sub_grid_item = next(
                (item for item in gen_res["master_grid"] if item["faculty"]["name"] == f_sub.name),
                None
            )
            self.assertIsNotNone(sub_grid_item)

            has_regular_session = False
            has_substitute_session = False

            for slot in sub_grid_item["slots"]:
                for d in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
                    val = slot.get(d)
                    is_sub = slot.get(f"{d}_is_sub")
                    if val and val != "Free":
                        if is_sub:
                            has_substitute_session = True
                            self.assertIn("Sub for", val)
                        else:
                            has_regular_session = True

            self.assertTrue(has_regular_session, "Substitute faculty must have their regular sessions in timetable")
            self.assertTrue(has_substitute_session, "Substitute faculty must have their substitute sessions in timetable")

            # Clean up
            db.session.delete(sub_asgn)
            db.session.commit()
            invalidate_timetable_cache()

    def test_faculty_on_leave_shows_actual_timetable_not_free(self):
        """Faculty on approved leave shows actual scheduled courses (with Sub indicator), NOT 'Free'."""
        with app.app_context():
            f_orig = Faculty.query.filter_by(name="Dr. M. K. Sharma").first() or Faculty.query.get(1)
            f_sub = Faculty.query.filter_by(name="Prof. R. V. Rao").first() or Faculty.query.get(2)

            sub_asgn = SubstituteAssignment(
                faculty_id=f_orig.id,
                faculty_name=f_orig.name,
                substitute_id=f_sub.id,
                substitute_name=f_sub.name,
                subject=f_orig.assigned_subject,
                section=f_orig.section,
                day="Tue",
                date="2026-10-20",
                status="Approved",
            )
            db.session.add(sub_asgn)
            db.session.commit()
            invalidate_timetable_cache()

            gen_res = get_or_generate_timetable(force_refresh=True)

            orig_grid_item = next(
                (item for item in gen_res["master_grid"] if item["faculty"]["name"] == f_orig.name),
                None
            )
            self.assertIsNotNone(orig_grid_item)

            # On Tuesday where f_orig has classes, they must NOT be 'Free'
            tue_classes = [slot["Tue"] for slot in orig_grid_item["slots"] if slot["Tue"] != "Free"]
            self.assertGreater(len(tue_classes), 0, "Faculty on leave must show actual timetable sessions, not 'Free'")
            for tc in tue_classes:
                self.assertIn(f_sub.name, tc)

            # Clean up
            db.session.delete(sub_asgn)
            db.session.commit()
            invalidate_timetable_cache()

    def test_cascading_substitute_leave_reassigns_both_faculty_without_clashes(self):
        """When Person B (substitute for Person A on day X) also takes leave on day X, both are reassigned without clashes."""
        with app.app_context():
            f_a = Faculty.query.filter_by(name="Dr. M. K. Sharma").first() or Faculty.query.get(1)
            f_b = Faculty.query.filter_by(name="Prof. R. V. Rao").first() or Faculty.query.get(2)

            # Step 1: Person A takes leave on 2026-10-20, Person B appointed as substitute
            leave_a = LeaveRequest(
                faculty_id=f_a.id,
                faculty_name=f_a.name,
                leave_date="2026-10-20",
                reason="Medical",
                substitute_name=f_b.name,
                status="Approved",
            )
            db.session.add(leave_a)
            sub_ab = SubstituteAssignment(
                faculty_id=f_a.id,
                faculty_name=f_a.name,
                substitute_id=f_b.id,
                substitute_name=f_b.name,
                subject=f_a.assigned_subject,
                section=f_a.section,
                day="Tue",
                date="2026-10-20",
                status="Approved",
            )
            db.session.add(sub_ab)
            db.session.commit()

            # Step 2: Person B now also applies for and gets approved leave on 2026-10-20
            leave_b = LeaveRequest(
                faculty_id=f_b.id,
                faculty_name=f_b.name,
                leave_date="2026-10-20",
                reason="Family Emergency",
                status="Pending",
            )
            db.session.add(leave_b)
            db.session.commit()

            # Approve Person B's leave via handle_emergency_substitute_leave
            reports = handle_emergency_substitute_leave(f_b.id, f_b.name, "2026-10-20")
            leave_b.status = "Approved"
            auto_assign_substitutes_for_leave(leave_b)
            db.session.commit()
            invalidate_timetable_cache()

            # Verify Person B's duty covering Person A was cancelled
            reloaded_sub_ab = db.session.get(SubstituteAssignment, sub_ab.id)
            self.assertEqual(reloaded_sub_ab.status, "Cancelled")

            # Verify active substitute for Person A is NOT Person B and NOT Person A
            active_a_subs = SubstituteAssignment.query.filter_by(
                faculty_id=f_a.id, date="2026-10-20", status="Approved"
            ).all()
            for sa in active_a_subs:
                self.assertNotEqual(sa.substitute_id, f_a.id)
                self.assertNotEqual(sa.substitute_id, f_b.id)

            # Verify active substitute for Person B is NOT Person A and NOT Person B
            active_b_subs = SubstituteAssignment.query.filter_by(
                faculty_id=f_b.id, date="2026-10-20", status="Approved"
            ).all()
            for sb in active_b_subs:
                self.assertNotEqual(sb.substitute_id, f_a.id)
                self.assertNotEqual(sb.substitute_id, f_b.id)

            # Clean up
            for s in SubstituteAssignment.query.filter_by(date="2026-10-20").all():
                db.session.delete(s)
            for l in LeaveRequest.query.filter_by(leave_date="2026-10-20").all():
                db.session.delete(l)
            db.session.commit()
            invalidate_timetable_cache()

    def test_admin_delete_leave_restores_original_schedule(self):
        """Admin delete leave route permanently removes leave and restores original timetable schedule."""
        with app.app_context():
            f_orig = Faculty.query.first()
            f_sub = Faculty.query.filter(Faculty.id != f_orig.id).first()

            leave = LeaveRequest(
                faculty_id=f_orig.id,
                faculty_name=f_orig.name,
                leave_date="2026-10-22",
                reason="Conference",
                substitute_name=f_sub.name,
                status="Approved",
            )
            db.session.add(leave)
            db.session.commit()
            leave_id = leave.id

            sub = SubstituteAssignment(
                faculty_id=f_orig.id,
                faculty_name=f_orig.name,
                substitute_id=f_sub.id,
                substitute_name=f_sub.name,
                subject=f_orig.assigned_subject,
                section=f_orig.section,
                day="Thu",
                date="2026-10-22",
                status="Approved",
            )
            db.session.add(sub)
            db.session.commit()
            invalidate_timetable_cache()

        # Delete the leave via admin route
        del_res = self.client.get(f"/admin/leave/{leave_id}/delete", follow_redirects=True)
        self.assertEqual(del_res.status_code, 200)

        with app.app_context():
            self.assertIsNone(db.session.get(LeaveRequest, leave_id))
            # Associated substitute assignments must be deleted
            subs = SubstituteAssignment.query.filter_by(date="2026-10-22").all()
            self.assertEqual(len(subs), 0)


if __name__ == "__main__":
    unittest.main()


