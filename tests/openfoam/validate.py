#!/usr/bin/env python3
"""Integration checks: execute the built solver, never substitute a mock.
Requires activated OpenFOAM tools and sedimentPimpleFoam on PATH.
Cases/logs are retained in a fresh temporary directory outside the checkout.
"""
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(tempfile.mkdtemp(prefix='sediment-validation-'))
BUDGET = re.compile(r'Sediment budget: suspended=(\S+) bed=(\S+) externalOut=(\S+) residual=(\S+) kg; min\(C\)=(\S+)')

def edit(case, name, old, new):
    path = case/name
    text = path.read_text()
    assert old in text, (path, old)
    path.write_text(text.replace(old, new))

def case(name, end='0.2'):
    dst = WORK/name
    shutil.copytree(ROOT/'tutorials/sedimentChannel', dst)
    edit(dst, 'system/controlDict', 'endTime 1;', f'endTime {end};')
    edit(dst, 'system/controlDict', 'writeInterval 0.5;', f'writeInterval {end};')
    return dst

def command(dst, cmd, logfile, expected=0):
    with (dst/logfile).open('w') as log:
        result = subprocess.run(cmd, cwd=dst, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode != expected:
        raise AssertionError(f'{cmd} returned {result.returncode}; see {dst/logfile}\n'+(dst/logfile).read_text()[-3000:])
    return (dst/logfile).read_text()

def run(dst, log='log.solver', mesh=True):
    if mesh:
        command(dst, ['blockMesh'], 'log.blockMesh')
    text = command(dst, ['sedimentPimpleFoam'], log)
    assert text.rstrip().endswith('End'), dst
    rows = [[float(x) for x in row] for row in BUDGET.findall(text)]
    assert rows, 'zero-step run'
    for suspended, bed, external, residual, minc in rows:
        assert all(math.isfinite(x) for x in [suspended, bed, external, residual, minc])
        assert abs(residual) < 1e-10, (dst, residual)
        assert minc >= -1e-12 and bed >= 0, (dst, minc, bed)
    return rows, text

def values(path, entry='internalField'):
    text = path.read_text()
    if entry == 'bed':
        text = text.split('\n    bed\n')[1]
        entry = 'value'
    m = re.search(r'\b'+entry+r'\s+nonuniform List<scalar>\s+(\d+)\s*\((.*?)\)', text, re.S)
    if m:
        vals = [float(x) for x in m[2].split()]
        assert len(vals) == int(m[1])
        return vals
    m = re.search(r'\b'+entry+r'\s+uniform\s+([^;]+);', text)
    return [float(m[1])]

def zero_inflow(dst):
    edit(dst, '0/C', 'inletValue uniform 0.1;', 'inletValue uniform 0;')

def turbulence_fields(dst, model):
    if model == 'kEpsilon':
        props = 'simulationType RAS;\nRAS { RASModel kEpsilon; turbulence on; printCoeffs on; }'
        specs = [('k', '[0 2 -2 0 0 0 0]', '0.0001', 'kqRWallFunction'),
                 ('epsilon', '[0 2 -3 0 0 0 0]', '0.0001', 'epsilonWallFunction'),
                 ('nut', '[0 2 -1 0 0 0 0]', '0', 'nutkWallFunction')]
    else:
        props = f'simulationType LES;\nLES {{ LESModel {model}; turbulence on; printCoeffs on; delta cubeRootVol; }}'
        specs = [('nut', '[0 2 -1 0 0 0 0]', '0', 'nutUSpaldingWallFunction')]
        if model == 'SpalartAllmarasDES':
            specs += [('nuTilda', '[0 2 -1 0 0 0 0]', '1e-5', 'fixedValue')]
    (dst/'constant/turbulenceProperties').write_text('FoamFile { version 2.0; format ascii; class dictionary; object turbulenceProperties; }\n'+props+'\n')
    for name, dims, val, wall in specs:
        text = f'''FoamFile {{ version 2.0; format ascii; class volScalarField; object {name}; }}
dimensions {dims};
internalField uniform {val};
boundaryField
{{
 upstream {{ type fixedValue; value uniform {val}; }}
 downstream {{ type zeroGradient; }}
 bed {{ type {wall}; value uniform {0 if name=='nuTilda' else val}; }}
 lid {{ type zeroGradient; }}
 sides {{ type empty; }}
}}
'''
        (dst/'0'/name).write_text(text)

print('Validation artifacts:', WORK, flush=True)
channel = case('inflow', '1')
rows, text = run(channel)
assert rows[-1][0] > 0 and rows[-1][1] > 0.001
assert rows[-1][2] < 0
assert 0.008 < float(re.search(r'ws=(\S+) m/s', text)[1]) < 0.015
print('PASS: open inflow, settling, deposition and discrete mass budget', flush=True)

closed = case('deposition')
zero_inflow(closed)
edit(closed, '0/U', '(0.1 0 0)', '(0 0 0)')
edit(closed, '0/C', 'internalField uniform 0;', 'internalField uniform 1;')
# Close both ends for sediment, eliminating diffusive boundary losses too.
edit(closed, '0/C', 'type inletOutlet; phi phiSed; inletValue uniform 0; value uniform 0;', 'type zeroGradient;')
rows, _ = run(closed)
assert rows[-1][0] < 0.01 and rows[-1][1] > 0.001
assert abs(rows[-1][0]+rows[-1][1]-0.011) < 1e-10
print('PASS: closed-domain deposition conserves suspended+bed mass', flush=True)

erosion = case('depletion')
zero_inflow(erosion)
edit(erosion, 'constant/sedimentProperties', 'thetaCrit 0.05;', 'thetaCrit 1e-9;')
edit(erosion, 'constant/sedimentProperties', 'erosionRate 0;', 'erosionRate 1;')
edit(erosion, 'constant/sedimentProperties', 'settlingModel FergusonChurch;', 'settlingModel constant;\nws 0;')
# Spatially variable finite reservoir verifies per-face rather than global cap.
edit(erosion, '0/Mbed', 'bed { type fixedValue; value uniform 0.01; }',
     'bed { type fixedValue; value nonuniform List<scalar> 20 ('+' '.join(str((i+1)*1e-8) for i in range(20))+'); }')
rows, _ = run(erosion)
assert rows[0][0] > 0
assert rows[-1][1] < 1e-15
assert min(values(erosion/'0.2/Mbed', 'bed')) >= -1e-15
print('PASS: erosion releases sediment and cannot overdraw variable face inventory', flush=True)

restart = case('restart', '0.5')
run(restart)
edit(restart, 'system/controlDict', 'startFrom startTime;', 'startFrom latestTime;')
edit(restart, 'system/controlDict', 'endTime 0.5;', 'endTime 1;')
run(restart, log='log.restart', mesh=False)
for name, entry in [('C', 'internalField'), ('Mbed', 'bed')]:
    a, b = values(channel/'1'/name, entry), values(restart/'1'/name, entry)
    assert len(a) == len(b)
    assert max(abs(x-y) for x, y in zip(a,b)) < 1e-9
print('PASS: restarted C and Mbed match uninterrupted integration', flush=True)

for model in ['kEpsilon', 'Smagorinsky', 'SpalartAllmarasDES']:
    dst = case(model, '0.05')
    turbulence_fields(dst, model)
    rows, text = run(dst)
    assert model in text
    assert max(values(dst/'0.05/Ds')) > 1e-6
    print(f'PASS: {model} turbulence selection and eddy sediment diffusivity', flush=True)


# Long enough for sediment to reach the downstream open boundary.
outflow = case('outflow', '12')
rows, _ = run(outflow)
assert rows[-1][0] > 0
flux = values(outflow/'12/sedimentFlux', 'internalField')
# Query the emitted patch flux, not net external flux (which includes inflow).
patch = (outflow/'12/sedimentFlux').read_text().split('\n    downstream\n')[1]
assert sum(float(x) for x in re.search(r'\((.*?)\)', patch, re.S)[1].split()) > 0
print('PASS: sediment exits the downstream boundary', flush=True)

reverse = case('reverse')
edit(reverse, '0/U', '(0.1 0 0)', '(-0.1 0 0)')
edit(reverse, '0/C', 'upstream { type inletOutlet; phi phiSed; inletValue uniform 0.1; value uniform 0.1; }',
     'upstream { type inletOutlet; phi phiSed; inletValue uniform 0; value uniform 0; }')
edit(reverse, '0/C', 'downstream { type inletOutlet; phi phiSed; inletValue uniform 0; value uniform 0; }',
     'downstream { type inletOutlet; phi phiSed; inletValue uniform 0.1; value uniform 0.1; }')
rows, _ = run(reverse)
assert rows[-1][0] > 0 and rows[-1][2] < 0
print('PASS: particle-flux inletOutlet handles reverse flow', flush=True)

multiple = case('multipleBeds')
edit(multiple, 'constant/sedimentProperties', 'bedPatches (bed);', 'bedPatches (bed lid);')
edit(multiple, 'constant/sedimentProperties', 'thetaCrit 0.05;', 'thetaCrit 1e-9;')
edit(multiple, 'constant/sedimentProperties', 'erosionRate 0;', 'erosionRate 1;')
edit(multiple, '0/U', 'lid { type slip; }', 'lid { type noSlip; }')
edit(multiple, '0/Mbed', 'value uniform 0.01;', 'value uniform 1e-8;')
edit(multiple, '0/Mbed', 'lid { type calculated; value uniform 0; }',
     'lid { type fixedValue; value uniform 1e-8; }')
edit(multiple, '0/C', 'lid { type zeroGradient; }',
     'lid { type mixed; refValue uniform 0; refGradient uniform 0; valueFraction uniform 0; value uniform 0; }')
zero_inflow(multiple)
rows, _ = run(multiple)
assert rows[0][0] > 0
print('PASS: two independently inventoried active wall patches', flush=True)

parallel = case('parallel', '1')
(parallel/'system/decomposeParDict').write_text(
    'FoamFile { version 2.0; format ascii; class dictionary; object decomposeParDict; }\n'
    'numberOfSubdomains 2; method simple; simpleCoeffs { n (2 1 1); delta 0.001; }\n')
command(parallel, ['blockMesh'], 'log.blockMesh')
command(parallel, ['decomposePar'], 'log.decomposePar')
text = command(parallel, ['mpirun', '-np', '2', 'sedimentPimpleFoam', '-parallel'], 'log.parallel')
rows = [[float(x) for x in row] for row in BUDGET.findall(text)]
assert rows and all(abs(row[3]) < 1e-10 for row in rows)
command(parallel, ['reconstructPar', '-latestTime'], 'log.reconstructPar')
for name, entry in [('C', 'internalField'), ('Mbed', 'bed')]:
    a, b = values(channel/'1'/name, entry), values(parallel/'1'/name, entry)
    assert len(a) == len(b)
    assert max(abs(x-y) for x,y in zip(a,b)) < 1e-8
print('PASS: two-rank parallel bed conservation and field reconstruction', flush=True)

# Sheared mesh exercises corrected diffusion and flux-consistent accounting.
skewed = case('nonorthogonal')
edit(skewed, 'system/blockMeshDict',
     '(0 0 0.1) (1 0 0.1) (1 0.1 0.1) (0 0.1 0.1)',
     '(0.03 0 0.1) (1.03 0 0.1) (1.03 0.1 0.1) (0.03 0.1 0.1)')
rows, _ = run(skewed)
assert rows[-1][0] > 0 and rows[-1][1] > 0.001
print('PASS: nonorthogonal mesh conservative corrected diffusion', flush=True)

# Invalid input must fail rather than divide by zero or quietly reset a bed.
invalid = case('invalid')
edit(invalid, 'constant/sedimentProperties', 'ScT 0.7;', 'ScT 0;')
command(invalid, ['blockMesh'], 'log.blockMesh')
text = command(invalid, ['sedimentPimpleFoam'], 'log.solver', expected=1)
assert 'Invalid sedimentProperties' in text
print('PASS: invalid Schmidt number rejected', flush=True)
missing = case('missingInventory')
command(missing, ['blockMesh'], 'log.blockMesh')
(missing/'0/Mbed').unlink()
text = command(missing, ['sedimentPimpleFoam'], 'log.solver', expected=1)
assert 'Mbed' in text and 'cannot find file' in text
print('PASS: missing inventory is rejected instead of replenished', flush=True)
print('All integration checks passed.', flush=True)
