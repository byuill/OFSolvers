# Submerged turbulent CFD–DEM bedload solver

`bedloadDemPimpleFoam` couples OpenCFD v1912 PIMPLE flow to the **LAMMPS
GRANULAR** research DEM engine. Every sphere is a physical grain. It exchanges
momentum in both directions, updates fluid porosity as grains move, and includes
native grain–grain and grain–wall contact. It is separate from the one-way
`largeTracerFoam` replay solver and the weighted `sandParcelPimpleFoam` cloud.

**Status: experimental research coupling, with verified component/reference
problems. It is not an experimentally calibrated river-bed transport model.**
The DEM engine is an established research code; that does not by itself validate
this new flow coupling or a particular bedload prediction.

The first implementation is submerged with a rigid lid, as requested. It uses
an axis-aligned periodic/walled box. Fluid decomposition supports MPI; a single
serial LAMMPS instance on the CFD master owns the common grain inventory.

## Build

Activate a matching **OpenCFD v1912, double-precision** installation with source
headers. Build a serial shared LAMMPS library with GRANULAR and exceptions.
The reproducible helper downloads **22 Jul 2025 Update 4**, checks the archive's
SHA-256, and installs outside system directories:

```bash
python -m pip install cmake       # if CMake >=3.20 is not installed
scripts/build-dem-backend.sh /absolute/empty/lammps-install
export LAMMPS_ROOT=/absolute/empty/lammps-install
source /path/to/OpenFOAM-v1912/etc/bashrc
./Allwmake --with-dem
```

`DEM_BUILD_JOBS` controls backend compilation parallelism. `CMAKE` can point to a
specific CMake executable. The backend script refuses a nonempty installation
prefix. Upstream LAMMPS sources are downloaded, not vendored in this repository;
LAMMPS retains its own GPL license and acknowledgements.

The ordinary `./Allwmake` continues to build the four existing solvers without
requiring LAMMPS. The optional executable links only the public LAMMPS C API and
embeds the selected library path. MPI-enabled LAMMPS libraries and BIGBIG atom
IDs are refused by this initial adapter; build the serial SMALLBIG backend.
Newer LAMMPS releases must preserve the selected API/contact syntax and need
verification before research use.

In the prepared workspace:

```bash
source scripts/openfoam-env.sh
export LAMMPS_ROOT=/workspace/lammps-install
./Allwmake --with-dem
```

## Four-millimetre river-bed demonstration

```bash
python bedload_dem.py /absolute/empty/bedload-case --diameter .004
/absolute/empty/bedload-case/Allrun
python visualize_dem.py \
  /absolute/empty/bedload-case/postProcessing/bedloadDEM/bedload-0 --flow
```

Or run `tutorials/bedloadDEM/Allrun /absolute/empty/bedload-case`.
Install the existing GUI requirements for Python plotting/VTK support.

Defaults are a **0.16 × 0.064 × 0.064 m** submerged periodic channel, 4 mm grains
of density 2,650 kg/m³, four initially mobile layers over one fixed basal layer,
water at 1,000 kg/m³ and ν=10⁻⁶ m²/s. This produces **2,925 grains: 2,340 mobile
and 585 fixed**. Mobile layers initially have clearance and must settle.
The initial intrinsic flow is 0.3 m/s with downstream forcing 0.1 m/s².
The default turbulence model is porosity-weighted k–ω SST. This is an imposed
startup state, not an equilibrated bed or developed river flow.

The demonstration uses an **effective Young modulus of 100 MPa**, restitution
0.8, sliding friction 0.5 and optional SDS rolling resistance. This stiffness
is a computational demonstration value, not a measured quartz elastic modulus.
Check overlaps and stiffness sensitivity before using it for predictions.
Use `python bedload_dem.py --help` to change grain size, density, geometry, contact
properties, time settings, kernel quadrature and turbulence. Particle populations
can also be specified in native `constant/DEM/particles.data` sphere format:
`id type diameter density x y z`. IDs must be consecutive from 1. Type 1 is
mobile; type 2 is fixed roughness. Native `fix freeze` gives fixed grains the
correct infinite-inertia contact treatment. Their displaced volume and fluid
drag remain included.

The generated `Allrun` refuses an existing mesh. Resume deliberately using the
restart instructions below rather than regenerating an existing case.
The workbench execution selector recognizes this solver and checks its fluid
fields, porosity, properties and checkpoint files. The specialized physical-bed
case generator is currently a command-line workflow; the old tracer controls
configure a different solver and must not be used for DEM contacts.

## Equations and coupling

U is intrinsic liquid velocity, ε is fluid porosity, p is **kinematic dynamic
pressure with hydrostatics removed**, and φ is the superficial liquid volume
flux εU·Sf. The implemented fluid equations are

