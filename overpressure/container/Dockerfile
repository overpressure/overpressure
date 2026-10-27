# overpressure's build container: the Linux side of the headsets' builds (userspace, system
# image, stock firmware extraction) on any host with Docker or Podman. It runs privileged: the
# builds mount /proc, /sys and overlayfs and chroot into an aarch64 root, which the host kernel's
# binfmt handler (qemu-aarch64 with the F flag) runs; Docker Desktop registers one. The host's
# x86-64 clang and lld (LLVM 20) compile natively inside that root.
# Ubuntu 24.04, the release the builds are tested on (multi-arch manifest digest).
FROM ubuntu:24.04@sha256:24737cef17f4e22b1977df7d9a05e77c4e28b7da5b8911f8d8ccda136bf3573c
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash ca-certificates coreutils curl tar zstd xz-utils python3 brotli e2fsprogs util-linux \
      clang-20 lld-20 \
 && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /usr/local/bin/overpressure-entrypoint
RUN chmod 0755 /usr/local/bin/overpressure-entrypoint
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/overpressure-entrypoint"]
CMD ["bash"]
