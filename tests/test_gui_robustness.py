from pathlib import Path
import os
import signal
import sys
import time

import numpy as np
import pytest

from foam_io import entries, value, boundary_patches, write_case_files, update_values
from openfoam_dict_parser import OpenFoamDictParser
from case_model import CaseModel
from case_qaqc import check_case, mesh_passed
from sediment_setup import create_starter, case_patches, configure_case


def prepared_case(tmp_path, solver='sedimentPimpleFoam'):
    case = tmp_path/'case'; case.mkdir()
    create_starter(case,solver)
    patches = case_patches(case)
    mesh = case/'constant/polyMesh'; mesh.mkdir()
    for name in ('points','faces','owner','neighbour'):
        (mesh/name).write_text('test placeholder; actual geometry is covered by executable integration checks')
    (mesh/'boundary').write_text('FoamFile { format ascii; class polyBoundaryMesh; object boundary; }\n'
        + str(len(patches))+'\n(\n'+''.join(f'{p["name"]} {{ type {p["type"]}; nFaces 1; startFace 0; }}\n' for p in patches)+')\n')
    return case


def wait(qapp, condition, seconds=6):
    deadline = time.monotonic()+seconds
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents(); time.sleep(.005)
    qapp.processEvents()
    assert condition(), 'Qt operation did not finish in time'


def test_dictionary_comments_quotes_and_patch_merge(tmp_path):
    path = tmp_path/'U'
    original = '''// boundaryField { this is a comment, not an entry }
FoamFile { object U; format ascii; class volVectorField; note "braces { }"; }
dimensions [0 1 -1 0 0 0 0];
internalField uniform (1 2 3);
boundaryField
{
 inlet { type fixedValue; value uniform (1 2 3); /* } */ }
 outlet { type inletOutlet; inletValue uniform (4 5 6); value uniform (7 8 9); }
 "wall.*" { type noSlip; }
}
'''
    path.write_text(original)
    parser = OpenFoamDictParser(str(path),'volVectorField','U')
    rendered = parser.render({'inlet':{'type':'flowRateInletVelocity','volumetricFlowRate':'0.2','value':'uniform (0 0 0)'}},merge=True)
    assert 'outlet { type inletOutlet; inletValue uniform (4 5 6); value uniform (7 8 9); }' in rendered
    assert '"wall.*" { type noSlip; }' in rendered
    assert value(rendered,'internalField') == 'uniform (1 2 3)'
    assert original == path.read_text()  # rendering prepares before changing any file


@pytest.mark.parametrize('text',['boundaryField { inlet { type noSlip; }',
    'boundaryField { #include "other" }','boundaryField {}; boundaryField {};'])
def test_malformed_or_included_boundary_refuses_overwrite(tmp_path,text):
    path=tmp_path/'U';path.write_text(text)
    parser=OpenFoamDictParser(str(path),'volVectorField','U')
    with pytest.raises(ValueError):parser.render({'inlet':{'type':'noSlip'}},merge=True)
    assert path.read_text()==text


def test_boundary_metadata_same_line_quoted_and_comments(tmp_path):
    path=tmp_path/'boundary'
    path.write_text('FoamFile { object boundary; note "(fake)"; }\n2 ( inlet { type patch; } /* } */ "side-1" { type empty; } )')
    assert boundary_patches(path)==[{'name':'inlet','type':'patch'},{'name':'side-1','type':'empty'}]
    path.write_text(path.read_text().replace('2 (','3 ('))
    with pytest.raises(ValueError):boundary_patches(path)


def test_bundle_backup_and_rollback(tmp_path,monkeypatch):
    import foam_io
    (tmp_path/'a').write_bytes(b'old a');(tmp_path/'b').write_bytes(b'old b')
    original=foam_io.atomic_write
    def fail(path,text):
        if Path(path)==tmp_path/'b' and text=='new b':raise OSError('simulated write failure')
        original(path,text)
    monkeypatch.setattr(foam_io,'atomic_write',fail)
    with pytest.raises(OSError):write_case_files(tmp_path,{'a':'new a','b':'new b'})
    assert (tmp_path/'a').read_bytes()==b'old a' and (tmp_path/'b').read_bytes()==b'old b'
    backup=list((tmp_path/'.ofsolvers-backups').iterdir())[0]
    assert (backup/'a').read_bytes()==b'old a'
    with pytest.raises(ValueError):write_case_files(tmp_path,{'../outside':'unsafe'})


