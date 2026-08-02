
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, send_file, Response, jsonify
)

import cv2
import os
import csv
import shutil
import atexit
import threading
import time

from datetime import datetime
from functools import wraps

from database import init_db, get_connection
from train_model import train_face_model, get_training_status


# ============================================================
# APP CONFIGURATION
# ============================================================

app = Flask(__name__)
app.secret_key = "supersecretkey"


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATASET_DIR = os.path.join(BASE_DIR, "dataset")
TRAINER_DIR = os.path.join(BASE_DIR, "trainer")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")

TRAINER_FILE = os.path.join(TRAINER_DIR, "trainer.yml")


os.makedirs(DATASET_DIR, exist_ok=True)
os.makedirs(TRAINER_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)


# ============================================================
# FACE CASCADE
# ============================================================

cascade_path = os.path.join(
    BASE_DIR,
    "haarcascade_frontalface_default.xml"
)

FACE_CASCADE = cv2.CascadeClassifier(cascade_path)

if FACE_CASCADE.empty():
    raise ValueError(
        f"Could not load Haar Cascade file from: {cascade_path}"
    )


# ============================================================
# DATABASE
# ============================================================

init_db()


# ============================================================
# GLOBAL CAMERA / ATTENDANCE STATE
# ============================================================

camera = None

attendance_running = False
last_mark_time = {}


# ============================================================
# FACE CAPTURE STATE
# ============================================================

capture_running = False
capture_student_id = None
capture_count = 0
capture_max_samples = 60
capture_done = False
capture_message = "Idle"


# ============================================================
# TRAINING STATE
# ============================================================

training_thread = None


# ============================================================
# CAMERA RELEASE
# ============================================================

def release_camera():
    global camera

    if camera is not None:
        try:
            camera.release()
        except Exception:
            pass

        camera = None


atexit.register(release_camera)


# ============================================================
# LOGIN REQUIRED DECORATOR
# ============================================================

def login_required(func):

    @wraps(func)
    def wrapper(*args, **kwargs):

        if "admin_logged_in" not in session:
            flash("Please login first.", "danger")
            return redirect(url_for("login"))

        return func(*args, **kwargs)

    return wrapper


# ============================================================
# FACE COUNT
# ============================================================

def get_face_count(student_id):

    folder = os.path.join(
        DATASET_DIR,
        str(student_id)
    )

    if not os.path.exists(folder):
        return 0

    return len([
        f
        for f in os.listdir(folder)
        if f.lower().endswith(
            (".jpg", ".jpeg", ".png")
        )
    ])


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    if "admin_logged_in" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


# ============================================================
# LOGIN
# ============================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_connection()

        admin = conn.execute(
            "SELECT * FROM admin WHERE username = ? AND password = ?",
            (username, password)
        ).fetchone()

        conn.close()

        if admin:
            session["admin_logged_in"] = True
            session["admin_username"] = username

            return render_template(
                "login.html",
                login_success=True,
                message="Login successful! Redirecting to dashboard..."
            )

        return render_template(
            "login.html",
            login_success=False,
            message="Incorrect username or password."
        )

    return render_template("login.html")



# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    global attendance_running
    global capture_running

    attendance_running = False
    capture_running = False

    release_camera()

    session.clear()

    flash(
        "Logged out successfully.",
        "success"
    )

    return redirect(url_for("login"))


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/dashboard")
@login_required
def dashboard():

    conn = get_connection()

    total_students = conn.execute(
        "SELECT COUNT(*) AS count FROM students"
    ).fetchone()["count"]

    total_attendance = conn.execute(
        "SELECT COUNT(*) AS count FROM attendance"
    ).fetchone()["count"]

    today = datetime.now().strftime("%Y-%m-%d")

    today_attendance = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM attendance
        WHERE date = ?
        """,
        (today,)
    ).fetchone()["count"]

    recent_records = conn.execute(
        """
        SELECT
            name,
            roll,
            department,
            date,
            time,
            status
        FROM attendance
        ORDER BY id DESC
        LIMIT 8
        """
    ).fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        total_students=total_students,
        total_attendance=total_attendance,
        today_attendance=today_attendance,
        recent_records=recent_records
    )


# ============================================================
# STUDENTS
# ============================================================

@app.route("/students")
@login_required
def students():

    query = request.args.get(
        "q",
        ""
    ).strip()

    conn = get_connection()

    if query:

        students_list = conn.execute(
            """
            SELECT *
            FROM students
            WHERE
                name LIKE ?
                OR roll LIKE ?
                OR department LIKE ?
            ORDER BY id DESC
            """,
            (
                f"%{query}%",
                f"%{query}%",
                f"%{query}%"
            )
        ).fetchall()

    else:

        students_list = conn.execute(
            """
            SELECT *
            FROM students
            ORDER BY id DESC
            """
        ).fetchall()

    conn.close()

    students_with_faces = []

    for student in students_list:

        students_with_faces.append({
            "id": student["id"],
            "name": student["name"],
            "roll": student["roll"],
            "department": student["department"],
            "face_count": get_face_count(
                student["id"]
            )
        })

    return render_template(
        "students.html",
        students=students_with_faces,
        query=query
    )


# ============================================================
# ADD STUDENT PAGE
# ============================================================

@app.route("/add_student", methods=["GET"])
@login_required
def add_student():

    return render_template(
        "add_student.html"
    )


# ============================================================
# ADD STUDENT AJAX
# ============================================================

@app.route("/add_student_ajax", methods=["POST"])
@login_required
def add_student_ajax():

    data = request.get_json(
        silent=True
    ) or request.form

    name = str(
        data.get("name", "")
    ).strip()

    roll = str(
        data.get("roll", "")
    ).strip()

    department = str(
        data.get("department", "")
    ).strip()

    if not name or not roll or not department:

        return jsonify({
            "success": False,
            "message": "All fields are required."
        }), 400

    conn = get_connection()

    try:

        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO students
            (name, roll, department)
            VALUES (?, ?, ?)
            """,
            (
                name,
                roll,
                department
            )
        )

        conn.commit()

        student_id = cur.lastrowid

        conn.close()

        student_folder = os.path.join(
            DATASET_DIR,
            str(student_id)
        )

        os.makedirs(
            student_folder,
            exist_ok=True
        )

        return jsonify({
            "success": True,
            "message": "Student added successfully.",
            "student_id": student_id
        })

    except Exception:

        conn.close()

        return jsonify({
            "success": False,
            "message": "Roll number already exists."
        }), 400


# ============================================================
# EDIT STUDENT
# ============================================================

@app.route(
    "/edit_student/<int:student_id>",
    methods=["GET", "POST"]
)
@login_required
def edit_student(student_id):

    conn = get_connection()

    student = conn.execute(
        """
        SELECT *
        FROM students
        WHERE id = ?
        """,
        (student_id,)
    ).fetchone()

    if not student:

        conn.close()

        flash(
            "Student not found.",
            "danger"
        )

        return redirect(
            url_for("students")
        )

    if request.method == "POST":

        name = request.form[
            "name"
        ].strip()

        roll = request.form[
            "roll"
        ].strip()

        department = request.form[
            "department"
        ].strip()

        try:

            conn.execute(
                """
                UPDATE students
                SET
                    name = ?,
                    roll = ?,
                    department = ?
                WHERE id = ?
                """,
                (
                    name,
                    roll,
                    department,
                    student_id
                )
            )

            conn.commit()
            conn.close()

            flash(
                "Student updated successfully.",
                "success"
            )

            return redirect(
                url_for("students")
            )

        except Exception:

            conn.close()

            flash(
                "Roll number already exists.",
                "danger"
            )

            return redirect(
                url_for(
                    "edit_student",
                    student_id=student_id
                )
            )

    conn.close()

    return render_template(
        "edit_student.html",
        student=student
    )


# ============================================================
# DELETE STUDENT
# ============================================================

