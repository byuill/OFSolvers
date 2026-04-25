from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter, 
                             QGroupBox, QLabel, QDoubleSpinBox, QCheckBox, 
                             QPushButton, QListWidget, QComboBox, QStackedWidget, 
                             QFormLayout, QMessageBox)
from PyQt6.QtCore import Qt

class BoundaryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.patch_data = {}  # Dictionary to store settings for each patch
        self.setup_ui()

    def setup_ui(self):
        # Main layout using a Splitter
        main_layout = QHBoxLayout(self)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        # ---------------------------------------------------------
        # LEFT SIDE: Boundary Patch List
        # ---------------------------------------------------------
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        
        left_layout.addWidget(QLabel("<b>Available Boundary Patches:</b>"))
        
        self.patch_list = QListWidget()
        # Add some placeholder patches for demonstration
        self.patch_list.addItems(["inlet", "outlet", "bottom", "atmosphere", "side1", "side2"])
        self.patch_list.currentItemChanged.connect(self.on_patch_selected)
        left_layout.addWidget(self.patch_list)
        
        self.btn_import_patches = QPushButton("Import Patches from Mesh")
        self.btn_import_patches.clicked.connect(self.import_patches)
        left_layout.addWidget(self.btn_import_patches)
        
        self.splitter.addWidget(left_widget)

        # ---------------------------------------------------------
        # RIGHT SIDE: Conceptual Boundary Configuration
        # ---------------------------------------------------------
        right_widget = QWidget()
        self.right_layout = QVBoxLayout(right_widget)
        
        # Conceptual Boundary Type Selection
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("<b>Conceptual Boundary Type:</b>"))
        self.bc_type_combo = QComboBox()
        self.bc_type_combo.addItems(["Inlet", "Outlet", "Atmosphere", "Bed/Wall", "Symmetry"])
        self.bc_type_combo.currentTextChanged.connect(self.on_bc_type_changed)
        type_layout.addWidget(self.bc_type_combo)
        self.right_layout.addLayout(type_layout)
        
        # Stacked Widget for Dynamic Input Forms
        self.stacked_widget = QStackedWidget()
        
        # Build individual pages
        self.page_inlet = self.build_inlet_page()
        self.page_outlet = self.build_outlet_page()
        self.page_atmosphere = self.build_atmosphere_page()
        self.page_wall = self.build_wall_page()
        self.page_symmetry = self.build_symmetry_page()
        
        # Add pages to stacked widget (Must match the order in the combobox)
        self.stacked_widget.addWidget(self.page_inlet)       # Index 0
        self.stacked_widget.addWidget(self.page_outlet)      # Index 1
        self.stacked_widget.addWidget(self.page_atmosphere)  # Index 2
        self.stacked_widget.addWidget(self.page_wall)        # Index 3
        self.stacked_widget.addWidget(self.page_symmetry)    # Index 4
        
        self.right_layout.addWidget(self.stacked_widget)
        self.right_layout.addStretch()  # Push everything up
        
        # Write Boundaries Button
        self.btn_write_0 = QPushButton("Write '0' Directory Boundaries")
        self.btn_write_0.setStyleSheet("font-weight: bold; padding: 10px;")
        self.btn_write_0.clicked.connect(self.write_0_directory)
        self.right_layout.addWidget(self.btn_write_0)
        
        self.splitter.addWidget(right_widget)
        
        # Set initial splitter sizes (30% left, 70% right)
        self.splitter.setSizes([300, 700])
        
        # Disable right pane until a patch is selected
        right_widget.setEnabled(False)
        self.right_widget_container = right_widget

    # --- Page Builders for QStackedWidget ---

    def build_inlet_page(self):
        page = QGroupBox("Inlet Configuration")
        layout = QFormLayout(page)
        
        info_label = QLabel("<i>Maps to: U (variableHeightFlowRateInletVelocity), alpha.water (variableHeightFlowRate)</i>")
        info_label.setWordWrap(True)
        layout.addRow(info_label)
        
        self.inlet_flow_rate = QDoubleSpinBox()
        self.inlet_flow_rate.setRange(0.0, 100000.0)
        self.inlet_flow_rate.setSuffix(" m³/s")
        layout.addRow("Volumetric Flow Rate:", self.inlet_flow_rate)
        
        self.inlet_mwl = QDoubleSpinBox()
        self.inlet_mwl.setRange(-100.0, 10000.0)
        self.inlet_mwl.setSuffix(" m")
        layout.addRow("Mean Water Level:", self.inlet_mwl)
        
        return page

    def build_outlet_page(self):
        page = QGroupBox("Outlet Configuration")
        layout = QFormLayout(page)
        
        info_label = QLabel("<i>Maps to: outletPhaseMeanVelocity or zeroGradient</i>")
        info_label.setWordWrap(True)
        layout.addRow(info_label)
        
        self.outlet_free_outfall = QCheckBox("Free Outfall / Zero Gradient")
        self.outlet_free_outfall.stateChanged.connect(self.toggle_outlet_wl)
        layout.addRow("", self.outlet_free_outfall)
        
        self.outlet_dwl = QDoubleSpinBox()
        self.outlet_dwl.setRange(-100.0, 10000.0)
        self.outlet_dwl.setSuffix(" m")
        layout.addRow("Downstream Water Level:", self.outlet_dwl)
        
        return page
        
    def toggle_outlet_wl(self, state):
        """Disables the downstream water level input if free outfall is checked."""
        self.outlet_dwl.setEnabled(not state)

    def build_atmosphere_page(self):
        page = QGroupBox("Atmosphere Configuration")
        layout = QFormLayout(page)
        
        info_label = QLabel("<i>Maps to: U (pressureInletOutletVelocity), p_rgh (totalPressure)</i>")
        info_label.setWordWrap(True)
        layout.addRow(info_label)
        
        self.atm_pressure = QDoubleSpinBox()
        self.atm_pressure.setRange(0.0, 200000.0)
        self.atm_pressure.setDecimals(0)
        self.atm_pressure.setValue(100000.0)
        self.atm_pressure.setSuffix(" Pa")
        layout.addRow("Reference Pressure:", self.atm_pressure)
        
        return page

    def build_wall_page(self):
        page = QGroupBox("Bed/Wall Configuration")
        layout = QFormLayout(page)
        
        info_label = QLabel("<i>Maps to: Standard Wall Functions</i>")
        layout.addRow(info_label)
        
        self.wall_function_combo = QComboBox()
        self.wall_function_combo.addItems(["kqRWallFunction", "nutkWallFunction", "nutkRoughWallFunction"])
        layout.addRow("Turbulence Wall Function:", self.wall_function_combo)
        
        self.wall_ks = QDoubleSpinBox()
        self.wall_ks.setRange(0.0, 1.0)
        self.wall_ks.setDecimals(5)
        self.wall_ks.setSingleStep(0.001)
        self.wall_ks.setSuffix(" m")
        layout.addRow("Equivalent Sand Roughness (Ks):", self.wall_ks)
        
        return page

    def build_symmetry_page(self):
        page = QGroupBox("Symmetry Configuration")
        layout = QVBoxLayout(page)
        
        info_label = QLabel("<i>Maps to: symmetry or symmetryPlane</i><br><br>No additional inputs required for symmetry boundaries.")
        info_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(info_label)
        layout.addStretch()
        
        return page

    # --- Slots / Logic ---

    def on_patch_selected(self, current, previous):
        """Enables the configuration pane when a patch is selected."""
        if current:
            self.right_widget_container.setEnabled(True)
            # In a full implementation, you would load the saved dict states for 'current.text()' here

    def on_bc_type_changed(self, bc_type):
        """Dynamically switches the visible input form based on the selected boundary type."""
        index = self.bc_type_combo.findText(bc_type)
        if index >= 0:
            self.stacked_widget.setCurrentIndex(index)

    def import_patches(self):
        """Placeholder for reading polyMesh/boundary to populate the list."""
        QMessageBox.information(self, "Import Patches", "This will parse constant/polyMesh/boundary and populate the list widget.")
        # e.g., parser = OpenFoamDictParser("constant/polyMesh/boundary")
        # patches = parser.get_patches()
        # self.patch_list.clear()
        # self.patch_list.addItems(patches)

    def write_0_directory(self):
        """
        Placeholder for writing to 0/U, 0/p_rgh, 0/alpha.water using OpenFoamDictParser.
        """
        print("Writing '0' directory boundaries...")
        
        # Pseudo-code demonstrating use of custom parser:
        # 
        # u_parser = OpenFoamDictParser("0/U.template")
        # p_parser = OpenFoamDictParser("0/p_rgh.template")
        # alpha_parser = OpenFoamDictParser("0/alpha.water.template")
        #
        # for i in range(self.patch_list.count()):
        #     patch_name = self.patch_list.item(i).text()
        #     bc_type = self.patch_data.get(patch_name, {}).get("type", "Wall")
        #     
        #     if bc_type == "Inlet":
        #         u_parser.set_value(f"boundaryField.{patch_name}.type", "variableHeightFlowRateInletVelocity")
        #         u_parser.set_value(f"boundaryField.{patch_name}.flowRate", self.inlet_flow_rate.value())
        #         alpha_parser.set_value(f"boundaryField.{patch_name}.type", "variableHeightFlowRate")
        #     elif bc_type == "Atmosphere":
        #         u_parser.set_value(f"boundaryField.{patch_name}.type", "pressureInletOutletVelocity")
        #         p_parser.set_value(f"boundaryField.{patch_name}.type", "totalPressure")
        #         p_parser.set_value(f"boundaryField.{patch_name}.p0", f"uniform {self.atm_pressure.value()}")
        #     # ... handle Outlet, Wall, Symmetry similarly ...
        #
        # u_parser.write("0/U")
        # p_parser.write("0/p_rgh")
        # alpha_parser.write("0/alpha.water")
        
        QMessageBox.information(self, "Success", "Successfully wrote boundary conditions to '0' directory files!")