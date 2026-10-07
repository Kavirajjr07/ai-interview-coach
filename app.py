import os
import sqlite3
import json
import re
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import requests

# PDF parsing
from pypdf import PdfReader
# Word parsing
try:
    import docx
except ImportError:
    docx = None

# ReportLab imports for PDF generation
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'interview_ai_secret_key_12984719284')
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.template_filter('json_loads_safe')
def json_loads_safe(value):
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        if isinstance(value, list):
            return value
        return [value]

DB_PATH = 'database.db'

# ---------------------------------------------------------
# DATABASE INITIALIZATION
# ---------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        # Users Table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS Users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                profile_pic TEXT,
                skills TEXT,
                target_role TEXT,
                xp INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                last_activity_date TEXT,
                is_admin INTEGER DEFAULT 0
            )
        ''')
        
        # Interviews Table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS Interviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                role TEXT NOT NULL,
                company TEXT NOT NULL,
                experience TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                score_communication INTEGER,
                score_technical INTEGER,
                score_confidence INTEGER,
                score_problem_solving INTEGER,
                score_professionalism INTEGER,
                overall_score INTEGER,
                current_question_index INTEGER DEFAULT 0,
                questions_list TEXT,
                answers_list TEXT,
                FOREIGN KEY(user_id) REFERENCES Users(id)
            )
        ''')
        
        # Reports Table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS Reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interview_id INTEGER UNIQUE NOT NULL,
                transcript TEXT NOT NULL,
                feedback_strengths TEXT,
                feedback_weaknesses TEXT,
                recommendations TEXT,
                learning_resources TEXT,
                FOREIGN KEY(interview_id) REFERENCES Interviews(id)
            )
        ''')
        
        # ResumeAnalysis Table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ResumeAnalysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                ats_score INTEGER NOT NULL,
                missing_skills TEXT,
                missing_keywords TEXT,
                formatting_issues TEXT,
                grammar_suggestions TEXT,
                recommendations TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES Users(id)
            )
        ''')
        
        # ActivityLogs Table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ActivityLogs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                activity_type TEXT NOT NULL,
                description TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES Users(id)
            )
        ''')
        
        # Seed default admin if not existing
        admin_email = 'admin@interview.ai'
        cursor = conn.execute('SELECT * FROM Users WHERE email = ?', (admin_email,))
        if not cursor.fetchone():
            hashed_pass = generate_password_hash('Admin@123')
            conn.execute('''
                INSERT INTO Users (name, email, password, is_admin, skills, target_role, profile_pic)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', ('Platform Admin', admin_email, hashed_pass, 1, 'Systems, Databases, AI', 'System Administrator', 'admin_avatar.png'))
            conn.commit()

init_db()

