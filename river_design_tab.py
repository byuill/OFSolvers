"""Channel/obstacle generation, mesh advice and hydraulic boundary workflow."""
from dataclasses import fields
import csv
from pathlib import Path

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QScrollArea,
    QTabWidget,QDoubleSpinBox,QSpinBox,QComboBox,QPushButton,QLabel,QSplitter,
    QPlainTextEdit,QTableWidget,QTableWidgetItem,QFileDialog,QMessageBox,QCheckBox)
from pyvistaqt import QtInteractor
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from river_design import (Channel,MeshDesign,Hydraulics,Obstacle,RiverDesign,
                          analyze_design,channel_surfaces,obstacle_surfaces,bed_z,normal_depth)
from river_case import export_river_case,export_geometry,load_river_case,finalize_river_boundaries,DESIGN_FILE


class RiverDesignTab(QWidget):
    case_exported=pyqtSignal(str,str)
    mesh_requested=pyqtSignal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self.case_dir=''
        self.inputs={}
        layout=QVBoxLayout(self)
        note=QLabel('X downstream · Y across channel · Z up · SI metres. Fixed-domain cases use a rigid lid. '
                    'VOF mode exports geometry/meshing inputs for a separately completed native free-surface case.')
        note.setWordWrap(True);layout.addWidget(note)
        splitter=QSplitter(Qt.Orientation.Horizontal);layout.addWidget(splitter,1)
        controls=QWidget();controls_layout=QVBoxLayout(controls)
        self.pages=QTabWidget();controls_layout.addWidget(self.pages)
        self._channel_page();self._mesh_page();self._hydraulic_page();self._obstacle_page()
        actions=QHBoxLayout()
        preview=QPushButton('Preview and analyze');preview.clicked.connect(self.preview)
        export=QPushButton('Export STL files…');export.clicked.connect(self.export_stl)
        actions.addWidget(preview);actions.addWidget(export);controls_layout.addLayout(actions)
        self.create_button=QPushButton('Create / update unmeshed river case in active folder')
        self.create_button.clicked.connect(self.create_case);controls_layout.addWidget(self.create_button)
        self.mesh_button=QPushButton('Build river mesh and run native quality checks')
        self.mesh_button.clicked.connect(self.request_mesh);controls_layout.addWidget(self.mesh_button)
        self.boundary_button=QPushButton('Apply water / sediment boundaries to checked river mesh')
        self.boundary_button.clicked.connect(self.apply_boundaries);controls_layout.addWidget(self.boundary_button)
        self.status=QLabel('Select an empty active case folder, design the channel, then create and build the case.')
        self.status.setWordWrap(True);controls_layout.addWidget(self.status)
        splitter.addWidget(controls)
        visual=QWidget();visual_layout=QVBoxLayout(visual)
        self.preview_tabs=QTabWidget();visual_layout.addWidget(self.preview_tabs,2)
        profile=QWidget();profile_layout=QVBoxLayout(profile)
        self.station=QDoubleSpinBox();self.station.setRange(0,1);self.station.setDecimals(3);self.station.setValue(.5)
        self.station.setSingleStep(.05);self.station.valueChanged.connect(self.draw_profile)
        station_layout=QHBoxLayout();station_layout.addWidget(QLabel('Cross-section station X / reach length:'));station_layout.addWidget(self.station)
        profile_layout.addLayout(station_layout)
        self.profile_figure=Figure(figsize=(6,4),layout='constrained')
        self.profile_canvas=FigureCanvasQTAgg(self.profile_figure);profile_layout.addWidget(self.profile_canvas,1)
        self.preview_tabs.addTab(profile,'Design cross-section')
        view=QWidget();view_layout=QVBoxLayout(view)
        self.plotter=QtInteractor(view);view_layout.addWidget(self.plotter.interactor)
        self.preview_tabs.addTab(view,'3D terrain / obstacles')
        self.preview_design=None
        self.report=QPlainTextEdit();self.report.setReadOnly(True)
        visual_layout.addWidget(self.report,1)
        splitter.addWidget(visual);splitter.setSizes([560,700])

    def _page(self,title):
        widget=QWidget();form=QFormLayout(widget)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(widget)
        self.pages.addTab(scroll,title)
        return form

    def _number(self,form,group,key,label,value,minimum=0,maximum=1e8,decimals=5,integer=False):
        box=QSpinBox() if integer else QDoubleSpinBox()
        if not integer:box.setDecimals(decimals)
        box.setRange(minimum,maximum);box.setValue(value)
        self.inputs[(group,key)]=box;form.addRow(label,box)
        return box

    def _combo(self,form,group,key,label,choices,default):
        box=QComboBox()
        for title,value in choices:box.addItem(title,value)
        box.setCurrentIndex(box.findData(default));self.inputs[(group,key)]=box
        form.addRow(label,box)

    def _channel_page(self):
        form=self._page('Channel')
        self._combo(form,'channel','mode','Model domain:',[('Fixed water domain / rigid lid','fixed'),('Water + air geometry for VOF','vof')],'fixed')
        for key,label,value,minimum in [('length','Reach length (m):',40.,1),('width','Flat bed width (m):',6.,.1),
            ('depth','Design water depth (m):',1.2,.02),('bank_slope','Bank slope H:V (0 = vertical):',1.5,0),
            ('bed_slope','Bed fall S₀ (m/m):',.001,0),('bed_elevation','Upstream bed elevation (m):',0.,-1e6),
            ('freeboard','VOF air height above water (m):',.8,0)]:
            self._number(form,'channel',key,label,value,minimum)
        self._number(form,'channel','stations','Terrain stations along X:',81,3,2000,integer=True)
        self._number(form,'channel','cross_points','Terrain points across bed:',33,3,2000,integer=True)
        depth=QPushButton('Set design depth from reference Q and Manning estimate')
        depth.clicked.connect(self.use_normal_depth);form.addRow(depth)

    def _mesh_page(self):
        form=self._page('Mesh / walls')
        self._combo(form,'mesh','method','Meshing method:',[('Automatic','auto'),('Mapped, graded blockMesh','block'),('Surface / obstacle snappyHexMesh','snappy')],'auto')
        for key,label,value,minimum,maximum in [
            ('base_size','Bulk / background spacing (m):',.4,.001,1000),
            ('wall_size','Snappy bed/bank surface spacing (m):',.15,.0001,100),
            ('first_height','First cell / first layer thickness (m):',.01,.000001,10),
            ('growth','Adjacent cell / layer expansion:',1.2,1.001,1.3)]:
            self._number(form,'mesh',key,label,value,minimum,maximum,7)
        for key,label,value,minimum,maximum in [('layers','Requested snappy wall layers (0 = off):',4,0,12),
            ('cells_per_obstacle','Cells across minimum solid dimension:',10,6,50),
            ('max_level','Maximum octree refinement level:',6,0,8),('max_cells','Maximum cells:',2000000,1000,100000000)]:
            self._number(form,'mesh',key,label,value,minimum,maximum,integer=True)
        for key,label,value in [('bed_ks','Unresolved bed sand roughness Ks (m):',.002),
            ('bank_ks','Unresolved bank sand roughness Ks (m):',.002),('roughness_cs','Roughness coefficient Cs:',.5)]:
            self._number(form,'hydraulic',key,label,value,0,10,7)
        note=QLabel('Mapped meshes grade the entire depth/width and may reduce streamwise spacing to protect corner-cell quality. '
                    'Snappy layer thickness/coverage is a request, not a measured result. Ks is not Manning n and excludes resolved obstacles/bedforms.')
        note.setWordWrap(True);form.addRow(note)

    def _hydraulic_page(self):
        form=self._page('Flow / sediment')
        self._combo(form,'hydraulic','solver','Runnable fixed-domain solver:',[(s,s) for s in ['sedimentPimpleFoam','sandParcelPimpleFoam','pimpleFoam']],'sedimentPimpleFoam')
        self._combo(form,'hydraulic','turbulence','RANS closure:',[(s,s) for s in ['kOmegaSST','kEpsilon']],'kOmegaSST')
        self._combo(form,'hydraulic','parcel_profile','Parcel feed profile:',[(s,s) for s in ['Rouse','uniform']],'Rouse')
        for key,label,value,minimum,decimals in [
            ('discharge','Constant / reference water Q (m³/s):',3.,.000001,6),
            ('manning_n','Manning n (planning estimate only):',.03,.0001,6),
            ('nu','Water kinematic viscosity (m²/s):',1e-6,1e-9,10),
            ('intensity','Inlet turbulence intensity (fraction):',.05,.0001,6),
            ('length_scale','Inlet turbulence length scale (m):',.1,.00001,6),
            ('inlet_c','Incoming suspended C (kg/m³):',.05,0,6),
            ('initial_c','Initial suspended C (kg/m³):',0.,0,6),
            ('bed_inventory','Initial bed inventory (kg/m²):',1.,0,6),
            ('grain_mm','Sediment diameter d50 (mm):',.2,.0001,6),
            ('particle_density','Sediment density (kg/m³):',2650.,1,2),
            ('water_density','Water density (kg/m³):',1000.,1,2),
            ('parcel_lower_fraction','Lower parcel feed height / depth:',.01,.0001,5),
            ('parcel_upper_fraction','Upper parcel feed height / depth:',.9,.0001,5),
            ('end_time','Simulation duration (s):',100.,.001,4),
            ('max_co','Adaptive water Courant target:',.5,.001,5),
            ('outlet_pressure','Outlet kinematic pressure reference (m²/s²):',0.,-1e6,6)]:
            self._number(form,'hydraulic',key,label,value,minimum,1e8,decimals)
        self._number(form,'hydraulic','parcel_rate','Computational parcels / second:',1000,1,1000000,integer=True)
        self.use_hydrograph=QCheckBox('Use tabular water Q and incoming C (Eulerian/water only)')
        form.addRow(self.use_hydrograph)
        self.hydrograph=QTableWidget(0,3);self.hydrograph.setHorizontalHeaderLabels(['time_s','Q_m3_s','C_kg_m3'])
        self.hydrograph.setMinimumHeight(140);form.addRow(self.hydrograph)
        importer=QPushButton('Import hydrograph CSV…');importer.clicked.connect(self.import_hydrograph);form.addRow(importer)
        note=QLabel('Positive Q is downstream. Tabular times must start at zero and cover the run; reversal is not supported. '
                    'Fixed-domain p is a pressure reference, not a water-stage boundary. Parcel feed is constant Q × C; bed erosion/resuspension starts disabled.')
        note.setWordWrap(True);form.addRow(note)

    def _obstacle_page(self):
        page=QWidget();layout=QVBoxLayout(page)
        buttons=QHBoxLayout()
        for text,kind in [('Pier','cylinder'),('Rigid stems','vegetation'),('Boulder','boulder'),('Structure','box'),('Bedform','bedform')]:
            button=QPushButton('+ '+text);button.clicked.connect(lambda checked=False,k=kind:self.add_obstacle(k));buttons.addWidget(button)
        layout.addLayout(buttons)
        self.obstacles=QTableWidget(0,13)
        self.obstacles.setHorizontalHeaderLabels(['Name','Type','X (m)','Y (m)','Length (m)','Width / diameter (m)',
            'Height (m)','Yaw (°)','Ks (m)','Rows','Columns','Stem spacing (m)','Bank attached'])
        layout.addWidget(self.obstacles,1)
        remove=QPushButton('Remove selected obstacle');remove.clicked.connect(lambda:self.obstacles.removeRow(self.obstacles.currentRow()))
        layout.addWidget(remove)
        text=QLabel('Bedforms are smooth raised terrain patches; cylinders are rigid vegetation/stems, not porous or flexible plants. '
                    'Boxes may represent piers, groynes or low weirs. Explicitly mark bank attachment. '
                    'Use separate nonoverlapping solids, resolve gaps and provide enough inlet/outlet distance. Scroll horizontally for all properties.')
        text.setWordWrap(True);layout.addWidget(text)
        self.pages.addTab(page,'Obstacles')

    def add_obstacle(self,kind='cylinder',item=None):
        if item is None:
            length=self.inputs[('channel','length')].value();width=self.inputs[('channel','width')].value()
            item=Obstacle(name=kind+str(self.obstacles.rowCount()+1),kind=kind,x=length/2)
            if kind=='vegetation':item.width=.15;item.length=.15;item.height=1.5;item.rows=2;item.columns=3;item.spacing=.8
            if kind=='bedform':item.length=min(8.,length*.25);item.width=width*.6;item.height=.15
        row=self.obstacles.rowCount();self.obstacles.insertRow(row)
        values=[item.name,item.kind,item.x,item.y,item.length,item.width,item.height,item.angle,item.ks,item.rows,item.columns,item.spacing,str(item.attach_bank).lower()]
        for column,value in enumerate(values):self.obstacles.setItem(row,column,QTableWidgetItem(str(value)))

    def design(self):
        values={group:{} for group in ('channel','mesh','hydraulic')}
        for (group,key),widget in self.inputs.items():
            values[group][key]=widget.currentData() if isinstance(widget,QComboBox) else widget.value()
        if self.use_hydrograph.isChecked():
            values['hydraulic']['hydrograph']=[[float(self.hydrograph.item(row,col).text()) for col in range(3)] for row in range(self.hydrograph.rowCount())]
            if not values['hydraulic']['hydrograph']:raise ValueError('Import a hydrograph before enabling tabular flow.')
        obstacles=[]
        for row in range(self.obstacles.rowCount()):
            cells=[self.obstacles.item(row,col).text().strip() for col in range(13)]
            if cells[12].lower() not in ('true','false'):raise ValueError('Bank attached must be true or false.')
            obstacles.append(Obstacle(cells[0],cells[1],*[float(v) for v in cells[2:9]],
                int(cells[9]),int(cells[10]),float(cells[11]),cells[12].lower()=='true'))
        return RiverDesign(Channel(**values['channel']),MeshDesign(**values['mesh']),Hydraulics(**values['hydraulic']),obstacles)

    def set_design(self,design):
        for group in ('channel','mesh','hydraulic'):
            source=getattr(design,group)
            for field in fields(source):
                widget=self.inputs.get((group,field.name))
                if widget is None:continue
                value=getattr(source,field.name)
                if isinstance(widget,QComboBox):widget.setCurrentIndex(widget.findData(value))
                else:widget.setValue(value)
        self.obstacles.setRowCount(0)
        for item in design.obstacles:self.add_obstacle(item=item)
        self.use_hydrograph.setChecked(bool(design.hydraulic.hydrograph))
        self._set_hydrograph(design.hydraulic.hydrograph)

    def set_case_directory(self,path):
        self.case_dir=path
        if (Path(path)/DESIGN_FILE).is_file():
            try:
                _,design=load_river_case(path);self.set_design(design)
                self.status.setText('Loaded saved river design. A new mesh design requires a fresh case once a mesh exists.')
            except (OSError,ValueError,TypeError,KeyError) as exc:self.status.setText('Cannot load river design: '+str(exc))

    def _set_hydrograph(self,rows):
        self.hydrograph.setRowCount(len(rows))
        for row,values in enumerate(rows):
            for column,value in enumerate(values):self.hydrograph.setItem(row,column,QTableWidgetItem(str(value)))

    def import_hydrograph(self):
        path,_=QFileDialog.getOpenFileName(self,'Hydrograph: time_s, Q_m3_s, C_kg_m3',self.case_dir,'CSV (*.csv)')
        if not path:return
        try:
            with open(path,encoding='utf-8-sig',newline='') as stream:
                rows=[[float(row[key]) for key in ('time_s','Q_m3_s','C_kg_m3')] for row in csv.DictReader(stream)]
            self._set_hydrograph(rows);self.use_hydrograph.setChecked(True)
        except (OSError,ValueError,KeyError) as exc:self._error(exc)

    def _error(self,exc):
        self.status.setText(str(exc));QMessageBox.warning(self,'River design',str(exc))

    def use_normal_depth(self):
        try:
            design=self.design();c,h=design.channel,design.hydraulic
            depth=normal_depth(h.discharge,c.width,c.bank_slope,c.bed_slope,h.manning_n)
            self.inputs[('channel','depth')].setValue(depth)
            self.status.setText('Set a uniform-flow planning depth. Review boundary stages and hydraulic regime; this is not a backwater solution.')
        except (ValueError,TypeError,AttributeError) as exc:self._error(exc)

    def preview(self):
        try:
            design=self.design();report=analyze_design(design)
            patches,_=channel_surfaces(design);solids=obstacle_surfaces(design)
            self.plotter.clear()
            for name in ('bed','bankLeft','bankRight'):
                self.plotter.add_mesh(patches[name],color='#ae9870',show_edges=False)
            self.plotter.add_mesh(patches['lid'],color='#38bdf8',opacity=.15)
            for name,solid in solids.items():self.plotter.add_mesh(solid,color='#5a7652' if 'vegetation' in name else '#b6b9bc')
            self.plotter.show_axes();self.plotter.show_bounds(xtitle='X downstream (m)',ytitle='Y (m)',ztitle='Z (m)')
            self.plotter.view_isometric();self.plotter.reset_camera()
            self.preview_design=design;self.draw_profile()
            normal=f'{report["normal_depth_m"]:.4g} m' if report['normal_depth_m'] is not None else 'unavailable at zero bed slope'
            self.report.setPlainText('\n'.join([
                f'Method: {report["method"]} · Initial cells estimate: {report["initial_cells_estimate"]:,}',
                f'Area {report["area_m2"]:.4g} m² · peak U {report["peak_velocity_m_s"]:.4g} m/s · Fr {report["froude"]:.3g}',
                f'Manning shear estimate u*: {report["ustar_estimate_m_s"]:.4g} m/s · normal depth: {normal}',
                f'Critical depth at peak/reference Q: {report["critical_depth_m"]:.4g} m',
                f'Estimated bed/bank y+: {report["bed_yplus_estimate"]:.4g} / {report["bank_yplus_estimate"]:.4g}; bed Ks+: {report["bed_ks_plus"]:.4g}',
                f'First-centre bed distance: {report["bed_wall_distance_m"]:.5g} m · streamwise spacing {report["streamwise_size_m"]:.4g} m',
                f'Advection-only Δt estimate: {report["advective_dt_estimate"]:.4g} s; also check settling CFL.',
                f'Settling estimate {report["settling_speed_estimate_m_s"]:.4g} m/s · Rouse P {report["rouse_parameter_estimate"]:.3g} · settling-only Δt {report["settling_dt_estimate"]:.4g} s',
                f'Constant-plan inlet sediment load: {report["inlet_sediment_kg_s"]:.4g} kg/s (Q × C, not per unit width)',
                *[item['level']+': '+item['text'] for item in report['issues']]]))
            self.status.setText('Preview is design geometry. Export, run native mesh checks and verify actual wall layers/yPlus before trusting a simulation.')
        except (OSError,ValueError,TypeError,AttributeError,RuntimeError) as exc:self._error(exc)

    def draw_profile(self,*_):
        if self.preview_design is None:return
        design=self.preview_design;c=design.channel;x=c.length*self.station.value()
        base=c.bed_elevation-c.bed_slope*x
        bed_y=np.linspace(-c.width/2,c.width/2,301)
        if c.bank_slope:
            y=np.r_[np.linspace(-c.width/2-c.bank_slope*c.height,-c.width/2,40)[:-1],bed_y,
                    np.linspace(c.width/2,c.width/2+c.bank_slope*c.height,40)[1:]]
            z=bed_z(design,x,y)+np.maximum(np.abs(y)-c.width/2,0)/c.bank_slope
        else:y=bed_y;z=bed_z(design,x,y)
        self.profile_figure.clear();axis=self.profile_figure.add_subplot(111)
        axis.fill_between(y,base-.2*c.depth,z,color='#bd9b6a',alpha=.7,label='Designed bed / banks')
        axis.fill_between(y,z,base+c.depth,where=z<=base+c.depth,color='#75c7e3',alpha=.6,label='Design water domain')
        axis.plot(y,z,color='#715334',linewidth=2)
        if not c.bank_slope:
            axis.plot([-c.width/2,-c.width/2],[base,base+c.height],color='#715334',linewidth=2)
            axis.plot([c.width/2,c.width/2],[base,base+c.height],color='#715334',linewidth=2)
        axis.axhline(base+c.depth,color='#087f8c',linestyle='--',label='Water level / rigid-lid design')
        if c.mode=='vof':axis.axhline(base+c.height,color='#64748b',linestyle=':',label='Air-domain top')
        axis.set(xlabel='Y across channel (m)',ylabel='Z elevation (m)',title=f'Design cross-section at X = {x:.3g} m',
                 ylim=(base-.2*c.depth,base+c.height+.15*c.depth))
        axis.grid(alpha=.2)
        axis.legend(loc='upper center',bbox_to_anchor=(.5,-.23),ncol=1,fontsize=8)
        self.profile_canvas.draw_idle()

    def export_stl(self):
        path=QFileDialog.getExistingDirectory(self,'Export channel/terrain and obstacle STL files',self.case_dir)
        if path:
            try:export_geometry(path,self.design());self.status.setText('Exported terrain, closed channel boundary and separate solid STL files to '+path)
            except (OSError,ValueError,TypeError,AttributeError) as exc:self._error(exc)

    def create_case(self):
        try:
            design=self.design();manifest,_=export_river_case(self.case_dir,design)
            self.case_exported.emit(self.case_dir,'interFoam' if design.channel.mode=='vof' else design.hydraulic.solver)
            self.status.setText('Exported '+manifest['report']['method']+' inputs. Build and check the river mesh next; water/sediment fields are finalized from its actual patches.')
        except (OSError,ValueError,TypeError,AttributeError,KeyError) as exc:self._error(exc)

    def request_mesh(self):
        try:
            _,saved=load_river_case(self.case_dir)
            if saved.to_dict()!=self.design().to_dict():raise ValueError('Controls differ from the exported design. Create/update an unmeshed case before building.')
            self.mesh_requested.emit()
        except (OSError,ValueError,TypeError,AttributeError,KeyError) as exc:self._error(exc)

    def mesh_completed(self,success):
        if success:
            try:
                result=finalize_river_boundaries(self.case_dir,mesh_checked=True)
                self.case_exported.emit(self.case_dir,'interFoam' if result['design']['channel']['mode']=='vof' else result['design']['hydraulic']['solver'])
                self.status.setText('Native mesh checks passed. '+('Water, RANS and sediment boundaries are ready; run full case QAQC before solving.'
                    if result['boundaries_ready'] else 'VOF geometry mesh is ready; complete the native phase, pressure and stage setup before solving.'))
            except (OSError,ValueError,TypeError,KeyError) as exc:self._error(exc)
        else:self.status.setText('Native mesh generation/quality checks failed or stopped. Inspect command logs; refine a fresh design case rather than accepting failed cells.')

    def apply_boundaries(self):
        try:
            data,_=load_river_case(self.case_dir)
            if not data.get('mesh_checked'):raise ValueError('Run the native river meshing/quality pipeline before applying boundaries.')
            if data['design']!=self.design().to_dict():raise ValueError('Controls differ from the saved design. Use a fresh case for redesigned geometry or boundaries.')
            self.mesh_completed(True)
        except (OSError,ValueError,TypeError,KeyError,AttributeError) as exc:self._error(exc)
