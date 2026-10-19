# Repository review — October 2026

The review covered the four C++ solvers and their build files, Python workbench
and case writers, geometry/terrain and mesh tools, execution and restart paths,
trajectory/flow visualization, tutorials, tests, and operating documentation.
Changes target reproducible defects and measured costs. This is a source review
and regression exercise, not a certification of every physical configuration.

## Corrections and improvements

| Area | Finding and resulting behavior |
| --- | --- |
| Particle integration | The frozen-drag displacement formula subtracted large terminal-velocity terms. Acceleration-based exponential integration with small-argument series now approaches the ballistic limit without cancellation. Velocity and position still use the same frozen linearized force law. |
| Particle time steps | A tiny remainder before a carrier snapshot incorrectly triggered a minimum-step failure. Snapshot/output remainders are accepted when the physical stability/displacement limits permit the entire remainder. A small output remainder no longer excuses a still smaller unstable step. |
| Carrier replay | Only snapshots bracketing the requested tracking interval require phase fields. Final output no longer loads an unrelated future field. Duplicate numeric directory aliases, such as `0` and `0.0`, are rejected. |
| Replay efficiency | Advancing a bracket retains its already-loaded upper frame as the next lower frame, including interpolation and gradients. Integration and collision-index buffers are reused between steps. |
| Particle preprocessing | Literal `blockMeshDict` bounds now honor `scale` and legacy `convertToMeters`, with v1912 precedence and nonpositive-scale behavior. Seed suggestions use metre coordinates. |
| Compressed cases | Preflight and patch import recognize gzip mesh and field files. Fluid boundary edits retain gzip compression, preserve initialized internal fields and other patches, and back up original compressed bytes. Uncompressed files take precedence, matching OpenFOAM lookup. |
| MPI restart | The effective restart time comes from processor directories. A newer root reconstruction no longer prevents a valid earlier processor restart. Rank count must be a positive integer; inconsistent processor times remain errors. |
| Atmospheric boundaries | The VOF fluid editor now uses `prghTotalPressure` for physical total pressure, accounting for hydrostatic head in `p_rgh`. The single-phase pressure template is unchanged. |
| Mesh operations | Generated castle/scour `Allmesh` scripts and the workbench blockMesh action refuse existing meshes, including compressed meshes. Re-running a setup script cannot silently remesh and reinitialize an existing simulation. |
| Trajectory loading | Numeric columns are read into NumPy instead of retaining a dictionary of boxed floats per record. Duplicate/missing time–ID states, invalid IDs/flags, nonfinite required states, and changing particle material/eligibility metadata are rejected. Legacy CSV column layouts remain supported. |
| Automation | GitHub Actions runs the Python/Qt/terrain regression suite on Python 3.10 and 3.12 without a display. Native solver tests remain separate and require OpenCFD v1912. |

The free-surface castle outlet was also compared with a flux-pressure outlet in
a 1,536-cell background-channel probe at 0.3 m/s through 0.1 s. Peak speeds were
0.30690 and 0.30710 m/s. This did not justify changing the generated outlet.
The probe is a startup comparison without an obstacle, not a developed flume
validation. Existing castle geometry and refinement settings were retained.

## Validation

Validation used Linux, Python 3.12 and native OpenCFD v1912, double precision
with 32-bit labels. The initial Python suite had 106 passing tests. The expanded
suite has **123 passing tests**. All four solvers build successfully.

| Executable check | Passing check groups |
| --- | ---: |
| `validate.py`: Eulerian conservation, settling, restart, mesh and turbulence | 14 |
| `validate_parcels.py`: feed profiles, mass, deposition and restart | 13 |
| `validate_resuspension.py`: release statistics, history, MPI migration/restart | 8 |
| `validate_large_tracers.py`: mechanics, contact cutoff, preview and new numerical regressions | 29 |
| `validate_gui.py`: workbench-to-solver, MPI/restart, snappy dictionaries | 5 |
| `validate_tracer_workbench.py`: actual placement preview and carrier preservation | 1 |
| `validate_operations.py`: gzip case, native boundary edit/pressure and remesh protection | 4 |

The weak-drag reference trajectory has a final position error of
1.11 × 10⁻¹⁶ m. The compressed-case atmospheric check used 25 Pa total pressure
at z=1 m with air density 1.2 kg/m³: saved `p_rgh` spans 36.75917–36.77200 Pa,
consistent with the 36.772 Pa hydrostatic value and a small dynamic correction.
The mesh hashes are unchanged after solving and after a refused remesh.

The workbench integration includes a real 12-particle initialization preview,
no transport advancement, and preservation of carrier files/control settings.
The existing flow-workbench tests include serial and two-rank solver runs,
resuspension restart/reconstruction and native blockMesh/snappyHexMesh execution.
Headless Qt warns about unavailable OpenGL contexts; these runs exercise widget
and case operations rather than an interactive 3D display.

Run the checks from the repository root after installing Python dependencies:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
source /path/to/OpenFOAM-v1912/etc/bashrc
./Allwmake
python tests/openfoam/validate.py
python tests/openfoam/validate_parcels.py
python tests/openfoam/validate_resuspension.py
python tests/openfoam/validate_large_tracers.py
QT_QPA_PLATFORM=offscreen python tests/openfoam/validate_gui.py
QT_QPA_PLATFORM=offscreen python tests/openfoam/validate_tracer_workbench.py
QT_QPA_PLATFORM=offscreen python tests/openfoam/validate_operations.py
```

Each native script creates its own temporary case directory and prints its
location. Selected results and the check transcripts are retained in
[`validation/code-review-2026-10`](validation/code-review-2026-10).
GitHub Actions runner results are separate from the local checks above.

## Trajectory loader measurement

A synthetic completed run contained 999 particle IDs at 200 output times:
199,800 records, with the current solver's 21 CSV columns. Separate Python
processes loaded the same file with the previous and revised implementations;
both produced an identical `(200, 999, 11)` state array.

| Loader | Elapsed time | Peak process RSS |
| --- | ---: | ---: |
| Previous | 7.20 s | 260.95 MiB |
| Revised | 1.06 s | 76.22 MiB |

This local measurement is approximately 6.8× faster with 71% lower peak memory.
Elapsed times depend on the machine and load. It measures CSV loading, not CFD
or browser playback. Full replay arrays and HTML still grow with saved states;
choose a practical particle output interval for long simulations.

## Remaining limits

The native checks use v1912 APIs; other OpenFOAM versions require adaptation.
Particle dynamics retain one-way unresolved sphere coupling, a static carrier
mesh and serial replay. Contact and partial-submergence approximations do not
replace resolved-fluid particle interactions or experimental calibration.
Sediment solvers retain a fixed bed and their existing transport closures.

The Python dictionary editor handles explicit ASCII dictionaries, including
gzip-compressed ASCII fields. Binary fields and generated/included dictionaries
still need appropriate native tools or manual preparation. Large field edits
can occupy the GUI thread. Terrain elevation units must be supplied consistently
with the metre-based case. Full mesh/time-step sensitivity and hydraulic
validation remain case-specific work; the large castle meshes were not
regenerated in this review.
