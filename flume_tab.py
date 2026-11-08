"""Workbench controls for a reproducible castle flume."""
import json
from pathlib import Path

from PyQt6.QtCore import pyqtSignal, QByteArray
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel,
                             QDoubleSpinBox, QComboBox, QPushButton, QMessageBox, QScrollArea)
from castle_flume import FlumeConfig, generate_case, geometry_svg


class FlumeTab(QWidget):
    case_prepared = pyqtSignal(str, str)
    mesh_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.case_dir = ''
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        root.addWidget(scroll)
        info = QLabel('Castle flume: 3 × 0.25 × 1 m. Centered 1 × 0.1 × 0.75 m castle, '
                      'with broad profile facing the side wall, a round arch and 75 mm side gaps.\n'
                      'Uses k–ω SST with water/air VOF at 0.5 m depth and a filled flume at 1 m. '
                      'The 0.5 m case includes the air above the water and a moving free surface.\n'
                      'Generate into an empty folder, then mesh and check before running. '
                      'See docs/castle-flume.md for assumptions and refinement settings.')
        info.setWordWrap(True)
        layout.addWidget(info)
        self.preview = QSvgWidget()
        self.preview.setMinimumHeight(200)
        self.preview.load(QByteArray(geometry_svg().encode()))
        layout.addWidget(self.preview)
        form = QFormLayout()
        layout.addLayout(form)
        self.velocity = QDoubleSpinBox()
        self.velocity.setDecimals(3)
        self.velocity.setRange(.1,1)
        self.velocity.setSingleStep(.1)
        self.velocity.setValue(.3)
        form.addRow('Mean inlet speed (m/s)',self.velocity)
        self.depth = QComboBox()
        self.depth.addItems(['0.5','1'])
        self.depth.currentTextChanged.connect(
            lambda depth: self.preview.load(QByteArray(geometry_svg(float(depth)).encode())))
        form.addRow('Water depth (m)',self.depth)
        self.settings = {}
        for name,label,minimum,maximum,decimals in [
            ('diameter','Grain diameter (m)',.000001,.1,6),
            ('inlet_concentration','Inlet concentration (kg/m³)',0,100,4),
            ('bed_inventory','Initial bed inventory (kg/m²)',0,10000,4),
            ('erosion_rate','Calibrated erosion rate (kg/m²/s)',0,10,8),
            ('end_time','Duration (s)',.001,100000,3)]:
            spin = QDoubleSpinBox()
            spin.setDecimals(decimals)
            spin.setRange(minimum,maximum)
            spin.setValue(getattr(FlumeConfig(),name))
            self.settings[name] = spin
            form.addRow(label,spin)
        self.generate_button = QPushButton('Generate castle flume in selected EMPTY folder')
        self.generate_button.clicked.connect(self.generate)
        layout.addWidget(self.generate_button)
        self.mesh_button = QPushButton('Mesh castle flume and check mesh')
        self.mesh_button.setEnabled(False)
        self.mesh_button.clicked.connect(self.mesh_requested.emit)
        layout.addWidget(self.mesh_button)
        self.status = QLabel('Select an empty case folder at the top of the workbench.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()

    def set_case_directory(self,path):
        self.case_dir = path
        manifest = Path(path)/'flume.json'
        self.mesh_button.setEnabled(False)
        self.status.setText('Select an empty folder to generate a castle flume, or open an existing castle case.')
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text())
                if data['preset'] != 'castle-flume-v2':
                    raise ValueError('Unknown flume preset version.')
                config = FlumeConfig(**data['settings'])
                config.validate()
                self.velocity.setValue(config.velocity)
                self.depth.setCurrentText(f'{config.depth:g}')
                for name, spin in self.settings.items():
                    spin.setValue(getattr(config,name))
                self.mesh_button.setEnabled(True)
                self.status.setText('Castle flume loaded. Mesh and check before running.')
            except (OSError,ValueError,TypeError,KeyError) as exc:
                self.status.setText(f'Could not load castle flume settings: {exc}')

    def generate(self):
        try:
            config = FlumeConfig(velocity=self.velocity.value(),depth=float(self.depth.currentText()),
                                 **{name:spin.value() for name,spin in self.settings.items()})
            path = generate_case(self.case_dir,config)
            self.case_prepared.emit(str(path),config.solver)
            self.status.setText(f'Generated {path}. Mesh and check next; erosion requires calibration. '
                                'Existing case files are never replaced by this preset.')
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'Castle flume',str(exc))
