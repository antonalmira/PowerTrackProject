from PyQt5.QtWidgets import (
    QTableWidgetItem, QApplication, QDialog, QCheckBox,
    QMessageBox, QTableWidget, QFileDialog, QMenu, QWidgetAction,
    QTreeWidgetItem, QTableView, QHeaderView
)
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtCore import Qt, QAbstractTableModel, QSortFilterProxyModel
from PyQt5 import uic
import sqlite3
import pandas as pd
from database import DB_PATH
from data_manager import safe_float, normalize_uid
import os


# ── Course Mapping Dialog ──────────────────────────────────────────────────────

class CourseMappingDialog(QDialog):
    def __init__(self, current_org_filter, parent=None):
        super().__init__(parent)
        ui_path = os.path.join(os.path.dirname(__file__), "..", "dialog_mapping.ui")
        uic.loadUi(ui_path, self)

        self.course_filter  = current_org_filter
        self.courses_deleted = False
        self.setWindowTitle(
            f"Map Courses to Control Codes ({self.course_filter} View)"
        )

        self.tree_categories.dropEvent = self.tree_drop_event
        self.list_unassigned.dropEvent = self.list_drop_event

        self.btn_save.clicked.connect(self.save_mapping)
        self.btn_delete_course.clicked.connect(self.delete_selected_courses)

        self.setup_tree_structure()
        self.load_data()

    def tree_drop_event(self, event):
        source      = event.source()
        target_item = self.tree_categories.itemAt(event.pos())

        if not target_item or target_item.parent() is None:
            event.ignore()
            return

        if target_item.parent().parent() is not None:
            target_item = target_item.parent()

        if source == self.tree_categories:
            for item in self.tree_categories.selectedItems():
                if item.parent() and item.parent().parent():
                    item.parent().takeChild(item.parent().indexOfChild(item))
                    target_item.addChild(item)
            event.setDropAction(Qt.CopyAction)
            event.accept()

        elif source == self.list_unassigned:
            for item in source.selectedItems():
                new_item = QTreeWidgetItem([item.text()])
                new_item.setFlags(
                    Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
                )
                target_item.addChild(new_item)
                source.takeItem(source.row(item))
            target_item.setExpanded(True)
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def list_drop_event(self, event):
        source = event.source()
        if source == self.list_unassigned:
            event.accept()
        elif source == self.tree_categories:
            for item in source.selectedItems():
                if (item.parent() is not None and item.parent().parent() is not None):
                    self.list_unassigned.addItem(item.text(0))
                    item.parent().takeChild(item.parent().indexOfChild(item))
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def setup_tree_structure(self):
        structure = {
            "Low & Mid Power": ["InnoSwitch", "LinkSwitch", "LYTSwitch", "Other"],
            "High Power":      ["High Power"],
            "Automotive":      ["Automotive"],
            "Motor Driver":    ["Motor Driver"],
        }
        for control_code, categories in structure.items():
            cc_node = QTreeWidgetItem(self.tree_categories, [control_code])
            cc_node.setFlags(Qt.ItemIsEnabled)
            for cat in categories:
                cat_node = QTreeWidgetItem(cc_node, [cat])
                cat_node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsDropEnabled)
        self.tree_categories.expandAll()

    def load_data(self):
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            if self.course_filter == "FAE":
                cursor.execute(
                    "SELECT course_id, course_name, control_code, graph_category "
                    "FROM courses WHERE course_type IN ('FAE', 'Both')"
                )
            elif self.course_filter == "Sales":
                cursor.execute(
                    "SELECT course_id, course_name, control_code, graph_category "
                    "FROM courses WHERE course_type IN ('Sales', 'Both')"
                )
            else:
                cursor.execute(
                    "SELECT course_id, course_name, control_code, graph_category "
                    "FROM courses"
                )

            for cid, cname, cc, gc in cursor.fetchall():
                item_text = f"{cname} [{cid}]"
                if cc == "Unassigned" or gc == "Unassigned":
                    self.list_unassigned.addItem(item_text)
                else:
                    for i in range(self.tree_categories.topLevelItemCount()):
                        parent = self.tree_categories.topLevelItem(i)
                        if parent.text(0) == cc:
                            for j in range(parent.childCount()):
                                child = parent.child(j)
                                if child.text(0) == gc:
                                    course_node = QTreeWidgetItem(child, [item_text])
                                    course_node.setFlags(
                                        Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
                                    )

    def delete_selected_courses(self):
        courses_to_delete = []

        for item in self.list_unassigned.selectedItems():
            courses_to_delete.append((item.text(), "list", item))

        for item in self.tree_categories.selectedItems():
            if (item.parent() is not None and item.parent().parent() is not None):
                courses_to_delete.append((item.text(0), "tree", item))

        if not courses_to_delete:
            QMessageBox.information(
                self, "Selection Required",
                "Please select at least one course to delete.",
            )
            return

        if (QMessageBox.question(
                self, "Confirm Delete",
                f"Permanently delete {len(courses_to_delete)} course(s)?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes):
            
            ids = []
            for text, src_type, item in courses_to_delete:
                course_id = text.rsplit("[", 1)[-1].replace("]", "").strip()
                ids.append(course_id)
                if src_type == "list":
                    self.list_unassigned.takeItem(self.list_unassigned.row(item))
                else:
                    item.parent().takeChild(item.parent().indexOfChild(item))

            with sqlite3.connect(DB_PATH) as conn:
                conn.executemany(
                    "DELETE FROM courses WHERE course_id=?",
                    [(i,) for i in ids],
                )
                conn.executemany(
                    "DELETE FROM completions WHERE course_id=?",
                    [(i,) for i in ids],
                )

            self.courses_deleted = True

    def save_mapping(self):
        records = []
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()

            if self.course_filter == "FAE":
                cursor.execute(
                    "UPDATE courses SET control_code='Unassigned', graph_category='Unassigned' "
                    "WHERE course_type IN ('FAE', 'Both')"
                )
            elif self.course_filter == "Sales":
                cursor.execute(
                    "UPDATE courses SET control_code='Unassigned', graph_category='Unassigned' "
                    "WHERE course_type IN ('Sales', 'Both')"
                )
            else:
                cursor.execute(
                    "UPDATE courses SET control_code='Unassigned', graph_category='Unassigned'"
                )

            for i in range(self.tree_categories.topLevelItemCount()):
                cc_node = self.tree_categories.topLevelItem(i)
                for j in range(cc_node.childCount()):
                    gc_node = cc_node.child(j)
                    for k in range(gc_node.childCount()):
                        course_id = (
                            gc_node.child(k).text(0).rsplit("[", 1)[-1]
                            .replace("]", "").strip()
                        )
                        records.append(
                            (cc_node.text(0), gc_node.text(0), course_id)
                        )

            conn.executemany(
                "UPDATE courses SET control_code=?, graph_category=? WHERE course_id=?",
                records,
            )
        self.accept()


# ── Models for UI Virtualization ───────────────────────────────────────────────

class MatrixTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []
        
        self.color_green  = QColor(0, 176, 80)
        self.color_yellow = QColor(255, 255, 0)
        self.color_red    = QColor(255, 0, 0)
        self.color_gray   = QColor(100, 100, 100)
        self.bg_color_table = QColor("#121214")

    def rowCount(self, parent=None): 
        return len(self._data)
        
    def columnCount(self, parent=None): 
        return len(self._data[0]) if self._data else 0

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): 
            return None
            
        row, col = index.row(), index.column()
        cell = self._data[row][col]

        if role == Qt.DisplayRole:
            return str(cell["text"])
        elif role == Qt.TextAlignmentRole:
            return Qt.AlignCenter | Qt.AlignVCenter if col >= 1 else Qt.AlignLeft | Qt.AlignVCenter
        elif role == Qt.BackgroundRole:
            if col >= 5 and cell.get("bg"): return QBrush(cell["bg"])
            return QBrush(self.bg_color_table)
        elif role == Qt.ForegroundRole:
            if col >= 5 and cell.get("fg"): return QBrush(cell["fg"])
            return QBrush(Qt.white)
        elif role == Qt.UserRole: 
            if col == 0: return cell.get("codes", "")
            if col == 2: return cell.get("region", "")
            
        return None

    def update_data(self, new_data):
        self.beginResetModel()
        self._data = new_data
        self.endResetModel()


class MatrixFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.search_txt = ""
        self.target_region = "All Regions"
        self.target_code = "All Codes"

    def set_filters(self, search, region, code):
        self.search_txt = search.lower()
        self.target_region = region
        self.target_code = code
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        
        name = model.data(model.index(source_row, 0, source_parent)).lower()
        email = model.data(model.index(source_row, 1, source_parent)).lower()
        region = model.data(model.index(source_row, 2, source_parent), Qt.UserRole)
        codes = model.data(model.index(source_row, 0, source_parent), Qt.UserRole)

        match_search = (self.search_txt in name) or (self.search_txt in email)
        match_region = (self.target_region == "All Regions" or region == self.target_region)
        match_code   = (self.target_code == "All Codes" or self.target_code in codes)

        return match_search and match_region and match_code


# ── Matrix tab component ───────────────────────────────────────────────────────

class MatrixTabComponent:
    def __init__(self, ui_window):
        self.ui                 = ui_window
        self.current_org_filter = "FAE"

        self.ui.tabBar_matrix.addTab("FAE")
        self.ui.tabBar_matrix.addTab("Sales")
        self.ui.tabBar_matrix.addTab("Unknown")

        self.ui.btn_refresh_matrix.clicked.connect(self.open_mapping_manager)
        self.ui.btn_clear_matrix.clicked.connect(self.export_matrix_to_excel)

        # ── Replace table_matrix with QTableView for Virtualization ──
        table_widget = self.ui.table_matrix
        layout = self.ui.layout_matrix_tables  # Targeting correct nested layout
        idx = layout.indexOf(table_widget)
        
        self.table_view = QTableView()
        self.table_view.setObjectName("table_matrix")
        self.table_view.horizontalHeader().setVisible(False)
        self.table_view.verticalHeader().setFixedWidth(50)
        
        # Apply dark theme styling to QTableView
        new_style = self.ui.styleSheet().replace('QTableWidget {', 'QTableWidget, QTableView {')
        self.ui.setStyleSheet(new_style)
        
        layout.insertWidget(idx, self.table_view)
        table_widget.deleteLater()
        self.ui.table_matrix = self.table_view

        # Setup MVC Model
        self.model = MatrixTableModel(self.ui)
        self.proxy = MatrixFilterProxyModel(self.ui)
        self.proxy.setSourceModel(self.model)
        self.ui.table_matrix.setModel(self.proxy)

        # ── Configure Header Table ──
        self.ui.header_table.verticalHeader().setFixedWidth(50)
        
        # Make Custom Header read-only
        self.ui.header_table.setEditTriggers(QTableWidget.NoEditTriggers)
        
        # Disable vertical scroll on custom header
        self.ui.header_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.ui.header_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Enable resizing via native horizontal header
        hh = self.ui.header_table.horizontalHeader()
        hh.setVisible(True)
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.sectionResized.connect(
            lambda logicalIndex, oldSize, newSize: self.table_view.setColumnWidth(logicalIndex, newSize)
        )

        self.ui.table_matrix.horizontalScrollBar().valueChanged.connect(
            self.ui.header_table.horizontalScrollBar().setValue
        )
        self.ui.header_table.horizontalScrollBar().valueChanged.connect(
            self.ui.table_matrix.horizontalScrollBar().setValue
        )

        self.ui.header_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.header_table.customContextMenuRequested.connect(
            self.show_column_menu
        )

        self.ui.tabBar_matrix.currentChanged.connect(self.on_tab_changed)

        self.setup_filters()
        self.load_matrix_data()

    # ── Context menu ───────────────────────────────────────────────────────────

    def show_column_menu(self, pos):
        menu = QMenu(self.ui)
        menu.setStyleSheet(
            "QMenu { background: #202025; color: white; border: 1px solid #3a3a40; }"
        )

        for c in range(5):
            item = self.ui.header_table.item(0, c)
            text = item.text() if item else f"Column {c}"
            action = QWidgetAction(menu)
            chk    = QCheckBox(text)
            chk.setStyleSheet(
                "QCheckBox { padding: 5px; background: transparent; color: white; }"
                "QCheckBox:hover { background: #0085ca; }"
            )
            chk.setChecked(not self.ui.header_table.isColumnHidden(c))
            chk.toggled.connect(lambda checked, col=c: self.toggle_column(col, checked))
            action.setDefaultWidget(chk)
            menu.addAction(action)

        menu.addSeparator()
        courses_menu = menu.addMenu("Course Columns")
        courses_menu.setStyleSheet(
            "QMenu { background: #202025; color: white; border: 1px solid #3a3a40; }"
        )

        for c in range(5, self.ui.header_table.columnCount()):
            item = self.ui.header_table.item(2, c)
            if not item:
                continue
            action = QWidgetAction(courses_menu)
            chk    = QCheckBox(item.text())
            chk.setStyleSheet(
                "QCheckBox { padding: 5px; background: transparent; color: white; }"
                "QCheckBox:hover { background: #0085ca; }"
            )
            chk.setChecked(not self.ui.header_table.isColumnHidden(c))
            chk.toggled.connect(lambda checked, col=c: self.toggle_column(col, checked))
            action.setDefaultWidget(chk)
            courses_menu.addAction(action)

        menu.exec_(self.ui.header_table.viewport().mapToGlobal(pos))

    def toggle_column(self, col, show):
        self.ui.header_table.setColumnHidden(col, not show)
        self.ui.table_matrix.setColumnHidden(col, not show)

    # ── Export ─────────────────────────────────────────────────────────────────

    def export_matrix_to_excel(self):
        path, _ = QFileDialog.getSaveFileName(
            self.ui, "Export Matrix", "", "Excel Files (*.xlsx)"
        )
        if not path:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            cols = self.ui.header_table.columnCount()
            rows = self.proxy.rowCount()

            row0, row1, row2 = [], [], []
            curr_cc, curr_gc = "", ""

            for c in range(cols):
                if c < 5:
                    item = self.ui.header_table.item(0, c)
                    row0.append(item.text() if item else "")
                    row1.append("")
                    row2.append("")
                else:
                    if self.ui.header_table.item(0, c):
                        curr_cc = self.ui.header_table.item(0, c).text()
                    if self.ui.header_table.item(1, c):
                        curr_gc = self.ui.header_table.item(1, c).text()
                    row0.append(curr_cc)
                    row1.append(curr_gc)
                    item = self.ui.header_table.item(2, c)
                    row2.append(item.text() if item else "")

            data = []
            for r in range(rows):
                row_data = []
                for c in range(cols):
                    idx = self.proxy.index(r, c)
                    row_data.append(self.proxy.data(idx, Qt.DisplayRole))
                data.append(row_data)

            df = pd.DataFrame(
                data, columns=pd.MultiIndex.from_arrays([row0, row1, row2])
            )
            df.to_excel(path, index=False)
            QMessageBox.information(
                self.ui, "Export Successful",
                f"Matrix successfully exported to:\n{path}",
            )
        except Exception as exc:
            QMessageBox.critical(
                self.ui, "Export Error",
                f"Failed to export matrix:\n{str(exc)}",
            )
        finally:
            QApplication.restoreOverrideCursor()

    # ── Mapping dialog ─────────────────────────────────────────────────────────

    def open_mapping_manager(self):
        dialog = CourseMappingDialog(self.current_org_filter, self.ui)
        if dialog.exec_() == QDialog.Accepted or dialog.courses_deleted:
            self.load_matrix_data()

    # ── Filters ────────────────────────────────────────────────────────────────

    def setup_filters(self):
        self.ui.combo_matrix_region.clear()
        self.ui.combo_matrix_region.addItems(
            ["All Regions", "Americas", "Europe", "China", "Taiwan",
             "Japan", "Korea", "ISEA", "Worldwide"]
        )
        self.ui.combo_matrix_region.currentTextChanged.connect(self.apply_filters)

        self.ui.combo_matrix_code.clear()
        self.ui.combo_matrix_code.addItems(
            ["All Codes", "Low & Mid Power", "High Power", "Automotive", "Motor Driver"]
        )
        self.ui.combo_matrix_code.currentTextChanged.connect(self.apply_filters)

        self.ui.search_matrix.textChanged.connect(self.apply_filters)

    def apply_filters(self):
        self.proxy.set_filters(
            self.ui.search_matrix.text(),
            self.ui.combo_matrix_region.currentText(),
            self.ui.combo_matrix_code.currentText()
        )

    # ── Tab change ─────────────────────────────────────────────────────────────

    def on_tab_changed(self, index):
        self.current_org_filter = ["FAE", "Sales", "Unknown"][index]
        self.load_matrix_data()

    # ── Data fetch ─────────────────────────────────────────────────────────────

    def fetch_matrix_data(self):
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()

            ALLOWED_ORGS = {"FAE", "Sales", "Unknown"}
            if self.current_org_filter not in ALLOWED_ORGS:
                raise ValueError(
                    f"Invalid org filter: {self.current_org_filter!r}"
                )

            cursor.execute(
                """
                SELECT course_id, course_name, control_code, graph_category
                FROM   courses
                WHERE  course_type IN (?, 'Both')
                ORDER BY
                    CASE control_code
                        WHEN 'Low & Mid Power' THEN 1
                        WHEN 'Motor Driver'    THEN 2
                        WHEN 'Automotive'      THEN 3
                        WHEN 'High Power'      THEN 4
                        ELSE 5
                    END ASC,
                    graph_category ASC,
                    course_name    ASC
                """,
                (self.current_org_filter,),
            )
            courses = cursor.fetchall()

            cursor.execute(
                """
                SELECT user_id,
                       first_name || ' ' || last_name,
                       email, location, title, manager,
                       control_codes, region_bucket
                FROM   students
                WHERE  organization = ?
                ORDER BY
                    CASE WHEN region_bucket = 'Worldwide' OR region_bucket IS NULL
                         THEN 1 ELSE 0 END,
                    region_bucket ASC, location ASC, first_name ASC
                """,
                (self.current_org_filter,),
            )
            students = cursor.fetchall()

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
            for uid, cid, percent in cursor.fetchall():
                uid_norm = normalize_uid(uid)
                completions_map.setdefault(uid_norm, {})[cid] = safe_float(percent)

        return courses, students, completions_map

    # ── Table rendering ────────────────────────────────────────────────────────

    def load_matrix_data(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            courses, students, completions_map = self.fetch_matrix_data()
            header_table = self.ui.header_table

            header_table.setUpdatesEnabled(False)
            header_table.clear()

            base_headers = ["Name", "Email", "Location", "Title", "Manager"]
            total_cols   = len(base_headers) + len(courses)

            header_table.setColumnCount(total_cols)
            header_table.setRowCount(3)

            for r in range(3):
                header_table.setVerticalHeaderItem(r, QTableWidgetItem(""))

            # Make native header empty labels so it looks like a clean resize bar
            for c in range(total_cols):
                header_table.setHorizontalHeaderItem(c, QTableWidgetItem(""))

            col_widths = [180, 240, 210, 210, 150]
            for i in range(len(base_headers)):
                header_table.setColumnWidth(i, col_widths[i])
            for i in range(5, total_cols):
                header_table.setColumnWidth(i, 130)

            header_table.setRowHeight(0, 26)
            header_table.setRowHeight(1, 26)
            header_table.setRowHeight(2, 45)
            
            # Increase FixedHeight to accommodate Native Header (approx 25px) + the 3 Rows + Borders
            header_table.setFixedHeight(130)

            font_bold = header_table.font()
            font_bold.setBold(True)
            font_small = header_table.font()
            font_small.setPointSize(9)

            for col_idx, text in enumerate(base_headers):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                item.setBackground(QColor("#202025"))
                item.setForeground(QColor("white"))
                item.setFont(font_bold)
                header_table.setItem(0, col_idx, item)
                header_table.setSpan(0, col_idx, 3, 1)

            current_cc, cc_start_col = None, 5
            current_gc, gc_start_col = None, 5

            for i, (course_id, cname, cc, gc) in enumerate(courses):
                col_idx = 5 + i
                c_item  = QTableWidgetItem(cname if cname else "Unnamed")
                c_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                c_item.setBackground(QColor("#2a2a30"))
                c_item.setForeground(QColor("white"))
                c_item.setFont(font_small)
                header_table.setItem(2, col_idx, c_item)

                if cc != current_cc:
                    if current_cc is not None and (col_idx - cc_start_col) > 1:
                        header_table.setSpan(0, cc_start_col, 1, col_idx - cc_start_col)
                    current_cc, cc_start_col = cc, col_idx
                    cc_item = QTableWidgetItem(cc)
                    cc_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                    cc_item.setBackground(
                        QColor("#4472C4") if cc != "Unassigned" else QColor("#444444")
                    )
                    cc_item.setForeground(QColor("white"))
                    cc_item.setFont(font_bold)
                    header_table.setItem(0, col_idx, cc_item)

                if gc != current_gc or (i > 0 and cc != courses[i - 1][2]):
                    if current_gc is not None and (col_idx - gc_start_col) > 1:
                        header_table.setSpan(1, gc_start_col, 1, col_idx - gc_start_col)
                    current_gc, gc_start_col = gc, col_idx
                    gc_item = QTableWidgetItem(gc)
                    gc_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                    gc_item.setBackground(QColor("#666666"))
                    gc_item.setForeground(QColor("white"))
                    gc_item.setFont(font_bold)
                    header_table.setItem(1, col_idx, gc_item)

            if courses:
                last_col = 5 + len(courses)
                if (last_col - cc_start_col) > 1:
                    header_table.setSpan(0, cc_start_col, 1, last_col - cc_start_col)
                if (last_col - gc_start_col) > 1:
                    header_table.setSpan(1, gc_start_col, 1, last_col - gc_start_col)

            # Generate the virtualized data structure
            table_data = []
            for student in students:
                user_id, name, email, location, title, manager, student_codes, region_bucket = student
                student_codes_list = (
                    [c.strip() for c in student_codes.split(",")]
                    if student_codes else []
                )
                uid_norm = normalize_uid(user_id)

                row_cells = []
                # Add base columns with hidden filter metadata
                row_cells.append({"text": name or "N/A", "codes": student_codes or ""})
                row_cells.append({"text": email or "N/A"})
                row_cells.append({"text": location or "N/A", "region": region_bucket or "Worldwide"})
                row_cells.append({"text": title or "N/A"})
                row_cells.append({"text": manager or "N/A"})

                user_completions = completions_map.get(uid_norm, {})
                for course_id, _, course_control_code, _ in courses:
                    pct = user_completions.get(course_id, 0.0)

                    if (course_control_code == "Unassigned"
                            or course_control_code not in student_codes_list):
                        row_cells.append({"text": "N/A", "bg": None, "fg": self.model.color_gray})
                    elif pct >= 100.0:
                        row_cells.append({"text": "1.00", "bg": self.model.color_green, "fg": Qt.black})
                    elif pct > 0.0:
                        row_cells.append({"text": "0.01", "bg": self.model.color_yellow, "fg": Qt.black})
                    else:
                        row_cells.append({"text": "0.00", "bg": self.model.color_red, "fg": Qt.white})

                table_data.append(row_cells)

            # Push data to model and set columns for the View
            self.model.update_data(table_data)
            
            for i in range(len(base_headers)):
                self.ui.table_matrix.setColumnWidth(i, col_widths[i])
            for i in range(5, total_cols):
                self.ui.table_matrix.setColumnWidth(i, 130)

            self.apply_filters()
        finally:
            header_table.setUpdatesEnabled(True)
            QApplication.restoreOverrideCursor()