# River preprocessing GUI

[Getting started](getting-started.md) · [Concentration solver](sedimentPimpleFoam.md) ·
[Particle solver](sandParcelPimpleFoam.md)

The desktop GUI prepares OpenFOAM case dictionaries, initial conditions and
boundary conditions. The **River Sediment** tab supports both custom solvers.
Execution uses the OpenFOAM installation on the machine running the GUI.
Both sediment solvers use a fixed water domain and fixed bed elevation with
one-way dilute transport. They do not track a moving water surface or update
river bathymetry. Use a complete water-domain mesh and physically appropriate
water-flow boundary conditions.

## Launch

Use Python 3.10 or later. Install GUI dependencies in a virtual environment;
DEM processing additionally needs the terrain requirements:

```bash
cd OFSolvers
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-gui.txt
# Optional GeoTIFF/DEM tools:
python -m pip install -r requirements-terrain.txt
source /path/to/OpenFOAM-v1912/etc/bashrc
./Allwmake
python main.py
```

Use a Linux desktop/display or an approved remote desktop to run and visualize
the GUI. In the prepared cloud installation, its Python environment and
OpenFOAM activation are already available:

```bash
cd /workspace/OFSolvers
source scripts/openfoam-env.sh
python main.py
```

The cloud command still requires an attached graphical display. This is a
PyQt desktop application; it has no browser interface. On a government Windows
computer without administrator rights, use an IT-approved Linux machine or
remote desktop with OpenFOAM installed. Installing WSL may require IT support.
Source folders and cloud result folders live on that remote machine. Download
complete result directories for viewing in an approved Windows ParaView
installation. See [Getting started](getting-started.md#windows-and-wsl).

The preprocessing widgets can run without OpenFOAM, provided Python/Qt/VTK
and a display work. Mesh checks and simulations require the native executables.
Activate **OpenCFD v1912** before launching the GUI to use the sediment solvers.

## First sediment case

1. Create an empty results folder outside the source checkout. Click
   **Select case folder** at the top of the window and select it. All case-writing
   and execution tabs use this folder.
2. Open **7. River Sediment**, select `sedimentPimpleFoam` or
   `sandParcelPimpleFoam`, and click **Create runnable starter case**.
   This copies the corresponding channel example's `0/`, `constant/` and
   `system/` dictionaries. It refuses a nonempty folder.
3. Select inlet, outlet and bed walls in **Water, sediment and patches**. The
   starter already has suitable choices. Set grain diameter in **mm**, densities
   in **kg/m³**, and water kinematic viscosity in **m²/s**. Optional initial
   velocity and kinematic pressure update the uniform internal `0/U` and `0/p`.
4. Set the solver-specific controls described below. Click **Validate and write
   sediment properties and initial conditions**. This writes the initial fields
   and material/cloud dictionaries; it does not write water-flow boundaries.
5. In **8. Execution & Monitoring**, click **Generate initial mesh (blockMesh)**.
   The starter's mesh dictionary is ready to use. Existing meshes are protected
   from accidental regeneration through this button.
6. Open **5. Boundary Conditions**, import the actual mesh patch names, select
   the inlet, choose **Inlet**, and enter the water volumetric flow rate in m³/s.
   Click **Write edited fluid boundaries in 0/**. Review the bed and outlet
   conditions in the on-disk preview. Selecting a patch alone does not overwrite it.
7. In **1. Conceptualization**, adjust end time and time step if needed, then
   click **Save plan, time controls and mesh inputs**. This saves a JSON plan and
   updates existing `controlDict` application/startTime/endTime/deltaT entries.
   It does not generate a mesh or launch a solver. Existing `startFrom` and
   output controls remain in the dictionary.
8. In **Execution**, choose one MPI rank initially, click **Check case and mesh**,
   resolve errors and review warnings, then click **Run solver**. Every run
   repeats file checks and `checkMesh`. Mesh success requires the native
   `Mesh OK.` result, not just an exit code.

The GUI console displays output but does not create persistent solver log files.
Save/copy console output if needed, or use the terminal `Allrun` workflows for
on-disk logs. Numerical time directories are written according to `controlDict`.

## Suspended concentration and finite bed inventory

Choose `sedimentPimpleFoam` and use **Concentration and bed inventory**:

| Control | Meaning / units |
| --- | --- |
| Initial suspended concentration | Uniform internal `C`, kg/m³ |
| Incoming concentration | Sediment entering the selected inlet, kg/m³ |
| Initial bed inventory | Uniform storage on selected bed walls, kg/m² |
| Settling model | Ferguson–Church estimate or prescribed constant speed |
| Constant settling speed | m/s; used only with the constant model |
| Schmidt numbers | Positive dimensionless diffusion parameters |
| Critical Shields parameter | Dimensionless erosion threshold |
| Erosion scale | kg/m²/s; zero disables erosion |
| Excess-Shields exponent | Dimensionless erosion law exponent |

The writer defines all `C` and `Mbed` patch conditions for the actual mesh (or
the starter's block dictionary before meshing). Beds use the solver's required
mixed `C` condition and finite fixed-value `Mbed` storage. Open patches use
`inletOutlet` with `phiSed`: the selected inlet receives the specified incoming
concentration, while other openings receive zero sediment on reversal. Other
walls are impermeable. Empty/symmetry/cyclic conditions follow mesh metadata.
`Mbed` internal values are unused and set to zero.

This editor deliberately replaces sediment boundary conditions and uniform
initial concentrations/storage. Review custom spatial distributions before
using it. See the [solver guide](sedimentPimpleFoam.md) for equations and budgets.

## Sand-parcel feed and optional resuspension

Choose `sandParcelPimpleFoam` and use **Parcel inlet and resuspension**.
The initial cloud is empty; the inlet introduces weighted computational parcels.
The GUI does not create an initial cloud at specified particle positions.

| Control | Meaning / units |
| --- | --- |
| Total inlet sediment feed | Total mass across the transect, kg/s |
| Computational parcels per second | Sampling rate; independent of physical grain count |
| Feed start / duration | Seconds; total prescribed mass = rate × duration |
| Uniform / Rouse distribution | Random sampling in height; Rouse weights heights using settling/shear |
| Transect bed origin | XYZ in mesh coordinates, metres, at the base of the inlet transect |
| Width direction / width | Horizontal direction along the inlet and positive width in metres |
| Depth / lower / upper feed heights | Heights above the origin in metres; `0 < lower < upper < depth` |
| Rouse shear estimate | Prescribed shear velocity in m/s or local bed estimate |
| Rouse shear reference bed | One of the selected sticking bed walls |
| Shear sampling length | Positive local bed sampling length, metres |

Gravity defines the vertical direction. The native injector checks that the
rectangle is fully on a suitable vertical inlet with inward water flow. GUI
range/name checks cannot establish that geometric coverage. Use a **3-D mesh**;
the parcel setup refuses empty patches. The default example origin is
`(0, 0.05, 0)`, width direction `(0, 1, 0)`, width `0.04 m` and depth `0.1 m`.
Reposition these values for your river mesh; they are not automatically inferred
from the DEM or patch bounds.

Selected beds **stick**: parcels become inactive and retain their represented
mass. Other walls rebound with the generated coefficients `e=1`, `mu=0`; other
open patches allow parcels to escape. The writer supports one injector named
`feed`, and preserves existing force, dispersion and integration settings.
Configure additional injectors or other rebound laws directly in the dictionary.

Enable **resuspension of deposited parcels** to choose a threshold:

| Threshold | Critical-value units |
| --- | --- |
| Shields | Dimensionless |
| Bed shear stress | Pa |
| Near-bed velocity | Tangential bed-owner-cell speed, m/s; mesh dependent |

Set release-rate scale in 1/s, excess-threshold exponent and minimum bed
residence in seconds. Above threshold, release is stochastic according to the
integrated release hazard; exceeding the threshold does not release every
parcel immediately. Normal launch speed is in m/s. Lift height in metres
overrides lift in particle diameters when positive. Released parcels resume
native transport and may redeposit. All released mass comes from deposited
parcels; no new sediment is created. The process is disabled by default.
See [particle resuspension](sandParcelPimpleFoam.md) for the equations,
near-bed estimate and restart requirements.

## River terrain and custom meshes

The Geometry tab can send its **currently transformed** surface to Mesh
Generation. Mesh Generation writes actual `blockMeshDict` and basic
`snappyHexMeshDict` files, and copies the active surface to `constant/triSurface`.
Background mesh patches are named `xMin`, `xMax`, `yMin`, `yMax`, `zMin`, `zMax`.
Review their wall/open types and refinement settings. Basic snappy generation
supports one loaded geometry, implicit feature snapping and no boundary layers.
Run `snappyHexMesh -overwrite` on a deliberately prepared fresh case outside the
GUI; the Execution tab currently launches background `blockMesh` only.

Choose and verify `locationInMesh` in the intended retained water region.
Inside/outside checks require a closed manifold surface and a point strictly
inside the background domain, away from its surface. Automatic point selection
can fail for thin/disconnected regions; set a manual point and review the
retained region. Validating one point does not prove the entire mesh is suitable.

Terrain conversion respects rotated/sheared raster transforms, pixel centres,
projected XY units and downsampling. It requires a projected CRS. Z elevations
are assumed to be in metres before the explicit Z scale is applied. Geographic
latitude/longitude rasters must be reprojected first. NoData is refused by
default; explicitly choosing minimum-elevation fill changes the terrain and
requires review. Extrusion closes the surface below its minimum elevation.
Terrain conversion runs synchronously and can take time on large rasters.

After changing mesh patch names, import them again in Boundary Conditions.
For a complete starter whose flow fields still have old patch names, explicitly
select **Rebuild ALL fluid patch conditions for a changed mesh**, review every
patch's replacement form, then write. This replaces all flow boundaries with
those templates while preserving internal fields. Coupled/mapped advanced
conditions require manual editing. Then refresh the River Sediment patch list,
select the actual inlet/bed names, reposition the particle transect if used, and
write sediment setup again. Turbulence fields and advanced physical/numerical
settings remain your responsibility.

## QAQC, backups and continuing a run

Case checks read the selected folder and its configured initial/restart time,
not the working directory or unsaved forms. They check required files, solver
agreement, basic dimensions, patch coverage/types, common turbulence fields,
executable availability, MPI decomposition and parcel restart metadata. Native
`checkMesh` follows. Read warnings: unknown models or dictionaries the lightweight
ASCII parser cannot inspect require manual verification. A passed check does
not establish physical calibration, stability or mesh/time-step convergence.

GUI edits back up existing target files under `.ofsolvers-backups/edit-*/` in
the selected case before atomically replacing them. Multi-file writers prepare
contents first and restore already changed targets on a write failure. Retain
backups when restoring an earlier setup. Included/generated dictionaries are
refused by editors rather than flattened; configure such cases manually.

To restart, retain complete saved fields/cloud/bed history and set `startFrom
latestTime` and a later `endTime` in `system/controlDict`. The sediment initial
setup writer refuses a selected nonzero restart time. For changed initial
conditions use a fresh case. Preserve parcel feed and resuspension settings for
a reproducible continuation; see the solver-specific guides before intentionally
changing history/settings.

For MPI, choose the number of ranks and a decomposition axis, then **Write
decomposition and run decomposePar** on a fresh undecomposed case. The GUI uses
native `simple` decomposition. Existing processor folders are protected.
Run the solver with the same rank count. Restarts use consistent per-rank times
and metadata. **Reconstruct latest results** gathers output fields; it does not
convert distributed bed history into a portable serial parcel restart. Continue
parcel/resuspension restarts with the existing rank decomposition.

**Stop running command** terminates the native process group, including MPI
children. Case selection and editing tabs are disabled while a command runs.
HPC scheduler submission is explicitly unavailable in this GUI; use your
approved remote/scheduler workflow.

## Developer verification

Install the terrain requirements as well to run the DEM tests. Then:

```bash
python -m pip install pytest
python -m pytest -q
# Linux, built solvers and activated OpenFOAM required:
xvfb-run -a python tests/openfoam/validate_gui.py
```

The GUI integration check creates fresh temporary cases and tests both starter
workflows, initial/flow boundary writers, mesh QAQC, native solver runs,
conservation, serial restart, two-rank parcel resuspension/restart/reconstruction,
generated block/snappy mesh dictionaries, and a native parcel run after rebuilding
the flow and sediment conditions for a custom mesh. It prints the artifact directory.
Xvfb is only needed for headless GUI testing; a normal desktop uses its display.
