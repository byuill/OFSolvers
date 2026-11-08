"""Guided preprocessing for saved-flow particle tracking."""
from dataclasses import replace
from html import escape

from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QLabel,QTextBrowser,
                            QDoubleSpinBox,QComboBox,QPushButton,QMessageBox)

from tracer_preprocess import inspect_carrier,suggested_placement


class TracerPreprocessPanel(QWidget):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner; self.inspection=None
        layout=QVBoxLayout(self)
        info=QLabel('1. Mesh and initialize the carrier water domain.  2. Inspect its fields and properties. '
            '3. Set the particle population, seed region and particle boundaries. '
            '4. Validate initial placement before running transport. '
            'The carrier flow is solved separately; particle setup does not rewrite its flow boundaries.')
        info.setWordWrap(True); layout.addWidget(info)
        self.report=QTextBrowser(); layout.addWidget(self.report,1)
        form=QFormLayout(); layout.addLayout(form)
        self.phase=QComboBox()
        self.phase.addItem('Water/air free surface','freeSurface')
        self.phase.addItem('Filled single-phase liquid','filled')
        form.addRow('Carrier liquid domain',self.phase)
        self.water=QDoubleSpinBox(); self.water.setDecimals(8); self.water.setRange(-1e9,1e9); self.water.setValue(.5)
        form.addRow('Initial water elevation for seed suggestions (m)',self.water)
        self.preset=QComboBox()
        self.preset.addItem('Buoyant tracers in the wetted volume','buoyant')
        self.preset.addItem('Slightly denser tracers on bed contact surfaces','bed')
        form.addRow('Placement suggestion',self.preset)
        actions=[('Inspect / refresh carrier case',self.refresh),
                 ('Use carrier times, liquid properties and gravity',self.use_carrier),
                 ('Apply placement suggestion',self.apply_placement),
                 ('Open carrier flow boundary editor',owner.carrier_boundaries_requested.emit),
                 ('Validate and export initial placement (no transport)',owner.preview),
                 ('View latest initial-placement preview',owner.plot_preview)]
        for title,callback in actions:
            button=QPushButton(title); button.clicked.connect(lambda checked=False,fn=callback:fn()); layout.addWidget(button)

    def refresh(self):
        self.inspection=None
        if not self.owner.case_dir:
            self.report.setPlainText('Select a carrier case or an empty folder for a new scour demonstration.'); return
        try:
            i=inspect_carrier(self.owner.case_dir); self.inspection=i
            self.phase.setCurrentIndex(0 if i.alpha_fields or i.solver in ('interFoam','sedimentInterFoam') else 1)
            if i.water_level is not None: self.water.setValue(i.water_level)
            times=', '.join(f'{t:g}' for t in i.times[:6])
            if len(i.times)>6: times+=f' … {i.times[-1]:g}'
            bounds=f'{i.bounds[0]} to {i.bounds[1]}' if i.bounds else 'Set seed coordinates manually'
            summary=[('Carrier solver',i.solver),('Mesh', 'Present' if i.meshed else 'Mesh and initialize before preview'),
                     ('Saved U snapshots',f'{len(i.times)}: {times}'),('Water-fraction fields',', '.join(i.alpha_fields) or 'None found'),
                     ('Block extents (m)',bounds)]
            html='<table>'+''.join(f'<tr><th align="left">{escape(k)}</th><td>{escape(str(v))}</td></tr>' for k,v in summary)+'</table>'
            if len(i.times)==1:
                html+='<p>One snapshot supports initial-placement preview. Transient transport needs saved fields covering its full interval; frozen-flow mode is an explicit alternative.</p>'
            html+='<p>'+'<br>'.join(escape(n) for n in i.notes)+'</p>'
            self.report.setHtml(html)
        except (OSError,ValueError,TypeError) as exc:
            self.report.setPlainText(f'Carrier inspection unavailable: {exc}')

    def use_carrier(self):
        try:
            i=self.inspection
            if i is None: raise ValueError('Inspect a complete carrier case first.')
            cfg=self.owner.config()
            updates=dict(i.properties)
            if self.phase.currentData()=='freeSurface':
                if 'alpha.water' in i.alpha_fields: updates['alpha_field']='alpha.water'
                elif cfg.alpha_field in i.alpha_fields: updates['alpha_field']=cfg.alpha_field
                else: raise ValueError('Set the actual liquid-fraction field in Particle settings. '
                    'Available fields: '+(', '.join(i.alpha_fields) or 'none; initialize the carrier first')+
                    '. An air fraction cannot be used as a water fraction.')
            else: updates['alpha_field']='none'
            if i.times:
                updates.update(start_time=i.times[0],flow_time=i.times[0],
                    end_time=i.times[-1] if len(i.times)>1 else max(cfg.end_time,i.times[0]+1))
            self.owner.load_config(replace(cfg,**updates))
            self.owner.status.setText('Imported carrier properties and time range. Review liquid phase, seed region and particle boundaries before preview.')
        except (OSError,ValueError,TypeError) as exc:
            QMessageBox.warning(self,'Particle preprocessing',str(exc))

    def apply_placement(self):
        try:
            if self.inspection is None: raise ValueError('Inspect the carrier case first.')
            cfg=suggested_placement(self.owner.config(),self.inspection,self.preset.currentData(),self.water.value())
            self.owner.load_config(cfg)
            self.owner.status.setText('Placement suggestion applied. Review the seed box and set the physical scour retention box/rim in Particle settings; preview checks the actual wetted mesh.')
        except (OSError,ValueError,TypeError) as exc:
            QMessageBox.warning(self,'Particle placement',str(exc))
