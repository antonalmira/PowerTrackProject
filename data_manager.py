import pandas as pd
import difflib
import os
import glob
import re
from database import get_connection, setup_database

def parse_region(location_str):
    loc = str(location_str).lower()
    if re.search(r'\b(us|usa|canada|america|mexico|philippines)\b', loc): return 'Americas'
    if re.search(r'\b(sea|singapore|malaysia|india)\b', loc): return 'ISEA'
    if any(x in loc for x in ['europe', 'germany', 'italy', 'uk', 'switzerland', 'france', 'munich', 'spain', 'poland']): return 'Europe'
    if any(x in loc for x in ['china', 'beijing', 'shenzhen', 'shanghai', 'qingdao', 'chengdu', 'foshan', 'xiamen']): return 'China'
    if any(x in loc for x in ['taiwan', 'taipei']): return 'Taiwan'
    if any(x in loc for x in ['japan', 'tokyo', 'yokohama']): return 'Japan'
    if any(x in loc for x in ['korea', 'seoul']): return 'Korea'
    return 'Worldwide'

def clean_string_for_matching(text):
    if not isinstance(text, str): return ""
    text = text.lower()
    text = text.replace('-sales-students.xlsx', '').replace('-fae-students.xlsx', '')
    text = text.replace('students.xlsx', '').replace('-students.xlsx', '')
    text = text.replace('.xlsx', '').replace('.csv', '')
    text = text.replace('introduction to', 'introducing').replace('introduction-to', 'introducing')
    return re.sub(r'[^a-z0-9]', '', text)

def safe_float(val, default=0.0):
    try: return float(str(val).replace('%', '').strip())
    except (ValueError, TypeError): return default

def load_dataframe(file_path, header_row=0):
    try:
        # Fix: Fallback for any function calls that explicitly pass "infer"
        if header_row == "infer":
            header_row = 0
            
        if file_path.lower().endswith('.csv'):
            return pd.read_csv(file_path, low_memory=False, header=header_row)
        elif file_path.lower().endswith(('.xlsx', '.xls')):
            return pd.read_excel(file_path, header=header_row)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
    return None

