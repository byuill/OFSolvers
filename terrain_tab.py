import os
import numpy as np
import pyvista as pv

try:
    import rasterio
except ImportError:
    rasterio = None

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFileDialog,
    QCheckBox,
    QSpinBox,
    QDoubleSpinBox,
    QGroupBox,
    QFormLayout,
    QProgressBar,
    QMessageBox,
    QApplication,
)
from PyQt6.QtCore import Qt


class TerrainTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.filepath = None
        self.setup_ui()

    def setup_ui(self):
        main_layout = QHBoxLayout(self)

        # Left Column for Controls
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        if rasterio is None:
            warning_lbl = QLabel(
                "⚠️ <b>rasterio</b> is not installed. Please run <i>pip install rasterio</i> in your terminal."
            )
            warning_lbl.setStyleSheet("color: red; font-size: 14px;")
            warning_lbl.setWordWrap(True)
            left_layout.addWidget(warning_lbl)

        # 1. Input Section
        input_group = QGroupBox("1. Input DEM (.tif)")
        input_layout = QVBoxLayout()
        self.btn_load_tif = QPushButton("Select GeoTIFF DEM")
        self.btn_load_tif.clicked.connect(self.browse_file)
        if rasterio is None:
            self.btn_load_tif.setEnabled(False)

        self.lbl_file = QLabel("File: None")
        self.lbl_res = QLabel("Resolution: -")
        self.lbl_crs = QLabel("CRS: -")
        self.lbl_bounds = QLabel("Bounds: -")

        for lbl in [self.lbl_file, self.lbl_res, self.lbl_crs, self.lbl_bounds]:
            lbl.setWordWrap(True)
            input_layout.addWidget(lbl)

        input_layout.insertWidget(0, self.btn_load_tif)
        input_group.setLayout(input_layout)
        left_layout.addWidget(input_group)

        # 2. Processing Controls
        proc_group = QGroupBox("2. Processing Controls")
        proc_layout = QFormLayout()

        self.chk_normalize = QCheckBox("Reset Minima to (0,0,0)")
        self.chk_normalize.setChecked(True)

        self.spin_factor = QSpinBox()
        self.spin_factor.setRange(1, 100)
        self.spin_factor.setValue(1)
        self.spin_factor.setToolTip(
            "Downsample the grid (e.g. 2 skips every other pixel) to reduce triangle count."
        )

        self.spin_zscale = QDoubleSpinBox()
        self.spin_zscale.setRange(0.001, 100.0)
        self.spin_zscale.setValue(1.0)
        self.spin_zscale.setDecimals(3)

        self.spin_extrude = QDoubleSpinBox()
        self.spin_extrude.setRange(0.1, 10000.0)
        self.spin_extrude.setValue(10.0)
        self.spin_extrude.setDecimals(2)
        self.spin_extrude.setToolTip(
            "Depth below the lowest terrain point to create the flat base."
        )

        proc_layout.addRow(self.chk_normalize)
        proc_layout.addRow("Downsample Factor:", self.spin_factor)
        proc_layout.addRow("Z-Scale (Exaggeration):", self.spin_zscale)
        proc_layout.addRow("Extrusion Depth (m):", self.spin_extrude)
        proc_group.setLayout(proc_layout)
        left_layout.addWidget(proc_group)

        # 3. Transformation Controls
        trans_group = QGroupBox("3. Transformation")
        trans_layout = QFormLayout()

        self.spin_dx = self._create_double_spinbox(-1000000.0, 1000000.0, 0.0)
        self.spin_dy = self._create_double_spinbox(-1000000.0, 1000000.0, 0.0)
        self.spin_dz = self._create_double_spinbox(-1000000.0, 1000000.0, 0.0)
        self.spin_rx = self._create_double_spinbox(-360.0, 360.0, 0.0)
        self.spin_ry = self._create_double_spinbox(-360.0, 360.0, 0.0)
        self.spin_rz = self._create_double_spinbox(-360.0, 360.0, 0.0)

        trans_layout.addRow("Translate X (m):", self.spin_dx)
        trans_layout.addRow("Translate Y (m):", self.spin_dy)
        trans_layout.addRow("Translate Z (m):", self.spin_dz)
        trans_layout.addRow("Rotate X (deg):", self.spin_rx)
        trans_layout.addRow("Rotate Y (deg):", self.spin_ry)
        trans_layout.addRow("Rotate Z (deg):", self.spin_rz)
        trans_group.setLayout(trans_layout)
        left_layout.addWidget(trans_group)

        left_layout.addStretch()
        main_layout.addWidget(left_widget)

        # Right Column for Export and Display
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        info_lbl = QLabel(
            "<h3>Terrain to STL Processor</h3>"
            "<p>This tool converts a GeoTIFF Digital Elevation Model (DEM) "
            "into a watertight, manifold STL optimized for OpenFOAM's snappyHexMesh.</p>"
            "<ul>"
            "<li><b>Normalization</b> helps center the terrain to your blockMesh domain.</li>"
            "<li><b>Downsampling</b> prevents memory exhaustion for huge TIFF files.</li>"
            "<li><b>Extrusion</b> builds walls and a base to ensure the geometry is a closed volume.</li>"
            "</ul>"
        )
        info_lbl.setWordWrap(True)
        right_layout.addWidget(info_lbl)
        right_layout.addStretch()

        self.progress = QProgressBar()
        self.progress.setValue(0)
        right_layout.addWidget(self.progress)

        self.btn_generate = QPushButton("Generate & Save STL")
        self.btn_generate.setStyleSheet(
            "font-size: 16px; font-weight: bold; padding: 10px;"
        )
        self.btn_generate.clicked.connect(self.generate_stl)
        if rasterio is None:
            self.btn_generate.setEnabled(False)
        right_layout.addWidget(self.btn_generate)

        main_layout.addWidget(right_widget)
        main_layout.setStretch(0, 4)
        main_layout.setStretch(1, 6)

    def _create_double_spinbox(self, min_val, max_val, default_val):
        sb = QDoubleSpinBox()
        sb.setRange(min_val, max_val)
        sb.setDecimals(3)
        sb.setValue(default_val)
        return sb

    def browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select GeoTIFF DEM", "", "GeoTIFF (*.tif *.tiff);;All Files (*)"
        )
        if path and rasterio:
            self.filepath = path
            self.lbl_file.setText(f"File: {os.path.basename(path)}")
            try:
                with rasterio.open(path) as src:
                    self.lbl_res.setText(
                        f"Resolution: {src.width} x {src.height} pixels"
                    )
                    self.lbl_crs.setText(f"CRS: {src.crs}")
                    self.lbl_bounds.setText(
                        f"Bounds: {src.bounds.left:.2f}, {src.bounds.bottom:.2f} "
                        f"to {src.bounds.right:.2f}, {src.bounds.top:.2f}"
                    )
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to read metadata:\n{e}")
                self.filepath = None

    def generate_stl(self):
        if rasterio is None:
            QMessageBox.warning(
                self,
                "Warning",
                "rasterio is not installed. Install it to process DEM files.",
            )
            return

        if not self.filepath:
            QMessageBox.warning(self, "Warning", "Please select a GeoTIFF file first.")
            return

        # Gather inputs
        factor = self.spin_factor.value()
        z_scale = self.spin_zscale.value()
        extrude_depth = self.spin_extrude.value()
        normalize = self.chk_normalize.isChecked()

        self.btn_generate.setEnabled(False)
        self.progress.setValue(10)
        QApplication.processEvents()

        try:
            with rasterio.open(self.filepath) as src:
                # Read the first band
                data = src.read(1)
                nodata = src.nodata
                bounds = src.bounds

            self.progress.setValue(30)
            QApplication.processEvents()

            # Flip the data vertically so Y ascends naturally (right-handed coordinates)
            data = data[::-1, :]

            # Apply downsampling factor
            z = data[::factor, ::factor].astype(float)

            # Mask out 'nodata' values to avoid crazy artifacts
            if nodata is not None:
                valid_z = z[z != nodata]
                min_valid_z = np.nanmin(valid_z) if valid_z.size > 0 else 0
                z[z == nodata] = min_valid_z
            else:
                min_valid_z = np.nanmin(z)

            # Create 2D Grid X and Y arrays
            ny, nx = z.shape
            x = np.linspace(bounds.left, bounds.right, nx)
            y = np.linspace(bounds.bottom, bounds.top, ny)
            xx, yy = np.meshgrid(x, y)

            self.progress.setValue(50)
            QApplication.processEvents()

            # Normalization
            if normalize:
                xx -= np.min(xx)
                yy -= np.min(yy)
                z -= np.min(z)
                min_valid_z = 0.0

            # Apply Z-scale
            z *= z_scale
            min_valid_z *= z_scale

            # Build a 3D Structured Grid. By duplicating the xy grid twice in Z
            # (Layer 0: flat base, Layer 1: the terrain), we instantly get a manifold solid.
            bottom_z = np.full_like(z, min_valid_z - extrude_depth)

            X = np.stack((xx, xx), axis=-1)
            Y = np.stack((yy, yy), axis=-1)
            Z = np.stack((bottom_z, z), axis=-1)

            grid = pv.StructuredGrid(X, Y, Z)

            # Extract the outer surface (makes it watertight)
            solid = grid.extract_surface()

            self.progress.setValue(75)
            QApplication.processEvents()

            # Transformations
            solid = solid.translate(
                (self.spin_dx.value(), self.spin_dy.value(), self.spin_dz.value()),
                inplace=False,
            )
            solid = solid.rotate_x(self.spin_rx.value(), inplace=False)
            solid = solid.rotate_y(self.spin_ry.value(), inplace=False)
            solid = solid.rotate_z(self.spin_rz.value(), inplace=False)

            self.progress.setValue(90)
            QApplication.processEvents()

            # Ask where to save
            # Default to a likely OpenFOAM constant/triSurface layout relative to CWD if possible
            default_dir = os.path.join(os.getcwd(), "constant", "triSurface")
            os.makedirs(default_dir, exist_ok=True)

            save_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Terrain Solid",
                os.path.join(default_dir, "terrain.stl"),
                "STL Files (*.stl)",
            )

            if save_path:
                solid.save(save_path)
                QMessageBox.information(
                    self,
                    "Success",
                    f"Terrain generated and saved to:\n{save_path}\n\n"
                    f"Final Vertices: {solid.n_points}\n"
                    f"Final Triangles: {solid.n_cells}",
                )

            self.progress.setValue(100)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to generate STL:\n{str(e)}")
            self.progress.setValue(0)
        finally:
            self.btn_generate.setEnabled(True)
