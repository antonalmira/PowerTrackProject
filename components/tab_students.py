from PyQt5.QtWidgets import (
    QTableWidgetItem, QHeaderView, QApplication, QDialog,
    QComboBox, QCheckBox, QMessageBox, QMenu, QWidgetAction,
    QFileDialog, QLineEdit, QDoubleSpinBox, QTableWidget,
)
from PyQt5.QtGui import QStandardItemModel, QStandardItem, QColor
from PyQt5.QtCore import Qt, QSettings, pyqtSignal, QThread
from PyQt5 import uic
import pandas as pd
import sqlite3
import os
import json
import glob

from database import DB_PATH
from data_manager import (
    DataManager, parse_region, load_dataframe, safe_float, normalize_uid,
    INTERNAL_DOMAINS,
)

# ── Email safety constants ─────────────────────────────────────────────────────
# FIX: cap the number of recipients per draft and require confirmation.
MAX_EMAIL_RECIPIENTS = 50

# ── Default progress rules ─────────────────────────────────────────────────────
DEFAULT_RULES = [
    {
        "name": "Up-to-Date",
        "metric": "Overall Completion %",
        "operator": "==",
        "value": 100.0,
        "subject": "Power Integrations Training - All Modules Complete!",
        "body": "Hi Team,<br><br>Thank you for keeping your training 100% up to date. We appreciate your dedication!",
    },
    {
        "name": "No Action / New",
        "metric": "Overall Completion %",
        "operator": "==",
        "value": 0.0,
        "subject": "Power Integrations Training - Action Required",
        "body": "Hi Team,<br><br>You have pending training modules that have not been started. Please log in to the portal and begin your training as soon as possible.",
    },
    {
        "name": "Progressed This Month",
        "metric": "Monthly Progress %",
        "operator": ">",
        "value": 0.0,
        "subject": "Power Integrations Training - Great Progress!",
        "body": "Hi Team,<br><br>Great job on the progress you've made this month. Keep up the good work and let us know if you need any support.",
    },
    {
        "name": "Missed New Module",
        "metric": "Missed New Modules",
        "operator": "==",
        "value": 1.0,
        "subject": "Power Integrations Training - New Module Assigned",
        "body": "Hi Team,<br><br>A new training module has been added to your curriculum. Please log in to complete this new requirement.",
    },
    {
        "name": "Stagnant Students",
        "metric": "Default",
        "operator": "==",
        "value": 0.0,
        "subject": "Power Integrations Training - Pending Modules",
        "body": "Hi Team,<br><br>We noticed your training progress has been stagnant recently. Please log in to the portal and continue your assigned modules.",
    },
]


# ── Background worker for progress calculation ─────────────────────────────────

class ProgressWorker(QThread):
    """
    FIX: calculate_progress() was blocking the main thread.  Moved to a
    QThread so the UI remains responsive while files are read.
    """
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, component, base_folder, update_folder):
        super().__init__()
        self._component    = component
        self._base_folder  = base_folder
        self._update_folder = update_folder

    def run(self):
        try:
            result = self._component.calculate_progress(
                self._base_folder, self._update_folder
            )
            self.done.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ── Template editor dialog ─────────────────────────────────────────────────────

class TemplateEditorDialog(QDialog):
    def __init__(self, rules, parent=None):
        super().__init__(parent)
        uic.loadUi(
            os.path.join(os.path.dirname(__file__), "..", "dialog_email_templates.ui"),
            self,
        )
        self.rules = rules

        self.combo_category.addItems([r["name"] for r in self.rules])
        self.combo_category.currentTextChanged.connect(self.load_template)

        self.edit_subject.textChanged.connect(self.save_current_template)
        self.edit_body.textChanged.connect(self.save_current_template)

        if self.rules:
            self.load_template(self.rules[0]["name"])

    def load_template(self, category_name):
        self.edit_subject.blockSignals(True)
        self.edit_body.blockSignals(True)

        for rule in self.rules:
            if rule["name"] == category_name:
                self.edit_subject.setText(
                    rule.get("subject", "Power Integrations Training Update")
                )
                self.edit_body.setHtml(
                    rule.get("body", "Hi Team,<br><br>Please check your training portal.")
                )
                break

        self.edit_subject.blockSignals(False)
        self.edit_body.blockSignals(False)

    def save_current_template(self):
        category_name = self.combo_category.currentText()
        for rule in self.rules:
            if rule["name"] == category_name:
                rule["subject"] = self.edit_subject.text()
                rule["body"]    = self.edit_body.toHtml()
                break


