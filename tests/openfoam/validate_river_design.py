#!/usr/bin/env python3
"""Real OpenCFD v1912 mesh/solver checks; activate OpenFOAM and the Python venv.

Retains every generated case and command log outside the checkout. This checks
export/runtime contracts and numerical closure, not hydraulic validation.
"""
import argparse
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from foam_io import value, update_values, boundary_patches
from river_design import Channel, MeshDesign, Hydraulics, Obstacle, RiverDesign
from river_case import (export_river_case, mesh_commands, prepare_mesh_check,
                        finalize_river_boundaries)

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--case',action='append',choices=['eulerian','parcel','pier','stems','terrain','vof'],
                    help='Run only the selected case(s); default runs all.')
selected=parser.parse_args().case
WORK=Path(tempfile.mkdtemp(prefix='river-design-validation-'))
print('Validation artifacts:',WORK,flush=True)
for binary in ('blockMesh','surfaceFeatureExtract','snappyHexMesh','checkMesh',
               'sedimentPimpleFoam','sandParcelPimpleFoam'):
    assert shutil.which(binary),f'Activate OpenFOAM and build {binary} first.'

def command(case,args):
    path=case/('log.'+args[0])
    with path.open('w') as stream:
        run=subprocess.run(args,cwd=case,stdout=stream,stderr=subprocess.STDOUT)
    text=path.read_text()
    assert run.returncode==0,(case,args,text[-4000:])
    return text

def design():
    return RiverDesign(Channel(length=8,width=3,depth=.8,bank_slope=1,stations=65),
                       MeshDesign(base_size=.3,wall_size=.15,first_height=.02,layers=2,
                                  cells_per_obstacle=6,max_cells=500000),
                       Hydraulics(discharge=.15,inlet_c=.02,initial_c=.01,end_time=.02))

def mesh(name,d):
    case=WORK/name;case.mkdir();export_river_case(case,d)
    for args in mesh_commands(case):
        if args[0]=='checkMesh':prepare_mesh_check(case)
        text=command(case,args)
    assert 'Mesh OK.' in text and re.search(r'Number of regions:\s+1\b',text),(case,text[-3000:])
    data=finalize_river_boundaries(case,mesh_checked=True)
    assert data['mesh_checked']
    count=re.search(r'cells:\s+(\d+)',text)
    print(f'PASS: {name}, {count[1] if count else "?"} cells, topology and explicit meshQuality checks',flush=True)
    return case

def scalars(text,entry):
    raw=value(text,entry)
    if raw.startswith('uniform '):return [float(raw.split()[1])]
    match=re.fullmatch(r'nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\)',raw,re.S)
    assert match,raw[:100]
    result=[float(x) for x in match[2].split()]
    assert len(result)==int(match[1])
    return result

def patch_values(case,time,field,patch):
    bf=value((case/time/field).read_text(),'boundaryField')[1:-1]
    return scalars(value(bf,patch)[1:-1],'value')

def solve(case,solver):
    path=case/'system/controlDict'
    path.write_text(update_values(path.read_text(),{'endTime':.003,'deltaT':.001,
        'maxDeltaT':.001,'writeInterval':.003,'writePrecision':14}))
    text=command(case,[solver])
    assert text.rstrip().endswith('End'),case
    inlet=sum(patch_values(case,'0.003','phi','inlet'))
    expected=.15+(.2-.15)*.003/.02 if solver=='sedimentPimpleFoam' else .15
    assert math.isclose(-inlet,expected,rel_tol=1e-8),(case,inlet,expected)
    if solver=='sedimentPimpleFoam':
        rows=re.findall(r'Sediment budget: suspended=(\S+) bed=(\S+) externalOut=(\S+) residual=(\S+) kg; min\(C\)=(\S+)',text)
        assert rows and 'Sediment Courant Number mean:' in text
        for row in rows:
            suspended,bed,external,residual,minc=map(float,row)
            assert all(map(math.isfinite,(suspended,bed,external,residual,minc)))
            assert abs(residual)<1e-10 and minc>=-1e-12 and bed>=0
        assert max(abs(x) for x in patch_values(case,'0.003','phiSed','lid'))==0
    else:
        # Mass of injected parcels must match Q*C*t; no losses in this short run.
        masses=re.findall(r'Sand inventory: mobile=(\S+) deposited=(\S+) kg',text)
        assert masses,text[-3000:]
        assert math.isclose(sum(map(float,masses[-1])),.15*.02*.003,rel_tol=1e-8)
        props=(case/'constant/sandCloudProperties').read_text()
        assert 'lid' in props and 'rebound' in props
    print('PASS:',solver,'time-varying/constant discharge, sediment boundary and short-run checks',flush=True)

if not selected or 'eulerian' in selected:
    euler=design();euler.hydraulic.hydrograph=[[0,.15,.02],[.02,.2,.03]]
    solve(mesh('graded-eulerian',euler),'sedimentPimpleFoam')
if not selected or 'parcel' in selected:
    parcel=design();parcel.hydraulic.solver='sandParcelPimpleFoam';parcel.hydraulic.turbulence='kEpsilon'
    solve(mesh('graded-parcel',parcel),'sandParcelPimpleFoam')
if not selected or 'pier' in selected:
    pier=design();pier.obstacles=[Obstacle(x=4,width=.5,length=.5,height=.4)]
    mesh('snappy-pier',pier)
if not selected or 'stems' in selected:
    stems=design();stems.obstacles=[Obstacle(name='stems',kind='vegetation',x=4,width=.25,length=.25,
                                          height=1,rows=1,columns=2,spacing=.7)]
    mesh('snappy-stems',stems)
if not selected or 'terrain' in selected:
    terrain=design();terrain.obstacles=[
        Obstacle(name='bar',kind='bedform',x=2,length=1.5,width=1,height=.15),
        Obstacle(name='rock',kind='boulder',x=3.7,length=.7,width=.5,height=.3,angle=30),
        Obstacle(name='groyne',kind='box',x=5.5,y=1.35,length=.4,width=.9,height=.5,attach_bank=True)]
    mesh('snappy-terrain-structures',terrain)
if not selected or 'vof' in selected:
    vof=design();vof.channel.mode='vof';vof.channel.freeboard=.4
    vof_case=mesh('vof-geometry',vof)
    assert not (vof_case/'0/U').exists() and not (vof_case/'0/alpha.water').exists()
    assert {p['name']:p['type'] for p in boundary_patches(vof_case/'constant/polyMesh/boundary')}['lid']=='patch'
print('Selected native river-design checks passed. VOF validation covers meshing only.',flush=True)
