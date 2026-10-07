# Getting started with the sediment solvers

[Repository home](../README.md) · [Concentration solver](sedimentPimpleFoam.md) ·
[Particle solver](sandParcelPimpleFoam.md) · [River GUI](gui-user-guide.md)

## Choose a solver

Use **sedimentPimpleFoam** for suspended sediment concentration `C` in kg/m³,
settling, deposition and erosion from a finite bed inventory `Mbed` in kg/m².
Use **sandParcelPimpleFoam** for sand trajectories represented by weighted
computational parcels, a specified inlet mass feed and optional resuspension of
previously deposited parcels. A parcel can represent many physical grains.

Both solve incompressible transient flow with PIMPLE and transport dilute sediment
without feedback on the flow. They keep the mesh and bed elevation fixed. Their
individual guides explain assumptions, equations and input units.

## Get the files and activate OpenFOAM

The supported development/runtime environment is **OpenFOAM.com/OpenCFD v1912**
on Linux, with matching development headers, `lnInclude` directories, `wmake`,
a C++ compiler and OpenFOAM libraries. The tested build uses double precision
and 32-bit labels. Installing only runtime binaries may not provide enough files
to compile these custom solvers. A newer distribution package is not automatically
compatible with v1912. The repository does not install OpenFOAM itself.

On a Linux or WSL machine with that environment installed, get the repository:

```bash
cd ~
git clone https://github.com/byuill/OFSolvers.git
cd OFSolvers
```

For an existing checkout, use `git pull` from its directory. Activate the matching
OpenFOAM installation in each new terminal. Replace the example installation path
below with the actual path on your machine:

```bash
source /path/to/OpenFOAM-v1912/etc/bashrc
echo "$WM_PROJECT_VERSION"
command -v wmake
command -v blockMesh
```

The version should be `v1912` or `1912`. Build and check the executable locations:

```bash
./Allwmake
command -v sedimentPimpleFoam
command -v sandParcelPimpleFoam
```

`Allwmake` builds both executables into the activated OpenFOAM user application
directory (`FOAM_USER_APPBIN`). Run the solvers from a terminal or configure
and run them using the desktop GUI's **River Sediment** and **Execution** tabs.
See the [GUI guide](gui-user-guide.md) for Python dependencies and the workflow.

In the **prepared Codex cloud environment**, use the existing checkout and its
activation helper instead of the clone and normal-installation activation steps:

```bash
cd /workspace/OFSolvers
source scripts/openfoam-env.sh
./Allwmake
```

That helper depends on the matching source/runtime files already prepared under
`/workspace`; copying the helper to a different computer does not install them.

## Windows and WSL

On a managed Windows computer without administrator rights, use an IT-approved
remote Linux installation/desktop or ask IT to provision WSL. The cloud folders
are remote; installing WSL is unnecessary to inspect code on GitHub or download
results. Follow the installation steps below only where your IT policy permits.

Run the solvers inside Linux, such as WSL2. If WSL is not installed, open an
administrator PowerShell window and run:

```powershell
wsl --install -d Ubuntu
```

Finish any requested restart and Linux account setup, then open the Ubuntu terminal.
Install the matching OpenCFD v1912 development environment there and follow the
Linux steps above. Package availability varies by distribution; verify the version
before building. The commands using `source`, `./Allwmake`, `blockMesh` and solver
names belong in the Linux terminal.

If you cloned into `~/OFSolvers`, open that folder in Windows File Explorer from
the WSL terminal with:

```bash
cd ~/OFSolvers
explorer.exe .
```

