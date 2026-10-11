import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy

basedir = os.path.abspath(os.path.dirname(__file__))
default_db_path = os.path.join(basedir, 'pfas.db')

app = Flask(__name__, template_folder='.')
app.config['SECRET_KEY'] = 'pfas-secret-key-123'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('PFAS_DATABASE_URI', f'sqlite:///{default_db_path}')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- DATABASE MODELS ---

class Faculty(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    department = db.Column(db.String(50), nullable=False)
    weekly_hours = db.Column(db.Integer, default=12)
    assigned_subject = db.Column(db.String(100), nullable=True)
    domain = db.Column(db.String(100), nullable=True)
    year = db.Column(db.String(10), nullable=True)
    section = db.Column(db.String(10), nullable=True)

class LeaveRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=False)
    faculty_name = db.Column(db.String(100), nullable=False)
    leave_date = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(200), nullable=False)
    substitute_name = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default='Pending')  # Pending, Approved, Rejected

class Preference(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=False)
    faculty_name = db.Column(db.String(100), nullable=False)
    preferred_subject = db.Column(db.String(100), nullable=False)
    preferred_time_slot = db.Column(db.String(100), nullable=False)

# --- ROUTES ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    role = request.form.get('role')
    email = request.form.get('email')
    
    session['user'] = email
    session['role'] = role

    if role == 'admin':
        return redirect(url_for('admin_dashboard'))
    else:
        return redirect(url_for('faculty_dashboard'))

# --- ADMIN ROUTES (Dashboard + CRUD + Approvals) ---

@app.route('/admin')
def admin_dashboard():
    selected_department = request.args.get('department', '')
    selected_year = request.args.get('year', '')
    selected_section = request.args.get('section', '')

    query = Faculty.query

    if selected_department:
        query = query.filter_by(department=selected_department)
    if selected_year:
        query = query.filter_by(year=selected_year)
    if selected_section:
        query = query.filter_by(section=selected_section)

    faculty_list = query.all()
    leaves = LeaveRequest.query.all()
    preferences = Preference.query.all()

    chart_labels = [f.name for f in faculty_list]
    chart_data = [f.weekly_hours for f in faculty_list]

    return render_template(
        'admin.html',
        faculty=faculty_list,
        leaves=leaves,
        preferences=preferences,
        selected_department=selected_department,
        selected_year=selected_year,
        selected_section=selected_section,
        chart_labels=chart_labels,
        chart_data=chart_data
    )

# CRUD: Add Faculty
@app.route('/admin/faculty/add', methods=['POST'])
def add_faculty():
    name = request.form.get('name')
    email = request.form.get('email')
    department = request.form.get('department')
    domain = request.form.get('domain')
    subject = request.form.get('assigned_subject')
    year = request.form.get('year')
    section = request.form.get('section')
    hours = request.form.get('weekly_hours', 12)

    new_faculty = Faculty(
        name=name, email=email, department=department,
        domain=domain, assigned_subject=subject,
        year=year, section=section, weekly_hours=int(hours)
    )
    db.session.add(new_faculty)
    db.session.commit()
    flash('Faculty added successfully!')
    return redirect(url_for('admin_dashboard'))

# CRUD: Edit Faculty
@app.route('/admin/faculty/edit/<int:id>', methods=['POST'])
def edit_faculty(id):
    f = Faculty.query.get_or_404(id)
    f.name = request.form.get('name')
    f.email = request.form.get('email')
    f.department = request.form.get('department')
    f.domain = request.form.get('domain')
    f.assigned_subject = request.form.get('assigned_subject')
    f.year = request.form.get('year')
    f.section = request.form.get('section')
    f.weekly_hours = int(request.form.get('weekly_hours', 12))
    
    db.session.commit()
    flash('Faculty updated successfully!')
    return redirect(url_for('admin_dashboard'))

# CRUD: Delete Faculty
@app.route('/admin/faculty/delete/<int:id>')
def delete_faculty(id):
    f = Faculty.query.get_or_404(id)
    db.session.delete(f)
    db.session.commit()
    flash('Faculty deleted successfully!')
    return redirect(url_for('admin_dashboard'))

