"""Validated background mesh generation and retained-volume checks."""
import math
import re
from pathlib import Path
import numpy as np
import pyvista as pv


def block_mesh_dictionary(bounds, cells, grading, boundary_types=None):
    if len(bounds) != 6 or any(not math.isfinite(v) for v in bounds):
        raise ValueError('Mesh bounds must be finite.')
    if any(bounds[2*i] >= bounds[2*i+1] for i in range(3)):
        raise ValueError('Each maximum coordinate must exceed its minimum.')
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in cells):
        raise ValueError('Cell counts must be positive integers.')
    if any(not math.isfinite(g) or g <= 0 for g in grading):
        raise ValueError('Grading ratios must be finite and positive.')
    x0, x1, y0, y1, z0, z1 = bounds
    vertices = [(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),
                (x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
    faces = {'xMin': '(0 4 7 3)', 'xMax': '(1 2 6 5)', 'yMin': '(0 1 5 4)',
             'yMax': '(3 7 6 2)', 'zMin': '(0 3 2 1)', 'zMax': '(4 5 6 7)'}
    types = boundary_types or {}
    patches = []
    for name, face in faces.items():
        kind = types.get(name, 'patch')
        if kind not in ('wall', 'patch', 'empty', 'symmetryPlane'):
            raise ValueError(f'Unsupported background mesh patch type: {kind}')
        patches.append(f' {name} {{ type {kind}; faces ({face}); }}')
    points = '\n'.join(' ('+' '.join(f'{v:.12g}' for v in p)+')' for p in vertices)
    return ('FoamFile { version 2.0; format ascii; class dictionary; object blockMeshDict; }\n'
            f'convertToMeters 1;\nvertices\n(\n{points}\n);\n'
            f'blocks (hex (0 1 2 3 4 5 6 7) ( {" ".join(map(str,cells))} ) '
            f'simpleGrading ( {" ".join(map(str,grading))} ));\nedges ();\nboundary\n(\n'
            +'\n'.join(patches)+'\n);\nmergePatchPairs ();\n')


def retained_point_valid(point, bounds, mesh, inside):
    if any(bounds[2*i] >= bounds[2*i+1] for i in range(3)):
        raise ValueError('Background mesh has invalid bounds.')
    if any(not math.isfinite(v) for v in point) or not all(
        bounds[2*i] < point[i] < bounds[2*i+1] for i in range(3)
    ):
        raise ValueError('locationInMesh must be strictly inside the background mesh.')
    if mesh is None:
        raise ValueError('Load the closed surface to validate the retained region.')
    surface = (mesh if isinstance(mesh,pv.PolyData) else mesh.extract_surface(algorithm='dataset_surface')).triangulate().clean()
    if surface.n_points == 0 or not surface.is_manifold:
        raise ValueError('A closed, manifold surface is required for inside/outside validation.')
    selected = pv.PolyData(np.array([point],dtype=float)).select_interior_points(surface, method='cell_locator', check_surface=True)
    if bool(selected['selected_points'][0]) != inside:
        raise ValueError('locationInMesh lies on the wrong side of the surface.')
    distance = abs(float(pv.PolyData(np.array([point],dtype=float)).compute_implicit_distance(surface)['implicit_distance'][0]))
    if distance < 1e-7*max(bounds[1]-bounds[0], bounds[3]-bounds[2], bounds[5]-bounds[4]):
        raise ValueError('locationInMesh is on or too close to the geometry surface.')
    return True


def find_retained_point(bounds, mesh, inside):
    if mesh is None:
        raise ValueError('Load a closed surface first.')
    if any(bounds[2*i] >= bounds[2*i+1] for i in range(3)):
        raise ValueError('Background mesh has invalid bounds.')
    candidates = [tuple(mesh.center)]
    for x in (.5, .25, .75, .1, .9):
        for y in (.5, .25, .75, .1, .9):
            for z in (.5, .25, .75, .1, .9):
                candidates.append(tuple(bounds[2*i]+f*(bounds[2*i+1]-bounds[2*i])
                                        for i, f in enumerate((x,y,z))))
    # Reject open surfaces once instead of retrying expensive tests.
    surface = (mesh if isinstance(mesh,pv.PolyData) else mesh.extract_surface(algorithm='dataset_surface')).triangulate().clean()
    if surface.n_points == 0 or not surface.is_manifold:
        raise ValueError('Automatic selection requires a closed, manifold surface.')
    for point in candidates:
        try:
            retained_point_valid(point, bounds, surface, inside)
            return point
        except ValueError:
            pass
    raise ValueError('No retained point found. Set and validate a manual point in the intended connected region.')


def snappy_dictionary(filename, refinements, point, max_local, max_global, snap, iterations, feature_snap):
    filename = Path(filename).name
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.(stl|obj)', filename, re.I):
        raise ValueError('Geometry filename must use letters, numbers, underscores, dots or hyphens.')
    name = Path(filename).stem
    if max_global < max_local:
        raise ValueError('maxGlobalCells must be at least maxLocalCells.')
    surfaces, regions = [], []
    for geometry, minimum, maximum, mode in refinements:
        if geometry != name:
            raise ValueError('Only the loaded geometry can be refined; remove rows referring to other files.')
        if minimum > maximum:
            raise ValueError('Minimum refinement level cannot exceed maximum level.')
        if mode == 'surface':
            surfaces.append(f' {name} {{ level ({minimum} {maximum}); }}')
        else:
            regions.append(f' {name} {{ mode {mode}; levels ((1e15 {maximum})); }}')
    vector = '('+' '.join(f'{v:.12g}' for v in point)+')'
    return f'''FoamFile {{ version 2.0; format ascii; class dictionary; object snappyHexMeshDict; }}
castellatedMesh true;
snap {str(snap).lower()};
addLayers false;
geometry {{ {filename} {{ type triSurfaceMesh; name {name}; }} }}
castellatedMeshControls
{{
 maxLocalCells {max_local}; maxGlobalCells {max_global}; minRefinementCells 0;
 maxLoadUnbalance 0.1; nCellsBetweenLevels 3;
 features ();
 refinementSurfaces {{ {''.join(surfaces)} }}
 resolveFeatureAngle 30;
 refinementRegions {{ {''.join(regions)} }}
 locationInMesh {vector};
 allowFreeStandingZoneFaces true;
}}
snapControls
{{
 nSmoothPatch 3; tolerance 2; nSolveIter 30; nRelaxIter 5;
 nFeatureSnapIter {iterations}; implicitFeatureSnap {str(feature_snap).lower()};
 explicitFeatureSnap false; multiRegionFeatureSnap false;
}}
addLayersControls
{{
 relativeSizes true; layers {{}}; expansionRatio 1; finalLayerThickness 0.3;
 minThickness 0.1; nGrow 0; featureAngle 60; nRelaxIter 3;
 nSmoothSurfaceNormals 1; nSmoothNormals 3; nSmoothThickness 10;
 maxFaceThicknessRatio 0.5; maxThicknessToMedialRatio 0.3;
 minMedialAxisAngle 90; nBufferCellsNoExtrude 0; nLayerIter 50;
}}
meshQualityControls
{{
 maxNonOrtho 65; maxBoundarySkewness 20; maxInternalSkewness 4; maxConcave 80;
 minVol 1e-13; minTetQuality 1e-15; minArea -1; minTwist 0.02;
 minDeterminant 0.001; minFaceWeight 0.02; minVolRatio 0.01;
 minTriangleTwist -1; nSmoothScale 4; errorReduction 0.75;
}}
mergeTolerance 1e-6;
'''
