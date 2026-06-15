from PyQt5.QtWidgets import (
    QTableWidgetItem, QHeaderView, QApplication, QDialog,
    QComboBox, QCheckBox, QMessageBox, QMenu, QWidgetAction,
    QFileDialog, QLineEdit, QDoubleSpinBox, QTableWidget,
    QTableView, QStyledItemDelegate, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
)
from PyQt5.QtGui import QStandardItemModel, QStandardItem, QColor, QBrush
from PyQt5.QtCore import Qt, QSettings, pyqtSignal, QThread, QAbstractTableModel, QSortFilterProxyModel
from PyQt5 import uic
import pandas as pd
import sqlite3
import os
import sys
import json
import glob
import datetime
import shutil

from database import DB_PATH
from data_manager import (
    DataManager, parse_region, load_dataframe, safe_float, normalize_uid,
    INTERNAL_DOMAINS,
)

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# ── Email safety constants ─────────────────────────────────────────────────────

try:
    import bleach
    BLEACH_AVAILABLE = True
except ImportError:
    BLEACH_AVAILABLE = False


# ── Default progress rules (Emojis on the right) ──────────────────────────────
DEFAULT_RULES = [
    {
        "name": "Up-to-Date ✅",
        "metric": "Overall Completion %",
        "operator": "==",
        "value": 100.0,
        "subject": "You’re All Set 👍",
        "body": "Hi [Name],<br><br>Just a quick note to say you’re currently up to date with all required training modules.<br><br>Great job staying on track. This really helps ensure you’re ready to support customers effectively.<br><br>We’ll keep you posted when new modules are released.<br><br>Thanks,<br>Training Team",
    },
    {
        "name": "New Students 👋",
        "metric": "Overall Completion %",
        "operator": "==",
        "value": -1.0, 
        "subject": "Welcome – Getting Started with Your Training",
        "body": "Hi [Name],<br><br>Welcome to the Power Integrations Skilljar Training.<br><br>Skilljar is our company training bank. It is accessible through <a href='https://powertest.skilljar.com'>https://powertest.skilljar.com</a>.<br><br>To get started, you can begin with your assigned modules.<br><br>We recommend starting with the first module and progressing step by step.<br><br>If you have any questions along the way, feel free to reach out.<br><br>Thanks,<br>Training Team",
    },
    {
        "name": "No Action (0%) ⚠️",
        "metric": "Overall Completion %",
        "operator": "==",
        "value": 0.0,
        "subject": "Reminder to Complete Your Training",
        "body": "Hi [Name],<br><br>Just a quick reminder to begin your assigned training modules.<br><br>We noticed there hasn’t been any progress yet. Getting started early will help you complete the content more smoothly.<br><br>You can access your training here:<br><a href='https://powertest.skilljar.com'>https://powertest.skilljar.com</a><br><br>Let me know if you need any support or if there are any blockers.<br><br>Thanks,<br>Training Team",
    },
    {
        "name": "Progressed This Month 🚀",
        "metric": "Monthly Progress %",
        "operator": ">",
        "value": 0.0,
        "subject": "Nice Progress This Month 👏",
        "body": "Hi [Name],<br><br>Nice work on your progress this month.<br><br>Your current completion rate is now at [X%], showing solid improvement. Keep it going to stay on track and complete the remaining modules.<br><br>Next step: continue with your remaining modules here:<br><a href='https://powertest.skilljar.com'>https://powertest.skilljar.com</a><br><br>Thanks,<br>Training Team",
    },
    {
        "name": "Missed New Module 🆕",
        "metric": "Missed New Modules",
        "operator": "==",
        "value": 1.0,
        "subject": "New Module Available – Please Review",
        "body": "Hi [Name],<br><br>A new training module has been released, but it looks like you haven’t accessed it yet.<br><br>You can find it here:<br><a href='https://powertest.skilljar.com'>https://powertest.skilljar.com</a><br><br>We recommend going through it as soon as possible to stay up to date with the latest content.<br><br>Let me know if you have any questions.<br><br>Thanks,<br>Training Team",
    },
    {
        "name": "Stagnant Students ⏳",
        "metric": "Default",
        "operator": "==",
        "value": 0.0,
        "subject": "Quick Check on Your Training Progress",
        "body": "Hi [Name],<br><br>We noticed that your current training progress is at [completion], with no activity recorded from the previous month. If you’re currently busy or facing challenges, that’s totally understandable.<br><br>Let us know if you need help or if there’s anything blocking you from continuing.<br><br>You can resume your training here:<br><a href='https://powertest.skilljar.com'>https://powertest.skilljar.com</a><br><br>Thanks,<br>Training Team",
    },
]