\[
\partial_t\epsilon+\nabla\cdot(\epsilon\mathbf U)=0,
\]
\[
\partial_t(\epsilon\mathbf U)+\nabla\cdot(\epsilon\mathbf U\mathbf U)
=-\epsilon\nabla p+\nabla\cdot[\epsilon\nu_{eff}\,
\mathrm{dev}(\nabla\mathbf U+\nabla\mathbf U^T)]
+\epsilon\mathbf a_{drive}+\mathbf S_{drag}.
\]

The fluid stress here is explicitly porosity weighted. Native v1912 turbulence
models are instantiated with ε as phase capacity and φ as their phase transport
flux, rather than using clear-water k/ω/ε transport in a dense bed. Available
models are laminar Stokes, k–ω SST, k–ε, Smagorinsky and WALE.

Each mobile sphere follows

\[
m_i\dot{\mathbf v}_i=\mathbf F_{contact,i}
+\beta_i(\overline{\mathbf U}_i-\mathbf v_i)
-\rho_f V_i\overline{\nabla p}_i
+(m_i-\rho_f V_i)\mathbf g.
\]

LAMMPS integrates translation/rotation, Hertz/material normal contact,
restitution-based damping, history-dependent Mindlin friction and optional SDS
rolling resistance. There are no weighted representative parcels or artificial
permanent sticking rules. Entrainment, rolling, sliding, deposition and bed
rearrangement emerge from forces and contacts.

The default drag is **Di Felice (1994)** with intrinsic slip and
Reₚ=ερd|U−v|/μ. A `SchillerNaumann` option supports dilute reference studies.
The algebra retains a finite drag coefficient as slip approaches zero.

A deterministic spherical kernel, of radius `kernelRadiusRatio × grain radius`,
distributes each grain's **actual** volume across local CFD cells. Support is
wrapped on periodic axes and renormalized across CFD ranks and clipped wall
support. Sampling U and depositing drag use the same weights. Each DEM interval's
velocity-Verlet drag impulse—including its half-weighted end forces—is returned
with opposite sign to the fluid. The solver checks volume and drag-impulse
conservation every CFD step. It aborts on excessive packing instead of clipping
solid volume to a porosity floor.

This is **partitioned, first-order coupling**, not a monolithic implicit solve:

1. Sample current fluid velocity, pressure gradient and porosity at grain kernels.
2. Hold these fluid data and linearized drag coefficients over the coupling step;
   DEM subcycles update instantaneous grain slip, contacts and spin.
3. Return the integrated drag impulse, remap porosity at new grain positions,
   and solve the porosity continuity/pressure–velocity and turbulence equations.

Both phases influence each other. Temporal lag remains and must be assessed by
reducing the coupling step. Pressure-gradient and buoyancy forces are distinct
from the drag exchange; hydrostatic buoyancy is not added to the fluid twice.
Fixed-bed supports, walls and the external channel driver can exchange momentum
with the system, so total momentum is conserved only in appropriate closed,
unforced reference problems.

## Time steps and settings

`constant/demCouplingProperties` contains:

| Settings | Meaning |
| --- | --- |
| `youngModulus`, `poissonRatio`, `restitution`, `friction` | Native Hertz/material and Mindlin contact parameters |
| `rollingStiffness`, `rollingDamping`, `rollingFriction` | Native [SDS rolling model](https://docs.lammps.org/pair_granular.html); set all three to zero to disable |
| `dragModel` | `DiFelice` or `SchillerNaumann` |
| `kernelRadiusRatio`, `volumeSamples` | Conservative filter size and 32–8192 quadrature points per grain |
| `minimumPorosity` | An abort threshold, not a clipping rule |
| `maxDEMDeltaT`, `maxDEMSubcycles` | DEM contact step and maximum work per coupling interval |
| `responseTimeFraction` | Upper bound on the coupling step relative to particle and cell-fluid drag response times, at most 0.2 |
| `couplingDisplacementFraction` | Step bound based on current grain speed/diameter, at most 0.25 |
| `drivingAcceleration` | External fluid acceleration in m/s² for the periodic channel |
| `bedloadElevation`, `movingSpeed` | Absolute elevation cutoff and speed threshold for diagnostics |
| `runName` | Output series name, protected against overwriting |

Fluid time integration must be Euler. With `adjustTimeStep yes`, the CFD step
respects fluid CFL, maximum CFD step, grain response/displacement and cell-fluid
response limits. A prescribed excessive step is rejected. The DEM interval
subdivides the CFD step exactly and additionally respects 10% of the minimum
Rayleigh time. A post-step half-diameter movement guard prevents silently using
large changes with stale spatial coupling. Contact stiffness, overlap and DEM
step convergence still need independent checks; the Rayleigh estimate is not
a general guarantee for every load/rolling parameter set.

CFD cells must be appropriate for **unresolved** CFD–DEM, not smaller than grains
without a validated filtering strategy. Filter radius, quadrature and fluid mesh
resolution affect the dense-bed solution and must be studied together.

## Results and restarts

- Native CFD fields include U, p, `porosity`, `demReaction`, fluid volume flux
  `phi`, and the chosen turbulence fields.
- `postProcessing/bedloadDEM/<runName>-<startTime>/tracks.csv` stores each physical
  ID, wrapped x/y/z, unwrapped `ux/uy/uz`, velocity, spin, diameter, density and
  mobile/fixed status. LAMMPS image flags retain periodic travel across restart.
- `budget.csv` records fluid volume, grain mass, phase momenta, drag impulse,
  continuity, moving count and downstream solid flux.
- `particles.pvd` opens the grain time series in ParaView; overlay it on
  `case.foam`. Use sphere glyphs scaled by diameter for finite grain size.
- `visualize_dem.py` creates a bed snapshot/flux/momentum plot and optionally an
  actual saved **central longitudinal x–z fluid velocity transect**.

The streamwise solid volume flux per channel width is
qₛ=Σ(mobile Vᵢvₓᵢ)/(LₓLᵧ), in m²/s. `bedload_flux_x` restricts this sum to
centres below `bedloadElevation`; `total_solid_flux_x` includes all mobile grains.
The diagnostic is a periodic-domain spatial average, not a measured equilibrium
transport law. The moving count uses the configured speed threshold.

Every CFD write also writes native `uniform/DEM/lammps.restart` and
`couplingState` in the **global case time directory**, including for MPI. Binary
LAMMPS restart retains normal/tangential/rolling contact history and native
particle images. Both phases must resume from the same complete saved time;
missing/mismatched state is refused. Contact parameters must match the checkpoint.
Use compatible LAMMPS versions/builds for binary restarts.

The adapter reapplies the same granular style settings after reading a restart
to restore its radius-derived neighbour cutoff, without replacing contact
coefficients/history. Native velocity-dependent granular contacts are not
bitwise identical on restart: forces are recomputed using full-step velocities
rather than the preceding Verlet half-step. The active-contact regression checks
position, velocity and spin within stated physical tolerances. Native DEM setup
and checkpoint diagnostics are retained in `log.lammps-<startTime>`.

To continue, preserve the case, set `startFrom latestTime` and a later `endTime`
in `controlDict`, then execute `bedloadDemPimpleFoam` directly. Each restart
creates a separate output segment named with its starting time.
For two fluid ranks:

```bash
# Fresh, already meshed case; dictionary defaults to two x partitions.
decomposePar
mpirun -np 2 bedloadDemPimpleFoam -parallel
# Restart using consistent processor times and the matching global DEM checkpoint.
# Reconstruct before plotting fluid results:
reconstructPar -latestTime
```

## Validation and limits

Run `python tests/openfoam/validate_dem.py` after building the optional solver.
It exercises reacting fluid/grains, mass/volume/impulse conservation, a closed
momentum reference, native restart, active-contact history, infinite-inertia basal
contact, periodic travel, serial/two-rank equivalence, coupling-step refinement,
all five turbulence selections and invalid packing/checkpoint refusal.
The Python suite also checks generated populations/cases and existing workbench
behavior. Measured results and demonstration plots are in
[`validation/bedload-dem`](validation/bedload-dem).

The initial implementation does **not** resolve a water–air interface, arbitrary
river geometry, distributed MPI DEM contacts, nonspherical/cohesive grains,
lubrication, added mass, hydrodynamic lift/history forces, or subgrid turbulent
particle fluctuations/wake production. RANS supplies mean turbulent transport;
LES only supplies resolved velocity fluctuations at the CFD/filter resolution.
MRF and general fluid fvOptions are outside this solver's current interface.
Physical bed roughness/packing, contact friction/rolling/stiffness and the drag
closure require calibration and sensitivity studies. Verify settling/drag,
rebound and incipient motion against the intended material/flow experiment,
then demonstrate mesh, filter, CFD and DEM step independence before publishing
bedload transport predictions.

References: [LAMMPS granular models](https://docs.lammps.org/pair_granular.html),
[external-force API](https://docs.lammps.org/fix_external.html),
[frozen roughness](https://docs.lammps.org/fix_freeze.html);
Di Felice, *Int. J. Multiphase Flow* **20** (1994), 153–159,
[doi:10.1016/0301-9322(94)90011-6](https://doi.org/10.1016/0301-9322(94)90011-6).
