"""
flume.py — UNTESTED research prototype.

3-D weakly compressible, volume-averaged SPH water coupled to:
  DEM: individual spheres, normal spring/dashpot, tangential history,
       Coulomb friction, rotational dynamics and frictional walls.
  CRM: material points with effective granular pressure and regularized
       mu(I) viscosity. This is a custom continuum approximation.

Boundary conditions:
  Periodic X for water and sediment.
  Solid bottom and sidewalls; atmospheric free surface, no top lid.
  X wrapping preserves Y, Z, velocity, spin, particle ID and history.

Important approximations:
  Fixed smoothing lengths; unresolved fluid/grain coupling.
  Simple mixing-length water turbulence closure.
  Softened DEM stiffness, spherical grains, no cohesion.
  CRM viscosity regularization permits creep and does not resolve saltation.
  No claim of physical validation or equivalence to Project Chrono CRM.

References:
  https://arxiv.org/abs/1301.0752
  https://arxiv.org/abs/cond-mat/0612110
  https://docs.lammps.org/pair_granular.html
"""

import argparse
import csv
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import queue
import time
import traceback
import uuid

import numpy as np
import taichi as ti


DEFAULT = dict(
    L=0.20, W=0.05, H=0.10,
    d=0.002, layers=3,
    surface=0.095, U=0.65, duration=30.0,
    dx=0.004, ds=0.0015,
    rho_water=1000.0, rho_grain=2650.0,
    nu_water=1.0e-6, nu_t_max=0.002,
    c0=12.0, cg=3.0,
    kn=200.0, mu=0.50, restitution=0.50,
    mu_s=0.38, mu_2=0.64, I0=0.279,
    eta_max=20.0, shear_regularization=0.1,
    dt_max=2.0e-5, ramp=1.0,
    frame_interval=0.10,
    mode="DEM", backend="CPU",
    threads=max(1, min(8, os.cpu_count() or 1)),
    affinity="0,1",
    output="outputs/flume_runs",
)

G = 9.81
CELL_CAPACITY = 1024
CONTACT_SLOTS = 32


def validate(c):
    for key, value in c.items():
        if isinstance(value, (int, float)) and not np.isfinite(value):
            raise ValueError(f"{key} must be finite.")
    positive = (
        "L", "W", "H", "d", "surface", "duration", "dx", "ds",
        "rho_water", "rho_grain", "nu_water", "c0", "cg",
        "kn", "eta_max", "shear_regularization", "dt_max",
        "ramp", "frame_interval",
    )
    for key in positive:
        if c[key] <= 0:
            raise ValueError(f"{key} must be positive.")
    if int(c["layers"]) != c["layers"] or c["layers"] < 1:
        raise ValueError("Sediment layers must be an integer >= 1.")
    if int(c["threads"]) != c["threads"] or c["threads"] < 1:
        raise ValueError("CPU threads must be an integer >= 1.")
    if not 0 < c["restitution"] <= 1:
        raise ValueError("Restitution must be in (0, 1].")
    if c["mu"] < 0 or c["U"] < 0 or c["nu_t_max"] < 0:
        raise ValueError("Friction, velocity and eddy-viscosity cap must be >= 0.")
    if not 0 <= c["mu_s"] <= c["mu_2"] or c["I0"] <= 0:
        raise ValueError("Require 0 <= mu_s <= mu_2 and I0 > 0.")
    if c["rho_grain"] <= c["rho_water"]:
        raise ValueError("This setup requires sediment denser than water.")
    bed = c["layers"] * c["d"]
    if not bed < c["surface"] < c["H"]:
        raise ValueError("Require sediment thickness < surface elevation < height.")
    if min(c["L"], c["W"]) < 4 * c["d"]:
        raise ValueError("Length and width must each accommodate at least 4 grains.")
    if c["mode"] not in ("DEM", "CRM"):
        raise ValueError("Mode must be DEM or CRM.")
    if c["backend"] not in ("Serial", "CPU", "CUDA", "CPU-affinity"):
        raise ValueError("Unknown execution backend.")
    if c["mode"] == "CRM" and bed / c["ds"] < 4 - 1e-8:
        raise ValueError(
            "CRM requires at least 4 numerical points through the bed. "
            "Reduce CRM spacing or increase sediment layers."
        )
    if c["c0"] < 10 * max(c["U"], math.sqrt(G * c["surface"])):
        raise ValueError("Increase c0: use >= 10*max(U, sqrt(g*surface)).")


def lattice(L, W, Z, spacing):
    counts = np.maximum(1, np.rint(np.array([L, W, Z]) / spacing).astype(int))
    steps = np.array([L, W, Z]) / counts
    axes = [(np.arange(n) + 0.5) * a for n, a in zip(counts, steps)]
    xyz = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    return xyz.astype(np.float32), steps, counts


def wall_lattice(L, W, H, steps, support):
    dx, dy, dz = steps
    nx, ny = round(L / dx), round(W / dy)
    padding = math.ceil(support / min(steps)) + 1
    result = []
    for i in range(nx):
        for j in range(-padding, ny + padding):
            for k in range(-padding, math.ceil(H / dz)):
                if j < 0 or j >= ny or k < 0:
                    result.append(((i + 0.5) * dx,
                                   (j + 0.5) * dy,
                                   (k + 0.5) * dz))
    return np.asarray(result, dtype=np.float32).reshape(-1, 3)


def wrap_x_numpy(x, length):
    result = np.array(x, copy=True)
    result[:, 0] -= length * np.floor(result[:, 0] / length)
    return result


