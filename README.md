# OFSolvers

This repository includes two custom sediment-transport solvers built from
OpenFOAM's fixed-mesh PIMPLE flow equations, their example cases and user guides.
The Python/PyQt river preprocessing GUI configures both sediment solvers,
initial and boundary conditions, mesh dictionaries and case/mesh QAQC.

## User guides

Start with [Getting started](docs/getting-started.md) for installation requirements,
building, running examples, finding files on Windows/WSL and viewing results.
Use the [River GUI guide](docs/gui-user-guide.md) for the desktop setup workflow,
sediment controls and QAQC.

| Solver | Choose it when you need | Guide | Example case |
| --- | --- | --- | --- |
| `sedimentPimpleFoam` | A suspended concentration field, settling and conservative exchange with a finite bed inventory | [Eulerian sediment guide](docs/sedimentPimpleFoam.md) | [sedimentChannel](tutorials/sedimentChannel) |
| `sandParcelPimpleFoam` | Individual weighted sand-parcel trajectories, a uniform/Rouse inlet feed and optional threshold-based resuspension | [Lagrangian sand guide](docs/sandParcelPimpleFoam.md) | [sandParcelChannel](tutorials/sandParcelChannel) |

Both solvers use one-way dilute sediment transport on a fixed bed. Their APIs
have been tested on **OpenFOAM.com/OpenCFD v1912**. The build script checks this
version; other OpenFOAM releases and forks need adaptation and validation.

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
- [Allwmake](Allwmake): builds both solvers after OpenFOAM activation.

The guides, solver sources and tutorials are versioned together in this repository.
Compiled executables are built locally; generated case results are separate from
the source checkout. The OpenFOAM solver code is GPL-3.0-or-later; see each solver's
`COPYING` file.
