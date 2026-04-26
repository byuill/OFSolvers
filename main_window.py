import math
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
)

from mesh_tab import MeshTab
from boundary_tab import BoundaryTab
from geometry_tab import GeometryTab
from terrain_tab import TerrainTab
from execution_tab import ExecutionTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OpenFOAM GUI")
        self.resize(900, 600)

        # Main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Tab Widget
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

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
        self.tab_mesh.mesh_updated.connect(self.tab_boundaries.import_patches)
        self.solver_combo.currentTextChanged.connect(
            self.tab_boundaries.set_solver_profile
        )
        self.tab_boundaries.set_solver_profile(self.solver_combo.currentText())
        self.tabs.addTab(self.tab_boundaries, "5. Boundary Conditions")

        # Tab 6: Execution & Monitoring
        self.tab_execution = ExecutionTab()
        self.tabs.addTab(self.tab_execution, "6. Execution & Monitoring")

        # Global Run/Save Button
        self.run_button = QPushButton("Save Conceptual Model & Run Test")
        layout.addWidget(self.run_button)

    def setup_conceptual_tab(self):
        main_h_layout = QHBoxLayout(self.tab_concept)

        left_widget = QWidget()
        layout = QVBoxLayout(left_widget)

        # --- Solver Selection ---
        solver_group = QGroupBox("Solver Selection")
        solver_layout = QFormLayout()
        self.solver_combo = QComboBox()
        self.solver_combo.addItems(
            [
                "interFoam",
                "multiphaseEulerFoam",
                "buoyantBoussinesqPimpleFoam",
                "pimpleFoam",
                "simpleFoam",
            ]
        )
        solver_layout.addRow("Solver:", self.solver_combo)
        solver_group.setLayout(solver_layout)
        layout.addWidget(solver_group)

        # --- Domain Extent ---
        extent_group = QGroupBox("Domain Extent (Meters)")
        extent_layout = QHBoxLayout()
        self.dim_x_spinbox = QDoubleSpinBox()
        self.dim_x_spinbox.setRange(0.1, 100000.0)
        self.dim_y_spinbox = QDoubleSpinBox()
        self.dim_y_spinbox.setRange(0.1, 100000.0)
        self.dim_z_spinbox = QDoubleSpinBox()
        self.dim_z_spinbox.setRange(0.1, 100000.0)

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
        self.start_time_spinbox.setRange(0.0, 100000.0)

        self.end_time_spinbox = QDoubleSpinBox()
        self.end_time_spinbox.setRange(0.1, 10000.0)

        self.delta_t_spinbox = QDoubleSpinBox()
        self.delta_t_spinbox.setRange(0.00001, 1.0)
        self.delta_t_spinbox.setDecimals(5)

        time_layout.addRow("Start Time:", self.start_time_spinbox)
        time_layout.addRow("End Time:", self.end_time_spinbox)
        time_layout.addRow("Delta T:", self.delta_t_spinbox)
        time_group.setLayout(time_layout)
        layout.addWidget(time_group)

        # Push everything to the top
        layout.addStretch()

        main_h_layout.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        main_h_layout.addWidget(right_widget)

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

        self.apply_dt_btn = QPushButton("Apply to controlDict")

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
