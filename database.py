import sqlite3
import os
import sys
import json

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, "powertrack.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def setup_database():
    with get_connection() as conn:
        cursor = conn.cursor()

        # ── Core tables ────────────────────────────────────────────────────────

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS students (
                user_id        TEXT PRIMARY KEY,
                first_name     TEXT,
                last_name      TEXT,
                email          TEXT,
                organization   TEXT DEFAULT 'Unknown',
                signed_up      TEXT,
                latest_activity TEXT,
                location       TEXT DEFAULT 'N/A',
                title          TEXT DEFAULT 'N/A',
                manager        TEXT DEFAULT 'N/A',
                progress_status TEXT DEFAULT 'N/A',
                control_codes  TEXT DEFAULT '',
                region_bucket  TEXT DEFAULT 'Worldwide'
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS courses (
                course_id      TEXT PRIMARY KEY,
                course_name    TEXT,
                course_type    TEXT DEFAULT 'Unknown',
                control_code   TEXT DEFAULT 'Unassigned',
                graph_category TEXT DEFAULT 'Unassigned'
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS completions (
                user_id          TEXT,
                course_id        TEXT,
                percent_complete REAL DEFAULT 0.0,
                completed_at     TEXT,
                score            TEXT,
                success_status   TEXT,
                PRIMARY KEY (user_id, course_id),
                FOREIGN KEY (user_id)   REFERENCES students (user_id),
                FOREIGN KEY (course_id) REFERENCES courses  (course_id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS monthly_snapshots (
                month     TEXT,
                year      INTEGER DEFAULT 2025,
                team      TEXT,
                published INTEGER,
                taiwan    REAL,
                china     REAL,
                korea     REAL,
                japan     REAL,
                americas  REAL,
                europe    REAL,
                isea      REAL,
                worldwide REAL,
                PRIMARY KEY (month, year, team)
            )
        ''')

        # ── Settings table for dynamic configurations ──────────────────────────
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        cursor.execute("SELECT 1 FROM app_settings WHERE key='category_tree'")
        if not cursor.fetchone():
            default_tree = {
                "Low & Mid Power": ["InnoSwitch", "LinkSwitch", "LYTSwitch", "Other"],
                "High Power":      ["High Power"],
                "Automotive":      ["Automotive"],
                "Motor Driver":    ["Motor Driver"]
            }
            cursor.execute("INSERT INTO app_settings (key, value) VALUES ('category_tree', ?)", (json.dumps(default_tree),))


        # ── Schema-migration table (versioned) ─────────────────────────────────
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id         INTEGER PRIMARY KEY,
                applied_at TEXT DEFAULT (datetime('now'))
            )
        ''')

        def applied(mid):
            return cursor.execute(
                "SELECT 1 FROM schema_migrations WHERE id = ?", (mid,)
            ).fetchone() is not None

        def run(mid, sql):
            if not applied(mid):
                try:
                    cursor.execute(sql)
                    cursor.execute(
                        "INSERT INTO schema_migrations (id) VALUES (?)", (mid,)
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise

        run(1, "ALTER TABLE students ADD COLUMN control_codes TEXT DEFAULT ''")
        run(2, "ALTER TABLE students ADD COLUMN region_bucket TEXT DEFAULT 'Worldwide'")
        run(3, "ALTER TABLE courses  ADD COLUMN control_code  TEXT DEFAULT 'Unassigned'")
        run(4, "ALTER TABLE courses  ADD COLUMN graph_category TEXT DEFAULT 'Unassigned'")
        run(5, "ALTER TABLE monthly_snapshots ADD COLUMN year INTEGER DEFAULT 2025")
        
        run(6, '''
            CREATE TABLE IF NOT EXISTS completions_history (
                user_id          TEXT,
                course_id        TEXT,
                year             INTEGER,
                month            INTEGER,
                percent_complete REAL,
                PRIMARY KEY (user_id, course_id, year, month)
            )
        ''')
        
        # Org-Isolated Course Mappings and Sort Orders
        run(7, "ALTER TABLE courses ADD COLUMN fae_control_code TEXT DEFAULT 'Unassigned'")
        run(8, "ALTER TABLE courses ADD COLUMN fae_graph_category TEXT DEFAULT 'Unassigned'")
        run(9, "ALTER TABLE courses ADD COLUMN fae_sort_order INTEGER DEFAULT 999")
        
        run(10, "ALTER TABLE courses ADD COLUMN sales_control_code TEXT DEFAULT 'Unassigned'")
        run(11, "ALTER TABLE courses ADD COLUMN sales_graph_category TEXT DEFAULT 'Unassigned'")
        run(12, "ALTER TABLE courses ADD COLUMN sales_sort_order INTEGER DEFAULT 999")
        
        run(13, "ALTER TABLE courses ADD COLUMN unknown_control_code TEXT DEFAULT 'Unassigned'")
        run(14, "ALTER TABLE courses ADD COLUMN unknown_graph_category TEXT DEFAULT 'Unassigned'")
        run(15, "ALTER TABLE courses ADD COLUMN unknown_sort_order INTEGER DEFAULT 999")
        
        # Backfill existing data safely
        run(16, """
            UPDATE courses SET 
                fae_control_code = control_code, fae_graph_category = graph_category,
                sales_control_code = control_code, sales_graph_category = graph_category,
                unknown_control_code = control_code, unknown_graph_category = graph_category
            WHERE fae_control_code = 'Unassigned' AND control_code != 'Unassigned'
        """)

        # ── Indexes ────────────────────────────────────────────────────────────
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_completions_user   ON completions (user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_completions_course ON completions (course_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_students_email ON students (email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_students_org   ON students (organization)")


setup_database()