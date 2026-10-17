#!/usr/bin/env python3
"""Real v1912 mesh and two-step sediment smoke checks; activate OpenFOAM first.

python tests/openfoam/validate_castle_flume.py --output /tmp/castle-validation
Uses 0.1 m/s at 0.5 m depth and 1 m/s at 1 m depth. Not a converged CFD study.
"""
import argparse
import math
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from castle_flume import FlumeConfig, generate_case
from case_qaqc import check_case, mesh_passed
from foam_io import value, update_values


def check_generated_mesh(case):
    import numpy as np
    import pyvista as pv
    depth = json.loads((case/'flume.json').read_text())['settings']['depth']
    reader = pv.OpenFOAMReader(case/'case.foam')
    reader.disable_all_cell_arrays()
    reader.disable_all_patch_arrays()
    reader.enable_patch_array('internalMesh')
    reader.cell_to_point_creation = False
    mesh = reader.read()['internalMesh']
    np.testing.assert_allclose(mesh.bounds,(0,3,0,.25,0,1),atol=1e-9)
    # Probe the actual snapped fluid mesh, not only the input STL.
    probes = [[1.5,.125,.2],[1.1,.125,.2],[1.5,.035,.45],
              [1.5,.215,.45],[2.3,.125,.4]]
    ids = mesh.find_containing_cell(np.array(probes))
    assert ids[0] >= 0 and ids[1] < 0 and all(ids[i] >= 0 for i in (2,3,4)), ids
    if depth == 1:
        assert mesh.find_containing_cell([1.5,.125,.9]) >= 0
    print(f'Validated {mesh.n_cells} cells: arch, side gaps and wake are fluid; castle is excluded.',flush=True)


def check_interface(case):
    import numpy as np
    def alpha(path):
        text = path.read_text()
        match = re.search(r'internalField\s+nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\)\s*;',text,re.S)
        if match:
            values = np.fromstring(match[2],sep=' ')
            assert len(values) == int(match[1])
            return values
        return np.array([float(value(text,'internalField').split()[1])])
    saved = sorted((float(p.name),p) for p in case.iterdir()
                   if p.is_dir() and re.fullmatch(r'[0-9.eE+-]+',p.name) and (p/'alpha.water').is_file())
    assert saved[-1][0] > 0, 'No saved free-surface state'
    initial = alpha(case/'0/alpha.water')
    final = alpha(saved[-1][1]/'alpha.water')
    assert np.isfinite(final).all() and final.min() >= -1e-8 and final.max() <= 1+1e-8
    change = float(np.max(np.abs(final-initial)))
    assert change > 1e-10, 'The free surface did not evolve'
    print(f'VOF interface evolved: max alpha change={change:.6g}; bounded alpha [{final.min():.6g}, {final.max():.6g}]',flush=True)


def check_generated_case(case):
    assert mesh_passed((case/'log.checkMesh').read_text(),0)
    check_generated_mesh(case)
    solver = value((case/'system/controlDict').read_text(),'application')
    report = check_case(case,solver)
    assert report.ok,report.errors
    environment = os.environ.copy()
    environment['PWD'] = str(case)
    with (case/('log.'+solver)).open('w') as log:
        subprocess.run([solver],cwd=case,stdout=log,
                       stderr=subprocess.STDOUT,check=True,env=environment)
    text = (case/('log.'+solver)).read_text()
    residuals = [float(v) for v in re.findall(r'residual=([^\s]+) kg',text)]
    assert residuals and all(math.isfinite(v) and abs(v)<1e-8 for v in residuals)
    minima = [float(v) for v in re.findall(r'min\(C\)=([^\s]+)',text)]
    assert minima and all(math.isfinite(v) and v >= -1e-10 for v in minima)
    assert text.rstrip().endswith('End') and 'FOAM FATAL' not in text
    if solver == 'sedimentInterFoam':
        check_interface(case)


def validate(output, include_fast_shallow=False):
    cases = [(.5,.1),(1.,1.)]
    if include_fast_shallow:
        cases.append((.5,1.))
    for depth,velocity in cases:
        case = output/f'depth-{depth:g}-speed-{velocity:g}'
        case.mkdir(parents=True)
        generate_case(case,FlumeConfig(depth=depth,velocity=velocity,
                                      erosion_rate=1e-5,end_time=.0002 if depth == .5 and velocity == 1 else .001))
        control = case/'system/controlDict'
        if not (depth == .5 and velocity == 1):
            control.write_text(update_values(control.read_text(),{'deltaT':.0005,'maxDeltaT':.0005}))
        subprocess.run(['sh','./Allmesh'],cwd=case,check=True)
        check_generated_case(case)
        print(f'PASS depth={depth:g} velocity={velocity:g}: {case}',flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path,help='Fresh validation directory')
    parser.add_argument('--include-fast-shallow',action='store_true',help='Also test the 1 m/s VOF endpoint')
    args = parser.parse_args()
    validate(args.output.resolve(),args.include_fast_shallow)
