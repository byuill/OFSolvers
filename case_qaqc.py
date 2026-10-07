"""Read-only case preflight checks, separate from the GUI and mesh execution."""
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import re
import shutil

from foam_io import boundary_patches, entries, scalar, value

SOLVERS = ('interFoam', 'multiphaseEulerFoam', 'buoyantBoussinesqPimpleFoam',
           'pimpleFoam', 'simpleFoam', 'sedFoam', 'sedimentPimpleFoam', 'sandParcelPimpleFoam')
PRESSURE_SOLVERS = {'pimpleFoam', 'simpleFoam', 'sedimentPimpleFoam', 'sandParcelPimpleFoam'}


@dataclass
class Report:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    time_directory: str = ''

    @property
    def ok(self):
        return not self.errors


def mesh_passed(output, code):
    return code == 0 and bool(re.search(r'^\s*Mesh OK\.\s*$', output, re.M)) and not re.search(
        r'Failed\s+[1-9]\d*\s+mesh checks|FOAM FATAL', output)


def start_directory(case, control):
    mode = value(control, 'startFrom')
    times = []
    for path in case.iterdir():
        if path.is_dir():
            try:
                time = float(path.name)
                if math.isfinite(time) and time >= 0:
                    times.append((time, path.name))
            except ValueError:
                pass
    if mode in ('latestTime', 'firstTime'):
        if not times:
            raise ValueError('No numeric saved time directory exists.')
        return (max(times) if mode == 'latestTime' else min(times))[1]
    if mode != 'startTime':
        raise ValueError(f'Unsupported startFrom: {mode}')
    time = scalar(control, 'startTime')
    for number, name in times:
        if math.isclose(number, time, abs_tol=1e-12, rel_tol=1e-12):
            return name
    raise ValueError(f'Initial/saved time {time:g} is missing.')