# ── Month Year Selection Dialog ────────────────────────────────────────────────
class MonthYearDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Data Month")
        self.setStyleSheet("""
            QDialog { background: #202025; color: white; }
            QLabel { color: white; font-weight: bold; }
            QComboBox { background: #1a1a1e; color: white; border: 1px solid #3a3a40; padding: 4px; }
            QPushButton { padding: 6px; border-radius: 4px; font-weight: bold; background: #0085ca; color: white; }
            QPushButton:hover { background: #3c649f; }
        """)
        
        layout = QVBoxLayout(self)
        
        hbox = QHBoxLayout()
        self.month_combo = QComboBox()
        self.month_combo.addItems([
            "January", "February", "March", "April", "May", "June", 
            "July", "August", "September", "October", "November", "December"
        ])
        
        self.year_combo = QComboBox()
        current_year = datetime.datetime.now().year
        self.year_combo.addItems([str(y) for y in range(current_year-2, current_year+3)])
        self.year_combo.setCurrentText(str(current_year))
        
        current_month_idx = datetime.datetime.now().month - 1
        self.month_combo.setCurrentIndex(current_month_idx)
        
        hbox.addWidget(QLabel("Month:"))
        hbox.addWidget(self.month_combo)
        hbox.addWidget(QLabel("Year:"))
        hbox.addWidget(self.year_combo)
        layout.addLayout(hbox)
        
        btn_layout = QHBoxLayout()
        self.btn_ok = QPushButton("OK")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_ok)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

    def get_selection(self):
        return self.month_combo.currentIndex() + 1, int(self.year_combo.currentText()), self.month_combo.currentText()


# ── Background worker for progress calculation ─────────────────────────────────
class ProgressWorker(QThread):
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, component, current_year, current_month):
        super().__init__()
        self._component = component
        self._current_year = current_year
        self._current_month = current_month

    def run(self):
        try:
            result = self._component.calculate_progress(self._current_year, self._current_month)
            self.done.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ── Template editor dialog ─────────────────────────────────────────────────────
class TemplateEditorDialog(QDialog):
    def __init__(self, rules, parent=None):
        super().__init__(parent)
        uic.loadUi(resource_path("dialog_email_templates.ui"), self)
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
                self.edit_subject.setText(rule.get("subject", "Power Integrations Training Update"))
                self.edit_body.setHtml(rule.get("body", "Hi [Name],<br><br>Please check your training portal."))
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
        uic.loadUi(resource_path("dialog_progress_logic.ui"), self)

        self.settings = QSettings("PowerIntegrations", "PowerTrack")
        # Load v3 rules to ensure new emoji rules apply cleanly
        self.rules = json.loads(self.settings.value("progress_rules_v3", json.dumps(DEFAULT_RULES)))

        for r in self.rules:
            if "subject" not in r: r["subject"] = "Power Integrations Training Update"
            if "body" not in r: r["body"] = "Hi [Name],<br><br>Please check your training portal."

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
            rule = {"name": "New Category", "metric": "Monthly Progress %", "operator": ">=", "value": 20.0, "subject": "Training Update", "body": "Please review your training."}

        row = self.table_rules.rowCount()
        self.table_rules.insertRow(row)

        name_edit = QLineEdit(rule["name"])
        name_edit.setStyleSheet("background: transparent; color: white; border: none; padding: 5px;")
        self.table_rules.setCellWidget(row, 0, name_edit)

        metric_combo = QComboBox()
        metric_combo.addItems(["Overall Completion %", "Monthly Progress %", "Missed New Modules", "Default"])
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

    def add_rule(self): self.add_rule_row()
    def delete_rule(self):
        row = self.table_rules.currentRow()
        if row >= 0: self.table_rules.removeRow(row)

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
        body = "Hi [Name],<br><br>Please check your training portal."
        for r in self.rules:
            if r["name"] == current_name:
                subj = r.get("subject", subj)
                body = r.get("body", body)
                break
        return {"name": current_name, "metric": self.table_rules.cellWidget(row, 1).currentText(), "operator": self.table_rules.cellWidget(row, 2).currentText(), "value": self.table_rules.cellWidget(row, 3).value(), "subject": subj, "body": body}

    def sync_ui_to_rules(self):
        self.rules = [self.get_row_data(i) for i in range(self.table_rules.rowCount())]

    def open_template_editor(self):
        self.sync_ui_to_rules()
        editor = TemplateEditorDialog(self.rules, self)
        editor.exec_()

    def save_rules(self):
        self.sync_ui_to_rules()
        self.settings.setValue("progress_rules_v3", json.dumps(self.rules))


