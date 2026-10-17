"""Reproducible castle flume with water/air VOF at 0.5 m water depth."""
from dataclasses import asdict, dataclass
import argparse
import json
import math
from pathlib import Path
import shutil
import tempfile

from foam_io import replace_block, update_values, update_nested, value
from vof_flume import set_solver_blocks

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class FlumeConfig:
    velocity: float = 0.3
    depth: float = 0.5
    diameter: float = 0.000125
    inlet_concentration: float = 0.1
    bed_inventory: float = 1.0
    erosion_rate: float = 0.0
    end_time: float = 30.0

    @property
    def solver(self):
        return 'sedimentInterFoam' if self.depth == .5 else 'sedimentPimpleFoam'

    def validate(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):
            raise ValueError('All flume settings must be finite.')
        if not 0.1 <= self.velocity <= 1:
            raise ValueError('Flow speed must be between 0.1 and 1 m/s.')
        if self.depth not in (0.5, 1.0):
            raise ValueError('Water depth must be 0.5 or 1 m.')
        if self.diameter <= 0 or self.end_time <= 0:
            raise ValueError('Grain diameter and end time must be positive.')
        if min(self.inlet_concentration, self.bed_inventory, self.erosion_rate) < 0:
            raise ValueError('Concentration, bed inventory and erosion rate cannot be negative.')


def castle_profile():
    """CCW x-z outline: three crenellated towers and a bed-open round arch."""
    points = [(1, 0), (1.34, 0), (1.34, .22)]
    points += [(1.5 + .16*math.cos(math.pi-i*math.pi/32),
                .22 + .16*math.sin(math.pi-i*math.pi/32)) for i in range(1, 33)]
    points += [(1.66, 0), (2, 0), (2, .75), (1.94, .75),
               (1.94, .68), (1.88, .68), (1.88, .75), (1.82, .75),
               (1.82, .55), (1.62, .55), (1.62, .75), (1.56, .75),
               (1.56, .68), (1.44, .68), (1.44, .75), (1.38, .75),
               (1.38, .55), (1.18, .55), (1.18, .75), (1.12, .75),
               (1.12, .68), (1.06, .68), (1.06, .75), (1, .75)]
    return points


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _triangulate(points):
    remaining = list(range(len(points)))
    triangles = []
    while len(remaining) > 3:
        for j, b in enumerate(remaining):
            a, c = remaining[j-1], remaining[(j+1) % len(remaining)]
            if _cross(points[a], points[b], points[c]) <= 1e-12:
                continue
            if any(all(_cross(points[u], points[v], points[p]) >= -1e-12
                       for u, v in ((a,b),(b,c),(c,a)))
                   for p in remaining if p not in (a,b,c)):
                continue
            triangles.append((a,b,c))
            remaining.pop(j)
            break
        else:
            raise ValueError('Castle profile cannot be triangulated.')
    triangles.append(tuple(remaining))
    return triangles


def castle_triangles():
    points = castle_profile()
    front = [(x, .075, z) for x,z in points]
    back = [(x, .175, z) for x,z in points]
    triangles = []
    for a,b,c in _triangulate(points):
        triangles += [(front[a],front[b],front[c]), (back[c],back[b],back[a])]
    for i in range(len(points)):
        j = (i+1) % len(points)
        triangles += [(front[i],back[i],back[j]), (front[i],back[j],front[j])]
    return triangles


def castle_stl():
    lines = ['solid castle']
    for a,b,c in castle_triangles():
        u, v = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
        n = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        length = math.sqrt(sum(t*t for t in n))
        lines += [' facet normal '+' '.join(f'{t/length:.12g}' for t in n), '  outer loop']
        lines += ['   vertex '+' '.join(f'{t:.12g}' for t in p) for p in (a,b,c)]
        lines += ['  endloop', ' endfacet']
    return '\n'.join(lines+['endsolid castle', ''])


