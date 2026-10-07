# sandParcelPimpleFoam

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
```

`Allwmake` builds both sediment solvers. `Allrun` creates a fresh copy of the 3-D
tutorial under `/tmp`, leaving the original unchanged. To choose the results
location, pass a new or empty directory to Allrun. On a normal v1912 installation,
source its `etc/bashrc` instead of the cloud-specific script. On Windows, run
OpenFOAM in an appropriate Linux/WSL installation; these are Linux C++ solvers,
not Windows GUI executables. Version-specific APIs have not been validated on
Foundation OpenFOAM, foam-extend or other OpenCFD releases.

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
Dense feeds, particle collisions, bed scour/resuspension and morphology are
outside this dilute model. The tutorial uses `sphereDrag` and `gravity`; changing
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

Inspect or change these policies for the application. Deposited particles are
not automatically resuspended. The solver reports mobile and deposited cloud
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
- `Make/files`, `Make/options`: OpenFOAM build target and libraries.
- `tutorials/sandParcelChannel`: complete 3-D feed example.
- `tests/openfoam/validate_parcels.py`: real executable integration checks.

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

The final one-second tutorial injected **1e-4 kg**, with **6.79e-5 kg mobile** and
**3.21e-5 kg deposited**, conserving the feed mass. The separate outlet/MPI test
accounted for mobile, deposited and escaped mass. These are implementation checks,
not experimental calibration or a grid-convergence study.

This instance's final test console log is
`/workspace/ofsolvers-validation/parcel-tests-final.log`; detailed test artifacts
are `/tmp/sand-parcel-validation-i2xdzly5`. Re-running the suite creates and prints
a fresh directory. The executable is
`/workspace/ofsolvers-build/bin/sandParcelPimpleFoam`.