def check_case(case_dir, solver, cores=1, check_programs=True):
    result = Report()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',solver):
        result.errors.append('Select a solver executable name, without paths or arguments.')
        return result
    case = Path(case_dir).expanduser().resolve() if case_dir else None
    if case is None or not case.is_dir():
        result.errors.append('Select an existing OpenFOAM case folder.')
        return result
    for folder in ('constant', 'system'):
        if not (case/folder).is_dir():
            result.errors.append(f'Missing directory: {folder}/')
    for name in ('controlDict', 'fvSchemes', 'fvSolution'):
        if not (case/'system'/name).is_file():
            result.errors.append(f'Missing system/{name}.')
    if result.errors:
        return result
    try:
        control = (case/'system/controlDict').read_text(encoding='utf-8')
        application = value(control, 'application')
        if application != solver:
            result.errors.append(f'Case application is {application}; selected solver is {solver}.')
        result.time_directory = start_directory(case, control)
        if scalar(control, 'endTime') <= float(result.time_directory):
            result.errors.append('endTime must be later than the selected initial/restart time.')
        if scalar(control, 'deltaT') <= 0 or scalar(control, 'writeInterval') <= 0:
            result.errors.append('deltaT and writeInterval must be positive.')
    except (ValueError, OSError) as exc:
        result.errors.append(f'controlDict: {exc}')
    patches = []
    for name in ('points', 'faces', 'owner', 'neighbour', 'boundary'):
        if not (case/'constant/polyMesh'/name).is_file():
            result.errors.append(f'Missing mesh file constant/polyMesh/{name}; run mesh generation first.')
    if not any('Missing mesh file' in e for e in result.errors):
        try:
            patches = boundary_patches(case/'constant/polyMesh/boundary')
        except (OSError, ValueError) as exc:
            result.errors.append(f'Mesh boundary metadata: {exc}')
    fields = ['U']
    properties = []
    if solver in PRESSURE_SOLVERS:
        fields.append('p')
        properties += ['transportProperties', 'turbulenceProperties']
    elif solver == 'interFoam':
        fields += ['p_rgh', 'alpha.water']
        properties += ['transportProperties', 'turbulenceProperties', 'g']
    else:
        result.warnings.append(f'Model-specific fields for {solver} are not fully checked by this GUI.')
    if solver == 'sedimentPimpleFoam':
        fields += ['C', 'Mbed']
        properties += ['sedimentProperties', 'g']
    if solver == 'sandParcelPimpleFoam':
        properties += ['sandCloudProperties', 'g']
    for name in properties:
        if not (case/'constant'/name).is_file():
            result.errors.append(f'Missing constant/{name}.')
    # Turbulence is selected from disk, not from unsaved GUI controls.
    turbulence = case/'constant/turbulenceProperties'
    if turbulence.exists():
        try:
            text = turbulence.read_text()
            regime = value(text, 'simulationType')
            if regime in ('RAS', 'LES'):
                body = value(text, regime)[1:-1]
                model = value(body, 'RASModel' if regime == 'RAS' else 'LESModel')
                required = {'kEpsilon': ['k', 'epsilon', 'nut'], 'RNGkEpsilon': ['k', 'epsilon', 'nut'],
                            'realizableKE': ['k', 'epsilon', 'nut'], 'kOmegaSST': ['k', 'omega', 'nut'],
                            'Smagorinsky': ['nut'], 'WALE': ['nut'], 'kEqn': ['k', 'nut']}
                fields += required.get(model, [])
                if model not in required:
                    result.warnings.append(f'Additional fields required by {model} must be checked manually.')
        except (ValueError, OSError) as exc:
            result.errors.append(f'turbulenceProperties: {exc}')
    if result.time_directory:
        for name in fields:
            path = case/result.time_directory/name
            if not path.is_file():
                result.errors.append(f'Missing {result.time_directory}/{name}.')
                continue
            if not patches:
                continue
            try:
                field_text = path.read_text()
                dimensions = {'U':[0,1,-1,0,0,0,0], 'p':[0,2,-2,0,0,0,0],
                    'p_rgh':[1,-1,-2,0,0,0,0], 'alpha.water':[0]*7,
                    'C':[1,-3,0,0,0,0,0], 'Mbed':[1,-2,0,0,0,0,0]}
                if name in dimensions:
                    actual_dimensions = [float(v) for v in value(field_text,'dimensions').strip('[]').split()]
                    if actual_dimensions != dimensions[name]:
                        result.errors.append(f'{path.name} has incorrect physical dimensions.')
                boundaries = value(field_text, 'boundaryField')[1:-1]
                boundary_entries = entries(boundaries)
                for patch in patches:
                    entry = boundary_entries.get(patch['name'])
                    if entry is None:
                        matches = [item for pattern, item in boundary_entries.items()
                                   if re.fullmatch(pattern, patch['name'])]
                        entry = matches[0] if matches else None
                    if entry is None:
                        result.errors.append(f'{path.name}: missing boundary for {patch["name"]}.')
                    else:
                        body = boundaries[entry.value_start+1:entry.value_end-1]
                        kind = value(body,'type')
                        parsed = entries(body)
                        if kind == 'flowRateInletVelocity' and not {'volumetricFlowRate','massFlowRate'} & set(parsed):
                            result.errors.append(f'{path.name}/{patch["name"]}: flow-rate inlet needs volumetricFlowRate or massFlowRate.')
                        if kind == 'variableHeightFlowRateInletVelocity' and not {'flowRate','alpha'} <= set(parsed):
                            result.errors.append(f'{path.name}/{patch["name"]}: variable-height inlet needs flowRate and alpha.')
                        if kind == 'variableHeightFlowRate' and not 0 <= scalar(body,'lowerBound') < scalar(body,'upperBound') <= 1:
                            result.errors.append(f'{path.name}/{patch["name"]}: phase-fraction bounds must lie between zero and one.')
                        if patch['type'] in ('empty', 'symmetryPlane', 'symmetry', 'cyclic', 'cyclicAMI') and kind != patch['type']:
                            result.errors.append(f'{path.name}/{patch["name"]} must have type {patch["type"]}.')
            except (ValueError, OSError, re.error) as exc:
                message = f'Cannot fully inspect {path.name}: {exc}'
                if 'Included or generated dictionaries' in str(exc):
                    result.warnings.append(message)
                else:
                    result.errors.append(message)
        if solver == 'sandParcelPimpleFoam' and cores == 1 and float(result.time_directory) > 0:
            metadata = case/result.time_directory/'uniform/lagrangian/sandCloud'
            if not (metadata/'sandCloudOutputProperties').is_file():
                result.errors.append('Parcel restart requires saved sandCloudOutputProperties.')
            try:
                cloud_text = (case/'constant/sandCloudProperties').read_text()
                if 'resuspension' in entries(cloud_text):
                    body = value(cloud_text, 'resuspension')[1:-1]
                    enabled = value(body, 'enabled') in ('true', 'yes', 'on', '1')
                    initialize = entries(body).get('initializeHistoryOnRestart')
                    initialize = initialize and value(body, 'initializeHistoryOnRestart') in ('true', 'yes', 'on', '1')
                    if enabled and not initialize and not (metadata/'sandResuspensionState').exists():
                        result.errors.append('Enabled parcel resuspension requires saved sandResuspensionState.')
            except (OSError, ValueError) as exc:
                result.errors.append(f'Parcel restart metadata: {exc}')
    if cores > 1:
        try:
            count = scalar((case/'system/decomposeParDict').read_text(), 'numberOfSubdomains')
            if count != cores:
                result.errors.append(f'Decomposition has {count:g} ranks but {cores} are selected.')
        except (OSError, ValueError) as exc:
            result.errors.append(f'Decomposition is missing or invalid: {exc}')
        rank_times = []
        for rank in range(cores):
            folder = case/f'processor{rank}'
            if not (folder/'constant/polyMesh/boundary').exists():
                result.errors.append(f'Missing processor{rank} mesh; decompose with the selected rank count.')
            try:
                rank_time = start_directory(folder, control)
                rank_times.append(rank_time)
                if scalar(control,'endTime') <= float(rank_time):
                    result.errors.append(f'endTime must be later than processor{rank} restart time {rank_time}.')
                for name in fields:
                    if not (folder/rank_time/name).is_file():
                        result.errors.append(f'Missing processor{rank}/{rank_time}/{name}.')
                if solver == 'sandParcelPimpleFoam' and float(rank_time) > 0:
                    metadata = folder/rank_time/'uniform/lagrangian/sandCloud'
                    if not (metadata/'sandCloudOutputProperties').exists():
                        result.errors.append(f'processor{rank} is missing parcel restart metadata.')
                    cloud_text = (case/'constant/sandCloudProperties').read_text()
                    if 'resuspension' in entries(cloud_text):
                        resume = value(cloud_text,'resuspension')[1:-1]
                        enabled = value(resume,'enabled') in ('true','yes','on','1')
                        initialize = 'initializeHistoryOnRestart' in entries(resume) and value(
                            resume,'initializeHistoryOnRestart') in ('true','yes','on','1')
                        if enabled and not initialize and not (metadata/'sandResuspensionState').exists():
                            result.errors.append(f'processor{rank} is missing bed resuspension history.')
            except (OSError, ValueError) as exc:
                result.errors.append(f'processor{rank} restart: {exc}')
        if len(set(rank_times)) > 1:
            result.errors.append('Processor restart times disagree; retain a consistent complete saved time.')
        if rank_times and len(set(rank_times)) == 1:
            result.time_directory = rank_times[0]
    if check_programs:
        if solver in ('sedimentPimpleFoam','sandParcelPimpleFoam'):
            version = os.environ.get('WM_PROJECT_VERSION','')
            if version and version not in ('1912','v1912'):
                result.errors.append(f'The custom sediment solvers require OpenCFD v1912; active version is {version}.')
        for program in [solver, 'checkMesh'] + (['mpirun'] if cores > 1 else []):
            if shutil.which(program) is None:
                result.errors.append(f'{program} is not on PATH. Activate OpenFOAM before launching the GUI.')
    return result