def geometry_svg(depth=.5):
    """Dimensioned side/plan preview; geometry uses the same profile as the STL."""
    polygon = ' '.join(f'{30+180*x:.4f},{200-180*z:.4f}' for x,z in castle_profile())
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 340">
<rect width="640" height="340" fill="#f4f7fb"/>
<g font-family="sans-serif" font-size="13" fill="#172b44">
<text x="30" y="15">Side view: 3 m long × 1 m high; castle 1 m × 0.75 m</text>
<rect x="30" y="20" width="540" height="180" fill="white" stroke="#172b44"/>
<rect x="30" y="{200-180*depth}" width="540" height="{180*depth}" fill="#b7ddeb"/>
<polygon points="{polygon}" fill="#ac3b36" stroke="#662622"/>
<text x="38" y="43">Flow → | depth {depth:g} m ({"free surface" if depth == .5 else "filled flume"})</text>
<text x="30" y="229">Plan view: 0.25 m channel width; 0.10 m obstacle thickness</text>
<rect x="30" y="245" width="540" height="45" fill="#b7ddeb" stroke="#172b44"/>
<rect x="210" y="258.5" width="180" height="18" fill="#ac3b36" stroke="#662622"/>
<text x="30" y="316">Two 0.075 m side gaps; broad castle face parallel to the side walls</text>
</g></svg>'''


def _header(name, cls='dictionary'):
    return f'FoamFile {{ version 2.0; format ascii; class {cls}; object {name}; }}\n'


def _field(name, dimensions, internal, conditions, vector=False):
    return (_header(name, 'volVectorField' if vector else 'volScalarField')
            + f'dimensions {dimensions};\ninternalField uniform {internal};\nboundaryField\n{{\n'
            + '\n'.join(f' {p} {{ {bc} }}' for p,bc in conditions.items()) + '\n}\n')


def _snappy(depth):
    # Reuse the workbench's v1912 snap and quality controls.
    from mesh_config import snappy_dictionary
    text = snappy_dictionary('castle.stl', [('castle',2,3,'surface')],
                             (.2,.125,.25), 2000000, 4000000, True, 15, False)
    boxes = {
        'obstacle': ((.95,.05,0),(2.05,.20,min(depth,.8)),2),
        'arch': ((1.30,.05,0),(1.70,.20,.42),3),
        'bed': ((0,0,0),(3,.25,.05),2),
        'gapLeft': ((.95,0,0),(2.05,.075,min(depth,.8)),2),
        'gapRight': ((.95,.175,0),(2.05,.25,min(depth,.8)),2),
        'wake': ((2,0,0),(2.8,.25,min(depth,.85)),2),
    }
    geometry = 'castle.stl { type triSurfaceMesh; name castle; }\n'
    regions = ''
    for name,(lo,hi,level) in boxes.items():
        vec = lambda p: '('+' '.join(map(str,p))+')'
        geometry += f'{name} {{ type searchableBox; min {vec(lo)}; max {vec(hi)}; }}\n'
        regions += f'{name} {{ mode inside; levels ((1e15 {level})); }}\n'
    text = replace_block(text,'geometry',geometry)
    text = update_nested(text,['castellatedMeshControls'], {
        'features':'({ file "castle.eMesh"; level 3; })', 'minRefinementCells':100})
    controls = value(text,'castellatedMeshControls')[1:-1]
    controls = replace_block(controls,'refinementSurfaces',
                             'castle { level (2 3); patchInfo { type wall; } }')
    controls = replace_block(controls,'refinementRegions',regions)
    text = replace_block(text,'castellatedMeshControls',controls)
    return update_nested(text,['snapControls'],{'explicitFeatureSnap':'true'})


def generate_case(case_dir, config=FlumeConfig()):
    """Only populate an existing empty folder; stage first to avoid partial cases."""
    config.validate()
    case = Path(case_dir).expanduser().resolve()
    if not case.is_dir() or any(case.iterdir()):
        raise ValueError('Select an existing EMPTY case folder; existing files will not be replaced.')
    from mesh_config import block_mesh_dictionary
    with tempfile.TemporaryDirectory(prefix='castle-flume-') as staging:
        stage = Path(staging)
        for name in ('0','constant','system'):
            shutil.copytree(ROOT/'tutorials/sedimentChannel'/name,stage/name)
        (stage/'constant/triSurface').mkdir()
        files = {'constant/triSurface/castle.stl':castle_stl(),
                 'geometry.svg':geometry_svg(config.depth)}
        block = block_mesh_dictionary((0,3,0,.25,0,1),
                                     (120,10,40), (1,1,1),
                                     {'xMin':'patch','xMax':'patch','yMin':'wall','yMax':'wall',
                                      'zMin':'wall','zMax':'patch' if config.depth == .5 else 'wall'})
        for old,new in [('xMin','upstream'),('xMax','downstream'),('yMin','leftWall'),
                        ('yMax','rightWall'),('zMin','bed'),('zMax','atmosphere' if config.depth == .5 else 'lid')]:
            block = block.replace(old+' {',new+' {')
        files['system/blockMeshDict'] = block
        files['system/snappyHexMeshDict'] = _snappy(1)
        files['system/meshQualityDict'] = (_header('meshQualityDict')
            + value(files['system/snappyHexMeshDict'],'meshQualityControls')[1:-1])
        files['system/surfaceFeatureExtractDict'] = _header('surfaceFeatureExtractDict') + '''
