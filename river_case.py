"""OpenCFD v1912 river meshing exports and fixed-domain hydraulic boundaries."""
import json
import math
from pathlib import Path

import numpy as np

from foam_io import boundary_patches, update_values, update_nested, write_case_files, tokens, matching, entries, value
from mesh_config import block_mesh_dictionary
from openfoam_dict_parser import OpenFoamDictParser
from river_design import (RiverDesign, analyze_design, channel_surfaces, obstacle_surfaces,
                          fluid_point, ascii_stl, refinement_level, expanded_obstacles)
from river_design import obstacle_cell_size
from sediment_setup import ROOT, TEMPLATES, configure_case


DESIGN_FILE = 'river-design.json'
REGIONS = ('bed','bankLeft','bankRight','inlet','outlet','lid')
QUALITY = '''maxNonOrtho 65; maxBoundarySkewness 4; maxInternalSkewness 4; maxConcave 80;
minVol 1e-18; minTetQuality 1e-15; minArea -1; minTwist 0.02;
minDeterminant 0.001; minFaceWeight 0.05; minVolRatio 0.05;
minTriangleTwist -1; nSmoothScale 4; errorReduction 0.75;'''


def vector(values):
    return '('+' '.join(f'{float(v):.12g}' for v in values)+')'


def header(name):
    return f'FoamFile {{ version 2.0; format ascii; class dictionary; object {name}; }}\n'


def _grading(values):
    return '('+' '.join(f'({value:.12g} 1 1)' for value in values)+')'


def body_fitted_dictionary(design, report):
    c = design.channel
    # X follows the bed slope. End faces remain vertical and normal to X.
    def coordinates(x):
        z = c.bed_elevation-c.bed_slope*x
        return [(x,-c.width/2,z),(x,c.width/2,z),
                (x,-c.width/2-c.bank_slope*c.height,z+c.height),
                (x,c.width/2+c.bank_slope*c.height,z+c.height)]
    left, right = coordinates(0), coordinates(c.length)
    points = [left[0],right[0],right[1],left[1],left[2],right[2],right[3],left[3]]
    lid_type = 'symmetryPlane' if c.mode == 'fixed' else 'patch'
    patches = [('inlet','patch','(0 4 7 3)'),('outlet','patch','(1 2 6 5)'),
               ('bankLeft','wall','(0 1 5 4)'),('bankRight','wall','(3 7 6 2)'),
               ('bed','wall','(0 3 2 1)'),('lid',lid_type,'(4 5 6 7)')]
    return (header('blockMeshDict')+'convertToMeters 1;\nvertices (\n'
            +'\n'.join(vector(point) for point in points)+'\n);\n'
            +f'blocks (hex (0 1 2 3 4 5 6 7) ({report["streamwise_cells"]} {len(report["lateral_cells"])} {len(report["vertical_cells"])}) '
            +f'simpleGrading (1 {_grading(report["lateral_cells"])} {_grading(report["vertical_cells"])}));\n'
            +'edges ();\nboundary (\n'+'\n'.join(f'{name} {{ type {kind}; faces ({face}); }}' for name,kind,face in patches)
            +'\n);\nmergePatchPairs ();\n')


