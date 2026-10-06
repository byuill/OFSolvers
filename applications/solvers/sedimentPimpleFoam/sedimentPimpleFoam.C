/* SPDX-License-Identifier: GPL-3.0-or-later
 * Fixed-mesh incompressible PIMPLE + dilute suspended sand.
 * Hydrodynamics adapted from OpenCFD's v1912 pimpleFoam (2011-2019,
 * OpenFOAM Foundation/OpenCFD). No sediment feedback enters UEqn or pEqn.
 */
#include <cmath>
#include "fvCFD.H"
#include "singlePhaseTransportModel.H"
#include "turbulentTransportModel.H"
#include "pimpleControl.H"
#include "fvOptions.H"
#include "mixedFvPatchFields.H"
#include "wallPolyPatch.H"

int main(int argc, char *argv[])
{
    argList::addNote("Fixed-mesh incompressible flow and finite-bed suspended sand");
    #include "setRootCaseLists.H"
    #include "createTime.H"
    #include "createMesh.H"
    pimpleControl pimple(mesh);
    #include "initContinuityErrs.H"
    #include "createFields.H"
    #include "createSediment.H"
    #include "createTimeControls.H"
    #include "CourantNo.H"
    #include "setInitialDeltaT.H"
    turbulence->validate();

    while (runTime.run())
    {
        #include "readTimeControls.H"
        #include "CourantNo.H"
        #include "setDeltaT.H"
        // Fluid CFL does not include settling. Limit dt independently using
        // the sum of absolute sediment face fluxes (volume/time) per cell.
        // Coupled/cyclic faces retain their flux; impermeable walls do not.
        phiSed = phi + (Ws & mesh.Sf());
        forAll(mesh.boundary(), patchi)
        {
            if (isA<wallPolyPatch>(mesh.boundaryMesh()[patchi]))
                phiSed.boundaryFieldRef()[patchi] = 0;
        }
        scalar sedimentRate = 0.5*gMax
        (
            fvc::surfaceSum(mag(phiSed))().primitiveField()/mesh.V()
        );
        if (adjustTimeStep && sedimentRate > SMALL)
            runTime.setDeltaT(min(runTime.deltaTValue(), maxSedimentCo/sedimentRate));
        ++runTime;
        Info<< "Time = " << runTime.timeName() << nl << endl;
        while (pimple.loop())
        {
            #include "UEqn.H"
            while (pimple.correct())
            {
                #include "pEqn.H"
            }
            if (pimple.turbCorr())
            {
                laminarTransport.correct();
                turbulence->correct();
            }
        }
        // Solve ONCE per physical step, after flow converges. Updating Mbed
        // inside outer PIMPLE iterations would count bed exchange repeatedly.
        #include "sedimentEqn.H"
        runTime.write();
        runTime.printExecutionTime(Info);
    }
    Info<< "End\n" << endl;
    return 0;
}
