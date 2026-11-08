"""Particle boundary rules, distinct from carrier U/pressure/alpha conditions."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,
    QComboBox,QDoubleSpinBox,QCheckBox,QHeaderView)

from large_tracer import ParticleBoundary


class ParticleBoundaryEditor(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.rows={}
        self.defaults=(.1,.4)
        layout=QVBoxLayout(self)
        info=QLabel('Particle boundaries use finite-radius contact or removal when the center leaves the domain. '
            'Contact supports sliding, rolling and rebound through restitution and friction. '
            'Mesh defaults are contact on walls and escape on open patches. '
            'These settings do not edit the carrier velocity, pressure or water-fraction boundaries. '
            'Select contact surfaces here for bed initialization.')
        info.setWordWrap(True); layout.addWidget(info)
        self.table=QTableWidget(0,6)
        self.table.setHorizontalHeaderLabels(['Patch','Mesh type','Particle action','Restitution','Friction','Seed surface'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
        self.note=QLabel(); self.note.setWordWrap(True); layout.addWidget(self.note)

    def load(self,patches,config):
        self.defaults=(config.restitution,config.friction)
        self.rows={}; self.table.setRowCount(0)
        kinds={p['name']:p['type'] for p in patches}
        for name in set(config.boundaries) | set(config.bed_patches):
            kinds.setdefault(name,'MISSING')
        for name,kind in kinds.items():
            index=self.table.rowCount(); self.table.insertRow(index)
            for col,text in enumerate((name,kind)):
                item=QTableWidgetItem(text); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(index,col,item)
            action=QComboBox()
            for label,data in [('Mesh default','auto'),('Contact / rebound','contact'),('Escape','escape')]: action.addItem(label,data)
            e_own=QCheckBox('Set'); mu_own=QCheckBox('Set')
            e=QDoubleSpinBox(); e.setDecimals(12); e.setRange(0,1)
            mu=QDoubleSpinBox(); mu.setDecimals(12); mu.setRange(0,1e9)
            seed=QCheckBox()
            row=dict(kind=kind,action=action,e_own=e_own,mu_own=mu_own,e=e,mu=mu,seed=seed)
            self.rows[name]=row
            rule=config.boundaries.get(name)
            if rule: action.setCurrentIndex(action.findData(rule.behavior))
            e_own.setChecked(bool(rule and rule.restitution is not None))
            mu_own.setChecked(bool(rule and rule.friction is not None))
            e.setValue(rule.restitution if rule and rule.restitution is not None else config.restitution)
            mu.setValue(rule.friction if rule and rule.friction is not None else config.friction)
            seed.setChecked(name in config.bed_patches)
            self.table.setCellWidget(index,2,action)
            for col,check,spin in ((3,e_own,e),(4,mu_own,mu)):
                cell=QWidget(); box=QHBoxLayout(cell); box.setContentsMargins(0,0,0,0)
                box.addWidget(check); box.addWidget(spin); self.table.setCellWidget(index,col,cell)
                check.setToolTip('Unchecked: inherit the global particle contact coefficient.')
            self.table.setCellWidget(index,5,seed)
            action.currentIndexChanged.connect(lambda _,n=name:self.update_row(n))
            e_own.toggled.connect(lambda _,n=name:self.update_row(n))
            mu_own.toggled.connect(lambda _,n=name:self.update_row(n))
            self.update_row(name)
        self.note.setText('Missing saved patches: '+', '.join(n for n,r in self.rows.items() if r['kind']=='MISSING')
                          if any(r['kind']=='MISSING' for r in self.rows.values()) else
                          'Pair-contact size thresholds do not disable boundary contact for smaller particles.')

    def update_defaults(self,e,mu):
        self.defaults=(e,mu)
        for name in self.rows: self.update_row(name)

    def update_row(self,name):
        row=self.rows[name]
        action=row['action'].currentData()
        contact=action=='contact' or action=='auto' and row['kind']=='wall'
        if not contact:
            row['e_own'].setChecked(False); row['mu_own'].setChecked(False); row['seed'].setChecked(False)
        for key,default in zip(('e','mu'),self.defaults):
            check=row[key+'_own']; spin=row[key]; check.setEnabled(contact)
            own=contact and check.isChecked()
            spin.setEnabled(own)
            if not own: spin.setValue(default)
        row['seed'].setEnabled(contact)

    def configuration(self):
        result={}; beds=[]
        for name,row in self.rows.items():
            action=row['action'].currentData()
            if row['seed'].isChecked(): beds.append(name)
            own=row['e_own'].isChecked() or row['mu_own'].isChecked()
            if action!='auto' or own:
                behavior=action if action!='auto' else 'contact'
                result[name]=ParticleBoundary(behavior,
                    row['e'].value() if row['e_own'].isChecked() else None,
                    row['mu'].value() if row['mu_own'].isChecked() else None)
        return result,tuple(beds)
