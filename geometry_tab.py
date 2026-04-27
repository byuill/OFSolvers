import os
from typing import Optional, Tuple
import numpy as np
import pyvista as pv
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QListWidget,
    QPushButton,
    QFileDialog,
    QLabel,
    QStackedWidget,
    QComboBox,
    QGroupBox,
    QFormLayout,
    QDoubleSpinBox,
    QMessageBox,
)
from PyQt6.QtCore import Qt


class GeometryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mesh_tab = None

        # Dictionary to store the actual PyVista mesh objects
        # Key: filename (string), Value: pv.PolyData
        self.geometries = {}
        # Track original source path for each loaded geometry key.
        self.geometry_sources = {}

        self.setup_ui()

    def set_mesh_tab(self, mesh_tab):
        """Allows access to the Mesh tab to read background mesh dimensions."""
        self.mesh_tab = mesh_tab

    def setup_ui(self):
        main_layout = QHBoxLayout(self)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        # ---------------------------------------------------------
        # LEFT PANE: Geometry Manager
        # ---------------------------------------------------------
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        left_layout.addWidget(QLabel("<b>Loaded Geometries:</b>"))

        self.geom_list = QListWidget()
        self.geom_list.currentItemChanged.connect(self.on_geometry_selected)
        left_layout.addWidget(self.geom_list)

        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("Add Geometry")
        self.btn_add.clicked.connect(self.add_geometry)
        self.btn_remove = QPushButton("Remove Geometry")
        self.btn_remove.clicked.connect(self.remove_geometry)

        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_remove)
        left_layout.addLayout(btn_layout)

        # Geometry Properties Display
        self.props_group = QGroupBox("Geometry Properties")
        props_layout = QVBoxLayout(self.props_group)
        self.lbl_bounds = QLabel("Bounds:\n X: - \n Y: - \n Z: -")
        self.lbl_volume = QLabel("Volume: -")
        props_layout.addWidget(self.lbl_bounds)
        props_layout.addWidget(self.lbl_volume)
        left_layout.addWidget(self.props_group)

        self.splitter.addWidget(left_widget)

        # ---------------------------------------------------------
        # RIGHT PANE: Transformation Tools
        # ---------------------------------------------------------
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        tool_selection_layout = QHBoxLayout()
        tool_selection_layout.addWidget(QLabel("<b>Active Tool:</b>"))
        self.tool_combo = QComboBox()
        self.tool_combo.addItems(["Translate", "Rotate", "Scale to Mesh with Buffer"])
        self.tool_combo.currentIndexChanged.connect(self.on_tool_changed)
        tool_selection_layout.addWidget(self.tool_combo)
        tool_selection_layout.addStretch()
        right_layout.addLayout(tool_selection_layout)

        # Stacked Widget to hold different tool controls
        self.stacked_tools = QStackedWidget()

        self.page_translate = self.build_translate_page()
        self.page_rotate = self.build_rotate_page()
        self.page_scale = self.build_scale_page()

        self.stacked_tools.addWidget(self.page_translate)
        self.stacked_tools.addWidget(self.page_rotate)
        self.stacked_tools.addWidget(self.page_scale)

        right_layout.addWidget(self.stacked_tools)
        right_layout.addStretch()

        # Save Action
        self.btn_save = QPushButton("Save Transformed Geometry")
        self.btn_save.setStyleSheet("font-weight: bold; padding: 10px;")
        self.btn_save.clicked.connect(self.save_geometry)
        right_layout.addWidget(self.btn_save)

        self.splitter.addWidget(right_widget)
        self.splitter.setSizes([300, 500])

        # Disable right pane until a geometry is selected
        right_widget.setEnabled(False)
        self.right_widget_container = right_widget

    # --- Tool Page Builders ---

    def build_translate_page(self):
        group = QGroupBox("Translate Geometry")
        layout = QFormLayout(group)

        self.spin_dx = self._create_spinbox(-1000.0, 1000.0, 0.0)
        self.spin_dy = self._create_spinbox(-1000.0, 1000.0, 0.0)
        self.spin_dz = self._create_spinbox(-1000.0, 1000.0, 0.0)

        layout.addRow("dx (m):", self.spin_dx)
        layout.addRow("dy (m):", self.spin_dy)
        layout.addRow("dz (m):", self.spin_dz)

        btn_apply = QPushButton("Apply Translate")
        btn_apply.clicked.connect(self.apply_translate)
        layout.addRow(btn_apply)
        return group

    def build_rotate_page(self):
        group = QGroupBox("Rotate Geometry")
        layout = QFormLayout(group)

        self.spin_rx = self._create_spinbox(-360.0, 360.0, 0.0)
        self.spin_ry = self._create_spinbox(-360.0, 360.0, 0.0)
        self.spin_rz = self._create_spinbox(-360.0, 360.0, 0.0)

        layout.addRow("X-axis Angle (deg):", self.spin_rx)
        layout.addRow("Y-axis Angle (deg):", self.spin_ry)
        layout.addRow("Z-axis Angle (deg):", self.spin_rz)

        btn_apply = QPushButton("Apply Rotation")
        btn_apply.clicked.connect(self.apply_rotate)
        layout.addRow(btn_apply)
        return group

    def build_scale_page(self):
        group = QGroupBox("Scale to Mesh with Buffer")
        layout = QFormLayout(group)

        info_lbl = QLabel(
            "<i>Scales and centers the geometry to fit within the blockMesh dimensions defined in the Mesh Generation tab.</i>"
        )
        info_lbl.setWordWrap(True)
        layout.addRow(info_lbl)

        self.spin_buffer = self._create_spinbox(0.0, 1000.0, 0.5)
        layout.addRow("Buffer (m):", self.spin_buffer)

        btn_apply = QPushButton("Scale and Center to Mesh")
        btn_apply.clicked.connect(self.apply_scale_to_mesh)
        layout.addRow(btn_apply)
        return group

    def _create_spinbox(self, min_val, max_val, default_val):
        spinbox = QDoubleSpinBox()
        spinbox.setRange(min_val, max_val)
        spinbox.setDecimals(4)
        spinbox.setValue(default_val)
        return spinbox

    # --- Slots & Logic ---

    def on_tool_changed(self, index):
        self.stacked_tools.setCurrentIndex(index)

    def add_geometry(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Geometry Files",
            "",
            "Geometry Files (*.stl *.obj);;All Files (*)",
        )
        for path in file_paths:
            base_name = os.path.basename(path)
            display_name = self._make_unique_geometry_name(base_name)
            try:
                # Use PyVista to read the geometry data (uses VTK under the hood)
                mesh = pv.read(path)
                self.geometries[display_name] = mesh
                self.geometry_sources[display_name] = path
                self.geom_list.addItem(display_name)
            except Exception as e:
                QMessageBox.warning(
                    self, "Error", f"Failed to load {base_name}:\n{str(e)}"
                )

    def _make_unique_geometry_name(self, base_name):
        """Avoid collisions when files from different folders share the same basename."""
        if base_name not in self.geometries:
            return base_name

        stem, ext = os.path.splitext(base_name)
        i = 2
        while True:
            candidate = f"{stem} ({i}){ext}"
            if candidate not in self.geometries:
                return candidate
            i += 1

    def remove_geometry(self):
        current_item = self.geom_list.currentItem()
        if current_item:
            name = current_item.text()
            # Remove from widget and internal dictionary
            self.geom_list.takeItem(self.geom_list.row(current_item))
            if name in self.geometries:
                del self.geometries[name]
            if name in self.geometry_sources:
                del self.geometry_sources[name]

            if self.geom_list.count() == 0:
                self.right_widget_container.setEnabled(False)
                self.lbl_bounds.setText("Bounds:\n X: - \n Y: - \n Z: -")
                self.lbl_volume.setText("Volume: -")

    def on_geometry_selected(self, current, previous):
        if current:
            self.right_widget_container.setEnabled(True)
            self.update_geometry_properties(current.text())
        else:
            self.right_widget_container.setEnabled(False)

    def update_geometry_properties(self, name):
        mesh = self.geometries.get(name)
        if mesh:
            # PyVista computes bounds dynamically using VTK
            b = mesh.bounds  # Returns (xmin, xmax, ymin, ymax, zmin, zmax)
            vol = mesh.volume

            bounds_txt = (
                f"Bounds:\n"
                f" X: {b[0]:.3f} to {b[1]:.3f} (Length: {b[1]-b[0]:.3f})\n"
                f" Y: {b[2]:.3f} to {b[3]:.3f} (Width: {b[3]-b[2]:.3f})\n"
                f" Z: {b[4]:.3f} to {b[5]:.3f} (Height: {b[5]-b[4]:.3f})"
            )
            self.lbl_bounds.setText(bounds_txt)
            self.lbl_volume.setText(f"Volume: {vol:.4f} m³")

    def get_current_mesh(self) -> Tuple[Optional[str], Optional[pv.DataSet]]:
        item = self.geom_list.currentItem()
        if not item:
            return None, None
        name = item.text()
        return name, self.geometries[name]

    def apply_translate(self):
        name, mesh = self.get_current_mesh()
        if mesh is not None and name is not None:
            dx, dy, dz = (
                self.spin_dx.value(),
                self.spin_dy.value(),
                self.spin_dz.value(),
            )
            # Apply PyVista (VTK) translation matrix
            self.geometries[name] = mesh.translate((dx, dy, dz), inplace=False)
            self.update_geometry_properties(name)

    def apply_rotate(self):
        name, mesh = self.get_current_mesh()
        if mesh is not None and name is not None:
            rx, ry, rz = (
                self.spin_rx.value(),
                self.spin_ry.value(),
                self.spin_rz.value(),
            )
            # PyVista allows chaining rotations easily around standard axes
            mesh = mesh.rotate_x(rx, inplace=False)
            mesh = mesh.rotate_y(ry, inplace=False)
            mesh = mesh.rotate_z(rz, inplace=False)
            self.geometries[name] = mesh
            self.update_geometry_properties(name)

    def apply_scale_to_mesh(self):
        name, geom_mesh = self.get_current_mesh()
        if geom_mesh is None or name is None or not self.mesh_tab:
            return

        try:
            # Fetch blockMesh bounds mapped from the Conceptual Setup / Mesh Tab
            mesh_xmin = self.mesh_tab.blockmesh_inputs["X"]["min"].value()
            mesh_xmax = self.mesh_tab.blockmesh_inputs["X"]["max"].value()
            mesh_ymin = self.mesh_tab.blockmesh_inputs["Y"]["min"].value()
            mesh_ymax = self.mesh_tab.blockmesh_inputs["Y"]["max"].value()
            mesh_zmin = self.mesh_tab.blockmesh_inputs["Z"]["min"].value()
            mesh_zmax = self.mesh_tab.blockmesh_inputs["Z"]["max"].value()
        except Exception:
            QMessageBox.warning(
                self, "Error", "Could not read background mesh dimensions."
            )
            return

        buffer_val = self.spin_buffer.value()

        # Mesh bounding box dimensions
        mesh_dx = (mesh_xmax - mesh_xmin) - (2 * buffer_val)
        mesh_dy = (mesh_ymax - mesh_ymin) - (2 * buffer_val)
        mesh_dz = (mesh_zmax - mesh_zmin) - (2 * buffer_val)

        if mesh_dx <= 0 or mesh_dy <= 0 or mesh_dz <= 0:
            QMessageBox.warning(
                self, "Error", "Buffer is too large for the mesh domain."
            )
            return

        # Geometry bounding box dimensions
        gb = geom_mesh.bounds
        geom_dx = gb[1] - gb[0]
        geom_dy = gb[3] - gb[2]
        geom_dz = gb[5] - gb[4]

        # Calculate uniform scale factor based on the tightest axis
        scale_x = mesh_dx / geom_dx if geom_dx > 0 else float("inf")
        scale_y = mesh_dy / geom_dy if geom_dy > 0 else float("inf")
        scale_z = mesh_dz / geom_dz if geom_dz > 0 else float("inf")
        scale_factor = min(scale_x, scale_y, scale_z)

        # 1. Translate geometry to origin (center it)
        geom_center = np.array(geom_mesh.center)
        geom_mesh = geom_mesh.translate(-geom_center, inplace=False)

        # 2. Scale the geometry using VTK/Pyvista built-in scale
        geom_mesh = geom_mesh.scale(
            [scale_factor, scale_factor, scale_factor], inplace=False
        )

        # 3. Translate geometry to the center of the blockMesh
        mesh_center = np.array(
            [
                (mesh_xmax + mesh_xmin) / 2.0,
                (mesh_ymax + mesh_ymin) / 2.0,
                (mesh_zmax + mesh_zmin) / 2.0,
            ]
        )
        geom_mesh = geom_mesh.translate(mesh_center, inplace=False)

        self.geometries[name] = geom_mesh
        self.update_geometry_properties(name)
        QMessageBox.information(
            self, "Success", f"Scaled by factor: {scale_factor:.4f}"
        )

    def save_geometry(self):
        name, mesh = self.get_current_mesh()
        if mesh is not None and name is not None:
            # Determine original extension
            source_path = self.geometry_sources.get(name, "")
            ext = (
                os.path.splitext(source_path)[1].lower()
                if source_path
                else os.path.splitext(name)[1].lower()
            )
            if not ext:
                ext = ".stl"
            filter_str = f"Geometry (*{ext})"

            save_path, _ = QFileDialog.getSaveFileName(
                self, "Save Transformed Geometry", f"transformed_{name}", filter_str
            )

            if save_path:
                try:
                    # PyVista handles writing out the new points/faces gracefully
                    mesh.save(save_path)
                    QMessageBox.information(
                        self, "Saved", f"Saved successfully to:\n{save_path}"
                    )
                except Exception as e:
                    QMessageBox.critical(
                        self, "Error", f"Failed to save file:\n{str(e)}"
                    )