def snappy_inputs(design, report, patches, closed, solids):
    c,m=design.channel,design.mesh
    padding=2*m.base_size
    bounds=[float(value) for value in closed.bounds]
    background=[value+(-padding if i%2==0 else padding) for i,value in enumerate(bounds)]
    cells=tuple(math.ceil((background[2*i+1]-background[2*i])/m.base_size) for i in range(3))
    if math.prod(cells)>m.max_cells:
        raise ValueError('Padded snappy background exceeds max cells; coarsen the base size.')
    geometry=['channel_boundary.stl { type triSurfaceMesh; name river; regions { '
              +' '.join(f'{name} {{ name {name}; }}' for name in REGIONS)+' } }']
    features=[f'{{ file "channel_boundary.eMesh"; level {report["wall_refinement_level"]}; }}']
    regions=[]
    wall=report['wall_refinement_level']
    for name in REGIONS:
        level=wall if name in ('bed','bankLeft','bankRight') else 0
        # Constraints on a stair-stepped lid distort snapping. Convert its
        # patch type to symmetry only after the geometry has been fitted.
        kind='wall' if name in ('bed','bankLeft','bankRight') else 'patch'
        regions.append(f'{name} {{ level ({level} {level}); patchInfo {{ type {kind}; }} }}')
    surfaces=['river { level (0 0); regions { '+' '.join(regions)+' } }']
    refinement=[]
    obstacles={item.name:item for item in expanded_obstacles(design) if item.kind!='bedform'}
    for name,solid in solids.items():
        item=obstacles[name]
        level=refinement_level(m.base_size,obstacle_cell_size(item,m,c))
        geometry.append(f'{name}.stl {{ type triSurfaceMesh; name {name}; }}')
        surfaces.append(f'{name} {{ level ({level} {level}); patchInfo {{ type wall; }} }}')
        features.append(f'{{ file "{name}.eMesh"; level {level}; }}')
        b=solid.bounds; diameter=max(item.length,item.width)
        low=(max(0,b[0]-2*diameter),b[2]-2*diameter,b[4])
        high=(min(c.length,b[1]+8*diameter),b[3]+2*diameter,b[5]+diameter)
        geometry.append(f'{name}Wake {{ type searchableBox; min {vector(low)}; max {vector(high)}; }}')
        refinement.append(f'{name}Wake {{ mode inside; levels ((1e15 {max(0,level-1)})); }}')
    # Bedform curvature is in the channel surface; add a volume box to resolve it.
    for item in design.obstacles:
        if item.kind!='bedform': continue
        level=refinement_level(m.base_size,min(m.wall_size,item.length/16,item.width/12))
        if level>m.max_level: raise ValueError(f'{item.name}: bedform resolution exceeds the refinement cap.')
        z=c.bed_elevation-c.bed_slope*item.x
        geometry.append(f'{item.name}Refine {{ type searchableBox; min {vector((item.x-item.length*.6,item.y-item.width*.6,z-.5))}; '
                        f'max {vector((item.x+item.length*.8,item.y+item.width*.6,z+item.height+2*m.wall_size))}; }}')
        refinement.append(f'{item.name}Refine {{ mode inside; levels ((1e15 {level})); }}')
    point=fluid_point(design,closed,solids)
    layer_names=['bed','bankLeft','bankRight',*solids]
    text=header('snappyHexMeshDict')+f'''
castellatedMesh true;
snap true;
addLayers {str(bool(m.layers)).lower()};
geometry {{ {' '.join(geometry)} }}
castellatedMeshControls
{{
 maxLocalCells {min(m.max_cells,1000000)};
 maxGlobalCells {m.max_cells};
 minRefinementCells 0;
 maxLoadUnbalance 0.1;
 nCellsBetweenLevels 3;
 features ({' '.join(features)});
 refinementSurfaces {{ {' '.join(surfaces)} }}
 resolveFeatureAngle 30;
 refinementRegions {{ {' '.join(refinement)} }}
 locationInMesh {vector(point)};
 allowFreeStandingZoneFaces true;
}}
snapControls
{{
 nSmoothPatch 3; tolerance 2; nSolveIter 50; nRelaxIter 5;
 nFeatureSnapIter 15; implicitFeatureSnap false; explicitFeatureSnap true;
 multiRegionFeatureSnap false;
}}
addLayersControls
{{
 relativeSizes false;
 layers {{ {' '.join(f'{name} {{ nSurfaceLayers {m.layers}; }}' for name in layer_names)} }}
 expansionRatio {m.growth}; firstLayerThickness {m.first_height}; minThickness {m.first_height*.2};
 nGrow 1; featureAngle 60; slipFeatureAngle 30; nRelaxIter 5;
 nSmoothSurfaceNormals 1; nSmoothNormals 3; nSmoothThickness 10;
 maxFaceThicknessRatio 0.5; maxThicknessToMedialRatio 0.3; minMedialAxisAngle 90;
 nBufferCellsNoExtrude 1; nLayerIter 50; nRelaxedIter 20;
}}
meshQualityControls
{{
 {QUALITY}
 relaxed {{ maxNonOrtho 65; }}
}}
writeFlags (scalarLevels layerSets layerFields);
mergeTolerance {min(1e-6,m.first_height/(100*max(background[2*i+1]-background[2*i] for i in range(3)))):.12g};
'''
    extract=header('surfaceFeatureExtractDict')+'\n'.join(
        f'{name}.stl {{ extractionMethod extractFromSurface; extractFromSurfaceCoeffs {{ includedAngle 150; }} writeObj no; }}'
        for name in ['channel_boundary',*solids])+'\n'
    return {'system/blockMeshDict':block_mesh_dictionary(background,cells,(1,1,1)),
            'system/snappyHexMeshDict':text,'system/surfaceFeatureExtractDict':extract},point


