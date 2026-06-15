import csv
import io
import os
import glob
import re

from PyQt5.QtWidgets import (
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox, QApplication,
)
from PyQt5.QtCore import Qt, QSettings
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import numpy as np
import sqlite3

from data_manager import safe_float, normalize_uid
from database import DB_PATH

# ── Team Analytics Controller (FAE & Sales) ───────────────────────────────────

class TeamAnalyticsController:
    """Controller linking UI elements from design.ui to analytics logic for FAE and Sales."""

    def __init__(self, team_name, table, scroll_area, btn_export, main_ui):
        self.team_name  = team_name
        self.table      = table
        self.scroll_area = scroll_area
        self.btn_export = btn_export
        self.main_ui    = main_ui

        self.analytics_data = {}
        self.regions        = []
        self.categories     = []
        
        self.is_loaded      = False

        self.setup_ui_elements()
        self.btn_export.clicked.connect(self.export_analytics)

    def setup_ui_elements(self):
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)

        if hasattr(self, "figure"):
            plt.close(self.figure)

        self.figure = plt.figure(figsize=(11, 12))
        self.figure.patch.set_facecolor("#121214")
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setStyleSheet("background-color: transparent;")
        self.canvas.setMinimumSize(1800, 1800)
        
        self.scroll_area.setWidget(self.canvas)

    def auto_load_data(self):
        if self.is_loaded:
            return

        settings = QSettings("PowerIntegrations", "PowerTrack")
        folder   = settings.value(
            "current_data_folder",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"),
        )

        pattern1 = os.path.join(folder, f"*Skilljar Metric*{self.team_name}*.csv")
        pattern2 = os.path.join(folder, "**", f"*Skilljar Metric*{self.team_name}*.csv")
        files = list(set(glob.glob(pattern1) + glob.glob(pattern2, recursive=True)))

        loaded_from_csv = False

        if files:
            if len(files) > 1:
                files.sort(key=os.path.getmtime, reverse=True)
            try:
                self.parse_csv_for_analytics(files[0])
                loaded_from_csv = True
            except Exception as exc:
                print(f"[WARN] Failed to parse CSV for {self.team_name}: {exc}. Falling back to DB.")

        if not loaded_from_csv:
            try:
                self.calculate_analytics_from_db()
                print(f"[INFO] Successfully loaded {self.team_name} analytics from Database fallback.")
            except Exception as exc:
                print(f"[ERROR] DB Fallback failed for {self.team_name}: {exc}")
                self.btn_export.setEnabled(False)
                self.table.clearContents()
                plt.close(self.figure)
                self.figure = plt.figure(figsize=(11, 12))
                self.figure.patch.set_facecolor("#121214")
                self.canvas.figure = self.figure
                self.canvas.draw()
                self.is_loaded = True 
                return

        self.populate_table()
        self.draw_charts()
        self.btn_export.setEnabled(True)
        self.is_loaded = True

    def calculate_analytics_from_db(self):
        self.analytics_data.clear()
        self.regions.clear()
        self.categories.clear()

        self.regions = ["Taiwan", "China", "Korea", "Japan", "US/Canada", "Europe", "ISEA", "Worldwide"]
        valid_cats = [
            "InnoSwitch", "LinkSwitch", "LYTSwitch", "Other",
            "Motor Driver", "Automotive", "High Power", "All Course"
        ]
        self.categories = valid_cats

        db_region_map = {
            "Taiwan": "Taiwan",
            "China": "China",
            "Korea": "Korea",
            "Japan": "Japan",
            "Americas": "US/Canada",
            "Europe": "Europe",
            "ISEA": "ISEA"
        }

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            prefix = self.team_name.lower()
            cursor.execute(f"SELECT course_id, {prefix}_control_code, {prefix}_graph_category FROM courses")
            course_map = {str(r[0]): (r[1], r[2]) for r in cursor.fetchall()}

            cursor.execute(
                "SELECT user_id, region_bucket, control_codes FROM students WHERE organization = ?",
                (self.team_name,)
            )
            students = cursor.fetchall()

            cursor.execute(
                """
                SELECT c.user_id, c.course_id, c.percent_complete
                FROM completions c
                JOIN students s ON s.user_id = c.user_id
                WHERE s.organization = ?
                """,
                (self.team_name,)
            )
            completions_map = {}
            for uid, cid, pct in cursor.fetchall():
                uid_str = normalize_uid(uid)
                completions_map.setdefault(uid_str, {})[str(cid)] = safe_float(pct)

        cat_totals = {cat: {reg: [] for reg in self.regions} for cat in valid_cats}

        for st in students:
            user_id, region_bucket, control_codes = st
            uid_str = normalize_uid(user_id)

            my_codes = (
                [c.strip() for c in str(control_codes).split(",")]
                if control_codes and str(control_codes).lower() not in ("nan", "n/a", "none", "")
                else []
            )
            if not my_codes:
                continue

            my_req_courses = [cid for cid, (cc, gc) in course_map.items() if cc in my_codes]
            if not my_req_courses:
                continue

            courses_by_cat = {cat: [] for cat in valid_cats}
            for cid in my_req_courses:
                gc = course_map[cid][1]
                if gc in valid_cats:
                    courses_by_cat[gc].append(cid)
                courses_by_cat["All Course"].append(cid)

            chart_region = db_region_map.get(region_bucket)
            student_completions = completions_map.get(uid_str, {})

            for cat, cids in courses_by_cat.items():
                if not cids:
                    continue
                total_pct = sum(student_completions.get(cid, 0.0) for cid in cids)
                avg_pct = total_pct / len(cids)
                cat_totals[cat]["Worldwide"].append(avg_pct)
                if chart_region:
                    cat_totals[cat][chart_region].append(avg_pct)

        for cat in valid_cats:
            row_data = []
            for reg in self.regions:
                vals = cat_totals[cat][reg]
                row_data.append((sum(vals) / len(vals)) if vals else 0.0)
            self.analytics_data[cat] = row_data

    def parse_csv_for_analytics(self, filepath):
        self.analytics_data.clear()
        self.regions.clear()
        self.categories.clear()

        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            rows = list(csv.reader(f))

        start_r, start_c = next(
            ((r, c) for r, row in enumerate(rows) for c, cell in enumerate(row) if cell.strip() == "Category Team"),
            (-1, -1),
        )
        if start_r == -1:
            raise ValueError("Could not find 'Category Team' summary block.")

        self.regions = [x.strip() for x in rows[start_r][start_c + 1: start_c + 9]]
        valid_cats = [
            "InnoSwitch", "LinkSwitch", "LYTSwitch", "Other",
            "Motor Driver", "Automotive", "High Power", "All Course",
        ]

        for r_idx in range(start_r + 1, min(start_r + 20, len(rows))):
            cat = rows[r_idx][start_c].strip()
            if cat in valid_cats:
                self.categories.append(cat)
                self.analytics_data[cat] = [
                    safe_float(v) for v in rows[r_idx][start_c + 1: start_c + 9]
                ]

    def populate_table(self):
        self.table.setUpdatesEnabled(False)
        self.table.clear()

        headers = ["Category"] + self.regions
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.categories))

        for r_idx, cat in enumerate(self.categories):
            item_cat = QTableWidgetItem(cat)
            item_cat.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            
            font = item_cat.font()
            font.setBold(True)
            item_cat.setFont(font)
            
            self.table.setItem(r_idx, 0, item_cat)

            for c_idx, val in enumerate(self.analytics_data[cat]):
                item_val = QTableWidgetItem(
                    f"{val:.2f}%" if val > 0 else "0.00%"
                )
                item_val.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                if val == 0:
                    item_val.setForeground(Qt.gray)
                self.table.setItem(r_idx, c_idx + 1, item_val)

        self.table.horizontalHeader().setMinimumSectionSize(110)
        if self.table.columnCount() > 0:
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            for i in range(1, self.table.columnCount()):
                self.table.horizontalHeader().setSectionResizeMode(i, QHeaderView.Interactive)
                self.table.setColumnWidth(i, 110)

        self.table.setUpdatesEnabled(True)

    def draw_charts(self):
        plt.close(self.figure)
        self.figure  = plt.figure(figsize=(11, 12))
        self.figure.patch.set_facecolor("#121214")
        self.canvas.figure = self.figure

        if not self.categories:
            self.canvas.draw()
            return

        chart_cats   = [c for c in self.categories if c != "All Course"]
        regions_no_ww = self.regions[:-1]
        ww_idx       = self.regions.index("Worldwide")

        ax1 = self.figure.add_subplot(211)
        ax1.set_facecolor("#121214")
        ax1.grid(axis="y", linestyle="-", color="#3a3a40", alpha=0.7, zorder=0)
        x, width = np.arange(len(chart_cats)), 0.11
        reg_colors = ["#4472C4", "#ED7D31", "#A5A5A5", "#FFC000", "#5B9BD5", "#00B050", "#264478"]

        for i, reg in enumerate(regions_no_ww):
            ax1.bar(
                x + (width * i),
                [self.analytics_data[cat][i] for cat in chart_cats],
                width, label=reg,
                color=reg_colors[i % len(reg_colors)],
                edgecolor="black", zorder=3,
            )

        ww_vals = [self.analytics_data[cat][ww_idx] for cat in chart_cats]
        line_x  = x + width * ((len(regions_no_ww) - 1) / 2)
        ax1.plot(
            line_x, ww_vals, color="#C65911",
            marker="o", linewidth=2, label="Worldwide", zorder=4,
        )
        for i, val in enumerate(ww_vals):
            ax1.text(
                line_x[i], val + 3, f"{val:.1f}%",
                ha="center", va="bottom", color="white",
                fontweight="bold", fontsize=9,
            )

        ax1.set_title(f"{self.team_name} Team", fontsize=12, fontweight="bold", color="white", pad=15)
        ax1.set_ylabel("Course Completion", color="white", fontweight="bold")
        ax1.set_xticks(line_x)
        ax1.set_xticklabels(chart_cats)
        ax1.set_ylim(0, 105)
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}%"))
        ax1.tick_params(colors="white")
        ax1.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, labelcolor="white")

        ax2 = self.figure.add_subplot(212)
        ax2.set_facecolor("#121214")
        ax2.grid(axis="y", linestyle="-", color="#3a3a40", alpha=0.7, zorder=0)
        x2, width2 = np.arange(len(self.regions)), 0.12
        cat_colors = ["#00B050", "#7030A0", "#C65911", "#7F7F7F", "#4472C4", "#548235", "#000000"]

        for i, cat in enumerate(chart_cats):
            ax2.bar(
                x2 + (width2 * i),
                self.analytics_data[cat],
                width2, label=cat,
                color=cat_colors[i % len(cat_colors)],
                edgecolor="black", zorder=3,
            )

        center_x2 = x2 + width2 * ((len(chart_cats) - 1) / 2)
        ax2.set_title(f"{self.team_name} Team", fontsize=12, fontweight="bold", color="white", pad=15)
        ax2.set_ylabel("Course Completion", color="white", fontweight="bold")
        ax2.set_xticks(center_x2)
        ax2.set_xticklabels(self.regions)
        ax2.set_ylim(0, 105)
        ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}%"))
        ax2.tick_params(colors="white")
        ax2.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, labelcolor="white")

        self.figure.tight_layout(rect=[0, 0, 0.88, 1])
        self.canvas.draw()

    def export_analytics(self):
        path, _ = QFileDialog.getSaveFileName(
            self.main_ui, "Export Analytics",
            f"{self.team_name}_Analytics_Export.xlsx", "Excel Files (*.xlsx)",
        )
        if not path:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            # 1. Pull data directly from the currently viewed table
            data = []
            headers = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
            
            for row in range(self.table.rowCount()):
                row_data = {}
                for col in range(self.table.columnCount()):
                    item = self.table.item(row, col)
                    val = item.text() if item else ""
                    
                    if "%" in val:
                        try:
                            row_data[headers[col]] = float(val.replace("%", "")) / 100.0
                        except:
                            row_data[headers[col]] = val
                    else:
                        row_data[headers[col]] = val
                data.append(row_data)

            df = pd.DataFrame(data)

            # Pre-render graph to a memory buffer
            img_data = io.BytesIO()
            self.figure.savefig(img_data, format="png", facecolor=self.figure.get_facecolor(), edgecolor='none', bbox_inches='tight')

            # 2. Apply pristine formatting and embed graphs
            try:
                # Attempt to use xlsxwriter for perfect image placement
                writer = pd.ExcelWriter(path, engine="xlsxwriter")
                df.to_excel(writer, sheet_name="Analytics", index=False)
                worksheet = writer.sheets["Analytics"]

                pct_format = writer.book.add_format({"num_format": "0.00%"})
                worksheet.set_column(1, len(headers) - 1, 15, pct_format)
                worksheet.set_column(0, 0, 25)

                header_format = writer.book.add_format({'bold': True, 'bg_color': '#202025', 'font_color': 'white', 'border': 1})
                for col_num, value in enumerate(df.columns.values):
                    worksheet.write(0, col_num, value, header_format)

                img_data.seek(0)
                # Scale slightly so it's not gigantic in the Excel file
                worksheet.insert_image(len(df) + 3, 0, "chart.png", {"image_data": img_data, "x_scale": 0.7, "y_scale": 0.7})
                writer.close()

            except ImportError:
                # Fallback: xlsxwriter missing, use openpyxl standard export
                df.to_excel(path, sheet_name="Analytics", index=False)
                
                graph_added = False
                try:
                    import openpyxl
                    from openpyxl.drawing.image import Image as xlImage
                    
                    wb = openpyxl.load_workbook(path)
                    ws = wb["Analytics"]
                    
                    img_data.seek(0)
                    xl_img = xlImage(img_data)
                    xl_img.width = int(xl_img.width * 0.7)
                    xl_img.height = int(xl_img.height * 0.7)
                    
                    ws.add_image(xl_img, f"A{len(df) + 4}")
                    wb.save(path)
                    graph_added = True
                except Exception as inner_exc:
                    print(f"[ERROR] Openpyxl image fallback failed: {inner_exc}")
                
                if not graph_added:
                    QMessageBox.warning(
                        self.main_ui, "Missing Library",
                        "The table exported successfully, but the graph was skipped.\n\n"
                        "To include graphs in the Excel export, please ensure the 'xlsxwriter' library is installed:\n"
                        "pip install xlsxwriter"
                    )

            QMessageBox.information(
                self.main_ui, "Export Successful",
                f"{self.team_name} Analytics exported successfully to:\n{path}",
            ) 

        except Exception as exc:
            QMessageBox.critical(self.main_ui, "Export Error", f"Failed to export analytics:\n{exc}")
        finally:
            QApplication.restoreOverrideCursor()


