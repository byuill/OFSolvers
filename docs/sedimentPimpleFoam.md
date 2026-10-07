# sedimentPimpleFoam

User guide · [Getting started, Windows/WSL and ParaView](getting-started.md) ·
[Other solver: Lagrangian sand](sandParcelPimpleFoam.md) · [River GUI setup](gui-user-guide.md) ·
[Repository home](../README.md)

Fixed-mesh, incompressible transient flow with dilute suspended **mass concentration**
`C` [kg/m³] and finite, face-local bed inventory `Mbed` [kg/m²]. Sand does not feed
back into density, momentum, viscosity or turbulence. Geometry and bed elevation
never change. The C++ code separates `createSediment.H` (properties/fields) and
`sedimentEqn.H` (transport/exchange) from the inherited hydrodynamic equations.

## Installation inspected and parent selection

The cloud runtime is Debian's **OpenFOAM.com/OpenCFD v1912**, package
`1912.200626-3+b1`; matching Debian source is `1912.200626-3`, including its GCC
compatibility patches. It is not OpenFOAM Foundation or foam-extend. OFSolvers
originally contained a PyQt GUI, with no C++ solver or OpenFOAM development headers.

The matching source tree is `/workspace/openfoam-source/openfoam-1912.200626`.
Relevant reused interfaces:

| Source in the OpenFOAM tree | Purpose |
| --- | --- |
| `applications/solvers/incompressible/pimpleFoam` | UEqn, pressure correction, PIMPLE and time-step controls |
| `applications/solvers/basic/scalarTransportFoam` | Conservative scalar finite-volume equation pattern |
| `src/TurbulenceModels/incompressible` | `incompressible::turbulenceModel::New`, `nu()`, `nut()`, `devReff()` |
| `src/functionObjects/field/wallShearStress` | Effective-stress wall traction convention |
| `src/finiteVolume/fields/fvPatchFields/basic/mixed` | Native implicit Robin bed condition |
| `src/finiteVolume/fields/fvPatchFields/derived/inletOutlet` | Reversal-sensitive concentration boundaries |

`pimpleFoam` is preferable to `icoFoam` (laminar PISO) or a multiphase/compressible
parent: it already selects laminar/RAS/LES turbulence and performs transient
PIMPLE pressure–velocity coupling. Mesh-motion code was removed, including mesh
updates and moving-face velocity `Uf`. Standard MRF/fvOptions momentum support
remains. Sediment has no arbitrary volume source or sediment fvOptions hook.
Gravity is read from `constant/g`; uniform fluid gravity can be absorbed into the
kinematic pressure (the standard incompressible-parent convention).

The executable is GPL-3.0-or-later, as are its inherited OpenFOAM components.
See the solver directory's COPYING. No cross-fork portability is claimed.

## Build and run in this prepared cloud machine

```bash
cd /workspace/OFSolvers
source scripts/openfoam-env.sh
./Allwmake
./tutorials/sedimentChannel/Allrun
python tests/openfoam/validate.py
```

`Allrun` creates a new case under `/tmp` and prints its path. It never overwrites
existing tutorial files or results. A user-supplied empty destination can be
passed as its first argument. View `C`, `Ds`, `Mbed` and `sedimentFlux` in normal
OpenFOAM post-processing tools. Mbed's **boundary values**, not its zero cell
values, represent the bed. Multiply each boundary value by its face area before
summing bed mass.

For a normal v1912 installation, source its `etc/bashrc` and use `./Allwmake`
instead of the cloud-specific activation script. Compilation requires the
matching source headers, generated `lnInclude` directories, wmake's build tools,
and runtime libraries with DP/32-bit labels. The cloud preparation downloaded
Debian source and build dependencies using APT's signed repository metadata,
ran `wmakeLnIncludeAll`, built `wmake/src`, and linked the source tree's
`platforms/linux64Gcc/lib` to `/workspace/ofsolvers-system/usr/lib`. It reused
existing shared libraries rather than rebuilding OpenFOAM. These source and
runtime directories must be retained together. For another version, adapt and
revalidate the APIs; the build wrapper deliberately rejects untested versions.

## Configure your own case

Run these commands from the repository root after OpenFOAM activation. They copy
the untouched tutorial into a fresh temporary case; choose a persistent location
if you need to keep results beyond the current environment:

```bash
case_dir=$(mktemp -d /tmp/sediment-custom-XXXXXX)
cp -a tutorials/sedimentChannel/. "$case_dir/"
foamDictionary "$case_dir/constant/sedimentProperties" -entry d50 -set 0.00025
foamDictionary "$case_dir/0/C" \
    -entry boundaryField.upstream.inletValue -set 'uniform 0.2'
foamDictionary "$case_dir/0/Mbed" \
    -entry boundaryField.bed.value -set 'uniform 0.02'
foamDictionary "$case_dir/system/controlDict" -entry endTime -set 2
blockMesh -case "$case_dir" > "$case_dir/log.blockMesh" 2>&1
checkMesh -case "$case_dir" > "$case_dir/log.checkMesh" 2>&1
sedimentPimpleFoam -case "$case_dir" > "$case_dir/log.solver" 2>&1
```

This example uses 0.25-mm sediment, 0.2 kg/m³ incoming concentration and
0.02 kg/m² initial bed storage. The incoming concentration is prescribed during
inflow by `inletOutlet`; its boundary value updates during the solve. Other
settings retain the example defaults, including `erosionRate 0`. To permit
erosion, select a positive, calibrated `erosionRate` and appropriate `thetaCrit`
and `erosionExponent` in `constant/sedimentProperties`. Erosion draws from the
local stored bed mass and is capped when that mass is exhausted.

| What to change | File/entry |
| --- | --- |
| Grain size, settling, diffusivity and bed exchange | `constant/sedimentProperties` |
| Initial/incoming sediment concentration | `0/C`, internalField and upstream inletValue |
| Initial bed inventory | `0/Mbed`, selected bed boundary value |
| Fluid viscosity and turbulence model | `constant/transportProperties`, `constant/turbulenceProperties` |
| Flow boundary conditions and initial pressure | `0/U`, `0/p` |
| Mesh shape and resolution | `system/blockMeshDict` |
| Duration, output interval and fluid Courant limit | `system/controlDict` |
| Numerical schemes and solve tolerances | `system/fvSchemes`, `system/fvSolution` |

Use the same patch names in the mesh, all field boundary dictionaries and
`bedPatches`. If selecting RANS/LES/DES, supply the fields and boundary conditions
required by that particular turbulence model. The supplied tutorial is laminar.