def export_river_case(case_dir, design):
    report=analyze_design(design)
    patches,closed=channel_surfaces(design)
    solids=obstacle_surfaces(design)
    case=Path(case_dir).expanduser().resolve()
    if not case.is_dir(): raise ValueError('Select an existing empty case/export folder.')
    prior=case/DESIGN_FILE
    if any(case.iterdir()) and not prior.exists():
        raise ValueError('River case/export creation needs an empty folder or an unmeshed case owned by River Design.')
    if (case/'constant/polyMesh').exists() or any(path.is_dir() and path.name.replace('.','',1).isdigit() and float(path.name)>0 for path in case.iterdir()) or any(case.glob('processor[0-9]*')):
        raise ValueError('This folder contains a mesh or results. Preserve it and select a fresh design case instead of replacing simulation inputs.')
    if prior.exists():
        old=json.loads(prior.read_text())
        if old.get('tool')!='ofsolvers-river-design' or old['design']['hydraulic']['solver']!=design.hydraulic.solver or old['design']['channel']['mode']!=design.channel.mode:
            raise ValueError('Use a fresh folder when changing solver families or fixed/VOF export mode.')
    template=TEMPLATES.get(design.hydraulic.solver,'sedimentChannel')
    contents={}
    # A VOF design is a mesh/geometry export, not an incomplete interFoam case.
    if design.channel.mode=='fixed':
        for folder in ('0','constant','system'):
            for path in (ROOT/'tutorials'/template/folder).rglob('*'):
                if path.is_file():
                    relative=str(path.relative_to(ROOT/'tutorials'/template))
                    if design.hydraulic.solver=='pimpleFoam' and relative in ('0/C','0/Mbed','constant/sedimentProperties'):continue
                    contents[relative]=path.read_bytes()
        if design.hydraulic.solver=='sedimentPimpleFoam':
            # Bound the concentration gradient used by nonorthogonal diffusion
            # correction at sharp inflow fronts. Retain corrected diffusion and
            # its conservative equation flux; never clip transported mass.
            contents['system/fvSchemes']=update_nested(contents['system/fvSchemes'].decode(),
                ['gradSchemes'],{'grad(C)':'cellLimited Gauss linear 1'})
        text=contents['system/controlDict'].decode()
        h=design.hydraulic
        dt=min(report['advective_dt_estimate'],report['settling_dt_estimate'],h.end_time/100)
        contents['system/controlDict']=update_values(text, {'application':h.solver,'endTime':h.end_time,'deltaT':dt,
            'adjustTimeStep':'yes','maxCo':h.max_co,'maxDeltaT':dt,'writeInterval':max(h.end_time/20,dt),'writeControl':'runTime'})
    else:
        contents['system/controlDict']=header('controlDict')+'application interFoam; startFrom startTime; startTime 0; endTime 1; deltaT 0.001; writeControl timeStep; writeInterval 100;\n'
        # checkMesh/snappy read fvMesh dictionaries even for geometry-only work.
        # These are meshing placeholders, not a completed multiphase solve setup.
        contents['system/fvSchemes']=header('fvSchemes')+'''// Meshing only: replace for the chosen native free-surface solver.
ddtSchemes { default Euler; }
gradSchemes { default Gauss linear; }
divSchemes { default none; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
fluxRequired { default no; }
'''
        contents['system/fvSolution']=header('fvSolution')+'// Meshing only; native phase/pressure solution controls are required.\nsolvers {}\n'
    contents['constant/triSurface/channel_boundary.stl']=ascii_stl(patches)
    contents['constant/triSurface/channel_terrain.stl']=ascii_stl({name:patches[name] for name in ('bed','bankLeft','bankRight')})
    for name,solid in solids.items(): contents[f'constant/triSurface/{name}.stl']=ascii_stl({name:solid})
    point=None
    if report['method']=='block': contents['system/blockMeshDict']=body_fitted_dictionary(design,report)
    else:
        dictionaries,point=snappy_inputs(design,report,patches,closed,solids)
        contents.update(dictionaries)
    boundary_plan={'water_discharge_m3_s':design.hydraulic.discharge,'hydrograph_time_Q_C':design.hydraulic.hydrograph,
                   'water_depth_m':design.channel.depth,'outlet_bed_elevation_m':design.channel.bed_elevation-design.channel.bed_slope*design.channel.length,
                   'outlet_water_level_design_m':design.channel.bed_elevation-design.channel.bed_slope*design.channel.length+design.channel.depth,
                   'outlet_kinematic_pressure_m2_s2':design.hydraulic.outlet_pressure,
                   'note':'Stage is a geometric/design target, not a p boundary. VOF exports need a complete native phase/pressure/free-surface case.'}
    contents['river-boundary-plan.json']=json.dumps(boundary_plan,indent=2)+'\n'
    contents['system/meshQualityDict']=header('meshQualityDict')+QUALITY+'\n'
    manifest=dict(tool='ofsolvers-river-design',version=1,design=design.to_dict(),report=report,
                  location_in_mesh=point,mesh_checked=False,boundaries_ready=False)
    contents[DESIGN_FILE]=json.dumps(manifest,indent=2,allow_nan=False)+'\n'
    backup=write_case_files(case,contents)
    return manifest,backup