# ── Summary Analytics Controller (Current + Trend) ────────────────────────────

class SummaryAnalyticsController:
    """Controller linking UI elements for the Summary (Current Month and Yearly Trends)."""

    def __init__(self, main_ui):
        self.ui = main_ui
        
        self.table_current = main_ui.table_summary_current
        self.scroll_current = main_ui.scroll_summary_current
        self.table_trend = main_ui.table_summary_trend
        self.scroll_trend = main_ui.scroll_summary_trend
        
        self.btn_export = main_ui.btn_export_current_summary
        self.btn_load = main_ui.btn_load_trend
        
        self.btn_export.clicked.connect(self.export_current_summary)
        self.btn_load.clicked.connect(self.load_trend_folder)

        self.regions = ["Taiwan", "China", "Korea", "Japan", "US/Canada", "Europe", "ISEA", "Worldwide"]
        self.months  = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        self.teams   = ["FAE", "Sales", "FAE+Sales"]
        
        self.current_data = {}
        self.trend_data = {}
        
        self.is_loaded = False
        
        self.setup_ui_elements()

    def setup_ui_elements(self):
        self.fig_current = plt.figure(figsize=(11, 15))
        self.fig_current.patch.set_facecolor("#121214")
        self.canvas_current = FigureCanvas(self.fig_current)
        self.canvas_current.setStyleSheet("background-color: transparent;")
        
        self.canvas_current.setMinimumSize(1000, 1550)
        self.scroll_current.setWidget(self.canvas_current)
        
        self.fig_trend = plt.figure(figsize=(11, 15))
        self.fig_trend.patch.set_facecolor("#121214")
        self.canvas_trend = FigureCanvas(self.fig_trend)
        self.canvas_trend.setStyleSheet("background-color: transparent;")
        
        self.canvas_trend.setMinimumSize(1000, 1550)
        self.scroll_trend.setWidget(self.canvas_trend)

    def auto_load_data(self):
        if self.is_loaded:
            return
            
        try:
            self.calculate_current_summary_from_db()
            self.populate_current_table()
            self.draw_current_charts()
            
            if not self.trend_data:
                self.initialize_empty_trend_data()
                self.populate_trend_table()
                self.draw_trend_charts()
                
            self.is_loaded = True
        except Exception as exc:
            print(f"[ERROR] generating Current Summary: {exc}")

    def calculate_current_summary_from_db(self):
        self.current_data = {t: {"Published": 0, "Regions": {r: 0.0 for r in self.regions}} for t in self.teams}
        
        regions_db_map = {
            "Taiwan": "Taiwan", "China": "China", "Korea": "Korea", "Japan": "Japan",
            "US/Canada": "Americas", "Europe": "Europe", "ISEA": "ISEA", "Worldwide": "Worldwide"
        }

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT course_id, fae_control_code, sales_control_code, course_type FROM courses")
            courses = cursor.fetchall()
            
            fae_courses   = [str(r[0]) for r in courses if r[3] in ("FAE", "Both")]
            sales_courses = [str(r[0]) for r in courses if r[3] in ("Sales", "Both")]
            both_courses  = list(set(fae_courses + sales_courses))

            cursor.execute(
                "SELECT user_id, organization, region_bucket, control_codes "
                "FROM students WHERE organization IN ('FAE', 'Sales')"
            )
            students = cursor.fetchall()

            cursor.execute(
                """
                SELECT c.user_id, c.course_id, c.percent_complete
                FROM completions c JOIN students s ON s.user_id = c.user_id
                WHERE s.organization IN ('FAE', 'Sales')
                """
            )
            completions_map = {}
            for uid, cid, pct in cursor.fetchall():
                uid_str = normalize_uid(uid)
                completions_map.setdefault(uid_str, {})[str(cid)] = safe_float(pct)

        teams_config = {
            "FAE":       {"orgs": ["FAE"],          "pub": len(fae_courses)},
            "Sales":     {"orgs": ["Sales"],        "pub": len(sales_courses)},
            "FAE+Sales": {"orgs": ["FAE", "Sales"], "pub": len(both_courses)},
        }

        for team, t_info in teams_config.items():
            self.current_data[team]["Published"] = t_info["pub"]
            region_totals = {r: [] for r in regions_db_map}

            for st in students:
                user_id, org, region, control_codes = st
                if org not in t_info["orgs"]:
                    continue

                uid_str  = normalize_uid(user_id)
                my_codes = (
                    [c.strip() for c in str(control_codes).split(",")]
                    if control_codes and str(control_codes).lower() not in ("nan", "n/a", "none", "") else []
                )
                
                idx = 1 if org == 'FAE' else 2
                my_req_courses = [str(cid) for cid, fae_cc, sales_cc, ct in courses if (fae_cc if idx==1 else sales_cc) in my_codes]

                if my_req_courses:
                    total_pct = sum(completions_map.get(uid_str, {}).get(cid, 0.0) for cid in my_req_courses)
                    avg_pct = total_pct / len(my_req_courses)
                    
                    region_totals["Worldwide"].append(avg_pct)
                    for out_reg, db_reg in regions_db_map.items():
                        if out_reg != "Worldwide" and region == db_reg:
                            region_totals[out_reg].append(avg_pct)

            for r_name in regions_db_map:
                vals = region_totals[r_name]
                self.current_data[team]["Regions"][r_name] = (sum(vals) / len(vals)) if vals else 0.0

    def populate_current_table(self):
        self.table_current.setUpdatesEnabled(False)
        self.table_current.clear()

        headers = ["Team", "Published Courses"] + self.regions
        self.table_current.setColumnCount(len(headers))
        self.table_current.setHorizontalHeaderLabels(headers)
        self.table_current.setRowCount(len(self.teams))

        for r_idx, team in enumerate(self.teams):
            item_team = QTableWidgetItem(team)
            item_team.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            font = item_team.font()
            font.setBold(True)
            item_team.setFont(font)
            self.table_current.setItem(r_idx, 0, item_team)

            pub_count = self.current_data[team]["Published"]
            item_pub = QTableWidgetItem(str(pub_count))
            item_pub.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            self.table_current.setItem(r_idx, 1, item_pub)

            for c_idx, reg in enumerate(self.regions):
                val = self.current_data[team]["Regions"][reg]
                item_val = QTableWidgetItem(f"{val:.2f}%" if val > 0 else "0.00%")
                item_val.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                if val == 0: item_val.setForeground(Qt.gray)
                self.table_current.setItem(r_idx, c_idx + 2, item_val)

        self.table_current.horizontalHeader().setMinimumSectionSize(110)
        self.table_current.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, self.table_current.columnCount()):
            self.table_current.horizontalHeader().setSectionResizeMode(i, QHeaderView.Interactive)
            self.table_current.setColumnWidth(i, 110)

        self.table_current.setUpdatesEnabled(True)

    def draw_current_charts(self):
        plt.close(self.fig_current)
        self.fig_current = plt.figure(figsize=(11, 15))
        self.fig_current.patch.set_facecolor("#121214")
        self.canvas_current.figure = self.fig_current

        x = np.arange(len(self.regions))
        colors = ["#4472C4", "#ED7D31", "#A5A5A5", "#FFC000", "#5B9BD5", "#00B050", "#264478", "#C65911"]

        for i, team in enumerate(self.teams):
            ax = self.fig_current.add_subplot(3, 1, i + 1)
            ax.set_facecolor("#121214")
            ax.grid(axis="y", linestyle="-", color="#3a3a40", alpha=0.7, zorder=0)

            vals = [self.current_data[team]["Regions"][r] for r in self.regions]
            bars = ax.bar(x, vals, width=0.6, color=colors[:len(vals)], edgecolor="black", zorder=3)

            for bar in bars:
                yval = bar.get_height()
                if yval > 0:
                    ax.text(bar.get_x() + bar.get_width()/2, yval + 2, f"{yval:.1f}%", 
                            ha="center", va="bottom", color="white", fontweight="bold", fontsize=9)

            ax.set_title(f"{team} Team - Current Analytics", color="white", fontweight="bold", pad=15)
            ax.set_ylabel("Course Completion", color="white", fontweight="bold")
            ax.set_xticks(x)
            ax.set_xticklabels(self.regions, color="white")
            ax.set_ylim(0, 105)
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}%"))
            ax.tick_params(colors="white")

        self.fig_current.tight_layout(rect=[0, 0, 1, 1])
        self.canvas_current.draw()

    def export_current_summary(self):
        path, _ = QFileDialog.getSaveFileName(
            self.ui, "Export Current Month Summary",
            "01-JAN-SUMMARY.xlsx", "Excel Files (*.xlsx)"
        )
        if not path:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            # 1. Pull data directly from the currently viewed summary table
            data = []
            headers = [self.table_current.horizontalHeaderItem(i).text() for i in range(self.table_current.columnCount())]
            
            for row in range(self.table_current.rowCount()):
                row_data = {}
                for col in range(self.table_current.columnCount()):
                    item = self.table_current.item(row, col)
                    val = item.text() if item else ""
                    
                    if "%" in val:
                        try:
                            row_data[headers[col]] = float(val.replace("%", "")) / 100.0
                        except:
                            row_data[headers[col]] = val
                    else:
                        try:
                            # Catch "Published Courses" whole numbers
                            row_data[headers[col]] = int(val) if val.isdigit() else val
                        except:
                            row_data[headers[col]] = val
                data.append(row_data)

            df = pd.DataFrame(data)

            # Pre-render graph to a memory buffer
            img_data = io.BytesIO()
            self.fig_current.savefig(img_data, format="png", facecolor=self.fig_current.get_facecolor(), edgecolor='none', bbox_inches='tight')

            # 2. Apply pristine formatting and embed graphs
            try:
                writer = pd.ExcelWriter(path, engine="xlsxwriter")
                df.to_excel(writer, sheet_name="Summary", index=False)
                worksheet = writer.sheets["Summary"]
                
                pct_format = writer.book.add_format({"num_format": "0.00%"})
                worksheet.set_column(2, len(headers) - 1, 15, pct_format)
                worksheet.set_column(0, 1, 20)

                header_format = writer.book.add_format({'bold': True, 'bg_color': '#202025', 'font_color': 'white', 'border': 1})
                for col_num, value in enumerate(df.columns.values):
                    worksheet.write(0, col_num, value, header_format)

                img_data.seek(0)
                # Scale slightly so it's not gigantic in the Excel file
                worksheet.insert_image(len(df) + 3, 0, "chart.png", {"image_data": img_data, "x_scale": 0.7, "y_scale": 0.7})
                writer.close()

            except ImportError:
                # Fallback: xlsxwriter missing, use openpyxl standard export
                df.to_excel(path, sheet_name="Summary", index=False)
                
                graph_added = False
                try:
                    import openpyxl
                    from openpyxl.drawing.image import Image as xlImage
                    
                    wb = openpyxl.load_workbook(path)
                    ws = wb["Summary"]
                    
                    img_data.seek(0)
                    xl_img = xlImage(img_data)
                    xl_img.width = int(xl_img.width * 0.7)
                    xl_img.height = int(xl_img.height * 0.7)
                    
                    ws.add_image(xl_img, f"A{len(df) + 4}")
                    wb.save(path)
                    graph_added = True
                except Exception as inner_exc:
                    print(f"[ERROR] Openpyxl image fallback failed: {inner_exc}")
                
                if not graph_added:
                    QMessageBox.warning(
                        self.ui, "Missing Library",
                        "The summary table exported successfully, but the graph was skipped.\n\n"
                        "To include graphs in the Excel export, please ensure the 'xlsxwriter' library is installed:\n"
                        "pip install xlsxwriter"
                    )

            QMessageBox.information(self.ui, "Export Successful", f"Summary successfully saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self.ui, "Export Error", f"Failed to export:\n{exc}")
        finally:
            QApplication.restoreOverrideCursor()

    # --- Trend Folder Logic ---

    def initialize_empty_trend_data(self):
        self.trend_data = {
            team: {"published": [np.nan]*12, "regions": {r: [np.nan]*12 for r in self.regions}}
            for team in self.teams
        }

    def load_trend_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self.ui, "Select Folder ")
        if not folder_path:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.initialize_empty_trend_data()
            
            files = glob.glob(os.path.join(folder_path, "*.xlsx"))
            files += glob.glob(os.path.join(folder_path, "*.xls"))

            parsed_count = 0
            for file in files:
                filename = os.path.basename(file).upper()
                
                if filename.startswith("~$"):
                    continue
                
                match = re.search(r'^(0[1-9]|1[0-2])[-_]', filename)
                month_idx = -1
                
                if match:
                    month_idx = int(match.group(1)) - 1
                else:
                    for i, m in enumerate(self.months):
                        if m.upper() in filename:
                            month_idx = i
                            break
                            
                if month_idx == -1:
                    continue

                df = pd.read_excel(file)
                for _, row in df.iterrows():
                    team = row.get("Team")
                    if team in self.teams:
                        pub = safe_float(row.get("Published Courses", 0))
                        self.trend_data[team]["published"][month_idx] = pub
                        for reg in self.regions:
                            val = row.get(reg, np.nan)
                            if pd.notna(val):
                                if isinstance(val, str) and '%' in val:
                                    val = safe_float(val.replace('%', ''))
                                elif isinstance(val, (int, float)) and 0.0 < val <= 1.0:
                                    val = val * 100
                                self.trend_data[team]["regions"][reg][month_idx] = float(val)
                parsed_count += 1

            self.populate_trend_table()
            self.draw_trend_charts()
            
            QMessageBox.information(self.ui, "Folder Loaded", f"Successfully parsed {parsed_count} monthly summary files.")
        except Exception as exc:
            QMessageBox.critical(self.ui, "Parse Error", f"Failed to load trend data:\n{exc}")
        finally:
            QApplication.restoreOverrideCursor()

    def populate_trend_table(self):
        self.table_trend.setUpdatesEnabled(False)
        self.table_trend.clear()

        headers = ["Team", "Metric"] + self.months
        self.table_trend.setColumnCount(len(headers))
        self.table_trend.setHorizontalHeaderLabels(headers)

        rows_data = []
        for team in self.teams:
            pub_row = [team, "Published Courses"]
            for m in range(12):
                val = self.trend_data[team]["published"][m]
                pub_row.append(str(int(val)) if pd.notna(val) else "-")
            rows_data.append(pub_row)
            
            for reg in self.regions:
                reg_row = [team, reg]
                for m in range(12):
                    val = self.trend_data[team]["regions"][reg][m]
                    reg_row.append(f"{val:.2f}%" if pd.notna(val) else "-")
                rows_data.append(reg_row)

        self.table_trend.setRowCount(len(rows_data))
        for r_idx, row in enumerate(rows_data):
            for c_idx, val in enumerate(row):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                if c_idx < 2:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if val == "-":
                    item.setForeground(Qt.gray)
                self.table_trend.setItem(r_idx, c_idx, item)

        self.table_trend.horizontalHeader().setMinimumSectionSize(60)
        self.table_trend.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_trend.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for i in range(2, self.table_trend.columnCount()):
            self.table_trend.horizontalHeader().setSectionResizeMode(i, QHeaderView.Stretch)

        self.table_trend.setUpdatesEnabled(True)

    def draw_trend_charts(self):
        plt.close(self.fig_trend)
        self.fig_trend = plt.figure(figsize=(11, 15))
        self.fig_trend.patch.set_facecolor("#121214")
        self.canvas_trend.figure = self.fig_trend

        for i, team in enumerate(self.teams):
            data = self.trend_data.get(team)
            if not data:
                continue

            ax1 = self.fig_trend.add_subplot(3, 1, i + 1)
            ax1.set_facecolor("#121214")
            ax2 = ax1.twinx()
            x = np.arange(12)

            bars = ax2.bar(
                x, data["published"], width=0.25,
                color="#B4C6E7", edgecolor="black", zorder=1,
            )
            bar_label_col = "#00B050" if team == "FAE+Sales" else "red"
            for bar in bars:
                yval = bar.get_height()
                if pd.notna(yval) and yval > 0:
                    ax2.text(bar.get_x() + bar.get_width() / 2, yval + 2, str(int(yval)),
                             ha="center", va="bottom", color=bar_label_col, fontweight="bold", fontsize=9)

            reg_colors = ["#ED7D31", "#FF0000", "#FFC000", "#5B9BD5", "#70AD47", "#264478", "#9E480E", "#7F7F7F"]
            for j, reg in enumerate(self.regions):
                ax1.plot(
                    x, data["regions"][reg],
                    label=reg, color=reg_colors[j],
                    marker="o", markersize=6, linewidth=2, zorder=3,
                )

            ax1.set_title(f"{team} Team Yearly Trend", color="white", fontweight="bold", pad=10)
            ax1.set_xticks(x)
            ax1.set_xticklabels(self.months, color="white")
            ax1.set_ylim(0, 120)
            ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}%"))
            ax1.set_ylabel("Course Completion Percentage", color="white")
            ax1.tick_params(colors="white")
            ax2.set_ylim(0, 100)
            ax2.set_ylabel("Course Published", color=bar_label_col, fontweight="bold")
            ax2.tick_params(axis="y", colors=bar_label_col)
            ax1.grid(True, axis="both", linestyle="-", color="#3a3a40", alpha=0.7)

            if i == 0:
                bar_patch = mpatches.Patch(color="#B4C6E7", ec="black", label="Published\nCourse")
                handles, labels = ax1.get_legend_handles_labels()
                ax1.legend(
                    handles=[bar_patch] + handles, labels=[bar_patch.get_label()] + labels,
                    loc="center left", bbox_to_anchor=(1.08, 0.5), frameon=False, labelcolor="white",
                )

        self.fig_trend.tight_layout(rect=[0, 0, 0.88, 1])
        self.canvas_trend.draw()