# ---------------------------------------------------------
# SECURITY DECORATORS & HELPERS
# ---------------------------------------------------------
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('is_admin') != 1:
            flash('Admin authorization required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def log_activity(user_id, activity_type, description):
    try:
        with get_db() as conn:
            conn.execute('''
                INSERT INTO ActivityLogs (user_id, activity_type, description, timestamp)
                VALUES (?, ?, ?, ?)
            ''', (user_id, activity_type, description, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            
            # Update XP and Streak
            user = conn.execute('SELECT * FROM Users WHERE id = ?', (user_id,)).fetchone()
            if user:
                xp_gain = 0
                if activity_type == 'login':
                    xp_gain = 10
                elif activity_type == 'interview_completed':
                    xp_gain = 100
                elif activity_type == 'resume_uploaded':
                    xp_gain = 50
                elif activity_type == 'career_chat':
                    xp_gain = 20

                new_xp = user['xp'] + xp_gain
                
                # Check streak
                today_str = datetime.now().strftime('%Y-%m-%d')
                last_active = user['last_activity_date']
                streak = user['streak']

                if last_active:
                    last_active_dt = datetime.strptime(last_active, '%Y-%m-%d')
                    today_dt = datetime.strptime(today_str, '%Y-%m-%d')
                    delta = (today_dt - last_active_dt).days
                    if delta == 1:
                        streak += 1
                    elif delta > 1:
                        streak = 1
                else:
                    streak = 1

                conn.execute('''
                    UPDATE Users 
                    SET xp = ?, streak = ?, last_activity_date = ? 
                    WHERE id = ?
                ''', (new_xp, streak, today_str, user_id))
            conn.commit()
    except Exception as e:
        print(f"Error logging activity: {e}")

# ---------------------------------------------------------
# AI SERVICE (GROQ API & MOCK ENGINE)
# ---------------------------------------------------------
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

def query_ai(system_prompt, user_prompt, max_tokens=1000, temperature=0.7):
    """
    Sends request to Groq API.
    If GROQ_API_KEY is not defined or the request fails, triggers Mock AI fallback.
    """
    if not GROQ_API_KEY:
        return query_mock_ai(system_prompt, user_prompt)
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama3-8b-8192",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content'].strip()
        else:
            print(f"Groq API returned error {response.status_code}: {response.text}")
            return query_mock_ai(system_prompt, user_prompt)
    except Exception as e:
        print(f"Failed to query Groq API, using mock engine. Error: {e}")
        return query_mock_ai(system_prompt, user_prompt)

def query_mock_ai(system_prompt, user_prompt):
    """
    High-fidelity deterministic Mock AI response generator.
    Parses prompts to simulate correct response formatting.
    """
    # 1. GENERATING INTERVIEW QUESTIONS LIST (expects a JSON array of questions)
    if "Generate 5 structured interview questions" in system_prompt or "Generate an array of 5 questions" in user_prompt:
        role = "Software Engineer"
        for r in ["Full Stack Developer", "Data Analyst", "AI Engineer", "DevOps Engineer", "UI/UX Designer", "Software Engineer"]:
            if r.lower() in user_prompt.lower():
                role = r
                break
        
        type_int = "Technical"
        for t in ["hr", "technical", "behavioral", "coding"]:
            if t.lower() in user_prompt.lower():
                type_int = t.capitalize()
                break

        if type_int == "Coding":
            questions = [
                "Implement a function to reverse a linked list and explain its time complexity.",
                "Given an array of integers, find the maximum subarray sum (Kadane's Algorithm).",
                "Explain the difference between a hash map and a binary search tree, and design an autocomplete prefix search.",
                "Write a function to check if a binary tree is balanced. What is a balanced tree?",
                "Design a rate limiter class that controls the rate of requests sent over a window."
            ]
        elif type_int == "Behavioral":
            questions = [
                "Describe a time when you faced a technical conflict in a team. How did you resolve it?",
                "Tell me about a project that failed. What did you learn and how did you pivot?",
                "Describe a situation where you had to work under tight constraints. How did you prioritize tasks?",
                "Tell me about a time you went above and beyond for a customer or a stakeholder.",
                "How do you handle receiving negative feedback about your implementation from a team lead?"
            ]
        elif type_int == "Hr":
            questions = [
                "Tell me about yourself and why you are interested in this position.",
                "What are your greatest professional strengths and key areas of weakness?",
                "Where do you see yourself in five years? How does this role align with your career map?",
                "Why do you want to work at this company specifically?",
                "What are your salary expectations and availability for onboarding?"
            ]
        else: # Technical
            questions = [
                f"What are the core pillars of object-oriented programming, and how do they apply to {role} roles?",
                "Explain the MVC architecture and how state management is handled in clean applications.",
                "How do you optimize SQL query execution plans in databases under heavy traffic?",
                "What is Docker, and why is containerization useful in modern CI/CD setups?",
                "Explain the difference between REST API and GraphQL. When would you use which?"
            ]
        return json.dumps(questions)

    # 2. EVALUATING COMPLETE INTERVIEW TRANSCRIPT (expects JSON output)
    if "Generate Final Score" in system_prompt or "Overall Score" in system_prompt or "analyze the transcript" in user_prompt.lower():
        scores = {
            "Communication": 82,
            "Technical Knowledge": 78,
            "Confidence": 85,
            "Problem Solving": 80,
            "Professionalism": 88,
            "Overall Score": 83
        }
        
        # Simple dynamic adjustments based on answers length
        if len(user_prompt) > 800:
            scores = {k: v + 4 for k, v in scores.items()}
        elif len(user_prompt) < 300:
            scores = {k: v - 10 for k, v in scores.items()}

        mock_report = {
            "scores": scores,
            "strengths": "Demonstrates excellent articulation, solid theoretical foundation on architecture patterns, and answers structural questions with ease.",
            "weaknesses": "Could provide more detailed real-world project scenarios. In coding questions, edge-case validation was slightly delayed.",
            "recommendations": "Practice time-boxed algorithmic designs. Strengthen database normalization patterns and indexing protocols.",
            "learning_resources": [
                {"title": "System Design Primer - Github", "url": "https://github.com/donnemartin/system-design-primer"},
                {"title": "LeetCode Algorithmic Workouts", "url": "https://leetcode.com"},
                {"title": "Star Method for Behavioral Interviews", "url": "https://www.indeed.com/career-advice/interviewing/star-method"}
            ]
        }
        return json.dumps(mock_report)

    # 3. RESUME ANALYSIS (expects JSON output)
    if "ATS Score" in system_prompt or "ATS" in user_prompt:
        score = 72
        if "python" in user_prompt.lower() or "javascript" in user_prompt.lower():
            score += 5
        if "experience" in user_prompt.lower() or "project" in user_prompt.lower():
            score += 8
            
        mock_analysis = {
            "ats_score": min(score, 98),
            "missing_skills": ["Kubernetes", "Redis Caching", "Unit Testing (PyTest/Jest)", "CI/CD Pipeline Construction"],
            "missing_keywords": ["Scalability", "Agile Methodologies", "Cloud Architecture", "System Optimization"],
            "formatting_issues": [
                "Multi-column grids might confuse older ATS parsers. Suggest a clean single-column format.",
                "Ensure date formats are unified (e.g., 'YYYY-MM' or 'Month YYYY')."
            ],
            "grammar_suggestions": [
                "Use active voice action verbs instead of passive phrasing (e.g., change 'was responsible for managing' to 'Led management of').",
                "Ensure absolute consistency in punctuation at the end of bullet points."
            ],
            "recommendations": "Add a dedicated core skills section at the top of the page. Elaborate on the business impact of projects using quantitative metrics (e.g., 'reduced page load time by 30%')."
        }
        return json.dumps(mock_analysis)

    # 4. CODE REVIEW / EVALUATION (expects markdown with code blocks)
    if "Evaluate the following code" in system_prompt or "Language selector" in user_prompt or "Correctness, Logic, Time Complexity" in system_prompt:
        lang = "Python"
        for l in ["python", "java", "cpp", "javascript"]:
            if l in user_prompt.lower():
                lang = l.capitalize()
                break
        
        return """### Coding Evaluation Report ({lang})

* **Correctness**: **Pass** (10/10) - The submitted solution compiles cleanly and matches all functional constraints of the two-sum problem.
* **Logic**: **Excellent** (9/10) - Uses a single-pass hash mapping layout which is highly optimal and avoids nested lookup overheads.
* **Time Complexity**: **O(N)** - Linear execution time, where N is the size of the array.
* **Space Complexity**: **O(N)** - Auxiliary hash map storage.
* **Code Quality**: **High** - Variables are descriptive and layout styling follows standard industry guidelines.

#### Optimized Solution:
```python
def two_sum_optimized(nums, target):
    # Optimized single-pass lookup index
    lookup = {}
    for idx, val in enumerate(nums):
        diff = target - val
        if diff in lookup:
            return [lookup[diff], idx]
        lookup[val] = idx
    return []
```
#### Next Steps:
Try reviewing dynamic programming challenges or binary tree traversals to prepare for tougher technical coding rounds.
""".replace("{lang}", lang)

    # 5. CAREER COACH CHAT
    return """**InterviewAI Coach Advice:**

To accelerate your preparation, focus on these three core segments:
1. **Mock Training**: Complete at least 3 behavioral and 2 technical interviews targeting your dream role.
2. **Resume Matching**: Align your profile highlights with keywords commonly queried in job alerts.
3. **Core Coding Rules**: Master dynamic arrays, graphs, hash lookup structures, and review basic sorting algorithms.

What target role or specific certification roadmaps can I assist you with building today?
"""

# ---------------------------------------------------------
# AUTHENTICATION ROUTES
# ---------------------------------------------------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if not name or not email or not password or not confirm_password:
            flash('All inputs are required.', 'danger')
            return render_template('register.html')
            
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('register.html')
            
        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            return render_template('register.html')
            
        try:
            with get_db() as conn:
                hashed_pass = generate_password_hash(password)
                conn.execute('''
                    INSERT INTO Users (name, email, password, skills, target_role)
                    VALUES (?, ?, ?, ?, ?)
                ''', (name, email, hashed_pass, '', 'Software Engineer'))
                conn.commit()
                
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Email already registered.', 'danger')
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not email or not password:
            flash('Please enter both email and password.', 'danger')
            return render_template('login.html')
            
        with get_db() as conn:
            user = conn.execute('SELECT * FROM Users WHERE email = ?', (email,)).fetchone()
            
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['name'] = user['name']
            session['email'] = user['email']
            session['is_admin'] = user['is_admin']
            
            # Record Activity log (will update XP & streak)
            log_activity(user['id'], 'login', 'Logged into the platform')
            
            flash(f"Welcome back, {user['name']}!", 'success')
            
            if user['is_admin'] == 1:
                return redirect(url_for('admin'))
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password.', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('landing'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        if not email:
            flash('Please enter your email.', 'danger')
            return render_template('forgot_password.html')

        with get_db() as conn:
            user = conn.execute('SELECT * FROM Users WHERE email = ?', (email,)).fetchone()
            
        if user:
            # Generate simulated reset token
            token = f"reset_token_simulated_{user['id']}"
            session['reset_token'] = token
            session['reset_email'] = email
            
            # In production, send email. Here, we offer a direct simulated link on dashboard or flash.
            flash(f"Password reset link generated. Simulate access by clicking: Reset Link Below", 'warning')
            return render_template('forgot_password.html', reset_token=token)
        else:
            flash('Email not found in our database.', 'danger')
            
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if session.get('reset_token') != token:
        flash('Invalid or expired reset token.', 'danger')
        return redirect(url_for('forgot_password'))
        
    if request.method == 'POST':
        new_pass = request.form.get('password')
        confirm_pass = request.form.get('confirm_password')
        
        if not new_pass or new_pass != confirm_pass:
            flash('Passwords must match.', 'danger')
            return render_template('forgot_password.html', token=token, step='reset')
            
        email = session.get('reset_email')
        with get_db() as conn:
            hashed_pass = generate_password_hash(new_pass)
            conn.execute('UPDATE Users SET password = ? WHERE email = ?', (hashed_pass, email))
            conn.commit()
            
        session.pop('reset_token', None)
        session.pop('reset_email', None)
        flash('Password successfully reset! Please log in.', 'success')
        return redirect(url_for('login'))
        
    return render_template('forgot_password.html', token=token, step='reset')

# ---------------------------------------------------------
# PROFILE MANAGEMENT
# ---------------------------------------------------------
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user_id = session['user_id']
    with get_db() as conn:
        user = conn.execute('SELECT * FROM Users WHERE id = ?', (user_id,)).fetchone()
        
    if request.method == 'POST':
        name = request.form.get('name')
        skills = request.form.get('skills')
        target_role = request.form.get('target_role')
        
        profile_pic_file = request.files.get('profile_pic')
        profile_pic_filename = user['profile_pic']
        
        if profile_pic_file and profile_pic_file.filename != '':
            filename = secure_filename(profile_pic_file.filename)
            unique_fn = f"user_{user_id}_{filename}"
            profile_pic_file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_fn))
            profile_pic_filename = unique_fn

        with get_db() as conn:
            conn.execute('''
                UPDATE Users 
                SET name = ?, skills = ?, target_role = ?, profile_pic = ? 
                WHERE id = ?
            ''', (name, skills, target_role, profile_pic_filename, user_id))
            conn.commit()
            
        session['name'] = name
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))
        
    return render_template('profile.html', user=user)