# ── Progress logic dialog ──────────────────────────────────────────────────────

class ProgressLogicDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        uic.loadUi(
            os.path.join(os.path.dirname(__file__), "..", "dialog_progress_logic.ui"),
            self,
        )

        self.settings = QSettings("PowerIntegrations", "PowerTrack")
        self.rules = json.loads(
            self.settings.value("progress_rules", json.dumps(DEFAULT_RULES))
        )

        for r in self.rules:
            if "subject" not in r:
                r["subject"] = "Power Integrations Training Update"
            if "body" not in r:
                r["body"] = "Hi Team,<br><br>Please check your training portal."

        self.table_rules.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_rules.setSelectionBehavior(QTableWidget.SelectRows)

        self.btn_add.clicked.connect(self.add_rule)
        self.btn_delete.clicked.connect(self.delete_rule)
        self.btn_up.clicked.connect(self.move_up)
        self.btn_down.clicked.connect(self.move_down)
        self.btn_edit_templates.clicked.connect(self.open_template_editor)
        self.buttonBox.accepted.connect(self.save_rules)

        self.populate_table()

    def populate_table(self):
        self.table_rules.setRowCount(0)
        for rule in self.rules:
            self.add_rule_row(rule)

    def add_rule_row(self, rule=None):
        if rule is None:
            rule = {
                "name": "New Category",
                "metric": "Monthly Progress %",
                "operator": ">=",
                "value": 20.0,
                "subject": "Training Update",
                "body": "Please review your training.",
            }

        row = self.table_rules.rowCount()
        self.table_rules.insertRow(row)

        name_edit = QLineEdit(rule["name"])
        name_edit.setStyleSheet(
            "background: transparent; color: white; border: none; padding: 5px;"
        )
        self.table_rules.setCellWidget(row, 0, name_edit)

        metric_combo = QComboBox()
        metric_combo.addItems(
            ["Overall Completion %", "Monthly Progress %", "Missed New Modules", "Default"]
        )
        metric_combo.setCurrentText(rule["metric"])
        self.table_rules.setCellWidget(row, 1, metric_combo)

        op_combo = QComboBox()
        op_combo.addItems([">", ">=", "<", "<=", "=="])
        op_combo.setCurrentText(rule["operator"])
        self.table_rules.setCellWidget(row, 2, op_combo)

        val_spin = QDoubleSpinBox()
        val_spin.setRange(-100, 100)
        val_spin.setValue(float(rule["value"]))
        self.table_rules.setCellWidget(row, 3, val_spin)

    def add_rule(self):
        self.add_rule_row()

    def delete_rule(self):
        row = self.table_rules.currentRow()
        if row >= 0:
            self.table_rules.removeRow(row)

    def move_up(self):
        row = self.table_rules.currentRow()
        if row > 0:
            self.sync_ui_to_rules()
            self.rules[row], self.rules[row - 1] = self.rules[row - 1], self.rules[row]
            self.populate_table()
            self.table_rules.selectRow(row - 1)

    def move_down(self):
        row = self.table_rules.currentRow()
        if row >= 0 and row < self.table_rules.rowCount() - 1:
            self.sync_ui_to_rules()
            self.rules[row], self.rules[row + 1] = self.rules[row + 1], self.rules[row]
            self.populate_table()
            self.table_rules.selectRow(row + 1)

    def get_row_data(self, row):
        current_name = self.table_rules.cellWidget(row, 0).text()

        subj = "Power Integrations Training Update"
        body = "Hi Team,<br><br>Please check your training portal."
        for r in self.rules:
            if r["name"] == current_name:
                subj = r.get("subject", subj)
                body = r.get("body", body)
                break

        return {
            "name":     current_name,
            "metric":   self.table_rules.cellWidget(row, 1).currentText(),
            "operator": self.table_rules.cellWidget(row, 2).currentText(),
            "value":    self.table_rules.cellWidget(row, 3).value(),
            "subject":  subj,
            "body":     body,
        }

    def sync_ui_to_rules(self):
        self.rules = [
            self.get_row_data(i) for i in range(self.table_rules.rowCount())
        ]

    def open_template_editor(self):
        self.sync_ui_to_rules()
        editor = TemplateEditorDialog(self.rules, self)
        editor.exec_()

    def save_rules(self):
        self.sync_ui_to_rules()
        self.settings.setValue("progress_rules", json.dumps(self.rules))


