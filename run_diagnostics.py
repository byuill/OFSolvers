"""Incremental, read-only diagnostics for OpenFOAM solver logs (no Qt required)."""
from dataclasses import asdict, dataclass, field
import csv
import math
from pathlib import Path
import re
import os
import tempfile


NUMBER = r'[-+]?(?:\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?|inf|nan)'
TIME = re.compile(r'^Time\s*=\s*('+NUMBER+r')\s*$', re.I)
RESIDUAL = re.compile(r'Solving for\s+(\S+),\s*Initial residual\s*=\s*('+NUMBER+r'),\s*Final residual\s*=\s*('+NUMBER+r'),\s*No Iterations\s+(\d+)', re.I)
COURANT = re.compile(r'^\s*(?:(Sediment|Interface)\s+)?Courant Number\s+mean:\s*('+NUMBER+r')\s+max:\s*('+NUMBER+r')', re.I)
BUDGET = re.compile(r'Sediment budget:\s*suspended=('+NUMBER+r')\s+bed=('+NUMBER+r')\s+externalOut=('+NUMBER+r')\s+residual=('+NUMBER+r')\s+kg;\s*min\(C\)=('+NUMBER+r')', re.I)
INVENTORY = re.compile(r'Sand inventory:\s*mobile=('+NUMBER+r')\s+deposited=('+NUMBER+r')\s+kg', re.I)
CONTINUITY = re.compile(r'continuity errors.*?global\s*=\s*('+NUMBER+r'),\s*cumulative\s*=\s*('+NUMBER+r')', re.I)
EXECUTION = re.compile(r'ExecutionTime\s*=\s*('+NUMBER+r')\s*s\s+ClockTime\s*=\s*('+NUMBER+r')\s*s', re.I)
FATAL = re.compile(r'FOAM FATAL|floating point exception|segmentation fault|MPI_ABORT|\bSIGFPE\b|(?:^|\]\s*|\d+:\s*)ERROR:|error while loading shared libraries|Failed\s+[1-9]\d*\s+mesh checks', re.I)
WARNING = re.compile(r'FOAM Warning|^\s*bounding\s|^\s*\*\*\*|maximum number of iterations|not converg', re.I)


@dataclass
class Step:
    segment: int
    time: float
    delta_t: float | None = None
    co_mean: float | None = None
    co_max: float | None = None
    sediment_co_max: float | None = None
    interface_co_max: float | None = None
    execution_s: float | None = None
    clock_s: float | None = None
    wall_step_s: float | None = None
    continuity_global: float | None = None
    continuity_cumulative: float | None = None
    suspended_kg: float | None = None
    bed_kg: float | None = None
    external_out_kg_s: float | None = None
    budget_residual_kg: float | None = None
    min_c_kg_m3: float | None = None
    mobile_kg: float | None = None
    deposited_kg: float | None = None


@dataclass
class LinearResidual:
    segment: int
    time: float
    field: str
    initial: float
    final: float
    iterations: int


@dataclass
class Limits:
    max_co: float = .5
    residual: float = 1e-6
    budget_kg: float = 1e-10
    negative_c: float = 1e-12


@dataclass
class Finding:
    level: str
    title: str
    evidence: str
    action: str


