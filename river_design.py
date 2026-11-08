"""Synthetic river geometry and engineering checks, in metres and seconds.

X is downstream, Y is transverse and Z is up. Manning estimates are planning
quantities, not a turbulence/roughness conversion or a backwater calculation.
"""
from dataclasses import asdict, dataclass, field
import math
import re

import numpy as np
import pyvista as pv


@dataclass
class Channel:
    length: float = 40.
    width: float = 6.
    depth: float = 1.2
    bank_slope: float = 1.5  # horizontal : vertical
    bed_slope: float = .001
    bed_elevation: float = 0.
    freeboard: float = .8
    mode: str = 'fixed'
    stations: int = 81
    cross_points: int = 33

    @property
    def height(self):
        return self.depth + (self.freeboard if self.mode == 'vof' else 0)


@dataclass
class MeshDesign:
    method: str = 'auto'
    base_size: float = .4
    wall_size: float = .15
    first_height: float = .01
    growth: float = 1.2
    layers: int = 4
    cells_per_obstacle: int = 10
    max_level: int = 6
    max_cells: int = 2_000_000


@dataclass
class Hydraulics:
    solver: str = 'sedimentPimpleFoam'
    discharge: float = 3.
    manning_n: float = .03
    nu: float = 1e-6
    bed_ks: float = .002
    bank_ks: float = .002
    roughness_cs: float = .5
    turbulence: str = 'kOmegaSST'
    intensity: float = .05
    length_scale: float = .1
    inlet_c: float = .05
    initial_c: float = 0.
    bed_inventory: float = 1.
    grain_mm: float = .2
    particle_density: float = 2650.
    water_density: float = 1000.
    parcel_rate: int = 1000
    parcel_profile: str = 'Rouse'
    parcel_lower_fraction: float = .01
    parcel_upper_fraction: float = .9
    end_time: float = 100.
    max_co: float = .5
    outlet_pressure: float = 0.  # kinematic p reference, NOT a water level
    hydrograph: list = field(default_factory=list)  # time_s, Q_m3_s, C_kg_m3


@dataclass
class Obstacle:
    name: str = 'pier'
    kind: str = 'cylinder'
    x: float = 20.
    y: float = 0.
    length: float = .8
    width: float = .8
    height: float = .7
    angle: float = 0.
    ks: float = 0.
    rows: int = 1
    columns: int = 1
    spacing: float = .6
    attach_bank: bool = False


@dataclass
class RiverDesign:
    channel: Channel = field(default_factory=Channel)
    mesh: MeshDesign = field(default_factory=MeshDesign)
    hydraulic: Hydraulics = field(default_factory=Hydraulics)
    obstacles: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(Channel(**data['channel']), MeshDesign(**data['mesh']),
                   Hydraulics(**data['hydraulic']), [Obstacle(**item) for item in data.get('obstacles', [])])


def section(width, slope, depth):
    area = depth*(width+slope*depth)
    wetted = width+2*depth*math.sqrt(1+slope*slope)
    return area, area/wetted, width+2*slope*depth


def normal_depth(q, width, slope, bed_slope, manning_n):
    if min(q, width, bed_slope, manning_n) <= 0 or slope < 0:
        raise ValueError('Normal depth needs positive Q, width, friction slope and Manning n.')
    def capacity(depth):
        area, radius, _ = section(width, slope, depth)
        return area*radius**(2/3)*math.sqrt(bed_slope)/manning_n
    low, high = 0., 1.
    while capacity(high) < q:
        high *= 2
        if high > 1e6:
            raise ValueError('Normal-depth search exceeded its physical range.')
    for _ in range(90):
        middle = (low+high)/2
        if capacity(middle) < q: low = middle
        else: high = middle
    return (low+high)/2


def critical_depth(q,width,slope):
    if min(q,width)<=0 or slope<0:raise ValueError('Critical depth needs positive Q/width and a nonnegative bank slope.')
    low,high=1e-12,1.
    def froude_squared(depth):
        area,_,top=section(width,slope,depth)
        return q*q*top/(9.81*area**3)
    while froude_squared(high)>1:
        high*=2
        if high>1e6:raise ValueError('Critical-depth search exceeded its physical range.')
    for _ in range(90):
        middle=(low+high)/2
        if froude_squared(middle)>1:low=middle
        else:high=middle
    return (low+high)/2


