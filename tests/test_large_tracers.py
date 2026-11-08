from dataclasses import asdict, replace
import json
from pathlib import Path
import csv

import numpy as np
import pytest

from large_tracer import Distribution, TracerConfig, save_tracers, load_tracers, preflight
from scour_tracer_demo import generate_demo
from tracer_tab import TracerTab
from visualize_tracers import load_tracks, visualize
from foam_io import value, entries


@pytest.mark.parametrize('kwargs',[
    {'count':0},{'count':1000},{'count':1.5},{'seed':-1},{'start_time':float('nan')},
    {'end_time':0},{'max_delta_t':0},{'run_name':'../../escape'},
    {'run_name':'bad;name'},{'alpha_field':'bad;field'},
    {'seed_min':(0,0,1)},{'density':Distribution('constant',low=-1)},
    {'restitution':1.1},{'min_water_fraction':0},{'volume_samples':12},
    {'friction':float('inf')},{'rim_elevation':float('nan')},
    {'collision_mode':'bad'},{'collision_diameter_threshold':0},
    {'collision_diameter_threshold':-.01},{'collision_diameter_threshold':float('nan')},
    {'collision_diameter_threshold':float('inf')}])
def test_invalid_settings_do_not_modify_case(tmp_path,kwargs):
    with pytest.raises(ValueError):
        save_tracers(tmp_path,replace(TracerConfig(),**kwargs))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('distribution',[
    Distribution('constant',low=.1),Distribution(),
    Distribution('lognormal',low=.01,high=.2,median=.05,sigma=.5),
    Distribution('discrete',values=(400,800,1100),weights=(1,2,1))])
def test_distribution_serialization(distribution):
    text=distribution.foam()[1:-1]
    assert value(text,'type')==distribution.kind
    assert set(entries(text)) == ({'type','value'} if distribution.kind=='constant' else
        {'type','values','weights'} if distribution.kind=='discrete' else
        {'type','min','max','median','sigma'} if distribution.kind=='lognormal' else {'type','min','max'})


@pytest.mark.parametrize('distribution',[
    Distribution('discrete',values=(1,),weights=(0,)),
    Distribution('discrete',values=(1,2),weights=(1,)),
    Distribution('discrete',values=(1,),weights=(-1,)),
    Distribution('lognormal',low=.1,high=.2,median=.5),
    Distribution('uniform',low=2,high=1),Distribution('bad')])
def test_invalid_distributions(distribution):
    with pytest.raises(ValueError): distribution.validate()


def test_configuration_roundtrip_backups_and_output_protection(tmp_path):
    (tmp_path/'system').mkdir()
    (tmp_path/'system/controlDict').write_text('application interFoam;')
    config=replace(TracerConfig(),diameter=Distribution('lognormal',low=.01,high=.2,median=.05))
    save_tracers(tmp_path,config)
    assert asdict(load_tracers(tmp_path))==asdict(TracerConfig.from_dict(asdict(config)))
    old=(tmp_path/'constant/largeTracerProperties').read_text()
    backup=save_tracers(tmp_path,replace(config,count=50))
    assert (backup/'constant/largeTracerProperties').read_text()==old
    assert (tmp_path/'system/controlDict').read_text()=='application interFoam;'
    (tmp_path/'postProcessing/largeTracers/tracers01').mkdir(parents=True)
    with pytest.raises(ValueError,match='already has output'): save_tracers(tmp_path,config)


@pytest.mark.parametrize('enabled',[True,False])
def test_legacy_collision_settings_load_without_changing_behavior(enabled):
    data=asdict(replace(TracerConfig(),collisions=enabled))
    del data['collision_mode'],data['collision_diameter_threshold']
    config=TracerConfig.from_dict(data)
    assert config.collisions is enabled and config.collision_mode=='all'
    assert value(config.foam(),'particleCollisions')==('true' if enabled else 'false')


def test_threshold_mode_configuration_and_workbench_roundtrip(qapp,dialogs,tmp_path):
    generate_demo(tmp_path)
    tab=TracerTab()
    tab.set_case_directory(str(tmp_path))
    threshold=tab.spins['collision_diameter_threshold']
    assert not threshold.isEnabled()
    tab.collision_mode.setCurrentIndex(tab.collision_mode.findData('largeOnly'))
    assert threshold.isEnabled()
    threshold.setValue(.045)
    assert tab.save()
    cfg=load_tracers(tmp_path)
    assert cfg.collision_mode=='largeOnly' and cfg.collision_diameter_threshold==.045
    props=(tmp_path/'constant/largeTracerProperties').read_text()
    assert value(props,'particleCollisionMode')=='largeOnly'
    assert float(value(props,'collisionDiameterThreshold'))==.045
    tab.set_case_directory(str(tmp_path))
    assert tab.collision_mode.currentData()=='largeOnly' and threshold.value()==.045
    tab.collisions.setChecked(False)
    assert not threshold.isEnabled() and not tab.collision_mode.isEnabled()
    assert tab.save() and not load_tracers(tmp_path).collisions
    assert not dialogs
    tab.close()


