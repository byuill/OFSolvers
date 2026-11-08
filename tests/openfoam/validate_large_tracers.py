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
from large_tracer import TracerConfig, Distribution, ParticleBoundary, save_tracers, foam_vector
from scour_tracer_demo import header, field
from foam_io import value


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


def run(case,cfg,positions=((.3,.5,.3),),extra='',success=True,legacy_contact_settings=False):
    save_tracers(case,cfg)
    props=case/'constant/largeTracerProperties'
    if legacy_contact_settings:
        props.write_text('\n'.join(line for line in props.read_text().splitlines()
            if not line.startswith(('particleCollisionMode ','collisionDiameterThreshold ')))+'\n')
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

# Sediment-contact cutoff: BOTH diameters must be strictly above the cutoff.
# All/disabled modes and old dictionaries retain their original behavior.
c=box('contact-cutoff')
pair_cfg=config(count=2,diameter=Distribution('constant',low=.1),
    density=Distribution('constant',low=1000),rho_water=1e-6,mu_water=1e-12,
    added_mass=0,restitution=1,friction=0,end_time=.6)
positions=((.4,.5,.5),(.6,.5,.5))
pair_extra='g (0 0 0); initialVelocities ((.2 0 0) (-.2 0 0));\n'
cutoff_results={}
for name,mode,threshold,enabled,bounce,legacy in [
    ('above','largeOnly',.099999999,True,True,False),
    ('equal','largeOnly',.1,True,False,False),
    ('below','largeOnly',.101,True,False,False),
    ('all','all',.101,True,True,False),
    ('off','largeOnly',.099,False,False,False),
    ('legacy-on','all',.1,True,True,True),
    ('legacy-off','all',.1,False,False,True)]:
    cfg=replace(pair_cfg,run_name=name,collision_mode=mode,
        collision_diameter_threshold=threshold,collisions=enabled)
    a=run(c,cfg,positions,pair_extra,legacy_contact_settings=legacy)
    last=a[-2:]
    np.testing.assert_allclose(last[:,5],[-.2,.2] if bounce else [.2,-.2],atol=1e-8,rtol=0)
    if bounce:
        assert np.min(a[1::2,2]-a[::2,2])>=.1-1e-10
    else:
        assert last[0,2]>last[1,2], 'Excluded pairs must have neither impulse nor overlap correction'
    with (c/'postProcessing/largeTracers'/name/'summary.csv').open() as stream:
        flags=[int(r['pair_contact_eligible']) for r in csv.DictReader(stream)]
    assert flags==[int(bounce)]*2
    if not legacy:
        snapshot=(c/'postProcessing/largeTracers'/name/'largeTracerProperties').read_text()
        assert float(value(snapshot,'collisionDiameterThreshold'))==threshold
    cutoff_results[name]={'eligible':sum(flags),'bounced':bounce}
RESULTS['diameter_cutoff_modes']=cutoff_results
print('PASS strict diameter cutoff, all/off modes and legacy dictionaries',flush=True)

# A large-small pair is excluded in threshold mode; in all mode its elastic
# collision follows the independently calculated unequal-mass collision law.
c=box('mixed-size-contact')
mixed=replace(pair_cfg,seed=2,diameter=Distribution('discrete',values=(.04,.1),weights=(1,1)),
    collision_mode='largeOnly',collision_diameter_threshold=.06,run_name='large-only')
a=run(c,mixed,positions,pair_extra)
with (c/'postProcessing/largeTracers/large-only/summary.csv').open() as stream:
    initial=list(csv.DictReader(stream))
diameters=np.array([float(r['diameter']) for r in initial])
assert set(diameters)=={.04,.1},'This seed must exercise a genuinely mixed-size pair'
assert [int(r['pair_contact_eligible']) for r in initial]==[int(d>.06) for d in diameters]
np.testing.assert_allclose(a[-2:,5],[.2,-.2],atol=1e-8,rtol=0)
assert a[-2,2]>a[-1,2]
b=run(c,replace(mixed,run_name='all',collision_mode='all'),positions,pair_extra)
mass=1000*np.pi*diameters**3/6
expected=np.array([((mass[0]-mass[1])*.2-2*mass[1]*.2)/sum(mass),
                   (2*mass[0]*.2-(mass[1]-mass[0])*.2)/sum(mass)])