def graded_cells(length, first, maximum, growth):
    """Exact-length graded stack with bounded adjacent expansion and outer size.

    The first cell is at most the requested height. Explicit one-cell grading
    sections avoid ambiguity between total expansion and adjacent expansion.
    """
    if min(length, first, maximum) <= 0 or not 1 < growth <= 1.3 or first > maximum:
        raise ValueError('Require positive spacings, first <= bulk spacing, and 1 < growth <= 1.3.')
    count = max(2, math.ceil(length/maximum))
    for _ in range(10000):
        if length/count <= first:
            return np.full(count, length/count)
        def total(ratio):
            if abs(ratio-1)<1e-14:return first*count
            power = count*math.log(ratio)
            return math.inf if power > 700 else first*math.expm1(power)/math.expm1(math.log(ratio))
        if total(growth) < length:
            count += 1
            continue
        low, high = 1., growth
        for _ in range(60):
            ratio = (low+high)/2
            if total(ratio) < length: low = ratio
            else: high = ratio
        ratio = (low+high)/2
        if first*ratio**(count-1) > maximum*(1+1e-8):
            count += 1
            continue
        sizes = first*ratio**np.arange(count)
        sizes *= length/sizes.sum()
        return sizes
    raise ValueError('Too many graded cells; increase first-cell or bulk spacing.')


def expanded_obstacles(design):
    result = []
    for item in design.obstacles:
        if item.kind == 'vegetation':
            for i in range(item.rows):
                for j in range(item.columns):
                    values = asdict(item)
                    values.update(name=f'{item.name}_{i}_{j}', kind='cylinder',
                                  x=item.x+(i-(item.rows-1)/2)*item.spacing,
                                  y=item.y+(j-(item.columns-1)/2)*item.spacing)
                    result.append(Obstacle(**values))
        else:
            result.append(item)
    return result


def bed_z(design, x, y):
    channel = design.channel
    result = channel.bed_elevation-channel.bed_slope*np.asarray(x) + np.zeros_like(np.asarray(y), dtype=float)
    for item in design.obstacles:
        if item.kind != 'bedform': continue
        a = (np.asarray(x)-item.x)/item.length+.5
        b = (np.asarray(y)-item.y)/item.width+.5
        result = result+np.where((a>0)&(a<1)&(b>0)&(b<1),
            item.height*np.sin(np.pi*a)**2*np.sin(np.pi*b)**2, 0)
    return result


def selected_method(design):
    if design.mesh.method != 'auto': return design.mesh.method
    return 'snappy' if design.obstacles or design.channel.bank_slope>2 else 'block'


def refinement_level(base_size, target):
    return max(0, math.ceil(math.log2(base_size/target)-1e-12))


def obstacle_cell_size(item,mesh,channel):
    dimensions=[item.width,min(item.height,channel.height)]
    if item.kind not in ('cylinder','vegetation'):dimensions.append(item.length)
    return min(dimensions)/mesh.cells_per_obstacle


