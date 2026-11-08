"""Generate a submerged periodic river-bed case for bedloadDemPimpleFoam."""
from dataclasses import dataclass,asdict,fields as dataclass_fields
import argparse
import json
import math
from pathlib import Path

from foam_io import atomic_write
from scour_tracer_demo import header,field


@dataclass(frozen=True)
class BedloadConfig:
    diameter: float = .004
    density: float = 2650
    length: float = .16
    width: float = .064
    height: float = .064
    mobile_layers: int = 4
    initial_speed: float = .3
    driving_acceleration: float = .1
    young_modulus: float = 1e8
    poisson_ratio: float = .25
    restitution: float = .8
    friction: float = .5
    rolling_stiffness: float = 1000
    rolling_damping: float = .1
    rolling_friction: float = .05
    end_time: float = .1
    delta_t: float = .0001
    dem_delta_t: float = .000002
    write_interval: float = .002
    turbulence: str = 'kOmegaSST'
    kernel_ratio: float = 3
    volume_samples: int = 128

    def validate(self):
        for name,number in asdict(self).items():
            if isinstance(number,(int,float)) and not math.isfinite(number):
                raise ValueError(f'{name} must be finite.')
        if min(self.diameter,self.length,self.width,self.height,self.young_modulus,
               self.end_time,self.delta_t,self.dem_delta_t,self.write_interval,self.kernel_ratio)<=0:
            raise ValueError('Sizes, stiffness and time settings must be positive.')
        if self.density<=1000 or min(self.length,self.width)<4*self.diameter:
            raise ValueError('Use submerged sediment density >1000 kg/m3 and horizontal lengths >=4 diameters.')
        if type(self.mobile_layers) is not int or not 1<=self.mobile_layers<=100:
            raise ValueError('mobile_layers must be an integer in 1–100.')
        if (self.mobile_layers+2)*self.diameter>=self.height:
            raise ValueError('Leave water space above the granular bed.')
        if self.initial_speed<0 or not 0<=self.poisson_ratio<.5 or not 0<self.restitution<=1 or self.friction<0:
            raise ValueError('Invalid speed, Poisson ratio, restitution or friction.')
        if min(self.rolling_stiffness,self.rolling_damping,self.rolling_friction)<0:
            raise ValueError('Rolling resistance coefficients cannot be negative.')
        if not ((self.rolling_stiffness==self.rolling_damping==self.rolling_friction==0)
                or self.rolling_stiffness>0 and self.rolling_friction>0):
            raise ValueError('Enable rolling with positive stiffness/friction, or set all rolling coefficients to zero.')
        if self.kernel_ratio<1 or type(self.volume_samples) is not int or not 32<=self.volume_samples<=8192:
            raise ValueError('Kernel radius ratio must be >=1 and volume samples an integer in 32–8192.')
        if self.turbulence not in ('laminar','kOmegaSST','kEpsilon','Smagorinsky','WALE'):
            raise ValueError('Choose a supported porosity-weighted turbulence model.')


def particles(config):
    """Nonoverlapping square layers; a fixed basal layer supplies roughness.

    Alternating layers are offset horizontally and begin with a small vertical
    clearance. Packing is allowed to settle dynamically; this is not a claim
    of a pre-equilibrated random bed.
    """
    config.validate()
    nx=int(config.length/(1.02*config.diameter)); ny=int(config.width/(1.02*config.diameter))
    dx,dy=config.length/nx,config.width/ny
    result=[]
    for layer in range(config.mobile_layers+1):
        z=config.diameter/2+layer*config.diameter*1.02
        for j in range(ny):
            for i in range(nx):
                x=((i+.5+.5*(layer%2))*dx)%config.length
                y=((j+.5+.5*(layer%2))*dy)%config.width
                result.append((len(result)+1,2 if layer==0 else 1,config.diameter,config.density,x,y,z))
    return result


def dem_properties(config):
    c=config
    return header('demCouplingProperties')+f'''
// SI; physical grains, submerged flow, porosity-weighted pressure and stress.
runName bedload;
dragModel DiFelice;
youngModulus {c.young_modulus:.16g}; poissonRatio {c.poisson_ratio:.16g};
restitution {c.restitution:.16g}; friction {c.friction:.16g};
rollingStiffness {c.rolling_stiffness:.16g}; rollingDamping {c.rolling_damping:.16g}; rollingFriction {c.rolling_friction:.16g};
maxDEMDeltaT {c.dem_delta_t:.16g}; maxDEMSubcycles 1000000;
responseTimeFraction .1; couplingDisplacementFraction .1;
kernelRadiusRatio {c.kernel_ratio:.16g}; volumeSamples {c.volume_samples}; minimumPorosity .1;
drivingAcceleration ({c.driving_acceleration:.16g} 0 0);
bedloadElevation {(c.mobile_layers+2)*c.diameter:.16g}; movingSpeed .001;
'''