@app.route(
    "/delete_student/<int:student_id>"
)
@login_required
def delete_student(student_id):

    conn = get_connection()

    student = conn.execute(
        """
        SELECT *
        FROM students
        WHERE id = ?
        """,
        (student_id,)
    ).fetchone()

    if not student:

        conn.close()

        flash(
            "Student not found.",
            "danger"
        )

        return redirect(
            url_for("students")
        )

    conn.execute(
        """
        DELETE FROM students
        WHERE id = ?
        """,
        (student_id,)
    )

    conn.execute(
        """
        DELETE FROM attendance
        WHERE student_id = ?
        """,
        (student_id,)
    )

    conn.commit()
    conn.close()

    student_folder = os.path.join(
        DATASET_DIR,
        str(student_id)
    )

    if os.path.exists(student_folder):

        shutil.rmtree(
            student_folder
        )

    flash(
        "Student deleted successfully.",
        "success"
    )

    return redirect(
        url_for("students")
    )


# ============================================================
# RESET FACE DATA
# ============================================================

@app.route(
    "/reset_face/<int:student_id>"
)
@login_required
def reset_face(student_id):

    folder = os.path.join(
        DATASET_DIR,
        str(student_id)
    )

    if os.path.exists(folder):

        shutil.rmtree(folder)

    os.makedirs(
        folder,
        exist_ok=True
    )

    flash(
        "Old face data removed. Capture new face data now.",
        "success"
    )

    return redirect(
        url_for("students")
    )


# ============================================================
# CAPTURE FACE PAGE
# ============================================================

@app.route(
    "/capture_face/<int:student_id>"
)
@login_required
def capture_face(student_id):

    conn = get_connection()

    student = conn.execute(
        """
        SELECT *
        FROM students
        WHERE id = ?
        """,
        (student_id,)
    ).fetchone()

    conn.close()

    if not student:

        flash(
            "Student not found.",
            "danger"
        )

        return redirect(
            url_for("students")
        )

    return render_template(
        "capture_face.html",
        student=student
    )


# ============================================================
# START FACE CAPTURE
# ============================================================

@app.route(
    "/start_capture/<int:student_id>"
)
@login_required
def start_capture(student_id):

    global capture_running
    global capture_student_id
    global capture_count
    global capture_done
    global capture_message

    conn = get_connection()

    student = conn.execute(
        """
        SELECT *
        FROM students
        WHERE id = ?
        """,
        (student_id,)
    ).fetchone()

    conn.close()

    if not student:

        return jsonify({
            "status": "error",
            "message": "Student not found."
        }), 404

    capture_student_id = student_id
    capture_count = 0
    capture_done = False
    capture_running = True
    capture_message = "Capture started."

    student_folder = os.path.join(
        DATASET_DIR,
        str(student_id)
    )

    os.makedirs(
        student_folder,
        exist_ok=True
    )

    # Remove previous face images

    for file_name in os.listdir(
        student_folder
    ):

        file_path = os.path.join(
            student_folder,
            file_name
        )

        if os.path.isfile(file_path):

            try:
                os.remove(file_path)
            except Exception:
                pass

    return jsonify({
        "status": "success",
        "message": "Capture started.",
        "student_id": student_id,
        "max_samples": capture_max_samples
    })


# ============================================================
# GENERATE FACE CAPTURE FRAMES
# ============================================================

