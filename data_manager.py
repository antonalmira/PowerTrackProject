import csv
import pandas as pd
import difflib
import os
import glob
import re
import sys
from database import get_connection, setup_database


# ── Configurable constants ─────────────────────────────────────────────────────

INTERNAL_DOMAINS = ["@power.com", "@powerint.com"]
FUZZY_CUTOFF = 0.75

# ── Shared utilities ───────────────────────────────────────────────────────────

def normalize_uid(uid) -> str:
    s = str(uid).strip()
    return s[:-2] if s.endswith(".0") else s

def parse_region(location_str: str) -> str:
    loc = str(location_str).lower()

    def _match(words):
        return any(re.search(r"\b" + re.escape(w) + r"\b", loc) for w in words)

    if _match(["india", "sea", "singapore", "malaysia", "isea",
               "vietnam", "thailand", "indonesia", "australia", "new zealand"]):
        return "ISEA"
    if _match(["usa", "canada", "america", "mexico", "brazil", "argentina",
               "san jose", "california", "texas", "boston", "philippines"]):
        return "Americas"
    if re.search(r"\bus\b", loc):
        return "Americas"
    if _match(["europe", "germany", "italy", "uk", "switzerland", "france",
               "munich", "spain", "poland"]):
        return "Europe"
    if _match(["china", "beijing", "shenzhen", "shanghai", "qingdao",
               "chengdu", "foshan", "xiamen"]):
        return "China"
    if _match(["taiwan", "taipei"]):
        return "Taiwan"
    if _match(["japan", "tokyo", "yokohama"]):
        return "Japan"
    if _match(["korea", "seoul"]):
        return "Korea"
    return "Worldwide"

def clean_string_for_matching(text: str) -> str:
    if not isinstance(text, str): return ""
    text = text.lower()
    text = text.replace("-sales-students.xlsx", "").replace("-fae-students.xlsx", "")
    text = text.replace("students.xlsx", "").replace("-students.xlsx", "")
    text = text.replace(".xlsx", "").replace(".csv", "")
    text = text.replace("introduction to", "introducing").replace("introduction-to", "introducing")
    return re.sub(r"[^a-z0-9]", "", text)

def safe_float(val, default=0.0) -> float:
    try:
        return float(str(val).replace("%", "").strip())
    except (ValueError, TypeError):
        return default

def load_dataframe(file_path: str, header_row=0):
    try:
        if header_row == "infer": header_row = 0
        if file_path.lower().endswith(".csv"):
            return pd.read_csv(file_path, low_memory=False, header=header_row)
        elif file_path.lower().endswith((".xlsx", ".xls")):
            return pd.read_excel(file_path, header=header_row)
    except Exception as exc:
        print(f"[ERROR] loading {file_path}: {exc}")
    return None

# ── DataManager ────────────────────────────────────────────────────────────────

