from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QDoubleSpinBox, QSpinBox, QPushButton, QTabWidget, QComboBox, QGroupBox, QFormLayout, QGridLayout)

from mesh_tab import MeshTab
from boundary_tab import BoundaryTab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OpenFOAM GUI")
        self.resize(500, 550)

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

        # Tab 2: Mesh Generation (Placeholder for later)
        self.tab_mesh = MeshTab()
        self.tabs.addTab(self.tab_mesh, "2. Mesh Generation")

        # Tab 3: Boundary Conditions (Specific details based on Tab 1)
        self.tab_boundaries = BoundaryTab()
        self.tab_boundaries.set_mesh_tab(self.tab_mesh)
        self.tab_mesh.mesh_updated.connect(self.tab_boundaries.import_patches)
        self.tabs.addTab(self.tab_boundaries, "3. Boundary Conditions")

        # Global Run/Save Button
        self.run_button = QPushButton("Save Conceptual Model & Run Test")
        layout.addWidget(self.run_button)

    def setup_conceptual_tab(self):
        layout = QVBoxLayout(self.tab_concept)

        # --- Solver Selection ---
        solver_group = QGroupBox("Solver Selection")
        solver_layout = QFormLayout()
        self.solver_combo = QComboBox()
        self.solver_combo.addItems([
            "interFoam",
            "multiphaseEulerFoam",
            "buoyantBoussinesqPimpleFoam",
            "pimpleFoam",
            "simpleFoam"
        ])
        solver_layout.addRow("Solver:", self.solver_combo)
        solver_group.setLayout(solver_layout)
        layout.addWidget(solver_group)

        # --- Domain Extent ---
        extent_group = QGroupBox("Domain Extent (Meters)")
        extent_layout = QHBoxLayout()
        self.dim_x_spinbox = QDoubleSpinBox(); self.dim_x_spinbox.setRange(0.1, 100000.0)
        self.dim_y_spinbox = QDoubleSpinBox(); self.dim_y_spinbox.setRange(0.1, 100000.0)
        self.dim_z_spinbox = QDoubleSpinBox(); self.dim_z_spinbox.setRange(0.1, 100000.0)

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
        self.cells_x_spinbox = QSpinBox(); self.cells_x_spinbox.setRange(1, 10000)
        self.cells_y_spinbox = QSpinBox(); self.cells_y_spinbox.setRange(1, 10000)
        self.cells_z_spinbox = QSpinBox(); self.cells_z_spinbox.setRange(1, 10000)

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

        self.advice_label = QLabel("💡 Advice: Define 'Inlet' and 'Outlet' for flow direction. Use 'Wall' for solid boundaries.")
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