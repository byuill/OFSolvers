"""Engineering invariants and case-write safety; native tests cover actual meshes."""
import json
import math
import re
import time

import numpy as np
import pytest

from foam_io import value, boundary_patches
from river_design import (RiverDesign,Obstacle,analyze_design,graded_cells,section,
    normal_depth,critical_depth,channel_surfaces,obstacle_surfaces,fluid_point,ascii_stl)
from river_case import (export_river_case,export_geometry,load_river_case,mesh_commands,
    finalize_river_boundaries,prepare_mesh_check,REGIONS)


def compact_design():
    d=RiverDesign();d.channel.stations=13;d.channel.cross_points=9
    return d


def mock_boundary(case,d,empty=None,omit=None):
    # Metadata is sufficient for field-writing checks, not native mesh acceptance.
    walls=['bed','bankLeft','bankRight',*obstacle_surfaces(d)]
    types={name:('wall' if name in walls else 'symmetryPlane' if name=='lid' and d.channel.mode=='fixed' else 'patch')
           for name in [*REGIONS,*obstacle_surfaces(d)] if name!=omit}
    path=case/'constant/polyMesh/boundary';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('FoamFile { format ascii; class polyBoundaryMesh; object boundary; }\n'
        +str(len(types))+' (\n'+''.join(f'{name} {{ type {kind}; nFaces {0 if name==empty else 1}; startFace 0; }}\n'
         for name,kind in types.items())+')\n')
    return path


def test_hydraulic_depths_match_manning_and_critical_flow():
    q,b,m,s,n=3.,6.,1.5,.001,.03
    depth=normal_depth(q,b,m,s,n);area,radius,_=section(b,m,depth)
    assert area*radius**(2/3)*math.sqrt(s)/n==pytest.approx(q)
    critical=critical_depth(q,b,m);area,_,top=section(b,m,critical)
    assert q*q*top/(9.81*area**3)==pytest.approx(1)
    with pytest.raises(ValueError):normal_depth(q,b,m,0,n)


@pytest.mark.parametrize('length,first,maximum,growth',[(1.2,.01,.4,1.2),(3,.018,.4,1.2),(.1,.1,.2,1.01),(50,.001,.5,1.3)])
def test_grading_is_exact_and_bounded(length,first,maximum,growth):
    sizes=graded_cells(length,first,maximum,growth)
    assert sizes.sum()==pytest.approx(length)
    assert sizes[0]<=first*(1+1e-8) and sizes.max()<=maximum*(1+1e-8)
    assert np.all(sizes>0) and np.max(sizes[1:]/sizes[:-1])<=growth*(1+1e-8)


def test_channel_is_closed_positive_and_preserves_analytic_volume():
    d=compact_design();patches,closed=channel_surfaces(d)
    area,_,_=section(d.channel.width,d.channel.bank_slope,d.channel.depth)
    assert closed.is_manifold and closed.n_open_edges==0
    assert closed.volume==pytest.approx(area*d.channel.length)
    text=ascii_stl(patches)
    assert all('solid '+name+'\n' in text for name in REGIONS)
    report=analyze_design(d)
    assert report['lateral_cells']==list(reversed(report['lateral_cells']))
    assert report['streamwise_size_m']<=6*report['vertical_cells'][0]


def test_bedform_changes_terrain_and_vegetation_produces_separate_solids():
    d=compact_design();d.channel.stations=81;d.channel.cross_points=33
    _,flat=channel_surfaces(d)
    d.obstacles=[Obstacle(name='bar',kind='bedform',x=12,length=8,width=3,height=.2),
                 Obstacle(name='stems',kind='vegetation',x=25,width=.2,length=.2,height=1.5,rows=2,columns=2,spacing=1)]
    assert analyze_design(d)['method']=='snappy'
    _,closed=channel_surfaces(d);solids=obstacle_surfaces(d)
    assert closed.volume<flat.volume and closed.is_manifold
    assert set(solids)=={'stems_0_0','stems_0_1','stems_1_0','stems_1_1'}
    assert all(mesh.is_manifold and mesh.volume>0 for mesh in solids.values())
    assert len(fluid_point(d,closed,solids))==3