def test_replay_requires_actual_carrier_and_explicit_frozen_mode(tmp_path):
    (tmp_path/'constant/polyMesh').mkdir(parents=True)
    (tmp_path/'constant/polyMesh/points').write_text('')
    (tmp_path/'0').mkdir()
    (tmp_path/'0/U').write_text('')
    cfg=replace(TracerConfig(),alpha_field='none')
    with pytest.raises(ValueError,match='cover'): preflight(tmp_path,cfg)
    preflight(tmp_path,replace(cfg,frozen_flow=True))
    with pytest.raises(ValueError,match='alpha.water'):
        preflight(tmp_path,replace(cfg,frozen_flow=True,alpha_field='alpha.water'))
    (tmp_path/'5').mkdir(); (tmp_path/'5/U').write_text('')
    preflight(tmp_path,cfg)
    with pytest.raises(ValueError,match='snapshot'): preflight(tmp_path,replace(cfg,frozen_flow=True,flow_time=2))


def test_scour_demo_is_free_surface_and_preserves_existing_cases(tmp_path):
    generate_demo(tmp_path,velocity=.1,end_time=2,coarse=True)
    assert value((tmp_path/'system/controlDict').read_text(),'application')=='interFoam'
    assert 'phases (water air)' in (tmp_path/'constant/transportProperties').read_text()
    assert '.5' in (tmp_path/'system/setFieldsDict').read_text()
    cfg=load_tracers(tmp_path)
    assert cfg.end_time==2 and cfg.alpha_field=='alpha.water' and cfg.count<1000
    before=(tmp_path/'0/U').read_text()
    with pytest.raises(ValueError,match='empty'): generate_demo(tmp_path)
    assert (tmp_path/'0/U').read_text()==before


def test_workbench_roundtrip_and_run_preflight(qapp,dialogs,tmp_path):
    generate_demo(tmp_path)
    tab=TracerTab()
    tab.set_case_directory(str(tmp_path))
    tab.count.setValue(60)
    tab.density.kind.setCurrentText('discrete')
    tab.density.values.setText('500 1100'); tab.density.weights.setText('1 2')
    assert tab.save()
    tab.set_case_directory(str(tmp_path))
    assert tab.count.value()==60 and tab.config().density.values==(500,1100)
    commands=[]
    tab.command_requested.connect(lambda *args:commands.append(args))
    tab.run()
    assert not commands  # no mesh/carrier yet
    tab.close()


@pytest.mark.parametrize('with_eligibility',[False,True])
def test_plots_replay_and_persistent_ids(tmp_path,with_eligibility):
    run=tmp_path/'postProcessing/largeTracers/test'
    run.mkdir(parents=True)
    columns=['time','id','x','y','z','vx','vy','vz','active','mobilized','left_region','crossed_rim','diameter','density','inside_region']
    if with_eligibility: columns.append('pair_contact_eligible')
    with (run/'tracks.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns); writer.writeheader()
        for t in (0,1):
            for id in (3,8):
                row=dict(time=t,id=id,x=.1*t,y=.5,z=.3+.1*t,
                    vx=.1,vy=0,vz=.1,active=1,mobilized=t,left_region=0,crossed_rim=0,
                    diameter=.05,density=500+id,inside_region=1)
                if with_eligibility: row['pair_contact_eligible']=int(id==8)
                writer.writerow(row)
    with pytest.raises(ValueError,match='completion'): load_tracks(run)
    (run/'complete').touch()
    data=load_tracks(run)
    assert data['ids']==[3,8] and data['data'].shape==(2,2,11)
    assert data['pair_eligible']==([False,True] if with_eligibility else None)
    out=visualize(run)
    assert (out/'trajectories.png').stat().st_size>1000
    assert '__DATA__' not in (out/'replay.html').read_text()
    assert json.loads((out/'outcomes.json').read_text())['mobilized']==2


@pytest.mark.parametrize('fault',['duplicate','missing','fractional_id','changed_density','nonfinite','invalid_flag'])
def test_track_loader_rejects_corrupt_completed_data(tmp_path,fault):
    run=tmp_path/'tracks'; run.mkdir(); (run/'complete').touch()
    columns=['time','id','x','y','z','vx','vy','vz','active','mobilized','left_region','crossed_rim','diameter','density','inside_region']
    rows=[dict(time=t,id=i,x=0,y=0,z=.3,vx=0,vy=0,vz=0,active=1,mobilized=0,
               left_region=0,crossed_rim=0,diameter=.03,density=700,inside_region=1)
          for t in (0,1) for i in (0,1)]
    if fault=='duplicate': rows.append(dict(rows[0]))
    elif fault=='missing': rows.pop()
    elif fault=='fractional_id': rows[-1]['id']=.5
    elif fault=='changed_density': rows[-1]['density']=701
    elif fault=='nonfinite': rows[-1]['z']=float('nan')
    elif fault=='invalid_flag': rows[-1]['active']=2
    with (run/'tracks.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns); writer.writeheader(); writer.writerows(rows)
    with pytest.raises(ValueError): load_tracks(run)
