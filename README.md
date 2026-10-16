# OFSolvers — river modeling workbench

OFSolvers combines an OpenFOAM desktop preprocessor, **run QAQC and debugging
tools**, and two custom sediment-transport solvers. It focuses on open-channel
and river applications: geometry, terrain, meshing, hydraulic boundaries,
turbulence, suspended sediment and sand parcels. Use **ParaView** to visualize
saved flow and particle fields; the GUI's post-processing focuses on numerical
stability, conservation, computational performance and error investigation.

**New to the desktop application? Start with the [GUI README](GUI_README.md).**
It covers installation, the workflow sidebar, first cases, live diagnostics,
log comparisons and troubleshooting. **GUI guide** in the application opens
that README as well.

## What is included

| Component | What you can do | Guide |
| --- | --- | --- |
| River preprocessing GUI | Prepare geometry/terrain and mesh inputs, edit actual patch boundaries, configure turbulence and sediment cases | [GUI README](GUI_README.md), [detailed setup guide](docs/gui-user-guide.md) |
| Synthetic river designer | Generate channel/obstacle STLs, graded block/snappy meshes, rough-wall checks and discharge/sediment boundary plans | [River design workflow](docs/river-design.md) |
| Execution and QAQC | Check case files and native mesh quality, run serial/MPI jobs, preserve command logs and numerical settings | [GUI run workflow](GUI_README.md#execution-logs-and-run-settings) |
| Run diagnostics | Plot linear residuals, Courant numbers, time-step/cost histories and sediment budgets; compare logs and inspect error context | [Diagnostics and interpretation](GUI_README.md#run-diagnostics) |
| `sedimentPimpleFoam` | Eulerian suspended concentration, settling and conservative exchange with a finite bed inventory | [Concentration solver](docs/sedimentPimpleFoam.md), [example](tutorials/sedimentChannel) |
| `sandParcelPimpleFoam` | Weighted sand parcels, uniform/Rouse inlet feed and optional threshold-based bed resuspension | [Parcel solver](docs/sandParcelPimpleFoam.md), [example](tutorials/sandParcelChannel) |

Both custom solvers use **one-way dilute transport, a fixed water-domain mesh
and fixed bed elevation**. They do not evolve a free surface or river bathymetry.
Native free-surface solvers such as `interFoam` can be selected for complete
compatible cases; the sediment starter cases do not supply a coupled VOF or
morphodynamic model. Residual convergence and conservation checks support
numerical QAQC; accuracy needs mesh/time-step studies and physical validation.

## Launch the GUI

Use Python **3.10+** on a desktop with working Qt/VTK graphics. On Linux or WSL:

```bash
git clone https://github.com/byuill/OFSolvers.git
cd OFSolvers
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-gui.txt
# Optional GeoTIFF/DEM processing:
python -m pip install -r requirements-terrain.txt
python main.py
```

Preprocessing and saved-log analysis do not need OpenFOAM. Running mesh utilities
or solvers requires an activated installation on the GUI's execution machine.
See the [GUI README](GUI_README.md#installation-and-launch) for Windows and
Linux/WSL details. This is a **PyQt6 desktop application**, not a web application.

## Build and run the sediment solvers

The supported native API is **OpenFOAM.com/OpenCFD v1912**, with development
headers, `lnInclude` directories, `wmake`, a compiler and matching libraries.
`Allwmake` checks the version. Other OpenFOAM releases/forks need adaptation
and validation; installing the GUI dependencies does not install OpenFOAM.

```bash
source /path/to/OpenFOAM-v1912/etc/bashrc
./Allwmake
./tutorials/sedimentChannel/Allrun /path/to/new/sediment-case
./tutorials/sandParcelChannel/Allrun /path/to/new/parcel-case
```

Choose new/empty output folders. Each `Allrun` prints the result location and
retains native utility/solver logs. With no output argument it uses `/tmp`.
See [Getting started](docs/getting-started.md) for installation, persistent
results, ParaView, restarts and parallel execution. The helper
`scripts/openfoam-env.sh` applies only to an environment already provisioned
with its matching source/runtime files; it does not install OpenFOAM.

## Development and repository map

```bash
python -m pip install pytest
python -m pytest -q
```

GUI tests use an offscreen Qt platform when no display is attached. Native
solver integration checks in [tests/openfoam](tests/openfoam) are separate and
require the activated, built OpenFOAM environment.

- [GUI entry point](main.py), [window/navigation](main_window.py),
  [GUI README](GUI_README.md).
- [River geometry and engineering checks](river_design.py), [case exporter](river_case.py),
  [design interface](river_design_tab.py), [native validation](tests/openfoam/validate_river_design.py).
- [Log parser and QAQC checks](run_diagnostics.py),
  [diagnostics interface](diagnostics_tab.py), [execution worker](execution_tab.py).
- [Solver sources](applications/solvers), [tutorials](tutorials),
  [solver and setup guides](docs/getting-started.md).

The guides, code and tutorials are versioned together. Executables are built
locally; case results and logs belong in separate run folders. The OpenFOAM
solver code is GPL-3.0-or-later; see each solver's `COPYING` file.
