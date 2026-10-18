# OFSolvers

This repository includes three custom sediment-transport flow solvers and an
individual large-particle replay solver, with example cases and user guides.
The Python/PyQt river workbench configures sediment models, initial and boundary
conditions, mesh dictionaries and case/mesh QAQC, including a free-surface flume.

## User guides

Start with [Getting started](docs/getting-started.md) for installation requirements,
building, running examples, finding files on Windows/WSL and viewing results.
Use the [River GUI guide](docs/gui-user-guide.md) for the desktop setup workflow,
sediment controls and QAQC.

| Solver | Choose it when you need | Guide | Example case |
| --- | --- | --- | --- |
| `largeTracerFoam` | 1–999 physical spheres with size/density distributions, buoyancy, contact, retention events and trajectories in saved flow | [Large tracer guide](docs/largeTracerFoam.md) | [Free-surface scour demo](tutorials/largeTracerScour/Allrun) |
| `sedimentInterFoam` | Moving water–air free surface with phase-weighted suspended sediment and finite bed exchange | [VOF sediment guide](docs/sedimentInterFoam.md) | [Castle flume generator](castle_flume.py) |
| `sedimentPimpleFoam` | A suspended concentration field, settling and conservative exchange with a finite bed inventory | [Eulerian sediment guide](docs/sedimentPimpleFoam.md) | [sedimentChannel](tutorials/sedimentChannel) |
| `sandParcelPimpleFoam` | Individual weighted sand-parcel trajectories, a uniform/Rouse inlet feed and optional threshold-based resuspension | [Lagrangian sand guide](docs/sandParcelPimpleFoam.md) | [sandParcelChannel](tutorials/sandParcelChannel) |

The sediment solvers use one-way dilute transport on a fixed bed.
`largeTracerFoam` replays saved carrier fields with one-way particle dynamics;
large blockage or local particle volume fraction requires a coupled model. Their APIs
have been tested on **OpenFOAM.com/OpenCFD v1912**. The build script checks this
version; other OpenFOAM releases and forks need adaptation and validation.

The [Castle flume preset](docs/castle-flume.md) generates a 3 × 0.25 × 1 m
flume with a centered, arched castle obstacle, sediment settings and local
mesh refinement from the desktop workbench or `castle_flume.py`.

The **Large tracers** workbench tab configures physical particles in an existing
flow case or a generated scour-hole example. It exports per-ID histories, VTK
particles, an interactive browser replay, and central x–z carrier velocity plots.
An optional diameter cutoff restricts noncohesive sediment pair contact to
pairs whose particles are both larger than the selected size.

## Quick start in the prepared cloud environment

```bash
cd /workspace/OFSolvers
source scripts/openfoam-env.sh
./Allwmake
./tutorials/sedimentChannel/Allrun
./tutorials/sandParcelChannel/Allrun
```

Each `Allrun` creates a fresh case under `/tmp` and prints `Results: ...`.
For your own Linux/WSL installation, follow the activation and persistent-result
instructions in [Getting started](docs/getting-started.md).

## Repository contents

- [Solver sources](applications/solvers): C++ code, OpenFOAM build files and licenses.
- [User guides](docs/getting-started.md): workflows, settings, units and model behavior.
- [Tutorials](tutorials): complete runnable case dictionaries and initial fields.
- [Executable integration checks](tests/openfoam): concentration, parcel and
  resuspension checks using the actual OpenFOAM solvers.
- [Allwmake](Allwmake): builds all four solvers after OpenFOAM activation.

The guides, solver sources and tutorials are versioned together in this repository.
Compiled executables are built locally; generated case results are separate from
the source checkout. The OpenFOAM solver code is GPL-3.0-or-later; see each solver's
`COPYING` file.
