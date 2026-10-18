from dataclasses import replace,asdict
import gzip

import pytest

from case_controller import CaseController
from case_model import CaseModel
from foam_io import value
from large_tracer import ParticleBoundary,TracerConfig,save_tracers,load_tracers,preflight
from particle_boundary_editor import ParticleBoundaryEditor
from scour_tracer_demo import generate_demo
from tracer_preprocess import inspect_carrier,carrier_patches,suggested_placement
from tracer_tab import TracerTab


def test_carrier_inspection_is_read_only_and_understands_free_surface(tmp_path):
    generate_demo(tmp_path,end_time=3,coarse=True)
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report=inspect_carrier(tmp_path)
    assert report.solver=='interFoam' and report.times==[0]
    assert report.alpha_fields==['alpha.water'] and not report.meshed
    assert report.bounds==((0,0,-.2),(3,.5,1)) and report.water_level==.5
    assert report.properties['rho_water']==1000 and report.properties['mu_water']==.001
    assert report.properties['mu_air']==pytest.approx(1.8e-5)
    assert report.properties['gravity']==(0,0,-9.81)
    assert all(p.read_bytes()==b for p,b in before.items())
    cfg=suggested_placement(TracerConfig(),report,'bed',.5)
    assert cfg.placement=='bed' and cfg.bed_patches==('bed',)
    assert cfg.retention_min==TracerConfig().retention_min  # physical hole ROI is not guessed
    assert cfg.density.low==1050


def test_compressed_boundary_metadata(tmp_path):
    mesh=tmp_path/'constant/polyMesh'; mesh.mkdir(parents=True)
    with gzip.open(mesh/'boundary.gz','wt') as stream:
        stream.write('FoamFile { class polyBoundaryMesh; object boundary; }\n2 ( bed { type wall; } outlet { type patch; } )')
    assert carrier_patches(tmp_path)==[{'name':'bed','type':'wall'},{'name':'outlet','type':'patch'}]


def test_air_fraction_is_not_automatically_imported_as_liquid(qapp,dialogs,tmp_path):
    generate_demo(tmp_path)
    (tmp_path/'0/alpha.water').rename(tmp_path/'0/alpha.air')
    tab=TracerTab(); tab.set_case_directory(str(tmp_path))
    tab.preprocessing.use_carrier()
    assert dialogs and 'air fraction' in dialogs[-1][1]
    assert tab.config().alpha_field=='alpha.water'
    assert tab.preprocessing.inspection.properties['rho_water']==1000
    tab.close()


@pytest.mark.parametrize('rule',[ParticleBoundary('stick'),ParticleBoundary('contact',1.1),
    ParticleBoundary('contact',friction=-1),ParticleBoundary('contact',float('nan')),
    ParticleBoundary('escape',0,0)])
def test_invalid_particle_boundary_rules(rule):
    with pytest.raises(ValueError): rule.validate()


def test_boundary_settings_roundtrip_and_patch_validation(tmp_path):
    generate_demo(tmp_path)
    cfg=replace(TracerConfig(),run_name='custom',boundaries={
        'bed':ParticleBoundary('contact',.2,.6),'downstream':ParticleBoundary('contact',1,0)})
    carrier=(tmp_path/'0/U').read_bytes()
    save_tracers(tmp_path,cfg)
    assert asdict(load_tracers(tmp_path))==asdict(cfg)
    native=(tmp_path/'constant/largeTracerProperties').read_text()
    assert value(value(native,'particleBoundaries')[1:-1],'downstream').startswith('{')
    assert (tmp_path/'0/U').read_bytes()==carrier
    before=(tmp_path/'constant/largeTracerProperties').read_bytes()
    for bad in [replace(cfg,boundaries={'missing':ParticleBoundary()}),
                replace(cfg,placement='bed',boundaries={'bed':ParticleBoundary('escape')})]:
        with pytest.raises(ValueError): save_tracers(tmp_path,bad)
        assert (tmp_path/'constant/largeTracerProperties').read_bytes()==before


def test_boundary_editor_preserves_independent_inheritance(qapp):
    editor=ParticleBoundaryEditor()
    cfg=replace(TracerConfig(),boundaries={'bed':ParticleBoundary('contact',friction=.6)})
    editor.load([{'name':'bed','type':'wall'},{'name':'outlet','type':'patch'}],cfg)
    boundaries,beds=editor.configuration()
    assert boundaries==cfg.boundaries and beds==('bed',)
    editor.update_defaults(.3,.9)
    assert editor.rows['bed']['e'].value()==.3 and editor.rows['bed']['mu'].value()==.6
    assert editor.configuration()[0]['bed'].restitution is None
    row=editor.rows['bed']; row['action'].setCurrentIndex(row['action'].findData('escape'))
    boundaries,beds=editor.configuration()
    assert boundaries['bed']==ParticleBoundary('escape') and not beds
    assert not row['seed'].isEnabled()
    editor.close()


def test_carrier_import_preview_gate_and_presets(qapp,dialogs,tmp_path):
    generate_demo(tmp_path,end_time=3)
    tab=TracerTab(); tab.set_case_directory(str(tmp_path))
    tab.preprocessing.use_carrier()
    assert tab.config().alpha_field=='alpha.water' and tab.config().end_time==3
    tab.preprocessing.preset.setCurrentIndex(1); tab.preprocessing.apply_placement()
    assert tab.config().placement=='bed' and tab.config().density.low==1050
    assert not dialogs
    (tmp_path/'constant/polyMesh').mkdir(); (tmp_path/'constant/polyMesh/points').write_text('test')
    preflight(tmp_path,tab.config(),preview=True)
    with pytest.raises(ValueError,match='cover'): preflight(tmp_path,tab.config())
    commands=[]; tab.command_requested.connect(lambda *args:commands.append(args))
    tab.preview()
    assert commands[-1][0]==['largeTracerFoam','-preview']
    tab.close()


def test_particle_workflow_save_does_not_change_carrier(qapp,dialogs,tmp_path):
    from main_window import MainWindow
    generate_demo(tmp_path)
    window=MainWindow(); controller=CaseController(CaseModel(),window)
    window.set_case_directory(str(tmp_path))
    assert window.particle_workflow and 'Carrier flow' in window.tabs.tabText(window.tabs.indexOf(window.tab_boundaries))
    before=(tmp_path/'system/controlDict').read_bytes()
    window.end_time_spinbox.setValue(999)
    window.tab_tracers.count.setValue(31)
    window.run_button.click()
    assert load_tracers(tmp_path).count==31
    assert (tmp_path/'system/controlDict').read_bytes()==before
    assert not (tmp_path/'ofsolvers-plan.json').exists()
    window.set_busy(True); assert not window.workflow.isEnabled()
    window.set_busy(False); assert window.workflow.isEnabled()
    window.workflow.setCurrentIndex(0)
    assert not window.particle_workflow and window.run_button.text().startswith('Save plan')
    window.close()