@ti.data_oriented
class Model:
    def __init__(self, c):
        validate(c)
        self.c = dict(c)
        for key in (
            "L", "W", "H", "d", "surface", "rho_water", "rho_grain",
            "nu_water", "nu_t_max", "c0", "cg", "kn", "mu", "mu_s",
            "mu_2", "I0", "eta_max", "shear_regularization",
        ):
            setattr(self, key, float(c[key]))
        self.crm = c["mode"] == "CRM"
        self.bed = c["layers"] * self.d
        self.radius = self.d / 2
        self.grain_volume = math.pi * self.d**3 / 6
        self.grain_mass = self.rho_grain * self.grain_volume
        loge = math.log(c["restitution"])
        self.zeta = -loge / math.sqrt(math.pi**2 + loge**2)

        # DEM inventory establishes equal initial sediment mass in both modes.
        nxg = int(math.floor(self.L / self.d + 1e-9))
        nyg = int(math.floor(self.W / self.d + 1e-9))
        self.dem_count = nxg * nyg * c["layers"]
        self.solid_mass = self.dem_count * self.grain_mass
        self.rhob = self.solid_mass / (self.L * self.W * self.bed)
        self.phi0 = self.rhob / self.rho_grain

        fluid, fs, _ = lattice(self.L, self.W, self.surface, c["dx"])
        self.hf = max(1.3 * max(fs), 2 * self.d)
        if self.crm:
            solid, ss, _ = lattice(self.L, self.W, self.bed, c["ds"])
            self.hs = 1.3 * max(ss)
            smass = self.solid_mass / len(solid)
            svol = np.prod(ss)
        else:
            axes = [
                (np.arange(nxg) + 0.5) * self.L / nxg,
                (np.arange(nyg) + 0.5) * self.W / nyg,
                (np.arange(c["layers"]) + 0.5) * self.d,
            ]
            solid = np.stack(
                np.meshgrid(*axes, indexing="ij"), axis=-1
            ).reshape(-1, 3).astype(np.float32)
            self.hs = self.hf
            smass = self.grain_mass
            svol = self.grain_volume

        self.R = max(2 * self.hf, 2 * self.hs if self.crm else self.d)
        fw = wall_lattice(self.L, self.W, self.H, fs, 2 * self.hf)
        sw = (
            wall_lattice(self.L, self.W, self.H, ss, 2 * self.hs)
            if self.crm else np.empty((0, 3), np.float32)
        )
        self.nf, self.ns = len(fluid), len(solid)
        self.nm = self.nf + self.ns
        xyz = np.concatenate([fluid, solid, fw, sw])
        self.n = len(xyz)
        if self.n > 300_000:
            raise ValueError("Too many particles for this reference implementation.")

        kind = np.concatenate([
            np.zeros(self.nf), np.ones(self.ns),
            np.full(len(fw), 2), np.full(len(sw), 3),
        ]).astype(np.int32)

        volume = np.concatenate([
            np.full(self.nf, np.prod(fs)),
            np.full(self.ns, svol),
            np.full(len(fw), np.prod(fs)),
            np.full(len(sw), np.prod(ss) if self.crm else 0),
        ]).astype(np.float32)

        mass = volume * self.rho_water
        mass[self.nf:self.nm] = smass
        if len(sw):
            mass[-len(sw):] = volume[-len(sw):] * self.rhob

        # X cell width >= R ensures a +/-1 cell search is complete.
        self.nx = int(math.floor(self.L / self.R))
        if self.nx < 3:
            raise ValueError("Require L >= 3*largest kernel support radius.")
        self.cx = self.L / self.nx
        self.ymin = float(min(xyz[:, 1].min(), 0)) - self.R
        self.zmin = float(min(xyz[:, 2].min(), 0)) - self.R
        self.ny = int(math.ceil((xyz[:, 1].max() - self.ymin) / self.R)) + 2
        self.nz = int(math.ceil((max(self.H, xyz[:, 2].max())
                                - self.zmin) / self.R)) + 2
        nc = self.nx * self.ny * self.nz
        if nc * CELL_CAPACITY > 100_000_000:
            raise ValueError("Neighbor-grid allocation exceeds the configured limit.")

        self.x = ti.Vector.field(3, ti.f32, shape=self.n)
        self.v = ti.Vector.field(3, ti.f32, shape=self.n)
        self.omega = ti.Vector.field(3, ti.f32, shape=self.n)
        self.a = ti.Vector.field(3, ti.f32, shape=self.n)
        self.alpha = ti.Vector.field(3, ti.f32, shape=self.n)
        self.theta = ti.Vector.field(3, ti.f32, shape=self.n)
        self.hydro = ti.Vector.field(3, ti.f32, shape=self.n)
        self.kind = ti.field(ti.i32, shape=self.n)
        self.m = ti.field(ti.f32, shape=self.n)
        self.vol0 = ti.field(ti.f32, shape=self.n)
        self.rho = ti.field(ti.f32, shape=self.n)
        self.eps = ti.field(ti.f32, shape=self.n)
        self.p = ti.field(ti.f32, shape=self.n)
        self.eta = ti.field(ti.f32, shape=self.n)
        self.drho = ti.field(ti.f32, shape=self.n)

        self.count = ti.field(ti.i32, shape=nc)
        self.ids = ti.field(ti.i32, shape=(nc, CELL_CAPACITY))
        self.bad = ti.field(ti.i32, shape=())
        self.overlap = ti.field(ti.f32, shape=())
        self.cross_mass = ti.field(ti.f64, shape=2)

        self.contact_id = ti.field(
            ti.i32, shape=(self.nm, CONTACT_SLOTS))
        self.contact_stamp = ti.field(
            ti.i32, shape=(self.nm, CONTACT_SLOTS))
        self.history = ti.Vector.field(
            3, ti.f32, shape=(self.nm, CONTACT_SLOTS))
        self.contact_id.fill(-999)
        self.contact_stamp.fill(-999)

        self.x.from_numpy(xyz)
        self.kind.from_numpy(kind)
        self.m.from_numpy(mass.astype(np.float32))
        self.vol0.from_numpy(volume)
        self.eps.fill(1)
        self.rho.fill(self.rho_water)
        self.rebuild()
        self.porosity()
        self.initialize()
        self.boundaries()

        self.initial_m = self.m.to_numpy()[:self.nm].astype(float)
        self.mobile_kind = kind[:self.nm]
        self.mass0_water = float(self.initial_m[:self.nf].sum())
        self.mass0_solid = float(self.initial_m[self.nf:].sum())

    @ti.func
    def displacement(self, a, b):
        r = a - b
        r[0] -= self.L * ti.floor(r[0] / self.L + 0.5)
        return r

    @ti.func
    def kernel(self, r, h):
        q = r.norm() / h
        w = 0.0
        grad = ti.Vector.zero(ti.f32, 3)
        if q < 2.0:
            f = 1.0 - 0.5 * q
            w = 21.0 / (16.0 * math.pi * h**3) * f**4 * (2*q + 1)
            grad = -105.0 / (16.0 * math.pi * h**5) * f**3 * r
        return w, grad

    @ti.func
    def cell(self, x):
        return ti.Vector([
            ti.cast(ti.floor(x[0] / self.cx), ti.i32),
            ti.cast(ti.floor((x[1] - self.ymin) / self.R), ti.i32),
            ti.cast(ti.floor((x[2] - self.zmin) / self.R), ti.i32),
        ])

    @ti.func
    def cell_id(self, q):
        result = -1
        if 0 <= q[1] < self.ny and 0 <= q[2] < self.nz:
            ix = (q[0] + self.nx) % self.nx
            result = (ix * self.ny + q[1]) * self.nz + q[2]
        return result

    @ti.kernel
    def insert(self):
        for i in range(self.n):
            cid = self.cell_id(self.cell(self.x[i]))
            if cid >= 0:
                slot = ti.atomic_add(self.count[cid], 1)
                if slot < CELL_CAPACITY:
                    self.ids[cid, slot] = i
                else:
                    ti.atomic_or(self.bad[None], 1)
            else:
                ti.atomic_or(self.bad[None], 2)

    def rebuild(self):
        self.count.fill(0)
        self.insert()

    @ti.kernel
    def porosity(self):
        for i in range(self.nf):
            phi = 0.0
            ci = self.cell(self.x[i])
            for ox, oy, oz in ti.ndrange((-1, 2), (-1, 2), (-1, 2)):
                cid = self.cell_id(ci + ti.Vector([ox, oy, oz]))
                if cid >= 0:
                    for s in range(ti.min(self.count[cid], CELL_CAPACITY)):
                        j = self.ids[cid, s]
                        if self.kind[j] == 1:
                            # Mirror solid volume through bottom and sidewalls
                            # to reduce truncated-kernel porosity bias.
                            for by, bz in ti.ndrange(3, 2):
                                xp = self.x[j]
                                if by == 1:
                                    xp[1] = -xp[1]
                                elif by == 2:
                                    xp[1] = 2*self.W - xp[1]
                                if bz == 1:
                                    xp[2] = -xp[2]
                                r = self.displacement(self.x[i], xp)
                                w, _ = self.kernel(r, self.hf)
                                phi += self.m[j] / self.rho_grain * w
            raw = 1.0 - phi
            if raw < 0.20:
                ti.atomic_or(self.bad[None], 4)
            self.eps[i] = ti.min(1.0, ti.max(0.20, raw))

    @ti.kernel
    def initialize(self):
        for i in range(self.n):
            k = self.kind[i]
            if k == 0 or k == 2:
                pp = self.rho_water * G * ti.max(self.surface - self.x[i][2], 0)
                self.rho[i] = self.eps[i] * (self.rho_water + pp / self.c0**2)
                self.m[i] = self.rho[i] * self.vol0[i]
                self.p[i] = pp
                self.eta[i] = self.rho_water * self.nu_water
            elif ti.static(self.crm):
                pp = (self.rhob - self.phi0*self.rho_water) * G * \
                     ti.max(self.bed - self.x[i][2], 0)
                self.rho[i] = self.rhob + pp / self.cg**2
                self.p[i] = pp
                self.eta[i] = self.eta_max
            else:
                self.rho[i] = self.rho_grain

    @ti.kernel
    def pressure(self):
        for i in range(self.nm):
            if self.kind[i] == 0:
                self.p[i] = ti.max(
                    0.0, self.c0**2 *
                    (self.rho[i] / self.eps[i] - self.rho_water))
            elif ti.static(self.crm):
                self.p[i] = ti.max(
                    0.0, self.cg**2 * (self.rho[i] - self.rhob))

    @ti.kernel
    def boundaries(self):
        for i in range(self.nm, self.n):
            phase = self.kind[i] - 2
            h = self.hf
            reference = self.rho_water
            sound = self.c0
            weight_density = self.rho_water
            if phase == 1:
                h = self.hs
                reference = self.rhob
                sound = self.cg
                weight_density = self.rhob - self.phi0*self.rho_water
            sw = 0.0
            sp = 0.0
            se = 0.0
            smu = 0.0
            sv = ti.Vector.zero(ti.f32, 3)
            ci = self.cell(self.x[i])
            for ox, oy, oz in ti.ndrange((-1, 2), (-1, 2), (-1, 2)):
                cid = self.cell_id(ci + ti.Vector([ox, oy, oz]))
                if cid >= 0:
                    for s in range(ti.min(self.count[cid], CELL_CAPACITY)):
                        j = self.ids[cid, s]
                        if self.kind[j] == phase:
                            r = self.displacement(self.x[i], self.x[j])
                            w, _ = self.kernel(r, h)
                            w *= self.m[j] / ti.max(self.rho[j], 1.0)
                            sw += w
                            sp += w * (self.p[j] + weight_density*G *
                                       (self.x[j][2] - self.x[i][2]))
                            se += w * self.eps[j]
                            smu += w * self.eta[j]
                            sv += w * self.v[j]
            if sw > 1e-9:
                self.p[i] = ti.max(sp / sw, 0.0)
                self.eps[i] = se / sw
                self.eta[i] = smu / sw
                self.v[i] = -sv / sw
            else:
                self.p[i] = 0.0
                self.v[i] = ti.Vector.zero(ti.f32, 3)
            density = reference + self.p[i] / sound**2
            if phase == 0:
                density *= self.eps[i]
            self.rho[i] = density
            self.m[i] = density * self.vol0[i]

    @ti.kernel
    def rheology(self):
        for i in range(self.nm):
            phase = self.kind[i]
            if phase == 0 or ti.static(self.crm):
                h = self.hf
                if phase == 1:
                    h = self.hs
                gradv = ti.Matrix.zero(ti.f32, 3, 3)
                ci = self.cell(self.x[i])
                for ox, oy, oz in ti.ndrange((-1, 2), (-1, 2), (-1, 2)):
                    cid = self.cell_id(ci + ti.Vector([ox, oy, oz]))
                    if cid >= 0:
                        for s in range(ti.min(self.count[cid], CELL_CAPACITY)):
                            j = self.ids[cid, s]
                            if self.kind[j] == phase or self.kind[j] == phase + 2:
                                r = self.displacement(self.x[i], self.x[j])
                                _, grad = self.kernel(r, h)
                                gradv += self.m[j] / ti.max(self.rho[j], 1.0) * \
                                    (self.v[j] - self.v[i]).outer_product(grad)
                D = 0.5 * (gradv + gradv.transpose())
                D -= ti.Matrix.identity(ti.f32, 3) * D.trace() / 3.0
                rate = ti.sqrt(2.0 * (D * D).sum() + 1e-12)
                if phase == 0:
                    z = ti.max(self.x[i][2] - self.bed, 0.0)
                    wall = ti.max(0.0, ti.min(self.x[i][1],
                                              self.W - self.x[i][1]))
                    ell = 0.41 * ti.min(z, wall)
                    nut = ti.min(self.nu_t_max, ell*ell*rate)
                    self.eta[i] = self.rho_water * (self.nu_water + nut)
                else:
                    effective_p = self.p[i]
                    inertial = self.d * rate * ti.sqrt(
                        self.rho_grain / ti.max(effective_p, 0.01))
                    friction = self.mu_s + (self.mu_2 - self.mu_s) * \
                        inertial / (self.I0 + inertial)
                    viscosity = friction * effective_p / ti.sqrt(
                        rate*rate + self.shear_regularization**2)
                    self.eta[i] = ti.min(self.eta_max, viscosity)

    @ti.kernel
    def internal_forces(self, drive: ti.f32):
        for i in range(self.nm):
            phase = self.kind[i]
            acc = ti.Vector.zero(ti.f32, 3)
            rate = 0.0
            if phase == 0 or ti.static(self.crm):
                h = self.hf
                sound = self.c0
                if phase == 1:
                    h = self.hs
                    sound = self.cg
                rhoi = ti.max(self.rho[i], 1.0)
                ci = self.cell(self.x[i])
                for ox, oy, oz in ti.ndrange((-1, 2), (-1, 2), (-1, 2)):
                    cid = self.cell_id(ci + ti.Vector([ox, oy, oz]))
                    if cid >= 0:
                        for s in range(ti.min(self.count[cid], CELL_CAPACITY)):
                            j = self.ids[cid, s]
                            if j != i and (
                                self.kind[j] == phase or self.kind[j] == phase + 2
                            ):
                                r = self.displacement(self.x[i], self.x[j])
                                _, grad = self.kernel(r, h)
                                dv = self.v[i] - self.v[j]
                                rhoj = ti.max(self.rho[j], 1.0)
                                den = r.dot(r) + 0.01*h*h
                                artificial = -0.03 * sound * h * \
                                    ti.min(dv.dot(r), 0.0) / \
                                    (den * 0.5*(rhoi + rhoj))
                                acc -= self.m[j] * (
                                    self.p[i]/rhoi**2 +
                                    self.p[j]/rhoj**2 + artificial) * grad
                                acc += self.m[j] * (self.eta[i] + self.eta[j]) / \
                                    (rhoi*rhoj) * r.dot(grad) / den * dv
                                rate += self.m[j] * dv.dot(grad)
            # theta excludes gravity, drive and interphase coupling.
            self.theta[i] = acc
            self.drho[i] = rate
            self.a[i] = acc + ti.Vector([0.0, 0.0, -G])
            if phase == 0:
                self.a[i][0] += drive
            self.alpha[i] = ti.Vector.zero(ti.f32, 3)
            self.hydro[i] = ti.Vector.zero(ti.f32, 3)

    @ti.kernel
    def coupling(self):
        for i in range(self.nf, self.nm):
            ci = self.cell(self.x[i])
            sw = 0.0
            eps = 0.0
            vel = ti.Vector.zero(ti.f32, 3)
            stress_force_density = ti.Vector.zero(ti.f32, 3)

            for ox, oy, oz in ti.ndrange((-1, 2), (-1, 2), (-1, 2)):
                cid = self.cell_id(ci + ti.Vector([ox, oy, oz]))
                if cid >= 0:
                    for s in range(ti.min(self.count[cid], CELL_CAPACITY)):
                        j = self.ids[cid, s]
                        if self.kind[j] == 0:
                            r = self.displacement(self.x[i], self.x[j])
                            w, _ = self.kernel(r, self.hf)
                            w *= self.m[j] / ti.max(self.rho[j], 1.0)
                            sw += w
                            vel += w * self.v[j]
                            eps += w * self.eps[j]
                            stress_force_density += w * self.rho[j] * self.theta[j]

            if sw > 1e-9:
                vel /= sw
                eps = ti.max(0.20, ti.min(1.0, eps/sw))
                stress_force_density /= sw
                slip = vel - self.v[i]
                speed = slip.norm()
                physical_volume = self.m[i] / self.rho_grain
                mu_water = self.rho_water * self.nu_water
                Re = ti.max(eps*self.d*speed/self.nu_water, 1e-8)
                Cd = 0.44
                if Re < 1000:
                    Cd = 24.0/Re * (1.0 + 0.15*Re**0.687)

                # Dense Ergun / dilute Wen-Yu-style drag closure.
                drag = ti.Vector.zero(ti.f32, 3)
                if eps < 0.8:
                    coefficient = physical_volume * (
                        150*mu_water*(1-eps)/(eps*self.d**2) +
                        1.75*self.rho_water*speed/self.d)
                    drag = coefficient * slip
                else:
                    equivalent_grains = physical_volume / self.grain_volume
                    area = math.pi*self.d**2/4
                    drag = equivalent_grains * 0.5*self.rho_water*Cd*area * \
                           speed*eps**(-2.65)*slip

                force = physical_volume * stress_force_density + drag
                self.hydro[i] = force
                self.a[i] += force / self.m[i]

                # Distribute equal-and-opposite TOTAL hydrodynamic force.
                # internal_forces has already finished clearing all a fields.
                for ox, oy, oz in ti.ndrange((-1, 2), (-1, 2), (-1, 2)):
                    cid = self.cell_id(ci + ti.Vector([ox, oy, oz]))
                    if cid >= 0:
                        for s in range(ti.min(self.count[cid], CELL_CAPACITY)):
                            j = self.ids[cid, s]
                            if self.kind[j] == 0:
                                r = self.displacement(self.x[i], self.x[j])
                                w, _ = self.kernel(r, self.hf)
                                fraction = self.m[j] / ti.max(self.rho[j], 1.0) * w/sw
                                for axis in ti.static(range(3)):
                                    ti.atomic_add(
                                        self.a[j][axis],
                                        -fraction * force[axis] / self.m[j])

    @ti.func
    def contact(self, i, j, normal, penetration, dt, step):
        force = ti.Vector.zero(ti.f32, 3)
        torque = ti.Vector.zero(ti.f32, 3)
        slot = -1
        for s in range(CONTACT_SLOTS):
            if self.contact_id[i, s] == j:
                slot = s
        if slot < 0:
            for s in range(CONTACT_SLOTS):
                if slot < 0 and self.contact_stamp[i, s] < step - 1:
                    slot = s
        if slot < 0:
            ti.atomic_or(self.bad[None], 8)
        else:
            old = ti.Vector.zero(ti.f32, 3)
            if self.contact_id[i, slot] == j and \
                    self.contact_stamp[i, slot] >= step - 1:
                old = self.history[i, slot]

            other_v = ti.Vector.zero(ti.f32, 3)
            other_w = ti.Vector.zero(ti.f32, 3)
            reduced_mass = self.m[i]
            if j >= 0:
                other_v = self.v[j]
                other_w = self.omega[j]
                reduced_mass = self.m[i]*self.m[j]/(self.m[i]+self.m[j])

            relative = self.v[i] - other_v - \
                self.radius * (self.omega[i] + other_w).cross(normal)
            vn = relative.dot(normal)
            vt = relative - vn*normal
            gn = 2*self.zeta*ti.sqrt(self.kn*reduced_mass)
            kt = 2.0/7.0*self.kn
            gt = 0.5*gn
            fn = ti.max(0.0, self.kn*penetration - gn*vn)

            old -= old.dot(normal)*normal
            history = old + dt*vt
            ft = -kt*history - gt*vt
            limit = self.mu*fn
            if ft.norm() > limit:
                ft *= limit / ti.max(ft.norm(), 1e-20)
                history = -(ft + gt*vt)/kt

            force = fn*normal + ft
            torque = (-self.radius*normal).cross(ft)
            self.contact_id[i, slot] = j
            self.contact_stamp[i, slot] = step
            self.history[i, slot] = history
            ti.atomic_max(self.overlap[None], penetration/self.d)
        return force, torque

    @ti.kernel
    def dem_contacts(self, dt: ti.f32, step: ti.i32):
        for i in range(self.nf, self.nm):
            f = ti.Vector.zero(ti.f32, 3)
            torque = ti.Vector.zero(ti.f32, 3)
            ci = self.cell(self.x[i])
            for ox, oy, oz in ti.ndrange((-1, 2), (-1, 2), (-1, 2)):
                cid = self.cell_id(ci + ti.Vector([ox, oy, oz]))
                if cid >= 0:
                    for s in range(ti.min(self.count[cid], CELL_CAPACITY)):
                        j = self.ids[cid, s]
                        if j != i and self.kind[j] == 1:
                            r = self.displacement(self.x[i], self.x[j])
                            distance = r.norm()
                            if 1e-12 < distance < self.d:
                                ff, tt = self.contact(
                                    i, j, r/distance, self.d-distance, dt, step)
                                f += ff
                                torque += tt

            for wall in ti.static(range(3)):
                normal = ti.Vector([0.0, 0.0, 1.0])
                distance = self.x[i][2]
                if wall == 1:
                    normal = ti.Vector([0.0, 1.0, 0.0])
                    distance = self.x[i][1]
                elif wall == 2:
                    normal = ti.Vector([0.0, -1.0, 0.0])
                    distance = self.W - self.x[i][1]
                if distance < self.radius:
                    ff, tt = self.contact(
                        i, -wall-2, normal, self.radius-distance, dt, step)
                    f += ff
                    torque += tt

            self.a[i] += f/self.m[i]
            inertia = 0.4*self.m[i]*self.radius**2
            self.alpha[i] = torque/inertia

    @ti.kernel
    def integrate(self, dt: ti.f32):
        for i in range(self.nm):
            self.v[i] += dt*self.a[i]
            self.x[i] += dt*self.v[i]
            if self.kind[i] == 0 or ti.static(self.crm):
                self.rho[i] += dt*self.drho[i]
                if self.rho[i] <= 1.0:
                    ti.atomic_or(self.bad[None], 16)
            else:
                self.omega[i] += dt*self.alpha[i]

            turns = ti.floor(self.x[i][0]/self.L)
            # Only X changes during recirculation.
            self.x[i][0] -= turns*self.L
            if self.kind[i] == 1:
                ti.atomic_add(self.cross_mass[0],
                              ti.cast(ti.max(turns, 0.0)*self.m[i], ti.f64))
                ti.atomic_add(self.cross_mass[1],
                              ti.cast(turns*self.m[i], ti.f64))

            # No hidden lid or silent deletion/reinsertion at other walls.
            if self.x[i][1] < 0 or self.x[i][1] > self.W or \
               self.x[i][2] < 0 or self.x[i][2] >= self.H:
                ti.atomic_or(self.bad[None], 32)

    def step(self, dt, step, drive):
        self.rebuild()
        self.porosity()
        self.pressure()
        self.boundaries()
        self.rheology()
        self.internal_forces(drive)
        self.coupling()
        if not self.crm:
            self.dem_contacts(dt, step)
        self.integrate(dt)

    def timestep(self, vmax, amax):
        hmin = min(self.hf, self.hs) if self.crm else self.hf
        limits = [
            self.c["dt_max"],
            0.10*hmin/(max(self.c0/math.sqrt(0.20), self.cg) + vmax + 1e-9),
            0.10*math.sqrt(hmin/max(amax, G)),
            0.06*self.hf**2 /
            ((self.nu_water+self.nu_t_max)/0.20 + 1e-12),
        ]
        if self.crm:
            limits.append(0.06*self.hs**2/(self.eta_max/self.rhob + 1e-12))
        else:
            limits.append(0.08*math.sqrt(0.5*self.grain_mass/self.kn))
        return min(limits)

    def state(self):
        n = self.nm
        return dict(
            x=self.x.to_numpy()[:n],
            v=self.v.to_numpy()[:n],
            omega=self.omega.to_numpy()[:n],
            rho=self.rho.to_numpy()[:n],
            eps=self.eps.to_numpy()[:n],
            a=self.a.to_numpy()[:n],
            mass=self.m.to_numpy()[:n],
            kind=self.mobile_kind,
        )

    def diagnostics(self, state, t):
        if not all(np.isfinite(state[k]).all()
                   for k in ("x", "v", "rho", "a")):
            raise RuntimeError("Non-finite state: numerical instability.")
        speed = np.linalg.norm(state["v"], axis=1)
        clear = (state["kind"] == 0) & (state["x"][:, 2] > self.bed)
        U = (
            float(np.average(state["v"][clear, 0], weights=state["mass"][clear]))
            if clear.any() else 0.0
        )
        intrinsic = state["rho"][:self.nf] / state["eps"][:self.nf]
        density_error = float(np.max(np.abs(intrinsic/self.rho_water - 1)))
        overlap = float(self.overlap[None])
        flags = int(self.bad[None])
        if flags:
            meaning = {
                1: "neighbor-cell capacity exceeded",
                2: "particle outside neighbor grid",
                4: "porosity below 0.20",
                8: "DEM contact-history capacity exceeded",
                16: "non-positive SPH density",
                32: "wall escape or overtopping",
            }
            detail = "; ".join(v for k, v in meaning.items() if flags & k)
            raise RuntimeError(detail)
        if density_error > 0.10:
            raise RuntimeError(
                f"Intrinsic water density error {density_error:.1%}; "
                "stop and investigate resolution, c0 and timestep."
            )
        if overlap > 0.10:
            raise RuntimeError(
                f"DEM overlap reached {overlap:.1%} of diameter. "
                "Increase stiffness and reduce timestep."
            )
        if not np.array_equal(state["mass"], self.initial_m.astype(np.float32)):
            raise RuntimeError("A mobile particle's mass changed.")
        crossings = self.cross_mass.to_numpy()
        return dict(
            t=t, U=U,
            vmax=float(speed.max(initial=0)),
            amax=float(np.linalg.norm(state["a"], axis=1).max(initial=G)),
            density_error=density_error,
            max_overlap_ratio=overlap,
            solid_mass_kg=float(state["mass"][self.nf:].sum(dtype=float)),
            water_mass_kg=float(state["mass"][:self.nf].sum(dtype=float)),
            recirculated_forward_kg=float(crossings[0]),
            net_outlet_kg=float(crossings[1]),
            mean_net_transport_kg_s_m=float(crossings[1]/max(t*self.W, 1e-20)),
        )


