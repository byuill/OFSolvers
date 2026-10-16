import math
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QPushButton,
    QTabWidget,
    QComboBox,
    QGroupBox,
    QFormLayout,
    QGridLayout,
    QScrollArea,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QToolBar,
    QStyle,
    QDialog,
    QTextBrowser,
)
from PyQt6.QtCore import pyqtSignal, Qt, QUrl
from PyQt6.QtGui import QAction, QKeySequence
from case_qaqc import SOLVERS
from foam_io import value, scalar

from mesh_tab import MeshTab
from turbulence_tab import TurbulenceTab
from boundary_tab import BoundaryTab
from geometry_tab import GeometryTab
from terrain_tab import TerrainTab
from execution_tab import ExecutionTab
from sediment_tab import SedimentTab
from diagnostics_tab import DiagnosticsTab


class MainWindow(QMainWindow):
    case_directory_changed = pyqtSignal(str)
    def __init__(self):
        super().__init__()
        self.setWindowTitle('OFSolvers · River modeling workbench')
        self.resize(1360, 900)
        self.setMinimumSize(1000, 680)

        # Main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        self.case_dir = ''
        case_layout = QHBoxLayout()
        self.case_dir_edit = QLineEdit()
        self.case_dir_edit.setPlaceholderText('Select the case folder used by all tabs')
        self.case_dir_edit.setReadOnly(True)
        self.case_browse_button = QPushButton('Select case folder')
        self.case_browse_button.clicked.connect(self.browse_case_directory)
        case_layout.addWidget(QLabel('Active case:'))
        case_layout.addWidget(self.case_dir_edit, 1)
        case_layout.addWidget(self.case_browse_button)
        layout.addLayout(case_layout)

        self.case_hint = QLabel('Start by selecting a case folder. River Sediment can create a runnable channel starter in an empty folder.')
        self.case_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.case_hint.setWordWrap(True)
        layout.addWidget(self.case_hint)

        # Tab Widget
        self.tabs = QTabWidget()
        self.workspace = QSplitter(Qt.Orientation.Horizontal)
        self.navigation = QTreeWidget()
        self.navigation.setObjectName('workflowNavigation')
        self.navigation.setHeaderHidden(True)
        self.navigation.setRootIsDecorated(False)
        self.navigation.setMinimumWidth(210)
        self.workspace.addWidget(self.navigation)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        self.stage_title = QLabel()
        self.stage_title.setObjectName('stageTitle')
        self.stage_description = QLabel()
        self.stage_description.setWordWrap(True)
        content_layout.addWidget(self.stage_title)
        content_layout.addWidget(self.stage_description)
        content_layout.addWidget(self.tabs, 1)
        self.workspace.addWidget(content)
        self.workspace.setStretchFactor(1, 1)
        self.workspace.setSizes([240, 1100])
        layout.addWidget(self.workspace, 1)
        self.tabs.tabBar().hide()

        # Tab 1: Conceptualization
        self.tab_concept = QWidget()
        self.tabs.addTab(self.tab_concept, "1. Conceptualization")
        self.setup_conceptual_tab()

        # Tab 2: Geometry Preprocessing
        self.tab_geometry = GeometryTab()
        self.tabs.addTab(self.tab_geometry, "2. Geometry Preprocessing")

        # Tab 3: Terrain Processor
        self.tab_terrain = TerrainTab()
        self.tabs.addTab(self.tab_terrain, "3. Terrain Processor")

        # Tab 4: Mesh Generation
        self.tab_mesh = MeshTab()
        self.tabs.addTab(self.tab_mesh, "4. Mesh Generation")
        self.tab_geometry.set_mesh_tab(self.tab_mesh)  # Provide MeshTab to GeometryTab

        # Tab 5: Boundary Conditions
        self.tab_boundaries = BoundaryTab()
        self.tab_boundaries.set_mesh_tab(self.tab_mesh)
        self.solver_combo.currentTextChanged.connect(
            self.tab_boundaries.set_solver_profile
        )
        self.tab_boundaries.set_solver_profile(self.solver_combo.currentText())
        self.tabs.addTab(self.tab_boundaries, "5. Boundary Conditions")

        # Tab 6: Turbulence Modeling
        self.tab_turbulence = TurbulenceTab()
        self.tabs.addTab(self.tab_turbulence, "6. Turbulence Modeling")
        self.sim_combo.currentTextChanged.connect(self.tab_turbulence.update_regime)
        self.tab_turbulence.update_regime(self.sim_combo.currentText())

        self.tab_sediment = SedimentTab()
        self.tabs.addTab(self.tab_sediment, '7. River Sediment')
        self.solver_combo.currentTextChanged.connect(self.tab_sediment.set_solver)
        self.tab_sediment.solver_changed.connect(self._sync_execution_solver)
        self.tab_sediment.case_prepared.connect(self._on_starter_prepared)

        # Tab 8: Execution & Monitoring
        self.tab_execution = ExecutionTab()
        self.tabs.addTab(self.tab_execution, '8. Execution & Monitoring')

        # Global Run/Save Button
        self.run_button = QPushButton('Save plan, time controls and mesh inputs')
        layout.addWidget(self.run_button)
        self.solver_combo.currentTextChanged.connect(self.tab_execution.set_solver)
        self.tab_execution.combo_solver.currentTextChanged.connect(self._sync_execution_solver)
        self.tab_execution.busy_changed.connect(self.set_busy)

        self.tab_diagnostics = DiagnosticsTab()
        self.tabs.addTab(self.tab_diagnostics, '9. Run diagnostics')
        self.tab_execution.diagnostics_updated.connect(self.tab_diagnostics.receive_live)
        self.tab_geometry.river_design.case_exported.connect(self._on_starter_prepared)
        self.tab_geometry.river_design.mesh_requested.connect(self._build_river_mesh)
        self.tab_execution.river_mesh_finished.connect(self.tab_geometry.river_design.mesh_completed)
        self._setup_navigation()
        self._setup_toolbar()
        self._apply_style()
        self.tabs.currentChanged.connect(self._stage_changed)
        self._stage_changed(0)

    def _setup_navigation(self):
        self._navigation_items = {}
        groups = [('PLAN', [0]), ('PREPROCESS', [1, 2, 3]), ('PHYSICS', [4, 5, 6]), ('RUN AND REVIEW', [7, 8])]
        for name, indices in groups:
            group = QTreeWidgetItem([name])
            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            self.navigation.addTopLevelItem(group)
            for index in indices:
                item = QTreeWidgetItem([self.tabs.tabText(index)])
                item.setData(0, Qt.ItemDataRole.UserRole, index)
                group.addChild(item)
                self._navigation_items[index] = item
            group.setExpanded(True)
        self.navigation.currentItemChanged.connect(self._navigate)

    def _navigate(self, item, previous):
        if item is not None:
            index = item.data(0, Qt.ItemDataRole.UserRole)
            if index is not None and self.tabs.isTabEnabled(index):
                self.tabs.setCurrentIndex(index)

    def _stage_changed(self, index):
        descriptions = [
            'Plan the domain, time controls and hydraulic regime. Saving the plan does not start a simulation.',
            'Design synthetic river terrain, wall spacing, obstacles and hydraulic boundaries, or prepare imported geometry.',
            'Convert projected terrain data into local model geometry; check units and vertical reference.',
            'Create the background mesh and snappyHexMesh inputs, then inspect mesh quality.',
            'Set water-flow boundaries using the actual mesh patch names.',
            'Configure the turbulence closure and its initial/boundary fields.',
            'Prepare suspended-concentration or sand-parcel transport on a fixed water domain and fixed bed.',
            'Check case files and the native mesh, execute commands, and retain their logs and settings.',
            'Review residuals, Courant numbers, conservation, computational cost and error context; compare runs.',
        ]
        self.stage_title.setText(self.tabs.tabText(index))
        self.stage_description.setText(descriptions[index])
        item = self._navigation_items.get(index)
        if item is not None and self.navigation.currentItem() is not item:
            self.navigation.setCurrentItem(item)
        if hasattr(self, 'tab_diagnostics') and self.tabs.widget(index) is self.tab_diagnostics:
            self.tab_diagnostics.render_report()

    def _setup_toolbar(self):
        toolbar = QToolBar('Case workflow', self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        def action(text, icon, callback, shortcut=None):
            result = QAction(self.style().standardIcon(icon), text, self)
            result.triggered.connect(callback)
            if shortcut:
                result.setShortcut(shortcut)
            toolbar.addAction(result)
            return result
        self.open_action = action('Open case', QStyle.StandardPixmap.SP_DirOpenIcon,
                                  self.browse_case_directory, QKeySequence.StandardKey.Open)
        self.save_action = action('Save plan', QStyle.StandardPixmap.SP_DialogSaveButton,
                                  self.run_button.click, QKeySequence.StandardKey.Save)
        toolbar.addSeparator()
        self.check_action = action('Check case', QStyle.StandardPixmap.SP_DialogApplyButton,
                                   self._check_from_toolbar)
        self.solve_action = action('Run solver', QStyle.StandardPixmap.SP_MediaPlay,
                                   self._run_from_toolbar)
        action('Run diagnostics', QStyle.StandardPixmap.SP_FileDialogDetailedView,
               lambda: self.tabs.setCurrentWidget(self.tab_diagnostics))
        toolbar.addSeparator()
        action('GUI guide', QStyle.StandardPixmap.SP_DialogHelpButton, self.show_gui_guide,
               QKeySequence.StandardKey.HelpContents)

    def _check_from_toolbar(self):
        self.tabs.setCurrentWidget(self.tab_execution)
        self.tab_execution.run_qaqc()

    def _run_from_toolbar(self):
        self.tabs.setCurrentWidget(self.tab_execution)
        self.tab_execution.run_solver()

    def _build_river_mesh(self):
        self.tabs.setCurrentWidget(self.tab_execution)
        self.tab_execution.run_river_mesh()

    def show_gui_guide(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('OFSolvers GUI guide')
        dialog.resize(920, 760)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        root = Path(__file__).resolve().parent
        browser.document().setBaseUrl(QUrl.fromLocalFile(str(root)+'/'))
        browser.setMarkdown((root/'GUI_README.md').read_text(encoding='utf-8'))
        layout.addWidget(browser)
        dialog.exec()

    def _apply_style(self):
        self.setStyleSheet('''
            QMainWindow { background: #f1f5f9; }
            QToolBar { background: #ffffff; border-bottom: 1px solid #cbd5e1; spacing: 12px; padding: 8px; }
            QGroupBox { font-weight: 600; border: 1px solid #cbd5e1; border-radius: 5px; margin-top: 14px; padding-top: 14px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton { padding: 6px 10px; }
            QLabel#stageTitle { font-size: 21px; font-weight: 600; color: #0f172a; }
            QLabel#diagnosticSummary { background: #e6f4f1; color: #134e4a; padding: 10px; border-radius: 5px; }
            QTreeWidget#workflowNavigation { background: #142d3a; color: #e2e8f0; border: none; font-size: 12px; }
            QTreeWidget#workflowNavigation::item { padding: 10px 5px; }
            QTreeWidget#workflowNavigation::item:selected { background: #087f8c; color: white; }
            QTreeWidget#workflowNavigation::item:disabled { color: #8b9eae; }
        ''')

    def setup_conceptual_tab(self):
        main_h_layout = QHBoxLayout(self.tab_concept)

        left_widget = QWidget()
        layout = QVBoxLayout(left_widget)

        # --- Simulation Type Selection ---
        sim_group = QGroupBox("Simulation Type")
        sim_layout = QVBoxLayout()
        self.sim_combo = QComboBox()
        self.sim_combo.addItems(["laminar", "RAS", "LES"])
        sim_layout.addWidget(self.sim_combo)
        sim_group.setLayout(sim_layout)
        layout.addWidget(sim_group)

        # --- Solver Selection ---
        solver_group = QGroupBox("Solver Selection")
        solver_layout = QFormLayout()
        self.solver_combo = QComboBox()
        self.solver_combo.addItems(SOLVERS)
        solver_layout.addRow("Solver:", self.solver_combo)
        solver_group.setLayout(solver_layout)
        layout.addWidget(solver_group)

        # --- Domain Extent ---
        extent_group = QGroupBox("Domain Extent (Meters)")
        extent_layout = QHBoxLayout()
        self.dim_x_spinbox = QDoubleSpinBox()
        self.dim_x_spinbox.setDecimals(6)
        self.dim_x_spinbox.setRange(1e-6, 1e9)
        self.dim_y_spinbox = QDoubleSpinBox()
        self.dim_y_spinbox.setDecimals(6)
        self.dim_y_spinbox.setRange(1e-6, 1e9)
        self.dim_z_spinbox = QDoubleSpinBox()
        self.dim_z_spinbox.setDecimals(6)
        self.dim_z_spinbox.setRange(1e-6, 1e9)

        extent_layout.addWidget(QLabel("X:"))
        extent_layout.addWidget(self.dim_x_spinbox)
        extent_layout.addWidget(QLabel("Y:"))
        extent_layout.addWidget(self.dim_y_spinbox)
        extent_layout.addWidget(QLabel("Z:"))
        extent_layout.addWidget(self.dim_z_spinbox)
        extent_group.setLayout(extent_layout)
        layout.addWidget(extent_group)

        # --- Domain Resolution ---
        res_group = QGroupBox("Domain Resolution (Cell Count)")
        res_layout = QHBoxLayout()
        self.cells_x_spinbox = QSpinBox()
        self.cells_x_spinbox.setRange(1, 10000)
        self.cells_y_spinbox = QSpinBox()
        self.cells_y_spinbox.setRange(1, 10000)
        self.cells_z_spinbox = QSpinBox()
        self.cells_z_spinbox.setRange(1, 10000)

        res_layout.addWidget(QLabel("Nx:"))
        res_layout.addWidget(self.cells_x_spinbox)
        res_layout.addWidget(QLabel("Ny:"))
        res_layout.addWidget(self.cells_y_spinbox)
        res_layout.addWidget(QLabel("Nz:"))
        res_layout.addWidget(self.cells_z_spinbox)
        res_group.setLayout(res_layout)
        layout.addWidget(res_group)

        # --- Boundary Conceptualization ---
        boundary_group = QGroupBox("Boundary Conceptualization")
        boundary_layout = QGridLayout()
        self.boundary_combos = {}
        boundary_types = ["Wall", "Inlet", "Outlet", "Atmosphere/Open"]

        faces = ["Min X", "Max X", "Min Y", "Max Y", "Min Z", "Max Z"]
        for i, face in enumerate(faces):
            combo = QComboBox()
            combo.addItems(boundary_types)
            self.boundary_combos[face] = combo
            boundary_layout.addWidget(QLabel(f"{face}:"), i // 2, (i % 2) * 2)
            boundary_layout.addWidget(combo, i // 2, (i % 2) * 2 + 1)

        self.advice_label = QLabel(
            "💡 Advice: Define 'Inlet' and 'Outlet' for flow direction. Use 'Wall' for solid boundaries."
        )
        self.advice_label.setWordWrap(True)
        boundary_layout.addWidget(self.advice_label, len(faces) // 2, 0, 1, 4)
        boundary_group.setLayout(boundary_layout)
        layout.addWidget(boundary_group)

        # --- Simulation Period ---
        time_group = QGroupBox("Simulation Period (Seconds)")
        time_layout = QFormLayout()

        self.start_time_spinbox = QDoubleSpinBox()
        self.start_time_spinbox.setDecimals(6)
        self.start_time_spinbox.setRange(0.0, 1e9)

        self.end_time_spinbox = QDoubleSpinBox()
        self.end_time_spinbox.setDecimals(6)
        self.end_time_spinbox.setRange(1e-6, 1e9)

        self.delta_t_spinbox = QDoubleSpinBox()
        self.delta_t_spinbox.setDecimals(8)
        self.delta_t_spinbox.setRange(1e-8, 1000.0)

        time_layout.addRow("Start Time:", self.start_time_spinbox)
        time_layout.addRow("End Time:", self.end_time_spinbox)
        time_layout.addRow("Delta T:", self.delta_t_spinbox)
        time_group.setLayout(time_layout)
        layout.addWidget(time_group)

        # Push everything to the top
        layout.addStretch()

        left_scroll = QScrollArea(); left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left_widget)
        main_h_layout.addWidget(left_scroll)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_scroll = QScrollArea(); right_scroll.setWidgetResizable(True)
        right_scroll.setWidget(right_widget)
        main_h_layout.addWidget(right_scroll)

        # --- Engineering Planning Assistant (Right Side) ---
        self.setup_courant_estimator(right_layout)
        self.setup_froude_calculator(right_layout)
        self.setup_mesh_estimator(right_layout)
        right_layout.addStretch()

    def setup_courant_estimator(self, parent_layout):
        group = QGroupBox("1. Courant Number & Time Step Estimator")
        layout = QFormLayout()

        self.max_vel_spinbox = QDoubleSpinBox()
        self.max_vel_spinbox.setRange(0.001, 1000.0)
        self.max_vel_spinbox.setValue(1.0)

        self.min_cell_spinbox = QDoubleSpinBox()
        self.min_cell_spinbox.setRange(0.0001, 1000.0)
        self.min_cell_spinbox.setDecimals(4)
        self.min_cell_spinbox.setValue(0.1)

        self.target_co_spinbox = QDoubleSpinBox()
        self.target_co_spinbox.setRange(0.1, 100.0)
        self.target_co_spinbox.setValue(0.9)

        self.dt_result_label = QLabel("Max \u0394t: - s")
        self.dt_result_label.setStyleSheet("font-weight: bold;")

        self.apply_dt_btn = QPushButton('Use estimated time step')
        self.apply_dt_btn.setToolTip('Updates the time-step input; Save plan writes it to controlDict.')

        layout.addRow("Max Expected Vel (m/s):", self.max_vel_spinbox)
        layout.addRow("Target Min Cell Size (m):", self.min_cell_spinbox)
        layout.addRow("Target Max Courant:", self.target_co_spinbox)
        layout.addRow(self.dt_result_label, self.apply_dt_btn)

        group.setLayout(layout)
        parent_layout.addWidget(group)

        self.max_vel_spinbox.valueChanged.connect(self.update_courant)
        self.min_cell_spinbox.valueChanged.connect(self.update_courant)
        self.target_co_spinbox.valueChanged.connect(self.update_courant)
        self.apply_dt_btn.clicked.connect(self.apply_courant_dt)

        self.update_courant()

    def setup_froude_calculator(self, parent_layout):
        group = QGroupBox("2. Flow Regime & Froude Number")
        layout = QFormLayout()

        self.avg_vel_spinbox = QDoubleSpinBox()
        self.avg_vel_spinbox.setRange(0.0, 1000.0)
        self.avg_vel_spinbox.setValue(1.0)

        self.avg_depth_spinbox = QDoubleSpinBox()
        self.avg_depth_spinbox.setRange(0.001, 1000.0)
        self.avg_depth_spinbox.setValue(1.0)

        self.fr_result_label = QLabel("Fr: - (Flow Regime)")
        self.fr_result_label.setStyleSheet("font-weight: bold;")

        layout.addRow("Avg Channel Vel (m/s):", self.avg_vel_spinbox)
        layout.addRow("Avg Flow Depth (m):", self.avg_depth_spinbox)
        layout.addRow(self.fr_result_label)

        group.setLayout(layout)
        parent_layout.addWidget(group)

        self.avg_vel_spinbox.valueChanged.connect(self.update_froude)
        self.avg_depth_spinbox.valueChanged.connect(self.update_froude)

        self.update_froude()

    def setup_mesh_estimator(self, parent_layout):
        group = QGroupBox("3. Mesh Footprint Estimator")
        layout = QFormLayout()

        self.avg_cell_size_spinbox = QDoubleSpinBox()
        self.avg_cell_size_spinbox.setRange(0.001, 1000.0)
        self.avg_cell_size_spinbox.setDecimals(3)
        self.avg_cell_size_spinbox.setValue(0.5)

        self.mesh_est_label = QLabel("Est. Cells: -")
        self.mesh_est_label.setStyleSheet("font-weight: bold;")
        self.mesh_est_label.setWordWrap(True)

        layout.addRow("Avg Global Cell Size (m):", self.avg_cell_size_spinbox)
        layout.addRow(self.mesh_est_label)

        group.setLayout(layout)
        parent_layout.addWidget(group)

        self.avg_cell_size_spinbox.valueChanged.connect(self.update_mesh_estimate)
        self.dim_x_spinbox.valueChanged.connect(self.update_mesh_estimate)
        self.dim_y_spinbox.valueChanged.connect(self.update_mesh_estimate)
        self.dim_z_spinbox.valueChanged.connect(self.update_mesh_estimate)

        self.update_mesh_estimate()

    def update_courant(self):
        v = self.max_vel_spinbox.value()
        dx = self.min_cell_spinbox.value()
        co = self.target_co_spinbox.value()
        if v > 0:
            dt = (co * dx) / v
            self.calculated_dt = dt
            self.dt_result_label.setText(f"Max \u0394t: {dt:.5f} s")
        else:
            self.dt_result_label.setText("Max \u0394t: N/A")

    def apply_courant_dt(self):
        if hasattr(self, "calculated_dt"):
            self.delta_t_spinbox.setValue(self.calculated_dt)

    def update_froude(self):
        v = self.avg_vel_spinbox.value()
        d = self.avg_depth_spinbox.value()
        g = 9.81
        if d > 0:
            fr = v / math.sqrt(g * d)
            if fr < 0.95:
                regime = "Subcritical"
                color = "green"
            elif fr > 1.05:
                regime = "Supercritical"
                color = "red"
            else:
                regime = "Critical"
                color = "orange"
            self.fr_result_label.setText(f"Fr: {fr:.2f} ({regime})")
            self.fr_result_label.setStyleSheet(f"font-weight: bold; color: {color};")
        else:
            self.fr_result_label.setText("Fr: N/A")
            self.fr_result_label.setStyleSheet("font-weight: bold;")

    def update_mesh_estimate(self):
        dx = self.dim_x_spinbox.value()
        dy = self.dim_y_spinbox.value()
        dz = self.dim_z_spinbox.value()
        cell_size = self.avg_cell_size_spinbox.value()

        if cell_size > 0:
            domain_vol = dx * dy * dz
            cell_vol = cell_size**3
            est_cells = domain_vol / cell_vol

            if est_cells > 5000000:
                self.mesh_est_label.setText(
                    f"Est. Cells: {est_cells:,.0f}\n\u26a0\ufe0f Warning: Heavy computational load (>5M cells)!"
                )
                self.mesh_est_label.setStyleSheet("font-weight: bold; color: red;")
            else:
                self.mesh_est_label.setText(f"Est. Cells: {est_cells:,.0f}")
                self.mesh_est_label.setStyleSheet("font-weight: bold; color: green;")
        else:
            self.mesh_est_label.setText("Est. Cells: N/A")
            self.mesh_est_label.setStyleSheet("font-weight: bold;")

    def _sync_execution_solver(self, name):
        if self.solver_combo.findText(name) < 0:
            self.solver_combo.addItem(name)
        self.solver_combo.setCurrentText(name)

    def browse_case_directory(self):
        path = QFileDialog.getExistingDirectory(self, 'Select OpenFOAM case folder', self.case_dir)
        if path:
            self.set_case_directory(path)

    def set_case_directory(self, path):
        if self.tab_execution.worker is not None:
            return
        folder = Path(path).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError('Case folder does not exist.')
        self.case_dir = str(folder)
        self.case_dir_edit.setText(self.case_dir)
        for tab in (self.tab_geometry, self.tab_mesh, self.tab_boundaries, self.tab_turbulence,
                    self.tab_execution, self.tab_terrain, self.tab_sediment, self.tab_diagnostics):
            tab.set_case_directory(self.case_dir)
        self.case_directory_changed.emit(self.case_dir)
        if (folder/'river-design.json').is_file():
            try:
                from river_case import load_river_case
                river,design=load_river_case(folder)
                if river.get('boundaries_ready'):
                    self.sim_combo.setCurrentText('RAS')
                    self.tab_turbulence.model_combo.setCurrentText(design.hydraulic.turbulence)
            except (OSError,ValueError,KeyError,TypeError):pass
        self.case_hint.setText('Active case: '+folder.name+' · Use Run diagnostics to review logs; open saved flow/parcel fields in ParaView.')
        control = folder/'system/controlDict'
        if control.exists():
            try:
                text = control.read_text()
                self._sync_execution_solver(value(text, 'application'))
                for name, spin in [('startTime', self.start_time_spinbox),
                                   ('endTime', self.end_time_spinbox), ('deltaT', self.delta_t_spinbox)]:
                    spin.setValue(scalar(text, name))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, 'Case controls', f'Could not load all time controls: {exc}')

    def _on_starter_prepared(self, path, solver):
        self.set_case_directory(path)
        self._sync_execution_solver(solver)

    def set_busy(self, running):
        self.case_browse_button.setEnabled(not running)
        self.run_button.setEnabled(not running)
        self.open_action.setEnabled(not running)
        self.save_action.setEnabled(not running)
        self.check_action.setEnabled(not running)
        self.solve_action.setEnabled(not running)
        for i in range(self.tabs.count()):
            enabled = not running or self.tabs.widget(i) in (self.tab_execution, self.tab_diagnostics)
            self.tabs.setTabEnabled(i, enabled)
            self._navigation_items[i].setDisabled(not enabled)

    def closeEvent(self, event):
        reader = self.tab_diagnostics.worker
        if reader is not None and reader.isRunning():
            reader.requestInterruption()
            if not reader.wait(2000):
                event.ignore()
                return
        worker = self.tab_execution.worker
        if worker is not None and worker.isRunning():
            worker.stop()
            if not worker.wait(3000):
                event.ignore()
                return
        self.tab_mesh.plotter.close()
        self.tab_geometry.river_design.plotter.close()
        self.tab_diagnostics.timer.stop()
        event.accept()