np.testing.assert_allclose(b[-2:,5],expected,atol=1e-8,rtol=0)
RESULTS['mixed_size_pair']={'diameters_m':diameters.tolist(),
    'large_only_final_vx':a[-2:,5].tolist(),'all_final_vx':b[-2:,5].tolist()}
print('PASS large-small pass-through and unequal-mass all-mode collision',flush=True)

# Coarse-sediment impact: normal restitution dissipates kinetic energy while
# conserving the isolated pair's linear momentum.
c=box('inelastic-contact')
cfg=replace(pair_cfg,run_name='inelastic',collision_mode='largeOnly',
    collision_diameter_threshold=.05,restitution=.3)
a=run(c,cfg,positions,pair_extra)
ratio=float(np.sum(a[-2:,5:8]**2)/(2*.2**2))
assert abs(ratio-.3**2)<1e-8 and np.linalg.norm(a[-2:,5:8].sum(axis=0))<1e-12
RESULTS['inelastic_pair_energy_ratio']=ratio
print('PASS sediment normal restitution and energy dissipation',flush=True)

# An oblique impact independently checks the Coulomb impulse bound, momentum,
# normal restitution and spin generated by tangential grain contact.
c=box('frictional-contact')
oblique='g (0 0 0); initialVelocities ((.2 .05 0) (-.2 -.05 0));\n'
RESULTS['frictional_pair']={}
for regime,mu in [('sticking',.4),('sliding',.05)]:
    cfg=replace(pair_cfg,run_name=regime,collision_mode='largeOnly',
        collision_diameter_threshold=.05,restitution=.3,friction=mu,
        max_delta_t=.0005,output_interval=.0005,end_time=.5)
    run(c,cfg,positions,oblique)
    with (c/'postProcessing/largeTracers'/regime/'tracks.csv').open() as stream:
        rows=[{k:float(v) for k,v in row.items()} for row in csv.DictReader(stream)]
    state=np.array([[r[k] for k in ('x','y','z','vx','vy','vz','omega_x','omega_y','omega_z')]
        for r in rows]).reshape(-1,2,9)
    impact=next(i for i,s in enumerate(state) if np.linalg.norm(s[:,6:])>1e-6)
    before,after=state[impact-1],state[impact]
    n=after[0,:3]-after[1,:3]; n/=np.linalg.norm(n)
    m=1000*np.pi*.1**3/6
    j=m*(after[0,3:6]-before[0,3:6]); jn=float(j@n)
    jt=float(np.linalg.norm(j-jn*n))
    assert jn>0 and jt>0 and jt<=cfg.friction*jn+1e-10
    normal_ratio=-float((after[0,3:6]-after[1,3:6])@n)/float((before[0,3:6]-before[1,3:6])@n)
    assert abs(normal_ratio-cfg.restitution)<1e-8
    inertia=.4*m*.05**2
    np.testing.assert_allclose(after[0,6:],np.cross(-.05*n,j)/inertia,atol=1e-8,rtol=0)
    contact_slip=(after[0,3:6]-after[1,3:6]
        +np.cross(after[0,6:],-.05*n)-np.cross(after[1,6:],.05*n))
    tangent_speed=np.linalg.norm(contact_slip-(contact_slip@n)*n)
    if regime=='sliding':
        assert abs(jt/jn-mu)<1e-8 and tangent_speed>1e-4
    else:
        assert tangent_speed<1e-8
    assert np.linalg.norm(state[-1,:,3:6].sum(axis=0))<1e-12
    energy=float((m*np.sum(state[-1,:,3:6]**2)+inertia*np.sum(state[-1,:,6:]**2))/2)
    initial_energy=m*(.2**2+.05**2)
    assert energy<initial_energy
    RESULTS['frictional_pair'][regime]={'normal_restitution':normal_ratio,
        'tangential_to_normal_impulse_ratio':jt/jn,'kinetic_energy_ratio':energy/initial_energy}
print('PASS sediment sliding friction, normal restitution, spin and energy loss',flush=True)