def export_geometry(folder,design):
    analyze_design(design)
    patches,_=channel_surfaces(design)
    solids=obstacle_surfaces(design)
    files={'channel_boundary.stl':ascii_stl(patches),
           'channel_terrain.stl':ascii_stl({name:patches[name] for name in ('bed','bankLeft','bankRight')}),
           'river-geometry.json':json.dumps(design.to_dict(),indent=2)+'\n'}
    files.update({name+'.stl':ascii_stl({name:solid}) for name,solid in solids.items()})
    return write_case_files(folder,files)


def load_river_case(case_dir):
    data=json.loads((Path(case_dir)/DESIGN_FILE).read_text())
    if data.get('tool')!='ofsolvers-river-design' or data.get('version')!=1:
        raise ValueError('Unrecognized river design manifest.')
    design=RiverDesign.from_dict(data['design'])
    analyze_design(design)
    return data,design


def mesh_commands(case_dir):
    data,_=load_river_case(case_dir)
    if (Path(case_dir)/'constant/polyMesh/faces').exists():
        raise ValueError('A mesh already exists; use a fresh case for mesh redesign.')
    commands=[['blockMesh']]
    if data['report']['method']=='snappy': commands += [['surfaceFeatureExtract'],['snappyHexMesh','-overwrite']]
    return commands+[['checkMesh','-allTopology','-meshQuality']]


def prepare_mesh_check(case_dir):
    """Set the final rigid-lid constraint after unconstrained surface snapping."""
    case=Path(case_dir);data,design=load_river_case(case)
    if design.channel.mode!='fixed' or data['report']['method']!='snappy':return
    path=case/'constant/polyMesh/boundary';text=path.read_text()
    items=tokens(text);start=matching(items,1)+1 if items[0][0]=='FoamFile' else 0
    if not items[start][0].isdigit() or items[start+1][0]!='(':raise ValueError('Invalid generated mesh boundary list.')
    finish=matching(items,start+1)
    begin,end=items[start+1].end(),items[finish].start()
    inner=text[begin:end];item=entries(inner).get('lid')
    if item is None:raise ValueError('Snappy mesh has no named lid patch.')
    body=inner[item.value_start+1:item.value_end-1]
    changed='{\n'+update_values(body,{'type':'symmetry'})+'\n}'
    inner=inner[:item.value_start]+changed+inner[item.value_end:]
    write_case_files(case,{'constant/polyMesh/boundary':text[:begin]+inner+text[end:]})


def _field(case,name,cls,dimensions,initial,conditions):
    parser=OpenFoamDictParser(str(case/'0'/name),cls,name,dimensions,initial)
    return update_values(parser.render(conditions),{'internalField':initial})


def _table(rows,column):
    return 'table ('+' '.join(f'({row[0]:.12g} {row[column]:.12g})' for row in rows)+')'