castle.stl
{
 extractionMethod extractFromSurface;
 extractFromSurfaceCoeffs { includedAngle 150; }
 writeObj no;
}
'''
        walls = ('bed','leftWall','rightWall','castle')
        speed = f'({config.velocity:g} 0 0)'
        def bcs(up,down,wall):
            return dict(upstream=up, downstream=down, lid='type zeroGradient;',
                        **{p:wall for p in walls})
        files['0/U'] = _field('U','[0 1 -1 0 0 0 0]',speed,
                              bcs('type fixedValue; value uniform '+speed+';',
                                  'type pressureInletOutletVelocity; value uniform '+speed+';',
                                  'type noSlip;'),True)
        files['0/U'] = files['0/U'].replace('lid { type zeroGradient; }','lid { type slip; }')
        files['0/p'] = _field('p','[0 2 -2 0 0 0 0]','0',
                              bcs('type zeroGradient;','type fixedValue; value uniform 0;',
                                  'type zeroGradient;'))
        c = bcs(f'type inletOutlet; phi phiSed; inletValue uniform {config.inlet_concentration}; value uniform {config.inlet_concentration};',
                'type inletOutlet; phi phiSed; inletValue uniform 0; value uniform 0;', 'type zeroGradient;')
        c['bed'] = 'type mixed; refValue uniform 0; refGradient uniform 0; valueFraction uniform 0; value uniform 0;'
        files['0/C'] = _field('C','[1 -3 0 0 0 0 0]','0',c)
        bed = bcs('type calculated; value uniform 0;','type calculated; value uniform 0;',
                  'type calculated; value uniform 0;')
        bed['lid'] = 'type calculated; value uniform 0;'
        bed['bed'] = f'type fixedValue; value uniform {config.bed_inventory};'
        files['0/Mbed'] = _field('Mbed','[1 -2 0 0 0 0 0]','0',bed)
        # 5% turbulence intensity, length scale 7% hydraulic diameter.
        k = 1.5*(.05*config.velocity)**2
        length = .07*(4*.25*config.depth/(.25+2*config.depth))
        omega = math.sqrt(k)/(.09**.25*length)
        for name,dim,initial,wall in [
            ('k','[0 2 -2 0 0 0 0]',k,'kqRWallFunction'),
            ('omega','[0 0 -1 0 0 0 0]',omega,'omegaWallFunction'),
            ('nut','[0 2 -1 0 0 0 0]',0,'nutkWallFunction')]:
            conditions = bcs(f'type fixedValue; value uniform {initial};',
                             f'type inletOutlet; inletValue uniform {initial}; value uniform {initial};',
                             f'type {wall}; value uniform {initial};')
            if name == 'nut':
                conditions['upstream'] = conditions['downstream'] = conditions['lid'] = 'type calculated; value uniform 0;'
            files['0/'+name] = _field(name,dim,str(initial),conditions)
        files['constant/turbulenceProperties'] = _header('turbulenceProperties') + '''
