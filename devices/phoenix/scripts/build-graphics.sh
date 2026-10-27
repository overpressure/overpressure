#!/bin/bash
# Build the optional ARM64 graphics tools in an isolated Alpine root using QEMU.
# Usage (Linux/WSL, root): scripts/build-graphics.sh BUILD_ROOT OUTPUT_OVERLAY
# Requires host curl, tar, sha512sum, chroot and a working aarch64 binfmt handler.
# The output excludes proprietary firmware; obtain it from your own stock backup.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)   # devices/phoenix
cache=$(cd "$project/../.." && pwd)/cache      # the repository's
build_root=${1:?specify an isolated Linux-filesystem build directory}
output=${2:?specify an output overlay directory}
[[ $EUID == 0 && "$build_root" = /* && "$build_root" != / ]] || { echo 'Root and an absolute isolated build directory are required' >&2; exit 2; }
mkdir -p "$build_root" "$output"
build_root=$(realpath "$build_root")
output=$(realpath "$output")
[[ "$build_root" != / ]] || { echo 'The build root must not resolve to /' >&2; exit 2; }
mkdir -p "$cache"
mesa_version=26.1.6
mesa_sha512=6746b73f5b87e8ff3723eee586b4b5f033dbe6d13cd0309d743b7fa484c350268859ec8ff21aadcbca7a04070cdaa7828146dd28dde4b1935bb879891d687f0b
archive="$cache/mesa-$mesa_version.tar.xz"
if [[ ! -s "$archive" ]]; then curl --fail --location --output "$archive" "https://archive.mesa3d.org/mesa-$mesa_version.tar.xz"; fi
printf '%s  %s\n' "$mesa_sha512" "$archive" | sha512sum --check
if [[ ! -f "$build_root/bin/busybox" ]]; then
    mini="$cache/v3.24/releases/alpine-minirootfs-3.24.2-aarch64.tar.gz"
    [[ -s "$mini" && -s "$mini.sha256" ]] || { echo 'Run the normal POC build first to obtain verified Alpine minirootfs' >&2; exit 1; }
    (cd "$(dirname "$mini")" && sha256sum --check "$(basename "$mini.sha256")")
    tar -xzf "$mini" -C "$build_root"
fi
mkdir -p "$build_root/etc/ssl/certs" "$build_root/src/pico4" "$build_root/artifacts/pico4"
cp /etc/resolv.conf "$build_root/etc/resolv.conf"
cp /etc/ssl/certs/ca-certificates.crt "$build_root/etc/ssl/certs/"
mounted=()
cleanup() { for ((i=${#mounted[@]}-1;i>=0;i--)); do umount "${mounted[$i]}"; done; }
trap cleanup EXIT
for name in dev proc sys; do
    if ! mountpoint -q "$build_root/$name"; then mount --bind "/$name" "$build_root/$name"; mounted+=("$build_root/$name"); fi
done
chroot "$build_root" /sbin/apk add --no-cache build-base meson ninja pkgconf python3 py3-mako py3-yaml py3-packaging bison flex libdrm-dev vulkan-loader-dev spirv-tools-dev zlib-dev zstd-dev expat-dev linux-headers cmake glslang-dev libarchive-dev elfutils-dev
[[ -d "$build_root/src/mesa-$mesa_version" ]] || tar -xJf "$archive" -C "$build_root/src"
cp "$project/src/graphics/"* "$build_root/src/pico4/"
chroot "$build_root" /bin/sh -eu -c '
cd /src/mesa-26.1.6
if [ ! -f build/build.ninja ]; then
    meson setup build --wrap-mode=nofallback -Dbuildtype=release -Dvulkan-drivers=freedreno -Dfreedreno-kmds=kgsl -Dgallium-drivers= -Dplatforms= -Dglx=disabled -Degl=disabled -Dopengl=false -Dgbm=disabled -Dgles1=disabled -Dgles2=disabled -Dllvm=disabled -Dvideo-codecs= -Dbuild-tests=false -Dglvnd=disabled -Dlibunwind=disabled -Dvalgrind=disabled
fi
ninja -C build -j8
mkdir -p /artifacts/pico4/usr/bin /artifacts/pico4/usr/lib /artifacts/pico4/usr/share/pico4-poc/graphics /artifacts/pico4/usr/share/vulkan/icd.d
cc -std=c11 -O2 -Wall -Wextra -Werror /src/pico4/pico4-vulkan-test.c -lvulkan -o /artifacts/pico4/usr/bin/pico4-vulkan-test
cc -std=c11 -O2 -Wall -Wextra -Werror -I/usr/include/libdrm /src/pico4/pico4-kms-test.c -ldrm -o /artifacts/pico4/usr/bin/pico4-kms-test
glslang -V /src/pico4/fullscreen.vert -o /artifacts/pico4/usr/share/pico4-poc/graphics/fullscreen.vert.spv
glslang -V /src/pico4/quadrants.frag -o /artifacts/pico4/usr/share/pico4-poc/graphics/quadrants.frag.spv
cp build/src/freedreno/vulkan/libvulkan_freedreno.so /artifacts/pico4/usr/lib/
strip /artifacts/pico4/usr/lib/libvulkan_freedreno.so /artifacts/pico4/usr/bin/pico4-*-test
apk info -v > /artifacts/pico4/usr/share/pico4-poc/graphics/build-packages.txt
'
cp -r "$build_root/artifacts/pico4/." "$output/"
cat > "$output/usr/share/vulkan/icd.d/pico4-turnip.json" <<'EOF'
{"file_format_version":"1.0.0","ICD":{"library_path":"/usr/lib/libvulkan_freedreno.so","api_version":"1.3.0"}}
EOF
printf '%s\n' "Mesa $mesa_version, KGSL only, source SHA512 $mesa_sha512" > "$output/usr/share/pico4-poc/graphics/provenance.txt"
printf 'Graphics overlay: %s\n' "$output"
