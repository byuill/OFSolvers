"""Case configuration for the two fixed-water-domain sediment solvers."""
import math
from pathlib import Path
import re
import shutil

from foam_io import (boundary_patches, entries, value, update_values, update_nested,
                     replace_block, write_case_files)
from openfoam_dict_parser import OpenFoamDictParser
from case_qaqc import start_directory

ROOT = Path(__file__).resolve().parent
TEMPLATES = {'sedimentPimpleFoam': 'sedimentChannel', 'sandParcelPimpleFoam': 'sandParcelChannel'}


def create_starter(case_dir, solver):
    case = Path(case_dir).resolve()
    if solver not in TEMPLATES:
        raise ValueError('Select one of the two sediment solvers.')
    if not case.is_dir() or any(case.iterdir()):
        raise ValueError('A starter case requires an existing empty folder. Existing files will not be replaced.')
    for name in ('0', 'constant', 'system'):
        shutil.copytree(ROOT/'tutorials'/TEMPLATES[solver]/name, case/name)
    return case


def case_patches(case_dir):
    path = Path(case_dir)/'constant/polyMesh/boundary'
    if path.exists():
        return boundary_patches(path)
    # A fresh copied blockMesh tutorial has not generated polyMesh yet.
    text = (Path(case_dir)/'system/blockMeshDict').read_text()
    body = value(text, 'boundary')
    if not body.startswith('('):
        raise ValueError('blockMeshDict boundary must be a patch list.')
    inner = body[1:-1]
    return [{'name': name, 'type': value(inner[item.value_start+1:item.value_end-1], 'type')}
            for name, item in entries(inner).items()]


def _word(name):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*', name):
        raise ValueError(f'Patch name is not supported by this writer: {name}')
    return name


def _vector(values):
    if len(values) != 3 or not all(math.isfinite(v) for v in values):
        raise ValueError('Vectors need three finite components.')
    return '('+' '.join(f'{v:.12g}' for v in values)+')'


def validate(config, patches):
    solver = config['solver']
    if solver not in TEMPLATES:
        raise ValueError('Select a sediment solver.')
    types = {p['name']: p['type'] for p in patches}
    inlet, outlet, beds = config['inlet'], config['outlet'], config['beds']
    for name in [inlet, outlet, *beds]:
        _word(name)
        if name not in types:
            raise ValueError(f'Patch {name} is not in the selected case.')
    if inlet == outlet or inlet in beds or outlet in beds:
        raise ValueError('Inlet, outlet and bed selections must be distinct.')
    if types[inlet] != 'patch' or types[outlet] != 'patch':
        raise ValueError('Inlet and outlet must be open mesh patches (type patch).')
    if not beds or len(set(beds)) != len(beds) or any(types[b] != 'wall' for b in beds):
        raise ValueError('Select at least one distinct wall patch for the bed.')
    if any(p['type'] not in ('wall','patch','empty','symmetry','symmetryPlane','cyclic','cyclicAMI') for p in patches):
        raise ValueError('This case contains unsupported mesh patch types; configure those conditions manually.')
    for name, number in config.items():
        if isinstance(number, (int,float)) and not math.isfinite(number):
            raise ValueError(f'{name} must be finite.')
    if not (config['diameter'] > 0 and config['rhoParticle'] > config['rhoFluid'] > 0 and config['nu'] > 0):
        raise ValueError('Require positive diameter/viscosity and sediment density greater than fluid density.')
    if solver == 'sedimentPimpleFoam':
        if min(config['initialC'],config['inletC'],config['initialBed'],config['erosionRate'],config['ws']) < 0:
            raise ValueError('Concentrations, bed inventory, settling speed and erosion rate cannot be negative.')
        if min(config['ScT'],config['ScM'],config['thetaCrit'],config['erosionExponent']) <= 0:
            raise ValueError('Schmidt numbers and erosion threshold/exponent must be positive.')
        if config['settlingModel'] not in ('FergusonChurch','constant'):
            raise ValueError('Select a supported settling model.')
    else:
        if any(p['type'] == 'empty' for p in patches):
            raise ValueError('The parcel feed requires a 3-D mesh; empty patches are not supported.')
        if min(config['feedRate'],config['parcelsPerSecond'],config['duration'],config['width'],config['depth']) <= 0 or config['SOI'] < 0:
            raise ValueError('Feed rate, parcel rate, duration, width and depth must be positive; start cannot be negative.')
        if not 0 < config['referenceHeight'] < config['maximumHeight'] < config['depth']:
            raise ValueError('Require 0 < lower feed height < upper feed height < water depth.')
        if config['profile'] not in ('uniform','Rouse') or config['shearVelocityModel'] not in ('constant','localBed'):
            raise ValueError('Select a supported feed profile and shear estimate.')
        if config['shearVelocityModel'] == 'constant' and config['uStar'] <= 0:
            raise ValueError('Prescribed shear velocity must be positive.')
        if config['shearSampleLength'] <= 0:
            raise ValueError('Bed shear sampling length must be positive.')
        if config.get('shearBed',beds[0]) not in beds:
            raise ValueError('The Rouse shear reference must be one of the selected bed walls.')
        _vector(config['origin']); _vector(config['widthDirection'])
        if sum(v*v for v in config['widthDirection']) <= 0:
            raise ValueError('Transect width direction cannot be zero.')
        if config['thresholdModel'] not in ('Shields','shearStress','nearBedVelocity'):
            raise ValueError('Select a resuspension threshold model.')
        if config['critical'] <= 0 or config['releaseRate'] < 0 or config['releaseExponent'] <= 0:
            raise ValueError('Resuspension threshold/exponent must be positive; release rate cannot be negative.')
        if min(config['minimumRestTime'],config['normalLaunchSpeed'],config['liftHeight']) < 0 or config['liftDiameters'] <= 0:
            raise ValueError('Residence/launch/height cannot be negative; lift diameters must be positive.')


