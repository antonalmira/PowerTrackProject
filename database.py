import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "powertrack.db")

def get_connection():
    return sqlite3.connect(DB_PATH)

def setup_database():
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS students (
                user_id TEXT PRIMARY KEY,
                first_name TEXT,
                last_name TEXT,
                email TEXT,
                organization TEXT DEFAULT 'Unknown',
                signed_up TEXT,
                latest_activity TEXT,
                location TEXT DEFAULT 'N/A',
                title TEXT DEFAULT 'N/A',
                manager TEXT DEFAULT 'N/A',
                progress_status TEXT DEFAULT 'N/A',
                control_codes TEXT DEFAULT '',
                region_bucket TEXT DEFAULT 'Worldwide'
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS courses (
                course_id TEXT PRIMARY KEY,
                course_name TEXT,
                course_type TEXT DEFAULT 'Unknown',
                control_code TEXT DEFAULT 'Unassigned',
                graph_category TEXT DEFAULT 'Unassigned'
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS completions (
                user_id TEXT,
                course_id TEXT,
                percent_complete INTEGER,
                completed_at TEXT,
                score TEXT,
                success_status TEXT,
                PRIMARY KEY (user_id, course_id),
                FOREIGN KEY (user_id) REFERENCES students (user_id),
                FOREIGN KEY (course_id) REFERENCES courses (course_id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS monthly_snapshots (
                month TEXT,
                team TEXT,
                published INTEGER,
                taiwan REAL,
                china REAL,
                korea REAL,
                japan REAL,
                americas REAL,
                europe REAL,
                isea REAL,
                worldwide REAL,
                PRIMARY KEY (month, team)
            )
        ''')

        migrations = [
            "ALTER TABLE students ADD COLUMN control_codes TEXT DEFAULT ''",
            "ALTER TABLE students ADD COLUMN region_bucket TEXT DEFAULT 'Worldwide'",
            "ALTER TABLE courses ADD COLUMN control_code TEXT DEFAULT 'Unassigned'",
            "ALTER TABLE courses ADD COLUMN graph_category TEXT DEFAULT 'Unassigned'",
        ]
        
        for migration in migrations:
            try: cursor.execute(migration)
            except sqlite3.OperationalError: pass 

setup_database()