# sedimentInterFoam: free-surface suspended sediment

The castle flume's 0.5 m preset uses OpenCFD **v1912 interFoam** hydrodynamics:
MULES water/air volume fraction, surface tension, gravity, mixture momentum and
pressure, with a new one-way suspended-sediment equation and finite bed inventory.
Build with `./Allwmake` after activating matching v1912. In addition to standard
headers/libraries, the matching upstream `applications/solvers/multiphase/interFoam`
and `VoF` headers must exist under `$FOAM_SOLVERS`; the prepared source environment
includes them. Upstream solver code and modifications are GPL-3.0-or-later.

For water fraction α, C is sediment concentration per **water** volume (kg/m³),
not concentration per mixture volume. The solver advances

```
d[(α + ε) C]/dt + div[(alphaPhiWater + ε phi + α_face Ws·Sf) C]
                  - div[(α + ε) (nuWater/ScM + nut/ScT) grad(C)] = 0
```

Here `alphaPhiWater` is the same bounded/compressed water flux that advanced α,
`phi` is mixture volume flux, `Ws` is downward settling velocity, and **ε = 1e-8**
is an explicit dry-cell capacity regularization. It prevents zero diagonals in
air while keeping the transport conservative. Budgets include εC; it is not
removed by clipping. Concentration can be nonzero in numerically dry cells;
interpret `(alpha.water + 1e-8)*C` when inspecting mixture sediment mass. Assess
regularization sensitivity before studying very dilute interface transport; ε is
currently a compile-time constant in `createSediment.H`.
The model does not track airborne grains or sediment buoyancy effects on flow.

Advection/diffusion remain impermeable at inactive walls. At selected bed walls,
the local wet fraction multiplies excess-Shields erosion and settling deposition.
Erosion cannot exhaust more than the saved inventory in a time step. The bed
update uses the actual final scalar-equation flux, and the global budget reports
suspended mass, bed mass, external flux, residual and minimum C each step.
Sediment advances once after the flow PIMPLE loop; outer iterations cannot count
bed exchange repeatedly. No negative-concentration clipping hides budget errors.

Only a **static mesh, Euler physical time and one alpha subcycle** are supported.
Unsupported local-time stepping, moving meshes and alpha subcycling are rejected.
The generated cases use two MULES corrections, interface Courant ≤ 0.25 and a
separate settling time-step limit. This model is a dilute fixed-bed approximation;
validate water mass, sediment conservation, interface movement and wetting before
using predictions quantitatively. See [castle flume workflow](castle-flume.md).