def finalize_river_boundaries(case_dir, mesh_checked=False):
    case=Path(case_dir)
    data,design=load_river_case(case)
    c,h=design.channel,design.hydraulic
    patches=boundary_patches(case/'constant/polyMesh/boundary')
    names={item['name'] for item in patches}
    required=set(REGIONS)|{item.name for item in expanded_obstacles(design) if item.kind!='bedform'}
    if not required.issubset(names):
        raise ValueError('River mesh is missing named patches: '+', '.join(sorted(required-names))+'. Complete the intended mesh, not just the snappy background.')
    boundary=(case/'constant/polyMesh/boundary').read_text()
    items=tokens(boundary);start=matching(items,1)+1 if items[0][0]=='FoamFile' else 0
    finish=matching(items,start+1)
    body=boundary[items[start+1].end():items[finish].start()]
    for name,item in entries(body).items():
        count=int(value(body[item.value_start+1:item.value_end-1],'nFaces'))
        if name in required and count<=0:
            raise ValueError(f'Generated patch {name} has no faces. Refine/move the feature and rebuild a fresh case.')
    if c.mode=='vof':
        data.update(mesh_checked=bool(mesh_checked),boundaries_ready=False)
        write_case_files(case,{DESIGN_FILE:json.dumps(data,indent=2)+'\n'})
        return data
    if value((case/'system/controlDict').read_text(),'application')!=h.solver:
        raise ValueError('Case solver differs from the river design; use a fresh consistent case.')
    report=data['report']
    q0=h.hydrograph[0][1] if h.hydrograph else h.discharge
    c0=h.hydrograph[0][2] if h.hydrograph else h.inlet_c
    speed=q0/report['area_m2']
    k=max(1.5*(speed*h.intensity)**2,1e-12)
    epsilon=max(.09**.75*k**1.5/h.length_scale,1e-12)
    omega=max(math.sqrt(k)/(.09**.25*h.length_scale),1e-6)
    obstacle_ks={item.name:item.ks for item in expanded_obstacles(design) if item.kind!='bedform'}
    roughness={'bed':h.bed_ks,'bankLeft':h.bank_ks,'bankRight':h.bank_ks,**obstacle_ks}
    velocity={}; pressure={}; kinetic={}; dissipation={}; nut={}
    for patch in patches:
        name,kind=patch['name'],patch['type']
        if name=='inlet':
            velocity[name]={'type':'flowRateInletVelocity','volumetricFlowRate':_table(h.hydrograph,1) if h.hydrograph else f'constant {h.discharge:.12g}',
                            'extrapolateProfile':'no','value':'uniform '+vector((speed,0,-c.bed_slope*speed))}
            pressure[name]={'type':'zeroGradient'}
            kinetic[name]={'type':'fixedValue','value':f'uniform {k:.12g}'}
            dissipation[name]={'type':'fixedValue','value':f'uniform {epsilon if h.turbulence=="kEpsilon" else omega:.12g}'}
            nut[name]={'type':'calculated','value':'uniform 0'}
        elif name=='outlet':
            velocity[name]={'type':'inletOutlet','inletValue':'uniform (0 0 0)','value':'uniform '+vector((speed,0,-c.bed_slope*speed))}
            pressure[name]={'type':'fixedValue','value':f'uniform {h.outlet_pressure:.12g}'}
            kinetic[name]={'type':'inletOutlet','inletValue':f'uniform {k:.12g}','value':f'uniform {k:.12g}'}
            dissipation[name]={'type':'inletOutlet','inletValue':f'uniform {epsilon if h.turbulence=="kEpsilon" else omega:.12g}','value':f'uniform {epsilon if h.turbulence=="kEpsilon" else omega:.12g}'}
            nut[name]={'type':'calculated','value':'uniform 0'}
        elif kind in ('symmetry','symmetryPlane'):
            for field in (velocity,pressure,kinetic,dissipation,nut): field[name]={'type':kind}
        elif kind=='wall' and name in roughness:
            velocity[name]={'type':'noSlip'};pressure[name]={'type':'zeroGradient'}
            kinetic[name]={'type':'kqRWallFunction','value':f'uniform {k:.12g}'}
            dissipation[name]={'type':'epsilonWallFunction' if h.turbulence=='kEpsilon' else 'omegaWallFunction',
                               'value':f'uniform {epsilon if h.turbulence=="kEpsilon" else omega:.12g}'}
            nut[name]={'type':'nutkRoughWallFunction','Ks':f'uniform {roughness[name]:.12g}','Cs':f'uniform {h.roughness_cs:.12g}','value':'uniform 0'} if roughness[name] else {'type':'nutkWallFunction','value':'uniform 0'}
        else:
            raise ValueError(f'Unexpected mesh patch {name} ({kind}); inspect geometry instead of assigning a default condition.')
    files={'0/U':_field(case,'U','volVectorField','[0 1 -1 0 0 0 0]','uniform '+vector((speed,0,-c.bed_slope*speed)),velocity),
           '0/p':_field(case,'p','volScalarField','[0 2 -2 0 0 0 0]',f'uniform {h.outlet_pressure:.12g}',pressure),
           '0/k':_field(case,'k','volScalarField','[0 2 -2 0 0 0 0]',f'uniform {k:.12g}',kinetic),
           '0/'+('epsilon' if h.turbulence=='kEpsilon' else 'omega'):_field(case,'epsilon' if h.turbulence=='kEpsilon' else 'omega','volScalarField',
               '[0 2 -3 0 0 0 0]' if h.turbulence=='kEpsilon' else '[0 0 -1 0 0 0 0]',f'uniform {epsilon if h.turbulence=="kEpsilon" else omega:.12g}',dissipation),
           '0/nut':_field(case,'nut','volScalarField','[0 2 -1 0 0 0 0]','uniform 0',nut),
           'constant/turbulenceProperties':header('turbulenceProperties')+f'simulationType RAS; RAS {{ RASModel {h.turbulence}; turbulence on; printCoeffs on; }}\n'}
    write_case_files(case,files)
    if h.solver in TEMPLATES:
        sediment={'solver':h.solver,'inlet':'inlet','outlet':'outlet','beds':['bed'],'shearBed':'bed',
            'diameter':h.grain_mm/1000,'rhoFluid':h.water_density,'rhoParticle':h.particle_density,'nu':h.nu,
            'initialC':h.initial_c,'inletC':c0,'initialBed':h.bed_inventory,'erosionRate':0.,'ws':0.,
            'settlingModel':'FergusonChurch','ScT':.7,'ScM':1.,'thetaCrit':.05,'erosionExponent':1.5,
            'feedRate':h.discharge*h.inlet_c,'parcelsPerSecond':h.parcel_rate,'duration':h.end_time,'SOI':0.,
            'width':.8*c.width,'depth':c.depth,'referenceHeight':max(c.depth*h.parcel_lower_fraction,2*h.grain_mm/1000),'maximumHeight':c.depth*h.parcel_upper_fraction,
            'profile':h.parcel_profile,'shearVelocityModel':'constant','uStar':report['ustar_estimate_m_s'],'shearSampleLength':max(c.length*.05,design.mesh.base_size),
            'origin':(0,0,c.bed_elevation),'widthDirection':(0,1,0),'resuspension':False,'thresholdModel':'Shields',
            'critical':.05,'releaseRate':1.,'releaseExponent':1.5,'minimumRestTime':.1,'normalLaunchSpeed':.02,'liftHeight':0.,'liftDiameters':2.,
            'updateFluidInitial':False}
        configure_case(case,sediment)
        if h.hydrograph and h.solver=='sedimentPimpleFoam':
            path=case/'0/C'
            parser=OpenFoamDictParser(str(path),'volScalarField','C','[1 -3 0 0 0 0 0]',f'uniform {h.initial_c:.12g}')
            write_case_files(case,{'0/C':parser.render({'inlet':{'type':'uniformFixedValue','uniformValue':_table(h.hydrograph,2),'value':f'uniform {c0:.12g}'}},merge=True)})
    else:
        properties=(case/'constant/transportProperties').read_text()
        write_case_files(case,{'constant/transportProperties':update_values(properties,{'nu':f'[0 2 -1 0 0 0 0] {h.nu}'})})
    data.update(mesh_checked=bool(mesh_checked),boundaries_ready=True)
    write_case_files(case,{DESIGN_FILE:json.dumps(data,indent=2)+'\n'})
    return data
