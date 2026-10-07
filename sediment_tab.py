"""River sediment setup, backed by validated case writers."""
from pathlib import Path
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel, QComboBox,
    QPushButton, QDoubleSpinBox, QSpinBox, QCheckBox, QGroupBox, QTabWidget,
    QListWidget, QAbstractItemView, QScrollArea, QMessageBox)

from foam_io import value, scalar, tokens, entries
from sediment_setup import TEMPLATES, create_starter, case_patches, configure_case


class SedimentTab(QWidget):
    solver_changed = pyqtSignal(str)
    case_prepared = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.case_dir = ''
        self.inputs = {}
        layout = QVBoxLayout(self)
        intro = QLabel('River sediment setup: fixed water domain and fixed bed elevation. '
                       'Sediment does not feed back on the flow. Configure water-flow boundaries in the Boundary tab.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.model_combo = QComboBox()
        self.model_combo.addItems(TEMPLATES)
        self.model_combo.currentTextChanged.connect(self._model_changed)
        layout.addWidget(self.model_combo)
        self.btn_starter = QPushButton('Create runnable starter case in the selected EMPTY folder')
        self.btn_starter.clicked.connect(self.create_starter_case)
        layout.addWidget(self.btn_starter)
        self.pages = QTabWidget()
        layout.addWidget(self.pages, 1)
        general = self._page('Water, sediment and patches')
        self.inlet_combo = QComboBox(); self.outlet_combo = QComboBox()
        general.addRow('Sediment inlet patch:', self.inlet_combo)
        general.addRow('Sediment outlet patch:', self.outlet_combo)
        self.bed_list = QListWidget()
        self.bed_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.bed_list.setMaximumHeight(130)
        general.addRow('Bed walls (select one or more):', self.bed_list)
        refresh = QPushButton('Refresh actual case patch names')
        refresh.clicked.connect(self.refresh_patches)
        general.addRow(refresh)
        self._number(general, 'diameterMM', 'Grain diameter (mm):', .125, decimals=6)
        self._number(general, 'rhoParticle', 'Sediment density (kg/m³):', 2650)
        self._number(general, 'rhoFluid', 'Water density (kg/m³):', 1000)
        self._number(general, 'nu', 'Water kinematic viscosity (m²/s):', 1e-6, decimals=10)
        self.update_fluid = QCheckBox('Also update initial U and p internal fields in 0/')
        general.addRow(self.update_fluid)
        for axis, default in zip('XYZ', (.5,0,0)):
            self._number(general, 'initialU'+axis, 'Initial U '+axis+' (m/s):', default, minimum=-1e6)
        self._number(general, 'initialP', 'Initial kinematic pressure (m²/s²):', 0, minimum=-1e9)
        euler = self._page('Concentration and bed inventory')
        self._number(euler, 'initialC', 'Initial suspended concentration (kg/m³):', 0)
        self._number(euler, 'inletC', 'Incoming concentration (kg/m³):', .1)
        self._number(euler, 'initialBed', 'Initial bed inventory (kg/m²):', .01)
        self._choice(euler, 'settlingModel', 'Settling model:', ['FergusonChurch','constant'])
        self._number(euler, 'ws', 'Constant settling speed (m/s):', .01106)
        self._number(euler, 'ScT', 'Turbulent Schmidt number:', .7)
        self._number(euler, 'ScM', 'Molecular Schmidt number:', 1)
        self._number(euler, 'thetaCrit', 'Critical Shields parameter:', .05)
        self._number(euler, 'erosionRate', 'Erosion scale (kg/m²/s; 0 disables):', 0, decimals=10)
        self._number(euler, 'erosionExponent', 'Excess-Shields exponent:', 1.5)
        note = QLabel('Initial C and bed storage are uniform. Inlet/reversal uses phiSed. '
                      'Selected bed walls get mixed C and finite fixed-value Mbed. '
                      'Other openings get zero incoming sediment; other walls are impermeable.')
        note.setWordWrap(True); euler.addRow(note)
        parcel = self._page('Parcel inlet and resuspension')
        self._number(parcel, 'feedRate', 'Total inlet sediment feed (kg/s):', .0001, decimals=10)
        count = QSpinBox(); count.setRange(1,10000000); count.setValue(1000)
        self.inputs['parcelsPerSecond'] = count
        parcel.addRow('Computational parcels per second:', count)
        self._number(parcel, 'SOI', 'Feed starts at (s):', 0)
        self._number(parcel, 'duration', 'Feed duration (s):', 1)
        self.feed_mass = QLabel()
        parcel.addRow('Prescribed total feed:', self.feed_mass)
        self.inputs['feedRate'].valueChanged.connect(self._update_feed_mass)
        self.inputs['duration'].valueChanged.connect(self._update_feed_mass)
        self._choice(parcel, 'profile', 'Vertical feed distribution:', ['Rouse','uniform'])
        for axis, default in zip('XYZ', (0,.05,0)):
            self._number(parcel, 'origin'+axis, 'Transect bed origin '+axis+' (m):', default, minimum=-1e6)
        for axis, default in zip('XYZ', (0,1,0)):
            self._number(parcel, 'widthDirection'+axis, 'Width direction '+axis+':', default, minimum=-1e6)
        self._number(parcel, 'width', 'Transect width (m):', .04)
        self._number(parcel, 'depth', 'Water depth above origin (m):', .1)
        self._number(parcel, 'referenceHeight', 'Lower feed height above origin (m):', .005)
        self._number(parcel, 'maximumHeight', 'Upper feed height above origin (m):', .095)
        self._choice(parcel, 'shearVelocityModel', 'Rouse shear estimate:', ['localBed','constant'])
        self.shear_bed_combo = QComboBox()
        parcel.addRow('Rouse shear reference bed:', self.shear_bed_combo)
        self._number(parcel, 'uStar', 'Prescribed shear velocity (m/s):', .02)
        self._number(parcel, 'shearSampleLength', 'Local bed shear sampling length (m):', .2)
        self.resuspension = QCheckBox('Enable resuspension of deposited parcels')
        parcel.addRow(self.resuspension)
        self._choice(parcel, 'thresholdModel', 'Resuspension threshold:', ['Shields','shearStress','nearBedVelocity'])
        self._number(parcel, 'critical', 'Critical value:', .05)
        self.threshold_units = QLabel('Critical value is a dimensionless Shields parameter.')
        parcel.addRow(self.threshold_units)
        self.inputs['thresholdModel'].currentTextChanged.connect(self._threshold_changed)
        self._number(parcel, 'releaseRate', 'Release-rate scale (1/s):', 1)
        self._number(parcel, 'releaseExponent', 'Release exponent:', 1.5)
        self._number(parcel, 'minimumRestTime', 'Minimum bed residence (s):', .1)
        self._number(parcel, 'normalLaunchSpeed', 'Normal launch speed into water (m/s):', .02)
        self._number(parcel, 'liftDiameters', 'Initial lift (particle diameters):', 2)
        self._number(parcel, 'liftHeight', 'Override lift (m; 0 uses diameters):', 0, decimals=10)
        note = QLabel('Requires a 3-D mesh and a rectangular transect fully on a vertical inlet. '
                      'Origin is at bed elevation, not the feed midpoint. Gravity defines up. '
                      'Selected beds stick; other walls rebound; other open patches escape. '
                      'The shear reference must be one of the selected bed walls. '
                      'New cases start without parcels; the inlet feed introduces them.')
        note.setWordWrap(True); parcel.addRow(note)
        self.status = QLabel('Select an existing case or an empty folder for a new starter.')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.btn_write = QPushButton('Validate and write sediment properties and initial conditions')
        self.btn_write.clicked.connect(self.write_setup)
        layout.addWidget(self.btn_write)
        self.inputs['settlingModel'].currentTextChanged.connect(lambda name:self.inputs['ws'].setEnabled(name=='constant'))
        self.inputs['ws'].setEnabled(False)
        self.inputs['shearVelocityModel'].currentTextChanged.connect(lambda name:self.inputs['uStar'].setEnabled(name=='constant'))
        self.inputs['uStar'].setEnabled(False)
        self.resuspension.toggled.connect(self._resuspension_toggled)
        self._resuspension_toggled(False)
        self._model_changed(self.model_combo.currentText())
        self._update_feed_mass()
        self.defaults = {name: widget.currentText() if isinstance(widget,QComboBox) else widget.value()
                         for name,widget in self.inputs.items()}

    def _page(self, title):
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        widget = QWidget(); form = QFormLayout(widget); scroll.setWidget(widget)
        self.pages.addTab(scroll, title)
        return form

    def _number(self, form, key, label, default, minimum=0, decimals=8):
        spin = QDoubleSpinBox(); spin.setDecimals(decimals)
        spin.setRange(minimum,1e9); spin.setValue(default)
        self.inputs[key] = spin; form.addRow(label,spin)

    def _choice(self, form, key, label, options):
        combo = QComboBox(); combo.addItems(options)
        self.inputs[key] = combo; form.addRow(label,combo)

    def _model_changed(self, name):
        if not hasattr(self,'pages') or self.pages.count() < 3:
            return
        self.pages.setTabVisible(1, name == 'sedimentPimpleFoam')
        self.pages.setTabVisible(2, name == 'sandParcelPimpleFoam')
        self.solver_changed.emit(name)

    def set_solver(self, name):
        if name in TEMPLATES:
            self.model_combo.setCurrentText(name)

    def _update_feed_mass(self):
        mass = self.inputs['feedRate'].value()*self.inputs['duration'].value()
        self.feed_mass.setText(f'{mass:.6g} kg (rate × duration)')

    def _threshold_changed(self, name):
        self.threshold_units.setText({'Shields':'Dimensionless critical Shields parameter.',
            'shearStress':'Critical bed shear stress in Pa.',
            'nearBedVelocity':'Critical tangential bed-owner-cell speed in m/s (mesh dependent).'}[name])

    def _resuspension_toggled(self, enabled):
        for name in ['thresholdModel','critical','releaseRate','releaseExponent','minimumRestTime',
                     'normalLaunchSpeed','liftDiameters','liftHeight']:
            self.inputs[name].setEnabled(enabled)

    def set_case_directory(self, path):
        self.case_dir = path
        self.inlet_combo.clear(); self.outlet_combo.clear(); self.bed_list.clear(); self.shear_bed_combo.clear()
        self.loaded_inlet = None; self.loaded_beds = None; self.loaded_shear_bed = None
        for name,default in self.defaults.items():
            widget = self.inputs[name]
            if isinstance(widget,QComboBox): widget.setCurrentText(default)
            else: widget.setValue(default)
        self.resuspension.setChecked(False)
        self.update_fluid.setChecked(False)
        self.status.setText('Select a complete sediment case or create a starter in this empty folder.')
        if (Path(path)/'system/controlDict').exists():
            self.load_case_settings()
            self.refresh_patches(show_errors=False)

    def refresh_patches(self, _checked=False, show_errors=True):
        try:
            patches = case_patches(self.case_dir)
            previous_inlet = self.inlet_combo.currentText()
            previous_outlet = self.outlet_combo.currentText()
            previous_beds = {i.text() for i in self.bed_list.selectedItems()}
            self.inlet_combo.clear(); self.outlet_combo.clear(); self.bed_list.clear()
            openings = [p['name'] for p in patches if p['type'] == 'patch']
            self.inlet_combo.addItems(openings); self.outlet_combo.addItems(openings)
            self.bed_list.addItems([p['name'] for p in patches if p['type'] == 'wall'])
            previous_shear = self.shear_bed_combo.currentText()
            self.shear_bed_combo.clear()
            self.shear_bed_combo.addItems([p['name'] for p in patches if p['type'] == 'wall'])
            self.shear_bed_combo.setCurrentText(getattr(self,'loaded_shear_bed',None) or previous_shear or 'bed')
            desired_inlet = getattr(self,'loaded_inlet',None) or previous_inlet
            desired_outlet = previous_outlet
            for combo, desired, guesses in [(self.inlet_combo,desired_inlet,['upstream','inlet','xMin']),
                                            (self.outlet_combo,desired_outlet,['downstream','outlet','xMax'])]:
                choices = [combo.itemText(i) for i in range(combo.count())]
                chosen = desired if desired in choices else next((g for g in guesses if g in choices),None)
                if chosen: combo.setCurrentText(chosen)
            beds = getattr(self,'loaded_beds',None) or previous_beds or {'bed','bottom','terrain','zMin'}
            for i in range(self.bed_list.count()):
                item = self.bed_list.item(i); item.setSelected(item.text() in beds)
            self.loaded_inlet = None; self.loaded_beds = None
        except (OSError,ValueError) as exc:
            self.status.setText('Cannot load patches: '+str(exc))
            if show_errors: QMessageBox.warning(self,'Case patches',str(exc))

    def configuration(self):
        config = {name: widget.currentText() if isinstance(widget,QComboBox) else widget.value()
                  for name,widget in self.inputs.items()}
        config['solver'] = self.model_combo.currentText()
        config['diameter'] = config.pop('diameterMM')/1000
        config['inlet'] = self.inlet_combo.currentText(); config['outlet'] = self.outlet_combo.currentText()
        config['beds'] = [i.text() for i in self.bed_list.selectedItems()]
        config['shearBed'] = self.shear_bed_combo.currentText()
        config['origin'] = tuple(config.pop('origin'+a) for a in 'XYZ')
        config['widthDirection'] = tuple(config.pop('widthDirection'+a) for a in 'XYZ')
        config['initialU'] = tuple(config.pop('initialU'+a) for a in 'XYZ')
        config['resuspension'] = self.resuspension.isChecked()
        config['updateFluidInitial'] = self.update_fluid.isChecked()
        return config

    def create_starter_case(self):
        try:
            solver = self.model_combo.currentText()
            path = create_starter(self.case_dir,solver)
            self.case_prepared.emit(str(path),solver)
            self.status.setText('Starter created. Configure sediment and flow, then run blockMesh and QAQC in Execution.')
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'Starter case',str(exc))

    def write_setup(self):
        try:
            contents,backup = configure_case(self.case_dir,self.configuration())
            self.status.setText('Saved: '+', '.join(contents)+ (f'\nPrevious files: {backup}' if backup else ''))
            QMessageBox.information(self,'Sediment setup saved',
                'Saved sediment settings and initial fields in the selected case.\n'
                'Flow boundaries and turbulence remain separately configured. Run case/mesh QAQC before solving.\n'
                'These edits apply to 0/; existing saved restart inventories/clouds are retained.')
        except (OSError,ValueError) as exc:
            self.status.setText('Not saved: '+str(exc))
            QMessageBox.warning(self,'Sediment validation',str(exc))

    def load_case_settings(self):
        """Restore explicit scalar settings so selecting a case does not reset them."""
        try:
            case = Path(self.case_dir)
            solver = value((case/'system/controlDict').read_text(),'application')
            self.set_solver(solver)
            if solver not in TEMPLATES:
                self.status.setText('Select a sediment starter or a complete sediment solver case.')
                return
            mappings = {}
            if solver == 'sedimentPimpleFoam':
                text = (case/'constant/sedimentProperties').read_text()
                mappings = {'diameterMM':('d50',1000),'rhoParticle':('rhoSediment',1), 'rhoFluid':('rhoFluid',1),
                            'nu':('settlingNu',1), **{k:(k,1) for k in ['ScT','ScM','thetaCrit','erosionRate','erosionExponent']}}
                self.loaded_beds = value(text,'bedPatches').strip('()').split()
                self.inputs['settlingModel'].setCurrentText(value(text,'settlingModel'))
                if 'ws' in entries(text): self.inputs['ws'].setValue(scalar(text,'ws'))
                for name,key in [('C','initialC')]:
                    internal = value((case/'0'/name).read_text(),'internalField').split()
                    if len(internal)==2 and internal[0]=='uniform': self.inputs[key].setValue(float(internal[1]))
                fields = value((case/'0/Mbed').read_text(),'boundaryField')[1:-1]
                if self.loaded_beds:
                    bed = value(fields,self.loaded_beds[0])[1:-1]
                    uniform = value(bed,'value').split()
                    if len(uniform)==2 and uniform[0]=='uniform': self.inputs['initialBed'].setValue(float(uniform[1]))
                concentration = value((case/'0/C').read_text(),'boundaryField')[1:-1]
                for name,item in entries(concentration).items():
                    patch = concentration[item.value_start+1:item.value_end-1]
                    if 'inletValue' in entries(patch):
                        uniform = value(patch,'inletValue').split()
                        if len(uniform)==2 and uniform[0]=='uniform' and float(uniform[1]) > 0:
                            self.loaded_inlet = name
                            self.inputs['inletC'].setValue(float(uniform[1]))
                            break
            else:
                cloud = (case/'constant/sandCloudProperties').read_text()
                text = value(value(value(cloud,'subModels')[1:-1],'injectionModels')[1:-1],'feed')[1:-1]
                mappings = {'diameterMM':('d50',1000),'feedRate':('massFeedRate',1), **{k:(k,1) for k in [
                    'parcelsPerSecond','SOI','duration','width','depth','referenceHeight','maximumHeight','shearSampleLength']}}
                self.loaded_inlet = value(text,'patch')
                self.loaded_beds = [value(text,'bedPatch')]
                self.loaded_shear_bed = self.loaded_beds[0]
                for key in ['profile','shearVelocityModel']:
                    self.inputs[key].setCurrentText(value(text,key))
                if value(text,'shearVelocityModel') == 'constant': self.inputs['uStar'].setValue(scalar(text,'uStar'))
                for key in ['origin','widthDirection']:
                    vector = [float(t[0]) for t in tokens(value(text,key)) if t[0] not in '()']
                    for axis, number in zip('XYZ',vector): self.inputs[key+axis].setValue(number)
                properties = value(cloud,'constantProperties')[1:-1]
                self.inputs['rhoParticle'].setValue(scalar(properties,'rho0'))
                fluid = (case/'constant/transportProperties').read_text()
                for key in ['nu','rho']:
                    number = float(value(fluid,key).split()[-1])
                    self.inputs['nu' if key=='nu' else 'rhoFluid'].setValue(number)
                if 'resuspension' in entries(cloud):
                    resume = value(cloud,'resuspension')[1:-1]
                    self.resuspension.setChecked(value(resume,'enabled') in ('true','yes','on','1'))
                    self.loaded_beds = value(resume,'bedPatches').strip('()').split()
                    threshold = value(resume,'thresholdModel')
                    self.inputs['thresholdModel'].setCurrentText(threshold)
                    critical = {'Shields':'criticalShields','shearStress':'criticalShearStress','nearBedVelocity':'criticalVelocity'}[threshold]
                    self.inputs['critical'].setValue(scalar(resume,critical))
                    for key in ['releaseRate','releaseExponent','minimumRestTime','normalLaunchSpeed','liftDiameters','liftHeight']:
                        self.inputs[key].setValue(scalar(resume,key))
            for name in ['U','p']:
                internal = value((case/'0'/name).read_text(),'internalField')
                components = [t[0] for t in tokens(internal) if t[0] not in '()']
                if components[0] == 'uniform':
                    if name == 'U' and len(components) == 4:
                        for axis,number in zip('XYZ',components[1:]): self.inputs['initialU'+axis].setValue(float(number))
                    elif name == 'p' and len(components) == 2:
                        self.inputs['initialP'].setValue(float(components[1]))
            for widget,(name,multiplier) in mappings.items():
                number = scalar(text,name)*multiplier
                self.inputs[widget].setValue(int(number) if isinstance(self.inputs[widget],QSpinBox) else number)
            self.status.setText('Loaded sediment case settings. Initial-field writes affect 0/, not saved restart states.')
        except (OSError,ValueError,KeyError) as exc:
            self.status.setText('Some settings need review before saving: '+str(exc))
