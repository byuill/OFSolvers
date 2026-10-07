# sandParcelPimpleFoam

User guide · [Getting started, Windows/WSL and ParaView](getting-started.md) ·
[Other solver: concentration and bed inventory](sedimentPimpleFoam.md) ·
[Repository home](../README.md)

A separate **Lagrangian** sand solver for the inspected OpenFOAM.com/OpenCFD
**v1912** installation. Incompressible flow uses fixed-mesh PIMPLE. Sand travels
as native `basicKinematicParcel` parcels, with inertia, sphere drag and
buoyancy-corrected gravity. The earlier Eulerian `sedimentPimpleFoam` remains
available. No suspended-concentration PDE is solved by this new solver.

The default diameter is 0.125 mm and the tutorial particle density is 2650 kg/m³.
Both are configurable. A computational **parcel** represents `nParticle` physical
spheres; its mass is `nParticle*rhoParticle*pi*d³/6`. Increasing the parcel rate
improves sampling without increasing the physical sediment feed rate. Statistical
weights below one particle are permitted rather than silently losing small feeds.

## Build and run

In this prepared cloud machine:

```bash
cd /workspace/OFSolvers
source scripts/openfoam-env.sh
./Allwmake
./tutorials/sandParcelChannel/Allrun
python tests/openfoam/validate_parcels.py
python tests/openfoam/validate_resuspension.py
```

`Allwmake` builds both sediment solvers. `Allrun` creates a fresh copy of the 3-D
tutorial under `/tmp`, leaving the original unchanged. To choose the results
location, pass a new or empty directory to Allrun. On a normal v1912 installation,
source its `etc/bashrc` instead of the cloud-specific script. On Windows, run
OpenFOAM in an appropriate Linux/WSL installation; these are Linux C++ solvers,
not Windows GUI executables. Version-specific APIs have not been validated on
Foundation OpenFOAM, foam-extend or other OpenCFD releases.

## Configure your own case

Run from the repository root after OpenFOAM activation. Copy the supplied tutorial
before editing it:

```bash
case_dir=$(mktemp -d /tmp/sand-parcel-custom-XXXXXX)
cp -a tutorials/sandParcelChannel/. "$case_dir/"
foamDictionary "$case_dir/constant/sandCloudProperties" \
    -entry subModels.injectionModels.feed.massFeedRate -set 0.0002
foamDictionary "$case_dir/constant/sandCloudProperties" \
    -entry subModels.injectionModels.feed.width -set 0.02
foamDictionary "$case_dir/constant/sandCloudProperties" \
    -entry subModels.injectionModels.feed.profile -set uniform
blockMesh -case "$case_dir" > "$case_dir/log.blockMesh" 2>&1
checkMesh -case "$case_dir" > "$case_dir/log.checkMesh" 2>&1
sandParcelPimpleFoam -case "$case_dir" > "$case_dir/log.solver" 2>&1
```

This uses a 0.0002 kg/s feed across a 0.02-m-wide strip for the original
one-second injection, with uniformly random locations. The total prescribed
feed is **0.0002 kg**, independent of strip width and computational parcel rate.
Choose a persistent location instead of `/tmp` when retaining results.

| What to change | File/entry |
| --- | --- |
| Feed rate, start, duration, size and strip geometry | `constant/sandCloudProperties`, subModels/injectionModels/feed |
| Uniform/Rouse sampling and shear estimate | Same feed dictionary, profile and shearVelocityModel |
| Grain density | `constant/sandCloudProperties`, constantProperties/rho0 |
| Stick/rebound/escape wall behavior | Same file, subModels/localInteractionCoeffs |
| Optional bed resuspension | Same file, top-level resuspension dictionary |
| Fluid density and viscosity | `constant/transportProperties`, rho and nu |
| Flow boundaries, turbulence and mesh | `0/U`, `0/p`, `constant/turbulenceProperties`, `system/blockMeshDict` |
| Duration, output interval and time-step controls | `system/controlDict` |

Use patch names that match the mesh and keep the entire feed rectangle on the
inlet. Changing injection `duration` changes feed mass; changing only `endTime`
controls how long transport continues. Increase `parcelsPerSecond` for finer
sampling. To use a Rouse feed, retain or restore `profile Rouse` and configure
the local or prescribed shear estimate as described below.

