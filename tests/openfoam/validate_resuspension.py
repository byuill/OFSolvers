#!/usr/bin/env python3
"""Real v1912 solver checks. Source scripts/openfoam-env.sh before running.

Each case and its logs are retained in the printed temporary directory.
"""
from pathlib import Path
import math
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(tempfile.mkdtemp(prefix='sand-resuspension-validation-'))
DEFAULTS = '''
enabled true; bedPatches (bed); thresholdModel Shields;
criticalShields 0.000001; releaseRate 10; releaseExponent 1;
minimumRestTime 0.05; normalLaunchSpeed 0.05; liftDiameters 2;
seed 67890; writeEvents true;
'''
EVENT = re.compile(r'Sand release: id=(p\d+_i\d+) episode=(\d+) time=(\S+) ratio=(\S+) mass=(\S+)')


def edit(dst, name, old, new):
    path = dst/name
    text = path.read_text()
    assert old in text, (path, old)
    path.write_text(text.replace(old, new))


def settings(dst, body):
    path = dst/'constant/sandCloudProperties'
    text = path.read_text().split('// Optional fixed-bed re-entrainment')[0]
    path.write_text(text + ('\nresuspension\n{\n' + body + '\n}\n' if body else ''))


def case(name, body=DEFAULTS, end='0.5'):
    dst = WORK/name
    shutil.copytree(ROOT/'tutorials/sandParcelChannel', dst)
    edit(dst, 'system/controlDict', 'endTime 1;', f'endTime {end};')
    edit(dst, 'system/controlDict', 'writeInterval 0.5;', 'writeInterval 0.1;')
    edit(dst, 'constant/sandCloudProperties', 'duration 1;', 'duration 0.1;')
    edit(dst, 'constant/sandCloudProperties', 'profile Rouse;', 'profile uniform;')
    edit(dst, 'constant/sandCloudProperties', 'referenceHeight 0.005;', 'referenceHeight 0.001;')
    edit(dst, 'constant/sandCloudProperties', 'maximumHeight 0.095;', 'maximumHeight 0.002;')
    settings(dst, body)
    return dst


def command(dst, args, log='log.solver', expected=0):
    with (dst/log).open('w') as stream:
        result = subprocess.run(args, cwd=dst, stdout=stream, stderr=subprocess.STDOUT)
    text = (dst/log).read_text()
    assert result.returncode == expected, (args, dst/log, text[-3500:])
    return text


def run(dst, mesh=True, log='log.solver'):
    if mesh:
        command(dst, ['blockMesh'], 'log.blockMesh')
    text = command(dst, ['sandParcelPimpleFoam'], log)
    assert text.rstrip().endswith('End')
    budget(text)
    return text


def budget(text):
    mass = float(re.findall(r'mass introduced\s*=\s*(\S+)', text)[-1])
    mobile, deposited = map(float, re.findall(r'Sand inventory: mobile=(\S+) deposited=(\S+) kg', text)[-1])
    escaped = float(re.findall(
        r'Parcel fate: system \(number, mass\)\s+- escape\s*=\s*\d+, (\S+)', text)[-1])
    assert math.isclose(mass, mobile+deposited+escaped, rel_tol=1e-9, abs_tol=1e-13)
    return mobile, deposited


def scalar_field(path):
    text = path.read_text()
    match = re.search(r'\n(\d+)\n\(\n(.*?)\n\)', text, re.S)
    if match:
        values = [float(v) for v in match[2].split()]
        assert len(values) == int(match[1])
        return values
    match = re.search(r'\n(\d+)\s*\{\s*(\S+)\s*\}', text)
    return [float(match[2])]*int(match[1])


def particles(dst, time):
    cloud = dst/str(time)/'lagrangian/sandCloud'
    points = [tuple(map(float, v.split())) for v in re.findall(
        r'^\(([^()\n]+)\)\s+\d+', (cloud/'positions').read_text(), re.M)]
    names = ['origProcId', 'origId', 'active', 'nParticle', 'd', 'age']
    fields = {name: scalar_field(cloud/name) for name in names}
    assert all(len(v) == len(points) for v in fields.values())
    return {(int(fields['origProcId'][i]), int(fields['origId'][i])):
            (*points[i], *(fields[name][i] for name in names[2:]))
            for i in range(len(points))}