# ── Main Analytics Tab Component ──────────────────────────────────────────────

class AnalyticsTabComponent:
    def __init__(self, ui_window):
        self.ui = ui_window
        self.data_dirty = True
        plt.style.use("dark_background")

        self.controllers = {
            0: TeamAnalyticsController("FAE", self.ui.table_fae, self.ui.scroll_fae, self.ui.btn_export_fae, self.ui),
            1: TeamAnalyticsController("Sales", self.ui.table_sales, self.ui.scroll_sales, self.ui.btn_export_sales, self.ui),
            2: SummaryAnalyticsController(self.ui)
        }

        self.ui.tabWidget_analytics.tabBar().setExpanding(True)
        self.ui.tabWidget_analytics.currentChanged.connect(self.load_current_tab)
        
        self.ui.tab_fae.tabBar().setExpanding(True)
        self.ui.tab_sales.tabBar().setExpanding(True)
        self.ui.tab_summary.tabBar().setExpanding(True)
    
        self.ui.tabWidget_analytics.setStyleSheet("""
            QTabWidget#tabWidget_analytics  QTabBar::tab {
                background: #202025;
                color: #777777;
                border: 1px solid #3a3a40;
                padding: 8px 15px;
                font-size: 14px;
                min-height: 20px;
            }
            QTabWidget#tabWidget_analytics  QTabBar::tab:selected {
                background: #0085ca;
                color: white;
                border: 1px solid #0085ca;
                font-weight: bold;
            }
            QTabWidget#tabWidget_analytics  QTabBar::tab:hover:!selected {
                background: #2a2a30;
            }
        """)

    def update_data(self):
        if self.data_dirty:
            for c in self.controllers.values():
                c.is_loaded = False
            self.data_dirty = False
            
        self.load_current_tab()

    def load_current_tab(self):
        idx = self.ui.tabWidget_analytics.currentIndex()
        if idx in self.controllers:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.controllers[idx].auto_load_data()
            finally:
                QApplication.restoreOverrideCursor()

    def mark_dirty(self):
        self.data_dirty = True
