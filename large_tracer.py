"""Configuration for largeTracerFoam: individual physical spheres in saved flow."""
from dataclasses import asdict, dataclass, field
import argparse
import json
import math
from pathlib import Path
import re

from foam_io import write_case_files


@dataclass(frozen=True)
class Distribution:
    kind: str = 'uniform'
    low: float = .02
    high: float = .04
    median: float = .03
    sigma: float = .25  # standard deviation of ln(d), not of d
    values: tuple = ()
    weights: tuple = ()

    def validate(self):
        if self.kind not in ('constant', 'uniform', 'lognormal', 'discrete'):
            raise ValueError('Distribution must be constant, uniform, lognormal or discrete.')
        if self.kind == 'discrete':
            if not self.values or len(self.values) != len(self.weights):
                raise ValueError('Discrete values and weights must have the same nonzero length.')
            if (not all(math.isfinite(v) and v > 0 for v in self.values)
                    or not all(math.isfinite(v) and v >= 0 for v in self.weights)
                    or not math.isfinite(sum(self.weights)) or sum(self.weights) <= 0):
                raise ValueError('Discrete values must be positive; weights nonnegative with positive finite sum.')
            return
        if not math.isfinite(self.low) or self.low <= 0:
            raise ValueError('Distribution minimum/value must be positive and finite.')
        if self.kind == 'constant':
            return
        if not math.isfinite(self.high) or self.high < self.low:
            raise ValueError('Distribution maximum must be finite and >= minimum.')
        if self.kind == 'lognormal' and not (
                self.low < self.high and math.isfinite(self.median)
                and self.low <= self.median <= self.high
                and math.isfinite(self.sigma) and self.sigma > 0):
            raise ValueError('Bounded lognormal needs min < max, median within bounds, and positive log sigma.')

    def foam(self):
        self.validate()
        if self.kind == 'constant':
            body = f'value {self.low:.16g};'
        elif self.kind == 'discrete':
            body = f'values {foam_vector(self.values)}; weights {foam_vector(self.weights)};'
        else:
            body = f'min {self.low:.16g}; max {self.high:.16g};'
            if self.kind == 'lognormal':
                body += f' median {self.median:.16g}; sigma {self.sigma:.16g};'
        return f'{{ type {self.kind}; {body} }}'


def foam_vector(values):
    return '(' + ' '.join(f'{v:.16g}' for v in values) + ')'


@dataclass(frozen=True)
class TracerConfig:
    count: int = 25
    seed: int = 42
    diameter: Distribution = field(default_factory=Distribution)
    density: Distribution = field(default_factory=lambda: Distribution(low=500, high=950, median=750))
    seed_min: tuple = (1.2, .1, -.18)
    seed_max: tuple = (1.8, .4, -.03)
    placement: str = 'volume'
    bed_patches: tuple = ('bed',)
    min_water_fraction: float = .99
    retention_min: tuple = (1, 0, -.2)
    retention_max: tuple = (2, .5, 0)
    rim_elevation: float = 0
    start_time: float = 0
    end_time: float = 5
    output_interval: float = .05
    max_delta_t: float = .002
    frozen_flow: bool = False
    flow_time: float = 0
    alpha_field: str = 'alpha.water'
    run_name: str = 'tracers01'
    initial_velocity: str = 'zero'
    rho_water: float = 1000
    mu_water: float = .001
    rho_air: float = 1.2
    mu_air: float = 1.8e-5
    added_mass: float = .5
    restitution: float = .1
    friction: float = .4
    collisions: bool = True
    mobilization_diameters: float = 1
    volume_samples: int = 128

    def validate(self):
        for name in ('frozen_flow','collisions'):
            if type(getattr(self,name)) is not bool:
                raise ValueError(f'{name} must be true or false.')
        if type(self.count) is not int or not 1 <= self.count <= 999:
            raise ValueError('Select 1–999 physical particles.')
        if type(self.seed) is not int or not 0 <= self.seed <= 2147483647:
            raise ValueError('Seed must be an integer in 0–2147483647.')
        self.diameter.validate()
        self.density.validate()
        for name in ('seed_min', 'seed_max', 'retention_min', 'retention_max'):
            values = getattr(self, name)
            if len(values) != 3 or not all(math.isfinite(v) for v in values):
                raise ValueError(f'{name} needs three finite coordinates.')
        for lo, hi in ((self.seed_min, self.seed_max), (self.retention_min, self.retention_max)):
            if any(b <= a for a, b in zip(lo, hi)):
                raise ValueError('Each box maximum must exceed its minimum.')
        if self.placement not in ('volume', 'bed') or self.initial_velocity not in ('zero', 'flow'):
            raise ValueError('Select volume/bed placement and zero/flow initial velocity.')
        safe_word = r'[A-Za-z_][A-Za-z0-9_.-]*'
        if (not re.fullmatch(safe_word, self.run_name) or not re.fullmatch(safe_word, self.alpha_field)
                or not self.bed_patches or not all(re.fullmatch(safe_word, p) for p in self.bed_patches)):
            raise ValueError('Use simple names for the run, alpha field, and bed patches.')
        if not (math.isfinite(self.start_time) and math.isfinite(self.end_time)
                and 0 <= self.start_time < self.end_time):
            raise ValueError('Tracking needs 0 ≤ start time < end time.')
        for name in ('output_interval', 'max_delta_t', 'rho_water', 'mu_water',
                     'rho_air', 'mu_air', 'mobilization_diameters'):
            v = getattr(self, name)
            if not math.isfinite(v) or v <= 0:
                raise ValueError(f'{name} must be positive and finite.')
        for name in ('added_mass', 'friction', 'flow_time'):
            v = getattr(self, name)
            if not math.isfinite(v) or v < 0:
                raise ValueError(f'{name} must be nonnegative and finite.')
        if not math.isfinite(self.rim_elevation):
            raise ValueError('Rim elevation must be finite.')
        if not math.isfinite(self.restitution) or not 0 <= self.restitution <= 1:
            raise ValueError('Restitution must be in [0,1].')
        if not math.isfinite(self.min_water_fraction) or not 0 < self.min_water_fraction <= 1:
            raise ValueError('Initial water fraction must be in (0,1].')
        if type(self.volume_samples) is not int or not 32 <= self.volume_samples <= 8192:
            raise ValueError('Sphere volume samples must be an integer in 32–8192.')

    def foam(self):
        self.validate()
        yes = lambda b: 'true' if b else 'false'
        return f'''FoamFile
{{ version 2.0; format ascii; class dictionary; object largeTracerProperties; }}
// SI units. Each ID is ONE physical sphere, not a weighted parcel.
count {self.count}; seed {self.seed}; runName {self.run_name};
diameter {self.diameter.foam()}
density {self.density.foam()}
seedRegion
{{
 min {foam_vector(self.seed_min)}; max {foam_vector(self.seed_max)};
 placement {self.placement}; bedPatches ({' '.join(self.bed_patches)});
 minWaterFraction {self.min_water_fraction:.16g};
}}
retentionMin {foam_vector(self.retention_min)};
retentionMax {foam_vector(self.retention_max)};
rimElevation {self.rim_elevation:.16g};
startTime {self.start_time:.16g}; endTime {self.end_time:.16g};
outputInterval {self.output_interval:.16g}; maxDeltaT {self.max_delta_t:.16g};
frozenFlow {yes(self.frozen_flow)}; flowTime {self.flow_time:.16g};
alphaField {self.alpha_field}; volumeSamples {self.volume_samples};
initialVelocity {self.initial_velocity};
rhoWater {self.rho_water:.16g}; muWater {self.mu_water:.16g};
rhoAir {self.rho_air:.16g}; muAir {self.mu_air:.16g};
g (0 0 -9.81); addedMassCoefficient {self.added_mass:.16g};
restitution {self.restitution:.16g}; friction {self.friction:.16g};
particleCollisions {yes(self.collisions)};
mobilizationDiameters {self.mobilization_diameters:.16g};
'''

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        for name in ('diameter', 'density'):
            if name in data:
                distribution = dict(data[name])
                for key in ('values','weights'):
                    if key in distribution: distribution[key] = tuple(distribution[key])
                data[name] = Distribution(**distribution)
        for name in ('seed_min','seed_max','retention_min','retention_max','bed_patches'):
            if name in data: data[name] = tuple(data[name])
        result = cls(**data)
        result.validate()
        return result


