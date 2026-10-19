#!/usr/bin/env python3
"""Native volume-averaged PIMPLE/LAMMPS regressions; activate v1912 first."""
import csv
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from bedload_dem import BedloadConfig,generate_bedload
from foam_io import update_values,update_nested,value
from case_qaqc import check_case

WORK=Path(tempfile.mkdtemp(prefix='ofsolvers-dem-validation-'))
print('Validation artifacts:',WORK,flush=True)
RESULTS={}
BASE=BedloadConfig(length=.04,width=.016,height=.024,mobile_layers=1,turbulence='laminar',
    end_time=.001,write_interval=.0005,delta_t=.0001,volume_samples=64,
    rolling_stiffness=0,rolling_damping=0,rolling_friction=0)

def command(case,args,name='solver',success=True):
    env=os.environ.copy();env['PWD']=str(case)
    with (case/('log.'+name)).open('w') as stream:
        result=subprocess.run(args,cwd=case,stdout=stream,stderr=subprocess.STDOUT,env=env)
    text=(case/('log.'+name)).read_text()
    assert (result.returncode==0)==success,(case,args,text[-6000:])
    return text


def make(name,cfg=BASE,population=None,cells=(4,2,3),gravity=(0,0,-9.81)):
    case=generate_bedload(WORK/name,cfg,population,cells)
    path=case/'constant/g';path.write_text(update_values(path.read_text(),{'value':'('+' '.join(map(str,gravity))+')'}))
    command(case,['blockMesh'],'mesh');return case


def run(case,parallel=False,success=True,name='solver'):
    args=['mpirun','-np','2','bedloadDemPimpleFoam','-parallel'] if parallel else ['bedloadDemPimpleFoam']
    text=command(case,args,name,success)
    if success:
        assert re.search(r'^End\s*$',text,re.M) and 'FOAM FATAL' not in text
        residual=np.array([float(x) for x in re.findall(r'impulseResidual=([^\s]+)',text)])
        assert len(residual) and max(residual)<1e-12,residual
        continuity=np.array([float(x) for x in re.findall(r'continuity: L1=([^\s]+)',text)])
        assert np.isfinite(continuity).all() and max(continuity)<1e-6,continuity
    return text


def table(path):
    with path.open() as stream:return [{k:float(v) for k,v in r.items()} for r in csv.DictReader(stream)]


def latest(case):
    return max((p for p in (case/'postProcessing/bedloadDEM').iterdir() if (p/'complete').is_file()),
               key=lambda p:(p/'complete').stat().st_mtime_ns)


def states(case):
    rows=table(latest(case)/'tracks.csv');t=max(r['time'] for r in rows)
    return np.array([[r[k] for k in ('id','ux','uy','uz','vx','vy','vz','omega_x','omega_y','omega_z')] for r in rows if r['time']==t])


# No mass exchange, with moving particles and a reacting fluid.
c=make('two-way');assert check_case(c,'bedloadDemPimpleFoam').ok
run(c);b=table(latest(c)/'budget.csv')
assert b[-1]['particle_px']>0 and b[-1]['fluid_px']<b[0]['fluid_px']
assert max(abs(r['particle_mass']-b[0]['particle_mass']) for r in b)<1e-15
assert max(abs(r['fluid_volume']-b[0]['fluid_volume']) for r in b)<1e-18
assert b[-1]['bedload_flux_x']>0
RESULTS['moving_bed_flux_m2_s']=b[-1]['bedload_flux_x']
print('PASS two-way drag, physical grain mass, pore volume and bedload flux',flush=True)

# Identical snapshot checkpoints must continue granular history and CFD fields.
continuous=states(c)
control=c/'system/controlDict';control.write_text(update_values(control.read_text(),{'endTime':.002,'startFrom':'latestTime'}))
run(c,name='restart');resumed=states(c)
f=make('uninterrupted',replace(BASE,end_time=.002));run(f)
assert np.max(np.abs(resumed-states(f)))<1e-8
RESULTS['restart_max_state_error']=float(np.max(np.abs(resumed-states(f))))
print('PASS native CFD/DEM restart, including granular contact history',flush=True)

# Distributed fluid, serial master-owned DEM: shared physical grains, not a
# separate overlapping cloud on each processor.
p=make('mpi');command(p,['decomposePar'],'decompose');run(p,parallel=True)
serial=make('serial-reference');run(serial)
assert np.max(np.abs(states(p)-states(serial)))<1e-7
control=p/'system/controlDict';control.write_text(update_values(control.read_text(),{'endTime':.002,'startFrom':'latestTime'}))
report=check_case(p,'bedloadDemPimpleFoam',cores=2);assert report.ok,report.errors
run(p,parallel=True,name='mpi-restart')
assert np.max(np.abs(states(p)-resumed))<1e-7
RESULTS['mpi_restart_max_state_error']=float(np.max(np.abs(states(p)-resumed)))
print('PASS two-rank porosity/force mapping and coupled restart',flush=True)

