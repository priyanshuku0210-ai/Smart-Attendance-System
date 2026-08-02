import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "students.db")


def get_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Admin table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    # Students table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            roll TEXT UNIQUE NOT NULL,
            department TEXT NOT NULL
        )
    """)

    # Attendance table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            roll TEXT NOT NULL,
            department TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY(student_id) REFERENCES students(id)
        )
    """)

    # Make sure department exists in older databases
    columns = [
        row[1]
        for row in cur.execute("PRAGMA table_info(attendance)").fetchall()
    ]

    if "department" not in columns:
        cur.execute("""
            ALTER TABLE attendance
            ADD COLUMN department TEXT NOT NULL DEFAULT ''
        """)

    # Remove old attendance index if it exists
    cur.execute("DROP INDEX IF EXISTS idx_attendance_unique")

    # Create default admin account
    admin = cur.execute(
        "SELECT * FROM admin WHERE username = ?",
        ("admin",)
    ).fetchone()

    if not admin:
        cur.execute(
            "INSERT INTO admin (username, password) VALUES (?, ?)",
            ("admin", "admin123")
        )
       demo = cur.execute(
    "SELECT * FROM admin WHERE username = ?",
    ("demo",)
).fetchone()

if not demo:
    cur.execute(
        "INSERT INTO admin (username, password) VALUES (?, ?)",
        ("demo", "Demo@2026")
    )

    conn.commit()
    conn.close()