# ---------------------------------------------------------
# LANDING PAGE
# ---------------------------------------------------------
@app.route('/')
def landing():
    return render_template('landing.html')

# ---------------------------------------------------------
# USER DASHBOARD
# ---------------------------------------------------------
@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    with get_db() as conn:
        user = conn.execute('SELECT * FROM Users WHERE id = ?', (user_id,)).fetchone()
        
        # Statistics
        interviews_cursor = conn.execute('SELECT * FROM Interviews WHERE user_id = ? AND status = "completed"', (user_id,))
        interviews = interviews_cursor.fetchall()
        total_interviews = len(interviews)
        
        # Calculate Average score
        avg_score = 0
        if total_interviews > 0:
            avg_score = int(sum([i['overall_score'] for i in interviews]) / total_interviews)
            
        # Calculate Average resume score
        resumes_cursor = conn.execute('SELECT * FROM ResumeAnalysis WHERE user_id = ?', (user_id,))
        resumes = resumes_cursor.fetchall()
        total_resumes = len(resumes)
        avg_resume_score = 0
        if total_resumes > 0:
            avg_resume_score = int(sum([r['ats_score'] for r in resumes]) / total_resumes)
            
        # Skills improved counts
        skills_improved = len([s for s in user['skills'].split(',') if s.strip()]) if user['skills'] else 0
        
        # Achievements tracking
        achievements = [
            {"id": "first_interview", "title": "First Interview", "desc": "Completed your first AI mock interview", "icon": "🏆", "unlocked": total_interviews >= 1},
            {"id": "top_performer", "title": "Top Performer", "desc": "Achieved overall score > 85% in an interview", "icon": "⚡", "unlocked": any([i['overall_score'] > 85 for i in interviews])},
            {"id": "streak_7", "title": "7 Day Streak", "desc": "Maintained an active preparation streak", "icon": "🔥", "unlocked": user['streak'] >= 7},
            {"id": "interview_master", "title": "Interview Master", "desc": "Successfully completed 5+ interviews", "icon": "👑", "unlocked": total_interviews >= 5}
        ]
        
        # Activity Logs
        logs = conn.execute('SELECT * FROM ActivityLogs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 6', (user_id,)).fetchall()
        
        # Charts Data - Mocking weekly metrics
        weekly_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        weekly_data = [0, 0, 0, 0, 0, 0, 0]
        
        today = datetime.now()
        for log in logs:
            try:
                log_dt = datetime.strptime(log['timestamp'], '%Y-%m-%d %H:%M:%S')
                delta_days = (today - log_dt).days
                if delta_days < 7:
                    weekday = log_dt.weekday() # 0 is Monday
                    weekly_data[weekday] += 1
            except Exception:
                pass
                
        # History
        history = conn.execute('''
            SELECT id, type, role, company, overall_score, created_at 
            FROM Interviews 
            WHERE user_id = ? AND status = "completed" 
            ORDER BY created_at DESC 
            LIMIT 5
        ''', (user_id,)).fetchall()

    return render_template('dashboard.html', 
                           user=user, 
                           total_interviews=total_interviews,
                           avg_score=avg_score,
                           avg_resume_score=avg_resume_score,
                           skills_improved=skills_improved,
                           achievements=achievements,
                           activity_logs=logs,
                           weekly_labels=weekly_labels,
                           weekly_data=weekly_data,
                           history=history)

