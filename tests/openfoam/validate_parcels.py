#!/usr/bin/env python3
"""Execute sandParcelPimpleFoam integration checks in fresh temporary cases.
Activate scripts/openfoam-env.sh first; logs and cloud fields are retained.
"""
from pathlib import Path
import math
import re
import shutil
import statistics
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]
WORK=Path(tempfile.mkdtemp(prefix='sand-parcel-validation-'))
SAMPLES=re.compile(r'Sand sample: id=(\d+) position=\(([^)]+)\)')

def edit(case,name,old,new):
    p=case/name
    text=p.read_text()
    assert old in text,(p,old)
    p.write_text(text.replace(old,new))

def case(name,profile='uniform',rate=10000,end='0.2'):
    dst=WORK/name
    shutil.copytree(ROOT/'tutorials/sandParcelChannel',dst)
    edit(dst,'system/controlDict','endTime 1;',f'endTime {end};')
    edit(dst,'system/controlDict','writeInterval 0.5;','writeInterval 0.1;')
    edit(dst,'constant/sandCloudProperties','duration 1;','duration 0.2;')
    edit(dst,'constant/sandCloudProperties','parcelsPerSecond 1000;',f'parcelsPerSecond {rate};')
    edit(dst,'constant/sandCloudProperties','profile Rouse;',f'profile {profile};')
    edit(dst,'constant/sandCloudProperties','writeSamples false;','writeSamples true;')
    edit(dst,'constant/sandCloudProperties','shearVelocityModel localBed;',
         'shearVelocityModel constant; uStar 0.02;')
    return dst

def command(dst,cmd,log,expected=0):
    with (dst/log).open('w') as stream:
        p=subprocess.run(cmd,cwd=dst,stdout=stream,stderr=subprocess.STDOUT)
    text=(dst/log).read_text()
    assert p.returncode==expected,(cmd,p.returncode,dst/log,text[-3000:])
    return text

def run(dst,log='log.solver',mesh=True):
    if mesh: command(dst,['blockMesh'],'log.blockMesh')
    text=command(dst,['sandParcelPimpleFoam'],log)
    assert text.rstrip().endswith('End')
    count=int(re.findall(r'parcels added\s*=\s*(\d+)',text)[-1])
    mass=float(re.findall(r'mass introduced\s*=\s*(\S+)',text)[-1])
    mobile,deposited=map(float,re.findall(r'Sand inventory: mobile=(\S+) deposited=(\S+) kg',text)[-1])
    assert math.isclose(mass,mobile+deposited,rel_tol=1e-9,abs_tol=1e-13)
    samples={int(i):tuple(map(float,v.split())) for i,v in SAMPLES.findall(text)}
    for x,y,z in samples.values():
        assert abs(x-1e-7)<1e-10 and 0.03<=y<=0.07 and 0.005<=z<=0.095
    return text,count,mass,samples

def field(path):
    text=path.read_text()
    m=re.search(r'\n(\d+)\n\(\n(.*?)\n\)',text,re.S)
    if m:
        vals=[float(x) for x in m[2].split()]
        assert len(vals)==int(m[1]),path
        return vals
    m=re.search(r'\n(\d+)\s*\{\s*(\S+)\s*\}',text)
    return [float(m[2])]*int(m[1])

def positions(dst,time):
    folder=dst/str(time)/'lagrangian/sandCloud'
    text=(folder/'positions').read_text()
    vectors=[tuple(map(float,v.split())) for v in re.findall(r'^\(([^()\n]+)\)\s+\d+',text,re.M)]
    ids=list(zip(field(folder/'origProcId'),field(folder/'origId')))
    assert len(vectors)==len(ids)
    return dict(zip(ids,vectors))

print('Validation artifacts:',WORK,flush=True)
uniform=case('uniform')
text,count,mass,uniformSamples=run(uniform)
assert count==2000 and len(uniformSamples)==count
assert math.isclose(mass,2e-5,rel_tol=1e-10)
z=[p[2] for p in uniformSamples.values()]
y=[p[1] for p in uniformSamples.values()]
assert abs(statistics.mean(z)-0.05)<0.002
assert abs(statistics.mean(y)-0.05)<0.001
assert abs(sum(v<0.05 for v in z)/len(z)-0.5)<0.04
print('PASS: feed rate, exact strip width, random uniform heights and width',flush=True)