@pytest.mark.parametrize('change', [{'end_time':0},{'delta_t':0},{'dim_x':float('nan')},{'cells_x':0},{'solver':'pimpleFoam; unwanted'}])
def test_model_invalid_update_is_transactional(change):
    model=CaseModel();before=model.get_case_dict();signals=[];model.data_changed.connect(lambda:signals.append(True))
    args={'solver':model.solver,'dim_x':10.,'dim_y':10.,'dim_z':10.,'cells_x':100,'cells_y':100,'cells_z':100,
          'boundaries':dict(model.boundaries),'start_time':0.,'end_time':100.,'delta_t':.001}
    args.update(change)
    with pytest.raises(ValueError):model.update_conceptual_data(**args)
    assert model.get_case_dict()==before and not signals
    exposed=model.get_case_dict();exposed['boundaries']['Min X']='Inlet'
    assert model.boundaries['Min X']=='Wall'


def test_qaqc_checks_files_time_application_and_empty_boundaries(tmp_path):
    case=prepared_case(tmp_path)
    assert check_case(case,'sedimentPimpleFoam',check_programs=False).ok
    assert not check_case(case,'sandParcelPimpleFoam',check_programs=False).ok
    path=case/'0/C';path.write_text(path.read_text().replace('sides { type empty; }','sides { type symmetry; }'))
    report=check_case(case,'sedimentPimpleFoam',check_programs=False)
    assert any('must have type empty' in e for e in report.errors)
    (case/'0/Mbed').unlink()
    assert any('Missing 0/Mbed' in e for e in check_case(case,'sedimentPimpleFoam',check_programs=False).errors)


def test_mesh_qaqc_requires_actual_success_text():
    assert mesh_passed('Checking...\nMesh OK.\nEnd\n',0)
    assert not mesh_passed('Failed 1 mesh checks.\nEnd\n',0)
    assert not mesh_passed('Mesh OK.\nFOAM FATAL ERROR',0)
    assert not mesh_passed('Mesh OK.',1)


def test_qaqc_rejects_malformed_field_instead_of_passing_mesh_only(tmp_path):
    case=prepared_case(tmp_path)
    path=case/'0/U'
    path.write_text(path.read_text().replace('internalField uniform (0.1 0 0);','internalField uniform (0.1 0 0)'))
    report=check_case(case,'sedimentPimpleFoam',check_programs=False)
    assert not report.ok and any('Cannot fully inspect U' in e for e in report.errors)


def test_boundary_selection_keeps_state_and_unedited_fields(tmp_path,qapp,dialogs):
    from boundary_tab import BoundaryTab
    case=prepared_case(tmp_path)
    tab=BoundaryTab();tab.set_case_directory(str(case));tab.set_solver_profile('sedimentPimpleFoam')
    tab.import_patches_from_boundary_file()
    assert not tab.dirty_patches
    for i in range(tab.patch_list.count()):tab.patch_list.setCurrentRow(i)
    assert not tab.dirty_patches
    old_c=(case/'0/C').read_bytes();old_bed=(case/'0/Mbed').read_bytes()
    old_u=(case/'0/U').read_text()
    tab.patch_list.setCurrentRow(0);tab.bc_type_combo.setCurrentText('Inlet');tab.inlet_flow_rate.setValue(.002)
    assert tab.dirty_patches=={'upstream'}
    tab.write_0_directory()
    assert not tab.dirty_patches
    text=(case/'0/U').read_text()
    patch=value(value(text,'boundaryField')[1:-1],'upstream')[1:-1]
    assert value(patch,'volumetricFlowRate')=='0.002'
    assert 'sides { type empty; }' in text and 'downstream { type zeroGradient; }' in text
    assert (case/'0/C').read_bytes()==old_c and (case/'0/Mbed').read_bytes()==old_bed
    assert old_u != text


def test_explicit_fluid_boundary_rebuild_uses_current_mesh(tmp_path,qapp,dialogs):
    from boundary_tab import BoundaryTab
    case=prepared_case(tmp_path)
    metadata=case/'constant/polyMesh/boundary'
    metadata.write_text(metadata.read_text().replace('upstream','riverInlet'))
    tab=BoundaryTab();tab.set_case_directory(str(case));tab.set_solver_profile('sedimentPimpleFoam')
    tab.import_patches_from_boundary_file()
    tab.patch_list.setCurrentRow(0);tab.bc_type_combo.setCurrentText('Inlet');tab.inlet_flow_rate.setValue(.002)
    original=(case/'0/U').read_bytes()
    tab.write_0_directory()
    assert (case/'0/U').read_bytes()==original  # default merge refuses a missing patch
    tab.rebuild_boundaries.setChecked(True);tab.write_0_directory()
    text=(case/'0/U').read_text()
    patches=entries(value(text,'boundaryField')[1:-1])
    assert 'riverInlet' in patches and 'upstream' not in patches
    assert value(text,'internalField')==value(original.decode(),'internalField')
    assert not tab.rebuild_boundaries.isChecked()