# ---------------------------------------------------------
# AI MOCK INTERVIEW SYSTEM
# ---------------------------------------------------------
@app.route('/interview', methods=['GET', 'POST'])
@login_required
def interview_setup():
    if request.method == 'POST':
        user_id = session['user_id']
        int_type = request.form.get('type')
        role = request.form.get('role')
        company = request.form.get('company')
        experience = request.form.get('experience')
        
        # 1. Ask AI to generate 5 structured questions
        system_prompt = "You are a professional hiring manager. Generate 5 structured interview questions in JSON array format."
        user_prompt = f"Generate an array of 5 questions for a {experience} candidate interviewing for a {role} position at {company}. The interview type is {int_type}. Respond ONLY with a valid JSON array of strings containing the questions."
        
        ai_reply = query_ai(system_prompt, user_prompt)
        try:
            questions = json.loads(ai_reply)
            if not isinstance(questions, list) or len(questions) == 0:
                raise ValueError("Not a valid list")
        except Exception as e:
            print(f"Failed to parse questions array: {e}. Active template list loaded instead.")
            questions = json.loads(query_mock_ai(system_prompt, user_prompt))

        # 2. Insert interview record
        with get_db() as conn:
            cursor = conn.execute('''
                INSERT INTO Interviews 
                (user_id, type, role, company, experience, created_at, status, current_question_index, questions_list, answers_list)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, int_type, role, company, experience, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'in_progress', 0, json.dumps(questions), json.dumps([])))
            interview_id = cursor.lastrowid
            conn.commit()
            
        log_activity(user_id, 'interview_started', f"Started {int_type} interview for {role} at {company}")
        return redirect(url_for('interview_session', id=interview_id))
        
    return render_template('interview.html', setup=True)

@app.route('/interview/<int:id>')
@login_required
def interview_session(id):
    user_id = session['user_id']
    with get_db() as conn:
        interview = conn.execute('SELECT * FROM Interviews WHERE id = ? AND user_id = ?', (id, user_id)).fetchone()
        
    if not interview:
        flash('Interview session not found.', 'danger')
        return redirect(url_for('dashboard'))
        
    if interview['status'] == 'completed':
        return redirect(url_for('interview_report', id=id))
        
    questions = json.loads(interview['questions_list'])
    curr_idx = interview['current_question_index']
    
    current_question = questions[curr_idx] if curr_idx < len(questions) else ""
    total_q = len(questions)

    return render_template('interview.html', 
                           setup=False, 
                           interview=interview, 
                           current_question=current_question, 
                           question_number=curr_idx + 1,
                           total_questions=total_q)

@app.route('/api/interview/<int:id>/answer', methods=['POST'])
@login_required
def submit_answer(id):
    user_id = session['user_id']
    data = request.get_json()
    answer = data.get('answer', '').strip()
    
    if not answer:
        return jsonify({"success": False, "message": "Answer cannot be empty."})

    with get_db() as conn:
        interview = conn.execute('SELECT * FROM Interviews WHERE id = ? AND user_id = ?', (id, user_id)).fetchone()
        
        if not interview:
            return jsonify({"success": False, "message": "Interview session not found."})
            
        questions = json.loads(interview['questions_list'])
        answers = json.loads(interview['answers_list'])
        curr_idx = interview['current_question_index']
        
        # Append answer
        answers.append(answer)
        new_idx = curr_idx + 1
        
        if new_idx >= len(questions):
            status = 'completed'
            transcript = [{"question": q, "answer": a} for q, a in zip(questions, answers)]
            
            eval_system_prompt = (
                "You are an expert interviewer. Analyze the transcript of questions and answers. "
                "Evaluate the candidate across five dimensions on a scale of 0-100: "
                "Communication, Technical Knowledge, Confidence, Problem Solving, Professionalism. "
                "Provide detailed feedback including strengths, weaknesses, recommendations, and learning resources. "
                "Respond ONLY with a valid JSON document in this format: "
                "{\"scores\": {\"Communication\": 80, \"Technical Knowledge\": 75, \"Confidence\": 85, \"Problem Solving\": 80, \"Professionalism\": 90, \"Overall Score\": 82}, "
                "\"strengths\": \"...\", \"weaknesses\": \"...\", \"recommendations\": \"...\", "
                "\"learning_resources\": [{\"title\": \"Resource Title\", \"url\": \"https://...\"}]}"
            )
            eval_user_prompt = f"Role: {interview['role']}, Experience: {interview['experience']}, Type: {interview['type']}. Transcript: {json.dumps(transcript)}"
            
            ai_eval_reply = query_ai(eval_system_prompt, eval_user_prompt)
            
            try:
                eval_data = json.loads(ai_eval_reply)
                scores = eval_data['scores']
            except Exception as e:
                print(f"Failed to parse AI evaluation data: {e}. Loading mockup grades.")
                eval_data = json.loads(query_mock_ai(eval_system_prompt, eval_user_prompt))
                scores = eval_data['scores']
                
            conn.execute('''
                UPDATE Interviews 
                SET status = ?, current_question_index = ?, answers_list = ?,
                    score_communication = ?, score_technical = ?, score_confidence = ?,
                    score_problem_solving = ?, score_professionalism = ?, overall_score = ?
                WHERE id = ?
            ''', (status, new_idx, json.dumps(answers), 
                  scores['Communication'], scores['Technical Knowledge'], scores['Confidence'],
                  scores['Problem Solving'], scores['Professionalism'], scores['Overall Score'], id))
            
            # Save detailed Report
            conn.execute('''
                INSERT INTO Reports (interview_id, transcript, feedback_strengths, feedback_weaknesses, recommendations, learning_resources)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (id, json.dumps(transcript), eval_data['strengths'], eval_data['weaknesses'], eval_data['recommendations'], json.dumps(eval_data['learning_resources'])))
            conn.commit()
            
            log_activity(user_id, 'interview_completed', f"Completed {interview['type']} mock interview with overall score {scores['Overall Score']}%")
            return jsonify({"success": True, "finished": True, "redirect_url": url_for('interview_report', id=id)})
        else:
            # Save and advance question
            conn.execute('''
                UPDATE Interviews 
                SET current_question_index = ?, answers_list = ?
                WHERE id = ?
            ''', (new_idx, json.dumps(answers), id))
            conn.commit()
            return jsonify({"success": True, "finished": False, "next_question": questions[new_idx]})