def validate_design(design):
    c, m, h = design.channel, design.mesh, design.hydraulic
    for value in (c.stations,c.cross_points,m.layers,m.cells_per_obstacle,m.max_level,m.max_cells,h.parcel_rate):
        if not isinstance(value,int) or isinstance(value,bool):raise ValueError('Resolution, layer, refinement and count inputs must be integers.')
    for group in (c, m, h):
        for name, item in asdict(group).items():
            if isinstance(item, (int,float)) and (isinstance(item,bool) or not math.isfinite(item)):
                raise ValueError(f'{name} must be finite.')
    if min(c.length,c.width,c.depth,m.base_size,m.wall_size,m.first_height,h.discharge,h.nu,h.manning_n,h.end_time) <= 0:
        raise ValueError('Channel dimensions, spacings, discharge, viscosity, Manning n and duration must be positive.')
    if min(c.bank_slope,c.bed_slope,c.freeboard,h.bed_ks,h.bank_ks,h.inlet_c,h.initial_c,h.bed_inventory) < 0:
        raise ValueError('Slopes, freeboard, roughness and sediment values cannot be negative.')
    if c.mode not in ('fixed','vof') or (c.mode == 'vof' and c.freeboard <= 0):
        raise ValueError('Choose fixed domain or VOF geometry with positive air freeboard.')
    if c.stations < 3 or c.cross_points < 3 or c.stations*c.cross_points > 250000:
        raise ValueError('Use at least three surface points in each direction and at most 250,000 points.')
    if m.method not in ('auto','block','snappy') or not 1 < m.growth <= 1.3:
        raise ValueError('Choose a mesh method and adjacent expansion in (1, 1.3].')
    if m.first_height > min(m.wall_size,m.base_size) or m.layers not in range(0,13):
        raise ValueError('First-cell/layer height must fit the surface/core size; use 0–12 snappy layers.')
    if m.cells_per_obstacle < 6 or not 0 <= m.max_level <= 8 or m.max_cells < 1000:
        raise ValueError('Use at least six cells per obstacle, refinement levels 0–8 and a viable cell budget.')
    if h.solver not in ('sedimentPimpleFoam','sandParcelPimpleFoam','pimpleFoam'):
        raise ValueError('Choose a supported fixed-domain solver; VOF exports are geometry/meshing inputs only.')
    if h.turbulence not in ('kEpsilon','kOmegaSST') or not 0 < h.intensity <= .3 or h.length_scale <= 0:
        raise ValueError('Select a supported RANS model, 0 < turbulence intensity <= 0.3 and a positive length scale.')
    if not 0 < h.roughness_cs <= 1 or min(h.max_co,h.grain_mm,h.water_density,h.parcel_rate) <= 0 or h.particle_density <= h.water_density:
        raise ValueError('Check roughness coefficient, Courant target, sediment density/diameter and parcel rate.')
    if h.grain_mm/1000>=.1*c.depth:raise ValueError('Suspended/parcel grains must be small compared with water depth; this is not a boulder/bedload solver.')
    if h.solver=='sandParcelPimpleFoam' and h.inlet_c<=0:
        raise ValueError('The river parcel starter uses a positive constant inlet mass feed Q × C; use a manual no-injection cloud for a zero-feed resuspension study.')
    if h.parcel_profile not in ('uniform','Rouse') or not 0<h.parcel_lower_fraction<h.parcel_upper_fraction<1:
        raise ValueError('Choose a uniform/Rouse parcel feed and 0 < lower feed fraction < upper fraction < 1.')
    if h.solver=='sandParcelPimpleFoam' and max(c.depth*h.parcel_lower_fraction,2*h.grain_mm/1000)>=c.depth*h.parcel_upper_fraction:
        raise ValueError('Keep parcel feed heights away from the bed/top and above the grain-scale clearance.')
    names = set()
    for item in design.obstacles:
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',item.name) or item.name in names or item.name in ('river','bed','bankLeft','bankRight','inlet','outlet','lid'):
            raise ValueError('Obstacle names must be unique safe words, separate from channel patch names.')
        names.add(item.name)
        if item.kind not in ('cylinder','box','boulder','bedform','vegetation'):
            raise ValueError('Choose a cylinder, box, boulder, bedform or rigid vegetation patch.')
        if any(not isinstance(value,int) or isinstance(value,bool) for value in (item.rows,item.columns)):
            raise ValueError('Vegetation rows and columns must be integer counts.')
        if not all(math.isfinite(v) for v in (item.x,item.y,item.length,item.width,item.height,item.angle,item.ks,item.spacing)):
            raise ValueError('Obstacle coordinates and dimensions must be finite.')
        if min(item.length,item.width,item.height) <= 0 or item.ks < 0:
            raise ValueError('Obstacle dimensions must be positive and roughness nonnegative.')
        if item.kind == 'vegetation' and (min(item.rows,item.columns) < 1 or item.rows*item.columns > 100 or item.spacing <= item.width):
            raise ValueError('Vegetation needs 1–100 separated rigid stems and spacing greater than stem diameter.')
        if item.kind == 'bedform' and (abs(item.angle) > 1e-9 or item.height > .4*c.depth):
            raise ValueError('Bedforms are axis-aligned smooth bars/dunes and must be lower than 40% of inlet depth.')
        if item.kind=='bedform' and item.ks!=0:
            raise ValueError('Bedforms share the bed patch; set their row Ks to zero and use the global bed Ks for unresolved texture.')
        if item.kind=='bedform' and min(item.length/(c.length/(c.stations-1)),item.width/(c.width/(c.cross_points-1)))<4:
            raise ValueError('A bedform needs at least four terrain intervals in each footprint direction; increase terrain resolution.')
    obstacles = expanded_obstacles(design)
    if len({item.name for item in obstacles}) != len(obstacles):
        raise ValueError('Expanded vegetation stem names collide with another obstacle.')
    if len(obstacles) > 150:
        raise ValueError('Limit explicitly resolved solids to 150; unresolved vegetation needs a separate drag/porous model.')
    extents = []
    for item in obstacles:
        angle=math.radians(item.angle)
        dx,dy=((abs(item.length*math.cos(angle))+abs(item.width*math.sin(angle)))/2,
               (abs(item.length*math.sin(angle))+abs(item.width*math.cos(angle)))/2) if item.kind == 'box' else (
                   (item.width/2,item.width/2) if item.kind == 'cylinder' else
                   (math.hypot(item.length*math.cos(angle),item.width*math.sin(angle))/2,
                    math.hypot(item.length*math.sin(angle),item.width*math.cos(angle))/2) if item.kind=='boulder' else (item.length/2,item.width/2))
        radius = (math.hypot(item.length,item.width)/2 if item.kind == 'box' else
                  max(item.length,item.width)/2 if item.kind=='boulder' else max(dx,dy))
        if item.x-dx < 2*m.base_size or item.x+dx > c.length-2*m.base_size:
            raise ValueError(f'{item.name}: leave at least two base cells between the obstacle and inlet/outlet.')
        if abs(item.y)+dy >= c.width/2 and not item.attach_bank:
            raise ValueError(f'{item.name}: footprint reaches a bank; reduce/move it or explicitly mark a bank-attached structure.')
        if abs(item.y)-dy >= c.width/2+c.bank_slope*c.height:
            raise ValueError(f'{item.name}: the obstacle misses the computational channel.')
        if item.kind=='box' and item.height>=c.height and item.y-dy<=-c.width/2-c.bank_slope*c.height and item.y+dy>=c.width/2+c.bank_slope*c.height:
            raise ValueError(f'{item.name}: a full-height, full-width solid blocks the channel. Provide an opening or use an appropriate overtopping/domain design.')
        if item.kind == 'bedform' and (abs(item.y)+item.width/2 >= c.width/2 or item.attach_bank):
            raise ValueError('Bedforms must taper to zero inside the flat bed, away from bank toes.')
        if item.kind != 'bedform':
            for other, other_radius in extents:
                if math.hypot(item.x-other.x,item.y-other.y) < radius+other_radius:
                    raise ValueError(f'{item.name} and {other.name} overlap (conservative footprint check); separate their solids.')
            extents.append((item,radius))
            target = obstacle_cell_size(item,m,c)
            if refinement_level(m.base_size,target) > m.max_level:
                raise ValueError(f'{item.name}: resolving its width needs a finer background or higher refinement cap; do not model unresolved stems as resolved geometry.')
    if selected_method(design) == 'block' and design.obstacles:
        raise ValueError('Bedforms and solid obstacles require snappyHexMesh. Use auto or snappy.')
    if selected_method(design)=='block' and c.bank_slope>2:
        raise ValueError('Single-block mapping is too skewed for banks flatter than 2H:1V; select snappy/auto.')
    if h.hydrograph:
        rows = np.asarray(h.hydrograph,dtype=float)
        if rows.ndim != 2 or rows.shape[1] != 3 or len(rows) < 2 or not np.isfinite(rows).all():
            raise ValueError('Hydrograph columns are time_s, Q_m3_s, C_kg_m3, with at least two finite rows.')
        if rows[0,0] != 0 or rows[-1,0] < h.end_time or np.any(np.diff(rows[:,0]) <= 0) or np.any(rows[:,1:] < 0):
            raise ValueError('Hydrograph times must increase from zero through endTime; Q and C cannot be negative.')
        if h.solver == 'sandParcelPimpleFoam':
            raise ValueError('The custom parcel inlet uses constant mass feed. Use a constant boundary plan or the Eulerian concentration solver for hydrographs.')
    return design


