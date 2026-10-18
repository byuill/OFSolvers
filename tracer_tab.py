"""Workbench setup and execution of individual large physical tracers."""
from dataclasses import replace
from pathlib import Path
import sys

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QHBoxLayout,
    QGroupBox, QLabel, QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox, QCheckBox,
    QPushButton, QScrollArea, QMessageBox)

from large_tracer import Distribution, TracerConfig, save_tracers, load_tracers, preflight
from scour_tracer_demo import generate_demo


def number(value, minimum=0, maximum=1e9, decimals=6):
    widget = QDoubleSpinBox()
    widget.setDecimals(decimals)
    widget.setRange(minimum,maximum)
    widget.setValue(value)
    return widget


class DistributionEditor(QGroupBox):
    def __init__(self, title):
        super().__init__(title)
        form = QFormLayout(self)
        self.kind = QComboBox()
        self.kind.addItems(['constant','uniform','lognormal','discrete'])
        form.addRow('Distribution',self.kind)
        self.spins = {}
        for name, label in [('low','Value / minimum'),('high','Maximum'),
                            ('median','Lognormal median'),('sigma','Std. dev. of natural log')]:
            spin = number(1,1e-9,1e9,9)
            self.spins[name] = spin
            form.addRow(label,spin)
        self.values = QLineEdit()
        self.weights = QLineEdit()
        form.addRow('Discrete values (space separated)',self.values)
        form.addRow('Discrete relative weights',self.weights)
        self.kind.currentTextChanged.connect(self.update_fields)

    def update_fields(self):
        kind = self.kind.currentText()
        for name, spin in self.spins.items():
            spin.setEnabled(kind!='discrete' and (name=='low' or
                name=='high' and kind!='constant' or kind=='lognormal'))
        self.values.setEnabled(kind=='discrete')
        self.weights.setEnabled(kind=='discrete')

    def load(self, config):
        self.kind.setCurrentText(config.kind)
        for name, spin in self.spins.items():
            spin.setValue(getattr(config,name))
        self.values.setText(' '.join(map(str,config.values)))
        self.weights.setText(' '.join(map(str,config.weights)))
        self.update_fields()

    def config(self):
        discrete = self.kind.currentText()=='discrete'
        return Distribution(kind=self.kind.currentText(),**{k:s.value() for k,s in self.spins.items()},
            values=tuple(float(v) for v in self.values.text().split()) if discrete else (),
            weights=tuple(float(v) for v in self.weights.text().split()) if discrete else ())


