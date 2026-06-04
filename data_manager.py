import csv
import pandas as pd
import difflib
import os
import glob
import re
from database import get_connection, setup_database


# ── Configurable constants ─────────────────────────────────────────────────────

# FIX: internal-domain check made configurable instead of hardcoded.
INTERNAL_DOMAINS = ["@power.com", "@powerint.com"]

# FIX: fuzzy-match cutoff raised from 0.5 to 0.75 to prevent
# cross-category misassignments (e.g. InnoSwitch ↔ LinkSwitch).
FUZZY_CUTOFF = 0.75


# ── Shared utilities ───────────────────────────────────────────────────────────

def normalize_uid(uid) -> str:
    """
    FIX: centralised UID normalisation — was copy-pasted across 8+ call sites.
    Converts float-formatted IDs ('12345.0') to plain strings ('12345').
    """
    s = str(uid).strip()
    return s[:-2] if s.endswith(".0") else s


def parse_region(location_str: str) -> str:
    """
    FIX: uses word-boundary regex instead of bare substring matching.
    Previously 'focus', 'various', 'caucasus' could falsely match 'us'.
    """
    loc = str(location_str).lower()

    def _match(words):
        return any(re.search(r"\b" + re.escape(w) + r"\b", loc) for w in words)

    if _match(["india", "sea", "singapore", "malaysia", "isea", "philippines",
               "vietnam", "thailand", "indonesia", "australia", "new zealand"]):
        return "ISEA"
    if _match(["usa", "canada", "america", "mexico", "brazil", "argentina",
               "san jose", "california", "texas", "boston"]):
        return "Americas"
    # 'us' kept last in its own check to avoid false positives
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
    if not isinstance(text, str):
        return ""
    text = text.lower()
    text = text.replace("-sales-students.xlsx", "").replace("-fae-students.xlsx", "")
    text = text.replace("students.xlsx", "").replace("-students.xlsx", "")
    text = text.replace(".xlsx", "").replace(".csv", "")
    text = text.replace("introduction to", "introducing").replace(
        "introduction-to", "introducing"
    )
    return re.sub(r"[^a-z0-9]", "", text)


def safe_float(val, default=0.0) -> float:
    try:
        return float(str(val).replace("%", "").strip())
    except (ValueError, TypeError):
        return default


