# Individual large tracer particles

`largeTracerFoam` tracks **1–999 individual physical spheres** in saved OpenFOAM
velocity fields. Choose a distribution of diameters and densities, place the
objects in an initially wetted region or on a bed, and follow their trajectories.
For a pre-existing scour hole, the outputs distinguish movement, leaving the
hole region, rising past the rim, and escaping the computational domain.

This is a **one-way replay solver**: it reads the carrier flow and integrates
the particles without changing the flow. Each ID represents exactly one sphere
with its own mass and diameter. There is no parcel multiplier or inlet mass feed.
The existing `sandParcelPimpleFoam` remains the model for weighted sand parcels.

The tested API is **OpenFOAM.com v1912**, double precision, 32-bit labels.

## Run the scour-hole demonstration

Activate OpenFOAM and install the workbench Python dependencies described in
[Getting started](getting-started.md). From the repository root:

```bash
./Allwmake
./tutorials/largeTracerScour/Allrun /path/to/new-empty-scour-case
```

The wrapper generates a case, meshes it, initializes water, runs `interFoam`,
replays the tracers, and produces plots. Use an empty destination.
For separate steps and finer carrier mesh:

```bash
python3 scour_tracer_demo.py /path/to/new-empty-scour-case --velocity 0.3 --end-time 5
cd /path/to/new-empty-scour-case
./Allmesh
interFoam > log.interFoam 2>&1
largeTracerFoam > log.largeTracerFoam 2>&1
```

The 3 × 0.5 m channel has a fixed, 0.2 m deep scour depression between x=1 and
x=2 m, with sloping sides and a flat bottom. Water initially reaches z=0.5 m
above its rim. The mesh extends to z=1 m and contains air: **the 0.5 m water
depth uses a moving VOF free surface**. Inlet water speed supports 0.1–1 m/s.
The outlet permits outflow; downstream water level is not prescribed. The
example uses laminar flow to keep it small and reproducible. It demonstrates
the workflow, rather than predicting a turbulent scour experiment. The bed
does not erode or change shape.

Defaults are 25 spheres with independently sampled uniform diameters of
20–40 mm and densities of 500–950 kg/m³, initially inside the depression and
at rest. These densities are below water density, so buoyancy can mobilize
them without an imposed bed-shear threshold. Edit them to represent the
actual objects. A dilute sand concentration model does not control these bodies.

For particles slightly denser than water, a second settings file seeds spheres
on the depression's bed with densities of 1050–1200 kg/m³. After the carrier run,
from the repository root:

```bash
python3 large_tracer.py /path/to/case --settings tutorials/largeTracerScour/near-neutral-settings.json
largeTracerFoam -case /path/to/case
python3 visualize_tracers.py /path/to/case/postProcessing/largeTracers/near-neutral-bed --show
```

This uses a separate run name and the same saved flow. In the recorded 3 s
demonstration, 7 of these 25 spheres moved at least one diameter and all 25
remained in the selected depression region.

## Workbench

The **Large tracers** tab shares the active case with the other tabs:

1. Open a meshed flow case, or select an empty folder and generate the scour demo.
2. Mesh and run the carrier solver in **Execution & Monitoring**. Save `U` and,
   for a free surface, the water-fraction field throughout the desired interval.
3. In **Large tracers**, set count, diameter/density distributions, seed-center
   box, placement mode, and the scour retention box and rim elevation.
4. Set tracking times within the saved flow interval. Select **Frozen flow**
   explicitly if one snapshot should remain unchanged during tracking.
5. Save settings and run `largeTracerFoam`. Choose a fresh output run name for
   each comparison; previous results are protected against overwriting.
6. Select **Plot trajectories and particle outcomes** to write a trajectory
   figure, a central x–z carrier-velocity figure, and an interactive browser replay.

Execution uses the workbench's existing process monitor and cancellation.
The carrier application and its `controlDict` remain the carrier's settings;
the tracker has its own time controls in `constant/largeTracerProperties`.

The setup JSON is `tracers.json`. GUI/CLI edits back up previous settings in
`.ofsolvers-backups`. The native solver reads `constant/largeTracerProperties`;
advanced manual dictionary edits are not imported back into the JSON editor.

## Configure an existing flow model

