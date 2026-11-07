# overpressure's build container: the Linux side of the headsets' builds (userspace, system
# image, Valve's kernel), the only place they run, on any host with Docker or Podman. It runs
# privileged: the builds mount /proc, /sys and overlayfs and chroot into an aarch64 root, whose
# programs the host kernel's binfmt handler runs through QEMU (binfmt.sh registers this image's
# qemu-aarch64-static when an x86-64 engine has no handler). The x86-64 clang and lld (LLVM 20)
# compile natively inside that root.
# Ubuntu 24.04 (multi-arch manifest digest). On amd64 its packages come from the archive's
# snapshot of one moment (snapshot.ubuntu.com, which serves the amd64 archive only), so every
# build gets the same versions; arm64 takes ports.ubuntu.com as it is. The snapshot must not be
# older than the base image: its packages (zlib1g) pin the -dev packages' versions.
FROM ubuntu:24.04@sha256:24737cef17f4e22b1977df7d9a05e77c4e28b7da5b8911f8d8ccda136bf3573c
ARG DEBIAN_FRONTEND=noninteractive
ARG SNAPSHOT=20261106T000000Z
# The snapshot is served over HTTPS and the base image has no CA certificates yet: the first
# update and ca-certificates go without checking the server (apt checks the signed indexes and
# every package's hash all the same), everything after it with. A snapshot's indexes are past
# their Valid-Until by design. The kernel's cross compiler only where the host is no arm64 (there
# the native gcc has its name).
RUN set -eu; \
    sources=/etc/apt/sources.list.d/ubuntu.sources; \
    arch=$(dpkg --print-architecture); \
    apt="apt-get -o Acquire::Retries=3"; \
    if [ "$arch" = amd64 ]; then \
      sed -i -E "s#http://(archive|security)\.ubuntu\.com/ubuntu/?#https://snapshot.ubuntu.com/ubuntu/${SNAPSHOT}/#" "$sources"; \
      if grep -E '^URIs:' "$sources" | grep -qv "https://snapshot.ubuntu.com/ubuntu/${SNAPSHOT}/"; then \
        echo "a package source is not the snapshot" >&2; exit 1; \
      fi; \
      apt="$apt -o Acquire::Check-Valid-Until=false"; \
      $apt -o Acquire::https::Verify-Peer=false update; \
      $apt -o Acquire::https::Verify-Peer=false install -y --no-install-recommends ca-certificates; \
    fi; \
    cross=$([ "$arch" = arm64 ] || echo gcc-aarch64-linux-gnu); \
    $apt update; \
    $apt install -y --no-install-recommends \
      bash ca-certificates coreutils curl diffutils findutils tar zstd xz-utils python3 e2fsprogs util-linux \
      clang-20 lld-20 qemu-user-static \
      make gcc libc6-dev $cross flex bison bc git perl cpio libssl-dev libelf-dev dwarves device-tree-compiler; \
    rm -rf /var/lib/apt/lists/*; \
    git config --system --add safe.directory /work  # the checkout is the host user's, git here runs as root
COPY entrypoint.sh /usr/local/bin/overpressure-entrypoint
COPY binfmt.sh /usr/local/bin/overpressure-binfmt
COPY hand-back.sh /usr/local/bin/overpressure-hand-back
RUN chmod 0755 /usr/local/bin/overpressure-entrypoint /usr/local/bin/overpressure-binfmt \
      /usr/local/bin/overpressure-hand-back
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/overpressure-entrypoint"]
CMD ["bash"]