def generate_capture_frames():

    global camera
    global capture_running
    global capture_student_id
    global capture_count
    global capture_max_samples
    global capture_done
    global capture_message

    if capture_student_id is None:
        return

    student_folder = os.path.join(
        DATASET_DIR,
        str(capture_student_id)
    )

    os.makedirs(
        student_folder,
        exist_ok=True
    )

    if camera is None:

        camera = cv2.VideoCapture(0)

    if not camera.isOpened():

        capture_running = False

        capture_message = (
            "Could not access webcam."
        )

        return

    while capture_running:

        success, frame = camera.read()

        if not success:
            break

        frame = cv2.flip(
            frame,
            1
        )

        gray = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2GRAY
        )

        faces = FACE_CASCADE.detectMultiScale(
            gray,
            scaleFactor=1.3,
            minNeighbors=5,
            minSize=(100, 100)
        )

        for (x, y, w, h) in faces:

            if capture_count < capture_max_samples:

                capture_count += 1

                face_img = gray[
                    y:y + h,
                    x:x + w
                ]

                save_path = os.path.join(
                    student_folder,
                    f"{capture_count}.jpg"
                )

                cv2.imwrite(
                    save_path,
                    face_img
                )

            cv2.rectangle(
                frame,
                (x, y),
                (x + w, y + h),
                (0, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"Sample {capture_count}/{capture_max_samples}",
                (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2
            )

            break

        cv2.putText(
            frame,
            "Look at camera from different angles",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2
        )

        ret, buffer = cv2.imencode(
            ".jpg",
            frame
        )

        if not ret:
            continue

        frame_bytes = buffer.tobytes()

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame_bytes
            + b"\r\n"
        )

        if capture_count >= capture_max_samples:

            capture_done = True
            capture_running = False

            capture_message = (
                f"Face captured successfully. "
                f"{capture_count} images saved."
            )

            break

        time.sleep(0.03)

    release_camera()


# ============================================================
# CAPTURE VIDEO FEED
# ============================================================

@app.route(
    "/capture_video_feed"
)
@login_required
def capture_video_feed():

    return Response(
        generate_capture_frames(),
        mimetype=(
            "multipart/x-mixed-replace; "
            "boundary=frame"
        )
    )


# ============================================================
# CAPTURE STATUS
# ============================================================

@app.route(
    "/capture_status"
)
@login_required
def capture_status():

    return jsonify({
        "running": capture_running,
        "done": capture_done,
        "count": capture_count,
        "max_samples": capture_max_samples,
        "message": capture_message,
        "student_id": capture_student_id
    })


# ============================================================
# BACKGROUND TRAINING
# ============================================================

def background_train_model():

    train_face_model()


# ============================================================
# STOP CAPTURE AND TRAIN
# ============================================================

@app.route(
    "/stop_capture_and_train"
)
@login_required
def stop_capture_and_train():

    global capture_running
    global capture_done
    global capture_message
    global training_thread

    capture_running = False

    release_camera()

    if capture_count == 0:

        return jsonify({
            "success": False,
            "message": "No face captured. Try again."
        })

    capture_done = True

    capture_message = (
        f"Face captured successfully. "
        f"{capture_count} images saved."
    )

    status = get_training_status()

    if status.get("training"):

        return jsonify({
            "success": True,
            "message": "Training is already in progress."
        })

    training_thread = threading.Thread(
        target=background_train_model,
        daemon=True
    )

    training_thread.start()

    return jsonify({
        "success": True,
        "message": "Training started."
    })


# ============================================================
# TRAINING STATUS
# ============================================================

@app.route(
    "/training_status"
)
@login_required
def training_status():

    return jsonify(
        get_training_status()
    )


# ============================================================
# MANUAL TRAIN MODEL
# ============================================================

@app.route(
    "/train_model"
)
@login_required
def train_model_route():

    success, message = train_face_model()

    flash(
        message,
        "success" if success else "danger"
    )

    return redirect(
        url_for("students")
    )


# ============================================================
# MARK ATTENDANCE PAGE
# ============================================================

@app.route(
    "/mark_attendance"
)
@login_required
def mark_attendance():

    return render_template(
        "mark_attendance.html"
    )


# ============================================================
# START ATTENDANCE CAMERA
# ============================================================

@app.route(
    "/start_attendance_camera"
)
@login_required
def start_attendance_camera():

    global attendance_running
    global last_mark_time

    if not os.path.exists(TRAINER_FILE):

        return jsonify({
            "status": "error",
            "message": "Train the model first."
        })

    if not hasattr(cv2, "face"):

        return jsonify({
            "status": "error",
            "message": "OpenCV face module not found."
        })

    attendance_running = True
    last_mark_time = {}

    return jsonify({
        "status": "success",
        "message": "Camera started."
    })


# ============================================================
# STOP ATTENDANCE CAMERA
# ============================================================

@app.route(
    "/stop_attendance_camera"
)
@login_required
def stop_attendance_camera():

    global attendance_running

    attendance_running = False

    release_camera()

    return jsonify({
        "status": "success",
        "message": "Camera stopped."
    })


# ============================================================
# ATTENDANCE STATUS
# ============================================================

@app.route(
    "/attendance_status"
)
@login_required
def attendance_status():

    conn = get_connection()

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    records = conn.execute(
        """
        SELECT
            name,
            roll,
            department,
            time
        FROM attendance
        WHERE date = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (today,)
    ).fetchall()

    conn.close()

    data = []

    for row in records:

        data.append({
            "name": row["name"],
            "roll": row["roll"],
            "department": row["department"],
            "time": row["time"]
        })

    return jsonify(data)


# ============================================================
# GENERATE ATTENDANCE FRAMES
# ============================================================

def generate_frames():

    global camera
    global attendance_running
    global last_mark_time

    if not os.path.exists(
        TRAINER_FILE
    ):
        return

    if not hasattr(
        cv2,
        "face"
    ):
        return

    recognizer = (
        cv2.face.LBPHFaceRecognizer_create()
    )

    recognizer.read(
        TRAINER_FILE
    )

    conn = get_connection()

    students_data = conn.execute(
        "SELECT * FROM students"
    ).fetchall()

    student_map = {
        row["id"]: row
        for row in students_data
    }

    conn.close()

    if camera is None:

        camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        return

    cooldown_seconds = 10

    while attendance_running:

        success, frame = camera.read()

        if not success:
            break

        frame = cv2.flip(
            frame,
            1
        )

        gray = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2GRAY
        )

        faces = FACE_CASCADE.detectMultiScale(
            gray,
            scaleFactor=1.2,
            minNeighbors=5,
            minSize=(100, 100)
        )

        now = datetime.now()

        date_str = now.strftime(
            "%Y-%m-%d"
        )

        time_str = now.strftime(
            "%H:%M:%S"
        )

        today_status = "Present"

        for (x, y, w, h) in faces:

            face_img = gray[
                y:y + h,
                x:x + w
            ]

            try:

                student_id, confidence = (
                    recognizer.predict(face_img)
                )

            except Exception:

                student_id = -1
                confidence = 999

            if (
                confidence < 55
                and student_id in student_map
            ):

                student = student_map[
                    student_id
                ]

                name = student["name"]
                roll = student["roll"]
                department = student["department"]

                allow_mark = False

                if student_id not in last_mark_time:

                    allow_mark = True

                else:

                    diff = (
                        now
                        - last_mark_time[student_id]
                    ).total_seconds()

                    if diff >= cooldown_seconds:

                        allow_mark = True

                if allow_mark:

                    conn = get_connection()

                    try:

                        conn.execute(
                            """
                            INSERT INTO attendance
                            (
                                student_id,
                                name,
                                roll,
                                department,
                                date,
                                time,
                                status
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                student_id,
                                name,
                                roll,
                                department,
                                date_str,
                                time_str,
                                today_status
                            )
                        )

                        conn.commit()

                        last_mark_time[
                            student_id
                        ] = now

                        label = "Attendance Marked"
                        color = (0, 255, 0)

                    except Exception:

                        label = "Mark Failed"
                        color = (0, 0, 255)

                    conn.close()

                else:

                    wait_left = (
                        cooldown_seconds
                        - int(
                            (
                                now
                                - last_mark_time[
                                    student_id
                                ]
                            ).total_seconds()
                        )
                    )

                    if wait_left < 0:
                        wait_left = 0

                    label = (
                        f"Please Wait {wait_left}s"
                    )

                    color = (
                        0,
                        255,
                        255
                    )

                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + w, y + h),
                    color,
                    3
                )

                cv2.putText(
                    frame,
                    name,
                    (x, y - 12),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    color,
                    2
                )

                cv2.putText(
                    frame,
                    label,
                    (x, y + h + 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.75,
                    color,
                    2
                )

            else:

                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + w, y + h),
                    (0, 0, 255),
                    3
                )

                cv2.putText(
                    frame,
                    "Unknown",
                    (x, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 0, 255),
                    2
                )

        cv2.putText(
            frame,
            "Smart Attendance",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (255, 255, 255),
            2
        )

        ret, buffer = cv2.imencode(
            ".jpg",
            frame
        )

        if not ret:
            continue

        frame_bytes = buffer.tobytes()

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame_bytes
            + b"\r\n"
        )

    release_camera()