The carrier can come from `interFoam`, `sedimentInterFoam`, or another solver
that writes compatible `U` and water-fraction fields on a static 3D mesh.
Reconstruct parallel results with `reconstructPar` before replay. Particle
tracking currently runs in serial; the carrier may have been computed in parallel.
Changing meshes, cyclic boundaries, wedge/empty 2D patches, and processor
patches are rejected. Boundary mesh patches of type `wall` provide contact;
other supported patches permit escape when the particle center leaves the mesh.
Check the mesh patch types, including any obstacle, before using a custom case.

Create defaults, edit them, and check saved carrier coverage:

```bash
python3 /path/to/OFSolvers/large_tracer.py /path/to/case
# Edit case/tracers.json, then regenerate the native dictionary:
python3 /path/to/OFSolvers/large_tracer.py /path/to/case
python3 /path/to/OFSolvers/large_tracer.py /path/to/case --check
largeTracerFoam -case /path/to/case
```

Defaults describe the supplied scour hole. **Set seed and retention boxes for
your geometry** before running an existing case. A seed box contains centers;
the entire sphere must clear all mesh boundaries, and its sampled water fraction
must meet `minWaterFraction`. The default threshold is 0.99. Placement rejects
initial overlaps, and the entire population must be placed successfully before
any output is created. Particle sizes and densities are sampled once per ID;
failed placements do not resample them and bias their distributions. Infeasible
packing produces an error with the particle ID and diameter.

For `placement bed`, x and y are drawn from the box. A downward ray intersects
the selected `bedPatches`, and the center is offset by its radius along the
inward normal. The ray range and resulting center must lie in the seed box.
This mode requires gravity along negative z. It supports a selected patch's
triangulated sloping bed, rather than assuming a horizontal floor.

Important native entries, all in SI units:

| Entry | Meaning |
| --- | --- |
| `count`, `seed`, `runName` | Physical object count, reproducible random seed, unique result folder |
| `diameter`, `density` | Independent distributions in m and kg/m³ |
| `seedRegion` | Center-box `min`/`max`, `placement volume` or `bed`, `bedPatches`, minimum initial water fraction |
| `retentionMin`, `retentionMax` | Box used for center-based retention/first-exit events |
| `rimElevation` | First upward center crossing, for particles initially below this elevation |
| `initialVelocity` | `zero` or local volume-averaged `flow` |
| `startTime`, `endTime`, `outputInterval` | Tracking interval and CSV/VTK output spacing, independent of carrier controls |
| `frozenFlow`, `flowTime` | Explicit frozen-snapshot mode and exact saved snapshot time |
| `alphaField` | `alpha.water`, another water-fraction field name, or `none` for a completely submerged single phase |
| `rhoWater`, `muWater`, `rhoAir`, `muAir`, `g` | Densities, **dynamic** viscosities, and gravity; match the carrier model |
| `addedMassCoefficient` | Default 0.5 for a sphere |
| `restitution`, `friction` | Normal contact restitution [0,1], Coulomb tangential friction ≥0 |
| `particleCollisions` | Enable sphere–sphere contact; seeding always prevents initial overlap |
| `particleCollisionMode` | `all` (default) or `largeOnly`, which requires both diameters to exceed the threshold |
| `collisionDiameterThreshold` | Positive diameter cutoff in m, default 0.03; used by `largeOnly` |
| `mobilizationDiameters` | Displacement threshold / particle diameter; default 1, a reporting metric |
| `volumeSamples` | Deterministic volume quadrature resolution, 32–8192, default 128 |
| `maxDeltaT` | Upper integration step, default 0.002 s |

Advanced native controls include `minDeltaT` (1e-9 s),
`maxDisplacementFraction` (0.1 radius, maximum 0.25), `maxSteps` (10 million),
and `contactIterations` (32). Unsafe timesteps or unconverged contact stop the
run without writing a completion marker. `initialPositions ((x y z) ...);`
and `initialVelocities ((vx vy vz) ...);` optionally supply exactly `count`
explicit states; otherwise initialization uses the region and velocity mode.
Explicit positions still undergo geometry, overlap, and water-fraction checks.

Distribution dictionaries:

```foam
diameter { type lognormal; min 0.01; max 0.08; median 0.03; sigma 0.3; }
density  { type discrete; values (500 800 1050); weights (1 2 1); }
```