@pytest.mark.parametrize('mutate,message',[
    (lambda d:setattr(d.channel,'depth',-1),'positive'),
    (lambda d:setattr(d.channel,'length',float('nan')),'finite'),
    (lambda d:setattr(d.mesh,'growth',1.5),'expansion'),
    (lambda d:setattr(d.mesh,'max_cells',1000),'budget'),
    (lambda d:setattr(d.mesh,'layers',2.5),'integers'),
    (lambda d:d.obstacles.append(Obstacle(name='../unsafe')),'names'),
    (lambda d:d.obstacles.extend([Obstacle(),Obstacle(name='other')]),'overlap'),
    (lambda d:d.obstacles.append(Obstacle(width=.001,height=.01)),'refinement'),
    (lambda d:setattr(d.hydraulic,'hydrograph',[[0,3,.1],[90,4,.2]]),'through endTime'),
    (lambda d:setattr(d.hydraulic,'hydrograph',[[0,3,.1],[100,-1,.2]]),'negative'),
])
def test_invalid_designs_refuse_exports(mutate,message):
    d=compact_design();mutate(d)
    with pytest.raises(ValueError,match=message):analyze_design(d)


def test_case_exports_preserve_existing_data_and_require_named_nonempty_patches(tmp_path):
    d=compact_design();case=tmp_path/'case';case.mkdir()
    export_river_case(case,d)
    assert load_river_case(case)[1].to_dict()==d.to_dict()
    assert mesh_commands(case)==[['blockMesh'],['checkMesh','-allTopology','-meshQuality']]
    path=mock_boundary(case,d,empty='inlet')
    original=(case/'0/U').read_text()
    with pytest.raises(ValueError,match='no faces'):finalize_river_boundaries(case,True)
    assert (case/'0/U').read_text()==original
    with pytest.raises(ValueError,match='mesh or results'):export_river_case(case,d)
    mock_boundary(case,d,omit='outlet')
    with pytest.raises(ValueError,match='outlet'):finalize_river_boundaries(case,True)
    mock_boundary(case,d)
    data=finalize_river_boundaries(case,True)
    assert data['boundaries_ready'] and data['mesh_checked']
    assert 'nutkRoughWallFunction' in (case/'0/nut').read_text()
    assert 'volumetricFlowRate constant 3' in (case/'0/U').read_text()
    assert 'erosionRate 0' in (case/'constant/sedimentProperties').read_text()
    assert 'grad(C) cellLimited Gauss linear 1' in (case/'system/fvSchemes').read_text()
    foreign=tmp_path/'foreign';foreign.mkdir();(foreign/'notes.txt').write_text('keep me')
    with pytest.raises(ValueError,match='empty folder'):export_river_case(foreign,d)
    assert (foreign/'notes.txt').read_text()=='keep me'


def test_obstacle_patches_cannot_silently_disappear(tmp_path):
    d=compact_design();d.obstacles=[Obstacle()]
    export_river_case(tmp_path,d);mock_boundary(tmp_path,d,omit='pier')
    with pytest.raises(ValueError,match='pier'):finalize_river_boundaries(tmp_path,True)


def test_tabular_water_and_concentration_and_snappy_lid_finalization(tmp_path):
    d=compact_design();d.mesh.method='snappy';d.hydraulic.hydrograph=[[0,3,.1],[100,4,.2]]
    export_river_case(tmp_path,d);path=mock_boundary(tmp_path,d)
    path.write_text(path.read_text().replace('type symmetryPlane','type patch'))
    prepare_mesh_check(tmp_path)
    assert {p['name']:p['type'] for p in boundary_patches(path)}['lid']=='symmetry'
    finalize_river_boundaries(tmp_path,True)
    assert 'table ((0 3) (100 4))' in (tmp_path/'0/U').read_text()
    assert 'table ((0 0.1) (100 0.2))' in (tmp_path/'0/C').read_text()


