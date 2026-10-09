from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SECRET_KEY'] = 'pfas-secret-key-123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///pfas.db'
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

    weekly_schedule = [
        {"time": "09:00 AM - 10:00 AM", "Mon": (faculty_member.assigned_subject or 'Class') + " (Sec " + (faculty_member.section or 'A') + ")", "Tue": "Lab Session", "Wed": faculty_member.assigned_subject or 'Class', "Thu": "Free", "Fri": faculty_member.assigned_subject or 'Class'},
        {"time": "10:00 AM - 11:00 AM", "Mon": "Free", "Tue": faculty_member.assigned_subject or 'Class', "Wed": "Free", "Thu": faculty_member.assigned_subject or 'Class', "Fri": "Lab Session"},
        {"time": "11:15 AM - 12:15 PM", "Mon": faculty_member.assigned_subject or 'Class', "Tue": "Free", "Wed": faculty_member.assigned_subject or 'Class', "Thu": "Free", "Fri": "Free"},
        {"time": "01:15 PM - 02:15 PM", "Mon": "Departmental Meeting", "Tue": "Free", "Wed": "Lab Session", "Thu": faculty_member.assigned_subject or 'Class', "Fri": "Free"},
        {"time": "02:15 PM - 03:15 PM", "Mon": "Free", "Tue": faculty_member.assigned_subject or 'Class', "Wed": "Free", "Thu": "Free", "Fri": faculty_member.assigned_subject or 'Class'},
    ]

    return render_template(
        'faculty.html',
        faculty=faculty_member,
        schedule=weekly_schedule,
        all_faculty=all_faculty,
        leaves=my_leaves,
        preference=my_preference
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
# 5. MASTER TIMETABLE / SCHEDULE (All Faculty Combined)
@app.route('/schedule')
def master_schedule():
    faculty_list = Faculty.query.all()
    
    time_slots = [
        "09:00 AM - 10:00 AM",
        "10:00 AM - 11:00 AM",
        "11:15 AM - 12:15 PM",
        "01:15 PM - 02:15 PM",
        "02:15 PM - 03:15 PM"
    ]
    
    # Build consolidated schedule grid per faculty
    master_grid = []
    for f in faculty_list:
        subj = f.assigned_subject or "Class"
        sec = f.section or "A"
        master_grid.append({
            "faculty": f,
            "slots": [
                {"time": "09:00 AM - 10:00 AM", "Mon": f"{subj} ({sec})", "Tue": "Lab", "Wed": subj, "Thu": "Free", "Fri": subj},
                {"time": "10:00 AM - 11:00 AM", "Mon": "Free", "Tue": subj, "Wed": "Free", "Thu": subj, "Fri": "Lab"},
                {"time": "11:15 AM - 12:15 PM", "Mon": subj, "Tue": "Free", "Wed": subj, "Thu": "Free", "Fri": "Free"},
                {"time": "01:15 PM - 02:15 PM", "Mon": "Meeting", "Tue": "Free", "Wed": "Lab", "Thu": subj, "Fri": "Free"},
                {"time": "02:15 PM - 03:15 PM", "Mon": "Free", "Tue": subj, "Wed": "Free", "Thu": "Free", "Fri": subj},
            ]
        })

    return render_template('schedule.html', master_grid=master_grid, time_slots=time_slots)

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