Other options are `type constant; value 0.03;` and
`type uniform; min 0.02; max 0.04;`. Lognormal `sigma` is the standard deviation
of the **natural logarithm**; samples are truncated by rejection at min/max,
not clipped to the endpoints. Discrete weights are relative probabilities.
Size and density are independent; correlated joint distributions are not implemented.

## Equations and numerical treatment

For each sphere, volume V=πd³/6, physical mass mₚ=ρₚV, and displaced-fluid
mass mᶠ=V[fρwater+(1−f)ρair], where f is its volume-averaged water fraction.
The translation model is:

```text
(mₚ + Cₐ mᶠ) dv/dt = Fdrag + (mₚ − mᶠ) g
                       + (1 + Cₐ) V [f ρwater awater + (1−f) ρair aair]
dx/dt = v
a = ∂U/∂t + (U · ∇)U
```

Including the fluid-acceleration force matters for low-density particles;
drag and gravity alone would not reproduce a neutral object in accelerating
uniform flow. Drag is Schiller–Naumann below Re=1000 and Cd=0.44 above that,
with the finite Stokes limit at zero slip. Water and air contributions are
weighted by their sampled volume fractions. Deterministic quasi-uniform
sphere-volume samples average velocity, acceleration, and water fraction.
Carrier fields and their gradients use `cellPoint` interpolation and linear
interpolation between saved times; transient data are never extrapolated.

Drag coefficients and sampled forces are frozen over each substep; velocity
and displacement use the exact solution of that linearized drag equation.
Adaptive steps limit displacement, acceleration, drag response, output times,
and carrier snapshot boundaries. This remains a first-order force update;
reduce `maxDeltaT` and carrier snapshot spacing to establish convergence.

Contact uses the sphere radius and nearest boundary triangles, with impulses
for normal restitution and Coulomb friction. Spheres have rotational inertia
I=2mₚR²/5. Contact generates spin and rolling; a Stokes rotational damping torque
opposes spin in the fluid. Sphere contacts use the physical masses, not the
added fluid masses. Overlap corrections iterate, with an x-axis broad phase
for particle pairs. Contacts conserve momentum for isolated elastic pairs.

### Sediment contact for large particles only

In **Large tracers → Physical properties and contact**, enable particle–particle
sediment contact and select **Both diameters above threshold**. Enter a diameter
in meters. For a 30 mm cutoff, the native settings are:

```foam
particleCollisions true;
particleCollisionMode largeOnly;
collisionDiameterThreshold 0.03;
restitution 0.1;
friction 0.4;
```

A pair is eligible only when **d₁ > cutoff AND d₂ > cutoff**. A diameter exactly
equal to the cutoff is excluded. Filtering occurs before the pair search,
contact impulses, and overlap corrections. Small–small and small–large pairs
can therefore overlap or pass through each other during tracking: the smaller
objects act as independent tracers for pair contact. They still have mass,
size-dependent fluid forces, buoyancy, inertia, and finite-radius bed/wall
contact. Initialization always avoids overlaps for every size.

The selected pair model is for **noncohesive coarse sediment**: normal inelastic
impact controlled by restitution, Coulomb tangential friction, and rotation
generated by tangential contact. Restitution and friction share the existing
coefficients used for bed/wall contacts; calibrate them for the sediment and
fluid conditions. These controls do not introduce a Shields entrainment law,
cohesion/flocculation, grain breakage, or a rolling-resistance law. Rolling from
the existing contact mechanics remains available. Lubrication and resolved
fluid disturbances between grains are outside this contact approximation.

`particleCollisions false` disables all pair contact in either mode. `all`
keeps the original contact behavior for every size. Existing native dictionaries
and schema-1 JSON without the new keys retain their original behavior. JSON
uses `collisions`, `collision_mode`, and `collision_diameter_threshold`.

The supplied mixed-size bed example can be run after the scour carrier flow:

```bash
python3 large_tracer.py /path/to/case --settings tutorials/largeTracerScour/large-particle-contact-settings.json
largeTracerFoam -case /path/to/case
python3 visualize_tracers.py /path/to/case/postProcessing/largeTracers/large-only-contact --show
```

