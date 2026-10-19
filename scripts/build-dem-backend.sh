#!/bin/bash
# Optional research DEM dependency; never replaces a system installation.
set -euo pipefail
prefix=${1:?Usage: scripts/build-dem-backend.sh /absolute/empty/install/prefix}
case "$prefix" in /*) ;; *) echo 'Use an absolute installation prefix.' >&2; exit 1;; esac
if [ -d "$prefix" ] && [ -n "$(ls -A "$prefix")" ]; then
    echo 'Choose an empty installation prefix; existing libraries are protected.' >&2
    exit 1
fi
dem_cmake=${CMAKE:-cmake}
command -v "$dem_cmake" >/dev/null || { echo 'Install CMake >=3.20 (for example: python -m pip install cmake).' >&2; exit 1; }
work=$(mktemp -d -t ofsolvers-lammps-XXXXXX)
trap 'rm -rf "$work"' EXIT
version=stable_22Jul2025_update4
curl --fail --location --retry 3 "https://github.com/lammps/lammps/archive/refs/tags/$version.tar.gz" -o "$work/source.tar.gz"
(cd "$work" && echo '411088d9c03339e025f6a975e0a5741bb9e3f351cc39eda220ab22ac318fe2fb  source.tar.gz' | sha256sum --check)
tar -xzf "$work/source.tar.gz" -C "$work"
"$dem_cmake" -S "$work/lammps-$version/cmake" -B "$work/build" \
    -DBUILD_MPI=OFF -DCMAKE_DISABLE_FIND_PACKAGE_MPI=ON -DBUILD_OMP=OFF \
    -DBUILD_SHARED_LIBS=ON -DPKG_GRANULAR=ON -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_INSTALL_RPATH="$prefix/lib;$prefix/lib64"
"$dem_cmake" --build "$work/build" -j "${DEM_BUILD_JOBS:-2}"
"$dem_cmake" --install "$work/build"
printf '\nSet LAMMPS_ROOT=%s, activate OpenCFD v1912, then run ./Allwmake --with-dem\n' "$prefix"
