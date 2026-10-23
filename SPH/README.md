# SPH

Standalone research prototype for 3-D sediment transport in a small flume, with a parameter GUI and a central X-Z view.

**Status: untested and not physically validated.** The authoring environment could not execute the source. No 30-second simulation has been completed. Review and test it before using results quantitatively.

## Default configuration

| Parameter | Default |
|---|---:|
| Flume length x width x height | 0.20 x 0.05 x 0.10 m |
| Grain diameter | 2 mm |
| Initial sediment thickness | 6 mm / 3 grain layers |
| Water surface above the floor | 95 mm |
| Target mean flow velocity | 0.65 m/s |
| Simulated duration | 30 s |
| Water point spacing | 4 mm |
| CRM point spacing | 1.5 mm |

Velocity is a configurable transport-driving target; actual transport intensity needs verification.

## Models and boundaries

Water uses weakly compressible, volume-averaged SPH with unresolved two-way sediment coupling and a simple mixing-length turbulence closure.

- **DEM:** Individual spherical grains with normal spring-dashpot contact, tangential contact history, Coulomb friction and rotation. Bed thickness can be reduced to one grain layer.
- **CRM:** A custom continuum approximation with effective granular pressure and regularized mu(I) viscosity. This is exploratory for thin submerged beds and is not Project Chrono CRM. At least four numerical points through the bed are required.

Both phases are periodic in X. Crossing an end changes only X, preserving Y, Z, velocity, spin and identity. Bottom and sidewalls are solid; the top is open. Flow is driven by a body force in a periodic reach.

## Installation

The standalone model is in the `SPH/` directory on the `SPH` branch. It does not require OpenFOAM.

Use Python 3.11 and preferably a virtual environment:

```bash
cd SPH
python -m venv .venv
```

Activate with `.\.venv\Scripts\Activate.ps1` in Windows PowerShell or `source .venv/bin/activate` on macOS/Linux.

```bash
python -m pip install -r requirements.txt
```

The GUI requires Tkinter, normally included with the standard Windows Python installer. Some Linux distributions package Tkinter separately.

## Run

Start with the short numerical smoke checks, then launch the GUI:

```bash
python flume.py --smoke
python flume.py
```

Headless examples:

```bash
python flume.py --headless --mode DEM --backend CPU --duration 30
python flume.py --headless --mode CRM --backend Serial --duration 0.01
python flume.py --headless --mode DEM --backend CUDA --duration 30
```

The GUI supports Serial, CPU, CUDA and CPU-affinity execution. CUDA requires a compatible NVIDIA GPU and driver. CPU affinity requires supported operating-system APIs and valid logical CPU IDs. Unavailable CUDA raises an error instead of silently falling back.

Override headless parameters with a JSON file:

```json
{
  "backend": "CPU-affinity",
  "threads": 2,
  "affinity": "0,1",
  "duration": 0.01,
  "layers": 3
}
```

```bash
python flume.py --headless --config config.json
```

## Results

Each run writes a timestamped folder under `outputs/flume_runs/` with configuration, metadata, CSV diagnostics, central X-Z slab snapshots and full initial/final particle states. Use **Open saved slice** to inspect a saved slice. Smoke outputs use `outputs/flume_smoke/`; failed runs write an error report when possible.

## Numerical limitations

The prototype uses fixed smoothing lengths, softened DEM contacts, unresolved fluid-grain coupling and an approximate turbulence closure. Regularized CRM permits creep and does not resolve individual-grain saltation.

A 30-second run may require millions of steps. Start with short runs, then check hydrostatic equilibrium, mass conservation, timestep/resolution convergence, overlap and measured transport benchmarks. Passing smoke checks alone does not establish physical accuracy. The dependency list is not a verified lockfile.

## Background

- [Robinson et al., SPH-DEM coupling](https://arxiv.org/abs/1301.0752)
- [Mu(I) granular rheology](https://arxiv.org/abs/cond-mat/0612110)
- [LAMMPS granular contact documentation](https://docs.lammps.org/pair_granular.html)
