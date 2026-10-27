#!/bin/bash
# Prepare a Fedora box for Mnemerynne: packages, the mnemerynne user, llama.cpp built
# for Vulkan in /opt/llama.cpp-vulkan, the GPU drop-ins and the embedding model.
# Safe to run again: finished steps are skipped.
#
#   sudo bash /opt/mnemerynne/deploy/linux/preinstall.sh
#
# Overrides (environment):
#   LLAMA_TAG=b11756        llama.cpp release to build
#   GPU_MATCH='RX 580'      text identifying Mnemerynne's card in --list-devices
#
# MicroJames's CUDA build in /opt/llama.cpp is not touched.
set -euo pipefail

LLAMA_TAG=${LLAMA_TAG:-b11756}
GPU_MATCH=${GPU_MATCH:-RX 580}
LLAMA_DIR=/opt/llama.cpp-vulkan
MODELS=/opt/models
EMBED_MODEL=Qwen3-Embedding-0.6B-Q8_0.gguf
EMBED_URL=https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/$EMBED_MODEL
EMBED_SHA_PREFIX=06507c7b4268
GEN_MODEL=LFM2.5-2.6B-Q4_K_M.gguf

step() { printf '\n== %s\n' "$*"; }
die() { printf 'preinstall: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run with sudo"
grep -q '^ID=fedora' /etc/os-release || die "this script is for Fedora"

# The root volume on a default Fedora Server install is only 15 GB.
free_gb=$(df --output=avail -BG / | tail -1 | tr -dc 0-9)
[ "$free_gb" -ge 5 ] || die "only ${free_gb} GB free on /; grow it first (lvextend -r)"

step "Packages"
dnf install -y \
    uv ocrmypdf tesseract-langpack-eng \
    git cmake gcc-c++ glslc glslang vulkan-headers vulkan-loader-devel spirv-headers-devel \
    mesa-vulkan-drivers vulkan-tools nvtop policycoreutils-python-utils curl

step "ffmpeg and video drivers (Media)"
# Media (Settings → Media) probes and transcodes with ffmpeg. Fedora's own ffmpeg-free and
# mesa-va-drivers can't decode or encode H.264/HEVC, so both come from RPM Fusion: full ffmpeg
# (with h264_vaapi and h264_nvenc) and the VAAPI driver for the RX 580. vainfo checks the card.
fedora=$(rpm -E %fedora)
rpm -q rpmfusion-free-release >/dev/null 2>&1 \
    || dnf install -y "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$fedora.noarch.rpm"
# install --allowerasing replaces ffmpeg-free and mesa-va-drivers where they are installed, and
# simply installs where they aren't (a swap does nothing then).
if ! { command -v ffmpeg >/dev/null && ffmpeg -hide_banner -encoders 2>/dev/null | grep -q h264_vaapi; }; then
    dnf install -y --allowerasing ffmpeg
fi
dnf install -y --allowerasing mesa-va-drivers-freeworld libva-utils
command -v ffmpeg >/dev/null || die "ffmpeg didn't install; check that the RPM Fusion repository is enabled"

step "mnemerynne user and folders"
id mnemerynne >/dev/null 2>&1 || useradd --system --home-dir /var/lib/mnemerynne --shell /sbin/nologin mnemerynne
install -d -m 755 /etc/mnemerynne
install -d -o mnemerynne -g mnemerynne -m 750 /var/backups/mnemerynne

step "llama.cpp $LLAMA_TAG for Vulkan in $LLAMA_DIR"
# A shallow clone reports "build 1", so the tag is recorded in a file instead.
if [ -x "$LLAMA_DIR/llama-server" ] && [ "$(cat "$LLAMA_DIR/TAG" 2>/dev/null)" = "$LLAMA_TAG" ]; then
    echo "already built ($LLAMA_TAG)"
else
    src=$(mktemp -d /var/tmp/llama-vulkan.XXXXXX)
    trap 'rm -rf "$src"' EXIT
    git clone --depth 1 --branch "$LLAMA_TAG" https://github.com/ggml-org/llama.cpp "$src"
    cmake -S "$src" -B "$src/build" -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
    cmake --build "$src/build" -j"$(nproc)" --target llama-server llama-bench
    rm -rf "$LLAMA_DIR"
    install -d "$LLAMA_DIR"
    cp -a "$src"/build/bin/llama-server "$src"/build/bin/llama-bench "$src"/build/bin/*.so* "$LLAMA_DIR"/
    echo "$LLAMA_TAG" > "$LLAMA_DIR/TAG"
fi
semanage fcontext -a -t bin_t "$LLAMA_DIR(/.*)?" 2>/dev/null || true
restorecon -R "$LLAMA_DIR"

step "Mnemerynne's GPU"
# mnemerynne-generator loads only Mesa's Radeon Vulkan driver, so its card is
# always Vulkan0 (see the unit). Check that with the same restriction.
RADEON_ICD=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json
[ -f "$RADEON_ICD" ] || die "no $RADEON_ICD; is mesa-vulkan-drivers installed?"
devices=$(VK_DRIVER_FILES=$RADEON_ICD VK_ICD_FILENAMES=$RADEON_ICD LD_LIBRARY_PATH=$LLAMA_DIR \
    "$LLAMA_DIR/llama-server" --list-devices 2>&1)
echo "$devices" | grep -E '^\s*Vulkan[0-9]+:' || true
echo "$devices" | grep -E '^\s*Vulkan0:' | grep -qF "$GPU_MATCH" \
    || die "Vulkan0 with only the Radeon driver isn't '$GPU_MATCH'; set GPU_MATCH or check the card"
# Older installs pinned a device number here, which could point at the wrong card.
rm -f /etc/systemd/system/mnemerynne-generator.service.d/gpu.conf
rm -f /etc/systemd/system/mnemerynne-embedder.service.d/gpu.conf
[ -x /opt/llama.cpp/llama-server ] || \
    echo "WARNING: no CUDA llama.cpp in /opt/llama.cpp; mnemerynne-embedder needs it (or run it on the CPU, see its unit)" >&2
systemctl daemon-reload

step "Models in $MODELS"
install -d "$MODELS"
if [ ! -f "$MODELS/$EMBED_MODEL" ]; then
    curl -fL --retry 3 -o "$MODELS/$EMBED_MODEL.part" "$EMBED_URL"
    mv "$MODELS/$EMBED_MODEL.part" "$MODELS/$EMBED_MODEL"
fi
sha=$(sha256sum "$MODELS/$EMBED_MODEL" | cut -c1-${#EMBED_SHA_PREFIX})
[ "$sha" = "$EMBED_SHA_PREFIX" ] \
    || die "$EMBED_MODEL checksum starts $sha, expected $EMBED_SHA_PREFIX; delete it and run again"
chown mnemerynne:mnemerynne "$MODELS/$EMBED_MODEL"
if [ -f "$MODELS/$GEN_MODEL" ]; then
    chmod o+r "$MODELS/$GEN_MODEL"
else
    echo "WARNING: $MODELS/$GEN_MODEL is missing; copy it there before starting mnemerynne-generator" >&2
fi

step "Done"
cat <<EOF
llama.cpp $LLAMA_TAG (Vulkan) is in $LLAMA_DIR and the generator will use the $GPU_MATCH (Vulkan0).
Next, from deploy/linux/README.md: uv sync (section 1), then settings (2) and services (3).
EOF
