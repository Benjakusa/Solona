"""Shared sky-blue and green stylesheet for the application."""

PRIMARY = "#38BDF8"      # sky blue
PRIMARY_DARK = "#0284C7"
ACCENT = "#22C55E"       # green
BACKGROUND = "#E0F2FE"
DANGER = "#16A34A"       # errors use the allowed green palette
TEXT = "#14532D"
SURFACE = "#FFFFFF"
SKY_PALE = "#BAE6FD"
GREEN_DARK = "#15803D"

APP_STYLESHEET = f"""
QWidget {{
    background-color: {BACKGROUND};
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 14px;
    color: {TEXT};
}}

QMainWindow, QDialog {{
    background-color: {BACKGROUND};
}}

QPushButton {{
    background-color: {PRIMARY};
    color: {TEXT};
    border: none;
    border-radius: 6px;
    padding: 10px 18px;
    font-weight: 600;
}}
QPushButton:hover {{ background-color: {PRIMARY_DARK}; }}
QPushButton:disabled {{ background-color: {SKY_PALE}; color: {GREEN_DARK}; }}

QPushButton[flat="true"] {{
    background-color: transparent;
    color: {PRIMARY};
    text-align: left;
    padding: 12px 16px;
    border-radius: 0px;
    font-weight: 500;
}}
QPushButton[flat="true"]:hover {{ background-color: {SKY_PALE}; }}
QPushButton[flat="true"][active="true"] {{
    background-color: {PRIMARY};
    color: {TEXT};
}}

QPushButton[danger="true"] {{ background-color: {DANGER}; }}
QPushButton[danger="true"]:hover {{ background-color: {GREEN_DARK}; }}

QPushButton[success="true"] {{ background-color: {ACCENT}; }}
QPushButton[success="true"]:hover {{ background-color: {GREEN_DARK}; }}

QPushButton[compact="true"] {{
    padding: 5px 10px;
    font-size: 13px;
    font-weight: 500;
    min-width: 64px;
}}

QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox, QTextEdit {{
    background-color: {SURFACE};
    border: 1px solid {PRIMARY};
    border-radius: 4px;
    padding: 6px 8px;
}}
QLineEdit:focus, QComboBox:focus {{ border: 2px solid {PRIMARY_DARK}; }}

QTableWidget {{
    background-color: {SURFACE};
    gridline-color: {SKY_PALE};
    border: 1px solid {PRIMARY};
    selection-background-color: {SKY_PALE};
    selection-color: {TEXT};
}}
QHeaderView::section {{
    background-color: {PRIMARY};
    color: {TEXT};
    padding: 6px;
    border: none;
    font-weight: 600;
}}

QLabel[heading="true"] {{
    font-size: 20px;
    font-weight: 700;
    color: {PRIMARY_DARK};
}}
QLabel[subheading="true"] {{
    font-size: 13px;
    color: {GREEN_DARK};
}}
QLabel[warning="true"] {{
    color: {DANGER};
    font-weight: 600;
}}

#Sidebar {{
    background-color: {TEXT};
    border-right: 1px solid {PRIMARY};
}}

#DashboardCard {{
    background-color: {SURFACE};
    border-radius: 8px;
    border: 1px solid {PRIMARY};
}}

QTabWidget::pane {{ border: 1px solid {PRIMARY}; background: {SURFACE}; }}
QTabBar::tab {{ padding: 8px 16px; }}
QTabBar::tab:selected {{ background: {PRIMARY}; color: {TEXT}; }}
"""
