#!/usr/bin/env python3
"""
gui.py  --  Drop Time Sniper GUI
=================================
Premium dark-theme PyQt5 GUI for drop_time_scraper.py.

Requires:
  pip install PyQt5 nodriver
  + Chrome / Edge / Brave / Chromium installed

Run:
  python gui.py
"""

from __future__ import annotations
import asyncio
import sys
import threading
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QObject, QTimer, QAbstractTableModel,
    QModelIndex, QSortFilterProxyModel, QSize,
)
from PyQt5.QtGui import (
    QColor, QFont, QPalette, QIcon, QPixmap, QPainter, QBrush,
    QLinearGradient, QFontDatabase,
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableView, QHeaderView,
    QFrame, QSplitter, QTextEdit, QComboBox, QFileDialog,
    QAbstractItemView, QSizePolicy, QProgressBar, QSystemTrayIcon,
    QMenu, QAction, QMessageBox, QToolButton, QGraphicsDropShadowEffect,
    QStyledItemDelegate, QStyleOptionViewItem,
)

try:
    from drop_time_scraper import get_drop_time, DropResult, _find_chrome_binary
except ImportError:
    print("ERROR: drop_time_scraper.py must be in the same directory as gui.py")
    sys.exit(1)

PST = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc

# ─────────────────────────────────────────────────────────────────────────────
# PALETTE  (rich dark theme)
# ─────────────────────────────────────────────────────────────────────────────
COL = {
    "bg":           "#0f0f13",
    "surface":      "#16161e",
    "surface2":     "#1c1c26",
    "surface3":     "#22222e",
    "border":       "#2a2a38",
    "border_hi":    "#3a3a52",
    "accent":       "#6c63ff",   # purple
    "accent_dim":   "#4a44cc",
    "accent_glow":  "#9d97ff",
    "success":      "#3ddc97",
    "warning":      "#f5a623",
    "error":        "#ff5c5c",
    "text":         "#e8e8f0",
    "text_muted":   "#8888a8",
    "text_faint":   "#44445a",
    "row_alt":      "#18181f",
    "row_hover":    "#1e1e2c",
    "row_select":   "#2a274d",
}