class DataManager:
    def __init__(self):
        setup_database()

    # ── Auto-Import Master Lists ───────────────────────────────────────────────
    
    def auto_import_master_lists(self):
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            
        master_dir = os.path.join(base_dir, "master_lists")
        
        if not os.path.exists(master_dir):
            os.makedirs(master_dir)
            return

        for filename in os.listdir(master_dir):
            if filename.lower().endswith(".csv"):
                file_path = os.path.join(master_dir, filename)
                self._process_master_csv(file_path)

    def _process_master_csv(self, file_path: str):
        df = load_dataframe(file_path)
        if df is None: return

        cols = {str(c).lower().strip(): c for c in df.columns}
        email_col = cols.get("email")
        if not email_col:
            print(f"[WARN] No 'Email' column in {file_path}. Skipping.")
            return

        df = df.dropna(subset=[email_col])
        df = df[df[email_col].astype(str).str.contains("@", na=False)]

        org_label = "FAE" if "fae" in file_path.lower() else "Sales" if "sales" in file_path.lower() else "Unknown"

        with get_connection() as conn:
            cursor = conn.cursor()
            for _, row in df.iterrows():
                email = str(row.get(email_col)).strip().lower()
                name = str(row.get(cols.get("full name", "Full Name"), "")).strip()
                loc = str(row.get(cols.get("location", "Location"), "")).strip()
                title = str(row.get(cols.get("title", "Title"), "")).strip()
                mgr = str(row.get(cols.get("manager", "Manager"), "")).strip()
                
                parts = name.split(" ", 1)
                first = parts[0]
                last = parts[1] if len(parts) > 1 else ""
                region = parse_region(loc)

                cursor.execute("SELECT user_id FROM students WHERE LOWER(email)=?", (email,))
                existing = cursor.fetchone()
                
                if existing:
                    cursor.execute("""
                        UPDATE students 
                        SET organization=?, location=?, title=?, manager=?, region_bucket=?, first_name=?, last_name=?
                        WHERE user_id=?
                    """, (org_label, loc, title, mgr, region, first, last, existing[0]))
                else:
                    cursor.execute("""
                        INSERT INTO students (user_id, first_name, last_name, email, organization, location, title, manager, region_bucket)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (email, first, last, email, org_label, loc, title, mgr, region))

    # ── Students ───────────────────────────────────────────────────────────────

    def import_students(self, file_path: str):
        df = load_dataframe(file_path)
        if df is None: return

        cols = {str(c).lower().strip(): c for c in df.columns}
        uid_col = cols.get("user id")
        if not uid_col: return

        df = df.dropna(subset=[uid_col])
        records = []
        for _, row in df.iterrows():
            uid = normalize_uid(row.get(uid_col, ""))
            if not uid: continue

            first = str(row.get(cols.get("first name", "First name"), "")).strip()
            last  = str(row.get(cols.get("last name",  "Last name"),  "")).strip()
            email = str(row.get(cols.get("email",      "Email"),      "")).strip()

            if first.lower() == "nan": first = ""
            if last.lower()  == "nan": last  = ""
            if email.lower() == "nan": email = ""

            records.append((uid, first, last, email, str(row.get(cols.get("signed up"), "")), str(row.get(cols.get("latest activity"), ""))))

        with get_connection() as conn:
            cursor = conn.cursor()
            for uid, first, last, email, signed_up, latest_activity in records:
                if email:
                    em_lower = email.lower()
                    cursor.execute("SELECT user_id FROM students WHERE user_id=?", (uid,))
                    if cursor.fetchone():
                        # Real UID already exists. Delete the placeholder email UID to prevent duplicates
                        cursor.execute("DELETE FROM students WHERE user_id=?", (em_lower,))
                    else:
                        # Rename the placeholder to the Real UID so we inherit Master List org data
                        cursor.execute("UPDATE OR IGNORE students SET user_id=? WHERE user_id=?", (uid, em_lower))

                cursor.execute(
                    '''
                    INSERT INTO students (user_id, first_name, last_name, email, signed_up, latest_activity)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        first_name     = excluded.first_name,
                        last_name      = excluded.last_name,
                        email          = excluded.email
                    ''', (uid, first, last, email, signed_up, latest_activity)
                )

    # ── Snapshot Archiving ─────────────────────────────────────────────────────
    
    def create_monthly_snapshot(self, year: int, month: int):
        with get_connection() as conn:
            conn.execute("DELETE FROM completions_history WHERE year=? AND month=?", (year, month))
            conn.execute("""
                INSERT INTO completions_history (user_id, course_id, year, month, percent_complete)
                SELECT user_id, course_id, ?, ?, percent_complete FROM completions
            """, (year, month))

    # ── Skilljar metric files ──────────────────────────────────────────────────

    def import_skilljar_metrics(self, folder_path: str):
        metric_files = glob.glob(os.path.join(folder_path, "**", "Skilljar Metric*"), recursive=True)
        if not metric_files: metric_files = glob.glob(os.path.join(folder_path, "Skilljar Metric*"))

        records = []
        for file in set(metric_files):
            df = load_dataframe(file)
            if df is None: continue
            cols = {str(c).lower().strip(): c for c in df.columns}
            email_col = cols.get("email")
            if not email_col: continue

            df = df.dropna(subset=[email_col])
            df = df[df[email_col].astype(str).str.contains("@", na=False)]

            for _, row in df.iterrows():
                email    = str(row.get(email_col, "")).strip()
                location = str(row.get(cols.get("location", "Location"), "N/A")).strip()
                title    = str(row.get(cols.get("title",    "Title"),    "N/A")).strip()
                manager  = str(row.get(cols.get("manager",  "Manager"),  "N/A")).strip()

                if location.lower() == "nan": location = "N/A"
                if title.lower()    == "nan": title    = "N/A"
                if manager.lower()  == "nan": manager  = "N/A"

                records.append((location, title, manager, parse_region(location), email))

        with get_connection() as conn:
            conn.executemany("UPDATE students SET location=?, title=?, manager=?, region_bucket=? WHERE LOWER(email)=LOWER(?)", records)

    # ── Course completion files ────────────────────────────────────────────────

    def import_course_files(self, folder_path: str):
        all_files = [
            os.path.join(root, f) for root, _, files in os.walk(folder_path) for f in files
            if f.lower().endswith((".csv", ".xlsx", ".xls"))
        ]

        course_records     = []
        completion_records = []

        for file in all_files:
            filename = os.path.basename(file).lower()
            if filename in ("students.csv", "students.xlsx") or filename.startswith("skilljar metric"): continue

            df = load_dataframe(file)
            if df is None: continue

            cols = {str(c).lower().strip(): c for c in df.columns}
            uid_col        = cols.get("user id")
            course_id_col  = cols.get("course id",   cols.get("course name",  cols.get("course title")))
            course_name_col = cols.get("course name", cols.get("course title", cols.get("course id")))
            pct_col        = cols.get("% complete",  cols.get("percent complete"))

            if not uid_col or not course_id_col: continue

            for _, row in df.iterrows():
                user_id    = row.get(uid_col)
                course_id  = row.get(course_id_col)
                course_name = row.get(course_name_col)

                if pd.isna(user_id) or pd.isna(course_id): continue

                uid_str        = normalize_uid(user_id)
                course_id_str  = str(course_id).strip()
                course_name_str = str(course_name).strip() if not pd.isna(course_name) else course_id_str
                clean_pct = safe_float(row.get(pct_col, 0) if pct_col else 0)

                course_records.append((course_id_str, course_name_str))
                completion_records.append((uid_str, course_id_str, clean_pct))

        with get_connection() as conn:
            # FIX: Prevent FOREIGN KEY constraint failure by silently adding missing UIDs
            unique_uids = list(set([r[0] for r in completion_records]))
            conn.executemany("INSERT OR IGNORE INTO students (user_id) VALUES (?)", [(u,) for u in unique_uids])

            conn.executemany(
                '''
                INSERT INTO courses (course_id, course_name)
                VALUES (?, ?)
                ON CONFLICT(course_id) DO UPDATE SET course_name = excluded.course_name
                ''', list(set(course_records))
            )
            conn.executemany(
                '''
                INSERT INTO completions (user_id, course_id, percent_complete)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, course_id) DO UPDATE SET percent_complete = excluded.percent_complete
                ''', completion_records
            )

            conn.execute('''
                UPDATE courses SET course_type = (
                    SELECT CASE 
                        WHEN SUM(CASE WHEN s.organization = 'FAE' THEN 1 ELSE 0 END) > 0 
                         AND SUM(CASE WHEN s.organization = 'Sales' THEN 1 ELSE 0 END) > 0 THEN 'Both'
                        WHEN SUM(CASE WHEN s.organization = 'FAE' THEN 1 ELSE 0 END) > 0 THEN 'FAE'
                        WHEN SUM(CASE WHEN s.organization = 'Sales' THEN 1 ELSE 0 END) > 0 THEN 'Sales'
                        ELSE 'Unknown'
                    END
                    FROM completions c
                    JOIN students s ON s.user_id = c.user_id
                    WHERE c.course_id = courses.course_id
                )
            ''')

    # ── Control codes ──────────────────────────────────────────────────────────

    def import_control_codes(self, folder_path: str):
        metric_files = glob.glob(os.path.join(folder_path, "**", "Skilljar Metric*"), recursive=True)
        if not metric_files: metric_files = glob.glob(os.path.join(folder_path, "Skilljar Metric*"))
        records = []

        for file in set(metric_files):
            df = load_dataframe(file, header_row=None)
            if df is None: continue

            try:
                first_row = [str(x).lower().strip() for x in df.iloc[0].values]
                if "control code" not in first_row and "email" not in first_row: continue

                try:
                    email_col_idx = first_row.index("email")
                except ValueError:
                    continue

                code_map = {}
                for search_row in range(1, min(6, len(df))):
                    row_vals = [str(df.iloc[search_row, c]).lower() for c in range(4)]
                    candidate = {}
                    for col_idx, val in enumerate(row_vals):
                        if "low" in val or "mid" in val: candidate[col_idx] = "Low & Mid Power"
                        elif "high" in val: candidate[col_idx] = "High Power"
                        elif "auto" in val: candidate[col_idx] = "Automotive"
                        elif "motor" in val: candidate[col_idx] = "Motor Driver"
                    if candidate:
                        code_map = candidate
                        data_start_row = search_row + 1
                        break

                if not code_map: continue

                for row_idx in range(data_start_row, len(df)):
                    email = str(df.iloc[row_idx, email_col_idx]).strip()
                    if not email or "@" not in email: continue

                    assigned_codes = [
                        code_name for col_idx, code_name in code_map.items()
                        if str(df.iloc[row_idx, col_idx]).strip() in ("1", "1.0", "1.00")
                    ]
                    if assigned_codes:
                        final = ", ".join(sorted(set(assigned_codes)))
                        records.append((final, email))

            except Exception as exc:
                print(f"[ERROR] parsing control codes from {file}: {exc}")

        with get_connection() as conn:
            conn.executemany("UPDATE students SET control_codes=? WHERE LOWER(email)=LOWER(?)", records)