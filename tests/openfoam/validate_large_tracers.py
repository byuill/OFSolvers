#!/usr/bin/env python3
"""Native largeTracerFoam physics checks. Activate OpenFOAM v1912 first.

All flow fields below are manufactured, independent reference problems; this
does not substitute for validating the carrier flow of a specific scour hole.
"""
import csv
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from large_tracer import TracerConfig, Distribution, save_tracers, foam_vector
from scour_tracer_demo import header, field


WORK = Path(tempfile.mkdtemp(prefix='large-tracer-validation-'))
print('Validation artifacts:',WORK,flush=True)
RESULTS = {}


def command(case, args, log, success=True):
    with (case/log).open('w') as stream:
        result = subprocess.run(args,cwd=case,stdout=stream,stderr=subprocess.STDOUT)
    text = (case/log).read_text()
    assert (result.returncode==0)==success,(args,case/log,text[-4000:])
    return text


def box(name, u=(0,0,0), alpha=False, walls=False, slope=False):
    case = WORK/name
    for d in ('system','constant','0'): (case/d).mkdir(parents=True)
    mesh = (ROOT/'tutorials/sandParcelChannel/system/blockMeshDict').read_text()
    mesh = mesh.replace('(1 0.1 0)','(1 1 0)').replace('(0 0.1 0)','(0 1 0)')
    mesh = mesh.replace('(0 0 0.1)','(0 0 1)').replace('(1 0 0.1)','(1 0 1)')
    mesh = mesh.replace('(1 0.1 0.1)','(1 1 1)').replace('(0 0.1 0.1)','(0 1 1)')
    mesh = mesh.replace('(20 4 10)','(4 4 100)' if alpha else '(4 4 4)')
    if slope: mesh=mesh.replace('(1 0 0)','(1 0 .2)').replace('(1 1 0)','(1 1 .2)')
    if not walls: mesh = mesh.replace('type wall','type patch')
    (case/'system/blockMeshDict').write_text(mesh)
    (case/'system/controlDict').write_text(header('controlDict')+'''
application largeTracerFoam; startFrom startTime; startTime 0; endTime 3;
deltaT .001; writeControl timeStep; writeInterval 1;
''')
    (case/'system/fvSchemes').write_text(header('fvSchemes')+'''
ddtSchemes {default Euler;} gradSchemes {default Gauss linear;}
divSchemes {default none;} laplacianSchemes {default Gauss linear corrected;}
interpolationSchemes {default linear;} snGradSchemes {default corrected;}
''')
    (case/'system/fvSolution').write_text(header('fvSolution')+'solvers {}\n')
    patches = ('upstream','downstream','bed','lid','sides')
    (case/'0/U').write_text(field('U','[0 1 -1 0 0 0 0]',foam_vector(u),
        {p:'type zeroGradient;' for p in patches},True))
    if alpha:
        (case/'0/alpha.water').write_text(field('alpha.water','[0 0 0 0 0 0 0]','0',
            {p:'type zeroGradient;' for p in patches}))
        (case/'system/setFieldsDict').write_text(header('setFieldsDict')+'''
defaultFieldValues (volScalarFieldValue alpha.water 0);
regions (boxToCell {box (-1 -1 -1) (2 2 .5); fieldValues (volScalarFieldValue alpha.water 1);});
''')
    command(case,['blockMesh'],'log.mesh')
    if alpha: command(case,['setFields'],'log.setFields')
    return case


def config(**kw):
    base = TracerConfig(count=1,diameter=Distribution('constant',low=.03),
        density=Distribution('constant',low=700),
        seed_min=(.01,.01,.01),seed_max=(.99,.99,.99),
        retention_min=(0,0,0),retention_max=(1,1,1),rim_elevation=.6,
        end_time=.3,output_interval=.01,alpha_field='none',frozen_flow=True)
    return replace(base,**kw)


