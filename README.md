# PFAS (Predictive Faculty Allocation System)

A simple guide to run and use the project locally on your machine.

---

## Prerequisites
Make sure you have **Python** installed.

---

## Option 1: Running with a Virtual Environment (Recommended)

Using a virtual environment keeps packages isolated.

### 1. Create a virtual environment
```bash
# On Linux / macOS:
python3 -m venv venv

# On Windows:
python -m venv venv
```

### 2. Activate it
```bash
# On Linux / macOS:
source venv/bin/activate

# On Windows (Command Prompt):
venv\Scripts\activate

# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
```

### 3. Install dependencies
```bash
pip install flask flask-sqlalchemy pandas numpy scikit-learn joblib
```

### 4. Start the application
```bash
python app.py
```

### 5. Open in browser
Open your browser and navigate to:
**http://127.0.0.1:5000**

---

## Option 2: Running directly without a Virtual Environment

If you don't want to create a virtual environment, you can install the packages directly:

### 1. Install dependencies
```bash
# On Linux / macOS:
pip3 install flask flask-sqlalchemy pandas numpy scikit-learn joblib

# On Windows:
pip install flask flask-sqlalchemy pandas numpy scikit-learn joblib
```

### 2. Start the application
```bash
# On Linux / macOS:
python3 app.py

# On Windows:
python app.py
```

### 3. Open in browser
Open your browser and navigate to:
**http://127.0.0.1:5000**

---

## How to Use the App
- **Master Timetable**: Visit `http://127.0.0.1:5000/schedule` to see the generated, conflict-free timetable for all faculty and sections. Click **"Regenerate Timetable"** if you want to rerun the solver.
- **Admin Dashboard**: Visit `http://127.0.0.1:5000/admin` to view, add, or edit faculty records and review leave requests.
- **Faculty Dashboard**: Log in with any registered faculty email (e.g., `sharma@icfaitech.in`) to see your personal timetable, apply for leave, or submit teaching preferences.

---

## Running the Tests
To verify everything is working properly:
```bash
python -m unittest discover -v -s ml/tests
```