@pytest.mark.parametrize('solver',['sedimentPimpleFoam','sandParcelPimpleFoam'])
def test_sediment_gui_writes_initial_and_boundary_settings(tmp_path,qapp,solver):
    from sediment_tab import SedimentTab
    case=tmp_path/'river';case.mkdir();create_starter(case,solver)
    tab=SedimentTab();tab.set_case_directory(str(case))
    conf=tab.configuration();conf.update(initialC=.3,inletC=.4,initialBed=.2,feedRate=.0002,profile='uniform',resuspension=True)
    original_u=(case/'0/U').read_bytes()
    files,backup=configure_case(case,conf)
    assert backup and (case/'0/U').read_bytes()==original_u
    if solver=='sedimentPimpleFoam':
        assert value((case/'0/C').read_text(),'internalField')=='uniform 0.3'
        assert 'phiSed' in (case/'0/C').read_text() and 'uniform 0.4' in (case/'0/C').read_text()
        assert 'uniform 0.2' in (case/'0/Mbed').read_text()
        boundaries=value((case/'0/C').read_text(),'boundaryField')[1:-1]
        assert value(value(boundaries,'sides')[1:-1],'type')=='empty'
    else:
        cloud=(case/'constant/sandCloudProperties').read_text()
        feed=value(value(value(cloud,'subModels')[1:-1],'injectionModels')[1:-1],'feed')[1:-1]
        assert value(feed,'massFeedRate')=='0.0002' and value(feed,'profile')=='uniform'
        assert value(value(cloud,'resuspension')[1:-1],'enabled')=='true'
    conf['rhoParticle']=999
    before={p:p.read_bytes() for p in case.rglob('*') if p.is_file() and '.ofsolvers-backups' not in p.parts}
    with pytest.raises(ValueError):configure_case(case,conf)
    assert all(p.read_bytes()==data for p,data in before.items())
    with pytest.raises(ValueError):create_starter(case,solver)


@pytest.mark.parametrize('solver',['sedimentPimpleFoam','sandParcelPimpleFoam'])
def test_initial_sediment_setup_refuses_active_restart(tmp_path,qapp,solver):
    from sediment_tab import SedimentTab
    case=tmp_path/'river';case.mkdir();create_starter(case,solver)
    tab=SedimentTab();tab.set_case_directory(str(case))
    config=tab.configuration()
    (case/'1').mkdir()
    path=case/'system/controlDict'
    path.write_text(update_values(path.read_text(),{'startFrom':'latestTime','endTime':2}))
    before={p:p.read_bytes() for p in case.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='configured to restart'):configure_case(case,config)
    assert all(p.read_bytes()==data for p,data in before.items())
    assert not (case/'.ofsolvers-backups').exists()


def test_worker_reports_failure_and_cancel_without_shell(qapp,tmp_path):
    from execution_tab import OpenFOAMWorker
    with pytest.raises(TypeError):OpenFOAMWorker('echo unsafe',tmp_path)
    worker=OpenFOAMWorker(['not-an-installed-command'],tmp_path)
    results=[];worker.finished_signal.connect(results.append);worker.start()
    wait(qapp,lambda:not worker.isRunning() and bool(results))
    assert results==[-1]
    worker=OpenFOAMWorker([sys.executable,'-c','raise SystemExit(7)'],tmp_path)
    results=[];worker.finished_signal.connect(results.append);worker.stop();worker.start()
    wait(qapp,lambda:not worker.isRunning() and bool(results))
    assert len(results)==1 and worker.cancelled


@pytest.mark.skipif(os.name!='posix',reason='POSIX process group behavior')
def test_stop_reaps_parent_and_child_holding_output_pipe(qapp,tmp_path):
    from execution_tab import OpenFOAMWorker
    script="import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)']); print(p.pid,flush=True); time.sleep(30)"
    worker=OpenFOAMWorker([sys.executable,'-u','-c',script],tmp_path)
    lines=[];codes=[];worker.output_signal.connect(lines.append);worker.finished_signal.connect(codes.append)
    worker.start();wait(qapp,lambda:any(line.isdigit() for line in lines))
    child=int(next(line for line in lines if line.isdigit()))
    time.sleep(.1)  # allow the child's SIGTERM handler to be installed
    worker.stop();wait(qapp,lambda:not worker.isRunning() and bool(codes))
    assert len(codes)==1 and worker.process.poll() is not None
    status=Path(f'/proc/{child}/stat')
    assert not status.exists() or status.read_text().split()[2]=='Z'


def test_execution_uses_selected_case_and_plain_text(qapp,tmp_path):
    from execution_tab import ExecutionTab
    tab=ExecutionTab();tab.set_case_directory(str(tmp_path))
    tab.execute_command([sys.executable,'-c','import os; print(os.getcwd()); print("<b>plain output</b>")'])
    wait(qapp,lambda:tab.worker is None)
    assert str(tmp_path) in tab.console.toPlainText() and '<b>plain output</b>' in tab.console.toPlainText()
    assert tab.btn_run.isEnabled() and not tab.btn_stop.isEnabled()
