import os
import pyvista as pv
from pyvistaqt import QtInteractor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
                             QGroupBox, QLabel, QDoubleSpinBox, QSpinBox,
                             QPushButton, QFileDialog, QLineEdit, QTableWidget,
                             QComboBox, QCheckBox, QFrame, QHeaderView, QScrollArea, QGridLayout, QMessageBox)
from PyQt6.QtCore import Qt

class MeshTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        # Main layout using a Splitter to separate controls from the 3D view
        main_layout = QHBoxLayout(self)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        # ---------------------------------------------------------
        # LEFT SIDE: Controls Panel (wrapped in a Scroll Area)
        # ---------------------------------------------------------
        self.controls_scroll = QScrollArea()
        self.controls_scroll.setWidgetResizable(True)
        self.controls_widget = QWidget()
        self.controls_layout = QVBoxLayout(self.controls_widget)

        self.setup_blockmesh_section()
        self.setup_snappyhexmesh_section()
        self.setup_diagnostics_section()

        self.controls_layout.addStretch()  # Push everything up
        self.controls_scroll.setWidget(self.controls_widget)

        self.splitter.addWidget(self.controls_scroll)

        # ---------------------------------------------------------
        # RIGHT SIDE: 3D Visualization Placeholder
        # ---------------------------------------------------------
        self.vis_frame = QFrame()
        self.vis_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.vis_frame.setStyleSheet("background-color: #2b2b2b")
        vis_layout = QVBoxLayout(self.vis_frame)

        self.plotter = QtInteractor(self.vis_frame)
        vis_layout.addWidget(self.plotter.interactor)

        self.plotter.show_axes()
        self.plotter.show_grid()
        self.plotter.add_text("3D Visualization Window", font_size=12, color="white", position="upper_left")

        self.splitter.addWidget(self.vis_frame)

        self.current_mesh = None

        # Set initial splitter sizes (e.g., 40% controls, 60% visualization)
        self.splitter.setSizes([400, 600])

    def setup_blockmesh_section(self):
        """Sets up the blockMesh configuration group box."""
        group_box = QGroupBox("1. Base Mesh (blockMesh)")
        layout = QGridLayout()

        # Headers
        layout.addWidget(QLabel("Min Coord (m)"), 0, 1)
        layout.addWidget(QLabel("Max Coord (m)"), 0, 2)
        layout.addWidget(QLabel("Cells"), 0, 3)
        layout.addWidget(QLabel("Grading (Ratio)"), 0, 4)

        axes = ["X", "Y", "Z"]
        self.blockmesh_inputs = {}

        for i, axis in enumerate(axes):
            row = i + 1
            layout.addWidget(QLabel(f"{axis}-Axis:"), row, 0)

            # Min Coord
            min_spin = QDoubleSpinBox()
            min_spin.setRange(-100000.0, 100000.0)
            layout.addWidget(min_spin, row, 1)

            # Max Coord
            max_spin = QDoubleSpinBox()
            max_spin.setRange(-100000.0, 100000.0)
            layout.addWidget(max_spin, row, 2)

            # Cells
            cells_spin = QSpinBox()
            cells_spin.setRange(1, 10000)
            cells_spin.setValue(50)
            layout.addWidget(cells_spin, row, 3)

            # Grading
            grading_spin = QDoubleSpinBox()
            grading_spin.setRange(0.001, 1000.0)
            grading_spin.setValue(1.0)
            grading_spin.setDecimals(3)
            layout.addWidget(grading_spin, row, 4)

            self.blockmesh_inputs[axis] = {
                'min': min_spin, 'max': max_spin,
                'cells': cells_spin, 'grading': grading_spin
            }

        # Generate Button
        self.btn_vis_blockmesh = QPushButton("Visualize BlockMesh")
        self.btn_vis_blockmesh.clicked.connect(self.visualize_blockmesh)
        layout.addWidget(self.btn_vis_blockmesh, len(axes) + 1, 0, 1, 2)

        self.btn_gen_blockmesh = QPushButton("Generate blockMeshDict")
        self.btn_gen_blockmesh.clicked.connect(self.generate_blockMeshDict)
        layout.addWidget(self.btn_gen_blockmesh, len(axes) + 1, 2, 1, 3)

        group_box.setLayout(layout)
        self.controls_layout.addWidget(group_box)

    def visualize_blockmesh(self):
        """Visualizes the blockMesh box based on coordinates."""
        try:
            min_x = self.blockmesh_inputs["X"]["min"].value()
            max_x = self.blockmesh_inputs["X"]["max"].value()
            min_y = self.blockmesh_inputs["Y"]["min"].value()
            max_y = self.blockmesh_inputs["Y"]["max"].value()
            min_z = self.blockmesh_inputs["Z"]["min"].value()
            max_z = self.blockmesh_inputs["Z"]["max"].value()

            # Create block bounds
            box = pv.Box(bounds=(min_x, max_x, min_y, max_y, min_z, max_z))

            self.plotter.clear()
            self.plotter.add_mesh(box, show_edges=True, opacity=0.5, color="lightblue")
            self.plotter.show_axes()
            self.plotter.show_grid()
            self.plotter.reset_camera()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not visualize blockMesh: {str(e)}")

    def setup_diagnostics_section(self):
        """Sets up the Mesh Diagnostics & Fixes group box."""
        group_box = QGroupBox("3. Mesh Diagnostics & Fixes")
        layout = QVBoxLayout()

        self.diagnostics_label = QLabel("No geometry loaded. Load an STL/OBJ to view diagnostics.")
        self.diagnostics_label.setWordWrap(True)
        self.diagnostics_label.setStyleSheet("color: #aaaaaa;")
        layout.addWidget(self.diagnostics_label)

        btn_layout = QHBoxLayout()

        self.btn_run_diagnostics = QPushButton("Run Diagnostics")
        self.btn_run_diagnostics.clicked.connect(self.run_diagnostics)
        btn_layout.addWidget(self.btn_run_diagnostics)

        self.btn_fix_mesh = QPushButton("Clean & Fix Mesh")
        self.btn_fix_mesh.clicked.connect(self.clean_and_fix_mesh)
        btn_layout.addWidget(self.btn_fix_mesh)

        layout.addLayout(btn_layout)
        group_box.setLayout(layout)
        self.controls_layout.addWidget(group_box)

    def run_diagnostics(self):
        """Computes properties on the currently loaded mesh."""
        if self.current_mesh is None:
            QMessageBox.warning(self, "Warning", "No mesh loaded!")
            return

        try:
            mesh = self.current_mesh
            points = mesh.n_points
            cells = mesh.n_cells
            volume = mesh.volume if hasattr(mesh, 'volume') else 'N/A'
            manifold = mesh.is_manifold if hasattr(mesh, 'is_manifold') else 'N/A'

            diag_text = (f"<b>Points:</b> {points}<br>"
                         f"<b>Cells:</b> {cells}<br>"
                         f"<b>Volume:</b> {volume}<br>"
                         f"<b>Is Manifold (Watertight):</b> {manifold}")
            self.diagnostics_label.setText(diag_text)
        except Exception as e:
            self.diagnostics_label.setText(f"Error running diagnostics: {str(e)}")

    def clean_and_fix_mesh(self):
        """Applies PyVista cleaning and triangulating to the current mesh."""
        if self.current_mesh is None:
            QMessageBox.warning(self, "Warning", "No mesh loaded to fix!")
            return

        try:
            mesh = self.current_mesh.clean()
            if hasattr(mesh, 'triangulate'):
                mesh = mesh.triangulate()
            if hasattr(mesh, 'compute_normals'):
                mesh = mesh.compute_normals(consistent_normals=True)

            self.current_mesh = mesh

            # Re-render
            self.plotter.clear()
            self.plotter.add_mesh(self.current_mesh, show_edges=True, color="lightgreen")
            self.plotter.show_axes()
            self.plotter.show_grid()
            self.plotter.reset_camera()

            QMessageBox.information(self, "Success", "Mesh cleaned and fixed!")
            self.run_diagnostics() # Update stats
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to fix mesh: {str(e)}")

    def setup_snappyhexmesh_section(self):
        """Sets up the snappyHexMesh configuration group box."""
        group_box = QGroupBox("2. Geometry Loading & snappyHexMesh")
        layout = QVBoxLayout()

        # --- Geometry Loading ---
        geom_layout = QHBoxLayout()
        self.geom_path_edit = QLineEdit()
        self.geom_path_edit.setPlaceholderText("Path to .stl or .obj file...")
        self.geom_path_edit.setReadOnly(True)

        btn_browse = QPushButton("Load Geometry")
        btn_browse.clicked.connect(self.browse_geometry)

        geom_layout.addWidget(self.geom_path_edit)
        geom_layout.addWidget(btn_browse)
        layout.addLayout(geom_layout)

        # --- Castellated Mesh Controls ---
        castellated_layout = QGridLayout()
        castellated_layout.addWidget(QLabel("maxLocalCells:"), 0, 0)
        self.max_local_cells = QSpinBox()
        self.max_local_cells.setRange(1000, 100000000)
        self.max_local_cells.setValue(1000000)
        self.max_local_cells.setSingleStep(100000)
        castellated_layout.addWidget(self.max_local_cells, 0, 1)

        castellated_layout.addWidget(QLabel("maxGlobalCells:"), 0, 2)
        self.max_global_cells = QSpinBox()
        self.max_global_cells.setRange(1000, 100000000)
        self.max_global_cells.setValue(2000000)
        self.max_global_cells.setSingleStep(100000)
        castellated_layout.addWidget(self.max_global_cells, 0, 3)
        layout.addLayout(castellated_layout)

        # --- Refinement Regions/Surfaces (Dynamic Table) ---
        layout.addWidget(QLabel("<b>Refinement Regions/Surfaces:</b>"))
        self.refinement_table = QTableWidget(0, 4)
        self.refinement_table.setHorizontalHeaderLabels(["Geometry Name", "Min Level", "Max Level", "Type"])
        self.refinement_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.refinement_table)

        btn_layout = QHBoxLayout()
        btn_add_refinement = QPushButton("+ Add Refinement")
        btn_add_refinement.clicked.connect(self.add_refinement_row)
        btn_remove_refinement = QPushButton("- Remove Selected")
        btn_remove_refinement.clicked.connect(self.remove_refinement_row)
        btn_layout.addWidget(btn_add_refinement)
        btn_layout.addWidget(btn_remove_refinement)
        layout.addLayout(btn_layout)

        # --- Snap Controls ---
        snap_layout = QHBoxLayout()
        self.enable_snap_cb = QCheckBox("Enable Snapping (snap true;)")
        self.enable_snap_cb.setChecked(True)

        snap_layout.addWidget(self.enable_snap_cb)
        snap_layout.addStretch()

        snap_layout.addWidget(QLabel("explicitFeatureSnap:"))
        self.explicit_feature_snap = QSpinBox()
        self.explicit_feature_snap.setRange(0, 100)
        self.explicit_feature_snap.setValue(10)
        snap_layout.addWidget(self.explicit_feature_snap)
        layout.addLayout(snap_layout)

        # Generate Button
        self.btn_gen_snappy = QPushButton("Generate snappyHexMeshDict")
        self.btn_gen_snappy.clicked.connect(self.generate_snappyHexMeshDict)
        layout.addWidget(self.btn_gen_snappy)

        group_box.setLayout(layout)
        self.controls_layout.addWidget(group_box)

    def update_from_conceptual_model(self, dim_x, dim_y, dim_z, cells_x, cells_y, cells_z):
        """
        Utility method to link Tab 1 conceptual extents to Tab 2 base mesh generation.
        Can be called by the main window controller when switching to this tab.
        """
        self.blockmesh_inputs["X"]["min"].setValue(-dim_x / 2.0)
        self.blockmesh_inputs["X"]["max"].setValue(dim_x / 2.0)
        self.blockmesh_inputs["X"]["cells"].setValue(cells_x)

        self.blockmesh_inputs["Y"]["min"].setValue(-dim_y / 2.0)
        self.blockmesh_inputs["Y"]["max"].setValue(dim_y / 2.0)
        self.blockmesh_inputs["Y"]["cells"].setValue(cells_y)

        self.blockmesh_inputs["Z"]["min"].setValue(0.0)
        self.blockmesh_inputs["Z"]["max"].setValue(dim_z)
        self.blockmesh_inputs["Z"]["cells"].setValue(cells_z)

    def browse_geometry(self):
        """Opens a file dialog to select a base geometry and visualizes it."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Geometry", "", "Geometry Files (*.stl *.obj);;All Files (*)"
        )
        if file_path:
            self.geom_path_edit.setText(file_path)
            # Automatically add the file as a default surface refinement region
            geom_name = os.path.splitext(os.path.basename(file_path))[0]
            self.add_refinement_row(default_name=geom_name)

            # Load and visualize the geometry
            try:
                self.current_mesh = pv.read(file_path)
                self.plotter.clear()
                self.plotter.add_mesh(self.current_mesh, show_edges=True, color="w")
                self.plotter.show_axes()
                self.plotter.show_grid()
                self.plotter.reset_camera()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to load geometry: {str(e)}")

    def add_refinement_row(self, default_name=""):
        """Dynamically adds a row to the refinement configuration table."""
        row = self.refinement_table.rowCount()
        self.refinement_table.insertRow(row)

        # Geometry Name
        name_edit = QLineEdit(default_name)
        self.refinement_table.setCellWidget(row, 0, name_edit)

        # Min Level
        min_spin = QSpinBox()
        min_spin.setRange(0, 8)
        min_spin.setValue(2)
        self.refinement_table.setCellWidget(row, 1, min_spin)

        # Max Level
        max_spin = QSpinBox()
        max_spin.setRange(0, 8)
        max_spin.setValue(3)
        self.refinement_table.setCellWidget(row, 2, max_spin)

        # Type
        type_combo = QComboBox()
        type_combo.addItems(["surface", "inside", "outside"])
        self.refinement_table.setCellWidget(row, 3, type_combo)

    def remove_refinement_row(self):
        """Removes the currently selected row from the refinement table."""
        current_row = self.refinement_table.currentRow()
        if current_row >= 0:
            self.refinement_table.removeRow(current_row)

    def generate_blockMeshDict(self):
        """
        Placeholder for generating blockMeshDict.
        Demonstrates how the values are extracted and passed to the custom DictParser.
        """
        print("Generating blockMeshDict...")

        # Example extraction:
        # min_x = self.blockmesh_inputs["X"]["min"].value()
        # max_x = self.blockmesh_inputs["X"]["max"].value()
        # cells_x = self.blockmesh_inputs["X"]["cells"].value()
        # grading_x = self.blockmesh_inputs["X"]["grading"].value()

        # Example usage of the dictionary parser (pseudo-code):
        # parser = OpenFoamDictParser("C:/.../v2206/documents/blockMeshDict.template")
        #
        # # Build the vertices block
        # parser.set_value("vertices", [
        #     f"({min_x} {min_y} {min_z})", f"({max_x} {min_y} {min_z})", ...
        # ])
        #
        # # Build the blocks -> hex mapping
        # parser.set_value("blocks", [
        #     f"hex (0 1 2 3 4 5 6 7) ({cells_x} {cells_y} {cells_z}) simpleGrading ({grading_x} {grading_y} {grading_z})"
        # ])
        #
        # parser.write("system/blockMeshDict")
        print("blockMeshDict generated successfully! (Placeholder)")

    def generate_snappyHexMeshDict(self):
        """
        Placeholder for generating snappyHexMeshDict.
        Demonstrates compiling snappyHexMesh parameters to write to template.
        """
        print("Generating snappyHexMeshDict...")

        # Example extraction:
        # parser = OpenFoamDictParser("C:/.../v2206/documents/snappyHexMeshDict.template")
        #
        # parser.set_value("castellatedMeshControls.maxLocalCells", self.max_local_cells.value())
        # parser.set_value("castellatedMeshControls.maxGlobalCells", self.max_global_cells.value())
        #
        # # Loop through refinement table to construct refinementSurfaces / refinementRegions
        # for row in range(self.refinement_table.rowCount()):
        #     geom_name = self.refinement_table.cellWidget(row, 0).text()
        #     min_lvl = self.refinement_table.cellWidget(row, 1).value()
        #     ... construct dictionary blocks based on Type ...
        #
        # parser.set_value("snap", "true" if self.enable_snap_cb.isChecked() else "false")
        # parser.write("system/snappyHexMeshDict")
        print("snappyHexMeshDict generated successfully! (Placeholder)")