import os
from pathlib import Path
import tempfile
import pyvista as pv
from pyvistaqt import QtInteractor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QGroupBox,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QPushButton,
    QFileDialog,
    QLineEdit,
    QTableWidget,
    QComboBox,
    QCheckBox,
    QFrame,
    QHeaderView,
    QScrollArea,
    QGridLayout,
    QMessageBox,
    QRadioButton,
)
from PyQt6.QtCore import Qt

from PyQt6.QtCore import pyqtSignal
from foam_io import write_case_files
from mesh_config import block_mesh_dictionary, retained_point_valid, find_retained_point, snappy_dictionary


class MeshVolumeSelectorWidget(QGroupBox):
    """Sub-widget for defining snappyHexMesh locationInMesh."""

    location_changed = pyqtSignal(tuple)

    def __init__(self, parent=None):
        super().__init__("Mesh Volume Definition (locationInMesh)", parent)
        self._active_mesh = None
        self._blockmesh_bounds = None
        self._setup_ui()
        self._connect_signals()
        self._on_auto_calculate_toggled(self.auto_calc_checkbox.isChecked())

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 1) Logical mode selection.
        self.mode_inside_radio = QRadioButton(
            "Mesh INSIDE the surface (e.g., internal channel flow)"
        )
        self.mode_outside_radio = QRadioButton(
            "Mesh OUTSIDE the surface (e.g., flow around a structure / terrain)"
        )
        self.mode_inside_radio.setChecked(True)
        layout.addWidget(self.mode_inside_radio)
        layout.addWidget(self.mode_outside_radio)

        # 2) Point calculation method.
        self.auto_calc_checkbox = QCheckBox(
            "Find a verified point in the retained region"
        )
        self.auto_calc_checkbox.setChecked(True)
        layout.addWidget(self.auto_calc_checkbox)

        # 3) Manual coordinate override.
        manual_layout = QGridLayout()
        manual_layout.addWidget(QLabel("X:"), 0, 0)
        manual_layout.addWidget(QLabel("Y:"), 0, 2)
        manual_layout.addWidget(QLabel("Z:"), 0, 4)

        self.x_spin = QDoubleSpinBox()
        self.y_spin = QDoubleSpinBox()
        self.z_spin = QDoubleSpinBox()
        for spin in (self.x_spin, self.y_spin, self.z_spin):
            spin.setRange(-100000.0, 100000.0)
            spin.setDecimals(6)
            spin.setSingleStep(0.1)

        manual_layout.addWidget(self.x_spin, 0, 1)
        manual_layout.addWidget(self.y_spin, 0, 3)
        manual_layout.addWidget(self.z_spin, 0, 5)
        layout.addLayout(manual_layout)

        # 4) Validation / feedback.
        self.status_label = QLabel("Waiting for geometry and blockMesh extents...")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

    def _connect_signals(self):
        self.auto_calc_checkbox.toggled.connect(self._on_auto_calculate_toggled)
        self.mode_inside_radio.toggled.connect(self._on_mode_changed)
        self.mode_outside_radio.toggled.connect(self._on_mode_changed)
        self.x_spin.valueChanged.connect(self._emit_manual_change)
        self.y_spin.valueChanged.connect(self._emit_manual_change)
        self.z_spin.valueChanged.connect(self._emit_manual_change)

    def set_context(self, active_mesh, blockmesh_bounds):
        """
        Updates context used by locationInMesh auto-calculation.

        blockmesh_bounds uses PyVista ordering:
        (xmin, xmax, ymin, ymax, zmin, zmax)
        """
        self._active_mesh = active_mesh
        self._blockmesh_bounds = blockmesh_bounds
        if self.auto_calc_checkbox.isChecked():
            self.calculate_location_in_mesh()

    def _on_auto_calculate_toggled(self, checked):
        for spin in (self.x_spin, self.y_spin, self.z_spin):
            spin.setEnabled(not checked)

        if checked:
            self.calculate_location_in_mesh()
        else:
            self.status_label.setText(
                "Manual override active. Ensure point is inside valid retained region."
            )
            self.status_label.setStyleSheet("color: #e0a000;")

    def _on_mode_changed(self, _checked):
        if self.auto_calc_checkbox.isChecked():
            self.calculate_location_in_mesh()

    def _emit_manual_change(self):
        if not self.auto_calc_checkbox.isChecked():
            self._update_status_feedback(self.get_location_tuple())
            self.location_changed.emit(self.get_location_tuple())

    def calculate_location_in_mesh(self):
        """Search for a verified point in the selected retained volume."""
        if self._blockmesh_bounds is None:
            self.status_label.setText("BlockMesh bounds are not available yet.")
            self.status_label.setStyleSheet("color: #d9534f;")
            return

        try:
            point = find_retained_point(self._blockmesh_bounds, self._active_mesh,
                                        self.mode_inside_radio.isChecked())
        except ValueError as exc:
            self.status_label.setText(str(exc))
            self.status_label.setStyleSheet('color: #d9534f;')
            return

        self._set_point(point)
        self.location_changed.emit(point)

    def _set_point(self, point):
        for spin, value in (
            (self.x_spin, point[0]),
            (self.y_spin, point[1]),
            (self.z_spin, point[2]),
        ):
            was_blocked = spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(was_blocked)
        self._update_status_feedback(point)

    def _update_status_feedback(self, point):
        if self._blockmesh_bounds is None:
            return
        try:
            retained_point_valid(point, self._blockmesh_bounds, self._active_mesh,
                                  self.mode_inside_radio.isChecked())
        except ValueError as exc:
            self.status_label.setText(str(exc))
            self.status_label.setStyleSheet('color: #d9534f;')
            return

        x, y, z = point
        xmin, xmax, ymin, ymax, zmin, zmax = self._blockmesh_bounds
        ranges = (
            max(abs(xmax - xmin), 1e-9),
            max(abs(ymax - ymin), 1e-9),
            max(abs(zmax - zmin), 1e-9),
        )
        normalized_face_distance = min(
            min(abs(x - xmin), abs(xmax - x)) / ranges[0],
            min(abs(y - ymin), abs(ymax - y)) / ranges[1],
            min(abs(z - zmin), abs(zmax - z)) / ranges[2],
        )

        if normalized_face_distance < 0.005:
            self.status_label.setText(
                "Warning: locationInMesh is very close to a blockMesh boundary face (<0.5%)."
            )
            self.status_label.setStyleSheet("color: #d9534f;")
        elif normalized_face_distance < 0.02:
            self.status_label.setText(
                "Caution: locationInMesh is close to a boundary face. Consider moving inward."
            )
            self.status_label.setStyleSheet("color: #e0a000;")
        else:
            self.status_label.setText(
                'Point is inside the background mesh and on the selected side of the closed surface.'
            )
            self.status_label.setStyleSheet("color: #4caf50;")

    def get_location_tuple(self):
        return (
            float(self.x_spin.value()),
            float(self.y_spin.value()),
            float(self.z_spin.value()),
        )

    def get_location_in_mesh_string(self):
        """Formats locationInMesh exactly as '(X Y Z);' for OpenFOAM dicts."""
        x, y, z = self.get_location_tuple()
        return f"({x:.6g} {y:.6g} {z:.6g});"


