import csv
import io
import os
import glob

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

from data_manager import safe_float
from database import DB_PATH


class TeamAnalyticsController:
    """Controller linking UI elements from design.ui to analytics logic."""

    def __init__(self, team_name, table, scroll_area, btn_export, main_ui):
        self.team_name  = team_name
        self.table      = table
        self.scroll_area = scroll_area
        self.btn_export = btn_export
        self.main_ui    = main_ui

        self.analytics_data = {}
        self.summary_data   = {}
        self.regions        = []
        self.categories     = []

        self.setup_ui_elements()
        self.btn_export.clicked.connect(self.export_analytics)

        if self.team_name == "Summary":
            self.main_ui.btn_save_snapshot.clicked.connect(self.save_month_snapshot)

    # ── Setup ──────────────────────────────────────────────────────────────────

    def setup_ui_elements(self):
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)

        fig_height = 15 if self.team_name == "Summary" else 12
        # FIX: close any previous figure before creating a new one to prevent
        # Matplotlib's internal figure list from growing unboundedly.
        if hasattr(self, "figure"):
            plt.close(self.figure)

        self.figure = plt.figure(figsize=(11, fig_height))
        self.figure.patch.set_facecolor("#121214")
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setStyleSheet("background-color: transparent;")
        self.scroll_area.setWidget(self.canvas)

    # ── Auto-load ──────────────────────────────────────────────────────────────

    def auto_load_data(self):
        if self.team_name == "Summary":
            try:
                self.load_snapshots_from_db()
            except Exception as exc:
                print(f"[ERROR] loading Summary: {exc}")
                self.btn_export.setEnabled(False)
            return

        settings = QSettings("PowerIntegrations", "PowerTrack")
        folder   = settings.value(
            "current_data_folder",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"),
        )

        pattern1 = os.path.join(folder, f"*Skilljar Metric*{self.team_name}*.csv")
        pattern2 = os.path.join(
            folder, "**", f"*Skilljar Metric*{self.team_name}*.csv"
        )
        files = list(set(glob.glob(pattern1) + glob.glob(pattern2, recursive=True)))

        if files:
            # FIX: when multiple matching files exist, use the most recently
            # modified one instead of an arbitrary list[0].
            if len(files) > 1:
                files.sort(key=os.path.getmtime, reverse=True)
                print(
                    f"[WARN] Multiple metric files found for '{self.team_name}'; "
                    f"using most recent: {files[0]}"
                )
            try:
                self.parse_csv_for_analytics(files[0])
                self.populate_table()
                self.draw_charts()
                self.btn_export.setEnabled(True)
            except Exception as exc:
                print(f"[ERROR] loading {self.team_name}: {exc}")
                self.btn_export.setEnabled(False)
        else:
            self.btn_export.setEnabled(False)
            self.table.clearContents()
            # FIX: properly close and recreate instead of just .clear()
            plt.close(self.figure)
            self.figure = plt.figure(figsize=(11, 12 if self.team_name != "Summary" else 15))
            self.figure.patch.set_facecolor("#121214")
            self.canvas.figure = self.figure
            self.canvas.draw()

    # ── Snapshot loading ───────────────────────────────────────────────────────

    def load_snapshots_from_db(self):
        self.summary_data.clear()
        self.regions = [
            "Taiwan", "China", "Korea", "Japan",
            "US/Canada", "Europe", "ISEA", "Worldwide",
        ]

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='monthly_snapshots'"
            )
            if not cursor.fetchone():
                self._clear_summary_ui()
                return

            # FIX: include year in the SELECT and sort by (year, month) so
            # data spanning multiple years is ordered correctly.
            cursor.execute(
                """
                SELECT month, year, team, published,
                       taiwan, china, korea, japan,
                       americas, europe, isea, worldwide
                FROM   monthly_snapshots
                """
            )
            rows = cursor.fetchall()

        if not rows:
            self._clear_summary_ui()
            return

        month_order = {
            "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
            "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
            "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
        }
        # Sort by year first, then month index
        rows.sort(key=lambda x: (x[1], month_order.get(x[0], 99)))

        for row in rows:
            month, year, team, pub = row[0], row[1], row[2], row[3]
            reg_vals = row[4:]  # taiwan, china, korea, japan, americas, europe, isea, worldwide

            if team not in self.summary_data:
                self.summary_data[team] = {
                    "months":    [],
                    "published": [],
                    "regions":   {r: [] for r in self.regions},
                }

            # Display month+year label so the chart x-axis is unambiguous
            label = f"{month} {year}" if year else month
            self.summary_data[team]["months"].append(label)
            self.summary_data[team]["published"].append(int(pub) if pub else 0)

            for r_idx, r_name in enumerate(self.regions):
                val = reg_vals[r_idx]
                self.summary_data[team]["regions"][r_name].append(
                    val if val is not None else np.nan
                )

        self.populate_table()
        self.draw_charts()
        self.btn_export.setEnabled(True)

    def _clear_summary_ui(self):
        self.btn_export.setEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(0)
        plt.close(self.figure)
        self.figure = plt.figure(figsize=(11, 15))
        self.figure.patch.set_facecolor("#121214")
        self.canvas.figure = self.figure
        self.canvas.draw()

    # ── Snapshot saving ────────────────────────────────────────────────────────

    def save_month_snapshot(self):
        month = self.main_ui.combo_snapshot_month.currentText()
        if not month:
            return

        # Derive the year from the current system date so we never overwrite
        # a prior-year snapshot with the same month name.
        import datetime
        current_year = datetime.date.today().year

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT course_id, control_code, course_type FROM courses"
                )
                courses    = cursor.fetchall()
                course_map = {str(r[0]): r[1] for r in courses}

                fae_courses   = [str(r[0]) for r in courses if r[2] in ("FAE",   "Both")]
                sales_courses = [str(r[0]) for r in courses if r[2] in ("Sales", "Both")]
                both_courses  = list(set(fae_courses + sales_courses))

                cursor.execute(
                    "SELECT user_id, organization, region_bucket, control_codes "
                    "FROM students WHERE organization IN ('FAE', 'Sales')"
                )
                students = cursor.fetchall()

                # FIX: filter completions to just FAE/Sales students
                cursor.execute(
                    """
                    SELECT c.user_id, c.course_id, c.percent_complete
                    FROM   completions c
                    JOIN   students s ON s.user_id = c.user_id
                    WHERE  s.organization IN ('FAE', 'Sales')
                    """
                )
                completions_map = {}
                for uid, cid, pct in cursor.fetchall():
                    from data_manager import normalize_uid
                    uid_str = normalize_uid(uid)
                    completions_map.setdefault(uid_str, {})[str(cid)] = safe_float(pct)

                teams_to_process = {
                    "FAE":      {"orgs": ["FAE"],          "pub": len(fae_courses)},
                    "Sales":    {"orgs": ["Sales"],        "pub": len(sales_courses)},
                    "FAE+Sales": {"orgs": ["FAE", "Sales"], "pub": len(both_courses)},
                }

                regions_db_map = {
                    "Taiwan":   "Taiwan",
                    "China":    "China",
                    "Korea":    "Korea",
                    "Japan":    "Japan",
                    "US/Canada": "Americas",
                    "Europe":   "Europe",
                    "ISEA":     "ISEA",
                    "Worldwide": "Worldwide",
                }

                for team, t_info in teams_to_process.items():
                    team_orgs  = t_info["orgs"]
                    pub_count  = t_info["pub"]
                    region_totals = {r: [] for r in regions_db_map}

                    for st in students:
                        user_id, org, region, control_codes = st
                        if org not in team_orgs:
                            continue

                        from data_manager import normalize_uid
                        uid_str  = normalize_uid(user_id)
                        my_codes = (
                            [c.strip() for c in str(control_codes).split(",")]
                            if control_codes and str(control_codes).lower()
                               not in ("nan", "n/a", "none", "")
                            else []
                        )
                        my_req_courses = [
                            cid for cid, cc in course_map.items() if cc in my_codes
                        ]

                        if my_req_courses:
                            total_pct = sum(
                                completions_map.get(uid_str, {}).get(cid, 0.0)
                                for cid in my_req_courses
                            )
                            avg_pct = total_pct / len(my_req_courses)
                            region_totals["Worldwide"].append(avg_pct)
                            for out_reg, db_reg in regions_db_map.items():
                                if out_reg != "Worldwide" and region == db_reg:
                                    region_totals[out_reg].append(avg_pct)

                    reg_avgs = {
                        r_name: (
                            sum(region_totals[r_name]) / len(region_totals[r_name])
                            if region_totals[r_name] else None
                        )
                        for r_name in regions_db_map
                    }

                    # FIX: REPLACE now includes the year column
                    cursor.execute(
                        """
                        REPLACE INTO monthly_snapshots
                            (month, year, team, published,
                             taiwan, china, korea, japan,
                             americas, europe, isea, worldwide)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            month, current_year, team, pub_count,
                            reg_avgs["Taiwan"],   reg_avgs["China"],
                            reg_avgs["Korea"],    reg_avgs["Japan"],
                            reg_avgs["US/Canada"], reg_avgs["Europe"],
                            reg_avgs["ISEA"],     reg_avgs["Worldwide"],
                        ),
                    )

            self.load_snapshots_from_db()
            QMessageBox.information(
                self.main_ui, "Snapshot Saved",
                f"Successfully saved analytics snapshot for {month} {current_year}.",
            )

        except Exception as exc:
            QMessageBox.critical(
                self.main_ui, "Error",
                f"Failed to save snapshot: {exc}",
            )
        finally:
            QApplication.restoreOverrideCursor()

    # ── CSV parsing ────────────────────────────────────────────────────────────

    def parse_csv_for_analytics(self, filepath):
        self.analytics_data.clear()
        self.summary_data.clear()
        self.regions.clear()
        self.categories.clear()

        # FIX: csv is now imported at the top of the module, not inside this
        # function (avoids a dict lookup on every call).
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            rows = list(csv.reader(f))

        start_r, start_c = next(
            (
                (r, c)
                for r, row in enumerate(rows)
                for c, cell in enumerate(row)
                if cell.strip() == "Category Team"
            ),
            (-1, -1),
        )
        if start_r == -1:
            raise ValueError("Could not find 'Category Team' summary block.")

        self.regions = [
            x.strip() for x in rows[start_r][start_c + 1: start_c + 9]
        ]
        valid_cats = [
            "InnoSwitch", "LinkSwitch", "LYTSwitch", "Other",
            "Motor Driver", "Automotive", "High Power", "All Course",
        ]

        for r_idx in range(start_r + 1, min(start_r + 20, len(rows))):
            cat = rows[r_idx][start_c].strip()
            if cat in valid_cats:
                self.categories.append(cat)
                self.analytics_data[cat] = [
                    safe_float(v)
                    for v in rows[r_idx][start_c + 1: start_c + 9]
                ]

    # ── Table population ───────────────────────────────────────────────────────

    def populate_table(self):
        self.table.setUpdatesEnabled(False)
        self.table.clear()

        if self.team_name == "Summary":
            headers = ["Team", "Month", "Published"] + self.regions
            self.table.setColumnCount(len(headers))
            self.table.setHorizontalHeaderLabels(headers)

            rows_data = []
            for team in ("FAE", "Sales", "FAE+Sales"):
                data = self.summary_data.get(team)
                if not data:
                    continue
                for m_idx, month in enumerate(data["months"]):
                    pub = data["published"][m_idx]
                    if pub > 0 or any(
                        not np.isnan(data["regions"][r][m_idx])
                        for r in self.regions
                    ):
                        row = (
                            [team, month, str(pub)]
                            + [
                                f"{data['regions'][r][m_idx]:.2f}%"
                                if not np.isnan(data["regions"][r][m_idx])
                                else "-"
                                for r in self.regions
                            ]
                        )
                        rows_data.append(row)

            self.table.setRowCount(len(rows_data))
            for r_idx, row in enumerate(rows_data):
                for c_idx, val in enumerate(row):
                    item = QTableWidgetItem(val)
                    item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                    if c_idx < 2:
                        item.setFont(self.get_bold_font(item))
                    if val == "-":
                        item.setForeground(Qt.gray)
                    self.table.setItem(r_idx, c_idx, item)
        else:
            headers = ["Category"] + self.regions
            self.table.setColumnCount(len(headers))
            self.table.setHorizontalHeaderLabels(headers)
            self.table.setRowCount(len(self.categories))

            for r_idx, cat in enumerate(self.categories):
                item_cat = QTableWidgetItem(cat)
                item_cat.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                item_cat.setFont(self.get_bold_font(item_cat))
                self.table.setItem(r_idx, 0, item_cat)

                for c_idx, val in enumerate(self.analytics_data[cat]):
                    item_val = QTableWidgetItem(
                        f"{val:.2f}%" if val > 0 else "0.00%"
                    )
                    item_val.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                    if val == 0:
                        item_val.setForeground(Qt.gray)
                    self.table.setItem(r_idx, c_idx + 1, item_val)

        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setUpdatesEnabled(True)

    def get_bold_font(self, item):
        font = item.font()
        font.setBold(True)
        return font

    # ── Chart drawing ──────────────────────────────────────────────────────────

    def draw_charts(self):
        # FIX: close the old figure before recreating to prevent memory leak.
        plt.close(self.figure)
        fig_height   = 15 if self.team_name == "Summary" else 12
        self.figure  = plt.figure(figsize=(11, fig_height))
        self.figure.patch.set_facecolor("#121214")
        self.canvas.figure = self.figure

        if self.team_name == "Summary":
            if not self.summary_data:
                self.canvas.draw()
                return

            for i, team in enumerate(("FAE", "Sales", "FAE+Sales")):
                data = self.summary_data.get(team)
                if not data or not data["months"]:
                    continue

                ax1 = self.figure.add_subplot(3, 1, i + 1)
                ax1.set_facecolor("#121214")
                ax2 = ax1.twinx()
                x   = np.arange(len(data["months"]))

                bars = ax2.bar(
                    x, data["published"], width=0.25,
                    color="#B4C6E7", edgecolor="black", zorder=1,
                )
                bar_label_col = "#00B050" if team == "FAE+Sales" else "red"
                for bar in bars:
                    yval = bar.get_height()
                    if yval > 0:
                        ax2.text(
                            bar.get_x() + bar.get_width() / 2,
                            yval + 2,
                            str(int(yval)),
                            ha="center", va="bottom",
                            color=bar_label_col, fontweight="bold", fontsize=9,
                        )

                reg_colors = [
                    "#ED7D31", "#FF0000", "#FFC000", "#5B9BD5",
                    "#70AD47", "#264478", "#9E480E", "#7F7F7F",
                ]
                for j, reg in enumerate(self.regions):
                    ax1.plot(
                        x, data["regions"][reg],
                        label=reg, color=reg_colors[j],
                        marker="o", markersize=6, linewidth=2, zorder=3,
                    )

                ax1.set_title(
                    ["FAE Team", "Sales Team", "Sales and FAE"][i],
                    color="white", fontweight="bold", pad=10,
                )
                ax1.set_xticks(x)
                ax1.set_xticklabels(data["months"], color="white")
                ax1.set_ylim(0, 120)
                ax1.yaxis.set_major_formatter(
                    plt.FuncFormatter(lambda y, _: f"{y:.0f}%")
                )
                ax1.set_ylabel("Course Completion Percentage", color="white")
                ax1.tick_params(colors="white")
                ax2.set_ylim(0, 100)
                ax2.set_ylabel(
                    "Course Published", color=bar_label_col, fontweight="bold"
                )
                ax2.tick_params(axis="y", colors=bar_label_col)
                ax1.grid(
                    True, axis="both", linestyle="-", color="#3a3a40", alpha=0.7
                )

                if i == 0:
                    bar_patch = mpatches.Patch(
                        color="#B4C6E7", ec="black", label="Published\nCourse"
                    )
                    handles, labels = ax1.get_legend_handles_labels()
                    ax1.legend(
                        handles=[bar_patch] + handles,
                        labels=[bar_patch.get_label()] + labels,
                        loc="center left", bbox_to_anchor=(1.08, 0.5),
                        frameon=False, labelcolor="white",
                    )
        else:
            if not self.categories:
                self.canvas.draw()
                return

            chart_cats   = [c for c in self.categories if c != "All Course"]
            regions_no_ww = self.regions[:-1]
            ww_idx       = self.regions.index("Worldwide")

            ax1 = self.figure.add_subplot(211)
            ax1.set_facecolor("#121214")
            ax1.grid(
                axis="y", linestyle="-", color="#3a3a40", alpha=0.7, zorder=0
            )
            x, width = np.arange(len(chart_cats)), 0.11
            reg_colors = [
                "#4472C4", "#ED7D31", "#A5A5A5", "#FFC000",
                "#5B9BD5", "#00B050", "#264478",
            ]

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

            ax1.set_title(
                f"{self.team_name} Team",
                fontsize=12, fontweight="bold", color="white", pad=15,
            )
            ax1.set_ylabel("Course Completion", color="white", fontweight="bold")
            ax1.set_xticks(line_x)
            ax1.set_xticklabels(chart_cats)
            ax1.set_ylim(0, 105)
            ax1.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda y, _: f"{y:.0f}%")
            )
            ax1.tick_params(colors="white")
            ax1.legend(
                loc="center left", bbox_to_anchor=(1.02, 0.5),
                frameon=False, labelcolor="white",
            )

            ax2 = self.figure.add_subplot(212)
            ax2.set_facecolor("#121214")
            ax2.grid(
                axis="y", linestyle="-", color="#3a3a40", alpha=0.7, zorder=0
            )
            x2, width2 = np.arange(len(self.regions)), 0.12
            cat_colors = [
                "#00B050", "#7030A0", "#C65911", "#7F7F7F",
                "#4472C4", "#548235", "#000000",
            ]

            for i, cat in enumerate(chart_cats):
                ax2.bar(
                    x2 + (width2 * i),
                    self.analytics_data[cat],
                    width2, label=cat,
                    color=cat_colors[i % len(cat_colors)],
                    edgecolor="black", zorder=3,
                )

            center_x2 = x2 + width2 * ((len(chart_cats) - 1) / 2)
            ax2.set_title(
                f"{self.team_name} Team",
                fontsize=12, fontweight="bold", color="white", pad=15,
            )
            ax2.set_ylabel("Course Completion", color="white", fontweight="bold")
            ax2.set_xticks(center_x2)
            ax2.set_xticklabels(self.regions)
            ax2.set_ylim(0, 105)
            ax2.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda y, _: f"{y:.0f}%")
            )
            ax2.tick_params(colors="white")
            ax2.legend(
                loc="center left", bbox_to_anchor=(1.02, 0.5),
                frameon=False, labelcolor="white",
            )

        self.figure.tight_layout(rect=[0, 0, 0.88, 1])
        self.canvas.draw()

    # ── Export ─────────────────────────────────────────────────────────────────

    def export_analytics(self):
        if self.team_name == "Summary":
            path, _ = QFileDialog.getSaveFileName(
                self.main_ui, "Export Analytics History",
                "Analytics_History_Export.xlsx", "Excel Files (*.xlsx)",
            )
            if not path:
                return
            try:
                data = []
                for team in ("FAE", "Sales", "FAE+Sales"):
                    info = self.summary_data.get(team)
                    if not info:
                        continue
                    for i, month in enumerate(info["months"]):
                        row = {
                            "Team":              team,
                            "Month":             month,
                            "Published Courses": info["published"][i],
                        }
                        for r_name in self.regions:
                            val = info["regions"][r_name][i]
                            row[r_name] = (
                                f"{val:.2f}%" if not np.isnan(val) else "N/A"
                            )
                        data.append(row)
                df = pd.DataFrame(data)
                df.to_excel(path, index=False)
                QMessageBox.information(
                    self.main_ui, "Export Successful",
                    f"History exported successfully to:\n{path}",
                )
            except Exception as exc:
                QMessageBox.critical(
                    self.main_ui, "Export Error",
                    f"Failed to export history:\n{exc}",
                )
            return

        # ── Per-team export ────────────────────────────────────────────────────
        path, _ = QFileDialog.getSaveFileName(
            self.main_ui, "Export Analytics",
            f"{self.team_name}_Analytics_Export.xlsx", "Excel Files (*.xlsx)",
        )
        if not path:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            chart_cats  = [c for c in self.categories if c != "All Course"]
            export_cats = chart_cats + (
                ["All Course"] if "All Course" in self.categories else []
            )

            data = []
            for cat in export_cats:
                row = {"Category": cat}
                for i, r_name in enumerate(self.regions):
                    row[r_name] = self.analytics_data[cat][i] / 100.0
                data.append(row)
            df = pd.DataFrame(data)

            # FIX: ExcelWriter is created inside try so it can always be
            # closed in the finally block, even if chart rendering fails.
            writer = None
            try:
                writer    = pd.ExcelWriter(path, engine="xlsxwriter")
                df.to_excel(writer, sheet_name="Analytics", index=False)
                worksheet = writer.sheets["Analytics"]

                pct_format = writer.book.add_format({"num_format": "0.00%"})
                worksheet.set_column(1, len(self.regions), 12, pct_format)
                worksheet.set_column(0, 0, 20)

                text_col = "#595959"
                grid_col = "#d9d9d9"
                regions_no_ww = self.regions[:-1]
                ww_idx        = self.regions.index("Worldwide")

                # Chart 1 — regions as bars, Worldwide as line
                fig1, ax1 = plt.subplots(figsize=(14, 6))
                fig1.patch.set_facecolor("white")
                fig1.patch.set_edgecolor(grid_col)
                fig1.patch.set_linewidth(1)
                ax1.set_facecolor("white")
                ax1.grid(axis="y", linestyle="-", color=grid_col, linewidth=0.7, zorder=0)
                for spine in ("top", "right"):
                    ax1.spines[spine].set_visible(False)
                ax1.spines["left"].set_color(grid_col)
                ax1.spines["bottom"].set_color(grid_col)

                x, step, bar_width = np.arange(len(chart_cats)), 0.115, 0.09
                reg_colors = [
                    "#4472C4", "#ED7D31", "#A5A5A5", "#FFC000",
                    "#5B9BD5", "#00B050", "#264478",
                ]

                for i, reg in enumerate(regions_no_ww):
                    ax1.bar(
                        x + (step * i),
                        [self.analytics_data[cat][i] for cat in chart_cats],
                        width=bar_width, label=reg,
                        color=reg_colors[i % len(reg_colors)],
                        edgecolor="black", linewidth=0.5, zorder=3,
                    )

                ww_vals = [self.analytics_data[cat][ww_idx] for cat in chart_cats]
                line_x  = x + (step * (len(regions_no_ww) - 1) / 2)
                ax1.plot(
                    line_x, ww_vals, color="#C65911",
                    marker="o", markersize=5, linewidth=2,
                    label="Worldwide", zorder=4,
                )
                for i, val in enumerate(ww_vals):
                    ax1.text(
                        line_x[i], val + 3, f"{val:.1f}%",
                        ha="center", va="bottom", color=text_col, fontsize=9,
                    )

                ax1.set_title(f"{self.team_name} Team", fontsize=12, color=text_col, pad=15)
                ax1.set_ylabel("Course Completion", color=text_col, fontweight="bold", fontsize=10)
                ax1.set_xlabel("Course Category",   color=text_col, fontweight="bold", fontsize=10, labelpad=10)
                ax1.set_xticks(line_x)
                ax1.set_xticklabels(chart_cats, color=text_col, fontsize=9)
                ax1.set_ylim(0, 105)
                ax1.yaxis.set_major_formatter(
                    plt.FuncFormatter(lambda y, _: f"{y:.0f}%")
                )
                ax1.tick_params(colors=text_col, labelsize=9)
                ax1.legend(
                    loc="center left", bbox_to_anchor=(1.02, 0.5),
                    frameon=False, labelcolor=text_col,
                    handlelength=1, handleheight=1, fontsize=9,
                )
                fig1.tight_layout(rect=[0, 0, 0.9, 1])

                img_data1 = io.BytesIO()
                fig1.savefig(
                    img_data1, format="png", dpi=100,
                    bbox_inches="tight", facecolor="white",
                    edgecolor=fig1.get_edgecolor(),
                )
                img_data1.seek(0)
                plt.close(fig1)

                # Chart 2 — categories as bars
                fig2, ax2 = plt.subplots(figsize=(14, 6))
                fig2.patch.set_facecolor("white")
                fig2.patch.set_edgecolor(grid_col)
                fig2.patch.set_linewidth(1)
                ax2.set_facecolor("white")
                ax2.grid(axis="y", linestyle="-", color=grid_col, linewidth=0.7, zorder=0)
                for spine in ("top", "right"):
                    ax2.spines[spine].set_visible(False)
                ax2.spines["left"].set_color(grid_col)
                ax2.spines["bottom"].set_color(grid_col)

                x2, step2, bar_width2 = np.arange(len(self.regions)), 0.115, 0.09
                cat_colors = [
                    "#00B050", "#7030A0", "#C65911", "#7F7F7F",
                    "#4472C4", "#548235", "#000000",
                ]

                for i, cat in enumerate(chart_cats):
                    ax2.bar(
                        x2 + (step2 * i),
                        self.analytics_data[cat],
                        width=bar_width2, label=cat,
                        color=cat_colors[i % len(cat_colors)],
                        edgecolor="black", linewidth=0.5, zorder=3,
                    )

                center_x2 = x2 + (step2 * (len(chart_cats) - 1) / 2)
                ax2.set_title(f"{self.team_name} Team", fontsize=12, color=text_col, pad=15)
                ax2.set_ylabel("Course Completion", color=text_col, fontweight="bold", fontsize=10)
                ax2.set_xlabel("Region",            color=text_col, fontweight="bold", fontsize=10, labelpad=10)
                ax2.set_xticks(center_x2)
                ax2.set_xticklabels(self.regions, color=text_col, fontsize=9)
                ax2.set_ylim(0, 105)
                ax2.yaxis.set_major_formatter(
                    plt.FuncFormatter(lambda y, _: f"{y:.0f}%")
                )
                ax2.tick_params(colors=text_col, labelsize=9)
                ax2.legend(
                    loc="center left", bbox_to_anchor=(1.02, 0.5),
                    frameon=False, labelcolor=text_col,
                    handlelength=1, handleheight=1, fontsize=9,
                )
                fig2.tight_layout(rect=[0, 0, 0.9, 1])

                img_data2 = io.BytesIO()
                fig2.savefig(
                    img_data2, format="png", dpi=100,
                    bbox_inches="tight", facecolor="white",
                    edgecolor=fig2.get_edgecolor(),
                )
                img_data2.seek(0)
                plt.close(fig2)

                worksheet.insert_image(
                    f"A{len(export_cats) + 4}", "chart1.png",
                    {"image_data": img_data1},
                )
                worksheet.insert_image(
                    f"A{len(export_cats) + 36}", "chart2.png",
                    {"image_data": img_data2},
                )

                # FIX: BytesIO buffers explicitly closed
                img_data1.close()
                img_data2.close()

            except ImportError:
                df.to_excel(path, index=False)
            finally:
                # FIX: writer is always closed, even if chart rendering raised
                if writer is not None:
                    try:
                        writer.close()
                    except Exception:
                        pass

            QMessageBox.information(
                self.main_ui, "Export Successful",
                f"{self.team_name} Analytics exported successfully to:\n{path}",
            )

        except Exception as exc:
            QMessageBox.critical(
                self.main_ui, "Export Error",
                f"Failed to export analytics:\n{exc}",
            )
        finally:
            QApplication.restoreOverrideCursor()


# ── Analytics tab (top-level component) ───────────────────────────────────────

class AnalyticsTabComponent:
    def __init__(self, ui_window):
        self.ui       = ui_window
        # FIX: dirty flag prevents redundant full reloads on every tab switch.
        self.data_dirty = True
        plt.style.use("dark_background")

        self.controllers = {
            "FAE":     TeamAnalyticsController(
                "FAE",     self.ui.table_fae,     self.ui.scroll_fae,
                self.ui.btn_export_fae,     self.ui,
            ),
            "Sales":   TeamAnalyticsController(
                "Sales",   self.ui.table_sales,   self.ui.scroll_sales,
                self.ui.btn_export_sales,   self.ui,
            ),
            "Summary": TeamAnalyticsController(
                "Summary", self.ui.table_summary, self.ui.scroll_summary,
                self.ui.btn_export_summary, self.ui,
            ),
        }

    def update_data(self):
        for controller in self.controllers.values():
            controller.auto_load_data()
        self.data_dirty = False

    def mark_dirty(self):
        """Call this whenever underlying data changes so next tab visit reloads."""
        self.data_dirty = True