class DataManager:
    def __init__(self):
        setup_database()

    def import_students(self, file_path):
        df = load_dataframe(file_path)
        if df is None: return
            
        cols = {str(c).lower().strip(): c for c in df.columns}
        uid_col = cols.get('user id')
        if not uid_col: return
            
        df = df.dropna(subset=[uid_col])
        
        with get_connection() as conn:
            cursor = conn.cursor()

            for _, row in df.iterrows():
                uid = str(row.get(uid_col, '')).strip()
                if uid.endswith('.0'): uid = uid[:-2] 
                
                first = str(row.get(cols.get('first name', 'First name'), '')).strip()
                last = str(row.get(cols.get('last name', 'Last name'), '')).strip()
                email = str(row.get(cols.get('email', 'Email'), '')).strip()

                if first.lower() == 'nan': first = ""
                if last.lower() == 'nan': last = ""
                if email.lower() == 'nan': email = ""

                if not first and not last and '@' in email:
                    prefix = email.split('@')[0]
                    parts = prefix.split('.')
                    if len(parts) >= 2: first, last = parts[0].title(), parts[-1].title() 
                    else: first = parts[0].title()

                cursor.execute('''
                    INSERT INTO students (user_id, first_name, last_name, email, signed_up, latest_activity)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        first_name=excluded.first_name, last_name=excluded.last_name, email=excluded.email
                ''', (uid, first, last, email, str(row.get(cols.get('signed up'), '')), str(row.get(cols.get('latest activity'), ''))))

    def import_skilljar_metrics(self, folder_path):
        metric_files = glob.glob(os.path.join(folder_path, "**", "Skilljar Metric*"), recursive=True)
        if not metric_files: metric_files = glob.glob(os.path.join(folder_path, "Skilljar Metric*"))
            
        with get_connection() as conn:
            cursor = conn.cursor()

            for file in set(metric_files):
                df = load_dataframe(file)
                if df is None: continue

                cols = {str(c).lower().strip(): c for c in df.columns}
                email_col = cols.get('email')
                if not email_col: continue

                df = df.dropna(subset=[email_col])
                df = df[df[email_col].astype(str).str.contains('@', na=False)]

                for _, row in df.iterrows():
                    email = str(row.get(email_col, '')).strip()
                    location = str(row.get(cols.get('location', 'Location'), 'N/A')).strip()
                    title = str(row.get(cols.get('title', 'Title'), 'N/A')).strip()
                    manager = str(row.get(cols.get('manager', 'Manager'), 'N/A')).strip()
                    
                    if location.lower() == 'nan': location = 'N/A'
                    region_bucket = parse_region(location)

                    cursor.execute('''
                        UPDATE students SET location = ?, title = ?, manager = ?, region_bucket = ? WHERE email = ?
                    ''', (location, title, manager, region_bucket, email))

    def import_course_files(self, folder_path):
        all_files = []
        for root, _, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith(('.csv', '.xlsx', '.xls')):
                    all_files.append(os.path.join(root, file))

        with get_connection() as conn:
            cursor = conn.cursor()

            for file in all_files:
                filename = os.path.basename(file).lower()
                if filename in ['students.csv', 'students.xlsx'] or filename.startswith('skilljar metric'): continue
                    
                df = load_dataframe(file)
                if df is None: continue
                    
                cols = {str(c).lower().strip(): c for c in df.columns}
                uid_col = cols.get('user id')
                course_id_col = cols.get('course id', cols.get('course name', cols.get('course title')))
                course_name_col = cols.get('course name', cols.get('course title', cols.get('course id')))
                pct_col = cols.get('% complete', cols.get('percent complete'))
                
                if not uid_col or not course_id_col: continue

                org_label = "FAE" if "fae" in filename else "Sales" if "sales" in filename else "Unknown"

                for _, row in df.iterrows():
                    user_id, course_id, course_name = row.get(uid_col), row.get(course_id_col), row.get(course_name_col)
                    if pd.isna(user_id) or pd.isna(course_id): continue
                    
                    uid_str = str(user_id).strip()
                    if uid_str.endswith('.0'): uid_str = uid_str[:-2]
                    course_id_str = str(course_id).strip()
                    course_name_str = str(course_name).strip() if not pd.isna(course_name) else course_id_str
                            
                    cursor.execute('''
                        INSERT INTO courses (course_id, course_name, course_type) VALUES (?, ?, ?) 
                        ON CONFLICT(course_id) DO UPDATE SET 
                            course_name = excluded.course_name,
                            course_type = CASE WHEN course_type = 'Unknown' THEN excluded.course_type WHEN course_type != excluded.course_type AND excluded.course_type != 'Unknown' THEN 'Both' ELSE course_type END
                    ''', (course_id_str, course_name_str, org_label))

                    if org_label != "Unknown":
                        cursor.execute("UPDATE students SET organization = ? WHERE user_id = ? AND (organization = 'Unknown' OR organization IS NULL)", (org_label, uid_str))

                    clean_pct = safe_float(row.get(pct_col, 0))

                    cursor.execute('''
                        INSERT INTO completions (user_id, course_id, percent_complete)
                        VALUES (?, ?, ?) ON CONFLICT(user_id, course_id) DO UPDATE SET percent_complete=excluded.percent_complete
                    ''', (uid_str, course_id_str, clean_pct))

    def import_control_codes(self, folder_path):
        metric_files = glob.glob(os.path.join(folder_path, "**", "Skilljar Metric*"), recursive=True)
        if not metric_files: 
            metric_files = glob.glob(os.path.join(folder_path, "Skilljar Metric*"))
            
        with get_connection() as conn:
            cursor = conn.cursor()

            for file in set(metric_files):
                df = load_dataframe(file, header_row=None)
                if df is None: continue
                
                try:
                    first_row = [str(x).lower().strip() for x in df.iloc[0].values]
                    if 'control code' not in first_row and 'email' not in first_row: 
                        continue 
                    
                    try: 
                        email_col_idx = first_row.index('email')
                    except ValueError: 
                        continue

                    code_map = {}
                    for col_idx in range(4):
                        header_val = str(df.iloc[3, col_idx]).lower()
                        if 'low' in header_val or 'mid' in header_val: code_map[col_idx] = "Low & Mid Power"
                        elif 'high' in header_val: code_map[col_idx] = "High Power"
                        elif 'auto' in header_val: code_map[col_idx] = "Automotive"
                        elif 'motor' in header_val: code_map[col_idx] = "Motor Driver"

                    if not code_map: continue

                    for row_idx in range(4, len(df)):
                        email = str(df.iloc[row_idx, email_col_idx]).strip()
                        if not email or '@' not in email: continue

                        assigned_codes = []
                        for col_idx, code_name in code_map.items():
                            val = str(df.iloc[row_idx, col_idx]).strip()
                            if val in ['1', '1.0', '1.00']: 
                                assigned_codes.append(code_name)

                        if assigned_codes:
                            final_codes_str = ", ".join(sorted(list(set(assigned_codes))))
                            cursor.execute("UPDATE students SET control_codes=? WHERE LOWER(email)=LOWER(?)", (final_codes_str, email))

                except Exception as e:
                    print(f"Error parsing control codes from {file}: {e}")

    def assign_course_categories(self, folder_path):
        metric_files = glob.glob(os.path.join(folder_path, "**", "Skilljar Metric*"), recursive=True)
        if not metric_files: 
            metric_files = glob.glob(os.path.join(folder_path, "Skilljar Metric*"))
        
        if not metric_files: return 
            
        with get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT course_id, course_name FROM courses")
            db_courses = cursor.fetchall()
            
            db_course_dict = {}
            for cid, cname in db_courses:
                db_course_dict[clean_string_for_matching(cname)] = cid
                db_course_dict[clean_string_for_matching(cid)] = cid 
            
            for file in set(metric_files):
                df = load_dataframe(file, header_row=None)
                if df is None: continue
                
                try:
                    first_row = [str(x).lower().strip() for x in df.iloc[0].values]
                    if 'all course' not in first_row: continue 
                    
                    start_col = first_row.index('all course') + 1
                    
                    current_cc = "Unassigned"
                    current_gc = "Unassigned"
                    
                    for col_idx in range(start_col, len(df.columns)):
                        cc_val = str(df.iloc[1, col_idx]).strip()
                        if cc_val and cc_val.lower() != 'nan':
                            if "low" in cc_val.lower() or "mid" in cc_val.lower(): current_cc = "Low & Mid Power"
                            elif "motor" in cc_val.lower(): current_cc = "Motor Driver"
                            elif "auto" in cc_val.lower(): current_cc = "Automotive"
                            elif "high" in cc_val.lower(): current_cc = "High Power"
                            
                        gc_val = str(df.iloc[2, col_idx]).strip()
                        if gc_val and gc_val.lower() != 'nan':
                            current_gc = gc_val
                            
                        course_name = str(df.iloc[3, col_idx]).strip()
                        if not course_name or course_name.lower() in ['nan', 'summary', 'course completion rate', 'category completion rate', 'all course completion']:
                            continue
                            
                        cleaned_csv_name = clean_string_for_matching(course_name)
                        closest_matches = difflib.get_close_matches(cleaned_csv_name, list(db_course_dict.keys()), n=1, cutoff=0.5)
                        
                        if closest_matches:
                            matched_cid = db_course_dict[closest_matches[0]]
                            cursor.execute('UPDATE courses SET control_code = ?, graph_category = ? WHERE course_id = ?', 
                                          (current_cc, current_gc, matched_cid))
                            
                except Exception as e:
                    print(f"Error parsing master CSV categories: {e}")
        