# Leave Approval Actions
@app.route('/admin/leave/<int:id>/<string:action>')
def handle_leave(id, action):
    leave = LeaveRequest.query.get_or_404(id)
    if action == 'approve':
        leave.status = 'Approved'
    elif action == 'reject':
        leave.status = 'Rejected'
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

# --- FACULTY ROUTES ---

@app.route('/faculty')
def faculty_dashboard():
    email = session.get('user', 'sharma@icfaitech.in')
    faculty_member = Faculty.query.filter_by(email=email).first()
    
    if not faculty_member:
        faculty_member = Faculty.query.first()

    all_faculty = Faculty.query.filter(Faculty.id != faculty_member.id).all()
    my_leaves = LeaveRequest.query.filter_by(faculty_id=faculty_member.id).all()
    my_preference = Preference.query.filter_by(faculty_id=faculty_member.id).first()

    # Retrieve faculty member's validated schedule from generated timetable
    timetable_res = get_or_generate_timetable()
    weekly_schedule = []
    for item in timetable_res.get('master_grid', []):
        fac = item.get('faculty')
        fac_id = getattr(fac, 'id', None) or (fac.get('id') if isinstance(fac, dict) else None)
        if fac_id == faculty_member.id:
            weekly_schedule = item.get('slots', [])
            break

    return render_template(
        'faculty.html',
        faculty=faculty_member,
        schedule=weekly_schedule,
        all_faculty=all_faculty,
        leaves=my_leaves,
        preference=my_preference,
        timetable_status=timetable_res.get('status'),
    )

# Apply for Leave & Request Substitute
@app.route('/faculty/apply-leave', methods=['POST'])
def apply_leave():
    faculty_id = request.form.get('faculty_id')
    faculty_name = request.form.get('faculty_name')
    leave_date = request.form.get('leave_date')
    reason = request.form.get('reason')
    substitute_name = request.form.get('substitute_name')

    new_leave = LeaveRequest(
        faculty_id=int(faculty_id),
        faculty_name=faculty_name,
        leave_date=leave_date,
        reason=reason,
        substitute_name=substitute_name
    )
    db.session.add(new_leave)
    db.session.commit()
    flash('Leave request and substitute request submitted!')
    return redirect(url_for('faculty_dashboard'))

# Submit Preferences
@app.route('/faculty/submit-preference', methods=['POST'])
def submit_preference():
    faculty_id = request.form.get('faculty_id')
    faculty_name = request.form.get('faculty_name')
    preferred_subject = request.form.get('preferred_subject')
    preferred_time_slot = request.form.get('preferred_time_slot')

    existing = Preference.query.filter_by(faculty_id=int(faculty_id)).first()
    if existing:
        existing.preferred_subject = preferred_subject
        existing.preferred_time_slot = preferred_time_slot
    else:
        pref = Preference(
            faculty_id=int(faculty_id),
            faculty_name=faculty_name,
            preferred_subject=preferred_subject,
            preferred_time_slot=preferred_time_slot
        )
        db.session.add(pref)
    
    db.session.commit()
    flash('Preferences updated successfully!')
    return redirect(url_for('faculty_dashboard'))
from ml import generate_timetable, format_master_grid, validate_timetable

