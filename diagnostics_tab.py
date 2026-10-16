"""Run QAQC and numerical debugging, leaving field visualization to ParaView."""
import json
import math
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QFileDialog, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QPlainTextEdit, QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QProgressBar, QMessageBox,
)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT

from run_diagnostics import Limits, RunDiagnostics, parse_log, export_diagnostics_csv


class LogLoader(QThread):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, path, purpose, generation, parent):
        super().__init__(parent)
        self.path, self.purpose, self.generation = path, purpose, generation

    def run(self):
        try:
            self.loaded.emit(parse_log(self.path, self.isInterruptionRequested))
        except (OSError, ValueError, InterruptedError) as exc:
            self.failed.emit(str(exc))


class DiagnosticsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.case_dir = ''
        self.report = RunDiagnostics()
        self.baseline = None
        self.worker = None
        self._generation = 0
        self._dirty = False
        self._summary_base = 'No diagnostic samples yet.'
        self._last_live_report = None
        layout = QVBoxLayout(self)
        intro = QLabel('Review numerical stability, conservation and computational cost. '
                       'Accuracy still requires mesh/time-step refinement and comparison with observations.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        files = QHBoxLayout()
        self.log_combo = QComboBox()
        self.log_combo.setMinimumContentsLength(25)
        self.log_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.log_combo.activated.connect(self.load_selected)
        files.addWidget(self.log_combo, 1)
        self.refresh_button = QPushButton('Refresh logs')
        self.refresh_button.clicked.connect(self.refresh_logs)
        files.addWidget(self.refresh_button)
        browse = QPushButton('Open log…')
        browse.clicked.connect(self.browse_log)
        files.addWidget(browse)
        compare = QPushButton('Compare log…')
        compare.clicked.connect(self.browse_baseline)
        files.addWidget(compare)
        clear = QPushButton('Clear comparison')
        clear.clicked.connect(self.clear_baseline)
        files.addWidget(clear)
        layout.addLayout(files)
        self.follow_live = QCheckBox('Follow the active command (updates every second)')
        self.follow_live.setChecked(True)
        self.follow_live.toggled.connect(self._follow_changed)
        layout.addWidget(self.follow_live)
        self.source_label = QLabel('No log selected. Run a command or open an existing solver log.')
        self.source_label.setWordWrap(True)
        self.source_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.source_label)
        self.summary = QLabel('No diagnostic samples yet.')
        self.summary.setWordWrap(True)
        self.summary.setObjectName('diagnosticSummary')
        layout.addWidget(self.summary)
        self.loading = QProgressBar()
        self.loading.setRange(0, 0)
        self.loading.hide()
        layout.addWidget(self.loading)
        self.pages = QTabWidget()
        layout.addWidget(self.pages, 1)

        history = QWidget()
        history_layout = QVBoxLayout(history)
        choices = QHBoxLayout()
        choices.addWidget(QLabel('Residual field:'))
        self.field_combo = QComboBox()
        self.field_combo.currentTextChanged.connect(self.redraw)
        choices.addWidget(self.field_combo)
        choices.addWidget(QLabel('Cost / time-step plot:'))
        self.cost_combo = QComboBox()
        self.cost_combo.addItems(['Time step (s)', 'Wall time per step (s)'])
        self.cost_combo.currentIndexChanged.connect(self.redraw)
        choices.addWidget(self.cost_combo)
        choices.addWidget(QLabel('Conservation plot:'))
        self.sediment_combo = QComboBox()
        self.sediment_combo.addItems(['Step-budget residual (kg)', 'Sediment inventories (kg)', 'Minimum concentration (kg/m³)',
                                     'Cumulative continuity (reported)'])
        self.sediment_combo.currentIndexChanged.connect(self.redraw)
        choices.addWidget(self.sediment_combo)
        choices.addStretch()
        history_layout.addLayout(choices)
        self.figure = Figure(figsize=(9, 5), layout='constrained')
        self.canvas = FigureCanvasQTAgg(self.figure)
        history_layout.addWidget(NavigationToolbar2QT(self.canvas, self))
        history_layout.addWidget(self.canvas, 1)
        history_layout.addWidget(QLabel('Long traces are sampled for plotting; findings and CSV use all parsed samples.'))
        self.pages.addTab(history, 'Histories')

        findings = QWidget()
        findings_layout = QVBoxLayout(findings)
        thresholds = QGroupBox('Screening thresholds — review prompts; no case files are edited')
        form = QFormLayout(thresholds)
        self.co_limit = self._limit(.5, 6, 1e-6, 10000)
        self.residual_limit = self._limit(1e-6, 12, 1e-12, 1)
        self.budget_limit = self._limit(1e-10, 15, 1e-15, 1e12)
        form.addRow('Water Courant target:', self.co_limit)
        form.addRow('Final linear residual target:', self.residual_limit)
        form.addRow('Absolute sediment step-budget tolerance (kg):', self.budget_limit)
        findings_layout.addWidget(thresholds)
        self.findings_table = QTableWidget(0, 4)
        self.findings_table.setHorizontalHeaderLabels(['Level', 'Check', 'Evidence', 'Controls / investigation'])
        self.findings_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.findings_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.findings_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.findings_table.verticalHeader().hide()
        findings_layout.addWidget(self.findings_table, 1)
        self.pages.addTab(findings, 'Findings and controls')
        self.events = QPlainTextEdit()
        self.events.setReadOnly(True)
        self.pages.addTab(self.events, 'Error / warning context')
        self.settings = QPlainTextEdit()
        self.settings.setReadOnly(True)
        self.pages.addTab(self.settings, 'Run settings snapshot')
        export = QPushButton('Export full diagnostic history (CSV)')
        export.clicked.connect(self.export_csv)
        layout.addWidget(export)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_live)
        self.timer.start()

    def _limit(self, value, decimals, minimum, maximum):
        spin = QDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.valueChanged.connect(self.redraw)
        return spin

    def limits(self):
        return Limits(self.co_limit.value(), self.residual_limit.value(), self.budget_limit.value())

    def set_case_directory(self, path):
        self._generation += 1
        self.case_dir = path
        self.report, self.baseline = RunDiagnostics(), None
        self._last_live_report = None
        self.follow_live.setChecked(True)
        self.refresh_logs()
        self.render_report()

    def refresh_logs(self):
        previous = self.log_combo.currentData()
        self.log_combo.blockSignals(True)
        self.log_combo.clear()
        if self.case_dir:
            case = Path(self.case_dir)
            candidates = list(case.glob('log.*')) + list((case/'logs').glob('*/console.log'))
            paths = sorted((p for p in candidates if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in paths:
                self.log_combo.addItem(str(path.relative_to(case)), str(path))
            index = self.log_combo.findData(previous)
            self.log_combo.setCurrentIndex(max(0, index))
        self.log_combo.blockSignals(False)
        if self.log_combo.count() and (not self.report.source or not self.follow_live.isChecked()):
            self.load_log(self.log_combo.currentData())

    def load_selected(self, *_):
        path = self.log_combo.currentData()
        if path:
            self.follow_live.setChecked(False)
            self.load_log(path)

    def browse_log(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open solver/utility log', self.case_dir, 'Log files (*.log log.*);;All files (*)')
        if path:
            self.follow_live.setChecked(False)
            self.load_log(path)

    def browse_baseline(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select comparison log', self.case_dir, 'Log files (*.log log.*);;All files (*)')
        if path:
            self.load_log(path, 'baseline')

    def load_log(self, path, purpose='current'):
        if self.worker is not None:
            self.source_label.setText('A log is loading; wait before selecting another log.')
            return
        self.worker = LogLoader(str(path), purpose, self._generation, self)
        self.worker.loaded.connect(self._loaded)
        self.worker.failed.connect(self._load_failed)
        self.worker.finished.connect(self._load_finished)
        self.loading.show()
        self.source_label.setText('Reading '+str(path))
        self.worker.start()

    def _load_failed(self, message):
        if self.worker.generation == self._generation:
            self.source_label.setText('Cannot load log: '+message)

    def _loaded(self, report):
        if self.worker.generation != self._generation:
            return
        if self.worker.purpose == 'baseline':
            self.baseline = report
        else:
            if self.follow_live.isChecked() and self._last_live_report is not None:
                return
            self.report = report
        self.render_report()

    def _load_finished(self):
        worker, self.worker = self.worker, None
        self.loading.hide()
        worker.deleteLater()
        if worker.generation != self._generation and not self.report.source:
            self.refresh_logs()

    def clear_baseline(self):
        self.baseline = None
        self.render_report()

    def receive_live(self, report):
        self._last_live_report = report
        if self.follow_live.isChecked():
            self.report = report
            self._dirty = True

    def _follow_changed(self, checked):
        if checked and self._last_live_report is not None:
            self.report = self._last_live_report
            self.render_report()

    def refresh_live(self):
        if self._dirty and self.follow_live.isChecked() and self.isVisible():
            self._dirty = False
            self.render_report()

    def render_report(self):
        self.source_label.setText('Log: '+(self.report.source or 'not selected')
                                 + ('\nComparison: '+self.baseline.source if self.baseline else ''))
        report = self.report
        latest = report.steps[-1] if report.steps else None
        co, budget = report.values('co_max'), report.values('budget_residual_kg')
        self._summary_base = (
            f'{len(report.steps):,} time steps | {len(report.residuals):,} linear solves'
            + (f' | Latest t: {latest.time:g} s' if latest else '')
            + (f' | Peak water Co: {max(co):.4g}' if co else ' | Water Co: not logged')
            + (f' | Peak |sediment budget residual|: {max(map(abs,budget)):.4g} kg' if budget else '')
            + (f' | Clock: {latest.clock_s:g} s' if latest and latest.clock_s is not None else '')
            + (' | CANCELED' if report.cancelled else (f' | Exit code: {report.exit_code}' if report.exit_code is not None else ''))
            + (' | End marker present' if report.completed else ' | No normal End marker'))
        fields = sorted({item.field for item in report.residuals}
                        | ({item.field for item in self.baseline.residuals} if self.baseline else set()))
        current = self.field_combo.currentText()
        self.field_combo.blockSignals(True)
        self.field_combo.clear()
        self.field_combo.addItems(fields)
        self.field_combo.setCurrentText(current if current in fields else ('p' if 'p' in fields else next(iter(fields), '')))
        self.field_combo.blockSignals(False)
        self.events.setPlainText('\n'.join(dict.fromkeys(report.events)) or 'No recognized error/warning context in this log.')
        settings = Path(report.source).with_name('settings.json') if report.source else None
        try:
            data = json.loads(settings.read_text(encoding='utf-8')) if settings and settings.is_file() else None
            text = (f'Command: {data["command"]}\nCase: {data["case"]}\nCreated UTC: {data["created_utc"]}\n'
                    f'OpenFOAM: {data["openfoam_version"]}\n\n'
                    +'\n\n'.join(f'--- {path} ---\n{content}' for path,content in data['files'].items())) if data else (
                        'No run settings snapshot accompanies this log. Current case dictionaries may differ from those used for the run.')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            text = 'Cannot read run settings snapshot: '+str(exc)
        if self.settings.toPlainText() != text:
            self.settings.setPlainText(text)
        self.redraw()

    def redraw(self, *_):
        if not hasattr(self, 'findings_table'):
            return
        findings = self.report.findings(self.limits())
        errors = sum(item.level == 'Error' for item in findings)
        reviews = sum(item.level == 'Review' for item in findings)
        self.summary.setText(f'{errors} error(s) · {reviews} review item(s) | '+self._summary_base)
        background, foreground = (('#fde8e8', '#7f1d1d') if errors else (
            ('#fff2d2', '#713f12') if reviews else ('#e6f4f1', '#134e4a')))
        self.summary.setStyleSheet(f'background: {background}; color: {foreground}; padding: 10px; border-radius: 5px;')
        self.findings_table.setRowCount(len(findings))
        for row, finding in enumerate(findings):
            for column, text in enumerate((finding.level, finding.title, finding.evidence, finding.action)):
                self.findings_table.setItem(row, column, QTableWidgetItem(text))
        self.findings_table.resizeRowsToContents()
        self.figure.clear()
        axes = self.figure.subplots(2, 2)
        for report, prefix, color in [(self.report, 'Current', '#007f86'), (self.baseline, 'Comparison', '#985aba')]:
            if report is None:
                continue
            residuals = [item for item in report.residuals if item.field == self.field_combo.currentText()]
            self._plot_records(axes[0,0], residuals, 'initial', prefix+' initial', color)
            self._plot_records(axes[0,0], residuals, 'final', prefix+' final', color, '--')
            self._plot_records(axes[0,1], report.steps, 'co_max', prefix+' water', color)
            self._plot_records(axes[0,1], report.steps, 'sediment_co_max', prefix+' sediment', color, '--')
            self._plot_records(axes[0,1], report.steps, 'interface_co_max', prefix+' interface', color, ':')
            cost = 'delta_t' if self.cost_combo.currentIndex() == 0 else 'wall_step_s'
            self._plot_records(axes[1,0], report.steps, cost, prefix, color)
            sediment = [('budget_residual_kg', '')] if self.sediment_combo.currentIndex() == 0 else (
                [('suspended_kg',' suspended'),('bed_kg',' bed'),('mobile_kg',' mobile'),('deposited_kg',' deposited')]
                if self.sediment_combo.currentIndex() == 1 else (
                    [('min_c_kg_m3','')] if self.sediment_combo.currentIndex() == 2 else [('continuity_cumulative','')]))
            for index, (metric, label) in enumerate(sediment):
                self._plot_records(axes[1,1], report.steps, metric, prefix+label, color, ['-','--',':','-.'][index])
        titles = [('Linear residual: '+(self.field_combo.currentText() or 'not logged'), 'Reported residual'),
                  ('Courant history', 'Courant number'),
                  (self.cost_combo.currentText(), 'Seconds'),
                  (self.sediment_combo.currentText(), {2:'kg/m³', 3:'Solver-reported value'}.get(self.sediment_combo.currentIndex(), 'kg'))]
        for axis, (title, ylabel) in zip(axes.flat, titles):
            axis.set_title(title, fontsize=10)
            axis.set_xlabel('Solver time / iteration', fontsize=9)
            axis.set_ylabel(ylabel, fontsize=9)
            axis.tick_params(labelsize=8)
            axis.grid(True, alpha=.2)
            if axis.lines:
                axis.legend(fontsize=7, loc='best')
            else:
                axis.text(.5, .5, 'Not logged', ha='center', va='center', transform=axis.transAxes, color='#64748b')
        if axes[0,0].lines:
            axes[0,0].set_yscale('symlog', linthresh=1e-15)
        axes[0,1].axhline(self.co_limit.value(), color='#c47a17', linestyle=':', linewidth=1,
                          label=f'Water target {self.co_limit.value():g}')
        axes[0,1].legend(fontsize=7, loc='best')
        if self.sediment_combo.currentIndex() == 0:
            axes[1,1].set_yscale('symlog', linthresh=self.budget_limit.value())
        self.canvas.draw_idle()

    @staticmethod
    def _plot_records(axis, records, attribute, label, color, style='-'):
        segments = sorted({item.segment for item in records})
        shown = False
        for segment in segments:
            values = [(item.time, getattr(item, attribute)) for item in records
                      if item.segment == segment and getattr(item, attribute) is not None
                      and math.isfinite(getattr(item, attribute))]
            if values:
                # Bound draw cost; peaks are still checked against all parsed samples.
                stride = max(1, math.ceil(len(values)/4000))
                points = np.asarray(values[::stride] + ([values[-1]] if len(values) % stride else []))
                axis.plot(points[:,0], points[:,1], style, color=color, linewidth=1,
                          marker='.' if len(points) < 3 else None,
                          label=label if not shown else '_nolegend_')
                shown = True

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export complete diagnostic history',
                                             str(Path(self.case_dir or '.')/'diagnostics.csv'), 'CSV (*.csv)')
        if path:
            try:
                export_diagnostics_csv(path, self.report)
            except OSError as exc:
                QMessageBox.warning(self, 'Export failed', str(exc))