STYLESHEET = f"""
/* ── App base ── */
QWidget {{
    background: {COL['bg']};
    color: {COL['text']};
    font-family: 'Segoe UI', 'Inter', 'SF Pro Display', sans-serif;
    font-size: 13px;
    selection-background-color: {COL['accent_dim']};
    selection-color: #ffffff;
}}
QMainWindow, QDialog {{
    background: {COL['bg']};
}}

/* ── Cards / frames ── */
.Card {{
    background: {COL['surface']};
    border: 1px solid {COL['border']};
    border-radius: 10px;
}}

/* ── Input ── */
QLineEdit {{
    background: {COL['surface2']};
    border: 1px solid {COL['border']};
    border-radius: 6px;
    padding: 8px 12px;
    color: {COL['text']};
    font-size: 13px;
}}
QLineEdit:focus {{
    border: 1px solid {COL['accent']};
}}
QLineEdit::placeholder {{
    color: {COL['text_faint']};
}}

/* ── Buttons ── */
QPushButton {{
    background: {COL['surface2']};
    border: 1px solid {COL['border']};
    border-radius: 6px;
    padding: 8px 18px;
    color: {COL['text']};
    font-size: 13px;
    font-weight: 500;
}}
QPushButton:hover {{
    background: {COL['surface3']};
    border-color: {COL['border_hi']};
}}
QPushButton:pressed {{
    background: {COL['border']};
}}
QPushButton#btnLookup {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {COL['accent_dim']}, stop:1 {COL['accent']});
    border: none;
    color: #ffffff;
    font-weight: 600;
    font-size: 13px;
    padding: 9px 28px;
    border-radius: 7px;
    min-width: 120px;
}}
QPushButton#btnLookup:hover {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {COL['accent']}, stop:1 {COL['accent_glow']});
}}
QPushButton#btnLookup:disabled {{
    background: {COL['surface3']};
    color: {COL['text_faint']};
}}
QPushButton#btnClear {{
    background: transparent;
    border: 1px solid {COL['border']};
    color: {COL['text_muted']};
    padding: 8px 14px;
}}
QPushButton#btnClear:hover {{
    border-color: {COL['error']};
    color: {COL['error']};
}}
QPushButton#btnImport {{
    background: transparent;
    border: 1px solid {COL['border']};
    color: {COL['text_muted']};
    padding: 8px 14px;
}}
QPushButton#btnImport:hover {{
    border-color: {COL['accent']};
    color: {COL['accent_glow']};
}}
QToolButton {{
    background: transparent;
    border: none;
    color: {COL['text_muted']};
    font-size: 18px;
    padding: 4px;
}}
QToolButton:hover {{
    color: {COL['text']};
}}

/* ── ComboBox ── */
QComboBox {{
    background: {COL['surface2']};
    border: 1px solid {COL['border']};
    border-radius: 6px;
    padding: 7px 12px;
    color: {COL['text']};
    min-width: 140px;
}}
QComboBox:focus {{
    border-color: {COL['accent']};
}}
QComboBox QAbstractItemView {{
    background: {COL['surface2']};
    border: 1px solid {COL['border_hi']};
    selection-background-color: {COL['row_select']};
    outline: none;
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    width: 12px;
    height: 12px;
}}

/* ── Table ── */
QTableView {{
    background: {COL['surface']};
    border: 1px solid {COL['border']};
    border-radius: 8px;
    gridline-color: {COL['border']};
    outline: none;
    selection-background-color: {COL['row_select']};
}}
QTableView::item {{
    padding: 6px 12px;
    border: none;
}}
QTableView::item:selected {{
    background: {COL['row_select']};
    color: #ffffff;
}}
QHeaderView::section {{
    background: {COL['surface2']};
    color: {COL['text_muted']};
    border: none;
    border-bottom: 1px solid {COL['border']};
    border-right: 1px solid {COL['border']};
    padding: 8px 12px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QHeaderView::section:last {{
    border-right: none;
}}

/* ── ScrollBars ── */
QScrollBar:vertical {{
    background: {COL['surface']};
    width: 6px;
    margin: 0;
    border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {COL['border_hi']};
    min-height: 32px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COL['accent']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {COL['surface']};
    height: 6px;
    border-radius: 3px;
}}
QScrollBar::handle:horizontal {{
    background: {COL['border_hi']};
    min-width: 32px;
    border-radius: 3px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {COL['accent']};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── Log panel ── */
QTextEdit {{
    background: {COL['surface']};
    border: 1px solid {COL['border']};
    border-radius: 8px;
    color: {COL['text_muted']};
    font-family: 'Consolas', 'Cascadia Code', 'Fira Code', monospace;
    font-size: 12px;
    padding: 8px;
}}

/* ── Progress bar ── */
QProgressBar {{
    background: {COL['surface2']};
    border: none;
    border-radius: 3px;
    height: 3px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {COL['accent_dim']}, stop:1 {COL['accent_glow']});
    border-radius: 3px;
}}

/* ── Tooltip ── */
QToolTip {{
    background: {COL['surface3']};
    color: {COL['text']};
    border: 1px solid {COL['border_hi']};
    border-radius: 4px;
    padding: 5px 8px;
    font-size: 12px;
}}

/* ── Status badge labels ── */
QLabel#badge_exact {{
    background: rgba(61,220,151,0.15);
    color: {COL['success']};
    border: 1px solid rgba(61,220,151,0.3);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#badge_running {{
    background: rgba(108,99,255,0.15);
    color: {COL['accent_glow']};
    border: 1px solid rgba(108,99,255,0.3);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#badge_fail {{
    background: rgba(255,92,92,0.15);
    color: {COL['error']};
    border: 1px solid rgba(255,92,92,0.3);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}
"""


# ─────────────────────────────────────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────────────────────────────────────

# Single "Drop Date" column replaces the old PST + UTC pair
COLS = ["Domain", "Drop Date", "Confidence", "Source", "Status"]
COL_DOMAIN  = 0
COL_DATE    = 1   # bare YYYY-MM-DD
COL_CONF    = 2
COL_SOURCE  = 3
COL_STATUS  = 4