def state(dst, time):
    return dst/str(time)/'uniform/lagrangian/sandCloud/sandResuspensionState'


def events(text):
    return {(id, int(episode)): (float(t), float(ratio), float(mass))
            for id, episode, t, ratio, mass in EVENT.findall(text)}


def compare(a, b, tolerance=1e-8):
    assert a.keys() == b.keys(), (len(a), len(b))
    assert max((abs(x-y) for key in a for x, y in zip(a[key], b[key])), default=0) < tolerance


def restart(dst, end):
    edit(dst, 'system/controlDict', 'startFrom startTime;', 'startFrom latestTime;')
    path = dst/'system/controlDict'
    path.write_text(re.sub(r'endTime \S+;', f'endTime {end};', path.read_text()))


print('Validation artifacts:', WORK, flush=True)
absent = case('absent', body=None)
baseline = run(absent)
disabled = case('disabled', body='enabled false;')
run(disabled)
assert particles(absent, '0.5') == particles(disabled, '0.5')
assert not state(disabled, '0.5').exists()
assert budget(baseline)[1] > 0
print('PASS: absent and disabled process are identical, retaining permanent bed mass', flush=True)

blocked = case('belowThreshold', DEFAULTS.replace('criticalShields 0.000001', 'criticalShields 100'))
text = run(blocked)
assert not events(text)
assert particles(absent, '0.5') == particles(blocked, '0.5')
assert state(blocked, '0.5').exists()
rest = case('minimumRestTime', DEFAULTS.replace('minimumRestTime 0.05', 'minimumRestTime 10'))
assert not events(run(rest))
assert particles(absent, '0.5') == particles(rest, '0.5')
print('PASS: subcritical shear and minimum residence time prevent release', flush=True)

active = case('Shields')
text = run(active)
released = events(text)
assert released and any(episode > 1 for id, episode in released)
assert min(value[0] for value in released.values()) >= 0.05
assert budget(text)[0] > 0
before, after = particles(absent, '0.5'), particles(active, '0.5')
assert before.keys() == after.keys()
assert all(before[key][4:6] == after[key][4:6] for key in before)  # nParticle and d
print('PASS: Shields release, redeposition and repeat release preserve IDs, sizes and mass', flush=True)

split = case('restart', end='0.2')
first = run(split)
restart(split, '0.5')
second = run(split, mesh=False, log='log.restart')
compare(particles(active, '0.5'), particles(split, '0.5'))
compare(released, events(first) | events(second), tolerance=1e-6)
assert all(abs(value[0] - (events(first) | events(second))[key][0]) < 1e-10
           for key, value in released.items())
# Carrier fields are saved to twelve digits; history itself retains seventeen.
# Compare the history numerically, allowing the carrier restart rounding error.
numbers = lambda path: [float(v) for v in re.findall(
    r'(?:depositedTime|hazard|threshold|releasedMass)\s+(\S+);', path.read_text())]
assert max(abs(a-b) for a, b in zip(numbers(state(active, '0.5')),
                                  numbers(state(split, '0.5')))) < 1e-10
print('PASS: restart retains bed times, hazards, repeated release events and trajectories', flush=True)

stress = case('shearStress', DEFAULTS.replace('thresholdModel Shields;', 'thresholdModel shearStress;')
              .replace('criticalShields 0.000001;', 'criticalShearStress 0.0000020233125;'))
stress_events = events(run(stress))
compare(released, stress_events, tolerance=1e-6)
compare(particles(active, '0.5'), particles(stress, '0.5'))
print('PASS: equivalent dimensional bed stress and Shields thresholds agree', flush=True)

