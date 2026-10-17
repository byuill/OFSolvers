"""Water/air initialization and boundary conditions for the half-depth flume."""
from foam_io import update_values, update_nested, value, replace_block, entries


def set_solver_blocks(text, blocks):
    body = value(text,"solvers")[1:-1]
    for name, settings in blocks.items():
        key = name.strip('"')
        body = replace_block(body,key if key in entries(body) else name,
                             settings.strip().removeprefix("{").removesuffix("}"))
    return replace_block(text,"solvers",body)


def configure_free_surface(files, config, field, header):
    solver = 'sedimentInterFoam'
    walls = ('bed','leftWall','rightWall','castle')
    def boundaries(up,down,wall,top):
        return dict(upstream=up,downstream=down,atmosphere=top,**{p:wall for p in walls})
    q = config.velocity*.25*config.depth
    files['0/U'] = field('U','[0 1 -1 0 0 0 0]','(0 0 0)',boundaries(
        f'type variableHeightFlowRateInletVelocity; flowRate constant {q}; alpha alpha.water; value uniform (0 0 0);',
        'type pressureInletOutletVelocity; value uniform (0 0 0);','type noSlip;',
        'type pressureInletOutletVelocity; value uniform (0 0 0);'),True)
    files['0/alpha.water'] = field('alpha.water','[0 0 0 0 0 0 0]','0',boundaries(
        'type fixedValue; value uniform 0;',
        'type inletOutlet; inletValue uniform 0; value uniform 0;',
        'type zeroGradient;','type inletOutlet; inletValue uniform 0; value uniform 0;'))
    files['0/p_rgh'] = field('p_rgh','[1 -1 -2 0 0 0 0]','9.81',boundaries(
        'type fixedFluxPressure; value uniform 0;',
        'type fixedValue; value uniform 9.81;',
        'type fixedFluxPressure; value uniform 0;',
        'type prghTotalPressure; p0 uniform 0; value uniform 0;'))
    files.pop('0/p',None)
    # Reuse sediment and turbulence conditions, replacing the solid lid with air.
    for name in ('C','Mbed','k','omega','nut'):
        text = files['0/'+name].replace('lid {','atmosphere {')
        if name == 'C':
            top = 'type inletOutlet; phi phiSed; inletValue uniform 0; value uniform 0;'
        elif name in ('Mbed','nut'):
            top = 'type calculated; value uniform 0;'
        else:
            top = 'type inletOutlet; inletValue uniform 1e-6; value uniform 1e-6;'
        body = value(text,'boundaryField')[1:-1]
        files['0/'+name] = replace_block(text,'boundaryField',replace_block(body,'atmosphere',top))
    files['constant/transportProperties'] = header('transportProperties') + '''
phases (water air);
water { transportModel Newtonian; nu 1e-6; rho 1000; }
air { transportModel Newtonian; nu 1.48e-5; rho 1; }
sigma 0.072;
'''
    files['system/setFieldsDict'] = header('setFieldsDict') + f'''
defaultFieldValues
(
 volScalarFieldValue alpha.water 0
 volVectorFieldValue U (0 0 0)
 volScalarFieldValue p_rgh 9.81
);
regions
(
 boxToCell
 {{
  box (-1 -1 -1) (4 1 {config.depth});
  fieldValues
  (
   volScalarFieldValue alpha.water 1
   volVectorFieldValue U ({config.velocity} 0 0)
   volScalarFieldValue p_rgh {1000*9.81*config.depth+9.81*(1-config.depth)}
  );
 }}
 boxToFace
 {{
  box (-1 -1 -1) (4 1 {config.depth});
  fieldValues
  (
   volScalarFieldValue alpha.water 1
   volScalarFieldValue p_rgh {1000*9.81*config.depth+9.81*(1-config.depth)}
  );
 }}
);
'''
    files['system/controlDict'] = update_values(files['system/controlDict'],
        {'application':solver,'maxAlphaCo':.25,'maxCo':.4,'deltaT':.0001,'maxDeltaT':.001})
    files['system/fvSchemes'] = replace_block(files['system/fvSchemes'],'divSchemes',"""
 default none;
 div(rhoPhi,U) Gauss linearUpwind grad(U);
 div(phi,alpha) Gauss vanLeer;
 div(phirb,alpha) Gauss linear;
 div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;
 div(phiSed,C) Gauss upwind;
 div(phi,k) Gauss upwind;
 div(phi,omega) Gauss upwind;
 """)
    files['system/fvSolution'] = set_solver_blocks(files['system/fvSolution'],{
        '"alpha.water.*"':'{ nAlphaCorr 2; nAlphaSubCycles 1; cAlpha 1; MULESCorr yes; nLimiterIter 5; solver smoothSolver; smoother symGaussSeidel; tolerance 1e-12; relTol 0; }',
        '"pcorr.*"':'{ solver GAMG; smoother DICGaussSeidel; tolerance 1e-8; relTol 0; }',
        'p_rgh':'{ solver GAMG; smoother DICGaussSeidel; tolerance 1e-8; relTol 0.05; }',
        'p_rghFinal':'{ $p_rgh; relTol 0; }'})
    files['system/fvSolution'] = update_nested(files['system/fvSolution'],['PIMPLE'],
        {'nOuterCorrectors':1,'momentumPredictor':'yes'})
    files['Allmesh'] = files['Allmesh'] + 'setFields > log.setFields 2>&1\n'
    files['Allrun'] = files['Allrun'].replace('sedimentPimpleFoam','sedimentInterFoam')
    return solver