# --- INSTITUTIONAL CURRICULUM & WORKLOAD CONTRACT ---
# Verified course catalog with accredited lecture hours, lab hours, and target sections.
# Courses with unverified hours or missing data will NOT be guessed; they remain unscheduled.
INSTITUTIONAL_COURSE_CATALOG = {
    "Data Structures": {
        "subject_id": "CS201",
        "domain": "Algorithms",
        "sections": ["A"],
        "lecture_hours": 3,
        "lab_hours": 2,
        "required_qualification": "Algorithms",
    },
    "Machine Learning": {
        "subject_id": "CS301",
        "domain": "AI/ML",
        "sections": ["B"],
        "lecture_hours": 3,
        "lab_hours": 0,
        "required_qualification": "AI/ML",
    },
    "Database Systems": {
        "subject_id": "CS202",
        "domain": "Database",
        "sections": ["A"],
        "lecture_hours": 3,
        "lab_hours": 2,
        "required_qualification": "Database",
    },
    "Deep Learning": {
        "subject_id": "AI401",
        "domain": "AI/ML",
        "sections": ["A"],
        "lecture_hours": 3,
        "lab_hours": 0,
        "required_qualification": "AI/ML",
    },
    "Computer Networks": {
        "subject_id": "CS302",
        "domain": "Networks",
        "sections": ["C"],
        "lecture_hours": 3,
        "lab_hours": 2,
        "required_qualification": "Networks",
    },
}

# Verified Contractual Workload Caps (Max Weekly Teaching Hours).
# Under institutional policy, confirmed workload caps per faculty.
# If a faculty cap is unverified, allocation must be stopped for review.
INSTITUTIONAL_FACULTY_CAPS = {
    1: 14.0,  # Dr. M. K. Sharma: Cap 14 hrs
    2: 14.0,  # Prof. R. V. Rao: Cap 14 hrs
    3: 14.0,  # Dr. S. K. Gupta: Cap 14 hrs
    4: 12.0,  # Prof. A. N. Reddy: Cap 12 hrs
    5: 14.0,  # Dr. P. C. Mehta: Cap 14 hrs
}

TIMETABLE_SEED = 42
_GENERATED_TIMETABLE_CACHE = None

def invalidate_timetable_cache():
    global _GENERATED_TIMETABLE_CACHE
    _GENERATED_TIMETABLE_CACHE = None

def get_or_generate_timetable(force_refresh=False):
    global _GENERATED_TIMETABLE_CACHE
    if _GENERATED_TIMETABLE_CACHE is not None and not force_refresh:
        return _GENERATED_TIMETABLE_CACHE

    faculty_list = Faculty.query.all()
    active_leaves = LeaveRequest.query.filter(LeaveRequest.status.in_(['Approved', 'Pending'])).all()
    faculty_on_leave_ids = {l.faculty_id for l in active_leaves}

    preferences = Preference.query.all()
    pref_by_fid = {p.faculty_id: p for p in preferences}

    # Normalize faculty pool using confirmed institutional data
    faculty_pool = []
    for f in faculty_list:
        is_on_leave = f.id in faculty_on_leave_ids
        # Only use confirmed workload cap. Do not invent a fallback cap.
        cap = INSTITUTIONAL_FACULTY_CAPS.get(f.id)
        pref = pref_by_fid.get(f.id)

        faculty_pool.append({
            "id": f.id,
            "faculty_id": f.id,
            "name": f.name,
            "faculty_name": f.name,
            "department": f.department,
            "assigned_subject": f.assigned_subject,
            "section": f.section,
            "year": f.year,
            "weekly_hours": f.weekly_hours,
            "domain": f.domain or f.assigned_subject,
            "qualifications": f.domain or f.assigned_subject,
            "workload": 0.0,
            "current_weekly_hours": 0.0,
            "max_workload": cap,
            "weekly_hour_cap": cap,
            "available": not is_on_leave,
            "on_leave": is_on_leave,
            "preferred_subject": pref.preferred_subject if pref else None,
            "preferred_time_slot": pref.preferred_time_slot if pref else None,
            "raw_record": {
                "id": f.id,
                "name": f.name,
                "department": f.department,
                "assigned_subject": f.assigned_subject,
                "section": f.section,
                "year": f.year,
                "weekly_hours": f.weekly_hours,
            },
        })

    # Assemble requirements from verified institutional course catalog
    requirements = []
    seen_requirements = set()
    for f in faculty_list:
        subj_name = f.assigned_subject
        if not subj_name:
            continue
        catalog_entry = INSTITUTIONAL_COURSE_CATALOG.get(subj_name)
        if catalog_entry:
            for sec in catalog_entry["sections"]:
                req_key = (catalog_entry["subject_id"], sec)
                if req_key not in seen_requirements:
                    seen_requirements.add(req_key)
                    requirements.append({
                        "subject_id": catalog_entry["subject_id"],
                        "subject": subj_name,
                        "name": subj_name,
                        "domain": catalog_entry["domain"],
                        "section": sec,
                        "lecture_hours": catalog_entry["lecture_hours"],
                        "lab_hours": catalog_entry["lab_hours"],
                        "required_qualification": catalog_entry.get("required_qualification"),
                        "preferred_faculty_id": f.id,
                    })
        else:
            # Unverified course: do NOT invent hours or assume labs
            req_key = (f"UNVERIFIED_{f.id}", f.section or "A")
            if req_key not in seen_requirements:
                seen_requirements.add(req_key)
                requirements.append({
                    "subject_id": f"UNVERIFIED_{f.id}",
                    "subject": subj_name,
                    "name": subj_name,
                    "domain": f.domain or "",
                    "section": f.section,
                    "lecture_hours": None,
                    "lab_hours": None,
                    "required_qualification": None,
                })

    time_slots = [
        "09:00 AM - 10:00 AM",
        "10:00 AM - 11:00 AM",
        "11:15 AM - 12:15 PM",
        "01:15 PM - 02:15 PM",
        "02:15 PM - 03:15 PM"
    ]

    gen_result = generate_timetable(
        requirements=requirements,
        faculty_pool=faculty_pool,
        time_slots=time_slots,
        seed=TIMETABLE_SEED,
        allow_partial=True,
    )

    _GENERATED_TIMETABLE_CACHE = gen_result
    return gen_result