# Independent kinematic check catches v1912's native double advancement of
# freshly injected parcels. Slip walls keep the carrier exactly uniform.
kinematic=case('analyticalMotion')
edit(kinematic,'0/U','bed { type noSlip; }','bed { type slip; }')
text,count,mass,samples=run(kinematic)
folder=kinematic/'0.2/lagrangian/sandCloud'
ages=field(folder/'age'); active=field(folder/'active')
ids=field(folder/'origId'); pos=positions(kinematic,'0.2')
ws=float(re.findall(r'ws=(\S+)',text)[-1])
checked=0
for id,age,isactive in zip(ids,ages,active):
    id=int(id)
    if isactive:
        birth=(id//100)*0.01+(id%100)*0.0001
        assert abs(age-(0.2-birth))<1e-9, (id,age,birth)
        point=pos[(0,id)]
        initial=samples[id]
        assert abs(point[0]-(initial[0]+0.5*age))<1e-8
        assert abs(point[2]-(initial[2]-ws*age))<1e-8
        checked+=1
assert checked>1000
print('PASS: parcel ages, advection and gravity/drag settling match exact uniform-flow trajectories',flush=True)

rouse=case('Rouse',profile='Rouse')
text,count,mass,samples=run(rouse)
P=float(re.findall(r'RouseP=(\S+)',text)[-1])
ws=float(re.findall(r'ws=(\S+)',text)[-1])
assert math.isclose(P,ws/(0.41*0.02),rel_tol=1e-10)
assert 0.008<ws<0.015
# Independent midpoint quadrature of the continuous Rouse profile. Carrier
# inlet is uniform here, so flux weighting adds only a constant factor.
H,a,b=0.1,0.005,0.095
N=20000
points=[a+(i+0.5)*(b-a)/N for i in range(N)]
weights=[(a*(H-v)/(v*(H-a)))**P for v in points]
expectedMean=sum(v*w for v,w in zip(points,weights))/sum(weights)
expectedCDF=sum(w for v,w in zip(points,weights) if v<0.025)/sum(weights)
z=[p[2] for p in samples.values()]
assert abs(statistics.mean(z)-expectedMean)<0.0015
assert abs(sum(v<0.025 for v in z)/len(z)-expectedCDF)<0.04
assert statistics.mean(z)<0.03
print('PASS: Rouse exponent and sampled CDF agree with independent integration',flush=True)

local=case('localShear',profile='Rouse',rate=1000)
edit(local,'constant/sandCloudProperties','shearVelocityModel constant; uStar 0.02;',
     'shearVelocityModel localBed;')
text,count,mass,samples=run(local)
uStars=[float(v) for v in re.findall(r'uStar=(\S+)',text)]
assert uStars and all(math.isfinite(v) and v>0 for v in uStars)
assert not math.isclose(uStars[-1],0.02,rel_tol=0.01)
print('PASS: local bed-stress shear estimate updates during PIMPLE flow',flush=True)

low=case('lowParcelRate',rate=0.5)
text,count,mass,samples=run(low)
assert count==1 and math.isclose(mass,2e-5,rel_tol=1e-10)
print('PASS: low-rate fractional final batch retains all requested feed mass',flush=True)

fractional=case('fractionalSchedule',rate=37.5)
edit(fractional,'constant/sandCloudProperties','SOI 0;','SOI 0.035;')
edit(fractional,'constant/sandCloudProperties','duration 0.2;','duration 0.123;')
text,count,mass,samples=run(fractional)
assert math.isclose(mass,1.23e-5,rel_tol=1e-10)
print('PASS: delayed start and duration not aligned to time steps',flush=True)

restart=case('restart',end='0.1')
_,_,_,first=run(restart)
edit(restart,'system/controlDict','startFrom startTime;','startFrom latestTime;')
edit(restart,'system/controlDict','endTime 0.1;','endTime 0.2;')
text,count,mass,second=run(restart,log='log.restart',mesh=False)
assert count==2000 and math.isclose(mass,2e-5,rel_tol=1e-10)
assert not (set(first)&set(second))
assert first|second==uniformSamples
continuous=positions(uniform,'0.2'); restarted=positions(restart,'0.2')
assert continuous.keys()==restarted.keys()
assert max(abs(a-b) for key in continuous for a,b in zip(continuous[key],restarted[key]))<1e-8
print('PASS: restart preserves feed mass, sample sequence and particle trajectories',flush=True)

parallel=case('parallel',rate=1000,end='3')
(parallel/'system/decomposeParDict').write_text(
    'FoamFile { version 2.0; format ascii; class dictionary; object decomposeParDict; }\n'
    'numberOfSubdomains 2; method simple; simpleCoeffs { n (2 1 1); delta 0.001; }\n')
command(parallel,['blockMesh'],'log.blockMesh')
command(parallel,['decomposePar'],'log.decomposePar')
text=command(parallel,['mpirun','-np','2','sandParcelPimpleFoam','-parallel'],'log.parallel')
assert int(re.findall(r'parcels added\s*=\s*(\d+)',text)[-1])==200
mass=float(re.findall(r'mass introduced\s*=\s*(\S+)',text)[-1])
assert math.isclose(mass,2e-5,rel_tol=1e-10)
mobile,deposited=map(float,re.findall(r'Sand inventory: mobile=(\S+) deposited=(\S+) kg',text)[-1])
assert mobile+deposited < mass
escaped=float(re.findall(r'Parcel fate: system \(number, mass\)\s+- escape\s*=\s*\d+, (\S+)',text)[-1])
assert math.isclose(mobile+deposited+escaped,mass,rel_tol=1e-9)
command(parallel,['reconstructPar','-latestTime'],'log.reconstructPar')
assert len(positions(parallel,'3')) < 200
print('PASS: two-rank feed, processor migration, outlet escape and cloud reconstruction',flush=True)

# Split the inlet itself to exercise globally weighted face selection.
split=case('splitInlet',rate=1000)
(split/'system/decomposeParDict').write_text(
    'FoamFile { version 2.0; format ascii; class dictionary; object decomposeParDict; }\n'
    'numberOfSubdomains 2; method simple; simpleCoeffs { n (1 2 1); delta 0.001; }\n')
command(split,['blockMesh'],'log.blockMesh')
command(split,['decomposePar'],'log.decomposePar')
text=command(split,['mpirun','-np','2','sandParcelPimpleFoam','-parallel'],'log.parallel')
assert int(re.findall(r'parcels added\s*=\s*(\d+)',text)[-1])==200
mass=float(re.findall(r'mass introduced\s*=\s*(\S+)',text)[-1])
assert math.isclose(mass,2e-5,rel_tol=1e-10)
for rank in range(2):
    assert (split/f'processor{rank}'/'0.2/lagrangian/sandCloud/positions').exists()
print('PASS: inlet split across two ranks respects the global feed rate',flush=True)

ras=case('RANSDispersion',profile='Rouse',rate=1000)
(ras/'constant/turbulenceProperties').write_text(
    'FoamFile { version 2.0; format ascii; class dictionary; object turbulenceProperties; }\n'
    'simulationType RAS; RAS { RASModel kEpsilon; turbulence on; printCoeffs on; }\n')
edit(ras,'constant/sandCloudProperties','dispersionModel none;','dispersionModel stochasticDispersionRAS;')
edit(ras,'constant/sandCloudProperties','shearVelocityModel constant; uStar 0.02;','shearVelocityModel localBed;')
for name,dims,value,wall in [('k','[0 2 -2 0 0 0 0]','0.0001','kqRWallFunction'),
                           ('epsilon','[0 2 -3 0 0 0 0]','0.0001','epsilonWallFunction'),
                           ('nut','[0 2 -1 0 0 0 0]','0','nutkWallFunction')]:
    (ras/'0'/name).write_text(f'''FoamFile {{ version 2.0; format ascii; class volScalarField; object {name}; }}
dimensions {dims}; internalField uniform {value};
boundaryField
{{
 upstream {{ type fixedValue; value uniform {value}; }}
 downstream {{ type zeroGradient; }}
 bed {{ type {wall}; value uniform {value}; }}
 lid {{ type zeroGradient; }}
 sides {{ type {wall}; value uniform {value}; }}
}}
''')
text,_,_,_=run(ras)
assert 'kEpsilon' in text and 'stochasticDispersionRAS' in text
print('PASS: RANS local shear and native stochastic particle dispersion',flush=True)

deposit=case('deposition',profile='Rouse',rate=1000,end='1')
text,_,mass,_=run(deposit)
mobile,deposited=map(float,re.findall(r'Sand inventory: mobile=(\S+) deposited=(\S+) kg',text)[-1])
assert deposited>0 and math.isclose(mobile+deposited,mass,rel_tol=1e-9)
print('PASS: native stick bed retains deposited sand mass',flush=True)

invalid=case('invalidWidth')
edit(invalid,'constant/sandCloudProperties','width 0.04;','width 0.2;')
command(invalid,['blockMesh'],'log.blockMesh')
text=command(invalid,['sandParcelPimpleFoam'],'log.solver',expected=1)
assert 'entire rectangular feed transect' in text
print('PASS: feed rectangle extending off the inlet is rejected',flush=True)
missing=case('missingRestartState',end='0.1')
run(missing)
(missing/'0.1/uniform/lagrangian/sandCloud/sandCloudOutputProperties').unlink()
edit(missing,'system/controlDict','startFrom startTime;','startFrom latestTime;')
edit(missing,'system/controlDict','endTime 0.1;','endTime 0.2;')
text=command(missing,['sandParcelPimpleFoam'],'log.restart',expected=1)
assert 'Restart requires sandCloudOutputProperties' in text
print('PASS: missing injector restart state is rejected',flush=True)
print('All parcel integration checks passed.',flush=True)
