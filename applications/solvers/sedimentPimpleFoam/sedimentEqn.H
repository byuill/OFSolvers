const scalar dt = runTime.deltaTValue();
scalar oldSuspended = fvc::domainIntegrate(C).value();
scalar oldBed = 0;
forAll(bedIDs, i)
    oldBed += gSum(Mbed.boundaryField()[bedIDs[i]]*mesh.magSf().boundaryField()[bedIDs[i]]);

// The same eddy viscosity interface is used for laminar (nut=0), RAS, LES
// and registered DES models. This gradient-diffusion closure is NOT a
// stochastic particle model or a resolved inertial-particle dispersion model.
Ds = turbulence->nu()/ScM + turbulence->nut()/ScT;
forAll(Ds.primitiveField(), celli)
    if (!std::isfinite(Ds[celli]))
        FatalErrorInFunction << "Nonfinite sediment diffusivity" << exit(FatalError);
forAll(Ds.boundaryField(), patchi)
    forAll(Ds.boundaryField()[patchi], facei)
        if (!(Ds.boundaryField()[patchi][facei] > 0)
         || !std::isfinite(Ds.boundaryField()[patchi][facei]))
            FatalErrorInFunction << "Invalid boundary sediment diffusivity" << exit(FatalError);
if (gMin(Ds.primitiveField()) <= 0)
    FatalErrorInFunction << "Sediment diffusivity must be positive" << exit(FatalError);
phiSed = phi + (Ws & mesh.Sf());
forAll(mesh.boundary(), patchi)
{
    if (isA<wallPolyPatch>(mesh.boundaryMesh()[patchi]))
    {
        // Wall settling is handled once, by the Robin diffusive flux below.
        // Inactive walls are impermeable and must use zeroGradient C.
        phiSed.boundaryFieldRef()[patchi] = 0;
        bool active = false;
        forAll(bedIDs, i) active = active || bedIDs[i] == patchi;
        if (!active && C.boundaryField()[patchi].type() != "zeroGradient")
            FatalErrorInFunction << "Inactive walls require zeroGradient C" << exit(FatalError);
    }
}
// Kinematic stress includes viscous and modeled turbulent stress. Remove
// normal traction; only the tangential component drives bed entrainment.
const volSymmTensorField stress(turbulence->devReff());
forAll(bedIDs, i)
{
    label patchi = bedIDs[i];
    const fvPatch& patch = mesh.boundary()[patchi];
    vectorField n(patch.nf());
    vectorField traction(n & stress.boundaryField()[patchi]);
    scalarField tau(rhoW*mag(traction - n*(n & traction)));
    scalarField theta(tau/((rhoS-rhoW)*mag(g.value())*d50));
    scalarField E(erosionRate*pow(max(theta/thetaCrit-scalar(1), scalar(0)), erosionExponent));
    // E: kg/(m2 s). Even a huge requested erosion rate cannot overdraw
    // the per-face reservoir. Fresh deposits become available NEXT step.
    // Leave only a machine-scale reserve to avoid negative inventory from
    // floating-point subtraction at exact depletion; no field clipping.
    E = min(E, (1-scalar(1e-12))*Mbed.boundaryField()[patchi]/dt);
    scalarField wDep(max(Ws.value() & n, scalar(0)));
    const scalarField& diffusivity = Ds.boundaryField()[patchi];
    mixedFvPatchScalarField& bedC = refCast<mixedFvPatchScalarField>(C.boundaryFieldRef()[patchi]);
    // Desired OUTWARD sediment flux q = wDep*Cface - E [kg/m2/s].
    // phiSed=0 here, so q=-Ds*dC/dn. Mixed BC evaluates
    // Cface=(Ds*delta*Ccell+E)/(Ds*delta+wDep), a positive implicit sink.
    // This is a genuine boundary flux, not a near-bed volume source.
    bedC.refValue() = 0;
    bedC.refGrad() = E/diffusivity;
    bedC.valueFraction() = wDep/(diffusivity*patch.deltaCoeffs()+wDep);
}
C.correctBoundaryConditions();
// Flux from the LAST assembled equation, including any explicit nonorthogonal
// correction, is authoritative. Reconstructing flux after solve can introduce
// a mismatch with the linear system's conservation on skewed meshes.
surfaceScalarField sedimentFlux
(
    IOobject("sedimentFlux", runTime.timeName(), mesh, IOobject::NO_READ, IOobject::AUTO_WRITE),
    mesh, dimensionedScalar("zero", dimMass/dimTime, 0)
);
for (label corr=0; corr<nSedimentCorrectors; ++corr)
{
    fvScalarMatrix CEqn
    (
        fvm::ddt(C) + fvm::div(phiSed, C) - fvm::laplacian(Ds, C)
    );
    // No relaxation: bed accounting must match the transient equation.
    CEqn.solve();
    if (corr == nSedimentCorrectors-1) sedimentFlux = CEqn.flux();
}
forAll(C.primitiveField(), celli)
    if (!std::isfinite(C[celli]))
        FatalErrorInFunction << "Nonfinite C: unstable scalar transport" << exit(FatalError);
if (gMin(C.primitiveField()) < -1e-10)
    FatalErrorInFunction << "Negative C: use upwind advection and reduce dt. "
        << "No mass-destroying clipping is performed" << exit(FatalError);
scalar newBed = 0;
forAll(bedIDs, i)
{
    label patchi = bedIDs[i];
    scalarField& inventory = Mbed.boundaryFieldRef()[patchi];
    inventory += dt*sedimentFlux.boundaryField()[patchi]/mesh.magSf().boundaryField()[patchi];
    forAll(inventory, facei)
        if (!std::isfinite(inventory[facei]))
            FatalErrorInFunction << "Nonfinite Mbed" << exit(FatalError);
    if (gMin(inventory) < 0)
        FatalErrorInFunction << "Negative Mbed: bed flux or discretization inconsistent" << exit(FatalError);
    newBed += gSum(inventory*mesh.magSf().boundaryField()[patchi]);
}
scalar openFlux = 0;
forAll(mesh.boundary(), patchi)
{
    bool bed = false;
    forAll(bedIDs, i) bed = bed || bedIDs[i] == patchi;
    // Exclude processor/cyclic interfaces from the global external budget.
    if (!bed && !mesh.boundary()[patchi].coupled())
        openFlux += gSum(sedimentFlux.boundaryField()[patchi]);
}
scalar newSuspended = fvc::domainIntegrate(C).value();
Info<< "Sediment budget: suspended=" << newSuspended << " bed=" << newBed
    << " externalOut=" << openFlux << " residual="
    << (newSuspended+newBed-oldSuspended-oldBed+dt*openFlux)
    << " kg; min(C)=" << gMin(C.primitiveField()) << endl;