def load_dataframe(file_path: str, header_row=0):
    """
    FIX: guard against callers that pass header_row='infer' (legacy calls).
    """
    try:
        if header_row == "infer":
            header_row = 0
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

    # ── Students ───────────────────────────────────────────────────────────────

    def import_students(self, file_path: str):
        df = load_dataframe(file_path)
        if df is None:
            return

        cols = {str(c).lower().strip(): c for c in df.columns}
        uid_col = cols.get("user id")
        if not uid_col:
            return

        df = df.dropna(subset=[uid_col])

        # FIX: build a list of tuples then use executemany — avoids one
        # round-trip per row; significantly faster for large student files.
        records = []
        for _, row in df.iterrows():
            uid = normalize_uid(row.get(uid_col, ""))
            if not uid:
                continue

            first = str(row.get(cols.get("first name", "First name"), "")).strip()
            last  = str(row.get(cols.get("last name",  "Last name"),  "")).strip()
            email = str(row.get(cols.get("email",      "Email"),      "")).strip()

            if first.lower() == "nan": first = ""
            if last.lower()  == "nan": last  = ""
            if email.lower() == "nan": email = ""

            if not first and not last and "@" in email:
                prefix = email.split("@")[0]
                parts  = prefix.split(".")
                first  = parts[0].title()
                last   = parts[-1].title() if len(parts) >= 2 else ""

            records.append((
                uid, first, last, email,
                str(row.get(cols.get("signed up"),        "")),
                str(row.get(cols.get("latest activity"),  "")),
            ))

        with get_connection() as conn:
            conn.executemany(
                '''
                INSERT INTO students (user_id, first_name, last_name, email,
                                      signed_up, latest_activity)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name     = excluded.first_name,
                    last_name      = excluded.last_name,
                    email          = excluded.email
                ''',
                records,
            )

    # ── Skilljar metric files (location / title / manager) ────────────────────

    def import_skilljar_metrics(self, folder_path: str):
        metric_files = glob.glob(
            os.path.join(folder_path, "**", "Skilljar Metric*"), recursive=True
        )
        if not metric_files:
            metric_files = glob.glob(
                os.path.join(folder_path, "Skilljar Metric*")
            )

        records = []
        for file in set(metric_files):
            df = load_dataframe(file)
            if df is None:
                continue

            cols = {str(c).lower().strip(): c for c in df.columns}
            email_col = cols.get("email")
            if not email_col:
                continue

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
            conn.executemany(
                "UPDATE students SET location=?, title=?, manager=?, region_bucket=? WHERE email=?",
                records,
            )

    # ── Course completion files ────────────────────────────────────────────────

    def import_course_files(self, folder_path: str):
        all_files = [
            os.path.join(root, f)
            for root, _, files in os.walk(folder_path)
            for f in files
            if f.lower().endswith((".csv", ".xlsx", ".xls"))
        ]

        course_records     = []
        student_org_records = []
        completion_records = []

        for file in all_files:
            filename = os.path.basename(file).lower()
            if filename in ("students.csv", "students.xlsx") or filename.startswith(
                "skilljar metric"
            ):
                continue

            df = load_dataframe(file)
            if df is None:
                continue

            cols = {str(c).lower().strip(): c for c in df.columns}
            uid_col        = cols.get("user id")
            course_id_col  = cols.get("course id",   cols.get("course name",  cols.get("course title")))
            course_name_col = cols.get("course name", cols.get("course title", cols.get("course id")))
            pct_col        = cols.get("% complete",  cols.get("percent complete"))

            if not uid_col or not course_id_col:
                continue

            org_label = (
                "FAE"   if "fae"   in filename else
                "Sales" if "sales" in filename else
                "Unknown"
            )

            for _, row in df.iterrows():
                user_id    = row.get(uid_col)
                course_id  = row.get(course_id_col)
                course_name = row.get(course_name_col)

                if pd.isna(user_id) or pd.isna(course_id):
                    continue

                uid_str        = normalize_uid(user_id)
                course_id_str  = str(course_id).strip()
                course_name_str = (
                    str(course_name).strip()
                    if not pd.isna(course_name)
                    else course_id_str
                )
                clean_pct = safe_float(row.get(pct_col, 0) if pct_col else 0)

                course_records.append((course_id_str, course_name_str, org_label))
                if org_label != "Unknown":
                    student_org_records.append((org_label, uid_str))
                completion_records.append((uid_str, course_id_str, clean_pct))

        with get_connection() as conn:
            conn.executemany(
                '''
                INSERT INTO courses (course_id, course_name, course_type)
                VALUES (?, ?, ?)
                ON CONFLICT(course_id) DO UPDATE SET
                    course_name = excluded.course_name,
                    course_type = CASE
                        WHEN course_type = 'Unknown'                                   THEN excluded.course_type
                        WHEN course_type != excluded.course_type
                             AND excluded.course_type != 'Unknown'                     THEN 'Both'
                        ELSE course_type
                    END
                ''',
                course_records,
            )
            conn.executemany(
                "UPDATE students SET organization=? WHERE user_id=? AND (organization='Unknown' OR organization IS NULL)",
                student_org_records,
            )
            conn.executemany(
                '''
                INSERT INTO completions (user_id, course_id, percent_complete)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, course_id) DO UPDATE SET
                    percent_complete = excluded.percent_complete
                ''',
                completion_records,
            )

    # ── Control codes ──────────────────────────────────────────────────────────

    def import_control_codes(self, folder_path: str):
        metric_files = glob.glob(
            os.path.join(folder_path, "**", "Skilljar Metric*"), recursive=True
        )
        if not metric_files:
            metric_files = glob.glob(os.path.join(folder_path, "Skilljar Metric*"))

        records = []

        for file in set(metric_files):
            df = load_dataframe(file, header_row=None)
            if df is None:
                continue

            try:
                # FIX: search for the header row dynamically instead of assuming
                # fixed row positions (row 0 = header, row 3 = codes).
                first_row = [str(x).lower().strip() for x in df.iloc[0].values]
                if "control code" not in first_row and "email" not in first_row:
                    continue

                try:
                    email_col_idx = first_row.index("email")
                except ValueError:
                    continue

                # Dynamically find the row that contains the code-column headers
                code_map = {}
                for search_row in range(1, min(6, len(df))):
                    row_vals = [str(df.iloc[search_row, c]).lower() for c in range(4)]
                    candidate = {}
                    for col_idx, val in enumerate(row_vals):
                        if "low" in val or "mid" in val:
                            candidate[col_idx] = "Low & Mid Power"
                        elif "high" in val:
                            candidate[col_idx] = "High Power"
                        elif "auto" in val:
                            candidate[col_idx] = "Automotive"
                        elif "motor" in val:
                            candidate[col_idx] = "Motor Driver"
                    if candidate:
                        code_map = candidate
                        data_start_row = search_row + 1
                        break

                if not code_map:
                    continue

                for row_idx in range(data_start_row, len(df)):
                    email = str(df.iloc[row_idx, email_col_idx]).strip()
                    if not email or "@" not in email:
                        continue

                    assigned_codes = [
                        code_name
                        for col_idx, code_name in code_map.items()
                        if str(df.iloc[row_idx, col_idx]).strip() in ("1", "1.0", "1.00")
                    ]
                    if assigned_codes:
                        final = ", ".join(sorted(set(assigned_codes)))
                        records.append((final, email))

            except Exception as exc:
                print(f"[ERROR] parsing control codes from {file}: {exc}")

        with get_connection() as conn:
            conn.executemany(
                "UPDATE students SET control_codes=? WHERE LOWER(email)=LOWER(?)",
                records,
            )

    # ── Course category assignment ─────────────────────────────────────────────

    def assign_course_categories(self, folder_path: str):
        metric_files = glob.glob(
            os.path.join(folder_path, "**", "Skilljar Metric*"), recursive=True
        )
        if not metric_files:
            metric_files = glob.glob(os.path.join(folder_path, "Skilljar Metric*"))
        if not metric_files:
            return

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT course_id, course_name FROM courses")
            db_courses = cursor.fetchall()

        db_course_dict = {}
        for cid, cname in db_courses:
            db_course_dict[clean_string_for_matching(cname)] = cid
            db_course_dict[clean_string_for_matching(cid)]   = cid

        update_records = []

        for file in set(metric_files):
            df = load_dataframe(file, header_row=None)
            if df is None:
                continue

            try:
                first_row = [str(x).lower().strip() for x in df.iloc[0].values]
                if "all course" not in first_row:
                    continue

                start_col   = first_row.index("all course") + 1
                current_cc  = "Unassigned"
                current_gc  = "Unassigned"

                for col_idx in range(start_col, len(df.columns)):
                    cc_val = str(df.iloc[1, col_idx]).strip()
                    if cc_val and cc_val.lower() != "nan":
                        if "low" in cc_val.lower() or "mid" in cc_val.lower():
                            current_cc = "Low & Mid Power"
                        elif "motor" in cc_val.lower():
                            current_cc = "Motor Driver"
                        elif "auto" in cc_val.lower():
                            current_cc = "Automotive"
                        elif "high" in cc_val.lower():
                            current_cc = "High Power"

                    gc_val = str(df.iloc[2, col_idx]).strip()
                    if gc_val and gc_val.lower() != "nan":
                        current_gc = gc_val

                    course_name = str(df.iloc[3, col_idx]).strip()
                    if not course_name or course_name.lower() in (
                        "nan", "summary", "course completion rate",
                        "category completion rate", "all course completion",
                    ):
                        continue

                    cleaned = clean_string_for_matching(course_name)
                    # FIX: cutoff raised from 0.5 → FUZZY_CUTOFF (0.75).
                    matches = difflib.get_close_matches(
                        cleaned, list(db_course_dict.keys()), n=1, cutoff=FUZZY_CUTOFF
                    )
                    if matches:
                        matched_cid = db_course_dict[matches[0]]
                        update_records.append((current_cc, current_gc, matched_cid))
                    else:
                        print(f"[WARN] No fuzzy match for course: '{course_name}'")

            except Exception as exc:
                print(f"[ERROR] parsing master CSV categories from {file}: {exc}")

        with get_connection() as conn:
            conn.executemany(
                "UPDATE courses SET control_code=?, graph_category=? WHERE course_id=?",
                update_records,
            )