def configure_backend(c):
    threads = int(c["threads"])
    if c["backend"] == "CPU-affinity":
        import psutil
        process = psutil.Process()
        requested = [int(s.strip()) for s in c["affinity"].split(",") if s.strip()]
        allowed = process.cpu_affinity()
        if not requested or not set(requested).issubset(allowed):
            raise ValueError(f"Affinity IDs must be selected from {allowed}.")
        process.cpu_affinity(requested)
        threads = min(threads, len(requested))
    if c["backend"] == "Serial":
        threads = 1
    arch = ti.cuda if c["backend"] == "CUDA" else ti.cpu
    ti.init(
        arch=arch, cpu_max_num_threads=threads,
        default_fp=ti.f32, fast_math=False,
        offline_cache=False, enable_fallback=False,
    )


def worker(c, q=None, stop=None):
    run = None

    def send(message):
        if q is not None:
            q.put(message)
        elif message["kind"] != "frame":
            print(message["text"], flush=True)

    try:
        validate(c)
        run = Path(c["output"]) / (
            time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        run.mkdir(parents=True, exist_ok=False)
        (run / "config.json").write_text(json.dumps(c, indent=2), encoding="utf-8")
        configure_backend(c)
        model = Model(c)

        t = 0.0
        step = 0
        initial = model.state()
        diag = model.diagnostics(initial, t)
        np.savez_compressed(run / "initial_state.npz", **initial)
        next_frame = 0.0
        frame_number = 0
        last_gui = 0.0
        started = time.monotonic()
        state = initial

        # Nominal shear estimate; not a measured wall/bed shear.
        depth = c["surface"] - model.bed
        z0 = c["d"]/12
        factor = ((depth+z0)*math.log1p(depth/z0)-depth)/depth
        ustar = 0.41*c["U"]/factor
        tau_nominal = c["rho_water"]*ustar**2
        radius_hydraulic = c["W"]*depth/(c["W"]+2*depth)
        base_drive = tau_nominal/(c["rho_water"]*radius_hydraulic)
        metadata = dict(
            mobile_water_points=model.nf,
            sediment_points=model.ns,
            equivalent_DEM_grains=model.dem_count,
            sediment_thickness_m=model.bed,
            initial_solid_fraction=model.phi0,
            nominal_shear_Pa=tau_nominal,
            nominal_Shields=tau_nominal /
                ((c["rho_grain"]-c["rho_water"])*G*c["d"]),
            note="Nominal shear is a rough log-law estimate, not model validation.",
        )
        (run / "metadata.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8")

        columns = list(diag) + ["dt", "step", "wall_seconds"]
        with (run / "diagnostics.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            while t < c["duration"]:
                if stop is not None and stop.is_set():
                    break
                dt = model.timestep(diag["vmax"], diag["amax"])
                target = c["U"]*min(t/c["ramp"], 1.0)
                drive = base_drive*min(t/c["ramp"], 1.0)**2 + \
                        (target-diag["U"])/0.5
                model.overlap[None] = 0.0

                # Bounded chunks keep GUI stop/status responsive.
                for _ in range(25):
                    if t >= c["duration"]:
                        break
                    ds = min(dt, c["duration"]-t)
                    model.step(ds, step, drive)
                    t += ds
                    step += 1

                state = model.state()
                diag = model.diagnostics(state, t)
                elapsed = time.monotonic()-started
                writer.writerow(dict(diag, dt=dt, step=step, wall_seconds=elapsed))

                now = time.monotonic()
                if t >= next_frame or now-last_gui >= 0.75 or t >= c["duration"]:
                    slab = max(c["d"], c["dx"])
                    selected = np.abs(state["x"][:, 1]-c["W"]/2) <= slab/2
                    xyz = state["x"][selected]
                    speed = np.linalg.norm(state["v"][selected], axis=1)
                    solid = state["kind"][selected] == 1
                    eta_seconds = elapsed*(c["duration"]-t)/max(t, 1e-12)
                    text = (
                        f"{c['mode']} / {c['backend']}\n"
                        f"t={t:.4f}/{c['duration']:g} s; step={step:,}\n"
                        f"U={diag['U']:.3f} m/s; dt={dt:.2e} s\n"
                        f"Density error={diag['density_error']:.2%}; "
                        f"overlap={diag['max_overlap_ratio']:.2%}\n"
                        f"Net mean transport="
                        f"{diag['mean_net_transport_kg_s_m']:.3e} kg/(s m)\n"
                        f"Elapsed={elapsed/60:.1f} min; "
                        f"estimated remaining={eta_seconds/60:.1f} min\n"
                        f"Slice thickness={slab:g} m\nOutput: {run}"
                    )
                    message = dict(kind="frame", x=xyz, speed=speed,
                                   solid=solid, t=t, text=text)
                    send(message)
                    last_gui = now

                    if t >= next_frame or t >= c["duration"]:
                        np.savez_compressed(
                            run / f"slice_{frame_number:05d}.npz",
                            x=xyz, speed=speed, solid=solid, t=t,
                            L=c["L"], W=c["W"], H=c["H"], slab=slab,
                        )
                        frame_number += 1
                        next_frame = t+c["frame_interval"]
                        f.flush()

        np.savez_compressed(run / "final_state.npz", t=t, **state)
        status = "Completed" if t >= c["duration"] else "Stopped"
        send(dict(kind="done", text=f"{status} at t={t:.6f} s.\nResults: {run}"))
        return run
    except Exception:
        detail = traceback.format_exc()
        if run is not None:
            (run / "error.txt").write_text(detail, encoding="utf-8")
        send(dict(kind="error", text=detail))
        if q is None:
            raise


def gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

    root = tk.Tk()
    root.title("3-D SPH sediment flume — unvalidated research prototype")
    root.geometry("1250x880")
    ctx = mp.get_context("spawn")
    state = dict(proc=None, q=None, stop=None, terminal=False, closing=False)
    variables = {}

    left = ttk.Frame(root, padding=10)
    left.pack(side="left", fill="y")
    right = ttk.Frame(root, padding=10)
    right.pack(side="right", fill="both", expand=True)

    fields = [
        ("L", "Length [m]"), ("W", "Width [m]"), ("H", "Height [m]"),
        ("d", "Grain diameter [m]"), ("layers", "Bed layers"),
        ("surface", "Surface elevation [m]"),
        ("U", "Target mean velocity [m/s]"), ("duration", "Duration [s]"),
        ("dx", "Water point spacing [m]"), ("ds", "CRM point spacing [m]"),
        ("kn", "DEM stiffness [N/m]"), ("mu", "DEM friction"),
        ("restitution", "Restitution"),
        ("eta_max", "CRM viscosity cap [Pa s]"),
        ("c0", "Water sound speed [m/s]"),
        ("dt_max", "Maximum step [s]"), ("threads", "CPU threads"),
    ]
    for row, (key, label) in enumerate(fields):
        variables[key] = tk.StringVar(value=str(DEFAULT[key]))
        ttk.Label(left, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(left, textvariable=variables[key], width=14).grid(
            row=row, column=1, padx=8)

    row = len(fields)
    for key, label, options in [
        ("mode", "Sediment model", ("DEM", "CRM")),
        ("backend", "Execution", ("Serial", "CPU", "CUDA", "CPU-affinity")),
    ]:
        variables[key] = tk.StringVar(value=DEFAULT[key])
        ttk.Label(left, text=label).grid(row=row, column=0, sticky="w")
        ttk.Combobox(left, textvariable=variables[key], values=options,
                     state="readonly", width=13).grid(row=row, column=1)
        row += 1
    variables["affinity"] = tk.StringVar(value=DEFAULT["affinity"])
    ttk.Label(left, text="Affinity CPU IDs").grid(row=row, column=0, sticky="w")
    ttk.Entry(left, textvariable=variables["affinity"], width=14).grid(
        row=row, column=1)
    output = tk.StringVar(value=str(Path(DEFAULT["output"]).resolve()))
    ttk.Label(left, text="Output parent folder").grid(
        row=row+1, column=0, columnspan=2, sticky="w")
    ttk.Entry(left, textvariable=output, width=43).grid(
        row=row+2, column=0, columnspan=2, sticky="ew")

    buttons = ttk.Frame(left)
    buttons.grid(row=row+3, column=0, columnspan=2, pady=8)
    start_button = ttk.Button(buttons, text="Run")
    start_button.pack(side="left", padx=4)
    stop_button = ttk.Button(buttons, text="Stop", state="disabled")
    stop_button.pack(side="left", padx=4)
    load_button = ttk.Button(buttons, text="Open saved slice")
    load_button.pack(side="left", padx=4)
    status = tk.StringVar(value="Ready. Changes apply to the next run.")
    ttk.Label(left, textvariable=status, wraplength=360, justify="left").grid(
        row=row+4, column=0, columnspan=2, sticky="w")
    ttk.Label(right, text="Central X–Z slab at y=W/2. Sediment: orange.").pack(
        anchor="w")

    fig = Figure(figsize=(7, 6), dpi=100, constrained_layout=True)
    ax = fig.add_subplot(111)
    water = ax.scatter([], [], c=[], cmap="viridis", s=9, vmin=0, vmax=1)
    sediment = ax.scatter([], [], color="darkorange", s=12)
    fig.colorbar(water, ax=ax, label="Water speed [m/s]")
    ax.set(xlim=(0, DEFAULT["L"]), ylim=(0, DEFAULT["H"]),
           xlabel="X [m]", ylabel="Z [m]")
    ax.set_aspect("equal", adjustable="box")
    canvas = FigureCanvasTkAgg(fig, master=right)
    canvas.get_tk_widget().pack(fill="both", expand=True)

    def draw(msg):
        xyz = np.asarray(msg["x"]).reshape(-1, 3)
        solid = np.asarray(msg["solid"], dtype=bool)
        speed = np.asarray(msg["speed"])
        water.set_offsets(xyz[~solid][:, [0, 2]])
        water.set_array(speed[~solid])
        water.set_clim(0, max(0.1, float(speed[~solid].max(initial=0))))
        sediment.set_offsets(xyz[solid][:, [0, 2]])
        ax.set_title(f"Central slice — t={float(msg['t']):.4f} s")
        canvas.draw_idle()

    def start():
        try:
            c = dict(DEFAULT)
            for key, _ in fields:
                c[key] = (
                    int(variables[key].get()) if key in ("layers", "threads")
                    else float(variables[key].get()))
            for key in ("mode", "backend", "affinity"):
                c[key] = variables[key].get()
            c["output"] = output.get()
            validate(c)
            state.update(q=ctx.Queue(), stop=ctx.Event(), terminal=False)
            state["proc"] = ctx.Process(
                target=worker, args=(c, state["q"], state["stop"]))
            state["proc"].start()
            ax.set(xlim=(0, c["L"]), ylim=(0, c["H"]))
            start_button.configure(state="disabled")
            load_button.configure(state="disabled")
            stop_button.configure(state="normal")
            status.set("Initializing and compiling kernels…")
        except Exception as exc:
            messagebox.showerror("Cannot start", str(exc), parent=root)

    def stop_run():
        if state["stop"] is not None:
            state["stop"].set()
            status.set("Stopping after the current calculation chunk…")
            stop_button.configure(state="disabled")

    def load():
        filename = filedialog.askopenfilename(
            title="Open slice_*.npz", filetypes=[("NumPy result", "*.npz")])
        if filename:
            try:
                with np.load(filename, allow_pickle=False) as data:
                    msg = {key: data[key] for key in ("x", "speed", "solid", "t")}
                    ax.set(xlim=(0, float(data["L"])), ylim=(0, float(data["H"])))
                    draw(msg)
                    status.set(f"Loaded {filename}")
            except Exception as exc:
                messagebox.showerror("Cannot load slice", str(exc), parent=root)

    def poll():
        if state["closing"]:
            return
        if state["q"] is not None:
            latest = None
            while True:
                try:
                    msg = state["q"].get_nowait()
                except queue.Empty:
                    break
                if msg["kind"] == "frame":
                    latest = msg
                else:
                    state["terminal"] = True
                    status.set(msg["text"])
            if latest is not None:
                draw(latest)
                if not state["terminal"]:
                    status.set(latest["text"])
            if not state["proc"].is_alive():
                state["proc"].join(timeout=0)
                if not state["terminal"]:
                    status.set(
                        f"Worker exited with code {state['proc'].exitcode}. "
                        "See console or error.txt.")
                state["q"].close()
                state.update(proc=None, q=None, stop=None)
                start_button.configure(state="normal")
                load_button.configure(state="normal")
                stop_button.configure(state="disabled")
        root.after(100, poll)

    def close():
        state["closing"] = True
        stop_run()
        deadline = time.monotonic()+4

        def finish():
            proc = state["proc"]
            if proc is not None and proc.is_alive() and time.monotonic() < deadline:
                root.after(100, finish)
                return
            if proc is not None:
                if proc.is_alive():
                    proc.terminate()
                proc.join(timeout=0.1)
            if state["q"] is not None:
                state["q"].cancel_join_thread()
                state["q"].close()
            root.destroy()
        finish()

    start_button.configure(command=start)
    stop_button.configure(command=stop_run)
    load_button.configure(command=load)
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll)
    root.mainloop()


def smoke_test():
    # Exact invariants for the coordinate-wrapping operation.
    x = np.array([
        [0.201, 0.01, 0.003],
        [-0.001, 0.02, 0.004],
        [0.601, 0.03, 0.005],
        [0.100, 0.04, 0.006],
    ])
    y = wrap_x_numpy(x, 0.20)
    assert np.all((y[:, 0] >= 0) & (y[:, 0] < 0.20))
    assert np.array_equal(y[:, 1:], x[:, 1:])
    np.testing.assert_allclose(y[:, 0], [0.001, 0.199, 0.001, 0.100])

    for mode in ("DEM", "CRM"):
        c = dict(DEFAULT)
        c.update(
            L=0.04, W=0.02, H=0.03, surface=0.025,
            U=0.20, duration=0.002, mode=mode,
            backend="Serial", threads=1,
            output="outputs/flume_smoke",
        )
        worker(c)
        ti.reset()
    print("Smoke checks completed. This does not establish physical validation.")


if __name__ == "__main__":
    mp.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--config", help="JSON file overriding DEFAULT")
    parser.add_argument("--mode", choices=["DEM", "CRM"])
    parser.add_argument("--backend", choices=["Serial", "CPU", "CUDA", "CPU-affinity"])
    parser.add_argument("--duration", type=float)
    args = parser.parse_args()

    if args.smoke:
        smoke_test()
    elif args.headless:
        config = dict(DEFAULT)
        if args.config:
            config.update(json.loads(Path(args.config).read_text(encoding="utf-8")))
        for name in ("mode", "backend", "duration"):
            value = getattr(args, name)
            if value is not None:
                config[name] = value
        worker(config)
    else:
        gui()