simulationType RAS;
RAS { RASModel kOmegaSST; turbulence on; printCoeffs on; }
'''
        files['constant/sedimentProperties'] = update_values(
            (stage/'constant/sedimentProperties').read_text(),
            {'d50':config.diameter, 'erosionRate':config.erosion_rate})
        files['system/controlDict'] = update_values((stage/'system/controlDict').read_text(),
            {'endTime':config.end_time,'deltaT':0.0001,'maxDeltaT':0.002,
             'maxCo':0.4,'writeInterval':min(1.0,config.end_time)})
        # Correct nonorthogonality on the snapped obstacle mesh.
        files['system/fvSchemes'] = replace_block(
            (stage/'system/fvSchemes').read_text(),'laplacianSchemes',
            'default Gauss linear corrected;\nlaplacian(Ds,C) Gauss linear uncorrected;')
        files['system/fvSolution'] = update_nested((stage/'system/fvSolution').read_text(),
            ['PIMPLE'],{'nNonOrthogonalCorrectors':2})
        files['flume.json'] = json.dumps({'preset':'castle-flume-v2','solver':config.solver,'flume_dimensions_m':[3,.25,1],
            'obstacle_dimensions_m':[1,.1,.75], 'obstacle_bounds_m':[1,2,.075,.175,0,.75],
            'side_gap_m':.075,'arch_width_m':.32,'arch_crown_m':.38,
            'water_model':'water-air VOF free surface' if config.depth == .5 else 'single-phase filled flume','settings':asdict(config)},indent=2)+'\n'
        files['Allmesh'] = '''#!/bin/sh
set -eu
cd "$(dirname "$0")"
blockMesh > log.blockMesh 2>&1
surfaceFeatureExtract > log.surfaceFeatureExtract 2>&1
snappyHexMesh -overwrite > log.snappyHexMesh 2>&1
checkMesh -allTopology -meshQuality > log.checkMesh 2>&1
grep -q '^[[:space:]]*Mesh OK[.][[:space:]]*$' log.checkMesh
if grep -Eq 'Failed [1-9][0-9]* mesh checks|FOAM FATAL' log.checkMesh; then exit 1; fi
'''
        files['Allrun'] = '''#!/bin/sh
set -eu
cd "$(dirname "$0")"
./Allmesh
sedimentPimpleFoam > log.sedimentPimpleFoam 2>&1
'''
        # Practical flow tolerances; retain tight scalar solves for mass budgets.
        files['system/fvSolution'] = set_solver_blocks(files['system/fvSolution'],{
            'U':'{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.05; }',
            'UFinal':'{ $U; relTol 0; }',
            'p':'{ solver GAMG; smoother DICGaussSeidel; tolerance 1e-8; relTol 0.05; }',
            'pFinal':'{ $p; relTol 0; }'})
        if config.depth == .5:
            from vof_flume import configure_free_surface
            configure_free_surface(files,config,_field,_header)
        files['case.foam'] = ''
        files['README.md'] = (ROOT/'docs/castle-flume.md').read_text().replace('(castle-flume.svg)','(geometry.svg)')
        files['sedimentInterFoam.md'] = (ROOT/'docs/sedimentInterFoam.md').read_text().replace('(castle-flume.md)','(README.md)')
        for name, text in files.items():
            (stage/name).write_text(text)
        preview = ROOT/'docs/figures/castle-velocity-central.png'
        if preview.is_file():
            (stage/'figures').mkdir()
            shutil.copy2(preview,stage/'figures'/preview.name)
        for name in ('Allmesh','Allrun'):
            (stage/name).chmod(0o755)
        if config.depth == .5:
            (stage/'0/p').unlink()
        for item in stage.iterdir():
            shutil.move(str(item),case/item.name)
    return case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case',type=Path,help='Existing empty case directory')
    for name,default in asdict(FlumeConfig()).items():
        parser.add_argument('--'+name.replace('_','-'), type=float,default=default)
    args = vars(parser.parse_args())
    case = args.pop('case')
    try:
        print(generate_case(case,FlumeConfig(**args)))
    except (ValueError,OSError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
