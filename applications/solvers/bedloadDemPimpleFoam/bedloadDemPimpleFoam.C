/* SPDX-License-Identifier: GPL-3.0-or-later
 * Volume-averaged PIMPLE + LAMMPS GRANULAR, OpenCFD v1912.
 * PIMPLE pressure projection follows OpenCFD/Foundation pimpleFoam (GPLv3+).
 * Partitioned first-order two-way coupling; no weighted representative parcels.
 */
#include "fvCFD.H"
#include "singlePhaseTransportModel.H"
#include "pimpleControl.H"
#include "DemTurbulence.H"
#include "DemCloud.H"
#include "DemOutput.H"
using namespace Foam;
int main(int argc,char* argv[])
{
    argList::addNote("Volume-averaged turbulent flow and LAMMPS granular bedload; static periodic/walled box");
    #include "setRootCaseLists.H"
    #include "createTime.H"
    #include "createMesh.H"
    requireDem(mesh.nGeometricD()==3,"CFD--DEM needs a three-dimensional static mesh");
    pimpleControl pimple(mesh);
    volScalarField p(IOobject("p",runTime.timeName(),mesh,IOobject::MUST_READ,IOobject::AUTO_WRITE),mesh);
    volVectorField U(IOobject("U",runTime.timeName(),mesh,IOobject::MUST_READ,IOobject::AUTO_WRITE),mesh);
    volScalarField porosity(IOobject("porosity",runTime.timeName(),mesh,IOobject::MUST_READ,IOobject::AUTO_WRITE),mesh);
    requireDem(U.dimensions()==dimVelocity&&p.dimensions()==sqr(dimVelocity)&&porosity.dimensions()==dimless,
        "U, p (kinematic dynamic pressure), porosity have incorrect dimensions");
    requireDem(word(mesh.ddtScheme("ddt(porosity,U)"))=="Euler","CFD--DEM impulse coupling requires Euler time integration");
    #include "readGravitationalAcceleration.H"
    IOdictionary cfg(IOobject("demCouplingProperties",runTime.constant(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE));
    IOdictionary transport(IOobject("transportProperties",runTime.constant(),mesh,IOobject::MUST_READ,IOobject::NO_WRITE));
    requireDem(transport.get<word>("transportModel")=="Newtonian","The initial CFD--DEM model requires a Newtonian fluid");
    const dimensionedScalar rho("rho",dimDensity,transport),nu("nu",dimViscosity,transport);
    requireDem(std::isfinite(rho.value())&&rho.value()>0&&std::isfinite(nu.value())&&nu.value()>0
        &&g.dimensions()==dimAcceleration&&std::isfinite(magSqr(g.value())),"Invalid fluid properties or gravity");
    vector drive=cfg.lookupOrDefault<vector>("drivingAcceleration",Zero);
    requireDem(std::isfinite(magSqr(drive)),"drivingAcceleration must be finite");
    DemCloud cloud(mesh,cfg,rho.value(),nu.value(),g.value());
    scalarField savedPorosity(porosity.primitiveField());cloud.porosity(porosity);
    if(runTime.value()>SMALL)
        requireDem(gMax(mag(porosity.primitiveField()-savedPorosity))<1e-10,"Saved CFD porosity and DEM checkpoint disagree");
    porosity.oldTime();
    if(runTime.value()<=SMALL)porosity.write();
    surfaceScalarField phiU(IOobject("phiU",runTime.timeName(),mesh,IOobject::NO_READ,IOobject::NO_WRITE),fvc::flux(U));
    surfaceScalarField phi(IOobject("phi",runTime.timeName(),mesh,IOobject::READ_IF_PRESENT,IOobject::AUTO_WRITE),fvc::interpolate(porosity)*phiU);
    phiU=phi/fvc::interpolate(porosity);
    singlePhaseTransportModel laminarTransport(U,phiU);
    autoPtr<DemTurbulence> turbulence(DemTurbulence::New(porosity,U,phi,phiU,laminarTransport));
    turbulence->validate();
    volVectorField reaction(IOobject("demReaction",runTime.timeName(),mesh,IOobject::NO_READ,IOobject::AUTO_WRITE),
        mesh,dimensionedVector("zero",dimAcceleration,Zero),zeroGradientFvPatchVectorField::typeName);
    label pRefCell=0;scalar pRefValue=0;setRefCell(p,pimple.dict(),pRefCell,pRefValue);mesh.setFluxRequired(p.name());
    #include "createTimeControls.H"
    scalar cumulative=0,lastContinuity=0,lastOutput=runTime.value();
    cloud.write(0);
    while(runTime.run())
    {
        #include "readTimeControls.H"
        scalar couplingLimit=cloud.prepare(U,p,porosity);
        scalar flowRate=.5*gMax(fvc::surfaceSum(mag(phi))().primitiveField()/(porosity.primitiveField()*mesh.V()));
        if(adjustTimeStep)
            runTime.setDeltaT(min(min(1.2*runTime.deltaTValue(),maxDeltaT),min(maxCo/max(flowRate,scalar(1e-30)),couplingLimit)));
        else requireDem(runTime.deltaTValue()<=couplingLimit*(1+1e-10),"CFD deltaT exceeds the drag/displacement coupling limit; enable adjustTimeStep or reduce deltaT");
        requireDem(runTime.deltaTValue()>VSMALL&&std::isfinite(runTime.deltaTValue()),"Invalid CFD timestep");
        ++runTime;Info<<"Time = "<<runTime.timeName()<<nl;
        cloud.advance(runTime.deltaTValue(),reaction,porosity);
        while(pimple.loop())
        {
            fvVectorMatrix UEqn(fvm::ddt(porosity,U)+fvm::div(phi,U)+turbulence->divDevReff(U)
                ==reaction+porosity*dimensionedVector("drive",dimAcceleration,drive));
            UEqn.relax();
            if(pimple.momentumPredictor())solve(UEqn==-porosity*fvc::grad(p));
            while(pimple.correct())
            {
            volScalarField rAU(1/UEqn.A()),rAUEps(rAU*porosity);
            volVectorField HbyA(constrainHbyA(rAU*UEqn.H(),U,p));
            surfaceScalarField epsf(fvc::interpolate(porosity));
            surfaceScalarField coefficient(epsf*fvc::interpolate(rAUEps));
            surfaceScalarField predicted(epsf*fvc::flux(HbyA)+epsf*fvc::interpolate(rAU)*fvc::ddtCorr(porosity,U,phi));
            constrainPressure(p,U,predicted,coefficient);
                while(pimple.correctNonOrthogonal())
                {
                    fvScalarMatrix pEqn(fvm::laplacian(coefficient,p)==fvc::div(predicted)+fvc::ddt(porosity));
                    pEqn.setReference(pRefCell,pRefValue);
                    pEqn.solve(mesh.solver(p.select(pimple.finalInnerIter())));
                    if(pimple.finalNonOrthogonalIter())phi=predicted-pEqn.flux();
                }
                U=HbyA-rAUEps*fvc::grad(p);U.correctBoundaryConditions();
            }
            phiU=phi/fvc::interpolate(porosity);
            if(pimple.turbCorr()){laminarTransport.correct();turbulence->correct();}
        }
        volScalarField continuity(fvc::ddt(porosity)+fvc::div(phi));
        scalar l1=fvc::domainIntegrate(mag(continuity)).value()/gSum(mesh.V());
        lastContinuity=l1;
        cumulative+=runTime.deltaTValue()*fvc::domainIntegrate(continuity).value();
        Info<<"CFD--DEM continuity: L1="<<l1<<" /s cumulative="<<cumulative<<" m3"<<nl;
        runTime.write();if(runTime.writeTime()){cloud.write(l1);lastOutput=runTime.value();}
        runTime.printExecutionTime(Info);
    }
    // Keep the actual final fluid and contact state even when the end of the
    // run falls between scheduled output times.
    if(lastOutput!=runTime.value()){runTime.writeNow();cloud.write(lastContinuity);}
    cloud.finish();Info<<"End"<<nl;return 0;
}