class DomainRow:
    def __init__(self, domain: str):
        self.domain    = domain
        self.date      = ""   # YYYY-MM-DD
        self.conf      = ""
        self.source    = ""
        self.status    = "Queued"   # Queued | Fetching | Done | Error
        self.raw       = ""
        self.error_msg = ""

    def apply_result(self, r: DropResult) -> None:
        if r.drop_dt_utc:
            self.date   = r.drop_date or ""
            self.conf   = r.confidence.upper()
            self.source = r.source or ""
            self.raw    = r.raw_text or ""
            self.status = "Done"
        else:
            self.error_msg = r.error or "Unknown error"
            self.status    = "Error"


class DomainTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[DomainRow] = []

    def rowCount(self, parent=QModelIndex()): return len(self._rows)
    def columnCount(self, parent=QModelIndex()): return len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        row = self._rows[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            return [
                row.domain, row.date, row.conf, row.source, row.status
            ][col]

        if role == Qt.ForegroundRole:
            if col == COL_STATUS:
                return {
                    "Done":     QColor(COL["success"]),
                    "Error":    QColor(COL["error"]),
                    "Fetching": QColor(COL["accent_glow"]),
                    "Queued":   QColor(COL["text_faint"]),
                }.get(row.status, QColor(COL["text"]))
            if col == COL_CONF and row.conf == "EXACT":
                return QColor(COL["success"])
            if col == COL_DATE and row.date:
                return QColor(COL["text"])   # bright white for the date
            if col == COL_DOMAIN:
                return QColor(COL["text"])
            return QColor(COL["text_muted"])

        if role == Qt.BackgroundRole:
            base = QColor(COL["row_alt"]) if index.row() % 2 else QColor(COL["surface"])
            return base

        if role == Qt.TextAlignmentRole:
            if col == COL_DATE:
                return Qt.AlignCenter
            return Qt.AlignVCenter | Qt.AlignLeft

        if role == Qt.ToolTipRole:
            if row.error_msg and col == COL_STATUS:
                return row.error_msg
            if row.raw and col == COL_SOURCE:
                return row.raw
            return None

        return None

    # ── mutations ──

    def add_domain(self, domain: str) -> int:
        """Add a new row, return its index."""
        for i, r in enumerate(self._rows):
            if r.domain == domain:
                return i
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
        self._rows.append(DomainRow(domain))
        self.endInsertRows()
        return len(self._rows) - 1

    def update_row(self, idx: int, result: DropResult) -> None:
        self._rows[idx].apply_result(result)
        self.dataChanged.emit(
            self.index(idx, 0),
            self.index(idx, len(COLS) - 1),
        )

    def set_status(self, idx: int, status: str) -> None:
        self._rows[idx].status = status
        cell = self.index(idx, COL_STATUS)
        self.dataChanged.emit(cell, cell)

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    def remove_row(self, idx: int) -> None:
        self.beginRemoveRows(QModelIndex(), idx, idx)
        self._rows.pop(idx)
        self.endRemoveRows()

    def domains(self) -> list[str]:
        return [r.domain for r in self._rows]


# ─────────────────────────────────────────────────────────────────────────────
# WORKER  (runs lookups in a background thread)
# ─────────────────────────────────────────────────────────────────────────────

class LookupWorker(QObject):
    result_ready = pyqtSignal(int, object)   # (row_idx, DropResult)
    row_started  = pyqtSignal(int)           # row_idx
    log_msg      = pyqtSignal(str)           # log line
    all_done     = pyqtSignal()

    def __init__(self, tasks: list[tuple[int, str]], source: str, browser_path: Optional[str]):
        super().__init__()
        self._tasks        = tasks
        self._source       = source
        self._browser_path = browser_path
        self._stop         = False

    def stop(self): self._stop = True

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_all())
        finally:
            loop.close()
        self.all_done.emit()

    async def _run_all(self):
        for idx, domain in self._tasks:
            if self._stop:
                break
            self.row_started.emit(idx)
            self.log_msg.emit(f"🔍  Looking up: {domain}")
            try:
                result = await get_drop_time(domain, self._source, self._browser_path)
                self.result_ready.emit(idx, result)
                if result.drop_dt_utc:
                    self.log_msg.emit(
                        f"  ✓  {domain}  →  {result.drop_date}"
                    )
                else:
                    self.log_msg.emit(f"  ✗  {domain}  →  {result.error}")
            except Exception as e:
                self.log_msg.emit(f"  ✗  {domain}  →  exception: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# HEADER BAR WIDGET
# ─────────────────────────────────────────────────────────────────────────────

class HeaderBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(64)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(12)

        # Logo mark
        logo = QLabel()
        logo.setFixedSize(36, 36)
        logo.setStyleSheet(f"""
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 {COL['accent_dim']}, stop:1 {COL['accent_glow']});
            border-radius: 8px;
            color: #ffffff;
            font-size: 18px;
            font-weight: 700;
        """)
        logo.setText("⊙")
        logo.setAlignment(Qt.AlignCenter)
        lay.addWidget(logo)

        # Title + subtitle
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title = QLabel("Drop Time Sniper")
        title.setStyleSheet(f"color: {COL['text']}; font-size: 15px; font-weight: 700; background: transparent;")
        sub = QLabel("Domain drop date lookup  ·  Dynadot + ExpiredDomains.net")
        sub.setStyleSheet(f"color: {COL['text_muted']}; font-size: 11px; background: transparent;")
        title_col.addWidget(title)
        title_col.addWidget(sub)
        lay.addLayout(title_col)

        lay.addStretch()

        # Browser path indicator
        self.browser_label = QLabel("No browser detected")
        self.browser_label.setStyleSheet(
            f"color: {COL['text_faint']}; font-size: 11px; background: transparent;"
        )
        lay.addWidget(self.browser_label)

        self.setStyleSheet(f"""
            HeaderBar {{
                background: {COL['surface']};
                border-bottom: 1px solid {COL['border']};
            }}
        """)

    def set_browser(self, path: Optional[str]):
        if path:
            name = path.split("\\")[-1].split("/")[-1].replace(".exe", "")
            self.browser_label.setText(f"🌐  {name}")
            self.browser_label.setStyleSheet(
                f"color: {COL['success']}; font-size: 11px; background: transparent;"
            )
        else:
            self.browser_label.setText("⚠  No browser found")
            self.browser_label.setStyleSheet(
                f"color: {COL['error']}; font-size: 11px; background: transparent;"
            )


# ─────────────────────────────────────────────────────────────────────────────
# STATS BAR
# ─────────────────────────────────────────────────────────────────────────────

class StatChip(QLabel):
    def __init__(self, label: str, value: str = "0", color: str = None):
        super().__init__()
        self._label = label
        self._color = color or COL["text_muted"]
        self.setValue(value)
        self.setAlignment(Qt.AlignCenter)

    def setValue(self, value: str):
        self.setText(f"<span style='color:{COL['text_faint']};font-size:10px;'>{self._label}</span><br>"
                     f"<span style='color:{self._color};font-size:18px;font-weight:700;'>{value}</span>")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN WINDOW
# ─────────────────────────────────────────────────────────────────────────────

class DropTimeWindow(QMainWindow):

    _sig_update_row = pyqtSignal(int, object)
    _sig_row_start  = pyqtSignal(int)
    _sig_log        = pyqtSignal(str)
    _sig_done       = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Drop Time Sniper")
        self.setMinimumSize(1080, 680)
        self.resize(1280, 780)

        self._model        = DomainTableModel()
        self._worker       : Optional[LookupWorker] = None
        self._thread       : Optional[QThread]      = None
        self._browser_path : Optional[str]          = None
        self._running      = False

        self._build_ui()
        self._connect_signals()
        self._detect_browser()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(STYLESHEET)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        self.header = HeaderBar()
        root.addWidget(self.header)

        # Progress bar (hidden until running)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)   # indeterminate
        self.progress.setFixedHeight(3)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # Content area
        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(20, 16, 20, 16)
        content_lay.setSpacing(14)
        root.addWidget(content)

        # ── Input row ──
        input_card = QFrame()
        input_card.setProperty("class", "Card")
        input_card.setStyleSheet(f"""
            QFrame {{
                background: {COL['surface']};
                border: 1px solid {COL['border']};
                border-radius: 10px;
                padding: 4px;
            }}
        """)
        input_lay = QHBoxLayout(input_card)
        input_lay.setContentsMargins(14, 10, 14, 10)
        input_lay.setSpacing(10)

        self.domain_input = QLineEdit()
        self.domain_input.setPlaceholderText(
            "Enter domain(s) — space or newline separated  (e.g.  zenithpicks.com reviewindex.com)"
        )
        self.domain_input.setFixedHeight(38)
        input_lay.addWidget(self.domain_input)

        lbl_source = QLabel("Source")
        lbl_source.setStyleSheet(f"color:{COL['text_muted']};font-size:12px;background:transparent;")
        input_lay.addWidget(lbl_source)

        self.source_combo = QComboBox()
        self.source_combo.addItems(["Auto", "Dynadot", "ExpiredDomains"])
        self.source_combo.setToolTip(
            "Auto: tries Dynadot first, then ExpiredDomains.net as fallback"
        )
        input_lay.addWidget(self.source_combo)

        self.btn_lookup = QPushButton("Look Up")
        self.btn_lookup.setObjectName("btnLookup")
        self.btn_lookup.setFixedHeight(38)
        self.btn_lookup.setToolTip("Fetch drop dates for all queued domains  (Enter)")
        input_lay.addWidget(self.btn_lookup)

        content_lay.addWidget(input_card)

        # ── Stats row ──
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)

        self.stat_total   = StatChip("DOMAINS",  "0",  COL["text"])
        self.stat_done    = StatChip("FOUND",     "0",  COL["success"])
        self.stat_failed  = StatChip("FAILED",    "0",  COL["error"])
        self.stat_pending = StatChip("PENDING",   "0",  COL["accent_glow"])

        for chip in (self.stat_total, self.stat_done, self.stat_failed, self.stat_pending):
            chip.setFixedSize(100, 56)
            chip.setStyleSheet(f"""
                background: {COL['surface']};
                border: 1px solid {COL['border']};
                border-radius: 8px;
            """)
            stats_row.addWidget(chip)

        stats_row.addStretch()

        self.btn_import = QPushButton("⬆  Import .txt")
        self.btn_import.setObjectName("btnImport")
        self.btn_import.setFixedHeight(36)
        self.btn_import.setToolTip("Import a .txt file with one domain per line")
        stats_row.addWidget(self.btn_import)

        self.btn_export = QPushButton("⬇  Export CSV")
        self.btn_export.setObjectName("btnImport")
        self.btn_export.setFixedHeight(36)
        self.btn_export.setToolTip("Export all results to a CSV file")
        stats_row.addWidget(self.btn_export)

        self.btn_clear = QPushButton("✕  Clear")
        self.btn_clear.setObjectName("btnClear")
        self.btn_clear.setFixedHeight(36)
        stats_row.addWidget(self.btn_clear)

        content_lay.addLayout(stats_row)

        # ── Splitter: table | log ──
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background: {COL['border']};
                border-radius: 2px;
            }}
            QSplitter::handle:hover {{
                background: {COL['accent']};
            }}
        """)

        # Table
        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_DOMAIN,  QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_DATE,    QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_CONF,    QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_SOURCE,  QHeaderView.Stretch)
        hh.setSectionResizeMode(COL_STATUS,  QHeaderView.ResizeToContents)
        hh.setMinimumSectionSize(80)
        splitter.addWidget(self.table)

        # Log panel
        log_frame = QFrame()
        log_lay = QVBoxLayout(log_frame)
        log_lay.setContentsMargins(0, 0, 0, 0)
        log_lay.setSpacing(4)

        log_header = QHBoxLayout()
        log_title = QLabel("Log")
        log_title.setStyleSheet(
            f"color:{COL['text_muted']};font-size:11px;font-weight:600;"
            f"text-transform:uppercase;letter-spacing:1px;background:transparent;"
        )
        log_header.addWidget(log_title)
        log_header.addStretch()
        self.btn_clear_log = QPushButton("Clear log")
        self.btn_clear_log.setObjectName("btnClear")
        self.btn_clear_log.setFixedHeight(26)
        self.btn_clear_log.setStyleSheet(
            f"font-size:11px;padding:3px 10px;color:{COL['text_faint']};"
            f"border:1px solid {COL['border']};border-radius:4px;background:transparent;"
        )
        log_header.addWidget(self.btn_clear_log)
        log_lay.addLayout(log_header)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(130)
        log_lay.addWidget(self.log)
        splitter.addWidget(log_frame)

        splitter.setSizes([480, 160])
        content_lay.addWidget(splitter)

        # ── Status bar ──
        self.statusBar().setStyleSheet(
            f"background:{COL['surface']};"
            f"color:{COL['text_muted']};"
            f"border-top:1px solid {COL['border']};"
            f"font-size:12px;"
        )
        self._set_status("Ready")

    # ── Signals ────────────────────────────────────────────────────

    def _connect_signals(self):
        self.btn_lookup.clicked.connect(self._on_lookup)
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_import.clicked.connect(self._on_import)
        self.btn_export.clicked.connect(self._on_export)
        self.btn_clear_log.clicked.connect(self.log.clear)
        self.domain_input.returnPressed.connect(self._on_lookup)
        self.table.customContextMenuRequested.connect(self._table_context_menu)

        self._sig_update_row.connect(self._on_result_ready)
        self._sig_row_start.connect(self._on_row_started)
        self._sig_log.connect(self._on_log)
        self._sig_done.connect(self._on_all_done)

    # ── Browser detection ─────────────────────────────────────────────────

    def _detect_browser(self):
        self._browser_path = _find_chrome_binary()
        self.header.set_browser(self._browser_path)
        if not self._browser_path:
            self._log_html(
                f"<span style='color:{COL['error']};'>⚠  No browser binary found. "
                "Install Chrome, Edge, or Brave.</span>"
            )

    # ── Actions ───────────────────────────────────────────────────────────

    def _on_lookup(self):
        if self._running:
            # Cancel
            if self._worker:
                self._worker.stop()
            return

        raw = self.domain_input.text().strip()
        domains = [d.strip().lower() for d in raw.replace(",", " ").split() if d.strip()]
        if not domains:
            self._set_status("Enter at least one domain.")
            return

        tasks = []
        for d in domains:
            idx = self._model.add_domain(d)
            tasks.append((idx, d))
        self.domain_input.clear()
        self._update_stats()

        source_map = {"Auto": "auto", "Dynadot": "dynadot", "ExpiredDomains": "expireddomains"}
        source = source_map[self.source_combo.currentText()]

        self._start_worker(tasks, source)

    def _start_worker(self, tasks: list[tuple[int, str]], source: str):
        self._running = True
        self.btn_lookup.setText("⏹  Stop")
        self.progress.setVisible(True)

        worker = LookupWorker(tasks, source, self._browser_path)
        worker.result_ready.connect(lambda idx, r: self._sig_update_row.emit(idx, r))
        worker.row_started.connect(lambda idx: self._sig_row_start.emit(idx))
        worker.log_msg.connect(lambda m: self._sig_log.emit(m))
        worker.all_done.connect(lambda: self._sig_done.emit())

        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        thread.start()

        self._worker = worker
        self._thread = thread

    def _on_result_ready(self, idx: int, result):
        self._model.update_row(idx, result)
        self._update_stats()

    def _on_row_started(self, idx: int):
        self._model.set_status(idx, "Fetching")
        self._set_status(f"Fetching  {self._model._rows[idx].domain} ...")

    def _on_log(self, msg: str):
        self._log_html(f"<span style='color:{COL['text_muted']};'>{msg}</span>")

    def _on_all_done(self):
        self._running = False
        self.btn_lookup.setText("Look Up")
        self.progress.setVisible(False)
        self._set_status("Done.")
        if self._thread:
            self._thread.quit()
            self._thread.wait()

    def _on_clear(self):
        if self._running:
            QMessageBox.warning(self, "Running", "Stop the current lookup before clearing.")
            return
        self._model.clear()
        self._update_stats()
        self._set_status("Cleared.")

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import domain list", "", "Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            lines = [l.strip().lower() for l in f if l.strip()]
        added = 0
        for d in lines:
            d = d.lstrip("http://").lstrip("https://").split("/")[0]
            if d:
                self._model.add_domain(d)
                added += 1
        self._update_stats()
        self._set_status(f"Imported {added} domains.")
        self._on_log(f"📂  Imported {added} domains from {path}")

    def _on_export(self):
        rows = self._model._rows
        if not rows:
            self._set_status("Nothing to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", "drop_dates.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write("domain,drop_date,confidence,source,status,raw\n")
            for r in rows:
                raw_esc = (r.raw or "").replace('"', '""')
                f.write(
                    f'"{r.domain}","{r.date}","{r.conf}",'
                    f'"{r.source}","{r.status}","{raw_esc}"\n'
                )
        self._set_status(f"Exported {len(rows)} rows → {path}")
        self._on_log(f"💾  Exported to {path}")

    def _table_context_menu(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        row_idx = idx.row()
        row = self._model._rows[row_idx]
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {COL['surface2']};
                border: 1px solid {COL['border_hi']};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 7px 20px 7px 12px;
                border-radius: 4px;
                color: {COL['text']};
            }}
            QMenu::item:selected {{ background: {COL['row_select']}; }}
        """)
        act_remove = QAction("Remove row", self)
        act_retry  = QAction("Retry lookup", self)
        act_copy   = QAction("Copy domain", self)
        menu.addAction(act_copy)
        menu.addSeparator()
        if row.status in ("Error", "Done"):
            menu.addAction(act_retry)
        menu.addAction(act_remove)

        chosen = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if chosen == act_remove:
            self._model.remove_row(row_idx)
            self._update_stats()
        elif chosen == act_retry:
            source_map = {"Auto": "auto", "Dynadot": "dynadot", "ExpiredDomains": "expireddomains"}
            source = source_map[self.source_combo.currentText()]
            self._start_worker([(row_idx, row.domain)], source)
        elif chosen == act_copy:
            QApplication.clipboard().setText(row.domain)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _update_stats(self):
        rows = self._model._rows
        total   = len(rows)
        done    = sum(1 for r in rows if r.status == "Done")
        failed  = sum(1 for r in rows if r.status == "Error")
        pending = sum(1 for r in rows if r.status in ("Queued", "Fetching"))
        self.stat_total.setValue(str(total))
        self.stat_done.setValue(str(done))
        self.stat_failed.setValue(str(failed))
        self.stat_pending.setValue(str(pending))

    def _set_status(self, msg: str):
        self.statusBar().showMessage(f"  {msg}")

    def _log_html(self, html: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.append(
            f"<span style='color:{COL['text_faint']};'>{ts}</span>  {html}"
        )
        self.log.verticalScrollBar().setValue(
            self.log.verticalScrollBar().maximum()
        )

    def closeEvent(self, event):
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            self._thread.wait(2000)
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Drop Time Sniper")
    app.setStyle("Fusion")

    # Apply dark Fusion palette so native dialogs (file picker etc.) match
    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor(COL["bg"]))
    pal.setColor(QPalette.WindowText,      QColor(COL["text"]))
    pal.setColor(QPalette.Base,            QColor(COL["surface"]))
    pal.setColor(QPalette.AlternateBase,   QColor(COL["surface2"]))
    pal.setColor(QPalette.ToolTipBase,     QColor(COL["surface3"]))
    pal.setColor(QPalette.ToolTipText,     QColor(COL["text"]))
    pal.setColor(QPalette.Text,            QColor(COL["text"]))
    pal.setColor(QPalette.Button,          QColor(COL["surface2"]))
    pal.setColor(QPalette.ButtonText,      QColor(COL["text"]))
    pal.setColor(QPalette.BrightText,      QColor(COL["accent_glow"]))
    pal.setColor(QPalette.Highlight,       QColor(COL["row_select"]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.Link,            QColor(COL["accent"]))
    pal.setColor(QPalette.Midlight,        QColor(COL["surface2"]))
    pal.setColor(QPalette.Mid,             QColor(COL["border"]))
    pal.setColor(QPalette.Dark,            QColor(COL["bg"]))
    pal.setColor(QPalette.Shadow,          QColor("#000000"))
    app.setPalette(pal)

    win = DropTimeWindow()
    win.show()
    sys.exit(app.exec_())