The solver prints `Sediment budget: suspended=... bed=... externalOut=... residual=...`.
Masses are kg, externalOut is kg/s, and residual is kg for that step. Check small
budget residuals and nonnegative concentrations/bed inventory. To visualize the
custom case, create `case.foam` inside it and follow the
[ParaView instructions](getting-started.md#view-the-results-in-paraview).

## Continue a saved run

For the custom case above, extend from its latest saved time to 3 seconds:

```bash
foamDictionary "$case_dir/system/controlDict" -entry startFrom -set latestTime
foamDictionary "$case_dir/system/controlDict" -entry endTime -set 3
sedimentPimpleFoam -case "$case_dir" > "$case_dir/log.restart" 2>&1
```

Retain `C`, `Mbed`, `U`, `p` and required turbulence fields at the saved time.
Do not run `blockMesh` again for an unchanged-mesh restart. Initial values in
`0/Mbed` do not replace the saved inventory; missing saved Mbed is an error.

## Run a fresh case on two MPI ranks

This example uses a separate fresh case and a streamwise decomposition:

```bash
parallel_case=$(mktemp -d /tmp/sediment-parallel-XXXXXX)
cp -a tutorials/sedimentChannel/. "$parallel_case/"
cat > "$parallel_case/system/decomposeParDict" <<'EOF'
FoamFile { version 2.0; format ascii; class dictionary; object decomposeParDict; }
numberOfSubdomains 2;
method simple;
simpleCoeffs { n (2 1 1); delta 0.001; }
EOF
blockMesh -case "$parallel_case" > "$parallel_case/log.blockMesh" 2>&1
checkMesh -case "$parallel_case" > "$parallel_case/log.checkMesh" 2>&1
decomposePar -case "$parallel_case" > "$parallel_case/log.decomposePar" 2>&1
mpirun -np 2 sedimentPimpleFoam -case "$parallel_case" -parallel \
    > "$parallel_case/log.parallel" 2>&1
reconstructPar -case "$parallel_case" -latestTime \
    > "$parallel_case/log.reconstructPar" 2>&1
```

Reconstruction makes the latest result available for ordinary serial viewing.
For parallel restart, retain the complete `processor*` directories and use
`startFrom latestTime` with a later endTime. The ordinary C/Mbed fields can also
be decomposed from a complete serial restart; include the bed boundary inventory.

## Transport and units

The equation is

```
dC/dt + div(phiSed*C) - div(Ds*grad(C)) = 0
phiSed = phi + Ws·Sf                 [m³/s]
Ws = ws*g/|g|                       [m/s]
Ds = nu/ScM + nut/ScT                [m²/s]
```

Field dimensions: C `[1 -3 0 0 0 0 0]`, Mbed `[1 -2 0 0 0 0 0]`.
`sedimentFlux` is the numerical **mass rate per face** [kg/s], positive outward
at external boundaries. `phiSed` includes settling on open and coupled faces.
On walls its normal flux is zero; active-bed settling is accounted for once,
by the exchange condition below. Other walls are impermeable to sediment and
must use `zeroGradient C`. Empty patches require `empty` fields.

The tutorial uses Euler time integration, implicit upwind scalar advection,
and corrected diffusion. Euler for `ddt(C)` is enforced because Mbed uses the
same first-order physical time update. C is solved after the flow loop, once
per step (with configurable nonorthogonal iterations), without under-relaxation.
Fluid and sediment Courant limits control adaptive time stepping. With fixed
time steps (`adjustTimeStep no`), the user is responsible for both CFL limits.
Use accurate C solver tolerances; the printed budget residual measures algebraic
and roundoff errors. Negative C/Mbed beyond numerical tolerance causes a fatal
error; there is no mass-destroying concentration clipping. An erosion cap leaves
a machine-scale reservoir reserve to prevent exact-depletion subtraction errors.

`ScM=1` reproduces the requested `Ds=nu+nut/ScT` closure. Molecular sediment
Brownian diffusivity is not physically equal to fluid viscosity; this is an
explicit modeling approximation. ScM allows changing that baseline. A resolved
LES or DES eddy viscosity contributes through the same API, but this is still
an Eulerian gradient-diffusion closure, not an inertial particle or stochastic
particle-dispersion model. LES/DES require appropriate three-dimensional grids,
wall treatment and inflow physics; the 2-D integration cases only test API use.

## Sediment properties (SI scalars)

`constant/sedimentProperties` provides these inputs:

| Entry | Default / meaning |
| --- | --- |
| `d50` | 1.25e-4 m, median noncohesive grain diameter |
| `rhoSediment`, `rhoFluid` | 2650, 1000 kg/m³; require rhoSediment > rhoFluid |
| `settlingNu` | 1e-6 m²/s, viscosity used in the settling correlation |
| `settlingModel` | `FergusonChurch` or `constant` |
| `ws` | Required nonnegative m/s for the constant model; zero disables settling |
| `C1`, `C2` | 18, 1, shape coefficients in Ferguson–Church |
| `ScT`, `ScM` | 0.7, 1; strictly positive |
| `bedPatches` | Required list of exact mesh wall-patch names; multiple supported |
| `thetaCrit` | 0.05, positive critical Shields parameter |
| `erosionRate` | 0 kg/m²/s, entrainment scale; zero disables erosion |
| `erosionExponent` | 1.5, positive excess-Shields exponent |
| `maxSedimentCo` | 0.5, positive settling-plus-advection CFL limit |
| `nSedimentCorrectors` | 2, scalar nonorthogonal iteration count, at least 1 |

Inputs are fixed during a run; restart after changing them. NaN/infinite scalar
parameters, nonpositive densities/diameter/viscosity/Schmidt numbers and zero
or invalid gravity are rejected. The correlation is Ferguson & Church (2004),
*A Simple Universal Equation for Grain Settling Velocity*, Journal of Sedimentary
Research 74, 933–937, doi:10.1306/051204740933:

```
R = rhoSediment/rhoFluid - 1
ws = R*|g|*d50² / (C1*settlingNu + sqrt(0.75*C2*R*|g|*d50³))
```

With the freshwater defaults it gives **0.01106 m/s**. Roughly 0.008–0.015 m/s
is a useful fine-sand order-of-magnitude range, dependent on temperature,
viscosity, grain shape and density. It is not universal. Hindered settling,
flocculation, grain-size mixtures and stratification are outside this model.

## Conservative fixed-bed exchange

Bed patches must have mesh type `wall`, C type `mixed` and Mbed type `fixedValue`.
The initial mixed coefficients in `0/C` are placeholders: the solver replaces
them at each step. No custom library or patch class is required.

For each selected face:

```
tau_b = rhoFluid*|tangential(devReff·normal)|
theta = tau_b / ((rhoSediment-rhoFluid)*|g|*d50)
E_requested = erosionRate*max(theta/thetaCrit - 1, 0)^erosionExponent
E = min(E_requested, (1-1e-12)*Mbed/dt)
wDep = max(Ws·outwardNormal, 0)
q = wDep*Cface - E                  [kg/m²/s, outward from water]
-Ds*dC/dn = q
```

This excess-Shields law is a configurable empirical entrainment closure, not
a calibrated prediction for arbitrary sand beds. `erosionRate=0` is the safe
example default. Select thetaCrit and entrainment coefficients based on relevant
experiments or site conditions. Effective wall shear includes viscous and modeled
turbulent stress; wall-function choices affect it.

The native mixed coefficients impose the implicit Robin relation

```
Cface = (Ds*delta*Ccell + E)/(Ds*delta + wDep)
refValue = 0; refGradient = E/Ds;
valueFraction = wDep/(Ds*delta + wDep)
```

Thus deposition is an implicit concentration sink and erosion a capped flux
source. Mbed increases by `dt*q` using the final scalar matrix's actual face
flux, including nonorthogonal corrections. That identical flux leaves the water
and enters the bed. Fresh deposits become available for erosion next step.
Bed exchange occurs once per physical step, never once per PIMPLE iteration.

Inventory is finite and locally variable. Initialize each face in `0/Mbed` with
a uniform or OpenFOAM nonuniform list. Cell values are unused; other boundary
values are ignored. MUST_READ/AUTO_WRITE provides restart persistence and refuses
to silently replenish a missing restart field. Native decomposition preserves
face association and reconstruction. For parallel restart, decompose the complete
restart time including Mbed (or retain existing processor restart directories).
Mesh topology changes between runs are unsupported.

No maximum storage capacity is imposed: all deposition stays in Mbed. This
preserves mass but does not raise the bed or limit storage by physical bed
thickness. There is no unlimited-erosion mode. `maxBedStorage` and `unlimitedBed`
entries are rejected explicitly rather than silently interpreted. These are
optional extensions, not part of this implementation.

## Open boundaries and restart

- Prescribed inflow: `type fixedValue; value uniform 0.1;` [kg/m³].
- Pure outflow: `type zeroGradient;` has zero normal diffusive flux and lets
  particles advect out; use only when outward **particle** flux is assured.
- Reversal: `type inletOutlet; phi phiSed; inletValue uniform 0.1; value uniform 0.1;`.
  The flux name is essential when gravity has a component normal to the opening.
  It switches using U+Ws, not U alone. Set the external inflow concentration for
  each opening; the tutorial's downstream inflow concentration is zero.
- `advective` is an alternative normal OpenFOAM BC; specify `phi phiSed` and
  validate its far-field/time treatment for the case. It is not used in the
  supplied conservation tutorial. No BC universally eliminates numerical reflection.

Restart: set `startFrom latestTime;`, extend endTime, and keep C, Mbed, U, p and
all turbulence fields in that time directory. Both C and Mbed must be present.
The written Robin coefficients are refreshed by the solver, not trusted as the
next step's erosion demand.

Every step prints suspended mass, selected-bed mass, external outward mass rate,
minimum C, and
`residual = change(suspended+bed) + dt*externalOut`. Coupled processor/cyclic faces
are excluded from the external budget. This is a discrete conservation check,
not evidence that erosion/dispersion closures are physically calibrated.

## Validation performed

The compiled v1912 solver passed **14 integration checks**: open inflow and
settling/deposition; closed-domain mass conservation; variable face inventory
depletion under excessive erosion demand; uninterrupted versus restarted C/Mbed;
kEpsilon, Smagorinsky and SpalartAllmarasDES; downstream sediment outflow;
reversed flow; multiple active wall patches; two-rank MPI/reconstruction versus
serial; corrected diffusion on a sheared nonorthogonal mesh; invalid ScT
rejection; and missing Mbed rejection. The largest observed per-step global
mass residual was **1.49e-15 kg** (rounded upward). The five original Python
repository tests also passed. Allwmake and the documented Allrun commands were
executed successfully. These small cases demonstrate implementation behavior,
not grid convergence or experimental validation of sediment closures.

Final integration console log in this instance:
`/workspace/ofsolvers-validation/integration-final.log`.
Per-case fields/logs: `/tmp/sediment-validation-faa4qzmo`.
The executable is `/workspace/ofsolvers-build/bin/sedimentPimpleFoam`.
Running the validation script creates a fresh artifact directory and reports it.
