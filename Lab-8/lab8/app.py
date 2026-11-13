# demo account: password for all: 12345
# demo student: cnorris
# demo teacher: ahepworth, jdoe
# demo admin: admin

import os
from functools import wraps
from math import isfinite

from flask import Flask, jsonify, redirect, render_template, request, session
from flask_admin import Admin, AdminIndexView
from flask_admin.contrib.sqla import ModelView
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from wtforms import PasswordField

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///enrollment.db")
db = SQLAlchemy(app)


# each class as a database table, user inputs what they want to do
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False)  # student | teacher | admin
    display_name = db.Column(db.String(100), nullable=False)

    def __str__(self):
        return self.display_name

# course table: each course has one teacher, class times and capacity
class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    time = db.Column(db.String(50), nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    teacher = db.relationship("User")

    def __str__(self):
        return self.name

# enrollment table: links one student to one course and stores their grade
# cannot enroll into the course twice
class Enrollment(db.Model):
    """One student in one course; the grade lives here."""
    __table_args__ = (UniqueConstraint("student_id", "course_id"),)
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    grade = db.Column(db.Float)
    student = db.relationship("User")
    course = db.relationship("Course", backref=db.backref("enrollments", cascade="all, delete-orphan"))


# keeps the logged in user, if not they goes back to welcome
def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None

# error messages
def role_required(role):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                return jsonify(error="Please log in"), 401
            if user.role != role:
                return jsonify(error="Not allowed"), 403
            return view(user, *args, **kwargs)
        return wrapper
    return decorator

# course into json
def course_dict(course, mine):
    return {"id": course.id, "name": course.name, "teacher": course.teacher.display_name,
            "time": course.time, "enrolled": len(course.enrollments),
            "capacity": course.capacity, "mine": mine}


# pages and login
@app.get("/")
def index():
    return render_template("index.html")

# for login, checks username and password, remembers the session
@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    user = db.session.execute(
        db.select(User).filter_by(username=str(data.get("username", "")).strip())).scalar()
    if user is None or not check_password_hash(user.password_hash, str(data.get("password", ""))):
        return jsonify(error="Wrong username or password"), 401
    session.clear()
    session["user_id"] = user.id
    return jsonify(name=user.display_name, role=user.role)

# logout clears the session
@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    user = current_user()
    if user is None:
        return jsonify(error="Please log in"), 401
    return jsonify(name=user.display_name, role=user.role)


# courses: students and teachers
@app.get("/api/courses")
def list_courses():
    """All courses. 'mine' = enrolled (student) or teaching (teacher)."""
    user = current_user()
    if user is None:
        return jsonify(error="Please log in"), 401
    enrolled_ids = {e.course_id for e in db.session.query(Enrollment).filter_by(student_id=user.id)}
    courses = db.session.execute(db.select(Course).order_by(Course.name)).scalars().all()
    return jsonify([course_dict(c, c.id in enrolled_ids if user.role == "student"
                                else c.teacher_id == user.id) for c in courses])


# student: enroll and drop; error messages added
@app.post("/api/enroll")
@role_required("student")
def enroll(user):
    data = request.get_json(silent=True) or {}
    course_id = data.get("course_id")
    course = db.session.get(Course, course_id) if isinstance(course_id, int) else None
    if course is None:
        return jsonify(error="Course not found"), 404
    if db.session.query(Enrollment).filter_by(student_id=user.id, course_id=course.id).first():
        return jsonify(error="Already enrolled"), 409
    count = db.session.query(func.count(Enrollment.id)).filter_by(course_id=course.id).scalar()
    if count >= course.capacity:
        return jsonify(error="Course is full"), 409
    db.session.add(Enrollment(student_id=user.id, course_id=course.id))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify(error="Already enrolled"), 409
    return jsonify(course_dict(course, True)), 201

# student drops a course; deletes enrollment
@app.delete("/api/enroll/<int:course_id>")
@role_required("student")
def drop(user, course_id):
    enrollment = db.session.query(Enrollment).filter_by(student_id=user.id, course_id=course_id).first()
    if enrollment is None:
        return jsonify(error="Not enrolled"), 404
    db.session.delete(enrollment)
    db.session.commit()
    return jsonify(dropped=course_id)


# teacher: roster and grades, only for their own courses
def own_course(user, course_id):
    course = db.session.get(Course, course_id)
    return course if course and course.teacher_id == user.id else None

# teacher view for their course
@app.get("/api/courses/<int:course_id>/roster")
@role_required("teacher")
def roster(user, course_id):
    course = own_course(user, course_id)
    if course is None:
        return jsonify(error="Not your course"), 403
    students = sorted(course.enrollments, key=lambda e: e.student.display_name)
    return jsonify(course=course_dict(course, True),
                   students=[{"student_id": e.student_id, "name": e.student.display_name,
                              "grade": e.grade} for e in students])

# teacher able to edit the student in their course, not the students other course
@app.put("/api/courses/<int:course_id>/grades/<int:student_id>")
@role_required("teacher")
def edit_grade(user, course_id, student_id):
    if own_course(user, course_id) is None:
        return jsonify(error="Not your course"), 403
    enrollment = db.session.query(Enrollment).filter_by(course_id=course_id, student_id=student_id).first()
    if enrollment is None:
        return jsonify(error="Not found"), 404
    grade = (request.get_json(silent=True) or {}).get("grade")
    if type(grade) not in (int, float) or not isfinite(grade) or not 0 <= grade <= 100:
        return jsonify(error="Grade must be a number from 0 to 100"), 400
    enrollment.grade = grade
    db.session.commit()
    return jsonify(student_id=student_id, grade=grade)


# admin (Flask-Admin), admins only
class AdminOnly:
    def is_accessible(self):
        user = current_user()
        return user is not None and user.role == "admin"

    def inaccessible_callback(self, name, **kwargs):
        return redirect("/")


class SecureIndex(AdminOnly, AdminIndexView):
    pass


class SecureView(AdminOnly, ModelView):
    pass

# admin view, set new password if needed
class UserView(SecureView):
    column_exclude_list = ["password_hash"]
    form_excluded_columns = ["password_hash"]
    form_extra_fields = {"password": PasswordField("Password (leave blank to keep)")}
    form_choices = {"role": [("student", "student"), ("teacher", "teacher"), ("admin", "admin")]}

    def on_model_change(self, form, model, is_created):
        if form.password.data:
            model.password_hash = generate_password_hash(form.password.data)
        elif is_created:
            raise ValueError("A password is required for a new user.")

# creates the admin page
admin = Admin(app, name="ACME Admin", index_view=SecureIndex())
admin.add_view(UserView(User, db.session, name="Users"))
admin.add_view(SecureView(Course, db.session, name="Courses"))
admin.add_view(SecureView(Enrollment, db.session, name="Enrollments / Grades"))


# demo data (only when the database is empty)
def seed():
    pw = generate_password_hash("12345")
    def user(username, name, role):
        u = User(username=username, display_name=name, role=role, password_hash=pw)
        db.session.add(u)
        return u
    user("admin", "Admin", "admin")
    t = {k: user(k, n, "teacher") for k, n in
         [("ahepworth", "Ammon Hepworth"), ("swalker", "Susan Walker"), ("rjenkins", "Ralph Jenkins")]}
    s = {k: user(k, n, "student") for k, n in
         [("jsantos", "Jose Santos"),("bbrown", "Betty Brown"), ("jstuart", "John Stuart"), ("nlittle", "Nancy Little"), 
          ("lcheng", "Li Cheng"), ("mnorris", "Mindy Norris"), ("aranganath", "Aditya Ranganath"), ("ywchen", "Yi Wen Chen")]}
    courses = {
        "Math 101": (t["rjenkins"], "MWF 10:00-10:50 AM", 8,
                    {"jsantos": 88, "bbrown": 65, "jstuart": 86, "lcheng": 77}),
        "Physics 121": (t["swalker"], "TR 11:00-11:50 AM", 10,
                    {"nlittle": 53, "lcheng": 85, "mnorris": 94, "jstuart":91, "bbrown": 88}),
        "CS 106": (t["ahepworth"], "MWF 2:00-2:50 PM", 10,
                    {"aranganath": 93, "ywchen": 85, "nlittle": 57, "mnorris": 68}),
        "CS 162": (t["ahepworth"], "TR 3:00-3:50 PM", 4,
                   {"aranganath": 99, "nlittle": 87, "ywchen": 92, "jstuart": 67}),
    }
    for name, (teacher, time, capacity, grades) in courses.items():
        course = Course(name=name, time=time, capacity=capacity, teacher=teacher)
        db.session.add(course)
        for username, grade in grades.items():
            db.session.add(Enrollment(student=s[username], course=course, grade=grade))
    db.session.commit()


with app.app_context():
    db.create_all()
    if db.session.query(User).first() is None:
        seed()


if __name__ == "__main__":
    app.run(debug=True)