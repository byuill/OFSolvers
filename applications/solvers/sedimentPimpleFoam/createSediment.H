// All model inputs are immutable during a run. Re-read them on restart.
IOdictionary sedimentProperties
(
    IOobject("sedimentProperties", runTime.constant(), mesh,
             IOobject::MUST_READ, IOobject::NO_WRITE)
);
// Gravity follows standard OpenFOAM uniformDimensionedVectorField I/O.
uniformDimensionedVectorField g
(
    IOobject("g", runTime.constant(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE)
);
if (g.dimensions() != dimAcceleration || !(mag(g.value()) > SMALL)
 || !std::isfinite(mag(g.value())))
    FatalErrorInFunction << "g must be a nonzero acceleration vector" << exit(FatalError);

// Dictionary scalars below are SI. C and all persistent fields have native
// dimension sets, so OpenFOAM checks the actual discretized equation units.
const scalar d50 = sedimentProperties.lookupOrDefault<scalar>("d50", 1.25e-4);
const scalar rhoS = sedimentProperties.lookupOrDefault<scalar>("rhoSediment", 2650);
const scalar rhoW = sedimentProperties.lookupOrDefault<scalar>("rhoFluid", 1000);
const scalar settlingNu = sedimentProperties.lookupOrDefault<scalar>("settlingNu", 1e-6);
const scalar ScT = sedimentProperties.lookupOrDefault<scalar>("ScT", 0.7);
const scalar ScM = sedimentProperties.lookupOrDefault<scalar>("ScM", 1.0);
const scalar thetaCrit = sedimentProperties.lookupOrDefault<scalar>("thetaCrit", 0.05);
const scalar erosionRate = sedimentProperties.lookupOrDefault<scalar>("erosionRate", 0);
const scalar erosionExponent = sedimentProperties.lookupOrDefault<scalar>("erosionExponent", 1.5);
const scalar maxSedimentCo = sedimentProperties.lookupOrDefault<scalar>("maxSedimentCo", 0.5);
const label nSedimentCorrectors = sedimentProperties.lookupOrDefault<label>("nSedimentCorrectors", 2);
if (!(d50 > 0 && rhoW > 0 && rhoS > rhoW && settlingNu > 0
   && ScT > 0 && ScM > 0 && thetaCrit > 0 && erosionRate >= 0
   && erosionExponent > 0 && maxSedimentCo > 0 && nSedimentCorrectors >= 1))
    FatalErrorInFunction << "Invalid sedimentProperties: require positive diameter, "
        << "densities (rhoS>rhoW), viscosity, Schmidt numbers, thetaCrit, exponent, "
        << "CFL and corrector count; erosionRate >= 0" << exit(FatalError);

// Comparisons alone reject NaN but not +infinity. Validate finite inputs too.
const scalar inputs[] = {d50, rhoS, rhoW, settlingNu, ScT, ScM, thetaCrit,
                         erosionRate, erosionExponent, maxSedimentCo};
for (scalar input : inputs)
    if (!std::isfinite(input))
        FatalErrorInFunction << "Sediment inputs must be finite" << exit(FatalError);
if (sedimentProperties.found("maxBedStorage") || sedimentProperties.found("unlimitedBed"))
    FatalErrorInFunction << "This solver implements finite inventory without a storage cap. "
        << "maxBedStorage/unlimitedBed are unsupported" << exit(FatalError);

const word settlingModel = sedimentProperties.lookupOrDefault<word>("settlingModel", "FergusonChurch");
scalar ws = 0;
if (settlingModel == "constant")
    ws = readScalar(sedimentProperties.lookup("ws"));
else if (settlingModel == "FergusonChurch")
{
    // Ferguson & Church (2004), Eq. 4. R = submerged relative density.
    // C1=18, C2=1 suit natural sand; configurable grain shape coefficients.
    scalar C1 = sedimentProperties.lookupOrDefault<scalar>("C1", 18);
    scalar C2 = sedimentProperties.lookupOrDefault<scalar>("C2", 1);
    if (!(C1 > 0 && C2 > 0 && std::isfinite(C1) && std::isfinite(C2)))
        FatalErrorInFunction << "C1 and C2 must be positive" << exit(FatalError);
    scalar Rg = (rhoS/rhoW-1)*mag(g.value());
    ws = Rg*sqr(d50)/(C1*settlingNu + Foam::sqrt(0.75*C2*Rg*pow3(d50)));
}
else
    FatalErrorInFunction << "settlingModel must be constant or FergusonChurch" << exit(FatalError);
if (!(ws >= 0 && std::isfinite(ws)))
    FatalErrorInFunction << "ws must be finite and nonnegative" << exit(FatalError);
const dimensionedVector Ws("Ws", dimVelocity, ws*g.value()/mag(g.value()));
Info<< "Settling model " << settlingModel << ", ws=" << ws << " m/s" << endl;

volScalarField C
(
    IOobject("C", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::AUTO_WRITE), mesh
);
// An ordinary volScalarField is understood by decomposePar/reconstructPar.
// Its cell values are unused (zero). Only selected bed boundary values store
// inventory. MUST_READ prevents silently replenishing a bed on restart.
volScalarField Mbed
(
    IOobject("Mbed", runTime.timeName(), mesh, IOobject::MUST_READ, IOobject::AUTO_WRITE), mesh
);
if (C.dimensions() != dimMass/dimVolume || Mbed.dimensions() != dimMass/dimArea)
    FatalErrorInFunction << "C needs kg/m3 dimensions; Mbed needs kg/m2" << exit(FatalError);
forAll(C.primitiveField(), celli)
    if (!std::isfinite(C[celli]))
        FatalErrorInFunction << "Initial C must be finite" << exit(FatalError);
forAll(C.boundaryField(), patchi)
    forAll(C.boundaryField()[patchi], facei)
        if (!std::isfinite(C.boundaryField()[patchi][facei])
         || C.boundaryField()[patchi][facei] < 0)
            FatalErrorInFunction << "Initial boundary C must be finite and nonnegative" << exit(FatalError);
if (gMin(C.primitiveField()) < 0)
    FatalErrorInFunction << "Initial C cannot be negative" << exit(FatalError);

// Euler in time is required: inventory uses exactly the same first-order
// time integration as suspension. Multistep schemes require bed history too.
if (word(mesh.ddtScheme("ddt(C)")) != "Euler")
    FatalErrorInFunction << "Use ddtSchemes { default Euler; } for conservative bed exchange" << exit(FatalError);
mesh.setFluxRequired("C");
wordList bedNames(sedimentProperties.lookup("bedPatches"));
labelList bedIDs(bedNames.size());
forAll(bedNames, i)
{
    label patchi = mesh.boundaryMesh().findPatchID(bedNames[i]);
    if (patchi < 0)
        FatalErrorInFunction << "Unknown bed patch " << bedNames[i] << exit(FatalError);
    for (label j=0; j<i; ++j)
        if (bedIDs[j] == patchi)
            FatalErrorInFunction << "Duplicate bed patch " << bedNames[i] << exit(FatalError);
    if (!isA<wallPolyPatch>(mesh.boundaryMesh()[patchi])
     || !isA<mixedFvPatchScalarField>(C.boundaryField()[patchi])
     || Mbed.boundaryField()[patchi].type() != "fixedValue")
        FatalErrorInFunction << "Bed " << bedNames[i]
            << " requires wall mesh type, mixed C and fixedValue Mbed" << exit(FatalError);
    forAll(Mbed.boundaryField()[patchi], facei)
        if (!std::isfinite(Mbed.boundaryField()[patchi][facei]))
            FatalErrorInFunction << "Bed inventory must be finite" << exit(FatalError);
    if (gMin(Mbed.boundaryField()[patchi]) < 0)
        FatalErrorInFunction << "Negative bed inventory" << exit(FatalError);
    // Non-bed Mbed patch values are ignored, including processor patches.
    bedIDs[i] = patchi;
}
volScalarField Ds
(
    IOobject("Ds", runTime.timeName(), mesh, IOobject::NO_READ, IOobject::AUTO_WRITE),
    turbulence->nu()/ScM + turbulence->nut()/ScT
);
surfaceScalarField phiSed
(
    IOobject("phiSed", runTime.timeName(), mesh, IOobject::NO_READ, IOobject::AUTO_WRITE),
    phi + (Ws & mesh.Sf())
);