def analyze_design(design):
    validate_design(design)
    c, m, h = design.channel, design.mesh, design.hydraulic
    area, radius, top = section(c.width,c.bank_slope,c.depth)
    qmax = max([h.discharge]+[row[1] for row in h.hydrograph])
    velocity = qmax/area
    friction_slope = (h.manning_n*velocity/radius**(2/3))**2
    ustar = math.sqrt(9.81*radius*friction_slope)
    grain=h.grain_mm/1000;relative=h.particle_density/h.water_density-1
    settling=relative*9.81*grain**2/(18*h.nu+math.sqrt(.75*relative*9.81*grain**3))
    vertical = graded_cells(c.height,m.first_height,m.base_size,m.growth)
    lateral_half = graded_cells(c.width/2,m.first_height*math.sqrt(1+c.bank_slope**2),m.base_size,m.growth)
    lateral = np.r_[lateral_half,lateral_half[::-1]]
    method = selected_method(design)
    stream_size=min(m.base_size,6*min(float(vertical[0]),float(lateral[0]))) if method=='block' else m.base_size
    nx = math.ceil(c.length/stream_size)
    bed_y = float(vertical[0]/2) if method == 'block' else (m.first_height/2 if m.layers else m.wall_size/2)
    bank_y = float(lateral[0]/2/math.sqrt(1+c.bank_slope**2)) if method == 'block' else bed_y
    wall_level = refinement_level(m.base_size,m.wall_size)
    if wall_level > m.max_level:
        raise ValueError('Requested bed/bank spacing exceeds the refinement cap.')
    estimate = nx*len(lateral)*len(vertical) if method == 'block' else math.prod(
        math.ceil(size/m.base_size)+4 for size in (c.length,top+2*c.bank_slope*(c.height-c.depth),c.height+c.bed_slope*c.length))
    if estimate > m.max_cells:
        raise ValueError(f'Initial mesh estimate {estimate:,} exceeds the {m.max_cells:,} cell budget; coarsen the design.')
    issues = []
    def note(level, text): issues.append({'level':level,'text':text})
    if velocity/math.sqrt(9.81*area/top) > .8:
        note('Review','Froude number is near or above critical. A fixed lid cannot represent drawdown, waves or hydraulic jumps; use a validated free-surface model.')
    if c.depth/m.base_size < 10:
        note('Review','Fewer than ten bulk cells per water depth; refine and perform a depth/time-step convergence study.')
    if method == 'snappy':
        note('Required','Snappy cell count and layer coverage are not guaranteed by these estimates. Inspect native layer statistics, checkMesh, and measured yPlus after solving.')
        note('Quality','Acceptance uses explicit meshQualityDict criteria and topology checks. An optional checkMesh -allGeometry audit can additionally flag concave/warped polyhedra and stretched layer cells; review those diagnostics in a mesh-sensitivity study.')
    if min(bed_y,bank_y)*ustar/h.nu < 30:
        note('Review','Estimated first-centre y+ is below 30. The chosen rough-wall RANS functions assume a log-layer mesh; resolve the wall only with an appropriate low-Re model.')
    if h.bed_ks >= bed_y or h.bank_ks >= bank_y:
        note('Review','Equivalent sand roughness reaches/exceeds the estimated first-centre wall distance. Increase wall distance or revise unresolved roughness; rough-wall clipping can change effective Ks.')
    if bed_y*ustar/h.nu > 300:
        note('Review','Estimated y+ exceeds the common smooth-wall 30–300 range. Rough river walls can require higher values; check Ks/y, wall-layer thickness and sensitivity instead of applying 300 as a universal limit.')
    if m.layers and m.first_height*(m.growth**m.layers-1)/(m.growth-1) > .25*c.depth:
        note('Review','Requested layer stack exceeds one quarter of water depth; opposing layers/gaps may truncate extrusion.')
    if design.obstacles and max(item.ks for item in design.obstacles) > m.first_height/2:
        note('Review','An obstacle roughness exceeds the requested first-layer centre distance.')
    solids = [item for item in expanded_obstacles(design) if item.kind != 'bedform']
    for i,item in enumerate(solids):
        if c.length-item.x < 10*max(item.length,item.width):
            note('Review',f'{item.name}: outlet is within ten obstacle widths; extend the domain and test outlet sensitivity.')
        for other in solids[i+1:]:
            gap = math.hypot(item.x-other.x,item.y-other.y)-(max(item.length,item.width)+max(other.length,other.width))/2
            local = m.base_size/2**max(refinement_level(m.base_size,obstacle_cell_size(item,m,c)),
                                     refinement_level(m.base_size,obstacle_cell_size(other,m,c)))
            if gap/local < 6:
                note('Review',f'{item.name}/{other.name}: fewer than six estimated gap cells; increase clearance or local refinement.')
    for item in design.obstacles:
        if item.kind == 'bedform' and min(item.length/(c.length/(c.stations-1)), item.width/(c.width/(c.cross_points-1))) < 8:
            note('Review',f'{item.name}: fewer than eight terrain samples across its footprint; increase surface resolution to represent its curvature.')
    if 4*radius*velocity/h.nu<2000:
        note('Review','Hydraulic-diameter Reynolds number is low; this rough-wall RANS starter is not a substitute for a suitable laminar/low-Re setup.')
    if max([h.inlet_c,h.initial_c]+[row[2] for row in h.hydrograph])/h.particle_density>.01:
        note('Review','Sediment volume fraction exceeds 1%; assess the one-way dilute-transport assumptions before using this solver.')
    note('Assumption','Shear velocity uses a Manning-based uniform-friction estimate. Manning n is not converted into Ks; resolved bedforms/obstacles must not be counted again as unresolved roughness.')
    note('Boundary','A fixed-domain outlet uses a kinematic-pressure reference, not a prescribed downstream water level. VOF stage/phase boundary conditions require a complete native free-surface case.')
    return dict(method=method, area_m2=area, hydraulic_radius_m=radius, top_width_m=top,
                peak_velocity_m_s=velocity, froude=velocity/math.sqrt(9.81*area/top),
                settling_speed_estimate_m_s=settling,rouse_parameter_estimate=settling/(.41*ustar),
                settling_dt_estimate=.5*min(float(vertical[0]),m.first_height)/settling,
                reynolds=4*radius*velocity/h.nu, friction_slope_estimate=friction_slope, ustar_estimate_m_s=ustar,
                normal_depth_m=normal_depth(qmax,c.width,c.bank_slope,c.bed_slope,h.manning_n) if c.bed_slope else None,
                critical_depth_m=critical_depth(qmax,c.width,c.bank_slope),
                bed_yplus_estimate=bed_y*ustar/h.nu, bank_yplus_estimate=bank_y*ustar/h.nu,
                bed_ks_plus=h.bed_ks*ustar/h.nu, bed_wall_distance_m=bed_y, bank_wall_distance_m=bank_y,
                vertical_cells=vertical.tolist(), lateral_cells=lateral.tolist(), streamwise_cells=nx,
                streamwise_size_m=c.length/nx,
                initial_cells_estimate=estimate, wall_refinement_level=wall_level,
                advective_dt_estimate=h.max_co*min([float(vertical[0]),float(lateral[0]),m.wall_size]
                    +[obstacle_cell_size(item,m,c) for item in solids])/max(velocity,1e-12),
                inlet_sediment_kg_s=h.discharge*h.inlet_c, issues=issues)