For mobile/deposited mass, read `Sand inventory` in `log.solver`. Include native
cumulative escaped mass when checking the injected-mass balance. To view parcels,
create `case.foam` inside the result directory and enable the reader's `sandCloud`
region using the [ParaView instructions](getting-started.md#view-the-results-in-paraview).

## Continue a saved run

For the one-second custom case above, continue transport to 2 seconds:

```bash
foamDictionary "$case_dir/system/controlDict" -entry startFrom -set latestTime
foamDictionary "$case_dir/system/controlDict" -entry endTime -set 2
sandParcelPimpleFoam -case "$case_dir" > "$case_dir/log.restart" 2>&1
```

The original one-second feed is already finished; this continues the existing
parcels without extending injection. Keep the feed schedule and geometry unchanged
and retain the complete saved cloud and uniform metadata. When resuspension is
enabled, retain `sandResuspensionState` as well. See the
[resuspension section](#optional-bed-resuspension) for first enabling that process
on older results. Do not rebuild an unchanged mesh during restart.

## Run a fresh case on two MPI ranks

```bash
parallel_case=$(mktemp -d /tmp/sand-parcel-parallel-XXXXXX)
cp -a tutorials/sandParcelChannel/. "$parallel_case/"
cat > "$parallel_case/system/decomposeParDict" <<'EOF'
FoamFile { version 2.0; format ascii; class dictionary; object decomposeParDict; }
numberOfSubdomains 2;
method simple;
simpleCoeffs { n (2 1 1); delta 0.001; }
EOF
blockMesh -case "$parallel_case" > "$parallel_case/log.blockMesh" 2>&1
checkMesh -case "$parallel_case" > "$parallel_case/log.checkMesh" 2>&1
decomposePar -case "$parallel_case" > "$parallel_case/log.decomposePar" 2>&1
mpirun -np 2 sandParcelPimpleFoam -case "$parallel_case" -parallel \
    > "$parallel_case/log.parallel" 2>&1
reconstructPar -case "$parallel_case" -latestTime \
    > "$parallel_case/log.reconstructPar" 2>&1
```

The feed rate is global across ranks. Parcels and their optional bed history
transfer when crossing processor boundaries. Reconstruction supports viewing
the latest cloud, but **parallel resuspension restart must use the original complete
processor time directories with the same mesh and decomposition**. Its per-rank
history is not remapped by `reconstructPar` or `decomposePar`. Set startFrom/endTime
in the case controlDict and rerun `mpirun` with the same rank count to continue.
Changing decomposition changes the inlet sampling realization.

## Feed definition

Edit `constant/sandCloudProperties`, under `subModels/injectionModels/feed`:

```foam
feed
{
    type sandTransectInjection;
    massFeedRate 0.0001;        // kg/s, total over this transect
    parcelsPerSecond 1000;     // computational parcels/s, independent of kg/s
    SOI 0;                    // start of injection, seconds
    duration 1;               // injection duration, seconds
    d50 1.25e-4;              // monodisperse sand diameter, metres

    patch upstream;
    origin (0 0.05 0);        // point on inlet at bed elevation, width midpoint
    widthDirection (0 1 0);   // horizontal vector in the inlet plane
    width 0.04;               // full width, metres
    depth 0.1;                // H, bed-to-surface depth, metres
    referenceHeight 0.005;    // a: lower sampling cutoff, metres above origin
    maximumHeight 0.095;      // upper cutoff, strictly below H
    inwardOffset 1e-7;        // tiny offset into the first inlet cell

    profile Rouse;            // uniform or Rouse
    profileBins 256;          // vertical quadrature/sampling resolution
    shearVelocityModel localBed;
    bedPatch bed;
    shearSampleLength 0.2;    // downstream extent of bed sampling region
    kappa 0.41;
    beta 1;
    fluxWeighted true;
    initialVelocity equilibrium; // carrier or equilibrium
    seed 12345;
    writeSamples false;       // true prints every injected position
}
```

The transect is a **rectangular area**, not a zero-area line: horizontal offsets
range from `-width/2` to `+width/2`, and heights from `referenceHeight` to
`maximumHeight`. Its entire area must lie on the selected planar, vertical inlet
patch. Upward is `-g/|g|`; `widthDirection` must be horizontal and tangent to the
inlet plane. The mesh must be 3-D with planar convex inlet faces. A moving free
surface, nonplanar inlet, incomplete rectangle or moving mesh is outside this
implementation. `inwardOffset` must be less than one tenth of the face-to-owner-
cell normal distance. It is only a geometric tracking tolerance, not a physical
injection distance.

Multiple separately named injectors may be placed in `injectionModels`. Give
each its own geometry, massFeedRate and seed. Each rate is a **total kg/s**, not
kg/s per metre or a water discharge. Native `massTotal`, `parcelBasisType` and
`minParticlesPerParcel` are generated internally from massFeedRate/duration and
overridden if supplied, avoiding redundant settings that could disagree.

## Uniform random and Rouse sampling

`profile uniform` samples positions randomly with uniform area density inside
the transect. “Uniform” describes the probability distribution; it does not
place particles on a regular lattice. Only inward-flowing inlet faces receive
particles. On a normal fully incoming inlet, the horizontal and vertical
marginals are uniform. `seed` makes sampling reproducible.

`profile Rouse` calculates the relative concentration

```
C(z)/C(a) = [ a*(H-z) / (z*(H-a)) ]^P
P = ws / (kappa * beta * uStar)
```

`beta` is the sediment-to-momentum turbulent diffusivity ratio. Positive lower
and upper cutoffs avoid the singular bed endpoint and vanishing concentration
at the surface. The distribution is normalized numerically; C(a) cancels because
the total physical feed is imposed independently by massFeedRate.

Rouse concentration is not itself a crossing-rate probability. With the default
`fluxWeighted true`, the sampling weight is `C(z)*max(U_initial·inwardNormal,0)`.
This gives the prescribed vertical concentration profile for the **flux entering**
through a nonuniform-velocity boundary. Set `fluxWeighted false` to sample the
concentration shape directly, regardless of velocity magnitude. Uniform sampling
always uses area weights, with the inward-flow filter. A transect with no inward
flow is rejected rather than injecting into reversed/outgoing flow.

Inlet face triangles are clipped to the exact width and subdivided into vertical
strips. Rouse concentration is evaluated at each clipped triangle's centroid;
triangles are selected using cumulative area/concentration/velocity weights, then
sampled uniformly by barycentric coordinates. Thus the Rouse distribution is a
**quadrature approximation**, not an exact analytic inverse CDF. Increase
`profileBins` (8–4096) and check sampling convergence for steep profiles or a very
small referenceHeight. Carrier velocity is taken from each inlet face's boundary
value. Horizontal variation follows the face velocities when flux weighting is
on. A single near-inlet uStar is calculated per transect per injection batch;
it is not a separate shear estimate for every sampled height or width position.

A counter-based SplitMix64 sampler uses the injector seed and persisted global
parcel count. This sampling sequence survives restart independently of native
cloud random draws. Changing MPI decomposition changes the ordering of geometry
and therefore the realization, while retaining the requested distribution and
**global** feed mass. Keep the same decomposition for reproducible restarts.

## Settling and local shear

By default, ws is obtained by numerically balancing submerged weight against
**the same sphereDrag law used by v1912 particle trajectories**:

```
Re = ws*d/nu
CdRe = 24*(1 + Re^(2/3)/6)  for Re <= 1000
CdRe = 0.424*Re             otherwise
CdRe*ws*nu/d = (4/3)*(rhoParticle/rhoFluid - 1)*|g|*d
```

For 0.125-mm quartz in freshwater with nu=1e-6 m²/s this gives about
**0.01158 m/s**. It assumes spherical dilute particles; natural grain shape,
hindered settling, flocculation and grain-size mixtures are not modeled.
Carrier viscosity/density are sampled from their fields for this estimate.
Optionally supply `settlingVelocity` [m/s] to override ws for the Rouse profile
and equilibrium initial velocity. That override does **not** replace the native
drag/gravity force law; a different supplied ws will relax toward the force-law
terminal speed during transport.

For `shearVelocityModel localBed`, bed face centres are selected within the
transect width and between the inlet and `shearSampleLength` downstream. Using
the selected incompressible turbulence model:

```
traction = outwardBedNormal · devReff
kinematicShear = |traction - normal*(normal·traction)|
uStar = sqrt(areaWeightedMean(kinematicShear))
```

This is equivalent to `sqrt(tau_b/rhoFluid)`. It includes viscous and modeled
stress, so wall functions and bed resolution affect the estimate. `bedPatch`
must be a wall. Empty sampling regions and zero shear for a Rouse feed are
rejected; there is no hidden minimum-shear floor. To prescribe shear instead:

```foam
shearVelocityModel constant;
uStar 0.02;                 // positive m/s
```

The reference bed region should represent the inlet hydrodynamics. For an
initially quiescent flow, establish the flow first or use a justified prescribed
uStar; imposing an equilibrium Rouse profile without shear is undefined.

`initialVelocity carrier` gives particles the local inlet fluid velocity;
gravity and drag establish slip over time. `initialVelocity equilibrium` adds
the computed downward ws slip at release. Gravity acts through the normal native
particle force model afterward; no extra settling displacement is added.

## Hydrodynamics, dispersion, boundaries and restart

The solver retains transient PIMPLE, fluid Courant control, ordinary turbulence
selection, fixed-mesh MRF/fvOptions momentum terms, and U/p/phi fields. The carrier
is incompressible, with rho read dimensionally from `constant/transportProperties`;
mu=rho*nu. The cloud must have `coupled false; transient yes;` and is evolved
once after each PIMPLE time step. Sand does not alter flow, turbulence or density.
Dense feeds, particle collisions, erosion of an external bed reservoir and
morphology are outside this dilute model. Optional resuspension reactivates
previously deposited parcels on the fixed bed, as described below.
The tutorial uses `sphereDrag` and `gravity`; changing
forces changes trajectories and can invalidate the calculated settling estimate.

A Rouse **injection** profile does not maintain an equilibrium concentration
profile downstream. In laminar flow, the tutorial's `dispersionModel none` gives
deterministic drag/gravity transport. For appropriate RANS cases,
`dispersionModel stochasticDispersionRAS` enables the native random-eddy model
using k and epsilon. Its interface was tested with kEpsilon. This does not make
the 3-D smoke mesh a calibrated sediment-dispersion model. LES/DES fluid-model
selection remains inherited, but their subgrid particle dispersion needs a
suitable validated closure; no universal new LES/DES particle model is supplied.

The tutorial's native `localInteraction` policy is:

- **bed: stick** — particles become inactive and remain on the fixed wall;
- **lid/sides: rebound** — elastic reflection in this example;
- **upstream/downstream: escape** — particles leave the cloud and their mass is
  retained in cumulative escaped-mass statistics.

Inspect or change these policies for the application. Resuspension is disabled
by default, so deposited particles remain inactive. The solver reports mobile
and deposited cloud
mass; native cloud output reports injected and escaped mass. Use the balance
`injected = mobile + deposited + escaped` for the supplied no-collision example.

OpenFOAM fields are written under `<time>/lagrangian/sandCloud/` (positions,
velocities, diameter, nParticle, age, active flags and particle identifiers).
Injection/escape history is under
`<time>/uniform/lagrangian/sandCloud/sandCloudOutputProperties`. Restart with
`startFrom latestTime;`, retaining **the complete time directory**, including
these cloud files and uniform metadata. Missing injector history at a nonzero
time after feed start is explicitly rejected. Keep feed schedule, rate, diameter,
seed and geometry unchanged across a restart. Native stochastic dispersion's RNG
is separate: exact turbulent realizations are not claimed reproducible on restart.

## Optional bed resuspension

Enable the top-level `resuspension` dictionary in
`constant/sandCloudProperties`. Select walls whose parcel interaction is `stick`:

```foam
resuspension
{
    enabled true;
    bedPatches (bed);
    thresholdModel Shields;
    criticalShields 0.05;
    releaseRate 1;             // K, per second
    releaseExponent 1.5;       // n
    minimumRestTime 0.1;       // seconds after deposition
    normalLaunchSpeed 0.02;    // m/s into the fluid
    launchShearFactor 0;       // additional launch speed = factor*uStar
    liftDiameters 2;           // initial inward displacement = 2*d
    liftHeight 0;              // positive metres overrides liftDiameters
    seed 67890;
    writeEvents false;         // true logs individual release times and IDs
    initializeHistoryOnRestart false;
}
```

The stress is evaluated **at each resting parcel's bed face**, using the current
carrier solution and `devReff` from the selected laminar/RANS/LES turbulence
model. Its tangential traction magnitude gives `tau_b = rhoFluid*kinematicShear`.
Wall functions, near-wall resolution and the carrier closure therefore affect
entrainment. Choose one threshold:

| thresholdModel | Required setting | Ratio R that controls release |
| --- | --- | --- |
| `Shields` | `criticalShields` (default 0.05) | `tau_b / [(rhoParticle-rhoFluid)*|g|*d*criticalShields]` |
| `shearStress` | `criticalShearStress`, Pa | `tau_b / criticalShearStress` |
| `nearBedVelocity` | `criticalVelocity`, m/s | `magnitude(tangential bed-owner-cell U) / criticalVelocity` |

For the tutorial's 0.125-mm quartz in freshwater, `criticalShields 0.05`
corresponds to about **0.101 Pa**. The velocity option samples the adjacent cell
centre, so its threshold is explicitly mesh dependent. It is not the no-slip
wall velocity. Thresholds and release/launch coefficients need calibration for
the intended sediment and bed conditions; this is an empirical entrainment
process, rather than a resolved contact-force model.

Once the minimum rest time has elapsed, the model uses

```
lambda = releaseRate * max(R - 1, 0)^releaseExponent
P(release during dt at constant lambda) = 1 - exp(-lambda*dt)
```

There is no release at or below the threshold. A deterministic exponential
waiting threshold is drawn once per deposition episode using the seed and native
particle ID. The integrated hazard accumulates during eligible intervals and
pauses below threshold. Its crossing gives the release time within the carrier
step. Subdividing a constant-rate interval preserves that sampled time; changing
time steps can still change the hydrodynamics and the deposition time. Newly
detected contacts are dated at the **end of the cloud step**, so residence timing
has that time-step resolution. No release is attempted again within that same
step after redeposition.

Release reactivates the **same computational parcel**. Its diameter, density,
`nParticle`, mass and native ID are preserved. The initial tangential velocity is
the bed-owner-cell carrier velocity, while the inward normal launch speed is
`normalLaunchSpeed + launchShearFactor*uStar`. The parcel is relocated inward
with fresh native tetrahedral addressing, then tracked only for the time
remaining after release. Gravity, drag and optional native turbulent dispersion
continue to act normally. The inward lift must be positive and less than half
the bed-face-to-owner-cell-centre normal distance; excessive displacement fails
explicitly. A parcel that returns to a sticking bed starts another residence
episode. Release acts on whole weighted parcels; increasing parcelsPerSecond
reduces sampling noise without altering the total feed.

The model adds no bed material. It can resuspend only the deposited particles
already in this cloud. The log reports release count, released mass and
**cumulative released mass**, which includes repeated releases of the same
parcel. Native `stick` statistics likewise count repeated deposition contacts.
Neither cumulative quantity is the current bed inventory. Use `Sand inventory`
and cumulative escaped mass for `injected = mobile + deposited + escaped`.

When enabled, residence times, episode counters, sampled thresholds, integrated
hazards and cumulative release statistics are saved in
`<time>/uniform/lagrangian/sandCloud/sandResuspensionState`, alongside the usual
cloud restart files. History follows the particle during MPI transfers. Keep
the complete time directory, the same fixed mesh and decomposition, and the same
resuspension settings for reproducible restart. Missing history is rejected.
To **first enable the process on older results** that lack this file, explicitly
set `initializeHistoryOnRestart true`; existing selected-wall deposits are then
assigned a new residence starting at the restart time. This cannot recover their
earlier contact times. Return that setting to false for subsequent restarts.
When disabled or omitted, no resuspension history is read or written.

For a fresh enabled run without changing the supplied tutorial:

```bash
case_dir=$(mktemp -d /tmp/sand-resuspension-XXXXXX)
cp -a tutorials/sandParcelChannel/. "$case_dir/"
foamDictionary "$case_dir/constant/sandCloudProperties" \
    -entry resuspension.enabled -set true
blockMesh -case "$case_dir" > "$case_dir/log.blockMesh" 2>&1
sandParcelPimpleFoam -case "$case_dir" > "$case_dir/log.solver" 2>&1
```

The supplied flow may stay below the default threshold. Choose physically
justified thresholds and inspect `maxThresholdRatio` in the log; a value above
one permits stochastic release after the minimum rest time.

Low parcel rates accumulate mass until a parcel is due; a final partial batch
is flushed at the end of duration, including durations shorter than one parcel
interval. These batches are released in the current fluid step, not backdated
with the current velocity field. Raise parcelsPerSecond for finer temporal and
spatial feed resolution. Fractional computational particle counts are normal
statistical weights. Input rates, geometry, gravity and profile parameters are
validated; invalid widths/cutoffs, nonfinite inputs and zero Rouse shear fail.

## v1912 parcel-time correction

The matching v1912 native injector advances a new parcel using its remaining
injection time, but also initializes a carrier-step fraction. The normal cloud
then resets all fractions and advances those parcels a second time. This was
observed as age 0.21 s at simulation time 0.2 s for a fresh uniform-flow feed.

`SandKinematicCloud.H` fixes both timing inconsistencies locally: new parcels
start at zero progress for their partial first tracking interval; progress is
converted back to the full carrier step for subsequent processor transfer;
existing parcels are reset **before** injection; the inherited transfer loop is
adapted to preserve new-parcel progress afterward. Shared installed OpenFOAM
libraries are unchanged. An independent uniform-flow test checks parcel ages,
horizontal advection and terminal settling against exact trajectories. This
wrapper is specific to the inspected v1912 APIs and fixed meshes.

## Source layout

- `sandParcelPimpleFoam.C`: PIMPLE flow, carrier properties, cloud evolution and
  mobile/deposited mass diagnostics.
- `SandTransectInjection.H`: runtime-selected native injection submodel;
  transect clipping, local shear, settling, profile quadrature and sampling.
- `SandKinematicCloud.H`: first-step tracking correction and native MPI transfer.
- `SandResuspension.H`: optional local threshold, waiting-time and launch model.
- `SandBedHistory.H`: restartable bed history transported with native parcel IDs.
- `Make/files`, `Make/options`: OpenFOAM build target and libraries.
- `tutorials/sandParcelChannel`: complete 3-D feed example.
- `tests/openfoam/validate_parcels.py`: real executable integration checks.
- `tests/openfoam/validate_resuspension.py`: release, residence, conservation,
  restart, velocity/stress thresholds, time-step and MPI checks.

Hydrodynamic UEqn/pEqn/createFields are reused from the sibling fixed-mesh solver,
which was adapted from OpenCFD v1912 pimpleFoam. The code is GPL-3.0-or-later;
see the solver directory's COPYING.

## Validation performed

The v1912 executable passed **13 integration checks**: physical feed rate and
uniform sampling within the exact strip; independent uniform-flow particle ages,
advection and drag/gravity settling; Rouse exponent and height CDF against
independent quadrature; dynamic local bed shear; sub-one-parcel final batches;
nonaligned injection start/duration; restart sample/trajectory continuity;
streamwise MPI migration and outlet mass accounting; an inlet split across MPI
ranks; kEpsilon with stochasticDispersionRAS; retained deposited mass; invalid
strip rejection; and missing restart-state rejection. The five original Python
tests also passed, and the documented build and tutorial commands ran successfully.

The default one-second tutorial injected **1e-4 kg**, with **6.79e-5 kg mobile** and
**3.21e-5 kg deposited**, conserving the feed mass. The separate outlet/MPI test
accounted for mobile, deposited and escaped mass. These are implementation checks,
not experimental calibration or a grid-convergence study.

The additional resuspension suite checks disabled-process equivalence,
subcritical stress and minimum residence, repeated release/redeposition without
mass or ID changes, continuous versus restarted trajectories, equivalent Shields
and dimensional-stress thresholds, constant-flow velocity thresholds and
exponential waiting statistics, time-step subdivision and partial-step tracking,
MPI history transfer/restart, and rejection of missing history or excessive lift.

This instance's test console logs are
`/workspace/ofsolvers-validation/parcel-tests-resuspension.log` and
`/workspace/ofsolvers-validation/resuspension-tests.log`. Re-running either suite
creates and prints a fresh directory containing cases, cloud fields and logs.
The executable is
`/workspace/ofsolvers-build/bin/sandParcelPimpleFoam`.
