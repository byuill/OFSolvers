from collections import Counter
from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
import pyvista as pv
import pytest

from castle_flume import (FlumeConfig, castle_profile, castle_triangles,
                          generate_case)
from foam_io import entries, value
from sediment_setup import case_patches


def test_castle_closed_oriented_with_open_arch():
    triangles = castle_triangles()
    edges = Counter()
    directed = Counter()
    for tri in triangles:
        for a,b in zip(tri,tri[1:]+tri[:1]):
            edges[tuple(sorted((a,b)))] += 1
            directed[(a,b)] += 1
    assert set(edges.values()) == {2}
    assert all(directed[(a,b)] == directed[(b,a)] for a,b in directed)
    points = np.array(triangles).reshape(-1,3)
    np.testing.assert_allclose(points.min(axis=0),[1,.075,0])
    np.testing.assert_allclose(points.max(axis=0),[2,.175,.75])
    volume = sum(np.dot(a,np.cross(b,c))/6 for a,b,c in np.array(triangles))
    assert .04 < volume < .07
    surface = pv.PolyData(points,np.array([[3,3*i,3*i+1,3*i+2] for i in range(len(triangles))])).clean()
    assert surface.is_manifold
    probes = pv.PolyData(np.array([[1.5,.125,.15],[1.5,.125,.35],
                                  [1.5,.125,.45],[1.1,.125,.2],
                                  [1.5,.03,.45],[1.5,.125,.7]]))
    inside = probes.select_interior_points(surface,method='cell_locator')['selected_points']
    assert list(inside) == [0,0,1,1,0,0]
    assert max(z for x,z in castle_profile() if 1.34 < x < 1.66 and z < .4) == pytest.approx(.38)


@pytest.mark.parametrize('depth',[.5,1])
@pytest.mark.parametrize('velocity',[.1,1])
def test_cases_depths_speed_endpoints_and_refinement(tmp_path,depth,velocity):
    generate_case(tmp_path,FlumeConfig(depth=depth,velocity=velocity,erosion_rate=1e-5))
    patches = case_patches(tmp_path)
    top = 'atmosphere' if depth == .5 else 'lid'
    expected = {p['name'] for p in patches} | {'castle'}
    assert expected == {'upstream','downstream','leftWall','rightWall','bed',top,'castle'}
    for path in (tmp_path/'0').iterdir():
        conditions = value(path.read_text(),'boundaryField')[1:-1]
        assert set(entries(conditions)) == expected, path.name
        top_type = value(value(conditions,top)[1:-1],'type')
        if depth == 1:
            assert top_type == ('slip' if path.name == 'U' else 'calculated' if path.name in ('nut','Mbed') else 'zeroGradient')
        else:
            assert top_type == ('prghTotalPressure' if path.name == 'p_rgh' else 'pressureInletOutletVelocity' if path.name == 'U' else 'calculated' if path.name in ('nut','Mbed') else 'inletOutlet')
        assert 'empty' not in conditions
    text = (tmp_path/'system/snappyHexMeshDict').read_text()
    regions = value(value(text,'castellatedMeshControls')[1:-1],'refinementRegions')[1:-1]
    assert set(entries(regions)) == {'obstacle','arch','bed','gapLeft','gapRight','wake'}
    assert 'castle.eMesh' in text
    assert 'explicitFeatureSnap true;' in text
    assert float(value((tmp_path/'system/meshQualityDict').read_text(),'maxNonOrtho')) == 65
    assert '-allTopology -meshQuality' in (tmp_path/'Allmesh').read_text()
    assert 'patchInfo { type wall; }' in text
    assert '(120 10 '+str(40)+')' in (tmp_path/'system/blockMeshDict').read_text().replace('( ', '(').replace(' )',')')
    u = (tmp_path/'0/U').read_text()
    assert value(u,'internalField') == (f'uniform ({velocity:g} 0 0)' if depth == 1 else 'uniform (0 0 0)')
    if depth == .5:
        assert not (tmp_path/'0/p').exists()
        assert (tmp_path/'0/alpha.water').exists()
        assert 'setFields' in (tmp_path/'Allmesh').read_text()
        assert 'boxToFace' in (tmp_path/'system/setFieldsDict').read_text()
        assert 'sedimentInterFoam' in (tmp_path/'system/controlDict').read_text()
        assert float(value((tmp_path/'system/controlDict').read_text(),'maxAlphaCo')) == .25
        assert 'variableHeightFlowRateInletVelocity' in u
    props = (tmp_path/'constant/sedimentProperties').read_text()
    assert value(props,'bedPatches') == '(bed)'
    assert float(value(props,'erosionRate')) == 1e-5
    manifest = json.loads((tmp_path/'flume.json').read_text())
    assert manifest['flume_dimensions_m'] == [3,.25,1]
    assert manifest['obstacle_bounds_m'] == [1,2,.075,.175,0,.75]
    assert manifest['settings']['depth'] == depth
    assert (tmp_path/'geometry.svg').is_file()
    assert (tmp_path/'Allmesh').stat().st_mode & 0o111
    assert (tmp_path/'constant/triSurface/castle.stl').exists()
    assert pv.read(tmp_path/'constant/triSurface/castle.stl').is_manifold