# ============================================================
# ATTENDANCE VIDEO FEED
# ============================================================

@app.route(
    "/video_feed"
)
@login_required
def video_feed():

    return Response(
        generate_frames(),
        mimetype=(
            "multipart/x-mixed-replace; "
            "boundary=frame"
        )
    )


# ============================================================
# ATTENDANCE RECORDS
# ============================================================

@app.route(
    "/attendance",
    methods=["GET"]
)
@login_required
def attendance():

    query = request.args.get(
        "q",
        ""
    ).strip()

    date_filter = request.args.get(
        "date",
        ""
    ).strip()

    conn = get_connection()

    sql = (
        "SELECT * FROM attendance "
        "WHERE 1=1"
    )

    params = []

    if query:

        sql += (
            " AND ("
            "name LIKE ? "
            "OR roll LIKE ? "
            "OR department LIKE ?"
            ")"
        )

        params.extend([
            f"%{query}%",
            f"%{query}%",
            f"%{query}%"
        ])

    if date_filter:

        sql += " AND date = ?"

        params.append(
            date_filter
        )

    sql += " ORDER BY id DESC"

    records = conn.execute(
        sql,
        params
    ).fetchall()

    conn.close()

    return render_template(
        "attendance.html",
        records=records,
        query=query,
        date_filter=date_filter
    )


