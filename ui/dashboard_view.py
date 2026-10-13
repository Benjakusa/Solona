"""Dashboard: welcome banner, low-stock/expiry alerts, today's sales summary,
a 7-day sales trend chart, and quick-action shortcuts."""
from datetime import date

from PySide6.QtCharts import QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QValueAxis
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from logic import auth, backup, inventory, reports, settings


def _build_sales_trend_chart(trend: list[dict]) -> QChartView:
    bar_set = QBarSet("Revenue")
    bar_set.append([t["total"] for t in trend])
    bar_set.setColor(QColor("#1565C0"))

    series = QBarSeries()
    series.append(bar_set)

    chart = QChart()
    chart.addSeries(series)
    chart.setTitle("Sales — Last 7 Days")
    chart.legend().setVisible(False)
    chart.setBackgroundVisible(False)

    axis_x = QBarCategoryAxis()
    axis_x.append([t["day"][5:] for t in trend])  # MM-DD, short enough to fit
    chart.addAxis(axis_x, Qt.AlignBottom)
    series.attachAxis(axis_x)

    max_total = max((t["total"] for t in trend), default=0)
    axis_y = QValueAxis()
    axis_y.setRange(0, max(max_total * 1.2, 10))
    chart.addAxis(axis_y, Qt.AlignLeft)
    series.attachAxis(axis_y)

    view = QChartView(chart)
    view.setRenderHint(QPainter.Antialiasing)
    view.setMinimumHeight(220)
    view.setMaximumHeight(260)
    return view


class _AlertCard(QWidget):
    def __init__(self, title: str, count: int, detail: str, button_text: str, on_click):
        super().__init__()
        self.setObjectName("DashboardCard")
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)

        title_label = QLabel(title)
        title_label.setProperty("subheading", True)
        layout.addWidget(title_label)

        count_label = QLabel(str(count))
        count_label.setProperty("heading", True)
        layout.addWidget(count_label)

        detail_label = QLabel(detail)
        detail_label.setWordWrap(True)
        layout.addWidget(detail_label)

        if count > 0:
            btn = QPushButton(button_text)
            btn.clicked.connect(on_click)
            layout.addWidget(btn)

        self.setLayout(layout)


class DashboardView(QWidget):
    def __init__(self, current_user: auth.User, navigate_callback=None):
        super().__init__()
        self.current_user = current_user
        self.navigate_callback = navigate_callback
        self.layout_ = QVBoxLayout()
        self.setLayout(self.layout_)
        self.refresh()

    def refresh(self):
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        title = QLabel(f"Welcome, {self.current_user.full_name or self.current_user.username}")
        title.setProperty("heading", True)
        self.layout_.addWidget(title)

        subtitle = QLabel(f"Role: {self.current_user.role.capitalize()}   |   {date.today().isoformat()}")
        subtitle.setProperty("subheading", True)
        self.layout_.addWidget(subtitle)

        cards_row = QHBoxLayout()

        low_stock = inventory.low_stock_medicines()
        cards_row.addWidget(
            _AlertCard(
                "⚠️  Low Stock Medicines", len(low_stock),
                ", ".join(m.name for m in low_stock[:4]) or "Everything is well stocked.",
                "📦  View Inventory", self._go_to_inventory,
            )
        )

        expiring = inventory.expiring_medicines(settings.get_expiry_alert_days())
        cards_row.addWidget(
            _AlertCard(
                f"⏳  Expiring within {settings.get_expiry_alert_days()} days", len(expiring),
                ", ".join(m.name for m in expiring[:4]) or "No medicines expiring soon.",
                "📦  View Inventory", self._go_to_inventory,
            )
        )

        today_report = reports.daily_sales_report(date.today().isoformat())
        totals = today_report["totals"]
        cards_row.addWidget(
            _AlertCard(
                "💰  Today's Sales", totals["count"],
                f"Total revenue: {totals['total_amount']:.2f}",
                "🧾  View Billing", self._go_to_billing,
            )
        )

        self.layout_.addLayout(cards_row)

        trend = reports.sales_trend(7)
        self.layout_.addWidget(_build_sales_trend_chart(trend))

        if self.current_user.is_admin:
            backup_row = QHBoxLayout()
            backup_btn = QPushButton("💾  Backup Now")
            backup_btn.setProperty("success", True)
            backup_btn.clicked.connect(self._quick_backup)
            backup_row.addWidget(backup_btn)
            backup_row.addStretch()
            self.layout_.addLayout(backup_row)

        self.layout_.addStretch()

    def _go_to_inventory(self):
        if self.navigate_callback:
            self.navigate_callback("Inventory")

    def _go_to_billing(self):
        if self.navigate_callback:
            self.navigate_callback("Billing")

    def _quick_backup(self):
        try:
            record = backup.create_backup(backup_type="manual")
            QMessageBox.information(
                self, "Backup Successful", f"Backup Successful ✅\n\nSaved to:\n{record.filename}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Backup Failed", str(e))
