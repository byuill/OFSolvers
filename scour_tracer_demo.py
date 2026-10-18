"""Generate a small water/air scour-hole carrier case for largeTracerFoam."""
from dataclasses import replace
import argparse
import json
import math
from pathlib import Path

from large_tracer import TracerConfig, save_tracers


def header(name, kind='dictionary'):
    return f'FoamFile\n{{ version 2.0; format ascii; class {kind}; object {name}; }}\n'


def field(name, dimensions, initial, boundaries, vector=False):
    return (header(name, 'volVectorField' if vector else 'volScalarField')
            + f'dimensions {dimensions};\ninternalField uniform {initial};\nboundaryField\n{{\n'
            + ''.join(f' {p} {{ {v} }}\n' for p, v in boundaries.items()) + '}\n')


def generate_demo(case, velocity=.3, end_time=5, coarse=False):
    """Fixed 0.2 m-deep depression, 0.5 m water above rim; moving VOF surface."""
    case = Path(case).expanduser().resolve()
    if (not math.isfinite(velocity) or not .1 <= velocity <= 1
            or not math.isfinite(end_time) or end_time <= 0):
        raise ValueError('Use 0.1–1 m/s inlet speed and a positive finite duration.')
    if case.exists() and (not case.is_dir() or any(case.iterdir())):
        raise ValueError('Generate the demo in an empty directory.')
    x = (0, 1, 1.3, 1.7, 2, 3)
    bed = (0, 0, -.2, -.2, 0, 0)
    points = [(xx, yy, zz) for xx, zz0 in zip(x, bed)
              for yy, zz in ((0, zz0), (.5, zz0), (0, 1), (.5, 1))]
    fmt = lambda p: '('+' '.join(f'{v:g}' for v in p)+')'
    blocks, patches = [], {name: [] for name in ('bed','sides','atmosphere')}
    nx = (12, 6, 8, 6, 12) if coarse else (20, 10, 12, 10, 20)
    ny, nz = (6, 24) if coarse else (10, 40)
    for i, n in enumerate(nx):
        a, b = 4*i, 4*(i+1)
        blocks.append(f'hex ({a} {b} {b+1} {a+1} {a+2} {b+2} {b+3} {a+3}) ({n} {ny} {nz}) simpleGrading (1 1 1)')
        patches['bed'].append((a,a+1,b+1,b))
        patches['sides'] += [(a,b,b+2,a+2),(a+1,a+3,b+3,b+1)]
        patches['atmosphere'].append((a+2,b+2,b+3,a+3))
    patches['upstream'] = [(0,2,3,1)]
    patches['downstream'] = [(20,21,23,22)]
    mesh = header('blockMeshDict')+'convertToMeters 1;\nvertices (\n'
    mesh += '\n'.join(fmt(p) for p in points)+');\nblocks (\n'+'\n'.join(blocks)+');\nedges ();\nboundary (\n'
    for name, faces in patches.items():
        mesh += f'{name} {{ type {"wall" if name in ("bed","sides") else "patch"}; faces ('+' '.join(fmt(f) for f in faces)+'); }\n'
    mesh += ');\nmergePatchPairs ();\n'
    def bc(up, down, wall, top):
        return dict(upstream=up, downstream=down, bed=wall, sides=wall, atmosphere=top)
    files = {
        'system/blockMeshDict': mesh,
        '0/U': field('U','[0 1 -1 0 0 0 0]','(0 0 0)',bc(
            f'type variableHeightFlowRateInletVelocity; flowRate constant {velocity*.5*.5:.16g}; alpha alpha.water; value uniform (0 0 0);',
            'type pressureInletOutletVelocity; value uniform (0 0 0);',
            'type noSlip;', 'type pressureInletOutletVelocity; value uniform (0 0 0);'),True),
        '0/alpha.water': field('alpha.water','[0 0 0 0 0 0 0]','0',bc(
            'type fixedValue; value uniform 0;',
            'type inletOutlet; inletValue uniform 0; value uniform 0;',
            'type zeroGradient;', 'type inletOutlet; inletValue uniform 0; value uniform 0;')),
        '0/p_rgh': field('p_rgh','[1 -1 -2 0 0 0 0]','11.772',bc(
            'type fixedFluxPressure; value uniform 0;',
            'type fixedFluxPressure; value uniform 11.772;',
            'type fixedFluxPressure; value uniform 0;',
            'type prghTotalPressure; p0 uniform 0; value uniform 0;')),
        'constant/g': header('g','uniformDimensionedVectorField')+'dimensions [0 1 -2 0 0 0 0]; value (0 0 -9.81);\n',
        'constant/transportProperties': header('transportProperties')+'''
phases (water air);
water { transportModel Newtonian; nu 1e-6; rho 1000; }
air { transportModel Newtonian; nu 1.5e-5; rho 1.2; }
sigma 0.072;
''',
        'constant/turbulenceProperties': header('turbulenceProperties')+'simulationType laminar;\n',
        'system/controlDict': header('controlDict')+f'''
application interFoam;
startFrom startTime; startTime 0; stopAt endTime; endTime {end_time:.16g};
deltaT 0.001; adjustTimeStep yes; maxCo 0.4; maxAlphaCo 0.3; maxDeltaT 0.005;
writeControl adjustableRunTime; writeInterval 0.1; purgeWrite 0;
writeFormat ascii; writePrecision 10; timeFormat general; timePrecision 10;
runTimeModifiable true;
''',
        'system/fvSchemes': header('fvSchemes')+'''
ddtSchemes { default Euler; }
gradSchemes { default Gauss linear; }
divSchemes {
 default none;
 div(rhoPhi,U) Gauss linearUpwind grad(U);
 div(phi,alpha) Gauss vanLeer;
 div(phirb,alpha) Gauss linear;
 div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
fluxRequired { default no; p_rgh; pcorr; alpha.water; }
''',
        'system/fvSolution': header('fvSolution')+'''
solvers {
 "alpha.water.*" { nAlphaCorr 2; nAlphaSubCycles 1; cAlpha 1; MULESCorr yes; nLimiterIter 5;
  solver smoothSolver; smoother symGaussSeidel; tolerance 1e-10; relTol 0; }
 "pcorr.*" { solver GAMG; smoother DICGaussSeidel; tolerance 1e-8; relTol 0; }
 p_rgh { solver GAMG; smoother DICGaussSeidel; tolerance 1e-8; relTol 0.02; }
 p_rghFinal { $p_rgh; relTol 0; }
 U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-9; relTol 0; }
 UFinal { $U; }
}
PIMPLE { momentumPredictor yes; nOuterCorrectors 1; nCorrectors 2; nNonOrthogonalCorrectors 1; }
relaxationFactors { equations { U 1; } }
''',
        'system/setFieldsDict': header('setFieldsDict')+f'''
defaultFieldValues (volScalarFieldValue alpha.water 0 volVectorFieldValue U (0 0 0) volScalarFieldValue p_rgh 11.772);
regions (
 boxToCell {{ box (-1 -1 -1) (4 1 .5); fieldValues (volScalarFieldValue alpha.water 1 volVectorFieldValue U ({velocity:.16g} 0 0) volScalarFieldValue p_rgh 4910.886); }}
 boxToFace {{ box (-1 -1 -1) (4 1 .5); fieldValues (volScalarFieldValue alpha.water 1 volScalarFieldValue p_rgh 4910.886); }}
);
''',
        'Allmesh': '#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\nblockMesh > log.blockMesh 2>&1\ncheckMesh > log.checkMesh 2>&1\nsetFields > log.setFields 2>&1\n',
        'Allrun': '#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\n./Allmesh\ninterFoam > log.interFoam 2>&1\nlargeTracerFoam > log.largeTracerFoam 2>&1\n',
        'scour-demo.json': json.dumps(dict(velocity=velocity,end_time=end_time,coarse=coarse,
            description='Fixed scour hole; 0.5 m water above rim; water/air VOF. Laminar demonstration, not a calibrated scour prediction.'),indent=2)+'\n',
        'scour.foam': '',
    }
    # Files are prepared in memory before the empty target is created.
    case.mkdir(parents=True,exist_ok=True)
    for relative, text in files.items():
        path = case/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(text)
        if path.name in ('Allrun','Allmesh'):
            path.chmod(0o755)
    save_tracers(case, replace(TracerConfig(),end_time=end_time))
    return case


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case',type=Path)
    parser.add_argument('--velocity',type=float,default=.3)
    parser.add_argument('--end-time',type=float,default=5)
    parser.add_argument('--coarse',action='store_true')
    args = parser.parse_args()
    print(generate_demo(args.case,args.velocity,args.end_time,args.coarse))
