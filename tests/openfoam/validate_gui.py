#!/usr/bin/env python3
"""Real GUI-to-OpenFOAM workflows. Activate OpenFOAM and run under a display.

Headless example: xvfb-run -a python tests/openfoam/validate_gui.py
"""
from pathlib import Path
import math
import re
import shutil
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from PyQt6.QtWidgets import QApplication, QMessageBox
from case_controller import CaseController
from case_model import CaseModel
from main_window import MainWindow
from foam_io import update_values, value
from mesh_config import block_mesh_dictionary, snappy_dictionary

WORK=Path(tempfile.mkdtemp(prefix='ofsolvers-gui-validation-'))
print('Validation artifacts:',WORK,flush=True)
app=QApplication([])
dialogs=[]
window=MainWindow()
controller=CaseController(CaseModel(),window)


def wait_execution():
    deadline=time.monotonic()+40
    while window.tab_execution.worker is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    app.processEvents()
    assert window.tab_execution.worker is None, 'GUI command did not finish'
    assert not window.tab_execution.btn_stop.isEnabled()


def control(case,**updates):
    path=case/'system/controlDict'
    path.write_text(update_values(path.read_text(),updates))


def inventory(output,expected):
    mobile,bed=map(float,re.findall(r'Sand inventory: mobile=(\S+) deposited=(\S+) kg',output)[-1])
    escaped=float(re.findall(r'Parcel fate: system \(number, mass\)\s+- escape\s*=\s*\d+, (\S+)',output)[-1])
    assert math.isclose(mobile+bed+escaped,expected,rel_tol=1e-8,abs_tol=1e-12)


def workflow(solver):
    case=WORK/solver;case.mkdir()
    window.set_case_directory(str(case))
    tab=window.tab_sediment
    tab.model_combo.setCurrentText(solver)
    tab.create_starter_case()
    assert (case/'0/U').exists() and window.tab_execution.combo_solver.currentText()==solver
    if solver=='sedimentPimpleFoam':
        tab.inputs['initialC'].setValue(.2)
        tab.inputs['inletC'].setValue(.3)
        tab.inputs['initialBed'].setValue(.02)
    else:
        tab.inputs['profile'].setCurrentText('uniform')
        tab.inputs['feedRate'].setValue(.0002)
        tab.inputs['duration'].setValue(.1)
        tab.inputs['referenceHeight'].setValue(.001)
        tab.inputs['maximumHeight'].setValue(.002)
        tab.resuspension.setChecked(True)
        tab.inputs['critical'].setValue(.000001)
        tab.inputs['releaseRate'].setValue(10)
        tab.inputs['minimumRestTime'].setValue(.02)
    before=len(dialogs)
    tab.write_setup()
    assert dialogs[-1][0]=='Sediment setup saved',dialogs[before:]
    control(case,endTime=.3,writeInterval=.1)
    execution=window.tab_execution
    execution.run_block_mesh();wait_execution()
    assert (case/'constant/polyMesh/boundary').exists()
    boundaries=window.tab_boundaries
    boundaries.import_patches_from_boundary_file()
    patches=[boundaries.patch_list.item(i).text() for i in range(boundaries.patch_list.count())]
    boundaries.patch_list.setCurrentRow(patches.index('upstream'))
    boundaries.bc_type_combo.setCurrentText('Inlet')
    boundaries.inlet_flow_rate.setValue(.001 if solver=='sedimentPimpleFoam' else .005)
    boundaries.write_0_directory()
    assert not boundaries.dirty_patches,dialogs[-1]
    execution.run_qaqc();wait_execution()
    assert execution.status_label.text()=='Case checks and checkMesh passed.',execution.console.toPlainText()
    execution.run_solver();wait_execution()
    assert execution.status_label.text()=='Solver completed.',execution.console.toPlainText()[-5000:]
    output=execution.console.toPlainText()
    (case/'log.gui').write_text(output)
    if solver=='sedimentPimpleFoam':
        residuals=[float(x) for x in re.findall(r'residual=(\S+) kg',output)]
        assert residuals and max(map(abs,residuals))<1e-10
        assert value((case/'0/C').read_text(),'internalField')=='uniform 0.2'
    else:
        assert re.search(r'Sand resuspension: released=[1-9]',output)
        inventory(output,2e-5)
    control(case,startFrom='latestTime',endTime=.4)
    execution.run_solver();wait_execution()
    assert execution.status_label.text()=='Solver completed.',execution.console.toPlainText()[-5000:]
    (case/'log.gui.restart').write_text(execution.console.toPlainText())
    print('PASS:',solver,'starter, initial/boundary setup, mesh, QAQC, solver and restart',flush=True)
    return case