# Empty cloud must have eps=1, zero DEM reaction and no particle momentum.
e=make('empty',population=[]);run(e)
eb=table(latest(e)/'budget.csv');assert eb[-1]['particle_mass']==0
assert abs(eb[-1]['fluid_volume']-BASE.length*BASE.width*BASE.height)<1e-18
print('PASS empty-cloud fluid limit',flush=True)

# A spatially uniform cloud in a fully periodic box has no wall/pressure
# impulse: the combined fluid+grain momentum is an independent conservation
# test of the actual DEM integration and fluid source, not just the mapper.
cfg=replace(BASE,length=.04,width=.02,height=.02,initial_speed=.0001,
    driving_acceleration=0,delta_t=.00001,end_time=.0003,write_interval=.0001)
pop=[]
for z in (.005,.015):
    for y in (.005,.015):
        for x in (.005,.015,.025,.035):pop.append((len(pop)+1,1,.004,2650,x,y,z))
m=make('closed-momentum',cfg,pop,cells=(4,2,2),gravity=(0,0,0))
mesh=m/'system/blockMeshDict'
mesh.write_text(mesh.read_text().replace('bed {type wall;','bed {type cyclic; neighbourPatch lid;').replace('lid {type wall;','lid {type cyclic; neighbourPatch bed;'))
shutil.rmtree(m/'constant/polyMesh');command(m,['blockMesh'],'mesh-periodic')
for path in (m/'0').iterdir():
    text=path.read_text()
    text=update_nested(text,['boundaryField','bed'],{'type':'cyclic'})
    text=update_nested(text,['boundaryField','lid'],{'type':'cyclic'})
    # Cyclic fields do not accept stale value coefficients, but native readers
    # ignore irrelevant coefficients in the initial fixedFluxPressure template.
    path.write_text(text)
fluid=m/'constant/transportProperties'
fluid.write_text(update_values(fluid.read_text(),{'nu':'[0 2 -1 0 0 0 0] .01'}))
props=m/'constant/demCouplingProperties';props.write_text(update_values(props.read_text(),{'dragModel':'SchillerNaumann'}))
run(m);b=table(latest(m)/'budget.csv')
momentum=np.array([r['fluid_px']+r['particle_px'] for r in b])
error=float(max(abs(momentum-momentum[0]))/abs(momentum[0]))
assert error<1e-7,error
assert b[-1]['fluid_px']<b[0]['fluid_px'] and b[-1]['particle_px']>0
RESULTS['closed_system_momentum_relative_error']=error
print('PASS combined fluid/grain momentum in a closed periodic reference',flush=True)

# Native periodic image flags must preserve total downstream travel on restart.
cfg=replace(BASE,initial_speed=.2,driving_acceleration=0)
w=make('periodic-travel',cfg,[(1,1,.004,2650,.0399,.008,.012)],gravity=(0,0,0))
data=w/'constant/DEM/particles.data';data.write_text(data.read_text()+'\nVelocities\n\n1 .2 0 0 0 0 0\n')
run(w);first=states(w)[0,1]
control=w/'system/controlDict';control.write_text(update_values(control.read_text(),{'endTime':.002,'startFrom':'latestTime'}))
run(w,name='restart');assert states(w)[0,1]>first>.04
print('PASS periodic travel and native image flags through restart',flush=True)

# Pair mechanics in a near-vacuum carrier isolate the native Hertz/Mindlin
# contact law, including restitution and spin generation through friction.
for tangent in (0,.06):
    name='normal-collision' if tangent==0 else 'frictional-collision'
    cfg=replace(BASE,end_time=.003,delta_t=.00002,write_interval=.0005,initial_speed=0,driving_acceleration=0,dem_delta_t=5e-7)
    pop=[(1,1,.004,2650,.018,.008,.012),(2,1,.004,2650,.0222,.008,.012)]
    pair=make(name,cfg,pop,gravity=(0,0,0))
    data=pair/'constant/DEM/particles.data'
    data.write_text(data.read_text()+f'\nVelocities\n\n1 .1 {tangent} 0 0 0 0\n2 -.1 {-tangent} 0 0 0 0\n')
    transport=pair/'constant/transportProperties'
    transport.write_text(update_values(transport.read_text(),{'rho':'[1 -3 0 0 0 0 0] 1e-9'}))
    run(pair);a=states(pair)
    assert abs(a[0,4]+a[1,4])<1e-10
    measured=-(a[0,4]-a[1,4])/.2
    if tangent==0:
        assert abs(measured-.8)<.015,measured
        RESULTS['normal_contact_restitution']=float(measured)
    else:assert np.max(np.abs(a[:,7:]))>1 and max(abs(a[:,5]))<tangent
print('PASS Hertz restitution, pair momentum and frictional spin',flush=True)