# 5. MASTER TIMETABLE / SCHEDULE (All Faculty Combined)
@app.route('/schedule')
def master_schedule():
    gen_result = get_or_generate_timetable()
    time_slots = [
        "09:00 AM - 10:00 AM",
        "10:00 AM - 11:00 AM",
        "11:15 AM - 12:15 PM",
        "01:15 PM - 02:15 PM",
        "02:15 PM - 03:15 PM"
    ]

    return render_template(
        'schedule.html',
        master_grid=gen_result.get('master_grid', []),
        time_slots=time_slots,
        timetable_status=gen_result.get('status'),
        unscheduled_requirements=gen_result.get('unscheduled_requirements', []),
        unscheduled_count=gen_result.get('unscheduled_count', 0),
        validation=gen_result.get('validation', {}),
        seed=gen_result.get('seed'),
        total_assigned_count=gen_result.get('total_assigned_count', 0),
    )

@app.route('/schedule/regenerate')
def regenerate_schedule():
    get_or_generate_timetable(force_refresh=True)
    flash('Timetable regenerated using ML constraint-based solver.')
    return redirect(url_for('master_schedule'))

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.')
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        db.drop_all()
        db.create_all()
        
        sample_data = [
            Faculty(name="Dr. M. K. Sharma", email="sharma@icfaitech.in", department="CSE", weekly_hours=12, assigned_subject="Data Structures", domain="Algorithms", year="2", section="A"),
            Faculty(name="Prof. R. V. Rao", email="rao@icfaitech.in", department="CSE", weekly_hours=14, assigned_subject="Machine Learning", domain="AI/ML", year="3", section="B"),
            Faculty(name="Dr. S. K. Gupta", email="gupta@icfaitech.in", department="CSE", weekly_hours=13, assigned_subject="Database Systems", domain="Database", year="2", section="A"),
            Faculty(name="Prof. A. N. Reddy", email="reddy@icfaitech.in", department="AI_DS", weekly_hours=11, assigned_subject="Deep Learning", domain="AI/ML", year="4", section="A"),
            Faculty(name="Dr. P. C. Mehta", email="mehta@icfaitech.in", department="AI_DS", weekly_hours=14, assigned_subject="Computer Networks", domain="Networks", year="3", section="C"),
        ]
        db.session.add_all(sample_data)
        db.session.commit()

    app.run(debug=True)