class TracerTab(QWidget):
    command_requested = pyqtSignal(list,str)
    case_prepared = pyqtSignal(str,str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.case_dir = ''
        self._config = TracerConfig()
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        root.addWidget(scroll)
        info = QLabel('Track 1–999 individual physical spheres in saved OpenFOAM flow fields. '
            'Each particle has its own size, density and ID. Includes inertia, drag, buoyancy, '
            'added mass, wall contact and particle contact. Uses one-way coupling: particles do not alter the flow.\n'
            'Run and save the carrier flow first; reconstruct parallel fields before tracking. '
            'For floating particles, save the water/air fraction as well as velocity. '
            'Large particles or crowded regions require checking this approximation. See docs/largeTracerFoam.md.')
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        layout.addLayout(form)
        self.run_name = QLineEdit()
        form.addRow('New output run name',self.run_name)
        self.count = QSpinBox(); self.count.setRange(1,999)
        form.addRow('Number of physical particles',self.count)
        self.seed = QSpinBox(); self.seed.setRange(0,2147483647)
        form.addRow('Random seed',self.seed)
        self.diameter = DistributionEditor('Diameter (m)')
        self.density = DistributionEditor('Particle density (kg/m³)')
        layout.addWidget(self.diameter); layout.addWidget(self.density)
        region = QGroupBox('Initial placement and scour-region tracking (m)')
        region_form = QFormLayout(region)
        layout.addWidget(region)
        self.boxes = {}
        for name, label in [('seed_min','Seed-center box minimum x, y, z'),('seed_max','Seed-center box maximum x, y, z'),
                            ('retention_min','Scour region minimum x, y, z'),('retention_max','Scour region maximum x, y, z')]:
            row = QHBoxLayout()
            spins = [number(0,-1e9,1e9) for _ in range(3)]
            for spin in spins: row.addWidget(spin)
            self.boxes[name] = spins
            region_form.addRow(label,row)
        self.placement = QComboBox(); self.placement.addItems(['volume','bed'])
        region_form.addRow('Placement (nonoverlapping, initially wetted)',self.placement)
        self.bed_patches = QLineEdit()
        region_form.addRow('Bed wall patch names (space separated)',self.bed_patches)
        self.initial_velocity = QComboBox(); self.initial_velocity.addItems(['zero','flow'])
        region_form.addRow('Initial particle velocity',self.initial_velocity)
        self.spins = {}
        for name, label, minimum, maximum in [
            ('rim_elevation','Scour rim elevation (center crossing)',-1e9,1e9),
            ('mobilization_diameters','Mobilization displacement / diameter',1e-6,1e6),
            ('min_water_fraction','Minimum initial sphere water fraction',.001,1)]:
            spin = number(1,minimum,maximum)
            self.spins[name] = spin
            region_form.addRow(label,spin)
        replay = QGroupBox('Carrier replay and time controls')
        replay_form = QFormLayout(replay); layout.addWidget(replay)
        self.frozen_flow = QCheckBox('Use one saved snapshot for the entire run')
        replay_form.addRow('Frozen flow',self.frozen_flow)
        self.alpha_field = QLineEdit()
        replay_form.addRow('Water fraction field (none = fully submerged)',self.alpha_field)
        for name, label, minimum in [('flow_time','Frozen snapshot time (s)',0),
                ('start_time','Tracking start time (s)',0),('end_time','Tracking end time (s)',1e-6),
                ('output_interval','Track output interval (s)',1e-6),('max_delta_t','Maximum integration step (s)',1e-7)]:
            spin = number(1,minimum,1e9,7)
            self.spins[name] = spin
            replay_form.addRow(label,spin)
        physics = QGroupBox('Physical properties and contact')
        physics_form = QFormLayout(physics); layout.addWidget(physics)
        for name, label, minimum, maximum in [
            ('rho_water','Water density (kg/m³)',1e-6,1e6),('mu_water','Water dynamic viscosity (Pa s)',1e-9,1e3),
            ('rho_air','Air density (kg/m³)',1e-6,1e6),('mu_air','Air dynamic viscosity (Pa s)',1e-9,1e3),
            ('added_mass','Added mass coefficient',0,10),('restitution','Contact restitution',0,1),
            ('friction','Coulomb friction coefficient',0,10)]:
            spin = number(1,minimum,maximum,9)
            self.spins[name] = spin
            physics_form.addRow(label,spin)
        self.collisions = QCheckBox('Enable particle–particle sediment contact')
        physics_form.addRow(self.collisions)
        self.collision_mode = QComboBox()
        self.collision_mode.addItem('All particle sizes','all')
        self.collision_mode.addItem('Both diameters above threshold','largeOnly')
        physics_form.addRow('Particle–particle contact mode',self.collision_mode)
        self.spins['collision_diameter_threshold'] = number(.03,1e-9,1e9,9)
        physics_form.addRow('Pair-contact diameter threshold (m)',self.spins['collision_diameter_threshold'])
        self.collision_note = QLabel('In threshold mode, both particles must have diameter > threshold. '
            'Uses inelastic normal contact and Coulomb friction with rotation. '
            'All particles retain fluid forces and bed/wall contact.')
        self.collision_note.setWordWrap(True)
        physics_form.addRow(self.collision_note)
        self.collisions.toggled.connect(self.update_contact_fields)
        self.collision_mode.currentIndexChanged.connect(self.update_contact_fields)
        self.volume_samples = QSpinBox(); self.volume_samples.setRange(32,8192)
        physics_form.addRow('Volume quadrature samples per sphere',self.volume_samples)
        for label, callback in [('Save tracer settings',self.save),('Run largeTracerFoam',self.run),
                                ('Plot trajectories and particle outcomes',self.plot),
                                ('Generate free-surface scour demo in selected EMPTY folder',self.demo),
                                ('Mesh and initialize scour demo',self.mesh_demo)]:
            button = QPushButton(label); button.clicked.connect(callback); layout.addWidget(button)
        self.status = QLabel('Select an existing carrier case, or an empty folder for the demo.')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.load_config(self._config)

    def update_contact_fields(self):
        self.collision_mode.setEnabled(self.collisions.isChecked())
        self.spins['collision_diameter_threshold'].setEnabled(
            self.collisions.isChecked() and self.collision_mode.currentData()=='largeOnly')

    def load_config(self, config):
        self._config = config
        for name in ('run_name','alpha_field'): getattr(self,name).setText(getattr(config,name))
        self.bed_patches.setText(' '.join(config.bed_patches))
        for name in ('count','seed','volume_samples'): getattr(self,name).setValue(getattr(config,name))
        for name in ('frozen_flow','collisions'): getattr(self,name).setChecked(getattr(config,name))
        self.collision_mode.setCurrentIndex(self.collision_mode.findData(config.collision_mode))
        for name in ('placement','initial_velocity'): getattr(self,name).setCurrentText(getattr(config,name))
        for name in ('diameter','density'): getattr(self,name).load(getattr(config,name))
        for name, spins in self.boxes.items():
            for spin, value in zip(spins,getattr(config,name)): spin.setValue(value)
        for name, spin in self.spins.items(): spin.setValue(getattr(config,name))
        self.update_contact_fields()

    def config(self):
        result = replace(self._config,
            run_name=self.run_name.text().strip(),alpha_field=self.alpha_field.text().strip(),
            count=self.count.value(),seed=self.seed.value(),volume_samples=self.volume_samples.value(),
            diameter=self.diameter.config(),density=self.density.config(),
            placement=self.placement.currentText(),bed_patches=tuple(self.bed_patches.text().split()),
            initial_velocity=self.initial_velocity.currentText(),frozen_flow=self.frozen_flow.isChecked(),
            collisions=self.collisions.isChecked(),collision_mode=self.collision_mode.currentData(),
            **{n:tuple(s.value() for s in spins) for n,spins in self.boxes.items()},
            **{n:s.value() for n,s in self.spins.items()})
        result.validate()
        return result

    def set_case_directory(self,path):
        self.case_dir = path
        self.load_config(TracerConfig())
        if (Path(path)/'tracers.json').is_file():
            try:
                self.load_config(load_tracers(path))
                self.status.setText('Tracer settings loaded. Use a new run name for each replay.')
            except (OSError,ValueError,TypeError,KeyError) as exc:
                self.status.setText(f'Could not load tracer settings: {exc}')
        else:
            self.status.setText('Set the seed region and carrier times for this case before tracking.')

    def save(self):
        try:
            config = self.config()
            backup = save_tracers(self.case_dir,config)
            self._config = config
            self.status.setText('Saved constant/largeTracerProperties and tracers.json.'+
                                (f' Previous settings backed up in {backup}.' if backup else ''))
            return True
        except (OSError,ValueError,TypeError) as exc:
            QMessageBox.warning(self,'Large tracers',str(exc))
            return False

    def run(self):
        try:
            preflight(self.case_dir,self.config())
        except (OSError,ValueError,TypeError) as exc:
            QMessageBox.warning(self,'Tracer carrier check',str(exc)); return
        if self.save(): self.command_requested.emit(['largeTracerFoam'],'large_tracers')

    def plot(self):
        try:
            config = self.config()
            run = Path(self.case_dir)/'postProcessing/largeTracers'/config.run_name
            if not (run/'complete').is_file():
                raise ValueError('Complete the selected tracer run before plotting it.')
            self.command_requested.emit([sys.executable,str(Path(__file__).with_name('visualize_tracers.py')),
                str(run),'--carrier','--show'],'tracer_plot')
        except (OSError,ValueError,TypeError) as exc:
            QMessageBox.warning(self,'Tracer visualization',str(exc))

    def demo(self):
        try:
            path = generate_demo(self.case_dir)
            self.case_prepared.emit(str(path),'interFoam')
            self.status.setText('Free-surface scour demo generated. Mesh, then run interFoam in Execution; then run tracers.')
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'Scour demo',str(exc))

    def mesh_demo(self):
        if not (Path(self.case_dir)/'scour-demo.json').is_file():
            QMessageBox.warning(self,'Scour demo','Select a generated scour demo first.'); return
        self.command_requested.emit(['sh','./Allmesh'],'scour_mesh')