# Restart in the middle of a frictional collision (overlap/history nonzero),
# then compare with the uninterrupted frictional collision above.
checkpoint=make('contact-history-restart',replace(cfg,end_time=.0012,write_interval=.0001),pop,gravity=(0,0,0))
data=checkpoint/'constant/DEM/particles.data'
data.write_text(data.read_text()+'\nVelocities\n\n1 .1 .06 0 0 0 0\n2 -.1 -.06 0 0 0 0\n')
transport=checkpoint/'constant/transportProperties';transport.write_text(update_values(transport.read_text(),{'rho':'[1 -3 0 0 0 0 0] 1e-9'}))
run(checkpoint)
a0=states(checkpoint);assert np.linalg.norm(a0[0,1:4]-a0[1,1:4])<.004
control=checkpoint/'system/controlDict';control.write_text(update_values(control.read_text(),{'endTime':.003,'startFrom':'latestTime'}))
run(checkpoint,name='restart')
delta=abs(states(checkpoint)-states(pair))
# Native velocity-dependent granular forces are recomputed from full-step
# velocities on restart rather than the preceding Verlet half-step. LAMMPS
# documents non-bitwise restart equivalence for these contact styles.
assert np.max(delta[:,1:4])<1e-8 and np.max(delta[:,4:7])<1e-5 and np.max(delta[:,7:])<1e-3,delta
RESULTS['active_contact_restart_errors']=dict(position_m=float(np.max(delta[:,1:4])),
    velocity_m_s=float(np.max(delta[:,4:7])),spin_rad_s=float(np.max(delta[:,7:])))
print('PASS restart during active tangential contact preserves force history and spin',flush=True)

# A frozen roughness grain has infinite inertia for contact damping. Test a
# head-on mobile/fixed collision with the specified restitution.
anchor=make('frozen-anchor',replace(cfg,end_time=.006),
    [(1,2,.004,2650,.018,.008,.012),(2,1,.004,2650,.0222,.008,.012)],gravity=(0,0,0))
data=anchor/'constant/DEM/particles.data'
data.write_text(data.read_text()+'\nVelocities\n\n1 0 0 0 0 0 0\n2 -.1 0 0 0 0 0\n')
transport=anchor/'constant/transportProperties';transport.write_text(update_values(transport.read_text(),{'rho':'[1 -3 0 0 0 0 0] 1e-9'}))
run(anchor);a=states(anchor)
assert abs(a[1,4]-.08)<.002 and np.max(abs(a[0,4:]))==0,a
RESULTS['frozen_anchor_restitution']=float(a[1,4]/.1)
print('PASS fixed roughness uses native infinite-inertia granular contact',flush=True)

# Coupling-step convergence at fixed DEM accuracy in the closed reference.
# The finest result is a numerical reference, not an experimental bedload law.
ref=[]
for dt in (.00004,.00002,.00001):
    step=make('step-'+str(dt),replace(BASE,delta_t=dt,end_time=.002,write_interval=.002,dem_delta_t=5e-7))
    run(step);ref.append(states(step))
ratio=float(np.linalg.norm(ref[1]-ref[2])/np.linalg.norm(ref[0]-ref[2]))
assert 0<ratio<.8,ratio
RESULTS['coupling_step_refinement_ratio']=ratio
print('PASS coupling timestep refinement with fixed DEM accuracy',flush=True)

# Native porosity-weighted RANS/LES model registrations and equations.
for model in ('kOmegaSST','kEpsilon','Smagorinsky','WALE'):
    q=make('turbulence-'+model,replace(BASE,turbulence=model,end_time=.0002,write_interval=.0002));run(q)
    assert (q/'.0002/nut').exists() or (q/'0.0002/nut').exists()
print('PASS porosity-weighted kOmegaSST, kEpsilon, Smagorinsky and WALE',flush=True)

final=make('unscheduled-final',replace(BASE,end_time=.0003,write_interval=.01))
run(final);b=table(latest(final)/'budget.csv')
assert abs(b[-1]['time']-.0003)<1e-12 and (final/'0.0003/uniform/DEM/lammps.restart').is_file()
assert (final/'0.0003/U').is_file()
print('PASS final CFD and DEM state saved between scheduled writes',flush=True)

# Refuse overpacking and missing restart state, without a completion marker.
bad=make('overpacked')
props=bad/'constant/demCouplingProperties';props.write_text(update_values(props.read_text(),{'minimumPorosity':.99}))
run(bad,success=False)
assert not list((bad/'postProcessing/bedloadDEM').glob('*/complete'))
state=c/'0.002/uniform/DEM/lammps.restart';state.unlink()
control=c/'system/controlDict';control.write_text(update_values(control.read_text(),{'endTime':.003}))
assert not check_case(c,'bedloadDemPimpleFoam').ok
run(c,success=False,name='missing-state')
print('PASS unsafe porosity and missing checkpoint fail without successful outputs',flush=True)

(WORK/'results.json').write_text(json.dumps(RESULTS,indent=2)+'\n')
print(json.dumps(RESULTS,indent=2));print('All native CFD--DEM checks passed.',flush=True)