@dataclass
class RunDiagnostics:
    steps: list = field(default_factory=list)
    residuals: list = field(default_factory=list)
    events: list = field(default_factory=list)
    completed: bool = False
    cancelled: bool = False
    exit_code: int | None = None
    source: str = ''
    _pending: dict = field(default_factory=dict, repr=False)
    _closed: bool = field(default=True, repr=False)
    _line: int = field(default=0, repr=False)
    _context: list = field(default_factory=list, repr=False)
    _context_left: int = field(default=0, repr=False)
    _nonfinite: list = field(default_factory=list, repr=False)
    _new_run: bool = field(default=False, repr=False)

    def _numbers(self, values):
        numbers = [float(item) for item in values]
        if any(not math.isfinite(item) for item in numbers):
            self._nonfinite.append(self._line)
        return numbers

    def feed(self, raw):
        self._line += 1
        text = re.sub(r'\x1b\[[0-9;]*m', '', raw.rstrip('\r\n'))
        if self._context_left:
            self.events.append(f'{self._line}: {text}')
            self._context_left -= 1
        if FATAL.search(text) or WARNING.search(text):
            self.events.extend(self._context[-4:])
            self.events.append(f'{self._line}: {text}')
            self._context_left = 7
        self._context.append(f'{self._line}: {text}')
        self._context = self._context[-4:]
        rank = re.match(r'^\[(\d+)\]\s*', text)
        if rank:
            if int(rank.group(1)) != 0:
                return  # Keep other-rank errors, avoid duplicate global metrics.
            text = text[rank.end():]
        if text.startswith('--- Executing:') or text.strip() in ('Create time', 'Starting time loop'):
            self._new_run = bool(self.steps)
            self._closed = True
            self._pending = {}
            self.completed = False
            self.exit_code = None
            self.cancelled = False
        match = TIME.match(text)
        if match:
            current = self._numbers(match.groups())[0]
            if not math.isfinite(current):
                return
            segment = self.steps[-1].segment if self.steps else 0
            if self.steps and (current <= self.steps[-1].time or self._new_run):
                segment += 1
            self.steps.append(Step(segment, current, **self._pending))
            self._pending = {}
            self._closed = False
            self.completed = False
            self._new_run = False
            return
        step = self.steps[-1] if self.steps else None
        match = COURANT.search(text)
        if match:
            mean, maximum = self._numbers(match.groups()[1:])
            kind = (match.group(1) or '').lower()
            values = ({'sediment_co_max': maximum} if kind == 'sediment' else (
                {'interface_co_max': maximum} if kind == 'interface' else {'co_mean': mean, 'co_max': maximum}))
            if step is None or self._closed:
                self._pending.update(values)
            else:
                for name, value in values.items():
                    setattr(step, name, value)
        match = re.match(r'^deltaT\s*=\s*('+NUMBER+r')', text, re.I)
        if match:
            dt = self._numbers(match.groups())[0]
            if step is None or self._closed:
                self._pending['delta_t'] = dt
            else:
                step.delta_t = dt
        match = RESIDUAL.search(text)
        if match and step is not None:
            initial, final = self._numbers(match.groups()[1:3])
            self.residuals.append(LinearResidual(step.segment, step.time, match.group(1), initial, final, int(match.group(4))))
        for pattern, names in [(BUDGET, ('suspended_kg','bed_kg','external_out_kg_s','budget_residual_kg','min_c_kg_m3')),
                               (INVENTORY, ('mobile_kg','deposited_kg')),
                               (CONTINUITY, ('continuity_global','continuity_cumulative')),
                               (EXECUTION, ('execution_s','clock_s'))]:
            match = pattern.search(text)
            if match and step is not None:
                for name, value in zip(names, self._numbers(match.groups())):
                    setattr(step, name, value)
                if pattern is EXECUTION:
                    previous = next((self.steps[index] for index in range(len(self.steps)-2, -1, -1)
                                     if self.steps[index].segment == step.segment and self.steps[index].clock_s is not None), None)
                    if previous is not None and step.clock_s >= previous.clock_s:
                        step.wall_step_s = step.clock_s-previous.clock_s
                    self._closed = True
        if text.strip() == 'End':
            self.completed = True
            self._closed = True
        match = re.search(r'exit code (-?\d+)', text)
        if match:
            self.exit_code = int(match.group(1))
            self.cancelled = 'Cancelled' in text

    def values(self, name):
        values = [getattr(step, name) for step in self.steps]
        if name in self._pending:
            values.append(self._pending[name])
        return [value for value in values if value is not None and math.isfinite(value)]

    def findings(self, limits=None):
        limits = limits or Limits()
        result = []
        if self.cancelled:
            result.append(Finding('Review', 'Command was canceled', 'The command log has a cancellation footer.',
                'Inspect the last completed time and preserve all fields needed for a restart. The run did not finish normally.'))
        fatal = [line for line in self.events if FATAL.search(line)]
        if fatal or self.exit_code not in (None, 0):
            result.append(Finding('Error', 'Command failed or reported a fatal error',
                fatal[0] if fatal else f'Exit code: {self.exit_code}',
                'Read the Events tab for the first error, dictionary path and stack context. Check missing fields, boundary names and dimensions before changing numerical controls.'))
        if self._nonfinite:
            result.append(Finding('Error', 'Nonfinite numeric diagnostic', f'NaN/Inf at log line(s) {self._nonfinite[:8]}',
                'Find the first affected field. Inspect initial/boundary values, mesh quality, deltaT and bounded schemes; do not treat later residuals as convergence.'))
        if not self.steps:
            result.append(Finding('Info', 'No solver time steps found', 'This may be a mesh/utility log or an incomplete solver start.',
                'Select the solver log. Use Execution → Check case and mesh for native mesh QAQC.'))
            return result
        co = self.values('co_max')
        if co and max(co) > limits.max_co:
            result.append(Finding('Review', 'Water Courant number exceeds screening target', f'Peak Co = {max(co):.6g}; target = {limits.max_co:g}',
                'Review system/controlDict: adjustTimeStep, maxCo and maxDeltaT. Inspect the smallest cells and inlet velocities. An acceptable Co depends on the solver and schemes.'))
        dt = [value for value in self.values('delta_t') if value > 0]
        if dt and max(dt)/min(dt) > 100:
            result.append(Finding('Review', 'Time step varies by more than 100×', f'Min Δt = {min(dt):.6g} s; max Δt = {max(dt):.6g} s',
                'Locate the reduction in the history. Check small/distorted cells, acceleration and settling CFL. Compare runs at smaller maxDeltaT before relaxing limits.'))
        budget = self.values('budget_residual_kg')
        if budget and max(map(abs, budget)) > limits.budget_kg:
            result.append(Finding('Review', 'Sediment step-budget residual exceeds tolerance', f'Peak |residual| = {max(map(abs,budget)):.6g} kg; tolerance = {limits.budget_kg:g} kg',
                'This is a per-step discrete budget, not cumulative mass loss. Review fvSolution C tolerances, fvSchemes transport, sediment boundary fluxes and time-step sensitivity; scale the tolerance to the problem inventory.'))
        minimum = self.values('min_c_kg_m3')
        if minimum and min(minimum) < -limits.negative_c:
            result.append(Finding('Review', 'Negative suspended concentration', f'Min C = {min(minimum):.6g} kg/m³',
                'Inspect C initial/inlet values, settling CFL, sediment sources and bounded discretization in system/fvSchemes. Quantify magnitude before judging roundoff versus a numerical problem.'))
        latest = {}
        last = self.steps[-1]
        for item in self.residuals:
            if item.segment == last.segment and item.time == last.time:
                latest[item.field] = item.final
        high = {key: value for key, value in latest.items() if math.isfinite(value) and value > limits.residual}
        if high:
            result.append(Finding('Review', 'Latest linear residuals exceed screening target', ', '.join(f'{key}={value:.3g}' for key,value in high.items()),
                'Compare each field with system/fvSolution tolerance/relTol and PIMPLE correction settings. A linear residual is not a nonlinear convergence or physical-accuracy measure.'))
        if not co:
            result.append(Finding('Info', 'Water Courant number was not logged', 'No recognized water-Courant entries.', 'Enable the appropriate solver diagnostics; no Courant value is inferred from residuals.'))
        if not self.values('sediment_co_max'):
            result.append(Finding('Info', 'Sediment Courant number was not logged', 'No recognized settling-inclusive Courant entries; older solver builds may omit them.',
                'Use the rebuilt concentration solver to log sediment Courant numbers. Review maxSedimentCo and settling speed; water Co alone does not include settling.'))
        if self.values('mobile_kg'):
            result.append(Finding('Info', 'Parcel inventories are available; closure is not inferred', 'Mobile and deposited mass are plotted in kg.',
                'A complete balance also needs injected and escaped mass, restart inventory and resuspended transfers. A changing inventory by itself is not conservation error.'))
        if self.events and not fatal:
            result.append(Finding('Review', 'Solver warning context was captured', 'See Events for bounding, convergence or FOAM warnings.',
                'Inspect the first warning and the affected field, mesh region and model settings.'))
        if not self.completed:
            result.append(Finding('Info', 'No normal End marker', 'The log may still be growing, stopped or incomplete.',
                'Refresh the log and inspect the process exit status. Reaching a saved output time does not imply the run finished.'))
        return result


def parse_log(path, cancelled=None):
    result = RunDiagnostics(source=str(Path(path).resolve()))
    with Path(path).open(encoding='utf-8', errors='replace') as stream:
        for index, line in enumerate(stream):
            if index % 1024 == 0 and cancelled and cancelled():
                raise InterruptedError('Log loading canceled.')
            result.feed(line)
    return result


def export_diagnostics_csv(path, report):
    step_names = list(Step.__dataclass_fields__)
    residual_names = ['field', 'initial', 'final', 'iterations']
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='',
                                         dir=path.parent, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, ['record', *step_names, *residual_names])
            writer.writeheader()
            for step in report.steps:
                writer.writerow(dict(record='step', **asdict(step)))
            for residual in report.residuals:
                writer.writerow(dict(record='linearResidual', **asdict(residual)))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