# ── Multi-select combobox ──────────────────────────────────────────────────────

class CheckableComboBox(QComboBox):
    selection_changed = pyqtSignal(str, str)

    def __init__(self, user_id, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self.setModel(QStandardItemModel(self))
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setAlignment(Qt.AlignCenter)
        self.view().pressed.connect(self.handle_item_pressed)
        self._changed = False

    def handle_item_pressed(self, index):
        item = self.model().itemFromIndex(index)
        item.setCheckState(
            Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
        )
        self._changed = True
        self.update_text()

    def set_items(self, items, checked_items):
        self.clear()
        for text in items:
            item = QStandardItem(text)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setData(
                Qt.Checked if text in checked_items else Qt.Unchecked,
                Qt.CheckStateRole,
            )
            self.model().appendRow(item)
        self.update_text()
        self._changed = False

    def update_text(self):
        checked = [
            self.model().item(i).text()
            for i in range(self.model().rowCount())
            if self.model().item(i).checkState() == Qt.Checked
        ]
        text = ", ".join(checked)
        self.lineEdit().setText(text)
        return text

    def hidePopup(self):
        super().hidePopup()
        if self._changed:
            self.selection_changed.emit(self.user_id, self.update_text())
            self._changed = False


# ── Main student tab component ─────────────────────────────────────────────────

class StudentTabComponent:
    def __init__(self, ui_window):
        self.ui                  = ui_window
        self.is_loading          = True
        self.current_org_filter  = "FAE"
        self._progress_worker    = None

        self.settings            = QSettings("PowerIntegrations", "PowerTrack")
        self.current_data_folder = self.settings.value(
            "current_data_folder",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"),
        )
        self.base_data_folder = self.settings.value("base_data_folder", None)

        try:
            self.progress_dict = json.loads(
                self.settings.value("progress_dict", "{}")
            )
        except Exception:
            self.progress_dict = {}

        self.setup_ui_bindings()
        self.setup_table()
        self.setup_filters()
        self.load_students_to_table()

    # ── UI wiring ──────────────────────────────────────────────────────────────

    def setup_ui_bindings(self):
        self.ui.tabBar_students.addTab("FAE")
        self.ui.tabBar_students.addTab("Sales")
        self.ui.tabBar_students.addTab("Unknown")
        self.ui.tabBar_students.currentChanged.connect(self.on_tab_changed)

        self.ui.btn_update_status.clicked.connect(
            lambda: self.select_data_folder(is_update=False)
        )
        self.ui.btn_refresh.clicked.connect(
            lambda: self.select_data_folder(is_update=True)
        )

        if hasattr(self.ui, "btn_edit_progress"):
            self.ui.btn_edit_progress.clicked.connect(self.open_progress_logic_editor)

        if hasattr(self.ui, "btn_email_students"):
            self.ui.btn_email_students.clicked.connect(self.draft_emails)

        self.ui.table_students.itemChanged.connect(self.on_cell_edited)

    def setup_table(self):
        table = self.ui.table_students
        table.setColumnCount(9)
        table.setHorizontalHeaderLabels(
            ["Name", "Email", "Location", "Title", "Manager",
             "Organization", "Control Code", "Avg. Completion", "Progress"]
        )

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self.show_column_menu)

        table.verticalHeader().setMinimumWidth(50)
        for col, width in enumerate([180, 200, 180, 180, 160, 100, 200, 120]):
            table.setColumnWidth(col, width)
        header.setStretchLastSection(True)

    def setup_filters(self):
        self.ui.search_student.textChanged.connect(self.apply_filters)

        self.ui.combo_region.clear()
        self.ui.combo_region.addItems(
            ["All Regions", "Americas", "Europe", "China", "Taiwan",
             "Japan", "Korea", "ISEA", "Worldwide"]
        )
        self.ui.combo_region.currentTextChanged.connect(self.apply_filters)

        rules = json.loads(
            self.settings.value("progress_rules", json.dumps(DEFAULT_RULES))
        )
        unique_categories = list(dict.fromkeys(r["name"] for r in rules))

        # FIX: safe disconnect — only disconnect the specific slot to avoid
        # disconnecting all receivers; use try/except instead of receiver count.
        try:
            self.ui.combo_progress.currentTextChanged.disconnect(self.apply_filters)
        except TypeError:
            pass

        self.ui.combo_progress.clear()
        self.ui.combo_progress.addItem("All Status")
        self.ui.combo_progress.addItems(unique_categories)
        self.ui.combo_progress.addItem("N/A")
        self.ui.combo_progress.currentTextChanged.connect(self.apply_filters)

        self.ui.btn_clear_filters.clicked.connect(self.clear_filters)

    # ── Filters ────────────────────────────────────────────────────────────────

    def apply_filters(self):
        search_text     = self.ui.search_student.text().lower()
        target_region   = self.ui.combo_region.currentText()
        target_progress = self.ui.combo_progress.currentText()

        visible_count = 0
        table = self.ui.table_students

        for row in range(table.rowCount()):
            # FIX: guard against None cells during partial loads
            name_item  = table.item(row, 0)
            email_item = table.item(row, 1)
            region_item = table.item(row, 2)
            prog_item  = table.item(row, 8)

            name     = name_item.text().lower()  if name_item  else ""
            email    = email_item.text().lower() if email_item else ""
            region   = (region_item.data(Qt.UserRole) or "Worldwide") if region_item else "Worldwide"
            progress = prog_item.text() if prog_item else ""

            match_search   = (search_text in name) or (search_text in email)
            match_region   = target_region   == "All Regions" or region   == target_region
            match_progress = target_progress == "All Status"  or progress == target_progress

            visible = match_search and match_region and match_progress
            table.setRowHidden(row, not visible)
            if visible:
                visible_count += 1

        self.ui.lbl_showing.setText(
            f"Showing {visible_count} of {table.rowCount()} students"
        )

    def clear_filters(self):
        self.ui.search_student.clear()
        self.ui.combo_region.setCurrentIndex(0)
        self.ui.combo_progress.setCurrentIndex(0)

    def show_column_menu(self, pos):
        menu  = QMenu(self.ui)
        menu.setStyleSheet(
            "QMenu { background: #202025; color: white; border: 1px solid #3a3a40; }"
        )
        table = self.ui.table_students

        for c in range(table.columnCount()):
            text   = table.horizontalHeaderItem(c).text()
            action = QWidgetAction(menu)
            chk    = QCheckBox(text)
            chk.setStyleSheet(
                "QCheckBox { padding: 5px; background: transparent; color: white; }"
                "QCheckBox:hover { background: #0085ca; }"
            )
            chk.setChecked(not table.isColumnHidden(c))
            chk.toggled.connect(
                lambda checked, col=c: table.setColumnHidden(col, not checked)
            )
            action.setDefaultWidget(chk)
            menu.addAction(action)

        menu.exec_(table.horizontalHeader().viewport().mapToGlobal(pos))

    # ── Data folder selection ──────────────────────────────────────────────────

    def select_data_folder(self, is_update=False):
        dialog_title = (
            "Select Update Folder (e.g. data/feb)"
            if is_update
            else "Select Base Data Folder (e.g. data/jan)"
        )
        last_dir = self.settings.value("last_dialog_dir", self.current_data_folder)
        folder_path = QFileDialog.getExistingDirectory(
            self.ui, dialog_title, last_dir,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )

        if folder_path:
            self.settings.setValue("last_dialog_dir", os.path.dirname(folder_path))
            self.current_data_folder = folder_path
            self.settings.setValue("current_data_folder", folder_path)

            if not is_update:
                self.base_data_folder = folder_path
                self.settings.setValue("base_data_folder", folder_path)
                self.progress_dict = {}
                self.settings.setValue("progress_dict", "{}")

            self.run_data_ingestion_with_folder(folder_path, is_update)

    # ── Progress calculation ───────────────────────────────────────────────────

    def evaluate_rule(self, stats, rule):
        if rule["metric"] == "Default":
            return True
        val    = stats.get(rule["metric"], 0.0)
        target = float(rule["value"])
        op     = rule["operator"]

        if op == ">":  return val >  target
        if op == "<":  return val <  target
        if op == ">=": return val >= target
        if op == "<=": return val <= target
        if op == "==": return val == target
        return False

    def calculate_progress(self, base_folder, update_folder):
        """
        Calculates progress labels for all students.
        NOTE: runs on a background QThread — do NOT touch Qt widgets here.
        """
        if not base_folder or not os.path.exists(base_folder):
            return {}

        rules = json.loads(
            self.settings.value("progress_rules", json.dumps(DEFAULT_RULES))
        )

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, control_codes FROM students")
            student_assigned_codes = {
                normalize_uid(row[0]): (
                    [c.strip() for c in str(row[1]).split(",")]
                    if row[1] else []
                )
                for row in cursor.fetchall()
            }
            cursor.execute("SELECT course_id, control_code FROM courses")
            course_control_codes = {
                str(row[0]): str(row[1]) for row in cursor.fetchall()
            }
            cursor.execute("SELECT user_id, email FROM students")
            uid_to_email = {
                normalize_uid(row[0]): str(row[1]).strip().lower()
                for row in cursor.fetchall()
            }

        def get_completions(folder):
            user_courses, all_courses = {}, set()
            all_files = [
                os.path.join(root, f)
                for root, _, files in os.walk(folder)
                for f in files
                if f.lower().endswith((".csv", ".xlsx", ".xls"))
            ]
            for f in all_files:
                fname = os.path.basename(f).lower()
                if fname in ("students.csv", "students.xlsx") or fname.startswith(
                    "skilljar metric"
                ):
                    continue
                df = load_dataframe(f)
                if df is None:
                    continue

                cols      = {str(c).lower().strip(): c for c in df.columns}
                uid_col   = cols.get("user id")
                course_col = cols.get("course id", cols.get("course name", cols.get("course title")))
                pct_col   = cols.get("% complete", cols.get("percent complete"))

                if not uid_col or not course_col:
                    continue

                for _, row in df.iterrows():
                    uid_val = row.get(uid_col)
                    course  = row.get(course_col)
                    pct     = row.get(pct_col) if pct_col else 0

                    if pd.isna(uid_val) or pd.isna(course):
                        continue

                    uid_str    = normalize_uid(uid_val)
                    course_str = str(course).strip()

                    all_courses.add(course_str)
                    if uid_str not in user_courses:
                        user_courses[uid_str] = {}
                    user_courses[uid_str][course_str] = max(
                        user_courses[uid_str].get(course_str, 0.0), safe_float(pct)
                    )
            return user_courses, all_courses

        def get_base_control_codes(folder):
            metric_files = glob.glob(
                os.path.join(folder, "**", "Skilljar Metric*"), recursive=True
            ) or glob.glob(os.path.join(folder, "Skilljar Metric*"))

            email_to_codes = {}
            for file in set(metric_files):
                df = load_dataframe(file, header_row=None)
                if df is None:
                    continue
                try:
                    first_row = [str(x).lower().strip() for x in df.iloc[0].values]
                    if "control code" not in first_row and "email" not in first_row:
                        continue
                    email_col_idx = first_row.index("email")

                    code_map = {}
                    for col_idx in range(4):
                        val = str(df.iloc[3, col_idx]).lower()
                        if "low" in val or "mid" in val:
                            code_map[col_idx] = "Low & Mid Power"
                        elif "high" in val:
                            code_map[col_idx] = "High Power"
                        elif "auto" in val:
                            code_map[col_idx] = "Automotive"
                        elif "motor" in val:
                            code_map[col_idx] = "Motor Driver"

                    if not code_map:
                        continue

                    for row_idx in range(4, len(df)):
                        email = str(df.iloc[row_idx, email_col_idx]).strip().lower()
                        if not email or "@" not in email:
                            continue
                        assigned = [
                            cn
                            for ci, cn in code_map.items()
                            if str(df.iloc[row_idx, ci]).strip() in ("1", "1.0", "1.00")
                        ]
                        if assigned:
                            if email not in email_to_codes:
                                email_to_codes[email] = set()
                            email_to_codes[email].update(assigned)
                except Exception:
                    pass
            return email_to_codes

        base_data,   base_all_courses   = get_completions(base_folder)
        update_data, update_all_courses = get_completions(update_folder)
        base_email_codes = get_base_control_codes(base_folder)
        base_uid_codes   = {
            db_uid: base_email_codes[db_email]
            for db_uid, db_email in uid_to_email.items()
            if db_email in base_email_codes
        }

        progress_dict = {}
        for uid_str, my_required_codes in student_assigned_codes.items():
            if not my_required_codes or my_required_codes == [""]:
                progress_dict[uid_str] = "N/A"
                continue

            update_courses = update_data.get(uid_str, {})
            base_courses   = base_data.get(uid_str,   {})
            my_req_cids    = [
                cid for cid, tag in course_control_codes.items()
                if tag in my_required_codes
            ]

            if not my_req_cids:
                progress_dict[uid_str] = "N/A"
                continue

            overall_pct      = sum(update_courses.get(c, 0.0) for c in my_req_cids) / len(my_req_cids)
            base_overall_pct = sum(base_courses.get(c,   0.0) for c in my_req_cids) / len(my_req_cids)

            missed_new = 0.0
            for c in my_req_cids:
                if update_courses.get(c, 0.0) == 0.0:
                    if c not in base_all_courses:
                        missed_new = 1.0
                        break
                    cc = course_control_codes.get(c)
                    student_base_codes = base_uid_codes.get(uid_str)
                    if (student_base_codes is not None and cc
                            and cc != "Unassigned" and cc not in student_base_codes):
                        missed_new = 1.0
                        break

            stats = {
                "Overall Completion %": overall_pct,
                "Monthly Progress %":   overall_pct - base_overall_pct,
                "Missed New Modules":   missed_new,
                "Default":              0.0,
            }

            assigned_category = "Unknown"
            for rule in rules:
                if self.evaluate_rule(stats, rule):
                    assigned_category = rule["name"]
                    break

            progress_dict[uid_str] = assigned_category

        return progress_dict

    # ── Data ingestion ─────────────────────────────────────────────────────────

    def run_data_ingestion_with_folder(self, folder_path, is_update=False):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            dm = DataManager()
            student_csv  = os.path.join(folder_path, "students.csv")
            student_xlsx = os.path.join(folder_path, "students.xlsx")

            if os.path.exists(student_csv):
                dm.import_students(student_csv)
            elif os.path.exists(student_xlsx):
                dm.import_students(student_xlsx)

            dm.import_skilljar_metrics(folder_path)
            dm.import_course_files(folder_path)
            dm.import_control_codes(folder_path)

            if is_update and self.base_data_folder:
                # FIX: run on background thread so the UI doesn't freeze
                self._start_progress_worker(self.base_data_folder, folder_path)
            else:
                self.load_students_to_table()
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self.ui, "Ingestion Error", str(exc))
        else:
            if not is_update or not self.base_data_folder:
                QApplication.restoreOverrideCursor()

    def _start_progress_worker(self, base_folder, update_folder):
        self._progress_worker = ProgressWorker(self, base_folder, update_folder)
        self._progress_worker.done.connect(self._on_progress_done)
        self._progress_worker.error.connect(self._on_progress_error)
        self._progress_worker.start()

    def _on_progress_done(self, result):
        QApplication.restoreOverrideCursor()
        self.progress_dict = result
        self.settings.setValue("progress_dict", json.dumps(self.progress_dict))
        self.setup_filters()
        self.load_students_to_table()

    def _on_progress_error(self, message):
        QApplication.restoreOverrideCursor()
        QMessageBox.critical(self.ui, "Progress Calculation Error", message)

    # ── Progress-logic editor ──────────────────────────────────────────────────

    def open_progress_logic_editor(self):
        dialog = ProgressLogicDialog(self.ui)
        if dialog.exec_() == QDialog.Accepted:
            if (self.base_data_folder and os.path.exists(self.base_data_folder)
                    and self.current_data_folder
                    and os.path.exists(self.current_data_folder)):
                QApplication.setOverrideCursor(Qt.WaitCursor)
                self._start_progress_worker(
                    self.base_data_folder, self.current_data_folder
                )

    # ── Email drafting ─────────────────────────────────────────────────────────

    def draft_emails(self):
        try:
            import win32com.client as win32
        except ImportError:
            QMessageBox.warning(
                self.ui, "Missing Library",
                "Outlook integration requires the 'pywin32' library.\n\n"
                "Run:  pip install pywin32",
            )
            return

        table = self.ui.table_students
        category_emails = {}

        for row in range(table.rowCount()):
            if table.isRowHidden(row):
                continue
            email_item = table.item(row, 1)
            prog_item  = table.item(row, 8)
            email    = email_item.text() if email_item else ""
            progress = prog_item.text()  if prog_item  else ""

            if email and email != "N/A" and "@" in email:
                category_emails.setdefault(progress, []).append(email)

        if not category_emails:
            QMessageBox.information(
                self.ui, "No Students",
                "No valid emails found in the current filtered view.",
            )
            return

        # FIX: count total recipients and require explicit confirmation
        total_recipients = sum(len(v) for v in category_emails.values())
        confirm = QMessageBox.question(
            self.ui,
            "Confirm Email Drafts",
            f"This will open {len(category_emails)} draft(s) covering "
            f"{total_recipients} recipient(s).\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            outlook = win32.Dispatch("outlook.application")
        except Exception as exc:
            QMessageBox.critical(
                self.ui, "Outlook Error",
                f"Could not connect to Outlook. Is it installed and running?\n\nError: {exc}",
            )
            return

        rules = json.loads(
            self.settings.value("progress_rules", json.dumps(DEFAULT_RULES))
        )
        templates = {
            r["name"]: {"subject": r.get("subject", ""), "body": r.get("body", "")}
            for r in rules
        }
        default_template = {
            "subject": "Power Integrations Training Update",
            "body": (
                "Hi Team,<br><br>Please check your Power Integrations training "
                "portal for updates on your required modules."
            ),
        }

        drafts_created = 0
        capped_categories = []

        for category, emails in category_emails.items():
            # FIX: cap recipients per draft to MAX_EMAIL_RECIPIENTS
            if len(emails) > MAX_EMAIL_RECIPIENTS:
                capped_categories.append(
                    f"'{category}': {len(emails)} → capped at {MAX_EMAIL_RECIPIENTS}"
                )
                emails = emails[:MAX_EMAIL_RECIPIENTS]

            mail          = outlook.CreateItem(0)
            mail.BCC      = "; ".join(emails)
            tmpl          = templates.get(category, default_template)
            mail.Subject  = tmpl["subject"]
            mail.HTMLBody = tmpl["body"]
            mail.Display(False)
            drafts_created += 1

        msg = f"Successfully opened {drafts_created} draft(s) in Outlook."
        if capped_categories:
            msg += "\n\nRecipient cap applied:\n" + "\n".join(capped_categories)
        QMessageBox.information(self.ui, "Success", msg)

    # ── Table population ───────────────────────────────────────────────────────

    def on_tab_changed(self, index):
        self.current_org_filter = ["FAE", "Sales", "Unknown"][index]
        self.load_students_to_table()

    def get_all_students(self):
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id,
                       first_name || ' ' || last_name AS name,
                       email, location, organization, title, manager,
                       region_bucket, control_codes
                FROM   students
                WHERE  organization = ?
                ORDER BY
                    CASE WHEN region_bucket = 'Worldwide' OR region_bucket IS NULL
                         THEN 1 ELSE 0 END,
                    region_bucket ASC, location ASC, first_name ASC
                """,
                [self.current_org_filter],
            )
            return cursor.fetchall()

    def load_students_to_table(self):
        try:
            self.progress_dict = json.loads(
                self.settings.value("progress_dict", "{}")
            )
        except Exception:
            self.progress_dict = {}

        self.is_loading  = True
        all_students     = self.get_all_students()

        # FIX: filter completions at the SQL level instead of loading everything
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT course_id, control_code FROM courses")
            course_map = {str(r[0]): str(r[1]) for r in cursor.fetchall()}

            cursor.execute(
                """
                SELECT c.user_id, c.course_id, c.percent_complete
                FROM   completions c
                JOIN   students s ON s.user_id = c.user_id
                WHERE  s.organization = ?
                """,
                (self.current_org_filter,),
            )
            completions_map = {}
            for uid, cid, pct in cursor.fetchall():
                uid_str = normalize_uid(uid)
                completions_map.setdefault(uid_str, {})[str(cid)] = safe_float(pct)

        self.ui.table_students.setRowCount(0)
        self.ui.table_students.setRowCount(len(all_students))

        for row_idx, student_data in enumerate(all_students):
            user_id, name, email, location, organization, title, manager, region_bucket, control_codes = student_data

            uid_str  = normalize_uid(user_id)
            my_codes = (
                [c.strip() for c in str(control_codes).split(",")]
                if control_codes and str(control_codes).lower() not in ("nan", "n/a", "none", "")
                else []
            )
            my_req_courses = [cid for cid, cc in course_map.items() if cc in my_codes]

            if not my_req_courses:
                avg_comp_str = "N/A"
            else:
                total_pct = sum(
                    completions_map.get(uid_str, {}).get(cid, 0.0)
                    for cid in my_req_courses
                )
                avg_comp_str = f"{(total_pct / len(my_req_courses)):.1f}%"

            row_values = [
                name, email, location, title, manager,
                organization, control_codes,
                avg_comp_str,
                self.progress_dict.get(uid_str, "N/A"),
            ]

            for col_idx, value in enumerate(row_values):
                val_str = str(value) if value and str(value) != "nan" else "N/A"

                if col_idx == 5:
                    combo = QComboBox()
                    combo.addItems(["FAE", "Sales", "Unknown"])
                    combo.setCurrentText(val_str)
                    combo.currentTextChanged.connect(
                        lambda text, uid=user_id: self.update_student_org(uid, text)
                    )
                    self.ui.table_students.setCellWidget(row_idx, col_idx, combo)
                    continue

                if col_idx == 6:
                    combo = CheckableComboBox(uid_str)
                    all_codes    = ["Low & Mid Power", "High Power", "Automotive", "Motor Driver"]
                    checked_codes = (
                        [c.strip() for c in val_str.split(",")]
                        if val_str != "N/A" else []
                    )
                    combo.set_items(all_codes, checked_codes)
                    combo.selection_changed.connect(self.update_student_codes)
                    self.ui.table_students.setCellWidget(row_idx, col_idx, combo)
                    continue

                item = QTableWidgetItem(val_str)
                if col_idx >= 1:
                    item.setTextAlignment(Qt.AlignCenter)
                item.setFlags(
                    Qt.ItemIsSelectable | Qt.ItemIsEnabled
                    | (Qt.ItemIsEditable if col_idx not in (7, 8) else Qt.NoItemFlags)
                )

                if col_idx == 0:
                    item.setData(Qt.UserRole, user_id)
                elif col_idx == 2:
                    item.setData(Qt.UserRole, region_bucket)

                self.ui.table_students.setItem(row_idx, col_idx, item)

        self.update_tab_counts(all_students)
        self.apply_filters()
        self.is_loading = False

    # ── Cell / widget change handlers ──────────────────────────────────────────

    def update_student_org(self, user_id, new_org):
        with sqlite3.connect(DB_PATH) as conn:
            conn.cursor().execute(
                "UPDATE students SET organization=? WHERE user_id=?",
                (new_org, user_id),
            )
        if new_org != self.current_org_filter:
            self.load_students_to_table()

    def update_student_codes(self, user_id, new_codes_str):
        with sqlite3.connect(DB_PATH) as conn:
            conn.cursor().execute(
                "UPDATE students SET control_codes=? WHERE user_id=?",
                (new_codes_str, user_id),
            )

        QApplication.setOverrideCursor(Qt.WaitCursor)
        if (self.base_data_folder and os.path.exists(self.base_data_folder)
                and self.current_data_folder and os.path.exists(self.current_data_folder)):
            self._start_progress_worker(
                self.base_data_folder, self.current_data_folder
            )
        else:
            QApplication.restoreOverrideCursor()
            self.load_students_to_table()

    def update_tab_counts(self, all_students):
        total    = len(all_students)
        # FIX: internal-domain check uses configurable INTERNAL_DOMAINS list
        internal = sum(
            1 for s in all_students
            if any(d in str(s[2]).lower() for d in INTERNAL_DOMAINS)
        )
        self.ui.lbl_total.setText(f"<b>Total:</b> {total}")
        self.ui.lbl_internal.setText(f"Internal: {internal}")
        self.ui.lbl_external.setText(f"External: {total - internal}")

    # ── Inline cell editing ────────────────────────────────────────────────────

    def on_cell_edited(self, item):
        if self.is_loading:
            return

        row       = item.row()
        col       = item.column()
        new_value = item.text().strip()

        name_item = self.ui.table_students.item(row, 0)
        user_id   = name_item.data(Qt.UserRole) if name_item else None
        if user_id is None:
            return

        # FIX: explicit allowlist for column names — prevents SQL injection if
        # col_map is ever extended with externally sourced values.
        ALLOWED_SINGLE_COLS = {"email", "title", "manager"}

        col_map = {
            0: ("split_name",),
            1: ("email",),
            2: ("location",),
            3: ("title",),
            4: ("manager",),
        }
        db_columns = col_map.get(col)
        if not db_columns:
            return

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            if col == 0:
                parts = new_value.split(" ", 1)
                cursor.execute(
                    "UPDATE students SET first_name=?, last_name=? WHERE user_id=?",
                    (parts[0], parts[1] if len(parts) > 1 else "", user_id),
                )
            elif col == 2:
                cursor.execute(
                    "UPDATE students SET location=?, region_bucket=? WHERE user_id=?",
                    (new_value, parse_region(new_value), user_id),
                )
            else:
                col_name = db_columns[0]
                if col_name not in ALLOWED_SINGLE_COLS:
                    return  # safety: reject unknown columns
                cursor.execute(
                    f"UPDATE students SET {col_name}=? WHERE user_id=?",
                    (new_value, user_id),
                )

        if col == 2:
            self.load_students_to_table()