@pytest.mark.parametrize('kwargs',[{'velocity':.09},{'velocity':1.01},{'depth':.75},
    {'diameter':0},{'inlet_concentration':-1},{'bed_inventory':-1},
    {'erosion_rate':-1},{'end_time':0},{'velocity':math.nan},{'depth':math.inf}])
def test_invalid_inputs_do_not_write(tmp_path,kwargs):
    with pytest.raises(ValueError):
        generate_case(tmp_path,replace(FlumeConfig(),**kwargs))
    assert not list(tmp_path.iterdir())


def test_no_overwrite_and_cli(tmp_path):
    marker = tmp_path/'keep'
    marker.write_text('untouched')
    with pytest.raises(ValueError,match='EMPTY'):
        generate_case(tmp_path)
    assert marker.read_text() == 'untouched'
    fresh = tmp_path/'new case'
    fresh.mkdir()
    result = subprocess.run([sys.executable,str(Path(__file__).parents[1]/'castle_flume.py'),
                             str(fresh),'--depth','1','--velocity','1'],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert (fresh/'flume.json').is_file()


def test_flume_workbench_generation_and_mesh_request(qapp,tmp_path,dialogs):
    from main_window import MainWindow
    window = MainWindow()
    window.set_case_directory(str(tmp_path))
    tab = window.tab_flume
    tab.velocity.setValue(1)
    tab.depth.setCurrentText('1')
    tab.generate_button.click()
    assert not dialogs
    assert window.solver_combo.currentText() == 'sedimentPimpleFoam'
    assert window.tab_execution.case_dir == str(tmp_path)
    assert tab.mesh_button.isEnabled()
    calls = []
    window.tab_execution.execute_command = lambda *args: calls.append(args)
    tab.mesh_button.click()
    assert calls == [(['sh','./Allmesh'],'castle_mesh')]
    window.set_busy(True)
    assert not window.tabs.isTabEnabled(window.tabs.indexOf(tab))
    window.set_busy(False)
    window.close()


def test_background_only_mesh_cannot_pass_castle_preflight(tmp_path):
    from case_qaqc import check_case
    generate_case(tmp_path)
    patches = case_patches(tmp_path)
    mesh = tmp_path/'constant/polyMesh'
    mesh.mkdir()
    for name in ('points','faces','owner','neighbour'):
        (mesh/name).write_text('placeholder')
    (mesh/'boundary').write_text('FoamFile { class polyBoundaryMesh; object boundary; }\n'
        + str(len(patches))+'\n(\n'+''.join(
            f'{p["name"]} {{ type {p["type"]}; nFaces 1; startFace 0; }}\n' for p in patches)+')\n')
    report = check_case(tmp_path,'sedimentPimpleFoam',check_programs=False)
    assert any('missing its castle wall' in e for e in report.errors)


def test_flume_tab_loads_existing_settings(qapp,tmp_path):
    from flume_tab import FlumeTab
    generate_case(tmp_path,FlumeConfig(depth=1,velocity=.8,erosion_rate=.0001))
    tab = FlumeTab()
    assert not tab.mesh_button.isEnabled()
    tab.set_case_directory(str(tmp_path))
    assert tab.depth.currentText() == '1'
    assert tab.velocity.value() == .8
    assert tab.settings['erosion_rate'].value() == .0001
    assert tab.mesh_button.isEnabled()
    (tmp_path/'flume.json').write_text('{broken')
    tab.set_case_directory(str(tmp_path))
    assert not tab.mesh_button.isEnabled()
    tab.close()


def test_vof_preflight_and_gui_solver_profile(qapp,tmp_path,dialogs):
    from main_window import MainWindow
    from case_qaqc import check_case
    window = MainWindow()
    window.set_case_directory(str(tmp_path))
    window.tab_flume.generate_button.click()
    assert not dialogs
    assert window.solver_combo.currentText() == 'sedimentInterFoam'
    assert window.tab_execution.combo_solver.currentText() == 'sedimentInterFoam'
    window.tab_boundaries.right_widget_container.setEnabled(True)
    assert window.tab_boundaries.btn_write_0.isEnabled()
    assert not window.tab_sediment.btn_write.isEnabled()
    report = check_case(tmp_path,'sedimentInterFoam',check_programs=False)
    assert not any('Missing 0/' in e for e in report.errors)
    # With a mesh present, preflight must also reject an uninitialized all-air state.
    patches = case_patches(tmp_path)
    mesh = tmp_path/'constant/polyMesh'
    mesh.mkdir()
    for name in ('points','faces','owner','neighbour'):
        (mesh/name).write_text('placeholder')
    (mesh/'boundary').write_text('FoamFile { class polyBoundaryMesh; object boundary; }\n'
        + str(len(patches))+'\n(\n'+''.join(
            f'{p["name"]} {{ type {p["type"]}; nFaces 1; startFace 0; }}\n' for p in patches)+')\n')
    report = check_case(tmp_path,'sedimentInterFoam',check_programs=False)
    assert any('Initialize the half-depth water phase' in e for e in report.errors)
    assert window.dim_x_spinbox.value() == 3
    assert window.dim_y_spinbox.value() == .25
    assert window.dim_z_spinbox.value() == 1
    assert window.sim_combo.currentText() == 'RAS'
    (tmp_path/'0/alpha.water').unlink()
    report = check_case(tmp_path,'sedimentInterFoam',check_programs=False)
    assert any('Missing 0/alpha.water' in e for e in report.errors)
    window.close()


def test_nonuniform_vector_preflight_keeps_boundary_checks(tmp_path):
    from case_qaqc import check_case
    generate_case(tmp_path,FlumeConfig(depth=1))
    patches = case_patches(tmp_path)+[{'name':'castle','type':'wall'}]
    mesh = tmp_path/'constant/polyMesh'
    mesh.mkdir()
    for name in ('points','faces','owner','neighbour'):
        (mesh/name).write_text('placeholder')
    (mesh/'boundary').write_text('FoamFile { class polyBoundaryMesh; object boundary; }\n'
        + str(len(patches))+'\n(\n'+''.join(
            f'{p["name"]} {{ type {p["type"]}; nFaces 1; startFace 0; }}\n' for p in patches)+')\n')
    path = tmp_path/'0/U'
    path.write_text(path.read_text().replace('internalField uniform (0.3 0 0);',
        'internalField nonuniform List<vector> 2 ((0.3 0 0) (0.3 0 0));'))
    report = check_case(tmp_path,'sedimentPimpleFoam',check_programs=False)
    assert report.ok, report.errors


@pytest.mark.parametrize('preset',['castle','scour'])
@pytest.mark.parametrize('compressed',[False,True])
def test_generated_mesh_scripts_preserve_existing_mesh(tmp_path,preset,compressed):
    import subprocess
    from scour_tracer_demo import generate_demo
    if preset=='castle': generate_case(tmp_path)
    else: generate_demo(tmp_path)
    mesh=tmp_path/'constant/polyMesh'; mesh.mkdir()
    path=mesh/('points.gz' if compressed else 'points'); path.write_bytes(b'preserve mesh')
    before=(tmp_path/'0/U').read_bytes()
    result=subprocess.run(['sh','./Allmesh'],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode!=0 and 'A mesh already exists' in result.stderr
    assert path.read_bytes()==b'preserve mesh' and (tmp_path/'0/U').read_bytes()==before
    assert not (tmp_path/'log.blockMesh').exists()