# ── Multi-select combobox for Delegate ─────────────────────────────────────────
class CheckableComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModel(QStandardItemModel(self))
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setAlignment(Qt.AlignCenter)
        self.view().pressed.connect(self.handle_item_pressed)

    def handle_item_pressed(self, index):
        item = self.model().itemFromIndex(index)
        item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
        self.update_text()

    def set_items(self, items, checked_items):
        self.clear()
        for text in items:
            item = QStandardItem(text)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setData(Qt.Checked if text in checked_items else Qt.Unchecked, Qt.CheckStateRole)
            self.model().appendRow(item)
        self.update_text()

    def update_text(self):
        checked = [self.model().item(i).text() for i in range(self.model().rowCount()) if self.model().item(i).checkState() == Qt.Checked]
        text = ", ".join(checked)
        self.lineEdit().setText(text)
        return text

# ── Delegates for QTableView ───────────────────────────────────────────────────
class OrgDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(["FAE", "Sales", "Unknown"])
        return combo

    def setEditorData(self, editor, index):
        value = index.model().data(index, Qt.EditRole)
        idx = editor.findText(value)
        if idx >= 0: editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)

class ControlCodeDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        return CheckableComboBox(parent)

    def setEditorData(self, editor, index):
        val_str = index.model().data(index, Qt.EditRole)
        checked_codes = [c.strip() for c in val_str.split(",")] if val_str and val_str != "N/A" else []
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_settings WHERE key = 'category_tree'")
            row = cursor.fetchone()
            structure = json.loads(row[0]) if row else {}
            
        editor.set_items(list(structure.keys()), checked_codes)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.lineEdit().text(), Qt.EditRole)