# ---------------------------------------------------------
# REPORT GENERATION & DOWNLOAD
# ---------------------------------------------------------
@app.route('/interview/report/<int:id>')
@login_required
def interview_report(id):
    user_id = session['user_id']
    with get_db() as conn:
        interview = conn.execute('SELECT * FROM Interviews WHERE id = ? AND user_id = ?', (id, user_id)).fetchone()
        if not interview or interview['status'] != 'completed':
            flash('Report not found or interview not completed.', 'danger')
            return redirect(url_for('dashboard'))
            
        report = conn.execute('SELECT * FROM Reports WHERE interview_id = ?', (id,)).fetchone()
        
    transcript = json.loads(report['transcript'])
    resources = json.loads(report['learning_resources'])
    
    return render_template('interview.html', 
                           report_view=True, 
                           interview=interview, 
                           report=report, 
                           transcript=transcript, 
                           resources=resources)

@app.route('/interview/report/<int:id>/pdf')
@login_required
def download_report_pdf(id):
    user_id = session['user_id']
    with get_db() as conn:
        user = conn.execute('SELECT * FROM Users WHERE id = ?', (user_id,)).fetchone()
        interview = conn.execute('SELECT * FROM Interviews WHERE id = ? AND user_id = ?', (id, user_id)).fetchone()
        if not interview or interview['status'] != 'completed':
            flash('Report PDF not found.', 'danger')
            return redirect(url_for('dashboard'))
            
        report = conn.execute('SELECT * FROM Reports WHERE interview_id = ?', (id,)).fetchone()
        
    transcript = json.loads(report['transcript'])
    
    pdf_filename = f"report_{id}.pdf"
    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename)
    
    # Generate PDF using ReportLab
    doc = SimpleDocTemplate(pdf_path, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    
    # Custom Styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=24,
        textColor=colors.HexColor('#8b5cf6'),
        spaceAfter=15
    )
    section_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=16,
        textColor=colors.HexColor('#3b82f6'),
        spaceBefore=15,
        spaceAfter=10
    )
    body_style = ParagraphStyle(
        'Body',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor('#1f2937'),
        spaceAfter=8
    )
    qa_style = ParagraphStyle(
        'QAStyle',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#4b5563'),
        spaceAfter=8
    )
    
    story = []
    
    # Title
    story.append(Paragraph("InterviewAI - Performance Report", title_style))
    story.append(Paragraph(f"<b>Candidate:</b> {user['name']}", body_style))
    story.append(Paragraph(f"<b>Interview Focus:</b> {interview['type']} - {interview['role']} for {interview['company']}", body_style))
    story.append(Paragraph(f"<b>Overall Score:</b> {interview['overall_score']}%", body_style))
    story.append(Paragraph(f"<b>Date Generated:</b> {interview['created_at']}", body_style))
    story.append(Spacer(1, 15))
    
    # Scores Grid Table
    data = [
        ['Skill Dimension', 'Score'],
        ['Communication', f"{interview['score_communication']}%"],
        ['Technical Knowledge', f"{interview['score_technical']}%"],
        ['Confidence', f"{interview['score_confidence']}%"],
        ['Problem Solving', f"{interview['score_problem_solving']}%"],
        ['Professionalism', f"{interview['score_professionalism']}%"],
        ['Overall Average', f"{interview['overall_score']}%"]
    ]
    t = Table(data, colWidths=[200, 100])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#8b5cf6')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f3f4f6')),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#e5e7eb')),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0,-1), (-1,-1), colors.HexColor('#8b5cf6'))
    ]))
    story.append(t)
    story.append(Spacer(1, 15))
    
    # Detailed Analysis Section
    story.append(Paragraph("Structured Evaluation Summary", section_style))
    story.append(Paragraph("<b>Key Strengths:</b>", body_style))
    story.append(Paragraph(report['feedback_strengths'], body_style))
    story.append(Spacer(1, 8))
    
    story.append(Paragraph("<b>Areas for Development:</b>", body_style))
    story.append(Paragraph(report['feedback_weaknesses'], body_style))
    story.append(Spacer(1, 8))
    
    story.append(Paragraph("<b>Coaching Recommendations:</b>", body_style))
    story.append(Paragraph(report['recommendations'], body_style))
    story.append(Spacer(1, 15))
    
    # Transcript Section
    story.append(Paragraph("Question & Answer Transcript", section_style))
    for idx, item in enumerate(transcript):
        story.append(Paragraph(f"<b>Q{idx+1}: {item['question']}</b>", body_style))
        story.append(Paragraph(f"Candidate: \"{item['answer']}\"", qa_style))
        story.append(Spacer(1, 5))
        
    doc.build(story)
    
    return send_file(pdf_path, as_attachment=True, download_name=f"InterviewAI_Report_{interview['role']}_{id}.pdf")