# The pair cutoff must not switch off fluid forcing or sediment/bed contact.
c=box('small-wall-contact',walls=True)
cfg=config(diameter=Distribution('constant',low=.1),density=Distribution('constant',low=1200),
    collision_mode='largeOnly',collision_diameter_threshold=.2,end_time=.5)
a=run(c,cfg,positions=((.5,.5,.07),))
assert a[:,4].min()>=.05-1e-10 and abs(a[-1,4]-.05)<1e-6
print('PASS below-threshold settling and finite-radius bed contact',flush=True)

# Native validation also applies to hand-edited dictionaries, before output.
c=box('invalid-contact-settings')
for name,extra in [('zero','collisionDiameterThreshold 0;'),
                   ('negative','collisionDiameterThreshold -0.1;'),
                   ('mode','particleCollisionMode invalid;')]:
    folder=run(c,config(run_name=name),extra=extra,success=False)
    assert not folder.exists()
print('PASS invalid native cutoff settings rejected before output',flush=True)

# Per-patch particle boundaries are independent of carrier patch type and of
# pair-contact coefficients. An open fluid patch can retain solid particles.
c=box('particle-boundaries',walls=True)
cfg=config(diameter=Distribution('constant',low=.1),density=Distribution('constant',low=1000),
    rho_water=1e-6,mu_water=1e-12,added_mass=0,gravity=(0,0,0),end_time=1,
    boundaries={'downstream':ParticleBoundary('contact',.5,0)},run_name='screen')
a=run(c,cfg,positions=((.85,.5,.5),),extra='previewOnly true; initialVelocities ((.2 0 0));')
assert a[:,2].max()<=.95+1e-10 and a[-1,9]==1 and abs(a[-1,5]+.1)<1e-8
assert value((c/'postProcessing/largeTracers/screen/largeTracerProperties').read_text(),'previewOnly')=='false'
cfg=replace(cfg,run_name='absorbing-bed',end_time=.6,boundaries={'bed':ParticleBoundary('escape')})
a=run(c,cfg,positions=((.5,.5,.08),),extra='initialVelocities ((0 0 -.2));')
assert a[-1,9]==0 and a[-1,4]<0
cfg=replace(cfg,run_name='smooth-bed',end_time=.3,boundaries={'bed':ParticleBoundary('contact',0,0)})
a=run(c,cfg,positions=((.5,.5,.08),),extra='initialVelocities ((.05 0 -.2));')
assert abs(a[-1,4]-.05)<1e-9 and abs(a[-1,5]-.05)<1e-8
with (c/'postProcessing/largeTracers/smooth-bed/tracks.csv').open() as stream:
    last=list(csv.DictReader(stream))[-1]
assert abs(float(last['omega_y']))<1e-10
print('PASS per-patch contact, restitution, friction and escape independent of carrier BCs',flush=True)

# Authoritative initialization-only preview uses one initialized flow snapshot
# without requiring the future transient solution or advancing the particles.
c=box('initial-preview')
cfg=config(count=3,frozen_flow=False,end_time=5)
save_tracers(c,cfg)
before={p:p.read_bytes() for p in (c/'0').iterdir()}
command(c,['largeTracerFoam','-preview'],'log.preview')
folder=c/'postProcessing/largeTracers/tracers01-preview'
assert (folder/'complete').is_file()
with (folder/'tracks.csv').open() as stream: initial=list(csv.DictReader(stream))
assert len(initial)==3 and {float(r['time']) for r in initial}=={0}
assert all(float(r['vx'])==float(r['vy'])==float(r['vz'])==0 for r in initial)
assert value((folder/'largeTracerProperties').read_text(),'previewOnly')=='true'
assert all(p.read_bytes()==data for p,data in before.items())
command(c,['largeTracerFoam','-preview'],'log.preview-repeat')
assert (c/'postProcessing/largeTracers/tracers01-preview-1/tracks.csv').read_text()==(folder/'tracks.csv').read_text()
command(c,['largeTracerFoam'],'log.no-future-fields',success=False)
assert not (c/'postProcessing/largeTracers/tracers01').exists()
print('PASS initialization-only preview, unique outputs and unchanged initial carrier',flush=True)