# ── Models for UI Virtualization ───────────────────────────────────────────────
class StudentTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.headers = ["Name", "Email", "Location", "Title", "Manager", "Organization", "Control Code", "Avg. Completion", "Progress"]
        self._data = []

    def rowCount(self, parent=None): return len(self._data)
    def columnCount(self, parent=None): return len(self.headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        row, col = index.row(), index.column()
        
        if role == Qt.DisplayRole or role == Qt.EditRole: return str(self._data[row][col])
        elif role == Qt.TextAlignmentRole: return Qt.AlignCenter | Qt.AlignVCenter if col >= 1 else Qt.AlignLeft | Qt.AlignVCenter
        elif role == Qt.UserRole: 
            if col == 0: return self._data[row][9]  
            if col == 2: return self._data[row][10] 
            if col == 8: return self._data[row][8]  
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if index.isValid() and role == Qt.EditRole:
            row, col = index.row(), index.column()
            old_val = str(self._data[row][col])
            new_val = str(value).strip()
            
            if old_val == new_val: return False
                
            self._data[row][col] = new_val
            user_id = self._data[row][9]
            ALLOWED_SINGLE_COLS = {1: "email", 3: "title", 4: "manager"}

            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                if col == 0:
                    parts = new_val.split(" ", 1)
                    cursor.execute("UPDATE students SET first_name=?, last_name=? WHERE user_id=?", (parts[0], parts[1] if len(parts) > 1 else "", user_id))
                elif col == 2:
                    new_region = parse_region(new_val)
                    self._data[row][10] = new_region
                    cursor.execute("UPDATE students SET location=?, region_bucket=? WHERE user_id=?", (new_val, new_region, user_id))
                elif col == 5:
                    cursor.execute("UPDATE students SET organization=? WHERE user_id=?", (new_val, user_id))
                elif col == 6:
                    cursor.execute("UPDATE students SET control_codes=? WHERE user_id=?", (new_val, user_id))
                elif col in ALLOWED_SINGLE_COLS:
                    col_name = ALLOWED_SINGLE_COLS[col]
                    cursor.execute(f"UPDATE students SET {col_name}=? WHERE user_id=?", (new_val, user_id))
                    
            self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
            return True
        return False

    def flags(self, index):
        if not index.isValid(): return Qt.NoItemFlags
        flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if index.column() not in (7, 8): flags |= Qt.ItemIsEditable
        return flags

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            if orientation == Qt.Horizontal: 
                return self.headers[section]
            elif orientation == Qt.Vertical:
                return str(section + 1)
        return None

    def update_data(self, new_data):
        self.beginResetModel()
        self._data = new_data
        self.endResetModel()

class StudentFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.search_text = ""
        self.target_region = "All Regions"
        self.target_progress = "All Status"

    def set_filters(self, search, region, progress):
        self.search_text = search.lower()
        self.target_region = region
        self.target_progress = progress
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        name = model.data(model.index(source_row, 0, source_parent)).lower()
        email = model.data(model.index(source_row, 1, source_parent)).lower()
        region = model.data(model.index(source_row, 2, source_parent), Qt.UserRole)
        progress = model.data(model.index(source_row, 8, source_parent), Qt.UserRole)

        match_search = (self.search_text in name) or (self.search_text in email)
        match_region = (self.target_region == "All Regions" or region == self.target_region)
        match_progress = (self.target_progress == "All Status" or progress == self.target_progress)
        return match_search and match_region and match_progress

# ── Main student tab component ─────────────────────────────────────────────────
class StudentTabComponent:
    def __init__(self, ui_window):
        self.ui = ui_window
        self.is_loading = True
        self.current_org_filter = "FAE"
        self._progress_worker = None

        self.settings = QSettings("PowerIntegrations", "PowerTrack")
        
        self.current_month = int(self.settings.value("current_month", 0))
        self.current_year = int(self.settings.value("current_year", 0))
        self.current_month_name = self.settings.value("current_month_name", "None")

        try:
            self.progress_dict = json.loads(self.settings.value("progress_dict", "{}"))
        except Exception:
            self.progress_dict = {}
            
        try:
            DataManager().auto_import_master_lists()
        except Exception as e:
            print(f"[WARN] Failed to auto-import master lists: {e}")

        self.setup_ui_bindings()
        self.setup_table()
        self.setup_filters()
        self.update_month_label()
        self.load_students_to_table()

    def setup_ui_bindings(self):
        self.ui.tabBar_students.addTab("FAE")
        self.ui.tabBar_students.addTab("Sales")
        self.ui.tabBar_students.addTab("Unknown")
        self.ui.tabBar_students.currentChanged.connect(self.on_tab_changed)

        self.ui.btn_import_monthly.clicked.connect(self.import_monthly_data)
        self.ui.btn_export_archive.clicked.connect(self.export_month_archive)

        if hasattr(self.ui, "btn_edit_progress"):
            self.ui.btn_edit_progress.clicked.connect(self.open_progress_logic_editor)
        if hasattr(self.ui, "btn_email_students"):
            self.ui.btn_email_students.clicked.connect(self.draft_emails)

    def setup_table(self):
        self.table_view = self.ui.table_students

        self.model = StudentTableModel(self.ui)
        self.proxy = StudentFilterProxyModel(self.ui)
        self.proxy.setSourceModel(self.model)
        self.table_view.setModel(self.proxy)

        self.table_view.setItemDelegateForColumn(5, OrgDelegate(self.ui))
        self.table_view.setItemDelegateForColumn(6, ControlCodeDelegate(self.ui))

        header = self.table_view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self.show_column_menu)

        # Increase row height to accommodate bigger fonts gracefully
        self.table_view.verticalHeader().setDefaultSectionSize(40)
        self.table_view.verticalHeader().setMinimumWidth(30)
        
        for col, width in enumerate([180, 200, 180, 180, 160, 100, 200, 150]):
            self.table_view.setColumnWidth(col, width)
        header.setStretchLastSection(True)
        self.model.dataChanged.connect(self.on_model_data_changed)

    def on_model_data_changed(self, top_left, bottom_right, roles):
        if self.is_loading: return
        col = top_left.column()
        if col == 5: 
            self.load_students_to_table()
        elif col == 6: 
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._start_progress_worker()

    def setup_filters(self):
        self.ui.search_student.textChanged.connect(self.apply_filters)
        self.ui.combo_region.clear()
        self.ui.combo_region.addItems(["All Regions", "Americas", "Europe", "China", "Taiwan", "Japan", "Korea", "ISEA", "Worldwide"])
        self.ui.combo_region.currentTextChanged.connect(self.apply_filters)

        rules = json.loads(self.settings.value("progress_rules_v3", json.dumps(DEFAULT_RULES)))
        unique_categories = list(dict.fromkeys(r["name"] for r in rules))
        try: self.ui.combo_progress.currentTextChanged.disconnect(self.apply_filters)
        except TypeError: pass

        self.ui.combo_progress.clear()
        self.ui.combo_progress.addItem("All Status")
        self.ui.combo_progress.addItems(unique_categories)
        self.ui.combo_progress.addItem("N/A")
        self.ui.combo_progress.currentTextChanged.connect(self.apply_filters)
        self.ui.btn_clear_filters.clicked.connect(self.clear_filters)

    def apply_filters(self):
        self.proxy.set_filters(self.ui.search_student.text(), self.ui.combo_region.currentText(), self.ui.combo_progress.currentText())
        self.ui.lbl_showing.setText(f"Showing {self.proxy.rowCount()} of {self.model.rowCount()} students")

    def clear_filters(self):
        self.ui.search_student.clear()
        self.ui.combo_region.setCurrentIndex(0)
        self.ui.combo_progress.setCurrentIndex(0)

    def show_column_menu(self, pos):
        menu  = QMenu(self.ui)
        menu.setStyleSheet("QMenu { background: #202025; color: white; border: 1px solid #3a3a40; }")
        for c in range(self.model.columnCount()):
            action = QWidgetAction(menu)
            chk = QCheckBox(self.model.headerData(c, Qt.Horizontal))
            chk.setStyleSheet("QCheckBox { padding: 5px; background: transparent; color: white; } QCheckBox:hover { background: #0085ca; }")
            chk.setChecked(not self.table_view.isColumnHidden(c))
            chk.toggled.connect(lambda checked, col=c: self.table_view.setColumnHidden(col, not checked))
            action.setDefaultWidget(chk)
            menu.addAction(action)
        menu.exec_(self.table_view.horizontalHeader().viewport().mapToGlobal(pos))

    def update_month_label(self):
        if self.current_month == 0:
            self.ui.lbl_current_month.setText("Current Data View: None (Please Import Data)")
        else:
            self.ui.lbl_current_month.setText(f"Current Data View: {self.current_month_name} {self.current_year}")

    def import_monthly_data(self):
        dialog = MonthYearDialog(self.ui)
        if dialog.exec_() != QDialog.Accepted: return
        month_idx, year, month_name = dialog.get_selection()
        
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            if os.path.basename(base_dir) == 'components':
                base_dir = os.path.dirname(base_dir)
                
        short_month = month_name[:3] 
        folder_name = f"{year}_{month_idx:02d}_{short_month}"
        raw_data_dir = os.path.join(base_dir, "monthly_raw_data", folder_name)
        
        has_files = False
        if not os.path.exists(raw_data_dir):
            os.makedirs(raw_data_dir)
        else:
            files = [f for f in os.listdir(raw_data_dir) if f.lower().endswith(('.csv', '.xlsx', '.xls'))]
            has_files = len(files) > 0
            
        if not has_files:
            QMessageBox.information(
                self.ui, "Folder Created / Empty", 
                f"The expected raw data folder is empty or was just created:\n\n{raw_data_dir}\n\nPlease place your Skilljar export files in this folder and click Import again."
            )
            try:
                os.startfile(raw_data_dir)
            except Exception:
                pass
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.current_month = month_idx
            self.current_year = year
            self.current_month_name = month_name
            self.settings.setValue("current_month", self.current_month)
            self.settings.setValue("current_year", self.current_year)
            self.settings.setValue("current_month_name", self.current_month_name)
            self.update_month_label()

            dm = DataManager()
            dm.auto_import_master_lists()
            
            student_csv  = os.path.join(raw_data_dir, "students.csv")
            student_xlsx = os.path.join(raw_data_dir, "students.xlsx")
            if os.path.exists(student_csv): dm.import_students(student_csv)
            elif os.path.exists(student_xlsx): dm.import_students(student_xlsx)

            dm.import_skilljar_metrics(raw_data_dir)
            dm.import_course_files(raw_data_dir)
            dm.import_control_codes(raw_data_dir)
            
            dm.create_monthly_snapshot(self.current_year, self.current_month)

            self._start_progress_worker()
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self.ui, "Ingestion Error", str(exc))

    def export_month_archive(self):
        filename = f"Filtered_Students_{self.current_month_name}_{self.current_year}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self.ui, "Export Filtered Students", filename, "Excel Files (*.xlsx)")
        if not path: return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            # 1. Pull data ONLY from the currently filtered view (self.proxy)
            export_data = []
            for row in range(self.proxy.rowCount()):
                src_row = self.proxy.mapToSource(self.proxy.index(row, 0)).row()
                
                # Extract clean percentage float for Excel formatting
                raw_pct = self.model._data[src_row][7]
                try:
                    clean_pct = float(raw_pct.replace('%', '')) / 100.0 if raw_pct != "N/A" else raw_pct
                except:
                    clean_pct = raw_pct

                export_data.append({
                    "Name": self.model._data[src_row][0],
                    "Email": self.model._data[src_row][1],
                    "Location": self.model._data[src_row][2],
                    "Title": self.model._data[src_row][3],
                    "Manager": self.model._data[src_row][4],
                    "Organization": self.model._data[src_row][5],
                    "Control Code": self.model._data[src_row][6],
                    "Avg. Completion": clean_pct,
                    "Progress": self.model._data[src_row][8]
                })

            df = pd.DataFrame(export_data)

            # 2. Apply the exact tab_analytics formatting (xlsxwriter)
            try:
                writer = pd.ExcelWriter(path, engine="xlsxwriter")
                df.to_excel(writer, sheet_name="Students", index=False)
                worksheet = writer.sheets["Students"]
                
                # Format column widths
                worksheet.set_column(0, 0, 22) # Name
                worksheet.set_column(1, 1, 35) # Email
                worksheet.set_column(2, 6, 18) # Location, Title, Org, etc.
                worksheet.set_column(8, 8, 30) # Progress Category
                
                # Format percentage column specifically
                pct_format = writer.book.add_format({"num_format": "0.00%"})
                worksheet.set_column(7, 7, 18, pct_format) 
                
                # Add stylish Header formatting
                header_format = writer.book.add_format({'bold': True, 'bg_color': '#202025', 'font_color': 'white', 'border': 1})
                for col_num, value in enumerate(df.columns.values):
                    worksheet.write(0, col_num, value, header_format)

                writer.close()
            except ImportError:
                df.to_excel(path, index=False)

            # 3. Keep the Database Backup Logic intact
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                if os.path.basename(base_dir) == 'components':
                    base_dir = os.path.dirname(base_dir)
            
            short_month = self.current_month_name[:3]
            db_backup_dir = os.path.join(base_dir, "archives_and_exports", "Database_Backups")
            os.makedirs(db_backup_dir, exist_ok=True)
            db_backup_path = os.path.join(db_backup_dir, f"powertrack_backup_{self.current_year}_{self.current_month:02d}_{short_month}.db")
            shutil.copy2(DB_PATH, db_backup_path)

            QMessageBox.information(self.ui, "Export Successful", f"Filtered View saved to:\n{path}\n\nA raw database backup was also created in:\narchives_and_exports/Database_Backups/")
        except Exception as exc:
            QMessageBox.critical(self.ui, "Export Error", f"Failed to export:\n{exc}")
        finally:
            QApplication.restoreOverrideCursor()

    def evaluate_rule(self, stats, rule):
        if rule["metric"] == "Default": return True
        val, target, op = stats.get(rule["metric"], 0.0), float(rule["value"]), rule["operator"]
        if op == ">":  return val >  target
        if op == "<":  return val <  target
        if op == ">=": return val >= target
        if op == "<=": return val <= target
        if op == "==": return val == target
        return False

    def calculate_progress(self, current_year, current_month):
        prev_month = current_month - 1
        prev_year = current_year
        if prev_month == 0:
            prev_month = 12
            prev_year -= 1

        rules = json.loads(self.settings.value("progress_rules_v3", json.dumps(DEFAULT_RULES)))

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, organization, control_codes FROM students")
            student_data = cursor.fetchall()
            
            cursor.execute("SELECT course_id, fae_control_code, sales_control_code, unknown_control_code FROM courses")
            course_data = cursor.fetchall()
            
            cursor.execute("SELECT user_id, course_id, percent_complete FROM completions")
            current_comps = {}
            for uid, cid, pct in cursor.fetchall():
                current_comps.setdefault(normalize_uid(uid), {})[str(cid)] = safe_float(pct)
                
            cursor.execute("SELECT user_id, course_id, percent_complete FROM completions_history WHERE year=? AND month=?", (prev_year, prev_month))
            prev_comps, prev_all_courses = {}, set()
            for uid, cid, pct in cursor.fetchall():
                prev_comps.setdefault(normalize_uid(uid), {})[str(cid)] = safe_float(pct)
                prev_all_courses.add(str(cid))

        progress_dict = {}
        for uid, org, control_codes in student_data:
            uid_str = normalize_uid(uid)
            my_required_codes = [c.strip() for c in str(control_codes).split(",")] if control_codes else []
            
            if not my_required_codes or my_required_codes == [""]:
                progress_dict[uid_str] = "N/A"
                continue

            curr_courses = current_comps.get(uid_str, {})
            prev_courses = prev_comps.get(uid_str, {})
            
            idx = 1 if org == 'FAE' else 2 if org == 'Sales' else 3
            my_req_cids = [str(cid) for cid, *ccs in course_data if ccs[idx-1] in my_required_codes]

            if not my_req_cids:
                progress_dict[uid_str] = "N/A"
                continue

            overall_pct = sum(curr_courses.get(c, 0.0) for c in my_req_cids) / len(my_req_cids)
            base_overall_pct = sum(prev_courses.get(c, 0.0) for c in my_req_cids) / len(my_req_cids)

            missed_new = 0.0
            for c in my_req_cids:
                if curr_courses.get(c, 0.0) == 0.0:
                    if c not in prev_all_courses:
                        missed_new = 1.0
                        break

            stats = {"Overall Completion %": overall_pct, "Monthly Progress %": overall_pct - base_overall_pct, "Missed New Modules": missed_new, "Default": 0.0}
            
            assigned_category = "Unknown"
            for rule in rules:
                if self.evaluate_rule(stats, rule):
                    assigned_category = rule["name"]
                    break
            progress_dict[uid_str] = assigned_category

        return progress_dict

    def _start_progress_worker(self):
        self._progress_worker = ProgressWorker(self, self.current_year, self.current_month)
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

    def open_progress_logic_editor(self):
        dialog = ProgressLogicDialog(self.ui)
        if dialog.exec_() == QDialog.Accepted:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._start_progress_worker()

    def draft_emails(self):
        try: 
            import win32com.client as win32
        except ImportError:
            QMessageBox.warning(self.ui, "Missing Library", "Outlook integration requires the 'pywin32' library.\n\nRun:  pip install pywin32")
            return
            
        if not BLEACH_AVAILABLE:
            QMessageBox.warning(self.ui, "Security Warning", "The HTML sanitization library 'bleach' is not installed. Sending HTML emails without it is disabled to prevent security vulnerabilities.\n\nRun: pip install bleach")
            return

        category_students = {}
        for row in range(self.proxy.rowCount()):
            src_row = self.proxy.mapToSource(self.proxy.index(row, 0)).row()
            name = self.model._data[src_row][0]
            email = self.model._data[src_row][1]
            completion = self.model._data[src_row][7]
            progress = self.model._data[src_row][8]
            
            if email and email != "N/A" and "@" in email:
                category_students.setdefault(progress, []).append({
                    "name": name,
                    "email": email,
                    "completion": completion
                })

        if not category_students:
            QMessageBox.information(self.ui, "No Students", "No valid emails found in the current filtered view.")
            return

        total_recipients = sum(len(v) for v in category_students.values())
        if QMessageBox.question(self.ui, "Confirm Email Drafts", f"This will generate {total_recipients} INDIVIDUAL email draft(s) in your Outlook.\n\nThis method allows placeholders like [Name] and [X%] to be personalized for each student.\n\nContinue?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try: 
            outlook = win32.Dispatch("outlook.application")
            
            rules = json.loads(self.settings.value("progress_rules_v3", json.dumps(DEFAULT_RULES)))
            templates = {r["name"]: {"subject": r.get("subject", ""), "body": r.get("body", "")} for r in rules}
            default_template = {"subject": "Power Integrations Training Update", "body": "Hi [Name],<br><br>Please check your Power Integrations training portal for updates."}

            allowed_tags = ['b', 'i', 'u', 'br', 'a', 'p', 'span', 'strong', 'em', 'ul', 'ol', 'li']
            allowed_attrs = {'*': ['style'], 'a': ['href', 'title']}
            allowed_styles = ['color', 'background-color', 'font-size', 'font-family', 'text-align']

            drafts_created = 0

            for category, students in category_students.items():
                tmpl = templates.get(category, default_template)
                
                for student in students:
                    mail = outlook.CreateItem(0)
                    mail.To = student["email"]
                    
                    # Extract first name
                    first_name = student["name"].split()[0] if student["name"] and student["name"] != "N/A" else "Team"
                    
                    # Replace placeholders
                    subject = tmpl["subject"].replace("[Name]", first_name).replace("[X%]", student["completion"]).replace("[completion]", student["completion"])
                    raw_body = tmpl["body"].replace("[Name]", first_name).replace("[X%]", student["completion"]).replace("[completion]", student["completion"])
                    
                    mail.Subject = subject
                    mail.HTMLBody = bleach.clean(raw_body, tags=allowed_tags, attributes=allowed_attrs, styles=allowed_styles)
                    
                    # Save securely to the Outlook 'Drafts' folder instead of flashing 50 windows open
                    mail.Save() 
                    drafts_created += 1

            QApplication.restoreOverrideCursor()
            QMessageBox.information(self.ui, "Success", f"Successfully created {drafts_created} individual draft(s).\n\nPlease open your Outlook 'Drafts' folder to review and send them.")

        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self.ui, "Outlook Error", f"Could not create drafts. Is Outlook installed and running?\n\nError: {exc}")

    def on_tab_changed(self, index):
        self.current_org_filter = ["FAE", "Sales", "Unknown"][index]
        self.load_students_to_table()

    def get_all_students(self):
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, first_name || ' ' || last_name AS name, email, location, organization, 
                       title, manager, region_bucket, control_codes, signed_up
                FROM students WHERE organization = ?
                ORDER BY CASE WHEN region_bucket = 'Worldwide' OR region_bucket IS NULL THEN 1 ELSE 0 END,
                         region_bucket ASC, location ASC, first_name ASC
                """, [self.current_org_filter]
            )
            return cursor.fetchall()

    def load_students_to_table(self):
        self.is_loading = True
        try: self.progress_dict = json.loads(self.settings.value("progress_dict", "{}"))
        except Exception: self.progress_dict = {}

        all_students = self.get_all_students()

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            prefix = self.current_org_filter.lower()
            cursor.execute(f"SELECT course_id, {prefix}_control_code FROM courses")
            course_map = {str(r[0]): str(r[1]) for r in cursor.fetchall()}

            cursor.execute("SELECT c.user_id, c.course_id, c.percent_complete FROM completions c JOIN students s ON s.user_id = c.user_id WHERE s.organization = ?", (self.current_org_filter,))
            completions_map = {}
            for uid, cid, pct in cursor.fetchall(): completions_map.setdefault(normalize_uid(uid), {})[str(cid)] = safe_float(pct)

        table_data = []
        for student_data in all_students:
            user_id, name, email, location, organization, title, manager, region_bucket, control_codes, signed_up = student_data
            uid_str = normalize_uid(user_id)
            my_codes = [c.strip() for c in str(control_codes).split(",")] if control_codes and str(control_codes).lower() not in ("nan", "n/a", "none", "") else []
            my_req_courses = [cid for cid, cc in course_map.items() if cc in my_codes]

            if not my_req_courses: avg_comp_str = "N/A"
            else:
                total_pct = sum(completions_map.get(uid_str, {}).get(cid, 0.0) for cid in my_req_courses)
                avg_comp_str = f"{(total_pct / len(my_req_courses)):.1f}%"

            table_data.append([
                name or "N/A", email or "N/A", location or "N/A", title or "N/A", manager or "N/A",
                organization or "Unknown", control_codes or "N/A", avg_comp_str, self.progress_dict.get(uid_str, "N/A"),
                user_id, region_bucket, signed_up
            ]) 

        if self.current_org_filter == "Unknown":
            def parse_date(date_str):
                if not date_str or str(date_str).lower() in ("n/a", "nan", "none"): return datetime.datetime.min
                try:
                    return pd.to_datetime(str(date_str)).to_pydatetime()
                except:
                    return datetime.datetime.min
            table_data.sort(key=lambda x: parse_date(x[11]), reverse=True)

        self.model.update_data(table_data)
        self.update_tab_counts(all_students)
        self.apply_filters()
        self.is_loading = False

    def update_tab_counts(self, all_students):
        total = len(all_students)
        internal = sum(1 for s in all_students if any(d in str(s[2]).lower() for d in INTERNAL_DOMAINS))
        self.ui.lbl_total.setText(f"<b>Total:</b> {total}")
        self.ui.lbl_internal.setText(f"Internal: {internal}")
        self.ui.lbl_external.setText(f"External: {total - internal}")