def generate_bedload(case,config=BedloadConfig(),population=None,cells=None):
    config.validate();case=Path(case).expanduser().resolve()
    if case.exists() and (not case.is_dir() or any(case.iterdir())):
        raise ValueError('Generate the coupled case in an empty folder.')
    grain=particles(config) if population is None else list(population)
    cells=cells or tuple(max(2,int(math.ceil(s/(2.5*config.diameter)))) for s in (config.length,config.width,config.height))
    if len(cells)!=3 or any(type(n) is not int or n<2 for n in cells):
        raise ValueError('Use three integer cell counts, at least two each.')
    l,w,h=config.length,config.width,config.height
    data=f'Physical sediment spheres\n\n{len(grain)} atoms\n2 atom types\n\n0 {l:.16g} xlo xhi\n0 {w:.16g} ylo yhi\n0 {h:.16g} zlo zhi\n\nAtoms # sphere\n\n'
    for expected,row in enumerate(grain,1):
        if len(row)!=7: raise ValueError('Particles need id,type,diameter,density,x,y,z.')
        if (type(row[0]) is not int or row[0]!=expected
                or row[1] not in (1,2) or not all(math.isfinite(x) for x in row)
                or min(row[2],row[3])<=0 or not 0<=row[4]<l or not 0<=row[5]<w or not row[2]/2<=row[6]<=h-row[2]/2):
            raise ValueError('Invalid physical particle or nonconsecutive particle ID.')
        data+=' '.join(str(x) if k<2 else f'{x:.17g}' for k,x in enumerate(row))+'\n'
    patch={name:'type cyclic;' for name in ('xMin','xMax','yMin','yMax')}
    def bcs(bed,top): return dict(patch,bed=bed,lid=top)
    files={'constant/DEM/particles.data':data,
        'constant/demCouplingProperties':dem_properties(config),
        '0/U':field('U','[0 1 -1 0 0 0 0]',f'({config.initial_speed:.16g} 0 0)',bcs('type noSlip;','type slip;'),True),
        '0/p':field('p','[0 2 -2 0 0 0 0]','0',bcs('type fixedFluxPressure; value uniform 0;','type fixedFluxPressure; value uniform 0;')),
        '0/porosity':field('porosity','[0 0 0 0 0 0 0]','1',bcs('type zeroGradient;','type zeroGradient;')),
        'constant/transportProperties':header('transportProperties')+'transportModel Newtonian;\nnu [0 2 -1 0 0 0 0] 1e-6;\nrho [1 -3 0 0 0 0 0] 1000;\n',
        'constant/g':header('g','uniformDimensionedVectorField')+'dimensions [0 1 -2 0 0 0 0]; value (0 0 -9.81);\n',
        'system/blockMeshDict':header('blockMeshDict')+f'''
scale 1;
vertices ((0 0 0) ({l} 0 0) ({l} {w} 0) (0 {w} 0) (0 0 {h}) ({l} 0 {h}) ({l} {w} {h}) (0 {w} {h}));
blocks (hex (0 1 2 3 4 5 6 7) ({' '.join(map(str,cells))}) simpleGrading (1 1 1)); edges ();
boundary (
 xMin {{type cyclic; neighbourPatch xMax; faces ((0 4 7 3));}}
 xMax {{type cyclic; neighbourPatch xMin; faces ((1 2 6 5));}}
 yMin {{type cyclic; neighbourPatch yMax; faces ((0 1 5 4));}}
 yMax {{type cyclic; neighbourPatch yMin; faces ((3 7 6 2));}}
 bed {{type wall; faces ((0 3 2 1));}}
 lid {{type wall; faces ((4 5 6 7));}}
); mergePatchPairs ();
''',
        'system/controlDict':header('controlDict')+f'''
application bedloadDemPimpleFoam; startFrom startTime; startTime 0; stopAt endTime;
endTime {config.end_time:.16g}; deltaT {config.delta_t:.16g};
adjustTimeStep yes; maxCo .3; maxDeltaT {config.delta_t:.16g};
writeControl adjustableRunTime; writeInterval {config.write_interval:.16g}; purgeWrite 0;
writeFormat ascii; writePrecision 16; timePrecision 12; runTimeModifiable false;
''',
        'system/fvSchemes':header('fvSchemes')+'''
ddtSchemes {default Euler;}
gradSchemes {default Gauss linear;}
divSchemes {
 default none; div(phi,U) Gauss upwind;
 div(((porosity*nuEff)*dev2(T(grad(U))))) Gauss linear;
 div(phi,k) Gauss upwind; div(phi,omega) Gauss upwind; div(phi,epsilon) Gauss upwind;
}
laplacianSchemes {default Gauss linear corrected;}
interpolationSchemes {default linear;} snGradSchemes {default corrected;}
fluxRequired {default no; p;}
wallDist {method meshWave;}
''',
        'system/fvSolution':header('fvSolution')+'''
solvers {
 p {solver PCG; preconditioner DIC; tolerance 1e-11; relTol 0;}
 pFinal {$p; relTol 0;}
 U {solver smoothSolver; smoother symGaussSeidel; tolerance 1e-11; relTol 0;}
 UFinal {$U;}
 "(k|omega|epsilon)" {solver smoothSolver; smoother symGaussSeidel; tolerance 1e-10; relTol 0;}
 "(k|omega|epsilon)Final" {solver smoothSolver; smoother symGaussSeidel; tolerance 1e-10; relTol 0;}
}
PIMPLE {nOuterCorrectors 2; nCorrectors 2; nNonOrthogonalCorrectors 1; momentumPredictor yes; pRefCell 0; pRefValue 0;}
relaxationFactors {equations {U 1; k 1; omega 1; epsilon 1;}}
''',
        'system/decomposeParDict':header('decomposeParDict')+'numberOfSubdomains 2; method simple; simpleCoeffs {n (2 1 1); delta .001;}\n',
        'case.foam':'',
        'bedload-dem.json':json.dumps({'schema':1,'settings':asdict(config),'particles':len(grain),
            'description':'Submerged periodic bed; fixed basal roughness, mobile physical spheres; startup demonstration, not calibrated bedload.'},indent=2)+'\n',
        'Allrun':'''#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -e constant/polyMesh/points ] || [ -e constant/polyMesh/points.gz ]; then
 echo 'Use a fresh case; preserve existing mesh/results for deliberate restart.' >&2
 exit 1
fi
blockMesh > log.blockMesh 2>&1
checkMesh > log.checkMesh 2>&1
grep -q '^[[:space:]]*Mesh OK[.][[:space:]]*$' log.checkMesh
bedloadDemPimpleFoam > log.solver 2>&1
'''}
    regime='laminar' if config.turbulence=='laminar' else 'RAS' if config.turbulence in ('kOmegaSST','kEpsilon') else 'LES'
    files['constant/turbulenceProperties']=header('turbulenceProperties')+'simulationType '+regime+';\n'
    if regime!='laminar':
        files['constant/turbulenceProperties']+=f'{regime} {{{regime}Model {config.turbulence}; turbulence on; printCoeffs on;'+(' delta cubeRootVol;' if regime=='LES' else '')+'}\n'
        k=max(1.5*(.05*config.initial_speed)**2,1e-8);scale=.002
        fields={'nut':('[0 2 -1 0 0 0 0]',0,'nutkWallFunction')}
        if regime=='RAS':
            fields['k']=('[0 2 -2 0 0 0 0]',k,'kqRWallFunction')
            if config.turbulence=='kOmegaSST': fields['omega']=('[0 0 -1 0 0 0 0]',math.sqrt(k)/(.09**.25*scale),'omegaWallFunction')
            else: fields['epsilon']=('[0 2 -3 0 0 0 0]',.09**.75*k**1.5/scale,'epsilonWallFunction')
        for name,(dim,initial,wall) in fields.items():
            files['0/'+name]=field(name,dim,str(initial),bcs(f'type {wall}; value uniform {initial};','type zeroGradient;' if name!='nut' else 'type calculated; value uniform 0;'))
    case.mkdir(parents=True,exist_ok=True)
    for name,text in files.items(): atomic_write(case/name,text)
    (case/'Allrun').chmod(0o755)
    return case


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('case',type=Path)
    for setting in dataclass_fields(BedloadConfig):
        parser.add_argument('--'+setting.name.replace('_','-'),type=setting.type,default=setting.default)
    args=vars(parser.parse_args());case=args.pop('case')
    try: print(generate_bedload(case,BedloadConfig(**args)))
    except (ValueError,OSError) as exc: parser.error(str(exc))


if __name__=='__main__':main()
