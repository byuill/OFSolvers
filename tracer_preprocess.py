"""Read-only carrier inspection and particle-specific setup suggestions."""
from dataclasses import dataclass, replace
from pathlib import Path
import math
import re

from foam_io import value, boundary_patches, entries, scalar
from large_tracer import Distribution


def carrier_patches(case):
    case=Path(case)
    for name in ('boundary','boundary.gz'):
        p=case/'constant/polyMesh'/name
        if p.is_file(): return boundary_patches(p)
    from sediment_setup import case_patches
    return case_patches(case)


def numeric_times(case):
    result=[]
    for p in Path(case).iterdir():
        try: t=float(p.name)
        except ValueError: continue
        if math.isfinite(t) and p.is_dir() and ((p/'U').is_file() or (p/'U.gz').is_file()):
            result.append((t,p))
    result.sort()
    if any(a[0]==b[0] for a,b in zip(result,result[1:])):
        raise ValueError('Carrier time directories must have unique numeric values; remove aliases such as 0 and 0.0.')
    return result


def _number(text,name):
    x=float(value(text,name).split()[-1])
    if not math.isfinite(x) or x<=0: raise ValueError(f'{name} must be positive.')
    return x


def _vector(text):
    numbers=tuple(float(x) for x in text.strip().removeprefix('(').removesuffix(')').split())
    if len(numbers)!=3 or not all(math.isfinite(x) for x in numbers):
        raise ValueError('Expected three finite coordinates.')
    return numbers


@dataclass
class CarrierInspection:
    solver: str
    patches: list
    times: list
    alpha_fields: list
    bounds: tuple | None
    water_level: float | None
    properties: dict
    meshed: bool
    notes: list


def inspect_carrier(case):
    case=Path(case)
    control=(case/'system/controlDict').read_text(encoding='utf-8')
    solver=value(control,'application')
    patches=carrier_patches(case)
    saved=numeric_times(case)
    alpha=set()
    for _,folder in saved:
        alpha.update(p.name.removesuffix('.gz') for p in folder.glob('alpha.*') if p.is_file())
    bounds=None; notes=[]
    try:
        block=(case/'system/blockMeshDict').read_text(encoding='utf-8')
        vertices=value(block,'vertices')
        points=[_vector('('+v+')') for v in re.findall(r'\(([^()]+)\)',vertices)]
        if not points: raise ValueError('No literal block vertices')
        keys=entries(block)
        # v1912 prefers scale and accepts the legacy convertToMeters alias.
        scale_key=next((key for key in ('scale','convertToMeters') if key in keys),None)
        factor=scalar(block,scale_key) if scale_key else 1
        if factor<=0: factor=1  # v1912 treats nonpositive scale as no scaling.
        points=[tuple(factor*x for x in p) for p in points]
        bounds=(tuple(min(p[k] for p in points) for k in range(3)),
                tuple(max(p[k] for p in points) for k in range(3)))
        notes.append('Extents come from blockMesh vertices; initialization preview checks the actual mesh and water field.')
    except (OSError,ValueError):
        notes.append('No literal blockMesh extents available. Set the seed box manually in Particle settings.')
    props={}
    try:
        transport=(case/'constant/transportProperties').read_text(encoding='utf-8')
        if alpha:
            # Field ordering does not identify the liquid: alpha.air must never
            # silently become a water fraction or supply the water density.
            phase='water'
            for phase_name,rho_name,mu_name in ((phase,'rho_water','mu_water'),('air','rho_air','mu_air')):
                try:
                    body=value(transport,phase_name)[1:-1]
                    props[rho_name]=_number(body,'rho')
                    props[mu_name]=props[rho_name]*_number(body,'nu')
                except ValueError: notes.append(f'Check material properties for phase {phase_name}.')
        else:
            props['rho_water']=_number(transport,'rho')
            props['mu_water']=props['rho_water']*_number(transport,'nu')
    except (OSError,ValueError): notes.append('Carrier material properties could not all be inferred; review densities and viscosities.')
    try: props['gravity']=_vector(value((case/'constant/g').read_text(encoding='utf-8'),'value'))
    except (OSError,ValueError): notes.append('Gravity could not be inferred; review the particle gravity vector.')
    water=None
    if alpha:
        try:
            fields=(case/'system/setFieldsDict').read_text(encoding='utf-8')
            boxes=re.findall(r'\bbox\s+\(([^()]+)\)\s+\(([^()]+)\)',fields)
            if boxes: water=_vector('('+boxes[0][1]+')')[2]
        except (OSError,ValueError): pass
    elif bounds: water=bounds[1][2]
    meshed=any((case/'constant/polyMesh'/n).is_file() for n in ('points','points.gz'))
    return CarrierInspection(solver,patches,[t for t,_ in saved],sorted(alpha),bounds,water,props,meshed,notes)


def suggested_placement(config,inspection,preset,water_level):
    """Suggestions are explicit user actions; native preview validates geometry."""
    if inspection.bounds is None: raise ValueError('Set seed and retention boxes manually; this case has no literal blockMesh extents.')
    lo,hi=inspection.bounds
    if not math.isfinite(water_level) or not lo[2]<water_level<=hi[2]:
        raise ValueError('Water elevation must lie above the domain minimum and at or below its top.')
    span=[b-a for a,b in zip(lo,hi)]
    seed_min=tuple(lo[k]+.1*span[k] for k in range(2))+(lo[2],)
    seed_max=tuple(hi[k]-.1*span[k] for k in range(2))+(water_level-.05*(water_level-lo[2]),)
    updates=dict(seed_min=seed_min,seed_max=seed_max)
    if preset=='bed':
        # A slightly lower ray end intersects the bed; centers must remain in the box.
        updates.update(placement='bed',density=Distribution('uniform',low=1050,high=1200,median=1100),
                       seed_min=seed_min[:2]+(lo[2]-.01*span[2],))
        beds=tuple(p['name'] for p in inspection.patches
                   if (p['name'] in config.bed_patches or 'bed' in p['name'].lower())
                   and (config.boundaries[p['name']].behavior=='contact'
                        if p['name'] in config.boundaries else p['type']=='wall'))
        if not beds: raise ValueError('Select a contact seed surface in Particle boundaries before using bed placement.')
        updates['bed_patches']=beds
    elif preset=='buoyant':
        updates.update(placement='volume',density=Distribution('uniform',low=500,high=950,median=750))
    else: raise ValueError('Unknown particle placement preset.')
    result=replace(config,**updates)
    result.validate()
    return result
