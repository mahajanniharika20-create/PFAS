"""Development script to seed synthetic faculty records into PFAS database.

Usage:
    python scripts/seed_synthetic_faculty.py

Rules:
1. Repeatable and idempotent: Running multiple times will never create duplicate records.
2. Preserves all real faculty records untouched.
3. Clearly distinguishes synthetic records via '[Synthetic]' name tag and 'synth@icfaitech.in' email domain.
4. Uses the existing database schema (id, name, email, department, weekly_hours, assigned_subject, domain, year, section).
5. Development-only: Not executed automatically on application startup.
"""
import os
import sys

# Ensure repository root is in python path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import app, db, Faculty, invalidate_timetable_cache


# Realistic synthetic faculty profiles tailored to support the accredited course catalog
SYNTHETIC_FACULTY_DATA = [
    # Section A Courses (CSE / AI_DS)
    {
        "name": "Dr. Aris Thorne [Synthetic]",
        "email": "thorne.synth@icfaitech.in",
        "department": "CSE",
        "weekly_hours": 14,
        "assigned_subject": "Operating Systems",
        "domain": "Networks",
        "year": "2",
        "section": "A",
    },
    {
        "name": "Prof. Elena Vance [Synthetic]",
        "email": "vance.synth@icfaitech.in",
        "department": "CSE",
        "weekly_hours": 14,
        "assigned_subject": "Web Development",
        "domain": "Web Development",
        "year": "2",
        "section": "A",
    },

    # Section B Courses (CSE)
    {
        "name": "Dr. Marcus Cole [Synthetic]",
        "email": "cole.synth@icfaitech.in",
        "department": "CSE",
        "weekly_hours": 14,
        "assigned_subject": "Data Structures",
        "domain": "Algorithms",
        "year": "2",
        "section": "B",
    },
    {
        "name": "Prof. Sarah Jenkins [Synthetic]",
        "email": "jenkins.synth@icfaitech.in",
        "department": "CSE",
        "weekly_hours": 14,
        "assigned_subject": "Database Systems",
        "domain": "Database",
        "year": "2",
        "section": "B",
    },
    {
        "name": "Dr. David Zhao [Synthetic]",
        "email": "zhao.synth@icfaitech.in",
        "department": "CSE",
        "weekly_hours": 14,
        "assigned_subject": "Operating Systems",
        "domain": "Networks",
        "year": "3",
        "section": "B",
    },
    {
        "name": "Dr. Fiona Gallagher [Synthetic]",
        "email": "gallagher.synth@icfaitech.in",
        "department": "CSE",
        "weekly_hours": 14,
        "assigned_subject": "Computer Networks",
        "domain": "Networks",
        "year": "3",
        "section": "B",
    },
    {
        "name": "Prof. Vikram Patel [Synthetic]",
        "email": "patel.synth@icfaitech.in",
        "department": "CSE",
        "weekly_hours": 14,
        "assigned_subject": "Data Science",
        "domain": "Data Science",
        "year": "3",
        "section": "A",
    },

    # Section C Courses (AI_DS)
    {
        "name": "Dr. Kenneth Hayes [Synthetic]",
        "email": "hayes.synth@icfaitech.in",
        "department": "AI_DS",
        "weekly_hours": 14,
        "assigned_subject": "Machine Learning",
        "domain": "AI/ML",
        "year": "3",
        "section": "C",
    },
    {
        "name": "Prof. Maya Lin [Synthetic]",
        "email": "lin.synth@icfaitech.in",
        "department": "AI_DS",
        "weekly_hours": 14,
        "assigned_subject": "Data Science",
        "domain": "Data Science",
        "year": "3",
        "section": "C",
    },
    {
        "name": "Dr. Julian Croft [Synthetic]",
        "email": "croft.synth@icfaitech.in",
        "department": "AI_DS",
        "weekly_hours": 14,
        "assigned_subject": "Deep Learning",
        "domain": "AI/ML",
        "year": "4",
        "section": "C",
    },
    {
        "name": "Prof. Rachel Foster [Synthetic]",
        "email": "foster.synth@icfaitech.in",
        "department": "AI_DS",
        "weekly_hours": 12,
        "assigned_subject": "Cybersecurity",
        "domain": "Security",
        "year": "3",
        "section": "C",
    },
    {
        "name": "Dr. Sunita Roy [Synthetic]",
        "email": "roy.synth@icfaitech.in",
        "department": "AI_DS",
        "weekly_hours": 14,
        "assigned_subject": "Web Development",
        "domain": "Web Development",
        "year": "2",
        "section": "C",
    },
]


def seed_synthetic_faculty():
    """Insert synthetic faculty records without modifying real records or creating duplicates."""
    added_count = 0
    updated_count = 0
    skipped_count = 0

    with app.app_context():
        db.create_all()
        for data in SYNTHETIC_FACULTY_DATA:
            existing = Faculty.query.filter_by(email=data["email"]).first()
            if existing:
                changed = False
                for key in ["name", "department", "weekly_hours", "assigned_subject", "domain", "year", "section"]:
                    if getattr(existing, key) != data[key]:
                        setattr(existing, key, data[key])
                        changed = True
                if changed:
                    updated_count += 1
                else:
                    skipped_count += 1
                continue

            new_fac = Faculty(
                name=data["name"],
                email=data["email"],
                department=data["department"],
                weekly_hours=data["weekly_hours"],
                assigned_subject=data["assigned_subject"],
                domain=data["domain"],
                year=data["year"],
                section=data["section"],
            )
            db.session.add(new_fac)
            added_count += 1

        db.session.commit()
        invalidate_timetable_cache()

        total = Faculty.query.count()
        synthetic_total = Faculty.query.filter(Faculty.email.like("%synth%")).count()
        real_total = total - synthetic_total

        print("=== Synthetic Faculty Seeding Summary ===")
        print(f"Added new synthetic records: {added_count}")
        print(f"Skipped (already existing):  {skipped_count}")
        print(f"Total faculty in database:   {total} (Real: {real_total}, Synthetic: {synthetic_total})")


if __name__ == "__main__":
    seed_synthetic_faculty()