# Between saved times preview interpolates U, rather than freezing the nearest
# field; the requested future interval may extend beyond current saved fields.
c=box('interpolated-preview')
(c/'1').mkdir()
(c/'1/U').write_text((c/'0/U').read_text().replace('uniform (0 0 0)','uniform (0.2 0 0)'))
cfg=config(initial_velocity='flow',start_time=.5,end_time=5,frozen_flow=False)
save_tracers(c,cfg)
command(c,['largeTracerFoam','-preview'],'log.preview')
with (c/'postProcessing/largeTracers/tracers01-preview/tracks.csv').open() as stream:
    preview=list(csv.DictReader(stream))
assert len(preview)==1 and abs(float(preview[0]['vx'])-.1)<1e-10
print('PASS preview at interpolated transient start time',flush=True)

c=box('invalid-particle-boundaries')
for name,rule in [('unknown','missing { type contact; }'),('type','bed { type invalid; }'),
                  ('restitution','bed { type contact; restitution 2; }'),
                  ('escape-coefficients','bed { type escape; friction 0.1; }')]:
    folder=run(c,config(run_name=name),extra='particleBoundaries { '+rule+' }',success=False)
    assert not folder.exists()
print('PASS invalid native particle boundary rules rejected before output',flush=True)

# Weak drag must tend continuously to a ballistic trajectory, without
# subtracting enormous equilibrium-velocity displacement terms.
c=box('ballistic-limit')
cfg=config(rho_water=1e-20,mu_water=1e-20,added_mass=0,
    end_time=.1,output_interval=.1)
a=run(c,cfg)
expected=.3-.5*9.81*.1**2
assert abs(a[-1,4]-expected)<1e-12 and abs(a[-1,7]+.981)<1e-12
RESULTS['ballistic_position_error_m']=float(abs(a[-1,4]-expected))
print('PASS weak-drag ballistic limit without cancellation',flush=True)

# Full replay uses only its bracketing fields, even at the final output time;
# unrelated earlier/later U snapshots need not have this replay's alpha field.
c=box('interval-only-phase',u=(.2,0,0))
for time in ('1','1.5','2','3'):
    folder=c/time; folder.mkdir(); shutil.copyfile(c/'0/U',folder/'U')
    if time!='3':
        (folder/'alpha.water').write_text(field('alpha.water','[0 0 0 0 0 0 0]','1',
            {p:'type zeroGradient;' for p in ('upstream','downstream','bed','lid','sides')}))
cfg=config(start_time=1,end_time=2,output_interval=.5,frozen_flow=False,
    initial_velocity='flow',density=Distribution('constant',low=1000),alpha_field='alpha.water')
a=run(c,cfg)
assert abs(a[-1,2]-.5)<1e-10
text=(c/'log.tracers01').read_text()
assert 'Carrier bracket: 1 .. 1.5' in text and 'Carrier bracket: 1.5 .. 2' in text
print('PASS replay brackets and phase coverage limited to tracking interval',flush=True)

# An event-time remainder smaller than minDeltaT is safe when the physical
# timestep limits still permit it. Previously this failed at a carrier frame.
c=box('short-frame-remainder',u=(.2,0,0))
for time in ('0.0100000001','0.02','0.03'):
    (c/time).mkdir(); shutil.copyfile(c/'0/U',c/time/'U')
a=run(c,config(end_time=.03,output_interval=.03,max_delta_t=.005,frozen_flow=False,
    initial_velocity='flow',density=Distribution('constant',low=1000)))
assert abs(a[-1,2]-.306)<1e-10
print('PASS short carrier snapshot remainder without false stiffness failure',flush=True)

c=box('duplicate-numeric-times')
shutil.copytree(c/'0',c/'0.0')
folder=run(c,config(),success=False)
assert not folder.exists() and 'unique increasing numeric values' in (c/'log.tracers01').read_text()
print('PASS aliased carrier times rejected before output',flush=True)

(WORK/'results.json').write_text(json.dumps(RESULTS,indent=2)+'\n')
print(json.dumps(RESULTS,indent=2))
print('All native tracer checks passed. Artifacts:',WORK,flush=True)
