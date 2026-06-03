# edapack rootless manylinux builder image.
#
# Extends a quay.io/pypa/manylinux* base with the UNION of system packages the
# core tools install at build time today, so that:
#   (1) builds no longer need to `yum install` at runtime, and can therefore
#       run as the host UID (`docker run --user $(id -u)`), and
#   (2) CI and local builds use byte-identical toolchains.
#
# One parametric Dockerfile drives every image via BASE_IMAGE (see
# builder-images.yml for the matrix). manylinux2014 = CentOS7/yum,
# manylinux_2_28 = AlmaLinux8, manylinux_2_34 = AlmaLinux9.
#
#   docker build --build-arg BASE_IMAGE=quay.io/pypa/manylinux_2_28_x86_64 \
#                -t ghcr.io/edapack/manylinux_2_28_x86_64 docker/

ARG BASE_IMAGE=quay.io/pypa/manylinux_2_28_x86_64
FROM ${BASE_IMAGE}

# The manylinux package manager is yum on all current bases (dnf is symlinked
# as yum on AlmaLinux), so a single install line works across them. EPEL
# provides eigen3/boost-static on the EL bases.
RUN set -eux; \
    (yum install -y epel-release || true); \
    yum install -y \
        autoconf automake libtool pkgconfig make gcc gcc-c++ \
        flex bison gperf patchelf \
        git curl tar xz bzip2 \
        zlib-devel bzip2-devel xz-devel libzstd-devel \
        readline-devel libffi-devel \
        tcl-devel \
        boost-devel boost-static \
        eigen3-devel \
        glibc-static perl-core \
        libusb-devel systemd-devel \
        ; \
    yum clean all; rm -rf /var/cache/yum

# Build helpers via pipx/pip into a system location on PATH. meson/ninja for
# verilator; intervaltree/apycula for nextpnr's chipdb generators.
RUN set -eux; \
    PYBIN="$(ls -d /opt/python/cp31*/bin | sort | tail -1)"; \
    "${PYBIN}/pip" install --no-cache-dir meson ninja intervaltree apycula; \
    for t in meson ninja; do ln -sf "${PYBIN}/$t" /usr/local/bin/$t; done

# Yosys' build invokes a cmake wrapper that pins an older policy minimum and
# adds /usr/lib64 to the linker path. Provide it on PATH so per-repo build.sh
# no longer has to create a root-owned cmake-wrapper/ dir in the workspace.
RUN set -eux; \
    printf '%s\n' '#!/bin/sh' \
      'exec /usr/bin/cmake -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_EXE_LINKER_FLAGS="-L/usr/lib64" "$@"' \
      > /usr/local/bin/cmake-compat; \
    chmod +x /usr/local/bin/cmake-compat

# A neutral writable HOME so tools run happily under an arbitrary host UID
# (no /root writes when --user is set). World-writable, sticky.
RUN mkdir -p /work /dist && chmod 1777 /work /dist
ENV HOME=/tmp
LABEL org.opencontainers.image.source="https://github.com/edapack/edapack-common"