def _initial_field(case, name, cls, dimensions, internal, boundaries):
    parser = OpenFoamDictParser(str(case/'0'/name), cls, name, dimensions, internal)
    text = parser.render(boundaries)
    return update_values(text, {'internalField': internal})


def configure_case(case_dir, config):
    case = Path(case_dir).resolve()
    patches = case_patches(case)
    validate(config, patches)
    if not (case/'0').is_dir():
        raise ValueError('Initial fields must be written in a complete case with a 0/ directory.')
    control = (case/'system/controlDict').read_text()
    current = value(control, 'application')
    if current != config['solver']:
        raise ValueError('Changing solver families requires a fresh starter case or a manually prepared complete case.')
    if float(start_directory(case, control)) != 0:
        raise ValueError('Sediment setup writes initial conditions at time 0. This case is configured to restart '
                         'from a saved nonzero time. Use a fresh starter for changed initial conditions, '
                         'or deliberately set startFrom startTime and startTime 0 before configuring a new run. '
                         'Keep feed and bed-history settings unchanged when continuing a parcel restart.')
    inlet, beds = config['inlet'], set(config['beds'])
    fluid = update_values((case/'constant/transportProperties').read_text(), {
        'rho': f'[1 -3 0 0 0 0 0] {config["rhoFluid"]}',
        'nu': f'[0 2 -1 0 0 0 0] {config["nu"]}'})
    contents = {'constant/transportProperties': fluid}
    if config.get('updateFluidInitial', False):
        for name, internal in [('U','uniform '+_vector(config['initialU'])), ('p',f'uniform {config["initialP"]}')]:
            path = case/'0'/name
            contents['0/'+name] = update_values(path.read_text(), {'internalField': internal})
    if config['solver'] == 'sedimentPimpleFoam':
        properties = update_values((case/'constant/sedimentProperties').read_text(), {
            'd50': config['diameter'], 'rhoSediment': config['rhoParticle'], 'rhoFluid': config['rhoFluid'],
            'settlingNu': config['nu'], 'settlingModel': config['settlingModel'], 'ws': config['ws'],
            'ScT': config['ScT'], 'ScM': config['ScM'], 'bedPatches': '('+' '.join(config['beds'])+')',
            'thetaCrit': config['thetaCrit'], 'erosionRate': config['erosionRate'],
            'erosionExponent': config['erosionExponent']})
        c, bed = {}, {}
        for patch in patches:
            name, kind = patch['name'], patch['type']
            if kind in ('empty','symmetry','symmetryPlane','cyclic','cyclicAMI'):
                c[name] = {'type':kind}; bed[name] = {'type':kind}
            elif name in beds:
                c[name] = {'type':'mixed','refValue':'uniform 0','refGradient':'uniform 0',
                           'valueFraction':'uniform 0','value':'uniform 0'}
                bed[name] = {'type':'fixedValue','value':f'uniform {config["initialBed"]}'}
            else:
                c[name] = {'type':'zeroGradient'} if kind == 'wall' else {
                    'type':'inletOutlet', 'phi':'phiSed', 'inletValue':f'uniform {config["inletC"] if name == inlet else 0}',
                    'value':f'uniform {config["inletC"] if name == inlet else 0}'}
                bed[name] = {'type':'calculated','value':'uniform 0'}
        contents['constant/sedimentProperties'] = properties
        contents['0/C'] = _initial_field(case,'C','volScalarField','[1 -3 0 0 0 0 0]',f'uniform {config["initialC"]}',c)
        contents['0/Mbed'] = _initial_field(case,'Mbed','volScalarField','[1 -2 0 0 0 0 0]','uniform 0',bed)
    else:
        text = (case/'constant/sandCloudProperties').read_text()
        injections = value(value(text,'subModels')[1:-1],'injectionModels')[1:-1]
        if list(entries(injections)) != ['feed']:
            raise ValueError('The GUI supports one injector named feed; other injector configurations are preserved by refusing this edit.')
        text = update_nested(text,['constantProperties'], {'rho0':config['rhoParticle']})
        text = update_nested(text,['subModels','injectionModels','feed'], {
            'type':'sandTransectInjection','massFeedRate':config['feedRate'], 'parcelsPerSecond':config['parcelsPerSecond'],
            'SOI':config['SOI'],'duration':config['duration'],'d50':config['diameter'], 'patch':config['inlet'],
            'origin':_vector(config['origin']),'widthDirection':_vector(config['widthDirection']),
            'width':config['width'],'depth':config['depth'],'referenceHeight':config['referenceHeight'],
            'maximumHeight':config['maximumHeight'],'profile':config['profile'],
            'shearVelocityModel':config['shearVelocityModel'],'uStar':config['uStar'],
            'bedPatch':config.get('shearBed',config['beds'][0]),'shearSampleLength':config['shearSampleLength']})
        # Keep force/dispersion/integration/cloud-function settings intact.
        interaction = []
        for patch in patches:
            name = patch['name']
            if patch['type'] in ('cyclic','cyclicAMI','symmetry','symmetryPlane'):
                continue
            kind = 'stick' if name in beds else ('rebound' if patch['type'] == 'wall' else 'escape')
            coeff = ' e 1; mu 0;' if kind == 'rebound' else ''
            interaction.append(f'{name} {{ type {kind};{coeff} }}')
        sub = value(text,'subModels')[1:-1]
        sub = update_values(sub, {'patchInteractionModel':'localInteraction'})
        sub = replace_block(sub,'localInteractionCoeffs', 'patches (\n'+'\n'.join(interaction)+'\n);')
        text = replace_block(text,'subModels',sub)
        threshold = {'Shields':'criticalShields','shearStress':'criticalShearStress','nearBedVelocity':'criticalVelocity'}[config['thresholdModel']]
        old = value(text,'resuspension')[1:-1] if 'resuspension' in entries(text) else ''
        resume = {'enabled':str(config['resuspension']).lower(),'bedPatches':'('+' '.join(config['beds'])+')',
                  'thresholdModel':config['thresholdModel'],threshold:config['critical'],
                  'releaseRate':config['releaseRate'],'releaseExponent':config['releaseExponent'],
                  'minimumRestTime':config['minimumRestTime'],'normalLaunchSpeed':config['normalLaunchSpeed'],
                  'liftDiameters':config['liftDiameters'],'liftHeight':config['liftHeight']}
        text = replace_block(text,'resuspension',update_values(old,resume))
        contents['constant/sandCloudProperties'] = text
    backup = write_case_files(case, contents)
    return contents, backup
