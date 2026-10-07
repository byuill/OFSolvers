/* SPDX-License-Identifier: GPL-3.0-or-later
 * PIMPLE flow adapted from OpenCFD v1912 pimpleFoam (Foundation/OpenCFD).
 * Dilute sand follows native basicKinematicCloud trajectories. The cloud is
 * evolved ONCE after each fluid time step; it never feeds back into momentum.
 */
#include "fvCFD.H"
#include "singlePhaseTransportModel.H"
#include "turbulentTransportModel.H"
#include "pimpleControl.H"
#include "fvOptions.H"
#include "SandKinematicCloud.H"
#include "SandTransectInjection.H"
#include "SandResuspension.H"

// Register our new injector in the existing v1912 kinematic-cloud table.
makeInjectionModelType(SandTransectInjection, basicKinematicCloud);

int main(int argc, char *argv[])
{
    argList::addNote("Fixed-mesh PIMPLE with Lagrangian sand transect feeding");
    #include "setRootCaseLists.H"
    #include "createTime.H"
    #include "createMesh.H"
    pimpleControl pimple(mesh);
    #include "initContinuityErrs.H"
    #include "createFields.H"
    #include "readGravitationalAcceleration.H"
    if (g.dimensions() != dimAcceleration || !(mag(g.value()) > SMALL))
        FatalErrorInFunction << "Nonzero acceleration g is required" << exit(FatalError);

    // Carrier rho/mu are volume fields because the native particle tracker
    // interpolates them at each particle. There is no compressible thermo.
    const dimensionedScalar rhoValue("rho", dimDensity, laminarTransport);
    if (!(rhoValue.value() > 0 && std::isfinite(rhoValue.value())))
        FatalErrorInFunction << "transportProperties rho must be positive" << exit(FatalError);
    volScalarField rho
    (
        IOobject("rho", runTime.timeName(), mesh, IOobject::NO_READ, IOobject::NO_WRITE),
        mesh, rhoValue
    );
    volScalarField mu
    (
        IOobject("mu", runTime.timeName(), mesh, IOobject::NO_READ, IOobject::NO_WRITE),
        rho*laminarTransport.nu()
    );
    SandKinematicCloud sandCloud("sandCloud", rho, U, mu, g);
    if (sandCloud.solution().coupled() || !sandCloud.solution().transient())
        FatalErrorInFunction << "sandCloud requires coupled false; transient yes" << exit(FatalError);
    SandResuspension resuspension(sandCloud,turbulence());
    #include "createTimeControls.H"
    #include "CourantNo.H"
    #include "setInitialDeltaT.H"
    turbulence->validate();

    while (runTime.run())
    {
        #include "readTimeControls.H"
        #include "CourantNo.H"
        #include "setDeltaT.H"
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
        mu = rho*laminarTransport.nu();
        resuspension.beforeEvolve();
        sandCloud.evolve();
        resuspension.afterEvolve();
        // Native cloud statistics include suspended, escaped and stuck mass.
        // Stick leaves inactive parcels in the cloud, preserving their mass.
        scalar mobileMass=0, depositedMass=0;
        forAllConstIters(sandCloud, iter)
        {
            const auto& particle=iter();
            scalar m=particle.nParticle()*particle.mass();
            if (particle.active()) mobileMass+=m;
            else depositedMass+=m;
        }
        reduce(mobileMass, sumOp<scalar>());
        reduce(depositedMass, sumOp<scalar>());
        Info<< "Sand inventory: mobile=" << mobileMass << " deposited="
            << depositedMass << " kg" << endl;
        runTime.write();
        resuspension.write();
        runTime.printExecutionTime(Info);
    }
    Info<< "End\n" << endl;
    return 0;
}