def _surface(points, quads):
    return pv.PolyData(np.asarray(points,dtype=float), np.asarray([[4,*face] for face in quads]).ravel()).triangulate().clean(tolerance=1e-10,absolute=True)


def channel_surfaces(design):
    validate_design(design)
    c = design.channel
    xs = np.linspace(0,c.length,c.stations)
    fractions = np.linspace(-1,1,c.cross_points)
    xx, yy = np.meshgrid(xs,fractions*c.width/2,indexing='ij')
    zz = bed_z(design,xx,yy)
    bottom = np.stack([xx,yy,zz],axis=-1)
    top = np.stack([xx,np.broadcast_to(fractions*(c.width/2+c.bank_slope*c.height),xx.shape),
                    np.broadcast_to((c.bed_elevation-c.bed_slope*xs+c.height)[:,None],xx.shape)],axis=-1)
    quads = [(i*c.cross_points+j,i*c.cross_points+j+1,(i+1)*c.cross_points+j+1,(i+1)*c.cross_points+j)
             for i in range(c.stations-1) for j in range(c.cross_points-1)]
    patches = {'bed':_surface(bottom.reshape(-1,3),quads),
               'lid':_surface(top.reshape(-1,3),[tuple(reversed(face)) for face in quads])}
    for side,column in [('bankLeft',0),('bankRight',-1)]:
        points = np.vstack([bottom[:,column],top[:,column]])
        faces = [(i,i+1,c.stations+i+1,c.stations+i) for i in range(c.stations-1)]
        patches[side] = _surface(points, faces if side == 'bankLeft' else [tuple(reversed(face)) for face in faces])
    for name,index in [('inlet',0),('outlet',-1)]:
        points = np.vstack([bottom[index],top[index]])
        faces = [(j,j+1,c.cross_points+j+1,c.cross_points+j) for j in range(c.cross_points-1)]
        patches[name] = _surface(points, [tuple(reversed(face)) for face in faces] if name == 'inlet' else faces)
    closed = pv.merge(list(patches.values()),merge_points=True).clean(tolerance=1e-9,absolute=True)
    if not closed.is_manifold or closed.n_open_edges or closed.volume <= 0:
        raise ValueError('Generated channel boundary is not a closed manifold fluid volume.')
    if np.min(top[:,:,2]-bottom[:,:,2]) <= .1*c.depth:
        raise ValueError('Bedforms leave too little water/air-column clearance.')
    return patches, closed