@pytest.mark.parametrize('solver',['sandParcelPimpleFoam','pimpleFoam'])
def test_fixed_solver_variants(tmp_path,solver):
    d=compact_design();d.hydraulic.solver=solver;d.hydraulic.turbulence='kEpsilon'
    export_river_case(tmp_path,d);mock_boundary(tmp_path,d)
    finalize_river_boundaries(tmp_path,True)
    assert value((tmp_path/'system/controlDict').read_text(),'application')==solver
    assert (tmp_path/'0/epsilon').exists()
    if solver=='sandParcelPimpleFoam':
        props=(tmp_path/'constant/sandCloudProperties').read_text()
        assert 'lid { type rebound;' in props
        assert float(re.search(r'massFeedRate\s+(\S+);',props)[1])==pytest.approx(.15)
    else:assert not (tmp_path/'0/C').exists()


def test_vof_and_geometry_exports_are_explicit_mesh_inputs(tmp_path):
    d=compact_design();d.channel.mode='vof'
    case=tmp_path/'case';case.mkdir();export_river_case(case,d)
    assert not (case/'0/U').exists() and not (case/'0/alpha.water').exists()
    mock_boundary(case,d);data=finalize_river_boundaries(case,True)
    assert data['mesh_checked'] and not data['boundaries_ready']
    folder=tmp_path/'stl';folder.mkdir();export_geometry(folder,d)
    assert (folder/'channel_boundary.stl').exists() and (folder/'channel_terrain.stl').exists()


def test_gui_design_roundtrip_profile_and_changed_mesh_guard(qapp,dialogs,tmp_path):
    from river_design_tab import RiverDesignTab
    tab=RiverDesignTab();d=compact_design();d.hydraulic.hydrograph=[[0,3,.1],[100,4,.2]]
    tab.set_design(d);assert tab.design().to_dict()==d.to_dict()
    tab.preview_design=d;tab.draw_profile()
    assert len(tab.profile_figure.axes)==1
    assert 'Design cross-section' in tab.profile_figure.axes[0].get_title()
    tab.set_case_directory(str(tmp_path));tab.create_case()
    signals=[];tab.mesh_requested.connect(lambda:signals.append(True))
    tab.inputs[('channel','length')].setValue(50)
    tab.request_mesh()
    assert not signals and 'differ' in dialogs[-1][1]
    assert json.loads((tmp_path/'river-design.json').read_text())['design']['channel']['length']==40
    tab.plotter.close();tab.close()


@pytest.mark.parametrize('fail_block,check_output,expected',[
    (False,'Number of regions: 1\nMesh OK.',True),
    (False,'Number of regions: 2\nMesh OK.',False),
    (False,'Number of regions: 1\nFailed 1 mesh checks.',False),
    (False,'Mesh OK.',False),
    (True,'Number of regions: 1\nMesh OK.',False),
])
def test_gui_mesh_pipeline_stops_on_native_failure(qapp,tmp_path,monkeypatch,fail_block,check_output,expected):
    from execution_tab import ExecutionTab
    case=tmp_path/'case';case.mkdir();export_river_case(case,compact_design())
    binaries=tmp_path/'bin';binaries.mkdir()
    for name in ('blockMesh','checkMesh'):
        # Real child processes exercise asynchronous chaining and captured output;
        # native geometry correctness is covered by validate_river_design.py.
        text=check_output if name=='checkMesh' else 'background mesh created'
        script=binaries/name
        script.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\n'
            +f'Path("called-{name}").write_text("yes")\nprint({text!r})\n'
            +('raise SystemExit(1)\n' if fail_block and name=='blockMesh' else ''))
        script.chmod(0o755)
    import os
    monkeypatch.setenv('PATH',str(binaries)+os.pathsep+os.environ['PATH'])
    tab=ExecutionTab();tab.set_case_directory(str(case));results=[]
    tab.river_mesh_finished.connect(results.append);tab.run_river_mesh()
    deadline=time.monotonic()+8
    while not results and time.monotonic()<deadline:qapp.processEvents();time.sleep(.005)
    assert results==[expected] and tab.worker is None
    assert (case/'called-checkMesh').exists() is (not fail_block)
    assert not tab._river_queue and not tab._river_active
    tab.close()