`pair_contact_eligible` in tracks, summaries, and VTK output identifies the
population selected for pair contact. The replay offers **Pair contact
eligibility** coloring and per-object eligibility details for new runs.
This flag describes eligibility by configuration and size, not an ongoing
collision. The solver log reports the selected mode, threshold, and eligible count.
See the [cutoff and sediment-contact validation](validation/large-tracers/diameter-cutoff/README.md).

There is no imposed sand Shields criterion, adhesion, or artificial bed sticking.
An ideal sphere can roll under sustained small tangential forcing. Real
particle shape, roughness, burial, entanglement, and rolling resistance require
additional models or calibration before interpreting a mobilization threshold.

## Outputs and visualization

Results are isolated under `postProcessing/largeTracers/<runName>/`:

| File | Contents |
| --- | --- |
| `tracks.csv` | Every ID at every output time: position, velocity, spin, diameter, density, water fraction, current activity/retention, event flags, maximum displacement |
| `events.csv` | First mobilization, region exit, upward rim crossing, and domain escape times |
| `summary.csv` | One row per physical object, mass, initial position, first-event times, final retention/activity |
| `particles.pvd`, `particles_*.vtk` | Time-indexed active-particle points with stable IDs, velocity, diameter, density, and water fraction |
| `largeTracerProperties` | Settings snapshot used for this run |
| `complete` | Written only after successful completion |

Event times have substep resolution. A value of −1 in the summary means the
event did not occur. “Mobilized” uses maximum straight-line displacement from
the initial center, not path length. “Left region” is the first inside-to-outside
transition; a particle can subsequently return. “Inside region” reports current
center membership separately. “Escaped” means the center left the mesh, not
merely that a sphere's leading edge crossed an outlet. Escaped objects stay
in CSV histories at their last state and disappear from active VTK snapshots.

Generate figures and the self-contained replay:

```bash
python3 visualize_tracers.py /path/to/case/postProcessing/largeTracers/tracers01 --carrier --show
```

Open `visualization/replay.html` in a browser. It includes play/pause, a time
slider, x–z/x–y/y–z projections, color by density/speed/ID, and clickable object
details. All particle y positions are projected into the x–z trajectory plot;
that plot is not a thin central-plane slice. `flow-xz.png` separately shows
the actual carrier velocity on the central longitudinal transect, with air
masked and the water-fraction 0.5 contour. It uses the closest saved time to
the end of tracking, or `flowTime` for a frozen run.

For a full 3D view, open the carrier `.foam` case and `particles.pvd` together
in ParaView. Apply **Glyph → Sphere** with diameter as the scale array and
color by ID, density, or `pair_contact_eligible`. Use **Temporal Particles To Pathlines** with ID as
the identity array. A **Slice** normal to y shows longitudinal carrier flow;
contouring `alpha.water=0.5` shows the free surface.

## Limits and validation

Low particle count does not imply low volume fraction. The particles do not
displace water, obstruct flow, change turbulence, or feed momentum back into
the carrier. Large confinement/blockage, dense packing, and particle-driven
surface waves require resolved moving-body CFD or an appropriate coupled
CFD–DEM model. Volume-averaged sampling does not resolve flow around a body.
The unbounded-sphere drag/added-mass laws need assessment for the actual sizes,
shapes, Reynolds numbers, and proximity to walls.

Free-surface flotation uses sampled displaced mass and approximate phase-weighted
drag. It omits surface tension on the particle, water entry/exit slamming, lift,
history forces, added-mass derivative terms across the interface, lubrication,
and wave generation by particles. Near-interface oscillations are approximate.
Rotational damping uses the viscous sphere approximation. With RANS carrier
fields, trajectories follow resolved mean flow; turbulent dispersion is not added.
The initial implementation has no restart/resume; rerun from the same seed and
start time under a new run name. Keep carrier fields unchanged for reproducibility.

Native checks are executable:

```bash
python3 tests/openfoam/validate_large_tracers.py
python3 -m pytest -q
```

They check neutral advection, analytical Stokes response with added mass,
buoyant terminal rise, acceleration in transient carrier fields, static floating
balance, timestep refinement, sloping-bed placement, rolling and finite-radius
bed contact, elastic pair momentum/energy, domain escape,
deterministic populations, infeasible packing, protected outputs, and 999 unique
physical objects. Manufactured carrier fields are checked for unchanged contents.
See the [recorded validation and demonstration](validation/large-tracers/README.md)
for measured errors, settings, plots, and the limits of the example.
