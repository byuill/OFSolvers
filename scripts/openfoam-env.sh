# Source this Bash file for the prepared Debian/cloud installation only.
# Normal OpenFOAM installations: source the distribution's etc/bashrc instead.
if [ ! -d /workspace/openfoam-source/openfoam-1912.200626/src ]; then
    echo 'Matching Debian v1912 source headers are missing; see docs/sedimentPimpleFoam.md.' >&2
    return 1
fi
source /workspace/ofsolvers-setup/env.sh
export WM_PROJECT_DIR=/workspace/openfoam-source/openfoam-1912.200626
export WM_PROJECT=OpenFOAM WM_PROJECT_VERSION=v1912 WM_ARCH=linux64 WM_COMPILER=Gcc
export WM_COMPILE_OPTION=Opt WM_PRECISION_OPTION=DP WM_LABEL_SIZE=32
export WM_LABEL_OPTION=Int32 WM_MPLIB=SYSTEMOPENMPI WM_OPTIONS=linux64Gcc
export WM_DIR=$WM_PROJECT_DIR/wmake
export FOAM_SRC=$WM_PROJECT_DIR/src FOAM_ETC=$WM_PROJECT_DIR/etc
export FOAM_LIBBIN=/workspace/ofsolvers-system/usr/lib
export FOAM_USER_APPBIN=/workspace/ofsolvers-build/bin
export FOAM_USER_LIBBIN=/workspace/ofsolvers-build/lib
export MPI_ARCH_PATH=/workspace/ofsolvers-system/usr/lib/x86_64-linux-gnu/openmpi
export PATH=$FOAM_USER_APPBIN:$WM_DIR:$WM_PROJECT_DIR/bin:$PATH
mkdir -p "$FOAM_USER_APPBIN" "$FOAM_USER_LIBBIN"
