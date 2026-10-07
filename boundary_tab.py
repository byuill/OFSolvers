import os
import re

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QGroupBox,
    QLabel,
    QDoubleSpinBox,
    QCheckBox,
    QPushButton,
    QListWidget,
    QComboBox,
    QStackedWidget,
    QFormLayout,
    QMessageBox,
    QLineEdit,
    QFileDialog,
    QTextEdit,
)
from PyQt6.QtCore import Qt

from openfoam_dict_parser import OpenFoamDictParser
from foam_io import boundary_patches, write_case_files, value, entries
from case_qaqc import PRESSURE_SOLVERS


class BoundaryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.patch_data = {}  # Dictionary to store settings for each patch
        self.mesh_tab = None
        self.current_patch_name = None
        self._loading_patch_state = False
        self.case_dir = ''
        self.mesh_patch_types = {}
        self.dirty_patches = set()
        self.setup_ui()

    def set_case_directory(self, path):
        self.case_dir = path
        self.case_dir_edit.setText(path)
        self.current_patch_name = None
        self.patch_list.clear()
        self.patch_data.clear()
        self.mesh_patch_types.clear()
        self.dirty_patches.clear()
        self.rebuild_boundaries.setChecked(False)

    def set_mesh_tab(self, mesh_tab):
        self.mesh_tab = mesh_tab

    def set_solver_profile(self, solver_name):
        """Synchronizes boundary writer profile with global solver selection."""
        supported = {'interFoam'} | PRESSURE_SOLVERS
        if self.solver_profile_combo.findText(solver_name) < 0:
            self.solver_profile_combo.addItem(solver_name)
        self.solver_profile_combo.setCurrentText(solver_name)
        self.btn_write_0.setEnabled(solver_name in supported)
        self.btn_write_0.setToolTip('Edits existing fluid fields only; advanced profiles require manual setup.'
                                  if solver_name not in supported else 'Writes only edited patches; keeps other entries unchanged.')
        is_inter = solver_name == 'interFoam'
        self.atm_pressure.setSuffix(' Pa gauge' if is_inter else ' m²/s² gauge')
        self.outlet_dwl.setSuffix(' Pa gauge' if is_inter else ' m²/s² gauge')
        self.inlet_mwl.setEnabled(is_inter)

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

        case_layout = QHBoxLayout()
        self.case_dir_edit = QLineEdit(self.case_dir)
        self.case_dir_edit.setReadOnly(True)
        self.case_dir_edit.setPlaceholderText(
            "OpenFOAM case directory (contains 0/, constant/, system/)"
        )
        self.case_dir_edit.editingFinished.connect(self._on_case_dir_edited)
        btn_case_browse = QPushButton("Browse Case")
        btn_case_browse.setEnabled(False)
        btn_case_browse.setToolTip('Select the shared case folder at the top of the window.')
        btn_case_browse.clicked.connect(self.browse_case_directory)
        case_layout.addWidget(self.case_dir_edit)
        case_layout.addWidget(btn_case_browse)
        left_layout.addLayout(case_layout)

        self.patch_list = QListWidget()
        self.patch_list.currentItemChanged.connect(self.on_patch_selected)
        left_layout.addWidget(self.patch_list)

        self.btn_import_patches = QPushButton("Import Patches from Mesh")
        self.btn_import_patches.clicked.connect(self.import_patches)
        left_layout.addWidget(self.btn_import_patches)

        self.btn_import_boundary = QPushButton(
            "Import Patch Names from constant/polyMesh/boundary"
        )
        self.btn_import_boundary.clicked.connect(self.import_patches_from_boundary_file)
        left_layout.addWidget(self.btn_import_boundary)

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
        self.bc_type_combo.addItems(
            ['Inlet', 'Outlet', 'Atmosphere', 'Bed/Wall', 'Symmetry', 'Mapped', 'Empty', 'Preserve existing']
        )
        self.bc_type_combo.currentTextChanged.connect(self.on_bc_type_changed)
        type_layout.addWidget(self.bc_type_combo)
        self.right_layout.addLayout(type_layout)

        solver_layout = QHBoxLayout()
        solver_layout.addWidget(QLabel("<b>Solver Profile:</b>"))
        self.solver_profile_combo = QComboBox()
        self.solver_profile_combo.setEnabled(False)
        self.solver_profile_combo.addItems(["interFoam", "pimpleFoam", "simpleFoam"])
        self.solver_profile_combo.setCurrentText("interFoam")
        solver_layout.addWidget(self.solver_profile_combo)
        solver_layout.addStretch()
        self.right_layout.addLayout(solver_layout)

        # Stacked Widget for Dynamic Input Forms
        self.stacked_widget = QStackedWidget()

        # Build individual pages
        self.page_inlet = self.build_inlet_page()
        self.page_outlet = self.build_outlet_page()
        self.page_atmosphere = self.build_atmosphere_page()
        self.page_wall = self.build_wall_page()
        self.page_symmetry = self.build_symmetry_page()
        self.page_mapped = self.build_mapped_page()

        # Add pages to stacked widget (Must match the order in the combobox)
        self.stacked_widget.addWidget(self.page_inlet)  # Index 0
        self.stacked_widget.addWidget(self.page_outlet)  # Index 1
        self.stacked_widget.addWidget(self.page_atmosphere)  # Index 2
        self.stacked_widget.addWidget(self.page_wall)  # Index 3
        self.stacked_widget.addWidget(self.page_symmetry)  # Index 4
        self.stacked_widget.addWidget(self.page_mapped)  # Index 5
        for message in ('No flow is resolved normal to an empty patch (2-D cases).',
                        'Existing coupled/advanced boundary dictionaries are preserved.'):
            page = QWidget(); QVBoxLayout(page).addWidget(QLabel(message))
            self.stacked_widget.addWidget(page)

        self.right_layout.addWidget(self.stacked_widget)
        note = QLabel('Forms define replacement templates for edited patches. '
                      'Existing on-disk conditions are shown below and remain unchanged until you edit and write.')
        note.setWordWrap(True)
        self.right_layout.addWidget(note)
        self.native_preview = QTextEdit()
        self.native_preview.setReadOnly(True)
        self.native_preview.setMaximumHeight(110)
        self.right_layout.addWidget(self.native_preview)
        self.right_layout.addStretch()  # Push everything up

        # Write Boundaries Button
        self.rebuild_boundaries = QCheckBox('Rebuild ALL fluid patch conditions for a changed mesh')
        self.rebuild_boundaries.setToolTip('Replaces every flow boundary with the forms above; review every patch first. '
                                          'Initial internal fields are preserved. Old files are backed up.')
        self.right_layout.addWidget(self.rebuild_boundaries)
        self.btn_write_0 = QPushButton('Write edited fluid boundaries in 0/')
        self.btn_write_0.setStyleSheet("font-weight: bold; padding: 10px;")
        self.btn_write_0.clicked.connect(self.write_0_directory)
        self.right_layout.addWidget(self.btn_write_0)

        self.splitter.addWidget(right_widget)

        # Set initial splitter sizes (30% left, 70% right)
        self.splitter.setSizes([300, 700])

        # Disable right pane until a patch is selected
        right_widget.setEnabled(False)
        self.right_widget_container = right_widget

        self._connect_state_tracking_signals()

    def _connect_state_tracking_signals(self):
        """Track edits so each patch keeps its own boundary settings."""
        self.inlet_flow_rate.valueChanged.connect(self.save_current_patch_settings)
        self.inlet_mwl.valueChanged.connect(self.save_current_patch_settings)
        self.outlet_free_outfall.stateChanged.connect(self.save_current_patch_settings)
        self.outlet_dwl.valueChanged.connect(self.save_current_patch_settings)
        self.atm_pressure.valueChanged.connect(self.save_current_patch_settings)
        self.wall_function_combo.currentTextChanged.connect(
            self.save_current_patch_settings
        )
        self.wall_ks.valueChanged.connect(self.save_current_patch_settings)
        self.mapped_avg_velocity.valueChanged.connect(self.save_current_patch_settings)
        self.mapped_target_patch.textChanged.connect(self.save_current_patch_settings)

    def _detect_case_directory(self):
        """Returns cwd if it looks like an OpenFOAM case, else returns cwd anyway."""
        cwd = os.getcwd()
        if self._is_openfoam_case_dir(cwd):
            return cwd
        return cwd

    @staticmethod
    def _is_openfoam_case_dir(path):
        return (
            os.path.isdir(path)
            and os.path.isdir(os.path.join(path, "0"))
            and os.path.isdir(os.path.join(path, "constant"))
            and os.path.isdir(os.path.join(path, "system"))
        )

    def _on_case_dir_edited(self):
        pass  # Shared case selection owns the path.

    def browse_case_directory(self):
        selected = QFileDialog.getExistingDirectory(
            self, "Select OpenFOAM Case Directory", self.case_dir
        )
        if selected:
            self.case_dir = selected
            self.case_dir_edit.setText(selected)

    def import_patches_from_boundary_file(self):
        """Imports real patch names from constant/polyMesh/boundary metadata."""
        self._on_case_dir_edited()
        boundary_path = os.path.join(self.case_dir, "constant", "polyMesh", "boundary")
        if not os.path.isfile(boundary_path):
            QMessageBox.warning(
                self,
                "Boundary File Not Found",
                f"Could not find:\n{boundary_path}\n\nPick your case directory or generate a mesh first.",
            )
            return

        try:
            patch_entries = self._parse_boundary_file(boundary_path)
        except Exception as e:
            QMessageBox.critical(
                self, "Boundary Parse Error", f"Failed parsing boundary file:\n{e}"
            )
            return

        if not patch_entries:
            QMessageBox.warning(
                self, "No Patches", "No patch entries were found in boundary metadata."
            )
            return

        patch_names = [entry["name"] for entry in patch_entries]
        self.mesh_patch_types = {entry['name']: entry['type'] for entry in patch_entries}
        self.current_patch_name = None
        self.patch_list.clear()
        self.patch_list.addItems(patch_names)
        self._seed_patch_defaults_from_boundary_types(patch_entries)
        self.patch_list.setCurrentRow(0)
        QMessageBox.information(
            self,
            "Imported",
            f"Imported {len(patch_entries)} patches from boundary metadata.",
        )

    @staticmethod
    def _strip_foam_comments(text):
        # Remove // comments first.
        text = re.sub(r"//.*", "", text)
        # Remove /* ... */ blocks.
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        return text

    def _parse_boundary_file(self, boundary_path):
        return boundary_patches(boundary_path)

    def _seed_patch_defaults_from_boundary_types(self, patch_entries):
        """Initialize patch_data defaults from boundary patch types when not already present."""
        type_map = {
            "wall": "Bed/Wall",
            "symmetry": "Symmetry",
            "symmetryPlane": "Symmetry",
            'empty': 'Empty',
            'cyclic': 'Preserve existing',
            'cyclicAMI': 'Preserve existing',
            'processor': 'Preserve existing',
            "patch": "Outlet",
        }

        for entry in patch_entries:
            name = entry["name"]
            if name in self.patch_data:
                continue

            conceptual = type_map.get(entry["type"], "Outlet")
            self.patch_data[name] = {
                "type": conceptual,
                "inlet_flow_rate": 0.0,
                "inlet_mwl": 1.0,
                "outlet_free_outfall": False,
                "outlet_dwl": 0.0,
                "atm_pressure": 0.0,
                "wall_function": "kqRWallFunction",
                "wall_ks": 0.0,
                "mapped_avg_velocity": 0.0,
                "mapped_target_patch": "",
            }

    # --- Page Builders for QStackedWidget ---

    def build_inlet_page(self):
        page = QGroupBox("Inlet Configuration")
        layout = QFormLayout(page)

        info_label = QLabel(
            '<i>Water discharge sets U. Single-phase profiles use flowRateInletVelocity; '
            'interFoam also configures the inlet water fraction.</i>'
        )
        info_label.setWordWrap(True)
        layout.addRow(info_label)

        self.inlet_flow_rate = QDoubleSpinBox()
        self.inlet_flow_rate.setDecimals(6)
        self.inlet_flow_rate.setRange(0.0, 100000.0)
        self.inlet_flow_rate.setSuffix(" m³/s")
        layout.addRow("Volumetric Flow Rate:", self.inlet_flow_rate)

        self.inlet_mwl = QDoubleSpinBox()
        self.inlet_mwl.setRange(0, 1)
        self.inlet_mwl.setValue(1)
        layout.addRow('Upper water-fraction bound (interFoam):', self.inlet_mwl)

        return page

    def build_outlet_page(self):
        page = QGroupBox("Outlet Configuration")
        layout = QFormLayout(page)

        info_label = QLabel('<i>Velocity uses inletOutlet; pressure uses a fixed gauge value or zeroGradient.</i>')
        info_label.setWordWrap(True)
        layout.addRow(info_label)

        self.outlet_free_outfall = QCheckBox("Free Outfall / Zero Gradient")
        self.outlet_free_outfall.stateChanged.connect(self.toggle_outlet_wl)
        layout.addRow("", self.outlet_free_outfall)

        self.outlet_dwl = QDoubleSpinBox()
        self.outlet_dwl.setRange(-100.0, 10000.0)
        self.outlet_dwl.setSuffix(" m")
        layout.addRow('Outlet gauge pressure:', self.outlet_dwl)
        self.outlet_dwl.setToolTip('This is pressure, not downstream water elevation.')

        return page

    def toggle_outlet_wl(self, state):
        """Disable the fixed-pressure input when zeroGradient is selected."""
        self.outlet_dwl.setEnabled(not state)

    def build_atmosphere_page(self):
        page = QGroupBox("Atmosphere Configuration")
        layout = QFormLayout(page)

        info_label = QLabel(
            "<i>Maps to: U (pressureInletOutletVelocity), p_rgh (totalPressure)</i>"
        )
        info_label.setWordWrap(True)
        layout.addRow(info_label)

        self.atm_pressure = QDoubleSpinBox()
        self.atm_pressure.setRange(-1e9, 1e9)
        self.atm_pressure.setDecimals(3)
        self.atm_pressure.setValue(0)
        self.atm_pressure.setSuffix(" Pa")
        layout.addRow("Reference Pressure:", self.atm_pressure)

        return page

    def build_wall_page(self):
        page = QGroupBox("Bed/Wall Configuration")
        layout = QFormLayout(page)

        info_label = QLabel('<i>Sets noSlip velocity and the fluid pressure condition. '
                            'Turbulence wall fields require separate configuration.</i>')
        info_label.setWordWrap(True)
        layout.addRow(info_label)

        self.wall_function_combo = QComboBox()
        self.wall_function_combo.addItems(
            ["kqRWallFunction", "nutkWallFunction", "nutkRoughWallFunction"]
        )
        self.wall_function_combo.setEnabled(False)
        self.wall_function_combo.setToolTip('Configure turbulence wall fields separately; this editor writes fluid fields only.')
        layout.addRow("Turbulence Wall Function:", self.wall_function_combo)

        self.wall_ks = QDoubleSpinBox()
        self.wall_ks.setRange(0.0, 1.0)
        self.wall_ks.setDecimals(5)
        self.wall_ks.setSingleStep(0.001)
        self.wall_ks.setSuffix(" m")
        self.wall_ks.setEnabled(False)
        layout.addRow("Equivalent Sand Roughness (Ks):", self.wall_ks)

        return page

    def build_symmetry_page(self):
        page = QGroupBox("Symmetry Configuration")
        layout = QVBoxLayout(page)

        info_label = QLabel(
            "<i>Maps to: symmetry or symmetryPlane</i><br><br>No additional inputs required for symmetry boundaries."
        )
        info_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(info_label)
        layout.addStretch()

        return page

    def build_mapped_page(self):
        page = QGroupBox("Mapped Configuration")
        layout = QFormLayout(page)

        info_label = QLabel('<i>Mapped conditions require manual native dictionary setup. '
                            'This editor cannot write them.</i>')
        info_label.setWordWrap(True)
        layout.addRow(info_label)

        self.mapped_avg_velocity = QDoubleSpinBox()
        self.mapped_avg_velocity.setRange(-10000.0, 10000.0)
        self.mapped_avg_velocity.setSuffix(" m/s")
        self.mapped_avg_velocity.setEnabled(False)
        layout.addRow("Average Velocity:", self.mapped_avg_velocity)

        self.mapped_target_patch = QLineEdit()
        self.mapped_target_patch.setPlaceholderText("e.g. internalPlane1")
        self.mapped_target_patch.setEnabled(False)
        layout.addRow("Target Patch:", self.mapped_target_patch)

        return page

    # --- Slots / Logic ---

    def on_patch_selected(self, current, previous):
        """Enables the configuration pane when a patch is selected."""
        if current:
            self.current_patch_name = current.text()
            self.right_widget_container.setEnabled(True)
            self.load_patch_settings(self.current_patch_name)
            self.show_native_conditions(self.current_patch_name)
        else:
            self.current_patch_name = None
            self.right_widget_container.setEnabled(False)

    def show_native_conditions(self, name):
        preview = []
        fields = ['U','p_rgh','alpha.water'] if self.solver_profile_combo.currentText() == 'interFoam' else ['U','p']
        for field in fields:
            try:
                with open(os.path.join(self.case_dir,'0',field),encoding='utf-8') as stream:
                    text = value(stream.read(),'boundaryField')[1:-1]
                preview.append(field+': '+value(text,name))
            except (OSError,ValueError):
                preview.append(field+': no explicit patch entry could be read')
        self.native_preview.setPlainText('\n'.join(preview))

    def on_bc_type_changed(self, bc_type):
        """Dynamically switches the visible input form based on the selected boundary type."""
        index = self.bc_type_combo.findText(bc_type)
        if index >= 0:
            self.stacked_widget.setCurrentIndex(index)
        self.save_current_patch_settings()

    def save_current_patch_settings(self):
        """Save the currently visible form state for the active patch."""
        if self._loading_patch_state or not self.current_patch_name:
            return
        self.dirty_patches.add(self.current_patch_name)

        self.patch_data[self.current_patch_name] = {
            "type": self.bc_type_combo.currentText(),
            "inlet_flow_rate": self.inlet_flow_rate.value(),
            "inlet_mwl": self.inlet_mwl.value(),
            "outlet_free_outfall": self.outlet_free_outfall.isChecked(),
            "outlet_dwl": self.outlet_dwl.value(),
            "atm_pressure": self.atm_pressure.value(),
            "wall_function": self.wall_function_combo.currentText(),
            "wall_ks": self.wall_ks.value(),
            "mapped_avg_velocity": self.mapped_avg_velocity.value(),
            "mapped_target_patch": self.mapped_target_patch.text(),
        }

    def load_patch_settings(self, patch_name):
        """Load previously saved state for a patch, or initialize defaults."""
        settings = self.patch_data.get(patch_name)
        if settings is None:
            settings = {
                "type": "Bed/Wall",
                "inlet_flow_rate": 0.0,
                "inlet_mwl": 1.0,
                "outlet_free_outfall": False,
                "outlet_dwl": 0.0,
                "atm_pressure": 0.0,
                "wall_function": "kqRWallFunction",
                "wall_ks": 0.0,
                "mapped_avg_velocity": 0.0,
                "mapped_target_patch": "",
            }

        self._loading_patch_state = True
        try:
            self.bc_type_combo.setCurrentText(settings.get("type", "Bed/Wall"))
            self.inlet_flow_rate.setValue(settings.get("inlet_flow_rate", 0.0))
            self.inlet_mwl.setValue(settings.get("inlet_mwl", 1.0))
            self.outlet_free_outfall.setChecked(
                settings.get("outlet_free_outfall", False)
            )
            self.outlet_dwl.setValue(settings.get("outlet_dwl", 0.0))
            self.atm_pressure.setValue(settings.get("atm_pressure", 0.0))
            self.wall_function_combo.setCurrentText(
                settings.get("wall_function", "kqRWallFunction")
            )
            self.wall_ks.setValue(settings.get("wall_ks", 0.0))
            self.mapped_avg_velocity.setValue(settings.get("mapped_avg_velocity", 0.0))
            self.mapped_target_patch.setText(settings.get("mapped_target_patch", ""))
            self.stacked_widget.setCurrentIndex(self.bc_type_combo.currentIndex())
            self.toggle_outlet_wl(self.outlet_free_outfall.isChecked())
        finally:
            self._loading_patch_state = False

        # Merely selecting a patch must not mark it for overwrite.

    def import_patches(self):
        """Only import verified mesh patch names, never guesses from the preview."""
        self.import_patches_from_boundary_file()

    def write_0_directory(self):
        """Writes 0/U, 0/p_rgh, and 0/alpha.water boundaryField blocks."""
        self._on_case_dir_edited()

        if self.patch_list.count() == 0:
            QMessageBox.warning(self, "No Patches", "No patches available to write.")
            return

        if not self._is_openfoam_case_dir(self.case_dir):
            QMessageBox.warning(
                self,
                "Invalid Case Directory",
                "Case directory must contain 0/, constant/, and system/.",
            )
            return

        try:
            profile = self.solver_profile_combo.currentText()
            if profile not in ({'interFoam'} | PRESSURE_SOLVERS):
                raise ValueError('This solver profile is not supported by the fluid boundary writer.')
            if profile == "interFoam":
                fields = ["U", "p_rgh", "alpha.water"]
            else:
                fields = ["U", "p"]

            rebuild = self.rebuild_boundaries.isChecked()
            u_boundary, p_boundary, alpha_boundary, warnings = self._build_boundary_dicts(profile, rebuild)

            if warnings:
                raise ValueError('\n'.join(warnings))
            if not self.dirty_patches and not rebuild:
                raise ValueError('No boundary settings have been edited.')
            actual = {p['name']: p['type'] for p in boundary_patches(
                os.path.join(self.case_dir, 'constant/polyMesh/boundary'))}
            if set(actual) != {self.patch_list.item(i).text() for i in range(self.patch_list.count())}:
                raise ValueError('Patch list is stale. Import the current mesh boundary metadata again.')
            targets = set(actual) if rebuild else self.dirty_patches
            for patch in targets:
                kind = self.patch_data[patch]['type']
                if actual[patch] == 'empty' and kind != 'Empty':
                    raise ValueError(f'{patch} is an empty mesh patch and must retain empty field conditions.')
                if actual[patch] in ('cyclic','cyclicAMI','processor'):
                    raise ValueError(f'{patch} is coupled; preserve and edit its advanced conditions manually.')
            contents = {}

            u_parser = OpenFoamDictParser(
                os.path.join(self.case_dir, "0", "U"),
                default_class="volVectorField",
                object_name="U",
                default_dimensions="[0 1 -1 0 0 0 0]",
                default_internal_field="uniform (0 0 0)",
            )
            for name in fields:
                path = os.path.join(self.case_dir, '0', name)
                if not os.path.isfile(path):
                    raise ValueError(f'Missing 0/{name}; start from a complete case template.')
            contents['0/U'] = u_parser.render({p: u_boundary[p] for p in targets}, merge=not rebuild)

            if profile == "interFoam":
                p_parser = OpenFoamDictParser(
                    os.path.join(self.case_dir, "0", "p_rgh"),
                    default_class="volScalarField",
                    object_name="p_rgh",
                    default_dimensions="[1 -1 -2 0 0 0 0]",
                    default_internal_field="uniform 0",
                )
                alpha_parser = OpenFoamDictParser(
                    os.path.join(self.case_dir, "0", "alpha.water"),
                    default_class="volScalarField",
                    object_name="alpha.water",
                    default_dimensions="[0 0 0 0 0 0 0]",
                    default_internal_field="uniform 0",
                )
                contents['0/p_rgh'] = p_parser.render({p: p_boundary[p] for p in targets}, merge=not rebuild)
                contents['0/alpha.water'] = alpha_parser.render({p: alpha_boundary[p] for p in targets}, merge=not rebuild)
            else:
                p_parser = OpenFoamDictParser(
                    os.path.join(self.case_dir, "0", "p"),
                    default_class="volScalarField",
                    object_name="p",
                    default_dimensions="[0 2 -2 0 0 0 0]",
                    default_internal_field="uniform 0",
                )
                contents['0/p'] = p_parser.render({p: p_boundary[p] for p in targets}, merge=not rebuild)
            backup = write_case_files(self.case_dir, contents)
            self.dirty_patches.clear()
            self.rebuild_boundaries.setChecked(False)

            QMessageBox.information(
                self,
                "Success",
                "Wrote boundaryField blocks to:\n"
                + "\n".join(os.path.join(self.case_dir, "0", f) for f in fields)
                + f'\nPrevious files: {backup}',
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Write Error", f"Failed writing boundary dictionaries:\n{e}"
            )

    def _build_boundary_dicts(self, profile, rebuild=False):
        u_boundary = {}
        p_boundary = {}
        alpha_boundary = {}
        warnings = []

        is_interfoam = profile == "interFoam"

        for i in range(self.patch_list.count()):
            item = self.patch_list.item(i)
            if item is None:
                continue
            patch_name = item.text()
            settings = self.patch_data.get(patch_name, {})
            bc_type = settings.get("type", "Bed/Wall")

            inlet_flow = settings.get("inlet_flow_rate", 0.0)
            inlet_mwl = settings.get("inlet_mwl", 1.0)
            free_outfall = settings.get("outlet_free_outfall", False)
            outlet_dwl = settings.get("outlet_dwl", 0.0)
            atm_pressure = settings.get("atm_pressure", 0.0)
            mapped_avg_vel = settings.get("mapped_avg_velocity", 0.0)
            mapped_target = settings.get("mapped_target_patch", "")

            if bc_type == "Inlet":
                if is_interfoam:
                    u_boundary[patch_name] = {
                        "type": "variableHeightFlowRateInletVelocity",
                        "flowRate": str(inlet_flow),
                        'alpha': 'alpha.water',
                        "value": "uniform (0 0 0)",
                    }
                    p_boundary[patch_name] = {
                        "type": "fixedFluxPressure",
                        "value": "uniform 0",
                    }
                    alpha_boundary[patch_name] = {
                        "type": "variableHeightFlowRate",
                        "lowerBound": "0",
                        "upperBound": str(inlet_mwl),
                        "value": "uniform 1",
                    }
                else:
                    u_boundary[patch_name] = {
                        'type': 'flowRateInletVelocity',
                        'volumetricFlowRate': str(inlet_flow),
                        "value": "uniform (0 0 0)",
                    }
                    p_boundary[patch_name] = {
                        "type": "zeroGradient",
                    }
            elif bc_type == "Outlet":
                u_boundary[patch_name] = {
                    "type": "inletOutlet",
                    "inletValue": "uniform (0 0 0)",
                    "value": "uniform (0 0 0)",
                }
                if is_interfoam:
                    p_boundary[patch_name] = {
                        "type": "zeroGradient" if free_outfall else "fixedValue",
                        "value": (
                            "uniform 0" if free_outfall else f"uniform {outlet_dwl}"
                        ),
                    }
                    alpha_boundary[patch_name] = {
                        "type": "inletOutlet",
                        "inletValue": "uniform 0",
                        "value": "uniform 0",
                    }
                else:
                    p_boundary[patch_name] = {
                        "type": "fixedValue" if not free_outfall else "zeroGradient",
                        "value": (
                            f"uniform {outlet_dwl}" if not free_outfall else "uniform 0"
                        ),
                    }
            elif bc_type == "Atmosphere":
                u_boundary[patch_name] = {
                    "type": "pressureInletOutletVelocity",
                    "value": "uniform (0 0 0)",
                }
                if is_interfoam:
                    p_boundary[patch_name] = {
                        "type": "totalPressure",
                        "p0": f"uniform {atm_pressure}",
                        "value": "uniform 0",
                    }
                    alpha_boundary[patch_name] = {
                        "type": "inletOutlet",
                        "inletValue": "uniform 0",
                        "value": "uniform 0",
                    }
                else:
                    p_boundary[patch_name] = {
                        "type": "fixedValue",
                        "value": f"uniform {atm_pressure}",
                    }
            elif bc_type == "Symmetry":
                symmetry = self.mesh_patch_types.get(patch_name, 'symmetry')
                if symmetry not in ('symmetry', 'symmetryPlane'):
                    warnings.append(f'{patch_name}: mesh patch must have a matching symmetry type.')
                u_boundary[patch_name] = {'type': symmetry}
                p_boundary[patch_name] = {'type': symmetry}
                if is_interfoam:
                    alpha_boundary[patch_name] = {'type': symmetry}
            elif bc_type == 'Empty':
                u_boundary[patch_name] = {'type': 'empty'}
                p_boundary[patch_name] = {'type': 'empty'}
                alpha_boundary[patch_name] = {'type': 'empty'}
            elif bc_type == 'Preserve existing':
                if rebuild or patch_name in self.dirty_patches:
                    warnings.append(f'{patch_name}: this advanced boundary cannot be rewritten by the editor.')
            elif bc_type == "Mapped":
                if rebuild or patch_name in self.dirty_patches:
                    warnings.append(f'{patch_name}: mapped boundary setup is not implemented; configure its native dictionary manually.')
                if not mapped_target and patch_name in self.dirty_patches:
                    warnings.append(
                        f"Patch '{patch_name}' is Mapped but target patch is empty."
                    )

                u_boundary[patch_name] = {
                    "type": "mapped",
                    "average": str(mapped_avg_vel),
                    "value": f"uniform ({mapped_avg_vel} 0 0)",
                }
                if mapped_target:
                    u_boundary[patch_name]["targetPatch"] = mapped_target

                p_boundary[patch_name] = {
                    "type": "mapped",
                    "value": "uniform 0",
                }
                if is_interfoam:
                    alpha_boundary[patch_name] = {
                        "type": "mapped",
                        "value": "uniform 0",
                    }
            else:  # Bed/Wall default
                u_boundary[patch_name] = {
                    "type": "noSlip",
                    "value": "uniform (0 0 0)",
                }
                p_boundary[patch_name] = {
                    "type": "fixedFluxPressure",
                    "value": "uniform 0",
                }
                if is_interfoam:
                    alpha_boundary[patch_name] = {
                        "type": "zeroGradient",
                    }
                else:
                    p_boundary[patch_name] = {
                        "type": "zeroGradient",
                    }

        return u_boundary, p_boundary, alpha_boundary, warnings