# ---------------------------------------------------------
# RESUME ANALYZER
# ---------------------------------------------------------
@app.route('/resume', methods=['GET'])
@login_required
def resume_viewer():
    user_id = session['user_id']
    with get_db() as conn:
        analyses = conn.execute('SELECT * FROM ResumeAnalysis WHERE user_id = ? ORDER BY created_at DESC', (user_id,)).fetchall()
    return render_template('resume.html', analyses=analyses)

@app.route('/resume/analyze', methods=['POST'])
@login_required
def analyze_resume():
    user_id = session['user_id']
    resume_file = request.files.get('resume')
    
    if not resume_file or resume_file.filename == '':
        flash('Please select a valid resume document.', 'danger')
        return redirect(url_for('resume_viewer'))
        
    filename = secure_filename(resume_file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ['.pdf', '.docx']:
        flash('Only PDF and DOCX document formats are supported.', 'danger')
        return redirect(url_for('resume_viewer'))
        
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"resume_{user_id}_{filename}")
    resume_file.save(file_path)
    
    resume_text = ""
    try:
        if ext == '.pdf':
            reader = PdfReader(file_path)
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    resume_text += text
        elif ext == '.docx':
            if docx:
                doc_obj = docx.Document(file_path)
                resume_text = "\n".join([p.text for p in doc_obj.paragraphs])
            else:
                resume_text = "DOCX parser dependency python-docx not correctly loaded."
    except Exception as e:
        print(f"Error parsing uploaded document: {e}")
        resume_text = "Parsing error occurred. Standard validation layout loaded."
        
    if not resume_text.strip():
        resume_text = "No plain text found on this document page. Creating simulated review."

    system_prompt = (
        "You are an ATS system evaluator. Audit the candidate resume text. "
        "Recommend missing skills, formatting alerts, grammatical tips, and keywords matching target standards. "
        "Respond ONLY with a valid JSON in this format: "
        "{\"ats_score\": 75, \"missing_skills\": [\"A\", \"B\"], \"missing_keywords\": [\"X\", \"Y\"], "
        "\"formatting_issues\": [\"Issue 1\"], \"grammar_suggestions\": [\"Tip 1\"], \"recommendations\": \"Overall summary...\"}"
    )
    user_prompt = f"Resume Content:\n{resume_text}"
    
    ai_reply = query_ai(system_prompt, user_prompt)
    
    try:
        analysis_data = json.loads(ai_reply)
    except Exception as e:
        print(f"Error reading JSON output: {e}. Fallback triggered.")
        analysis_data = json.loads(query_mock_ai(system_prompt, user_prompt))
        
    # Store Analysis
    with get_db() as conn:
        conn.execute('''
            INSERT INTO ResumeAnalysis (user_id, filename, ats_score, missing_skills, missing_keywords, formatting_issues, grammar_suggestions, recommendations, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, filename, analysis_data['ats_score'], 
              ", ".join(analysis_data['missing_skills']), 
              ", ".join(analysis_data['missing_keywords']),
              json.dumps(analysis_data['formatting_issues']), 
              json.dumps(analysis_data['grammar_suggestions']), 
              analysis_data['recommendations'], 
              datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        
    log_activity(user_id, 'resume_uploaded', f"Analyzed resume: '{filename}' with ATS score: {analysis_data['ats_score']}%")
    flash('Resume parsed and analyzed successfully!', 'success')
    return redirect(url_for('resume_viewer'))

# ---------------------------------------------------------
# AI CAREER COACH
# ---------------------------------------------------------
@app.route('/career-coach')
@login_required
def career_coach():
    return render_template('career_coach.html')

@app.route('/api/career_coach/chat', methods=['POST'])
@login_required
def career_coach_chat():
    user_id = session['user_id']
    data = request.get_json()
    user_msg = data.get('message', '').strip()
    system_role = data.get('system_role', 'coach')

    if not user_msg:
        return jsonify({"success": False, "message": "Empty query."})
        
    if system_role == 'helper':
        system_prompt = (
            "You are the InterviewAI Floating Helper Bot. Your job is to help the user navigate the InterviewAI platform "
            "and answer quick questions about career preparation, mock interviews, resume matching, and site features."
        )
    else:
        system_prompt = (
            "You are the InterviewAI Career Coach, a professional mentor. Offer advice, roadmaps, certifications, "
            "skills guidance, and job preparation guidelines. Format answers with clear, clean markdown."
        )

    ai_reply = query_ai(system_prompt, user_msg)
    
    log_activity(user_id, 'career_chat', f"Asked Coach: \"{user_msg[:30]}...\"")
    return jsonify({"success": True, "reply": ai_reply})

# ---------------------------------------------------------
# ADMIN PANEL
# ---------------------------------------------------------
@app.route('/admin')
@admin_required
def admin():
    with get_db() as conn:
        total_users = conn.execute('SELECT COUNT(*) FROM Users').fetchone()[0]
        total_runs = conn.execute('SELECT COUNT(*) FROM Interviews WHERE status = "completed"').fetchone()[0]
        avg_score = conn.execute('SELECT AVG(overall_score) FROM Interviews WHERE status = "completed"').fetchone()[0]
        avg_score = int(avg_score) if avg_score else 0
        
        users = conn.execute('SELECT id, name, email, target_role, xp, streak, is_admin FROM Users ORDER BY xp DESC').fetchall()
        
        logs = conn.execute('''
            SELECT u.name, a.activity_type, a.description, a.timestamp 
            FROM ActivityLogs a 
            JOIN Users u ON a.user_id = u.id 
            ORDER BY a.timestamp DESC 
            LIMIT 30
        ''').fetchall()
        
        reports = conn.execute('''
            SELECT r.id, i.role, i.type, i.overall_score, u.name, i.created_at 
            FROM Reports r 
            JOIN Interviews i ON r.interview_id = i.id 
            JOIN Users u ON i.user_id = u.id 
            ORDER BY i.created_at DESC
        ''').fetchall()

    return render_template('admin.html', 
                           total_users=total_users, 
                           total_runs=total_runs, 
                           avg_score=avg_score, 
                           users=users, 
                           logs=logs,
                           reports=reports)

# ---------------------------------------------------------
# BOOTSTRAP APP RUNNER
# ---------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True, port=5000)