# A uniform slip-wall flow gives an independent constant-rate test. Start both
# time steps from exactly the same deposited inventory, then enable the process.
seed = case('constantFlowSeed', body=None, end='0.3')
edit(seed, '0/U', 'bed { type noSlip; }', 'bed { type slip; }')
run(seed)
waiting = '''enabled true; bedPatches (bed); thresholdModel nearBedVelocity;
criticalVelocity 0.25; releaseRate 10; releaseExponent 1;
minimumRestTime 0; normalLaunchSpeed 0.05; liftHeight 0.002;
initializeHistoryOnRestart true; seed 67890; writeEvents true;'''
dt_events = []
for name, dt in [('dt001', '0.01'), ('dt0005', '0.005')]:
    dst = WORK/name
    shutil.copytree(seed, dst)
    settings(dst, waiting)
    restart(dst, '0.4')
    edit(dst, 'system/controlDict', 'deltaT 0.01;', f'deltaT {dt};')
    edit(dst, 'system/controlDict', 'maxDeltaT 0.01;', f'maxDeltaT {dt};')
    release = events(run(dst, mesh=False, log='log.release'))
    assert all(episode == 1 for id, episode in release)
    assert 45 < len(release) < 80  # expected 100*(1-exp(-10*0.1)) = 63.2
    assert all(math.isclose(ratio, 2, rel_tol=1e-9) for t, ratio, m in release.values())
    # Released parcels only advance for the time remaining after their sampled
    # event. The undisturbed tangential carrier flow is exactly 0.5 m/s.
    initial, final = particles(seed, '0.3'), particles(dst, '0.4')
    for (id, episode), (time, ratio, mass) in release.items():
        key = tuple(map(int, re.findall(r'\d+', id)))
        assert abs(final[key][0] - initial[key][0] - 0.5*(0.4-time)) < 1e-8
    dt_events.append(release)
compare(*dt_events)
print('PASS: velocity threshold, exponential release statistics, time-step invariant waiting and partial-step motion', flush=True)

parallel = case('parallel', DEFAULTS.replace('thresholdModel Shields;', 'thresholdModel nearBedVelocity;')
                .replace('criticalShields 0.000001;', 'criticalVelocity 0.000001; liftHeight 0.002;'), end='0.4')
edit(parallel, '0/U', 'bed { type noSlip; }', 'bed { type slip; }')
edit(parallel, 'system/blockMeshDict', '(1 0', '(0.2 0')
edit(parallel, 'constant/sandCloudProperties', 'shearSampleLength 0.2;', 'shearSampleLength 0.05;')
(parallel/'system/decomposeParDict').write_text(
    'FoamFile { version 2.0; format ascii; class dictionary; object decomposeParDict; }\n'
    'numberOfSubdomains 2; method simple; simpleCoeffs { n (2 1 1); delta 0.001; }\n')
command(parallel, ['blockMesh'], 'log.blockMesh')
command(parallel, ['decomposePar'], 'log.decomposePar')
parallel_text = command(parallel, ['mpirun', '-np', '2', 'sandParcelPimpleFoam', '-parallel'], 'log.parallel')
budget(parallel_text)
latest = max((p.parent.parent.parent.parent for p in (parallel/'processor1').glob(
    '*/uniform/lagrangian/sandCloud/sandResuspensionState')), key=lambda p: float(p.name)).name
downstream = state(parallel/'processor1', latest).read_text()
assert re.search(r'p0_i\d+\s*\{', downstream), downstream
assert events(parallel_text)
restart(parallel, '0.6')
parallel_next = command(parallel, ['mpirun', '-np', '2', 'sandParcelPimpleFoam', '-parallel'], 'log.parallelRestart')
budget(parallel_next)
print('PASS: bed history migrates with parcels across MPI boundaries and restarts on both ranks', flush=True)

missing = WORK/'missingState'
shutil.copytree(split, missing)
state(missing, '0.5').unlink()
edit(missing, 'system/controlDict', 'endTime 0.5;', 'endTime 0.6;')
text = command(missing, ['sandParcelPimpleFoam'], 'log.missing', expected=1)
assert 'Missing sandResuspensionState' in text
invalid = case('invalidLift', DEFAULTS + '\nliftHeight 0.01;')
command(invalid, ['blockMesh'], 'log.blockMesh')
text = command(invalid, ['sandParcelPimpleFoam'], 'log.invalid', expected=1)
assert 'Lift distance must be positive and less than half' in text
print('PASS: missing history and unsafe launch displacement are rejected', flush=True)
print('All resuspension integration checks passed.', flush=True)
