from PyQt5.QtWidgets import (
    QTableWidgetItem, QApplication, QDialog, QCheckBox,
    QMessageBox, QTableWidget, QFileDialog, QMenu, QWidgetAction,
    QTreeWidgetItem, QTableView, QHeaderView, QListWidgetItem,
    QInputDialog, QAbstractItemView, QTreeWidget, QListWidget
)
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtCore import Qt, QAbstractTableModel, QSortFilterProxyModel
from PyQt5 import uic
import sqlite3
import pandas as pd
import json
from database import DB_PATH
from data_manager import safe_float, normalize_uid
import os
import sys

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# ── Course Mapping Dialog ──────────────────────────────────────────────────────

class CourseMappingDialog(QDialog):
    def __init__(self, current_org_filter, parent=None):
        super().__init__(parent)
        ui_path = resource_path("dialog_mapping.ui")
        uic.loadUi(ui_path, self)

        self.course_filter  = current_org_filter
        self.prefix = self.course_filter.lower()
        self.courses_deleted = False
        self.setWindowTitle(f"Map Courses to Control Codes ({self.course_filter} View)")

        self.tree_categories.dropEvent = self.tree_drop_event
        self.list_unassigned.dropEvent = self.list_drop_event
        
        self.tree_categories.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_categories.customContextMenuRequested.connect(self.show_tree_context_menu)

        self.btn_save.clicked.connect(self.save_mapping)

        self.setup_tree_structure()
        self.load_data()

    def get_item_level(self, item):
        level = 0
        p = item.parent()
        while p:
            level += 1
            p = p.parent()
        return level

    def show_tree_context_menu(self, pos):
        item = self.tree_categories.itemAt(pos)
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #202025; color: white; border: 1px solid #3a3a40; }
            QMenu::item:selected { background: #0085ca; }
        """)

        if item is None:
            action_add = menu.addAction("Add Category")
            action_add.triggered.connect(self.add_category)
        else:
            level = self.get_item_level(item)
            if level == 0:
                action_add_sub = menu.addAction("Add Sub-category")
                action_add_sub.triggered.connect(lambda: self.add_sub_category(item))
                action_rename = menu.addAction("Rename Category")
                action_rename.triggered.connect(lambda: self.rename_item(item, level))
                action_del = menu.addAction("Delete Category")
                action_del.triggered.connect(lambda: self.delete_item(item, level))
            elif level == 1:
                action_rename = menu.addAction("Rename Sub-category")
                action_rename.triggered.connect(lambda: self.rename_item(item, level))
                action_del = menu.addAction("Delete Sub-category")
                action_del.triggered.connect(lambda: self.delete_item(item, level))
            elif level >= 2:
                action_unassign = menu.addAction("Remove from Category")
                action_unassign.triggered.connect(lambda: self.unassign_course(item))
                action_delete = menu.addAction("Delete Course completely")
                action_delete.triggered.connect(lambda: self.delete_course_completely(item))

        menu.exec_(self.tree_categories.viewport().mapToGlobal(pos))

    def add_category(self):
        name, ok = QInputDialog.getText(self, "Add Category", "Enter new Category name:")
        if ok and name.strip():
            node = QTreeWidgetItem(self.tree_categories, [name.strip()])
            node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)

    def add_sub_category(self, parent_item):
        name, ok = QInputDialog.getText(self, "Add Sub-category", "Enter new Sub-category name:")
        if ok and name.strip():
            node = QTreeWidgetItem(parent_item, [name.strip()])
            node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            parent_item.setExpanded(True)

    def rename_item(self, item, level):
        old_name = item.text(0)
        new_name, ok = QInputDialog.getText(self, "Rename", f"Rename '{old_name}' to:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            
            reply = QMessageBox.question(
                self, "Confirm Rename",
                f"Renaming this will globally update all related courses and student assignments in the database.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                item.setText(0, new_name)
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.cursor()
                    if level == 0:
                        cursor.execute("UPDATE courses SET fae_control_code = ? WHERE fae_control_code = ?", (new_name, old_name))
                        cursor.execute("UPDATE courses SET sales_control_code = ? WHERE sales_control_code = ?", (new_name, old_name))
                        cursor.execute("UPDATE courses SET unknown_control_code = ? WHERE unknown_control_code = ?", (new_name, old_name))
                        
                        cursor.execute("SELECT user_id, control_codes FROM students")
                        records = []
                        for uid, codes in cursor.fetchall():
                            if codes:
                                code_list = [c.strip() for c in codes.split(",")]
                                if old_name in code_list:
                                    code_list = [new_name if c == old_name else c for c in code_list]
                                    records.append((", ".join(code_list), uid))
                        if records:
                            cursor.executemany("UPDATE students SET control_codes = ? WHERE user_id = ?", records)
                    
                    elif level == 1:
                        cursor.execute("UPDATE courses SET fae_graph_category = ? WHERE fae_graph_category = ?", (new_name, old_name))
                        cursor.execute("UPDATE courses SET sales_graph_category = ? WHERE sales_graph_category = ?", (new_name, old_name))
                        cursor.execute("UPDATE courses SET unknown_graph_category = ? WHERE unknown_graph_category = ?", (new_name, old_name))

    def delete_item(self, item, level):
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Deleting '{item.text(0)}' will move all its mapped courses to the Unassigned list in THIS view.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            courses_to_move = []
            def extract_courses(node):
                if node.childCount() == 0 and node.parent() and node.parent().parent():
                    courses_to_move.append(node)
                for i in range(node.childCount()):
                    extract_courses(node.child(i))
                    
            extract_courses(item)
            
            for course_node in courses_to_move:
                list_item = QListWidgetItem(course_node.text(0))
                list_item.setData(Qt.UserRole, course_node.data(0, Qt.UserRole))
                self.list_unassigned.addItem(list_item)
                
            if item.parent():
                item.parent().removeChild(item)
            else:
                self.tree_categories.takeTopLevelItem(self.tree_categories.indexOfTopLevelItem(item))

    def unassign_course(self, item):
        list_item = QListWidgetItem(item.text(0))
        list_item.setData(Qt.UserRole, item.data(0, Qt.UserRole))
        self.list_unassigned.addItem(list_item)
        item.parent().removeChild(item)

    def delete_course_completely(self, item):
        reply = QMessageBox.question(
            self, "Confirm Permanent Delete",
            f"Are you sure you want to PERMANENTLY delete '{item.text(0)}' from the database?\nThis will erase all completion records for this course globally.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            course_id = item.data(0, Qt.UserRole)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM courses WHERE course_id=?", (course_id,))
                conn.execute("DELETE FROM completions WHERE course_id=?", (course_id,))
            item.parent().removeChild(item)
            self.courses_deleted = True

    def tree_drop_event(self, event):
        source = event.source()
        target_item = self.tree_categories.itemAt(event.pos())
        drop_indicator = self.tree_categories.dropIndicatorPosition()

        if source == self.tree_categories:
            dragged_items = self.tree_categories.selectedItems()
            if not dragged_items:
                event.ignore()
                return
            
            dragged_item = dragged_items[0]
            drag_level = self.get_item_level(dragged_item)

            if not target_item:
                if drag_level == 0:
                    idx = self.tree_categories.indexOfTopLevelItem(dragged_item)
                    item = self.tree_categories.takeTopLevelItem(idx)
                    self.tree_categories.addTopLevelItem(item)
                    event.setDropAction(Qt.MoveAction)
                    event.accept()
                else:
                    event.ignore()
                return

            target_level = self.get_item_level(target_item)

            if drag_level == 0:
                if target_level == 0 and drop_indicator != QAbstractItemView.OnItem:
                    curr_idx = self.tree_categories.indexOfTopLevelItem(dragged_item)
                    target_idx = self.tree_categories.indexOfTopLevelItem(target_item)
                    
                    if drop_indicator == QAbstractItemView.BelowItem:
                        target_idx += 1
                    if curr_idx < target_idx:
                        target_idx -= 1 
                        
                    item = self.tree_categories.takeTopLevelItem(curr_idx)
                    self.tree_categories.insertTopLevelItem(target_idx, item)
                    event.setDropAction(Qt.MoveAction)
                    event.accept()
                else:
                    event.ignore()
                return

            elif drag_level == 1:
                if target_level == 0 and drop_indicator == QAbstractItemView.OnItem:
                    dragged_item.parent().removeChild(dragged_item)
                    target_item.addChild(dragged_item)
                    target_item.setExpanded(True)
                    event.setDropAction(Qt.MoveAction)
                    event.accept()
                elif target_level == 1 and drop_indicator != QAbstractItemView.OnItem:
                    parent = target_item.parent()
                    curr_parent = dragged_item.parent()
                    
                    target_idx = parent.indexOfChild(target_item)
                    if drop_indicator == QAbstractItemView.BelowItem:
                        target_idx += 1
                        
                    if curr_parent == parent:
                        curr_idx = parent.indexOfChild(dragged_item)
                        if curr_idx < target_idx:
                            target_idx -= 1 
                            
                    curr_parent.removeChild(dragged_item)
                    parent.insertChild(target_idx, dragged_item)
                    event.setDropAction(Qt.MoveAction)
                    event.accept()
                else:
                    event.ignore()
                return

            elif drag_level == 2:
                if target_level == 1 and drop_indicator == QAbstractItemView.OnItem:
                    dragged_item.parent().removeChild(dragged_item)
                    target_item.addChild(dragged_item)
                    target_item.setExpanded(True)
                    event.setDropAction(Qt.MoveAction)
                    event.accept()
                elif target_level == 2 and drop_indicator != QAbstractItemView.OnItem:
                    parent = target_item.parent()
                    curr_parent = dragged_item.parent()
                    
                    target_idx = parent.indexOfChild(target_item)
                    if drop_indicator == QAbstractItemView.BelowItem:
                        target_idx += 1
                        
                    if curr_parent == parent:
                        curr_idx = parent.indexOfChild(dragged_item)
                        if curr_idx < target_idx:
                            target_idx -= 1 
                            
                    curr_parent.removeChild(dragged_item)
                    parent.insertChild(target_idx, dragged_item)
                    event.setDropAction(Qt.MoveAction)
                    event.accept()
                else:
                    event.ignore()
                return

        elif source == self.list_unassigned:
            if not target_item:
                event.ignore()
                return
                
            target_level = self.get_item_level(target_item)
            
            parent_sub = None
            insert_idx = -1
            
            if target_level == 1:
                if drop_indicator == QAbstractItemView.OnItem:
                    parent_sub = target_item
                    insert_idx = parent_sub.childCount()
                else:
                    event.ignore()
                    return
            elif target_level == 2:
                if drop_indicator != QAbstractItemView.OnItem:
                    parent_sub = target_item.parent()
                    insert_idx = parent_sub.indexOfChild(target_item)
                    if drop_indicator == QAbstractItemView.BelowItem:
                        insert_idx += 1
                else:
                    event.ignore()
                    return
            else:
                event.ignore()
                return

            for item in source.selectedItems():
                course_id = item.data(Qt.UserRole)
                new_item = QTreeWidgetItem([item.text()])
                new_item.setData(0, Qt.UserRole, course_id)
                new_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
                
                parent_sub.insertChild(insert_idx, new_item)
                insert_idx += 1 
                
                row = self.list_unassigned.row(item)
                self.list_unassigned.takeItem(row)
                
            parent_sub.setExpanded(True)
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def list_drop_event(self, event):
        source = event.source()
        if source == self.list_unassigned:
            QListWidget.dropEvent(self.list_unassigned, event)
            event.accept()
        elif source == self.tree_categories:
            for item in source.selectedItems():
                level = self.get_item_level(item)
                if level == 2: 
                    course_id = item.data(0, Qt.UserRole)
                    new_item = QListWidgetItem(item.text(0))
                    new_item.setData(Qt.UserRole, course_id)
                    self.list_unassigned.addItem(new_item)
                    item.parent().removeChild(item)
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def setup_tree_structure(self):
        structure = {}
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_settings WHERE key = 'category_tree'")
            row = cursor.fetchone()
            if row:
                structure = json.loads(row[0])
                
        for control_code, categories in structure.items():
            cc_node = QTreeWidgetItem(self.tree_categories, [control_code])
            cc_node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            for cat in categories:
                cat_node = QTreeWidgetItem(cc_node, [cat])
                cat_node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        self.tree_categories.expandAll()

    def load_data(self):
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            query = f"""
                SELECT course_id, course_name, {self.prefix}_control_code, {self.prefix}_graph_category, {self.prefix}_sort_order 
                FROM courses WHERE course_type IN (?, 'Both')
                ORDER BY {self.prefix}_sort_order ASC, course_name ASC
            """
            cursor.execute(query, (self.course_filter,))

            for cid, cname, cc, gc, sort_order in cursor.fetchall():
                cname_lower = cname.lower()
                if self.course_filter == "FAE" and "sales" in cname_lower: continue
                if self.course_filter == "Sales" and "fae" in cname_lower: continue

                item_text = cname if cname else f"Course {cid}"
                
                if cc == "Unassigned" or gc == "Unassigned":
                    list_item = QListWidgetItem(item_text)
                    list_item.setData(Qt.UserRole, cid)
                    self.list_unassigned.addItem(list_item)
                else:
                    found = False
                    for i in range(self.tree_categories.topLevelItemCount()):
                        parent = self.tree_categories.topLevelItem(i)
                        if parent.text(0) == cc:
                            for j in range(parent.childCount()):
                                child = parent.child(j)
                                if child.text(0) == gc:
                                    course_node = QTreeWidgetItem(child, [item_text])
                                    course_node.setData(0, Qt.UserRole, cid)
                                    course_node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
                                    found = True
                                    break
                        if found: break
                    
                    if not found:
                        list_item = QListWidgetItem(item_text)
                        list_item.setData(Qt.UserRole, cid)
                        self.list_unassigned.addItem(list_item)

    def save_mapping(self):
        tree_data = {}
        for i in range(self.tree_categories.topLevelItemCount()):
            cc_node = self.tree_categories.topLevelItem(i)
            cc_name = cc_node.text(0)
            tree_data[cc_name] = []
            for j in range(cc_node.childCount()):
                gc_node = cc_node.child(j)
                tree_data[cc_name].append(gc_node.text(0))

        records_to_update = []
        managed_course_ids = []

        for i in range(self.tree_categories.topLevelItemCount()):
            cc_node = self.tree_categories.topLevelItem(i)
            for j in range(cc_node.childCount()):
                gc_node = cc_node.child(j)
                for k in range(gc_node.childCount()):
                    course_node = gc_node.child(k)
                    c_id = course_node.data(0, Qt.UserRole)
                    records_to_update.append((cc_node.text(0), gc_node.text(0), k, c_id))
                    managed_course_ids.append(c_id)

        for row in range(self.list_unassigned.count()):
            item = self.list_unassigned.item(row)
            c_id = item.data(Qt.UserRole)
            managed_course_ids.append(c_id)

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            cursor.execute("UPDATE app_settings SET value = ? WHERE key = 'category_tree'", (json.dumps(tree_data),))

            if managed_course_ids:
                placeholders = ",".join("?" * len(managed_course_ids))
                cursor.execute(
                    f"UPDATE courses SET {self.prefix}_control_code='Unassigned', {self.prefix}_graph_category='Unassigned', {self.prefix}_sort_order=999 WHERE course_id IN ({placeholders})",
                    managed_course_ids
                )

            if records_to_update:
                conn.executemany(
                    f"UPDATE courses SET {self.prefix}_control_code=?, {self.prefix}_graph_category=?, {self.prefix}_sort_order=? WHERE course_id=?",
                    records_to_update,
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

        table_widget = self.ui.table_matrix
        layout = self.ui.layout_matrix_tables  
        idx = layout.indexOf(table_widget)
        
        self.table_view = QTableView()
        self.table_view.setObjectName("table_matrix")
        self.table_view.horizontalHeader().setVisible(False)
        self.table_view.verticalHeader().setFixedWidth(50)
        
        new_style = self.ui.styleSheet().replace('QTableWidget {', 'QTableWidget, QTableView {')
        self.ui.setStyleSheet(new_style)
        
        layout.insertWidget(idx, self.table_view)
        table_widget.deleteLater()
        self.ui.table_matrix = self.table_view

        self.model = MatrixTableModel(self.ui)
        self.proxy = MatrixFilterProxyModel(self.ui)
        self.proxy.setSourceModel(self.model)
        self.ui.table_matrix.setModel(self.proxy)

        self.ui.header_table.verticalHeader().setFixedWidth(50)
        self.ui.header_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.ui.header_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.ui.header_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.ui.header_table.setWordWrap(True)

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

    def export_matrix_to_excel(self):
        path, _ = QFileDialog.getSaveFileName(
            self.ui, "Export Matrix", "Filtered_Matrix_Export.xlsx", "Excel Files (*.xlsx)"
        )
        if not path:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            cols = self.ui.header_table.columnCount()
            rows = self.proxy.rowCount()

            row0, row1, row2 = [], [], []
            curr_cc, curr_gc = "", ""

            # Extract Headers from UI
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

            # Extract Data from currently filtered view
            data = []
            for r in range(rows):
                row_data = []
                for c in range(cols):
                    idx = self.proxy.index(r, c)
                    val = self.proxy.data(idx, Qt.DisplayRole)
                    
                    # Convert completion strings to actual floats for Excel to format
                    if c >= 5 and val in ("1.00", "0.01", "0.00"):
                        row_data.append(float(val))
                    else:
                        row_data.append(val)
                data.append(row_data)

            # Create flat DataFrame (bypassing Pandas MultiIndex error)
            df = pd.DataFrame(data)

            try:
                # Use xlsxwriter for advanced layout
                writer = pd.ExcelWriter(path, engine="xlsxwriter")
                
                # Write data starting at row 3 (leaving rows 0, 1, 2 for our custom headers)
                df.to_excel(writer, sheet_name="Matrix", index=False, header=False, startrow=3)
                
                worksheet = writer.sheets["Matrix"]
                workbook = writer.book

                # Create beautiful UI-matching styles
                fmt_base_header = workbook.add_format({'bold': True, 'bg_color': '#202025', 'font_color': 'white', 'border': 1, 'align': 'center', 'valign': 'vcenter'})
                fmt_cc_header = workbook.add_format({'bold': True, 'bg_color': '#4472C4', 'font_color': 'white', 'border': 1, 'align': 'center', 'valign': 'vcenter'})
                fmt_gc_header = workbook.add_format({'bold': True, 'bg_color': '#666666', 'font_color': 'white', 'border': 1, 'align': 'center', 'valign': 'vcenter'})
                fmt_course_header = workbook.add_format({'bold': True, 'bg_color': '#2a2a30', 'font_color': 'white', 'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True})
                pct_format = workbook.add_format({"num_format": "0%"})

                # Manually write the 3-tiered headers
                for c in range(cols):
                    if c < 5:
                        # Merge the base headers (Name, Email, etc.) so they span all 3 header rows
                        worksheet.merge_range(0, c, 2, c, row0[c], fmt_base_header)
                    else:
                        # Stack the matrix headers
                        worksheet.write(0, c, row0[c], fmt_cc_header)
                        worksheet.write(1, c, row1[c], fmt_gc_header)
                        worksheet.write(2, c, row2[c], fmt_course_header)

                # Set column widths
                worksheet.set_column(0, 0, 22) # Name
                worksheet.set_column(1, 1, 35) # Email
                worksheet.set_column(2, 4, 18) # Location, Title, Manager
                worksheet.set_column(5, cols - 1, 14, pct_format) # Course percentages
                
                # Make the course name row taller to fit text wrap
                worksheet.set_row(2, 60)

                writer.close()
            except ImportError:
                # Safe fallback if xlsxwriter is missing
                fallback_df = pd.DataFrame([row0, row1, row2] + data)
                fallback_df.to_excel(path, index=False, header=False)

            QMessageBox.information(self.ui, "Export Successful", f"Filtered Matrix successfully exported to:\n{path}")
            
        except Exception as exc:
            QMessageBox.critical(self.ui, "Export Error", f"Failed to export matrix:\n{str(exc)}")
        finally:
            QApplication.restoreOverrideCursor()

    def open_mapping_manager(self):
        dialog = CourseMappingDialog(self.current_org_filter, self.ui)
        if dialog.exec_() == QDialog.Accepted or dialog.courses_deleted:
            self.load_matrix_data()

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

    def on_tab_changed(self, index):
        self.current_org_filter = ["FAE", "Sales", "Unknown"][index]
        self.load_matrix_data()

    def fetch_matrix_data(self):
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()

            ALLOWED_ORGS = {"FAE", "Sales", "Unknown"}
            if self.current_org_filter not in ALLOWED_ORGS:
                raise ValueError(
                    f"Invalid org filter: {self.current_org_filter!r}"
                )

            cursor.execute("SELECT value FROM app_settings WHERE key = 'category_tree'")
            row = cursor.fetchone()
            structure = json.loads(row[0]) if row else {}
            
            cc_order = {cc: i for i, cc in enumerate(structure.keys())}
            
            prefix = self.current_org_filter.lower()
            cursor.execute(f"""
                SELECT course_id, course_name, {prefix}_control_code, {prefix}_graph_category, {prefix}_sort_order
                FROM   courses
                WHERE  course_type IN (?, 'Both')
            """, (self.current_org_filter,))
            raw_courses = cursor.fetchall()
            
            filtered_courses = []
            for cid, cname, cc, gc, sort_order in raw_courses:
                if cc == "Unassigned" or gc == "Unassigned": 
                    continue # Exclude unassigned courses from Matrix View
                    
                cname_lower = cname.lower()
                if self.current_org_filter == "FAE" and "sales" in cname_lower: continue
                if self.current_org_filter == "Sales" and "fae" in cname_lower: continue
                filtered_courses.append((cid, cname, cc, gc, sort_order))
                
            filtered_courses.sort(key=lambda x: (
                cc_order.get(x[2], 999), 
                x[3], 
                x[4], 
                x[1]
            ))
            courses = filtered_courses

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

    def load_matrix_data(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            courses, students, completions_map = self.fetch_matrix_data()
            header_table = self.ui.header_table

            # Crucial fix to resolve "span cannot overlap" bug
            header_table.clearSpans()
            header_table.setUpdatesEnabled(False)
            header_table.clear()

            base_headers = ["Name", "Email", "Location", "Title", "Manager"]
            total_cols   = len(base_headers) + len(courses)

            header_table.setColumnCount(total_cols)
            header_table.setRowCount(3)

            for r in range(3):
                header_table.setVerticalHeaderItem(r, QTableWidgetItem(""))

            for c in range(total_cols):
                header_table.setHorizontalHeaderItem(c, QTableWidgetItem(""))

            col_widths = [180, 240, 210, 210, 150]
            for i in range(len(base_headers)):
                header_table.setColumnWidth(i, col_widths[i])
            for i in range(5, total_cols):
                header_table.setColumnWidth(i, 130)

            # Updated header row heights for readability
            header_table.setRowHeight(0, 30)
            header_table.setRowHeight(1, 30)
            header_table.setRowHeight(2, 90)
            header_table.setFixedHeight(170)

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

            for i, (course_id, cname, cc, gc, sort_order) in enumerate(courses):
                col_idx = 5 + i
                c_item  = QTableWidgetItem(cname if cname else "Unnamed")
                c_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                c_item.setBackground(QColor("#2a2a30"))
                c_item.setForeground(QColor("white"))
                c_item.setFont(font_small)
                header_table.setItem(2, col_idx, c_item)

                if current_cc != cc:
                    if current_cc is not None and (col_idx - cc_start_col) > 1:
                        header_table.setSpan(0, cc_start_col, 1, col_idx - cc_start_col)
                    current_cc = cc
                    cc_start_col = col_idx
                    cc_item = QTableWidgetItem(cc)
                    cc_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                    cc_item.setBackground(QColor("#4472C4"))
                    cc_item.setForeground(QColor("white"))
                    cc_item.setFont(font_bold)
                    header_table.setItem(0, col_idx, cc_item)

                if current_gc != gc or cc_start_col == col_idx:
                    if current_gc is not None and (col_idx - gc_start_col) > 1:
                        header_table.setSpan(1, gc_start_col, 1, col_idx - gc_start_col)
                    current_gc = gc
                    gc_start_col = col_idx
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

            table_data = []
            for student in students:
                user_id, name, email, location, title, manager, student_codes, region_bucket = student
                student_codes_list = (
                    [c.strip() for c in student_codes.split(",")]
                    if student_codes else []
                )
                uid_norm = normalize_uid(user_id)

                row_cells = []
                row_cells.append({"text": name or "N/A", "codes": student_codes or ""})
                row_cells.append({"text": email or "N/A"})
                row_cells.append({"text": location or "N/A", "region": region_bucket or "Worldwide"})
                row_cells.append({"text": title or "N/A"})
                row_cells.append({"text": manager or "N/A"})

                user_completions = completions_map.get(uid_norm, {})
                for course_id, _, course_control_code, _, _ in courses:
                    pct = user_completions.get(course_id, 0.0)

                    if course_control_code not in student_codes_list:
                        row_cells.append({"text": "N/A", "bg": None, "fg": self.model.color_gray})
                    elif pct >= 100.0:
                        row_cells.append({"text": "1.00", "bg": self.model.color_green, "fg": Qt.black})
                    elif pct > 0.0:
                        row_cells.append({"text": "0.01", "bg": self.model.color_yellow, "fg": Qt.black})
                    else:
                        row_cells.append({"text": "0.00", "bg": self.model.color_red, "fg": Qt.white})

                table_data.append(row_cells)

            self.model.update_data(table_data)
            
            for i in range(len(base_headers)):
                self.ui.table_matrix.setColumnWidth(i, col_widths[i])
            for i in range(5, total_cols):
                self.ui.table_matrix.setColumnWidth(i, 130)

            self.apply_filters()
        finally:
            header_table.setUpdatesEnabled(True)
            QApplication.restoreOverrideCursor()