def run(case,cfg,positions=((.3,.5,.3),),extra='',success=True):
    save_tracers(case,cfg)
    props=case/'constant/largeTracerProperties'
    with props.open('a') as stream:
        stream.write('\ninitialPositions ('+' '.join(foam_vector(p) for p in positions)+');\n'+extra)
    before={str(p.relative_to(case)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in case.rglob('*') if p.is_file() and p.parts[-2] in ('0','1')}
    command(case,['largeTracerFoam'],'log.'+cfg.run_name,success)
    after={str(p.relative_to(case)):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in case.rglob('*') if p.is_file() and p.parts[-2] in ('0','1')}
    assert before==after,'Carrier fields changed!'
    folder=case/'postProcessing/largeTracers'/cfg.run_name
    if not success: return folder
    assert (folder/'complete').exists()
    with (folder/'tracks.csv').open() as stream:
        rows=[{k:float(v) for k,v in r.items()} for r in csv.DictReader(stream)]
    return np.array([[r[k] for k in ('time','id','x','y','z','vx','vy','vz','water_fraction','active')] for r in rows])


# Exact neutral advection in a uniform carrier; one ID is one object.
c=box('neutral',u=(.2,0,0))
cfg=config(density=Distribution('constant',low=1000),initial_velocity='flow')
a=run(c,cfg)
assert abs(a[-1,2]-.36)<1e-10 and abs(a[-1,5]-.2)<1e-10
RESULTS['neutral_advection_position_error_m']=float(abs(a[-1,2]-.36))
print('PASS neutral advection and unchanged carrier fields',flush=True)

# Independent Stokes solution; low Re makes Schiller-Naumann correction negligible.
c=box('stokes',u=(.0001,0,0))
cfg=config(diameter=Distribution('constant',low=.01),mu_water=1,
    end_time=.03,output_interval=.03,max_delta_t=.0001)
a=run(c,cfg,extra='g (0 0 0);\n')
tau=(700+.5*1000)*.01**2/18
v=.0001*(1-math.exp(-.03/tau))
x=.3+.0001*(.03-tau*(1-math.exp(-.03/tau)))
assert abs(a[-1,5]-v)/v<.0001 and abs(a[-1,2]-x)<1e-8
RESULTS['stokes_velocity_relative_error']=float(abs(a[-1,5]-v)/v)
print('PASS analytical Stokes response including added mass',flush=True)

# Terminal rising speed from high-Re quadratic drag, away from any free surface.
c=box('rise')
cfg=config(end_time=.7,output_interval=.05,max_delta_t=.001)
a=run(c,cfg)
terminal=math.sqrt(4*.03*(1000-700)*9.81/(3*.44*1000))
assert abs(a[-1,7]-terminal)/terminal<.025
RESULTS['terminal_rise_relative_error']=float(abs(a[-1,7]-terminal)/terminal)
print('PASS buoyant terminal rise',flush=True)

# Neutrally buoyant sphere in spatially uniform accelerating carrier: D U / D t
# (including pressure-gradient contribution) is needed, not drag alone.
c=box('accelerating')
(c/'1').mkdir()
(c/'1/U').write_text((c/'0/U').read_text().replace('uniform (0 0 0)','uniform (0.2 0 0)'))
cfg=config(density=Distribution('constant',low=1000),frozen_flow=False,
    end_time=.5,max_delta_t=.0005)
a=run(c,cfg)
assert abs(a[-1,5]-.1)<.0002 and abs(a[-1,2]-.325)<.0001
RESULTS['accelerating_flow_velocity_error_m_s']=float(abs(a[-1,5]-.1))
print('PASS transient field interpolation and fluid acceleration',flush=True)
coarse=run(c,replace(cfg,run_name='coarse',max_delta_t=.002))
assert abs(a[-1,5]-.1)<.35*abs(coarse[-1,5]-.1)
RESULTS['time_step_refinement_error_ratio']=float(abs(a[-1,5]-.1)/abs(coarse[-1,5]-.1))
print('PASS timestep refinement in accelerating flow',flush=True)

# Floating equilibrium: displaced water/air mass equals physical particle mass.
c=box('float',alpha=True)
cfg=config(diameter=Distribution('constant',low=.1),density=Distribution('constant',low=500),
    alpha_field='alpha.water',end_time=2,volume_samples=512,max_delta_t=.001,
    output_interval=.02,min_water_fraction=.4)
a=run(c,cfg,positions=((.5,.5,.5),))
tail=a[a[:,0]>1.5]
expected=(500-1.2)/(1000-1.2)
assert abs(tail[:,8].mean()-expected)<.005 and abs(tail[:,4].mean()-.5)<.001
RESULTS['floating_water_fraction_mean']=float(tail[:,8].mean())
RESULTS['floating_equilibrium_water_fraction']=expected
print('PASS partial submergence and floating equilibrium',flush=True)

# Physical radius cannot sink into a wall; the center must stop one radius above.
c=box('contact',walls=True)
cfg=config(diameter=Distribution('constant',low=.1),density=Distribution('constant',low=1200),end_time=.5)
a=run(c,cfg,positions=((.5,.5,.07),))
assert a[:,4].min()>=.05-1e-10 and abs(a[-1,4]-.05)<1e-6
print('PASS finite-radius bed contact',flush=True)

# A sphere under horizontal drag can roll, rather than permanently sticking.
c=box('rolling',u=(.3,0,0),walls=True)
cfg=config(diameter=Distribution('constant',low=.1),density=Distribution('constant',low=1200),
    end_time=.2,max_delta_t=.001)
run(c,cfg,positions=((.5,.5,.05),))
with (c/'postProcessing/largeTracers/tracers01/tracks.csv').open() as stream:
    rolling=list(csv.DictReader(stream))[-1]
assert float(rolling['x'])>.5 and float(rolling['omega_y'])>0
assert abs(float(rolling['vx'])-.05*float(rolling['omega_y']))<.002
print('PASS rolling contact and rotational state',flush=True)

# Bed initialization follows the actual triangulated sloping wall.
c=box('bed-seeding',walls=True,slope=True)
cfg=config(count=10,placement='bed',seed_min=(.1,.1,-.1),seed_max=(.9,.9,.5),
    density=Distribution('constant',low=1000),end_time=.002)
save_tracers(c,cfg); command(c,['largeTracerFoam'],'log.bedSeed')
with (c/'postProcessing/largeTracers/tracers01/tracks.csv').open() as stream:
    initial=[r for r in csv.DictReader(stream) if float(r['time'])==0]
for r in initial:
    distance=(float(r['z'])-.2*float(r['x']))/math.sqrt(1.04)
    assert abs(distance-float(r['diameter'])/2)<1e-7
print('PASS nonoverlapping bed seeding on a sloping surface',flush=True)

# An isolated elastic pair conserves momentum/energy (carrier drag negligible).
c=box('pair')
cfg=config(count=2,diameter=Distribution('constant',low=.1),density=Distribution('constant',low=1000),
    rho_water=1e-6,mu_water=1e-12,added_mass=0,restitution=1,friction=0,end_time=.4)
a=run(c,cfg,positions=((.4,.5,.5),(.6,.5,.5)),
    extra='g (0 0 0); initialVelocities ((.2 0 0) (-.2 0 0));\n')
last=a[-2:]
assert abs(sum(last[:,5]))<1e-12 and abs(last[0,5]+.2)<1e-8 and abs(last[1,5]-.2)<1e-8
assert np.min(a[1::2,2]-a[::2,2])>=.1-1e-10
print('PASS sphere collision momentum and energy',flush=True)

# First exit and computational escape have different meanings.
c=box('escape',u=(.2,0,0))
cfg=config(density=Distribution('constant',low=1000),initial_velocity='flow',
    retention_min=(.7,.1,.1),retention_max=(.9,.9,.9),end_time=1)
a=run(c,cfg,positions=((.85,.5,.5),))
events=(c/'postProcessing/largeTracers/tracers01/events.csv').read_text()
assert 'left_region' in events and 'escaped' in events and a[-1,9]==0
print('PASS retained-region exit and open-boundary escape',flush=True)

# Reject an infeasible particle before creating any output population.
c=box('invalid')
cfg=config(diameter=Distribution('constant',low=2))
folder=run(c,cfg,success=False)
assert not folder.exists()
print('PASS impossible initial geometry fails atomically',flush=True)

c=box('overlap')
folder=run(c,config(count=2),positions=((.3,.5,.3),(.3,.5,.3)),success=False)
assert not folder.exists()
folder=run(c,config(frozen_flow=False,run_name='missing-flow'),success=False)
assert not folder.exists()
print('PASS overlapping initialization and transient extrapolation rejected',flush=True)

# Particle sizes/densities and packing are reproducible, output cannot overwrite.
c=box('population')
cfg=config(count=30,diameter=Distribution('uniform',low=.02,high=.08),
    density=Distribution('discrete',values=(400,800,1100),weights=(1,2,1)),end_time=.002)
save_tracers(c,cfg)
command(c,['largeTracerFoam'],'log.first')
first=(c/'postProcessing/largeTracers/tracers01/tracks.csv').read_text()
cfg=replace(cfg,run_name='repeat')
save_tracers(c,cfg); command(c,['largeTracerFoam'],'log.repeat')
assert (c/'postProcessing/largeTracers/repeat/tracks.csv').read_text()==first
command(c,['largeTracerFoam'],'log.overwrite',success=False)
print('PASS deterministic populations and output protection',flush=True)

# Exercise the allowed population limit; this is not 999 weighted parcels.
c=box('maximum')
cfg=config(count=999,diameter=Distribution('constant',low=.01),
    density=Distribution('constant',low=1000),end_time=.002)
save_tracers(c,cfg); command(c,['largeTracerFoam'],'log.maximum')
with (c/'postProcessing/largeTracers/tracers01/summary.csv').open() as stream:
    population=list(csv.DictReader(stream))
assert len(population)==999 and len({r['id'] for r in population})==999
print('PASS 999 independent physical particles',flush=True)

(WORK/'results.json').write_text(json.dumps(RESULTS,indent=2)+'\n')
print(json.dumps(RESULTS,indent=2))
print('All native tracer checks passed. Artifacts:',WORK,flush=True)