class MeshTab(QWidget):
    mesh_updated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.case_dir = ''
        self.boundary_types = {}
        self.setup_ui()

    def set_case_directory(self, path):
        self.case_dir = path

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

        self.plotter = QtInteractor(self.vis_frame)  # type: ignore[arg-type]
        vis_layout.addWidget(self.plotter.interactor)

        self.plotter.show_axes()
        self.plotter.show_grid()
        self.plotter.add_text(
            "3D Visualization Window",
            font_size=12,
            color="white",
            position="upper_left",
        )

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
            min_spin.setDecimals(6)
            min_spin.setRange(-1e9, 1e9)
            layout.addWidget(min_spin, row, 1)

            # Max Coord
            max_spin = QDoubleSpinBox()
            max_spin.setDecimals(6)
            max_spin.setRange(-1e9, 1e9)
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
                "min": min_spin,
                "max": max_spin,
                "cells": cells_spin,
                "grading": grading_spin,
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

            if hasattr(self, "mesh_volume_selector"):
                self.mesh_volume_selector.set_context(
                    self.current_mesh, self.get_blockmesh_bounds()
                )

            self.mesh_updated.emit()
        except Exception as e:
            QMessageBox.warning(
                self, "Error", f"Could not visualize blockMesh: {str(e)}"
            )

    def setup_diagnostics_section(self):
        """Sets up the Mesh Diagnostics & Fixes group box."""
        group_box = QGroupBox("3. Mesh Diagnostics & Fixes")
        layout = QVBoxLayout()

        self.diagnostics_label = QLabel(
            "No geometry loaded. Load an STL/OBJ to view diagnostics."
        )
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
            volume = mesh.volume if hasattr(mesh, "volume") else "N/A"
            manifold = mesh.is_manifold if hasattr(mesh, "is_manifold") else "N/A"

            diag_text = (
                f"<b>Points:</b> {points}<br>"
                f"<b>Cells:</b> {cells}<br>"
                f"<b>Volume:</b> {volume}<br>"
                f"<b>Is Manifold (Watertight):</b> {manifold}"
            )
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
            if hasattr(mesh, "triangulate"):
                mesh = mesh.triangulate()
            if hasattr(mesh, "compute_normals"):
                mesh = mesh.compute_normals(consistent_normals=True)

            self.current_mesh = mesh

            # Re-render
            self.plotter.clear()
            self.plotter.add_mesh(
                self.current_mesh, show_edges=True, color="lightgreen"
            )
            self.plotter.show_axes()
            self.plotter.show_grid()
            self.plotter.reset_camera()

            QMessageBox.information(self, "Success", "Mesh cleaned and fixed!")
            self.run_diagnostics()  # Update stats
            self.mesh_updated.emit()
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
        self.refinement_table.setHorizontalHeaderLabels(
            ["Geometry Name", "Min Level", "Max Level", "Type"]
        )
        header = self.refinement_table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
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

        snap_layout.addWidget(QLabel('Feature snap iterations:'))
        self.explicit_feature_snap = QSpinBox()
        self.explicit_feature_snap.setRange(0, 100)
        self.explicit_feature_snap.setValue(10)
        snap_layout.addWidget(self.explicit_feature_snap)
        self.implicit_feature_snap = QCheckBox('Implicit feature snapping')
        self.implicit_feature_snap.setChecked(True)
        snap_layout.addWidget(self.implicit_feature_snap)
        layout.addLayout(snap_layout)

        # --- locationInMesh Controls ---
        self.mesh_volume_selector = MeshVolumeSelectorWidget(self)
        self.mesh_volume_selector.location_changed.connect(
            self.on_location_in_mesh_changed
        )
        layout.addWidget(self.mesh_volume_selector)

        # Generate Button
        self.btn_gen_snappy = QPushButton("Generate snappyHexMeshDict")
        self.btn_gen_snappy.clicked.connect(self.generate_snappyHexMeshDict)
        layout.addWidget(self.btn_gen_snappy)

        group_box.setLayout(layout)
        self.controls_layout.addWidget(group_box)

    def update_from_conceptual_model(
        self, dim_x, dim_y, dim_z, cells_x, cells_y, cells_z, boundaries=None
    ):
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
        if boundaries:
            self.boundary_types = {axis+side: 'wall' if boundaries[label] == 'Wall' else 'patch'
                for axis, label_axis in [('x','X'),('y','Y'),('z','Z')]
                for side, label in [('Min', 'Min '+label_axis), ('Max', 'Max '+label_axis)]}

        if hasattr(self, "mesh_volume_selector"):
            self.mesh_volume_selector.set_context(
                self.current_mesh, self.get_blockmesh_bounds()
            )

    def browse_geometry(self):
        """Opens a file dialog to select a base geometry and visualizes it."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Geometry", "", "Geometry Files (*.stl *.obj);;All Files (*)"
        )
        if file_path:
            self.geom_path_edit.setText(file_path)
            # Automatically add the file as a default surface refinement region
            geom_name = os.path.splitext(os.path.basename(file_path))[0]
            if not self._has_refinement_geometry_name(geom_name):
                self.add_refinement_row(default_name=geom_name)

            # Load and visualize the geometry
            try:
                self.current_mesh = pv.read(file_path)
                self.plotter.clear()
                self.plotter.add_mesh(self.current_mesh, show_edges=True, color="w")
                self.plotter.show_axes()
                self.plotter.show_grid()
                self.plotter.reset_camera()

                if hasattr(self, "mesh_volume_selector"):
                    self.mesh_volume_selector.set_context(
                        self.current_mesh, self.get_blockmesh_bounds()
                    )

                self.mesh_updated.emit()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to load geometry: {str(e)}")

    def _has_refinement_geometry_name(self, name):
        """Checks refinement table for an existing geometry-name row."""
        for row in range(self.refinement_table.rowCount()):
            widget = self.refinement_table.cellWidget(row, 0)
            if isinstance(widget, QLineEdit) and widget.text().strip() == name:
                return True
        return False

    def get_blockmesh_bounds(self):
        """Returns blockMesh bounds in PyVista order: (xmin, xmax, ymin, ymax, zmin, zmax)."""
        return (
            self.blockmesh_inputs["X"]["min"].value(),
            self.blockmesh_inputs["X"]["max"].value(),
            self.blockmesh_inputs["Y"]["min"].value(),
            self.blockmesh_inputs["Y"]["max"].value(),
            self.blockmesh_inputs["Z"]["min"].value(),
            self.blockmesh_inputs["Z"]["max"].value(),
        )

    def on_location_in_mesh_changed(self, _point):
        """Slot reserved for future live previews or additional validation hooks."""
        # Intentionally lightweight for now.
        pass

    def add_refinement_row(self, default_name=""):
        """Dynamically adds a row to the refinement configuration table."""
        if not isinstance(default_name, str):
            default_name = ''
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
        try:
            content = block_mesh_dictionary(self.get_blockmesh_bounds(),
                tuple(self.blockmesh_inputs[a]['cells'].value() for a in 'XYZ'),
                tuple(self.blockmesh_inputs[a]['grading'].value() for a in 'XYZ'),
                self.boundary_types)
            backup = write_case_files(self.case_dir, {'system/blockMeshDict': content})
            QMessageBox.information(self, 'Dictionary saved',
                f'Wrote {self.case_dir}/system/blockMeshDict.\nRun blockMesh in Execution to generate the mesh.\n'
                + (f'Previous files: {backup}' if backup else ''))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Mesh configuration', str(exc))

    def generate_snappyHexMeshDict(self):
        try:
            if self.current_mesh is None or not self.geom_path_edit.text():
                raise ValueError('Load a geometry surface first.')
            selector = self.mesh_volume_selector
            if selector.auto_calc_checkbox.isChecked():
                selector.set_context(self.current_mesh, self.get_blockmesh_bounds())
            point = selector.get_location_tuple()
            retained_point_valid(point, self.get_blockmesh_bounds(), self.current_mesh,
                                 selector.mode_inside_radio.isChecked())
            refinements = [(self.refinement_table.cellWidget(i,0).text().strip(),
                            self.refinement_table.cellWidget(i,1).value(),
                            self.refinement_table.cellWidget(i,2).value(),
                            self.refinement_table.cellWidget(i,3).currentText())
                           for i in range(self.refinement_table.rowCount())]
            filename = Path(self.geom_path_edit.text()).name
            content = snappy_dictionary(filename, refinements, point,
                self.max_local_cells.value(), self.max_global_cells.value(),
                self.enable_snap_cb.isChecked(), self.explicit_feature_snap.value(),
                self.implicit_feature_snap.isChecked())
            # Save the current cleaned/transformed surface, not the original file.
            with tempfile.TemporaryDirectory(prefix='ofsolvers-surface-') as tmp:
                surface = Path(tmp)/filename
                self.current_mesh.extract_surface().triangulate().save(surface, binary=False)
                backup = write_case_files(self.case_dir, {
                    'system/snappyHexMeshDict': content,
                    'constant/triSurface/'+filename: surface.read_bytes()})
            QMessageBox.information(self, 'Dictionary saved',
                'Saved snappyHexMeshDict and the active geometry in constant/triSurface.\n'
                'Review settings, generate the background mesh, then run snappyHexMesh and checkMesh.\n'
                + (f'Previous files: {backup}' if backup else ''))
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, 'snappyHexMesh configuration', str(exc))
