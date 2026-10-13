"""Shared stylesheet for a clean, light, medical-themed UI (blues/whites/greens)."""

PRIMARY = "#1565C0"      # blue
PRIMARY_DARK = "#0D47A1"
ACCENT = "#2E7D32"       # green
BACKGROUND = "#F5F7FA"
DANGER = "#C62828"

APP_STYLESHEET = f"""
QWidget {{
    background-color: {BACKGROUND};
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 14px;
    color: #1A1A1A;
}}

QMainWindow, QDialog {{
    background-color: {BACKGROUND};
}}

QPushButton {{
    background-color: {PRIMARY};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 10px 18px;
    font-weight: 600;
}}
QPushButton:hover {{ background-color: {PRIMARY_DARK}; }}
QPushButton:disabled {{ background-color: #B0BEC5; color: #ECECEC; }}

QPushButton[flat="true"] {{
    background-color: transparent;
    color: {PRIMARY};
    text-align: left;
    padding: 12px 16px;
    border-radius: 0px;
    font-weight: 500;
}}
QPushButton[flat="true"]:hover {{ background-color: #E3ECF7; }}
QPushButton[flat="true"][active="true"] {{
    background-color: {PRIMARY};
    color: white;
}}

QPushButton[danger="true"] {{ background-color: {DANGER}; }}
QPushButton[danger="true"]:hover {{ background-color: #8E0000; }}

QPushButton[success="true"] {{ background-color: {ACCENT}; }}
QPushButton[success="true"]:hover {{ background-color: #1B5E20; }}

QPushButton[compact="true"] {{
    padding: 5px 10px;
    font-size: 13px;
    font-weight: 500;
    min-width: 64px;
}}

QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox, QTextEdit {{
    background-color: white;
    border: 1px solid #C7D0DA;
    border-radius: 4px;
    padding: 6px 8px;
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {PRIMARY}; }}

QTableWidget {{
    background-color: white;
    gridline-color: #E0E4E8;
    border: 1px solid #DDE3E9;
    selection-background-color: #BBDEFB;
    selection-color: #0D1B2A;
}}
QHeaderView::section {{
    background-color: {PRIMARY};
    color: white;
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
    color: #546E7A;
}}
QLabel[warning="true"] {{
    color: {DANGER};
    font-weight: 600;
}}

#Sidebar {{
    background-color: white;
    border-right: 1px solid #DDE3E9;
}}

#DashboardCard {{
    background-color: white;
    border-radius: 8px;
    border: 1px solid #DDE3E9;
}}

QTabWidget::pane {{ border: 1px solid #DDE3E9; background: white; }}
QTabBar::tab {{ padding: 8px 16px; }}
QTabBar::tab:selected {{ background: {PRIMARY}; color: white; }}
"""