# ============================================================
# MANUAL ATTENDANCE
# ============================================================

@app.route(
    "/manual_attendance/<int:student_id>"
)
@login_required
def manual_attendance(student_id):

    conn = get_connection()

    student = conn.execute(
        """
        SELECT *
        FROM students
        WHERE id = ?
        """,
        (student_id,)
    ).fetchone()

    if not student:

        conn.close()

        flash(
            "Student not found.",
            "danger"
        )

        return redirect(
            url_for("students")
        )

    date_str = datetime.now().strftime(
        "%Y-%m-%d"
    )

    time_str = datetime.now().strftime(
        "%H:%M:%S"
    )

    status = "Present"

    conn.execute(
        """
        INSERT INTO attendance
        (
            student_id,
            name,
            roll,
            department,
            date,
            time,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            student["id"],
            student["name"],
            student["roll"],
            student["department"],
            date_str,
            time_str,
            status
        )
    )

    conn.commit()
    conn.close()

    flash(
        "Manual attendance marked successfully.",
        "success"
    )

    return redirect(
        url_for("attendance")
    )


# ============================================================
# EXPORT ATTENDANCE
# ============================================================

@app.route(
    "/export_attendance"
)
@login_required
def export_attendance():

    conn = get_connection()

    records = conn.execute(
        """
        SELECT *
        FROM attendance
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    filename = os.path.join(
        EXPORT_DIR,
        "attendance_export.csv"
    )

    with open(
        filename,
        mode="w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.writer(file)

        writer.writerow([
            "ID",
            "Student ID",
            "Name",
            "Roll",
            "Department",
            "Date",
            "Time",
            "Status"
        ])

        for row in records:

            writer.writerow([
                row["id"],
                row["student_id"],
                row["name"],
                row["roll"],
                row["department"],
                row["date"],
                row["time"],
                row["status"]
            ])

    return send_file(
        filename,
        as_attachment=True
    )


# ============================================================
# RUN APPLICATION
# ============================================================

if __name__ == "__main__":

    app.run(
        debug=True
    )