def save_tracers(case, config):
    config.validate()
    case = Path(case).resolve()
    if not (case/'system/controlDict').is_file():
        raise ValueError('Select an OpenFOAM case with system/controlDict.')
    if (case/'postProcessing/largeTracers'/config.run_name).exists():
        raise ValueError('This run already has output. Choose a new run name.')
    return write_case_files(case, {
        'constant/largeTracerProperties': config.foam(),
        'tracers.json': json.dumps({'schema': 1, 'settings': asdict(config)}, indent=2)+'\n',
    })


def load_tracers(case):
    data = json.loads((Path(case)/'tracers.json').read_text())
    if data.get('schema') != 1:
        raise ValueError('Unknown tracer settings schema.')
    return TracerConfig.from_dict(data['settings'])


def preflight(case, config):
    """Cheap checks; native solver also validates mesh, fields and wet placement."""
    config.validate()
    case = Path(case)
    if not (case/'constant/polyMesh/points').is_file() and not (case/'constant/polyMesh/points.gz').is_file():
        raise ValueError('Mesh the case first. Reconstruct parallel carrier fields before replay.')
    saved = []
    for path in case.iterdir():
        try:
            t = float(path.name)
        except ValueError:
            continue
        if math.isfinite(t) and path.is_dir() and ((path/'U').is_file() or (path/'U.gz').is_file()):
            saved.append((t,path))
    saved.sort()
    if config.frozen_flow:
        selected = [p for t,p in saved if abs(t-config.flow_time) < 1e-9]
        if not selected:
            raise ValueError('Frozen flow time must match a saved U snapshot.')
    else:
        if len(saved) < 2 or saved[0][0] > config.start_time+1e-10 or saved[-1][0] < config.end_time-1e-10:
            raise ValueError('Saved U snapshots must cover the tracking interval. Select frozen flow explicitly to replay one snapshot.')
        selected = [p for _,p in saved]
    if config.alpha_field != 'none' and any(not (p/config.alpha_field).is_file()
            and not (p/(config.alpha_field+'.gz')).is_file() for p in selected):
        raise ValueError(f'Save {config.alpha_field} at every carrier U time, or select fully submerged single-phase flow.')
    if (case/'postProcessing/largeTracers'/config.run_name).exists():
        raise ValueError('Output already exists. Choose a new run name.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path)
    parser.add_argument('--settings', type=Path, help='JSON settings; otherwise use case/tracers.json or defaults')
    parser.add_argument('--check', action='store_true', help='Check saved carrier fields, without modifying case')
    args = parser.parse_args()
    config = (TracerConfig.from_dict(json.loads(args.settings.read_text())) if args.settings else
              load_tracers(args.case) if (args.case/'tracers.json').is_file() else TracerConfig())
    if args.check:
        preflight(args.case,config)
        print('Carrier fields cover the requested replay. Native solver will check geometry and seeding.')
    else:
        save_tracers(args.case,config)
        print(f'Wrote tracer settings. Run: largeTracerFoam -case {args.case}')


if __name__ == '__main__':
    main()