It is also available through `\\wsl.localhost\<distribution>\home\<username>\OFSolvers`.
Keep the working checkout and simulation files in the Linux filesystem when possible.
Folders such as `/workspace/OFSolvers` in the cloud are on the remote cloud machine;
they are not folders on your Windows C: drive. View the pushed source and guides
on [GitHub](https://github.com/byuill/OFSolvers), or clone/download them locally.
Simulation results must be downloaded separately; they are not included in a code push.

## Run the supplied examples

From the repository root, create a results parent next to the checkout and run
each example into a new or empty directory:

```bash
run_root="$(dirname "$PWD")/ofsolvers-runs"
mkdir -p "$run_root"
./tutorials/sedimentChannel/Allrun "$run_root/sediment-example"
./tutorials/sandParcelChannel/Allrun "$run_root/parcel-example"
```

Each script copies its tutorial, runs `blockMesh`, `checkMesh` and the solver,
and prints the result path. It refuses a nonempty destination; choose a new name
for another run. With no destination argument, the script uses a temporary `/tmp`
directory. Copy results you want to retain out of temporary/cloud storage before
that environment is discarded.

Inside each result directory:

| Path | What it contains |
| --- | --- |
| `0/` | Initial velocity, pressure and any sediment fields |
| `constant/` | Material/model settings and generated mesh |
| `system/` | Time controls, numerical schemes, solver tolerances and mesh definition |
| `log.blockMesh`, `log.checkMesh`, `log.solver` | Mesh generation, mesh checks and simulation diagnostics |
| Numeric time directories | Written flow/sediment fields and parcel data |

Only configured output times are saved. A solver log can include time steps that
do not have a corresponding result directory. `endTime`, `writeInterval`, `deltaT`
and adaptive time-step controls are in `system/controlDict`.

For a custom simulation, copy an untouched tutorial into a fresh directory, edit
its dictionaries, then run mesh generation and the corresponding solver manually.
See the recipes in [sedimentPimpleFoam](sedimentPimpleFoam.md#configure-your-own-case)
and [sandParcelPimpleFoam](sandParcelPimpleFoam.md#configure-your-own-case).

## View the results in ParaView

Create an empty `.foam` marker inside the desired result directory:

```bash
touch "$run_root/sediment-example/case.foam"
touch "$run_root/parcel-example/case.foam"
```

Open the appropriate marker in ParaView, select the available mesh regions and
fields in the OpenFOAM reader, click **Apply**, then select a saved time. If using
ParaView on Windows, open the WSL result folder with `explorer.exe .`, or copy/
download the complete result directory to Windows and open its `case.foam` file.
The marker alone does not contain the results.

For the concentration solver, display `C` in the water. Select the **bed boundary**
to view `Mbed`; its internal cell values are unused. `sedimentFlux` is a numerical
mass rate per face, rather than a concentration. For the particle solver, enable
the reader's `sandCloud` Lagrangian region and select its parcel fields; use point
rendering or a Glyph filter to make parcels visible. `active=0` marks deposited
parcels and `active=1` marks mobile ones. Each point is a computational parcel,
not necessarily a single grain. Saved positions can show snapshots or animation;
they are not automatically continuous trajectory lines.

## Restart, parallel runs and troubleshooting

Use the solver-specific restart instructions and retain the **complete** saved
time directory. The concentration solver needs `C` and `Mbed`; the particle solver
needs native cloud files and uniform metadata, including resuspension history when
that process is enabled. MPI workflows are in each solver guide.

| Symptom | What to check |
| --- | --- |
| `wmake` or `blockMesh` not found | Activate the matching OpenFOAM environment in this terminal. |
| Build reports an unsupported version | Check `WM_PROJECT_VERSION`; the wrapper accepts v1912 only. |
| Compilation cannot find OpenFOAM headers/libraries | Check that matching development/source files and generated `lnInclude` directories are installed. |
| Solver executable not found after a successful build | Check `FOAM_USER_APPBIN` and ensure that directory is on `PATH`. |
| `Allrun` refuses the destination | Choose a new or empty result directory. |
| Case exits with `FOAM FATAL ERROR` | Read `log.solver`; check required fields, patch names, units and model settings against the solver guide. |
| No parcel resuspension occurs | Enable the process, use a sticking bed, check `maxThresholdRatio > 1`, minimum resting time and release rate. |
| Restart reports missing bed/cloud history | Restore the complete saved time directory; follow the explicit initialization option only when first enabling parcel resuspension on older results. |

## Optional validation

With OpenFOAM activated, these commands run actual solver cases in fresh temporary
directories and print their artifact locations:

```bash
python3 tests/openfoam/validate.py
python3 tests/openfoam/validate_parcels.py
python3 tests/openfoam/validate_resuspension.py
```

They check implementation behavior, conservation, restart and MPI, with additional
model-specific checks. They require Python 3, MPI and the built solvers. The original
Python application tests can be run separately with `python3 -m pytest -q` when
their Python dependencies are installed. The [GUI integration check](gui-user-guide.md#developer-verification)
also exercises actual native workflows. These checks do not calibrate the physical
models or replace mesh/time-step convergence studies.