with patch.object(QMessageBox,'information',lambda parent,title,text,*args:dialogs.append((title,text))), \
     patch.object(QMessageBox,'warning',lambda parent,title,text,*args:dialogs.append((title,text))), \
     patch.object(QMessageBox,'critical',lambda parent,title,text,*args:dialogs.append((title,text))):
    concentration=workflow('sedimentPimpleFoam')
    parcels=workflow('sandParcelPimpleFoam')
    parallel=WORK/'parallel';parallel.mkdir()
    for name in ('0','constant','system'):
        shutil.copytree(parcels/name,parallel/name)
    control(parallel,startFrom='startTime',startTime=0,endTime=.3)
    window.set_case_directory(str(parallel))
    execution=window.tab_execution
    execution.spin_cores.setValue(2)
    execution.run_decompose();wait_execution()
    assert (parallel/'processor1/constant/polyMesh/boundary').exists(),execution.console.toPlainText()[-3000:]
    execution.run_solver();wait_execution()
    assert execution.status_label.text()=='Solver completed.',execution.console.toPlainText()[-5000:]
    inventory(execution.console.toPlainText(),2e-5)
    control(parallel,startFrom='latestTime',endTime=.4)
    execution.run_solver();wait_execution()
    assert execution.status_label.text()=='Solver completed.',execution.console.toPlainText()[-5000:]
    inventory(execution.console.toPlainText(),2e-5)
    (parallel/'log.gui.parallel').write_text(execution.console.toPlainText())
    execution.run_reconstruct();wait_execution()
    assert (parallel/'0.4/U').exists(),execution.console.toPlainText()[-3000:]
    control(parallel,endTime=.5)
    execution.run_qaqc();wait_execution()
    assert execution.status_label.text()=='Case checks and checkMesh passed.',execution.console.toPlainText()[-5000:]
    print('PASS: GUI decomposition, two-rank resuspension, parallel restart and reconstruction',flush=True)
    # Exercise generated mesh dictionaries with the actual native utilities.
    geometry=WORK/'generatedMesh';shutil.copytree(ROOT/'tutorials/sandParcelChannel',geometry)
    (geometry/'system/blockMeshDict').write_text(block_mesh_dictionary(
        (-1,1,-1,1,-1,1),(8,8,8),(1,1,1),{'zMin':'wall'}))
    window.set_case_directory(str(geometry));execution.spin_cores.setValue(1)
    execution.run_block_mesh();wait_execution()
    assert (geometry/'constant/polyMesh/boundary').exists()
    import pyvista as pv
    surface=pv.Box(bounds=(-.4,.4,-.4,.4,-.4,.4)).triangulate()
    target=geometry/'constant/triSurface';target.mkdir()
    surface.save(target/'river.stl')
    dictionary=snappy_dictionary('river.stl',[('river',1,1,'surface')],(.75,.75,.75),100000,200000,True,10,True)
    (geometry/'system/snappyHexMeshDict').write_text(dictionary)
    execution.execute_command(['snappyHexMesh','-overwrite'],'snappy');wait_execution()
    assert execution._success,execution.console.toPlainText()[-5000:]
    (geometry/'log.gui.mesh').write_text(execution.console.toPlainText())
    print('PASS: generated background and snappyHexMesh dictionaries execute in v1912',flush=True)
    # Rebuild initial flow/sediment conditions for the newly named custom mesh.
    boundary=window.tab_boundaries;boundary.import_patches_from_boundary_file()
    names=[boundary.patch_list.item(i).text() for i in range(boundary.patch_list.count())]
    boundary.patch_list.setCurrentRow(names.index('xMin'))
    boundary.bc_type_combo.setCurrentText('Inlet');boundary.inlet_flow_rate.setValue(.4)
    boundary.rebuild_boundaries.setChecked(True);boundary.write_0_directory()
    assert dialogs[-1][0]=='Success',dialogs[-1]
    sediment=window.tab_sediment;sediment.refresh_patches()
    sediment.inlet_combo.setCurrentText('xMin');sediment.outlet_combo.setCurrentText('xMax')
    for i in range(sediment.bed_list.count()):
        item=sediment.bed_list.item(i);item.setSelected(item.text()=='zMin')
    sediment.shear_bed_combo.setCurrentText('zMin')
    for axis,number in zip('XYZ',(-1,-.2,-1)):sediment.inputs['origin'+axis].setValue(number)
    sediment.inputs['width'].setValue(.4);sediment.inputs['depth'].setValue(2)
    sediment.inputs['profile'].setCurrentText('uniform');sediment.inputs['duration'].setValue(.02)
    sediment.write_setup();assert dialogs[-1][0]=='Sediment setup saved',dialogs[-1]
    control(geometry,endTime=.03,writeInterval=.01)
    execution.run_solver();wait_execution()
    assert execution.status_label.text()=='Solver completed.',execution.console.toPlainText()[-5000:]
    (geometry/'log.gui.custom-river').write_text(execution.console.toPlainText())
    print('PASS: custom mesh flow-boundary rebuild, sediment patch setup and native parcel run',flush=True)
window.close();app.processEvents()
print('All GUI-to-OpenFOAM workflows passed.',flush=True)