def obstacle_surfaces(design):
    result = {}
    for item in expanded_obstacles(design):
        if item.kind == 'bedform': continue
        z = float(bed_z(design,item.x,item.y))
        embed = max(.05*item.height,.01)
        if item.kind == 'cylinder':
            mesh = pv.Cylinder(center=(item.x,item.y,z+(item.height-embed)/2),direction=(0,0,1),
                               radius=item.width/2,height=item.height+embed,resolution=32,capping=True)
        elif item.kind == 'boulder':
            mesh = pv.ParametricEllipsoid(item.length/2,item.width/2,item.height,u_res=32,v_res=24)
            mesh.rotate_z(item.angle,inplace=True)
            mesh.translate((item.x,item.y,z),inplace=True)
        else:
            mesh = pv.Box(bounds=(-item.length/2,item.length/2,-item.width/2,item.width/2,-embed,item.height))
            mesh.rotate_z(item.angle,inplace=True)
            mesh.translate((item.x,item.y,z),inplace=True)
        mesh = mesh.triangulate().clean()
        if not mesh.is_manifold or mesh.volume <= 0:
            raise ValueError(f'{item.name}: obstacle is not a closed manifold solid.')
        result[item.name] = mesh
    return result


def fluid_point(design, channel, obstacles):
    c=design.channel
    for xf in (.15,.35,.65,.85,.5):
        for yf in (0.,-.25,.25):
            point=(c.length*xf,c.width*yf,c.bed_elevation-c.bed_slope*c.length*xf+.7*c.height)
            sample=pv.PolyData(np.array([point]))
            if not sample.select_interior_points(channel,method='cell_locator',check_surface=True)['selected_points'][0]: continue
            if any(sample.select_interior_points(mesh,method='cell_locator',check_surface=True)['selected_points'][0] for mesh in obstacles.values()): continue
            return point
    raise ValueError('No connected fluid seed found; move obstacles or reduce blockage.')


def ascii_stl(regions):
    parts=[]
    for name, mesh in regions.items():
        triangles=mesh.triangulate()
        parts.append('solid '+name)
        for ids in triangles.faces.reshape(-1,4)[:,1:]:
            vertices=triangles.points[ids]
            normal=np.cross(vertices[1]-vertices[0],vertices[2]-vertices[0])
            length=np.linalg.norm(normal)
            if length <= 1e-14:
                raise ValueError(f'{name}: degenerate STL triangle.')
            parts.append(' facet normal '+' '.join(f'{v:.12g}' for v in normal/length)+'\n  outer loop')
            parts.extend('   vertex '+' '.join(f'{v:.12g}' for v in point) for point in vertices)
            parts.append('  endloop\n endfacet')
        parts.append('endsolid '+name)
    return '\n'.join(parts)+'\n'
