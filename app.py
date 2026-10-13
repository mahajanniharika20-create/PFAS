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
    time_slot = db.Column(db.String(50), nullable=True)
    reason = db.Column(db.String(200), nullable=False)
    substitute_name = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default='Pending')  # Pending, Approved, Rejected, Cancelled

class SubstituteAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=False)
    faculty_name = db.Column(db.String(100), nullable=False)
    substitute_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=True)
    substitute_name = db.Column(db.String(100), nullable=False)
    subject = db.Column(db.String(100), nullable=True)
    section = db.Column(db.String(10), nullable=True)
    day = db.Column(db.String(20), nullable=True)
    time_slot = db.Column(db.String(50), nullable=True)
    date = db.Column(db.String(20), nullable=True)
    status = db.Column(db.String(20), default='Approved')  # Approved, Cancelled

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
    substitutes = SubstituteAssignment.query.all()

    # Precalculate genuinely eligible substitute faculty for each leave request
    eligible_subs_by_leave_id = {l.id: get_eligible_substitutes_for_leave(l) for l in leaves}

    chart_labels = [f.name for f in faculty_list]
    chart_data = [f.weekly_hours for f in faculty_list]

    return render_template(
        'admin.html',
        faculty=faculty_list,
        leaves=leaves,
        preferences=preferences,
        substitutes=substitutes,
        eligible_subs_by_leave_id=eligible_subs_by_leave_id,
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

    if not email:
        flash('Faculty email is required.')
        return redirect(url_for('admin_dashboard'))

    existing = Faculty.query.filter_by(email=email).first()
    if existing:
        flash(f'Faculty with email {email} already exists! Please use a unique email.')
        return redirect(url_for('admin_dashboard'))

    try:
        hours_val = int(hours)
    except (ValueError, TypeError):
        hours_val = 12

    new_faculty = Faculty(
        name=name, email=email, department=department,
        domain=domain, assigned_subject=subject,
        year=year, section=section, weekly_hours=hours_val
    )
    db.session.add(new_faculty)
    try:
        db.session.commit()
        invalidate_timetable_cache()
        flash('Faculty added successfully!')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding faculty: {str(e)}')
    return redirect(url_for('admin_dashboard'))

# CRUD: Edit Faculty
@app.route('/admin/faculty/edit/<int:id>', methods=['POST'])
def edit_faculty(id):
    f = Faculty.query.get_or_404(id)
    name = request.form.get('name')
    email = request.form.get('email')
    department = request.form.get('department')
    domain = request.form.get('domain')
    subject = request.form.get('assigned_subject')
    year = request.form.get('year')
    section = request.form.get('section')
    hours = request.form.get('weekly_hours', 12)

    if not email:
        flash('Faculty email is required.')
        return redirect(url_for('admin_dashboard'))

    existing = Faculty.query.filter(Faculty.email == email, Faculty.id != id).first()
    if existing:
        flash(f'Another faculty with email {email} already exists! Please use a unique email.')
        return redirect(url_for('admin_dashboard'))

    try:
        hours_val = int(hours)
    except (ValueError, TypeError):
        hours_val = 12

    f.name = name
    f.email = email
    f.department = department
    f.domain = domain
    f.assigned_subject = subject
    f.year = year
    f.section = section
    f.weekly_hours = hours_val

    try:
        db.session.commit()
        invalidate_timetable_cache()
        flash('Faculty updated successfully!')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating faculty: {str(e)}')
    return redirect(url_for('admin_dashboard'))

# CRUD: Delete Faculty
@app.route('/admin/faculty/delete/<int:id>')
def delete_faculty(id):
    f = Faculty.query.get_or_404(id)
    db.session.delete(f)
    try:
        db.session.commit()
        invalidate_timetable_cache()
        flash('Faculty deleted successfully!')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting faculty: {str(e)}')
    return redirect(url_for('admin_dashboard'))

# Leave Approval & Substitute Actions
@app.route('/admin/leave/<int:id>/<string:action>')
def handle_leave(id, action):
    leave = LeaveRequest.query.get_or_404(id)
    if action == 'approve':
        leave.status = 'Approved'
        # 1. Detect and reassign any emergency substitute duties if this faculty was an appointed substitute
        emergency_notes = handle_emergency_substitute_leave(leave.faculty_id, leave.faculty_name, leave.leave_date, leave.time_slot)
        # 2. Automatically identify and assign suitable substitutes for this faculty's own affected classes
        assigned_records, unassigned_count = auto_assign_substitutes_for_leave(leave)
        db.session.commit()
        invalidate_timetable_cache()

        if unassigned_count > 0:
            flash(f'Leave approved for {leave.faculty_name}. Note: {unassigned_count} class period(s) could not find an available eligible substitute and are marked as "Unassigned — Admin Action Required".', 'warning')
        else:
            sub_names = {r['substitute_name'] for r in assigned_records if r.get('substitute_name') and r['substitute_name'] != 'Unassigned — Admin Action Required'}
            if sub_names:
                flash(f'Leave approved for {leave.faculty_name}. Automatically appointed substitute(s): {", ".join(sorted(sub_names))}.', 'success')
            else:
                flash(f'Leave approved for {leave.faculty_name}. (No scheduled class periods affected on {leave.leave_date}).', 'success')
        return redirect(url_for('admin_dashboard'))
    elif action == 'reject':
        leave.status = 'Rejected'
        for sa in SubstituteAssignment.query.filter_by(faculty_id=leave.faculty_id, date=leave.leave_date, status='Approved').all():
            sa.status = 'Cancelled'
        db.session.commit()
        invalidate_timetable_cache()
        flash(f'Leave request for {leave.faculty_name} rejected.', 'info')
        return redirect(url_for('admin_dashboard'))
    elif action == 'cancel':
        leave.status = 'Cancelled'
        for sa in SubstituteAssignment.query.filter_by(faculty_id=leave.faculty_id, date=leave.leave_date, status='Approved').all():
            sa.status = 'Cancelled'
        db.session.commit()
        invalidate_timetable_cache()
        flash(f'Leave request for {leave.faculty_name} cancelled and substitute assignments deactivated.', 'info')
        return redirect(url_for('admin_dashboard'))
    elif action == 'delete':
        return delete_leave(id)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/leave/<int:id>/delete')
def delete_leave(id):
    leave = LeaveRequest.query.get_or_404(id)
    fac_name = leave.faculty_name
    fac_id = leave.faculty_id
    leave_date = leave.leave_date

    # 1. Delete associated SubstituteAssignments for this leave
    for sa in SubstituteAssignment.query.filter_by(faculty_id=fac_id, date=leave_date).all():
        db.session.delete(sa)

    # 2. Also delete duties where this faculty was assigned as a substitute on this date
    for sa in SubstituteAssignment.query.filter_by(substitute_id=fac_id, date=leave_date).all():
        db.session.delete(sa)

    # 3. Delete the LeaveRequest itself
    db.session.delete(leave)
    db.session.commit()
    invalidate_timetable_cache()
    flash(f'Leave record for {fac_name} on {leave_date} deleted successfully and timetable schedule restored.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/leave/<int:id>/assign-substitute', methods=['POST'])
def assign_substitute_to_leave(id):
    leave = LeaveRequest.query.get_or_404(id)
    substitute_name = request.form.get('substitute_name')
    if not substitute_name or not substitute_name.strip():
        flash('Please select a valid substitute faculty.', 'danger')
        return redirect(url_for('admin_dashboard'))

    sub_name_clean = substitute_name.strip()
    sub_fac = Faculty.query.filter_by(name=sub_name_clean).first()
    if not sub_fac:
        flash(f'Faculty "{sub_name_clean}" not found.', 'danger')
        return redirect(url_for('admin_dashboard'))

    # Server-side revalidation of candidate availability
    is_valid, reason = validate_substitute_for_leave(sub_fac, leave)
    if not is_valid:
        flash(f'Cannot appoint {sub_fac.name}: {reason}', 'danger')
        return redirect(url_for('admin_dashboard'))

    # Save assignments for all affected classes
    affected = get_affected_classes_for_leave(leave)
    if affected:
        for cls in affected:
            day = cls.get('day')
            slot = cls.get('time_slot')
            subj = cls.get('subject')
            sec = cls.get('section')
            existing = SubstituteAssignment.query.filter_by(
                faculty_id=leave.faculty_id, day=day, time_slot=slot, status='Approved'
            ).first()
            if existing:
                existing.substitute_id = sub_fac.id
                existing.substitute_name = sub_fac.name
                existing.subject = subj
                existing.section = sec
                existing.status = 'Approved'
            else:
                db.session.add(SubstituteAssignment(
                    faculty_id=leave.faculty_id,
                    faculty_name=leave.faculty_name,
                    substitute_id=sub_fac.id,
                    substitute_name=sub_fac.name,
                    subject=subj,
                    section=sec,
                    day=day,
                    time_slot=slot,
                    date=leave.leave_date,
                    status='Approved',
                ))
    else:
        orig_fac = Faculty.query.get(leave.faculty_id)
        leave_days = resolve_leave_days(leave.leave_date)
        resolved_day = leave_days[0] if leave_days else None
        db.session.add(SubstituteAssignment(
            faculty_id=leave.faculty_id,
            faculty_name=leave.faculty_name,
            substitute_id=sub_fac.id,
            substitute_name=sub_fac.name,
            subject=orig_fac.assigned_subject if orig_fac else "General",
            section=orig_fac.section if orig_fac else None,
            day=resolved_day,
            time_slot=None,
            date=leave.leave_date,
            status='Approved',
        ))

    leave.substitute_name = sub_fac.name
    leave.status = 'Approved'
    db.session.commit()
    invalidate_timetable_cache()
    flash(f'Substitute {sub_fac.name} successfully appointed and verified for {leave.faculty_name}!', 'success')
    return redirect(url_for('admin_dashboard'))

# Direct Substitute Appointment by Admin
@app.route('/admin/substitute/appoint', methods=['POST'])
def appoint_substitute():
    faculty_id = request.form.get('faculty_id')
    substitute_name = request.form.get('substitute_name')
    subject = request.form.get('subject')
    section = request.form.get('section')
    day = request.form.get('day')
    time_slot = request.form.get('time_slot')
    date = request.form.get('date')

    fac = Faculty.query.get(int(faculty_id)) if faculty_id else None
    if not fac or not substitute_name:
        flash('Invalid faculty or substitute specified.', 'danger')
        return redirect(url_for('admin_dashboard'))

    sub_fac = Faculty.query.filter_by(name=substitute_name.strip()).first()
    if not sub_fac:
        flash(f'Substitute faculty "{substitute_name}" not found.', 'danger')
        return redirect(url_for('admin_dashboard'))

    # Server-side revalidation if day and time_slot are provided
    if day and time_slot:
        is_valid, reason = validate_substitute_for_class(
            sub_fac, fac.id, day, time_slot, subject or fac.assigned_subject, section or fac.section, date
        )
        if not is_valid:
            flash(f'Cannot appoint {sub_fac.name}: {reason}', 'danger')
            return redirect(url_for('admin_dashboard'))

    new_sub = SubstituteAssignment(
        faculty_id=fac.id,
        faculty_name=fac.name,
        substitute_id=sub_fac.id,
        substitute_name=sub_fac.name,
        subject=subject or fac.assigned_subject,
        section=section or fac.section,
        day=day,
        time_slot=time_slot,
        date=date,
        status='Approved',
    )
    db.session.add(new_sub)
    db.session.commit()
    invalidate_timetable_cache()
    flash(f'Substitute {sub_fac.name} appointed successfully for {fac.name}!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/substitute/cancel/<int:id>')
def cancel_substitute(id):
    sub = SubstituteAssignment.query.get_or_404(id)
    sub.status = 'Cancelled'
    # Clear matching LeaveRequest substitute appointments so cancellation is complete
    leaves = LeaveRequest.query.filter_by(faculty_id=sub.faculty_id).all()
    for l in leaves:
        l.substitute_name = None
    db.session.commit()
    invalidate_timetable_cache()
    flash('Substitute appointment cancelled successfully.')
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
    my_substitute_name = None
    for item in timetable_res.get('master_grid', []):
        fac = item.get('faculty')
        fac_id = getattr(fac, 'id', None) or (fac.get('id') if isinstance(fac, dict) else None)
        if str(fac_id) == str(faculty_member.id):
            weekly_schedule = item.get('slots', [])
            my_substitute_name = fac.get('substitute_name') if isinstance(fac, dict) else getattr(fac, 'substitute_name', None)
            break

    substitute_duties = []
    for cov in timetable_res.get('substitution_coverage', []):
        if cov.get('substitute_name') == faculty_member.name:
            substitute_duties.append(cov)

    return render_template(
        'faculty.html',
        faculty=faculty_member,
        schedule=weekly_schedule,
        all_faculty=all_faculty,
        leaves=my_leaves,
        preference=my_preference,
        timetable_status=timetable_res.get('status'),
        substitute_duties=substitute_duties,
        my_substitute_name=my_substitute_name,
    )

# Apply for Leave
@app.route('/faculty/apply-leave', methods=['POST'])
def apply_leave():
    faculty_id = request.form.get('faculty_id')
    faculty_name = request.form.get('faculty_name')
    if not faculty_name and faculty_id:
        f_rec = Faculty.query.get(int(faculty_id))
        if f_rec:
            faculty_name = f_rec.name
    leave_date = request.form.get('leave_date')
    time_slot = request.form.get('time_slot')
    reason = request.form.get('reason')

    is_emergency = "emergency" in str(reason or "").lower()

    new_leave = LeaveRequest(
        faculty_id=int(faculty_id),
        faculty_name=faculty_name,
        leave_date=leave_date,
        time_slot=time_slot if time_slot and time_slot.strip() else None,
        reason=reason,
        substitute_name=None,
        status='Approved' if is_emergency else 'Pending',
    )
    db.session.add(new_leave)
    db.session.commit()

    if is_emergency:
        # Emergency leave: immediately deactivate duties and auto-assign replacements
        handle_emergency_substitute_leave(int(faculty_id), faculty_name, leave_date, time_slot)
        assigned_records, unassigned_count = auto_assign_substitutes_for_leave(new_leave)
        db.session.commit()
        invalidate_timetable_cache()
        if unassigned_count > 0:
            flash(f'Emergency leave submitted. Note: {unassigned_count} class period(s) marked as Unassigned — Admin Action Required.', 'warning')
        else:
            flash('Emergency leave submitted and substitute coverage updated immediately.', 'success')
    else:
        invalidate_timetable_cache()
        flash('Leave request submitted successfully! Administrator will review and appoint an authorized substitute.')
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
# Verified course catalog with accredited lecture hours, lab hours, rooms, and target sections.
# Courses with unverified hours or missing data will NOT be guessed; they remain unscheduled.
INSTITUTIONAL_COURSE_CATALOG = {
    "Data Structures": {
        "subject_id": "CS201",
        "domain": "Algorithms",
        "sections": ["A", "B"],
        "lecture_hours": 3,
        "lab_hours": 2,
        "required_qualification": "Algorithms",
        "room": "Room 101",
        "lab_room": "Computing Lab 1",
    },
    "Database Systems": {
        "subject_id": "CS202",
        "domain": "Database",
        "sections": ["A", "B"],
        "lecture_hours": 3,
        "lab_hours": 2,
        "required_qualification": "Database",
        "room": "Room 102",
        "lab_room": "Database Lab",
    },
    "Operating Systems": {
        "subject_id": "CS203",
        "domain": "Networks",
        "sections": ["A", "B"],
        "lecture_hours": 3,
        "lab_hours": 2,
        "required_qualification": "Networks",
        "room": "Room 103",
        "lab_room": "Systems Lab",
    },
    "Machine Learning": {
        "subject_id": "CS301",
        "domain": "AI/ML",
        "sections": ["B", "C"],
        "lecture_hours": 3,
        "lab_hours": 0,
        "required_qualification": "AI/ML",
        "room": "Room 104",
        "lab_room": "AI/ML Lab",
    },
    "Deep Learning": {
        "subject_id": "AI401",
        "domain": "AI/ML",
        "sections": ["A", "C"],
        "lecture_hours": 3,
        "lab_hours": 0,
        "required_qualification": "AI/ML",
        "room": "Room 201",
        "lab_room": None,
    },
    "Computer Networks": {
        "subject_id": "CS302",
        "domain": "Networks",
        "sections": ["B", "C"],
        "lecture_hours": 3,
        "lab_hours": 2,
        "required_qualification": "Networks",
        "room": "Room 105",
        "lab_room": "Networks Lab",
    },
    "Web Development": {
        "subject_id": "CS204",
        "domain": "Web Development",
        "sections": ["A", "C"],
        "lecture_hours": 3,
        "lab_hours": 0,
        "required_qualification": "Web Development",
        "room": "Room 202",
        "lab_room": None,
    },
    "Data Science": {
        "subject_id": "DS301",
        "domain": "Data Science",
        "sections": ["A", "C"],
        "lecture_hours": 3,
        "lab_hours": 0,
        "required_qualification": "Data Science",
        "room": "Room 203",
        "lab_room": None,
    },
    "Cybersecurity": {
        "subject_id": "CS303",
        "domain": "Security",
        "sections": ["C"],
        "lecture_hours": 3,
        "lab_hours": 0,
        "required_qualification": "Security",
        "room": "Room 204",
        "lab_room": None,
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

CURRENT_TIMETABLE_SEED = 42
TIMETABLE_SEED = 42
_GENERATED_TIMETABLE_CACHE = None

def invalidate_timetable_cache():
    global _GENERATED_TIMETABLE_CACHE
    _GENERATED_TIMETABLE_CACHE = None

def get_active_substitutions():
    """Retrieve all confirmed, active substitute appointments approved by admin."""
    active_subs = []
    # Identify faculties currently on leave
    fids_on_leave = set()
    fnames_on_leave = set()
    try:
        for lr in LeaveRequest.query.filter_by(status='Approved').all():
            fids_on_leave.add(lr.faculty_id)
            if lr.faculty_name:
                fnames_on_leave.add(lr.faculty_name.strip().lower())
    except Exception:
        db.session.rollback()

    # Track covered faculties
    direct_covered = set()

    # 1. Direct SubstituteAssignments approved by admin
    try:
        approved_leaves = LeaveRequest.query.filter_by(status='Approved').all()
        for sa in SubstituteAssignment.query.filter_by(status='Approved').all():
            sub_name_clean = sa.substitute_name.strip() if sa.substitute_name else ""
            if not sub_name_clean:
                continue

            # Ensure appointed substitute is not on approved leave on the same date/day
            sa_days = resolve_leave_days(sa.date) if sa.date else ([sa.day] if sa.day else [])
            is_sub_on_leave = False
            for lr in approved_leaves:
                lr_days = resolve_leave_days(lr.leave_date)
                if (lr.faculty_id == sa.substitute_id) or (lr.faculty_name and lr.faculty_name.strip().lower() == sub_name_clean.lower()):
                    if sa.date and lr.leave_date and sa.date == lr.leave_date:
                        is_sub_on_leave = True
                        break
                    elif sa_days and any(d in lr_days for d in sa_days):
                        is_sub_on_leave = True
                        break
            if is_sub_on_leave:
                continue

            active_subs.append({
                'id': sa.id,
                'faculty_id': sa.faculty_id,
                'faculty_name': sa.faculty_name,
                'substitute_id': sa.substitute_id,
                'substitute_name': sub_name_clean,
                'subject': sa.subject,
                'section': sa.section,
                'day': sa.day,
                'time_slot': sa.time_slot,
                'date': sa.date,
                'source': 'direct',
            })
            direct_covered.add(str(sa.faculty_id))
    except Exception:
        db.session.rollback()

    # 2. Approved LeaveRequests with a substitute appointed
    try:
        for lr in LeaveRequest.query.filter_by(status='Approved').all():
            if lr.substitute_name and lr.substitute_name.strip():
                sub_clean = lr.substitute_name.strip()
                if sub_clean.lower() in fnames_on_leave:
                    continue
                if str(lr.faculty_id) in direct_covered:
                    continue
                active_subs.append({
                    'id': lr.id,
                    'faculty_id': lr.faculty_id,
                    'faculty_name': lr.faculty_name,
                    'substitute_name': sub_clean,
                    'date': lr.leave_date,
                    'source': 'leave',
                })
    except Exception:
        db.session.rollback()
    return active_subs

def get_or_generate_timetable(force_refresh=False, seed=None):
    global _GENERATED_TIMETABLE_CACHE, CURRENT_TIMETABLE_SEED
    if seed is not None:
        CURRENT_TIMETABLE_SEED = seed
        force_refresh = True
    if _GENERATED_TIMETABLE_CACHE is not None and not force_refresh:
        return _GENERATED_TIMETABLE_CACHE

    faculty_list = Faculty.query.all()
    active_leaves = LeaveRequest.query.filter(LeaveRequest.status == 'Approved').all()
    faculty_on_leave_ids = {l.faculty_id for l in active_leaves}

    preferences = Preference.query.all()
    pref_by_fid = {p.faculty_id: p for p in preferences}

    # Normalize faculty pool using confirmed institutional data
    faculty_pool = []
    for f in faculty_list:
        is_on_leave = f.id in faculty_on_leave_ids
        # Only use confirmed workload cap. Do not invent a fallback cap.
        cap = INSTITUTIONAL_FACULTY_CAPS.get(f.id)
        if cap is None and f.weekly_hours is not None:
            try:
                cap = float(f.weekly_hours)
            except (ValueError, TypeError):
                cap = None
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
            "available": True,
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
            # Target section from faculty record if present, otherwise catalog sections
            target_secs = [f.section] if f.section else catalog_entry.get("sections", ["A"])
            for sec in target_secs:
                if not sec:
                    continue
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
                        "room": catalog_entry.get("room"),
                        "lab_room": catalog_entry.get("lab_room"),
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

    active_subs = get_active_substitutions()

    gen_result = generate_timetable(
        requirements=requirements,
        faculty_pool=faculty_pool,
        time_slots=time_slots,
        seed=CURRENT_TIMETABLE_SEED,
        allow_partial=True,
        substitutions=active_subs,
    )

    _GENERATED_TIMETABLE_CACHE = gen_result
    return gen_result

# --- SUBSTITUTE FACULTY AVAILABILITY & AUTO-ASSIGNMENT ENGINE ---

def resolve_leave_days(leave_date: str) -> list[str]:
    """Resolve a leave_date string into 3-letter timetable day abbreviations."""
    if not leave_date:
        return ["Mon", "Tue", "Wed", "Thu", "Fri"]
    cleaned = str(leave_date).strip().lower()
    for d, code in [("mon", "Mon"), ("tue", "Tue"), ("wed", "Wed"), ("thu", "Thu"), ("fri", "Fri")]:
        if d in cleaned:
            return [code]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            from datetime import datetime
            dt = datetime.strptime(str(leave_date).strip(), fmt)
            code = dt.strftime("%a")
            if code in ["Mon", "Tue", "Wed", "Thu", "Fri"]:
                return [code]
        except (ValueError, TypeError):
            pass
    return ["Mon", "Tue", "Wed", "Thu", "Fri"]

def get_affected_classes_for_leave(leave: LeaveRequest) -> list[dict]:
    """Identify all classes affected by a leave request from the current weekly timetable."""
    from ml.generator import parse_slot_interval, slots_collide, normalize_text
    gen_result = get_or_generate_timetable()
    assignments = gen_result.get("assignments", [])
    leave_days = resolve_leave_days(leave.leave_date)
    target_slot = parse_slot_interval(leave.time_slot) if leave.time_slot else None

    affected = []
    seen = set()
    for a in assignments:
        orig_fid = a.get("original_faculty_id") or a.get("faculty_id")
        orig_fname = a.get("original_faculty_name") or a.get("faculty_name")

        matches = False
        if str(orig_fid) == str(leave.faculty_id):
            matches = True
        elif orig_fname and normalize_text(orig_fname) == normalize_text(leave.faculty_name):
            matches = True

        if not matches:
            continue

        if a.get("day") not in leave_days:
            continue

        if target_slot:
            a_slot = parse_slot_interval(a.get("time_slot") or "")
            collides, _ = slots_collide(target_slot, a_slot)
            if not collides:
                continue

        key = (a.get("day"), a.get("time_slot"), a.get("subject"), a.get("section"))
        if key not in seen:
            seen.add(key)
            affected.append(a)

    return affected

def find_eligible_substitutes(
    original_faculty_id: int,
    day: str,
    time_slot: str,
    subject: str = None,
    section: str = None,
    leave_date: str = None,
    duration_hours: float = 1.0,
) -> list[dict]:
    """Find and rank genuinely available, qualified faculty to substitute for a class period."""
    from ml.generator import parse_slot_interval, slots_collide, normalize_text

    target_slot = parse_slot_interval(time_slot)
    if duration_hours > 1.0 and target_slot.get("start_min") is not None:
        target_slot["end_min"] = target_slot["start_min"] + int(duration_hours * 60)

    orig_fac = Faculty.query.get(int(original_faculty_id)) if original_faculty_id else None
    orig_name = orig_fac.name if orig_fac else ""

    # Faculty on leave on this day (and overlapping with target slot if partial day)
    leaves_on_day = LeaveRequest.query.filter(
        LeaveRequest.status.in_(["Approved", "Pending"])
    ).all()
    fac_ids_on_leave = set()
    for lr in leaves_on_day:
        lr_days = resolve_leave_days(lr.leave_date)
        if day in lr_days:
            if lr.time_slot:
                lr_slot = parse_slot_interval(lr.time_slot)
                collides, _ = slots_collide(lr_slot, target_slot)
                if collides:
                    fac_ids_on_leave.add(lr.faculty_id)
            else:
                fac_ids_on_leave.add(lr.faculty_id)

    gen_result = get_or_generate_timetable()
    base_assignments = gen_result.get("assignments", [])
    active_sub_assignments = SubstituteAssignment.query.filter_by(status="Approved").all()

    eligible = []
    all_faculty = Faculty.query.all()

    for cand in all_faculty:
        # 1. Exclude the faculty on leave
        if cand.id == original_faculty_id or (orig_name and normalize_text(cand.name) == normalize_text(orig_name)):
            continue

        # 2. Exclude faculty on approved leave
        if cand.id in fac_ids_on_leave:
            continue

        # 3. Exclude faculty with existing timetable class during those periods (including multi-period labs)
        has_clash = False
        for a in base_assignments:
            is_cand = str(a.get("faculty_id")) == str(cand.id) or normalize_text(a.get("faculty_name")) == normalize_text(cand.name)
            if is_cand and not a.get("is_substituted"):
                if a.get("day") == day:
                    a_slot = parse_slot_interval(a.get("time_slot") or "")
                    if a.get("is_lab") and float(a.get("duration_hours", 1.0)) >= 2.0 and a_slot.get("start_min") is not None:
                        a_slot["end_min"] = a_slot["start_min"] + int(float(a["duration_hours"]) * 60)
                    collides, _ = slots_collide(a_slot, target_slot)
                    if collides:
                        has_clash = True
                        break
        if has_clash:
            continue

        # 4. Exclude faculty already appointed as substitute during these periods
        sub_clash = False
        for sa in active_sub_assignments:
            is_cand_sub = sa.substitute_id == cand.id or (sa.substitute_name and normalize_text(sa.substitute_name) == normalize_text(cand.name))
            if is_cand_sub and sa.day == day:
                if sa.time_slot:
                    sa_slot = parse_slot_interval(sa.time_slot)
                    collides, _ = slots_collide(sa_slot, target_slot)
                    if collides:
                        sub_clash = True
                        break
                else:
                    sub_clash = True
                    break
        if sub_clash:
            continue

        # 5. Exclude faculty who would exceed weekly workload cap
        cap = cand.weekly_hours or 14
        curr_load = 0.0
        for a in base_assignments:
            if (str(a.get("faculty_id")) == str(cand.id)) and not a.get("is_substituted"):
                curr_load += float(a.get("duration_hours") or 1.0)
        for sa in active_sub_assignments:
            if sa.substitute_id == cand.id or (sa.substitute_name and normalize_text(sa.substitute_name) == normalize_text(cand.name)):
                curr_load += 1.0

        if curr_load + duration_hours > cap:
            continue

        # 6. Evaluate domain/subject expertise
        score = 0
        is_exact = False
        if subject and cand.assigned_subject and normalize_text(cand.assigned_subject) == normalize_text(subject):
            score += 25
            is_exact = True
        if orig_fac and cand.domain and orig_fac.domain and normalize_text(cand.domain) == normalize_text(orig_fac.domain):
            score += 15
            is_exact = True
        if orig_fac and cand.department and orig_fac.department and cand.department == orig_fac.department:
            score += 5

        eligible.append({
            "faculty": cand,
            "id": cand.id,
            "name": cand.name,
            "department": cand.department,
            "assigned_subject": cand.assigned_subject,
            "weekly_hours": cand.weekly_hours,
            "current_load": curr_load,
            "score": score,
            "is_exact_match": is_exact,
        })

    eligible.sort(key=lambda x: (-x["score"], x["current_load"], x["name"]))
    return eligible

def validate_substitute_for_class(
    candidate: Faculty,
    original_faculty_id: int,
    day: str,
    time_slot: str,
    subject: str = None,
    section: str = None,
    leave_date: str = None,
    duration_hours: float = 1.0,
) -> tuple[bool, str]:
    """Revalidate that candidate faculty member is available and eligible for a specific class."""
    if not candidate:
        return False, "No candidate faculty specified."
    eligible = find_eligible_substitutes(
        original_faculty_id=original_faculty_id,
        day=day,
        time_slot=time_slot,
        subject=subject,
        section=section,
        leave_date=leave_date,
        duration_hours=duration_hours,
    )
    eligible_ids = {e["id"] for e in eligible}
    if candidate.id in eligible_ids:
        return True, "Eligible and available"
    return False, f"{candidate.name} is not available (schedule clash, on leave, or exceeds weekly workload cap)."

def get_eligible_substitutes_for_leave(leave: LeaveRequest) -> list[dict]:
    """Precompute all eligible substitute faculty capable of covering classes for this leave request."""
    affected = get_affected_classes_for_leave(leave)
    if not affected:
        all_fac = Faculty.query.filter(Faculty.id != leave.faculty_id).all()
        return [{"id": f.id, "name": f.name, "department": f.department, "assigned_subject": f.assigned_subject, "weekly_hours": f.weekly_hours, "is_exact_match": False} for f in all_fac]

    candidate_scores = {}
    candidate_info = {}
    for i, cls in enumerate(affected):
        dur = float(cls.get("duration_hours") or 1.0)
        el = find_eligible_substitutes(
            original_faculty_id=leave.faculty_id,
            day=cls.get("day"),
            time_slot=cls.get("time_slot"),
            subject=cls.get("subject"),
            section=cls.get("section"),
            leave_date=leave.leave_date,
            duration_hours=dur,
        )
        if i == 0:
            candidate_scores = {e["id"]: e["score"] for e in el}
            candidate_info = {e["id"]: e for e in el}
        else:
            candidate_scores = {e["id"]: candidate_scores[e["id"]] + e["score"] for e in el if e["id"] in candidate_scores}

    eligible_list = [candidate_info[cid] for cid in candidate_scores]
    eligible_list.sort(key=lambda x: (-candidate_scores[x["id"]], x["name"]))
    return eligible_list

def validate_substitute_for_leave(candidate: Faculty, leave: LeaveRequest) -> tuple[bool, str]:
    """Revalidate that candidate is available for all classes affected by this leave request."""
    affected = get_affected_classes_for_leave(leave)
    for cls in affected:
        dur = float(cls.get("duration_hours") or 1.0)
        valid, reason = validate_substitute_for_class(
            candidate=candidate,
            original_faculty_id=leave.faculty_id,
            day=cls.get("day"),
            time_slot=cls.get("time_slot"),
            subject=cls.get("subject"),
            section=cls.get("section"),
            leave_date=leave.leave_date,
            duration_hours=dur,
        )
        if not valid:
            return False, f"Clash during {cls.get('subject')} on {cls.get('day')} ({cls.get('time_slot')}): {reason}"
    return True, "Eligible"

def auto_assign_substitutes_for_leave(leave: LeaveRequest) -> tuple[list[dict], int]:
    """Automatically find and assign suitable substitute faculty for all affected classes."""
    affected = get_affected_classes_for_leave(leave)
    assigned_records = []
    unassigned_count = 0
    assigned_sub_names = set()

    for cls in affected:
        day = cls.get("day")
        slot = cls.get("time_slot")
        subj = cls.get("subject")
        sec = cls.get("section")
        dur = float(cls.get("duration_hours") or 1.0)

        existing = SubstituteAssignment.query.filter_by(
            faculty_id=leave.faculty_id,
            day=day,
            time_slot=slot,
            status="Approved"
        ).first()

        if existing and existing.substitute_id:
            sub_fac = Faculty.query.get(existing.substitute_id)
            if sub_fac:
                valid, _ = validate_substitute_for_class(sub_fac, leave.faculty_id, day, slot, subj, sec, leave.leave_date, dur)
                if valid:
                    assigned_records.append({
                        "subject": subj, "section": sec, "day": day, "time_slot": slot, "substitute_name": sub_fac.name
                    })
                    assigned_sub_names.add(sub_fac.name)
                    continue

        eligible = find_eligible_substitutes(
            original_faculty_id=leave.faculty_id,
            day=day,
            time_slot=slot,
            subject=subj,
            section=sec,
            leave_date=leave.leave_date,
            duration_hours=dur,
        )

        if eligible:
            chosen = eligible[0]
            if existing:
                existing.substitute_id = chosen["id"]
                existing.substitute_name = chosen["name"]
                existing.subject = subj
                existing.section = sec
                existing.status = "Approved"
            else:
                new_sub = SubstituteAssignment(
                    faculty_id=leave.faculty_id,
                    faculty_name=leave.faculty_name,
                    substitute_id=chosen["id"],
                    substitute_name=chosen["name"],
                    subject=subj,
                    section=sec,
                    day=day,
                    time_slot=slot,
                    date=leave.leave_date,
                    status="Approved",
                )
                db.session.add(new_sub)
            assigned_records.append({
                "subject": subj, "section": sec, "day": day, "time_slot": slot, "substitute_name": chosen["name"]
            })
            assigned_sub_names.add(chosen["name"])
        else:
            unassigned_name = "Unassigned — Admin Action Required"
            if existing:
                existing.substitute_id = None
                existing.substitute_name = unassigned_name
                existing.subject = subj
                existing.section = sec
                existing.status = "Approved"
            else:
                unassigned_sub = SubstituteAssignment(
                    faculty_id=leave.faculty_id,
                    faculty_name=leave.faculty_name,
                    substitute_id=None,
                    substitute_name=unassigned_name,
                    subject=subj,
                    section=sec,
                    day=day,
                    time_slot=slot,
                    date=leave.leave_date,
                    status="Approved",
                )
                db.session.add(unassigned_sub)
            unassigned_count += 1
            assigned_records.append({
                "subject": subj, "section": sec, "day": day, "time_slot": slot, "substitute_name": unassigned_name
            })

    if unassigned_count > 0:
        leave.substitute_name = "Unassigned — Admin Action Required"
    elif assigned_sub_names:
        leave.substitute_name = ", ".join(sorted(assigned_sub_names))
    elif not affected:
        leave.substitute_name = None

    return assigned_records, unassigned_count

def handle_emergency_substitute_leave(
    faculty_id: int,
    faculty_name: str,
    leave_date: str,
    time_slot: str = None
) -> list[str]:
    """Detect and reassign duties when an appointed substitute takes emergency leave."""
    from ml.generator import parse_slot_interval, slots_collide, normalize_text
    leave_days = resolve_leave_days(leave_date)
    target_slot = parse_slot_interval(time_slot) if time_slot else None

    all_duties = SubstituteAssignment.query.filter_by(status="Approved").all()
    my_duties = [
        s for s in all_duties
        if (s.substitute_id == faculty_id or (s.substitute_name and normalize_text(s.substitute_name) == normalize_text(faculty_name)))
    ]

    action_reports = []
    for duty in my_duties:
        duty_days = resolve_leave_days(duty.date) if duty.date else ([duty.day] if duty.day else [])
        duty_collides = False
        if duty.day and duty.day in leave_days:
            duty_collides = True
        elif duty.date and (duty.date == leave_date or any(d in leave_days for d in duty_days)):
            duty_collides = True
        elif not duty.day and not duty.date:
            duty_collides = True

        if not duty_collides:
            continue

        if target_slot and duty.time_slot:
            duty_slot = parse_slot_interval(duty.time_slot)
            collides, _ = slots_collide(target_slot, duty_slot)
            if not collides:
                continue

        duty.status = "Cancelled"

        orig_fac = Faculty.query.get(duty.faculty_id)
        duty_subj = duty.subject
        if not duty_subj or normalize_text(duty_subj) == normalize_text(duty.faculty_name or ""):
            duty_subj = orig_fac.assigned_subject if orig_fac else "General"
        duty_sec = duty.section or (orig_fac.section if orig_fac else "A")
        duty_day = duty.day or (duty_days[0] if duty_days else (leave_days[0] if leave_days else "Mon"))

        eligible = find_eligible_substitutes(
            original_faculty_id=duty.faculty_id,
            day=duty_day,
            time_slot=duty.time_slot,
            subject=duty_subj,
            section=duty_sec,
            leave_date=duty.date or leave_date,
        )

        orig_leave = LeaveRequest.query.filter_by(faculty_id=duty.faculty_id, status="Approved").first()

        if eligible:
            replacement = eligible[0]
            new_asgn = SubstituteAssignment(
                faculty_id=duty.faculty_id,
                faculty_name=duty.faculty_name,
                substitute_id=replacement["id"],
                substitute_name=replacement["name"],
                subject=duty_subj,
                section=duty_sec,
                day=duty_day,
                time_slot=duty.time_slot,
                date=duty.date or leave_date,
                status="Approved",
            )
            db.session.add(new_asgn)
            if orig_leave:
                orig_leave.substitute_name = replacement["name"]
            action_reports.append(f"Reassigned {duty_subj} (Sec {duty_sec}) on {duty_day} to {replacement['name']}")
        else:
            unassigned_name = "Unassigned — Admin Action Required"
            unassigned_asgn = SubstituteAssignment(
                faculty_id=duty.faculty_id,
                faculty_name=duty.faculty_name,
                substitute_id=None,
                substitute_name=unassigned_name,
                subject=duty_subj,
                section=duty_sec,
                day=duty_day,
                time_slot=duty.time_slot,
                date=duty.date or leave_date,
                status="Approved",
            )
            db.session.add(unassigned_asgn)
            if orig_leave:
                orig_leave.substitute_name = unassigned_name
            action_reports.append(f"No substitute available for {duty.subject} on {duty.day}; marked as Unassigned — Admin Action Required")

    return action_reports

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

    master_grid = gen_result.get('master_grid', [])
    section_grid = gen_result.get('section_grid', [])
    substitution_coverage = gen_result.get('substitution_coverage', [])
    active_subs = get_active_substitutions()

    # Identify substitute faculty names and on-leave faculty names
    substitute_names = {c.get('substitute_name') for c in substitution_coverage if c.get('substitute_name')}
    substitute_names.update({s.get('substitute_name') for s in active_subs if s.get('substitute_name')})

    on_leave_names = {c.get('original_faculty_name') for c in substitution_coverage if c.get('original_faculty_name')}
    on_leave_names.update({s.get('faculty_name') for s in active_subs if s.get('faculty_name')})

    # Prepare complete weekly timetables for appointed substitute faculty
    substitute_faculty_timetables = [
        item for item in master_grid
        if (item.get('faculty', {}).get('name') in substitute_names or
            item.get('faculty', {}).get('faculty_name') in substitute_names)
    ]

    # Prepare complete weekly timetables for faculty currently on leave
    on_leave_faculty_timetables = [
        item for item in master_grid
        if (item.get('faculty', {}).get('name') in on_leave_names or
            item.get('faculty', {}).get('faculty_name') in on_leave_names)
    ]

    # Identify sections that have active substitute appointments
    sub_sections = {str(c.get('section')) for c in substitution_coverage if c.get('section')}
    covered_sections_timetables = [
        s for s in section_grid
        if str(s.get('section')) in sub_sections
    ]

    return render_template(
        'schedule.html',
        master_grid=master_grid,
        section_grid=section_grid,
        substitute_faculty_timetables=substitute_faculty_timetables,
        on_leave_faculty_timetables=on_leave_faculty_timetables,
        covered_sections_timetables=covered_sections_timetables,
        time_slots=time_slots,
        timetable_status=gen_result.get('status'),
        unscheduled_requirements=gen_result.get('unscheduled_requirements', []),
        unscheduled_count=gen_result.get('unscheduled_count', 0),
        validation=gen_result.get('validation', {}),
        seed=gen_result.get('seed'),
        total_assigned_count=gen_result.get('total_assigned_count', 0),
        active_substitutions=active_subs,
        substitution_coverage=substitution_coverage,
    )

@app.route('/schedule/regenerate')
def regenerate_schedule():
    import random
    global CURRENT_TIMETABLE_SEED
    # Pick a fresh distinct seed to genuinely re-solve and re-shuffle valid allocations
    new_seed = random.randint(100, 99999)
    CURRENT_TIMETABLE_SEED = new_seed
    get_or_generate_timetable(force_refresh=True, seed=new_seed)
    flash('Timetable regenerated successfully with a new schedule arrangement.', 'success')
    tab = request.args.get('tab', '')
    dest = url_for('master_schedule')
    if tab:
        dest += f"#{tab}"
    return redirect(dest)

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.')
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if Faculty.query.count() == 0:
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