#!/bin/bash
# Prepare a Fedora box for Erinne: packages, the erinne user, llama.cpp built
# for Vulkan in /opt/llama.cpp-vulkan, the GPU drop-ins and the embedding model.
# Safe to run again: finished steps are skipped.
#
#   sudo bash /opt/erinne/deploy/linux/preinstall.sh
#
# Overrides (environment):
#   LLAMA_TAG=b11756        llama.cpp release to build
#   GPU_MATCH='RX 580'      text identifying Erinne's card in --list-devices
#   ERINNE_GPU=Vulkan1      skip detection and use this llama.cpp device
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

step "erinne user and folders"
id erinne >/dev/null 2>&1 || useradd --system --home-dir /var/lib/erinne --shell /sbin/nologin erinne
install -d -m 755 /etc/erinne
install -d -o erinne -g erinne -m 750 /var/backups/erinne

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

step "Erinne's GPU"
devices=$(LD_LIBRARY_PATH=$LLAMA_DIR "$LLAMA_DIR/llama-server" --list-devices 2>&1)
echo "$devices" | grep -E '^\s*Vulkan[0-9]+:' || true
if [ -z "${ERINNE_GPU:-}" ]; then
    ERINNE_GPU=$(echo "$devices" | grep -F "$GPU_MATCH" | grep -oE 'Vulkan[0-9]+' | head -1 || true)
    [ -n "$ERINNE_GPU" ] || die "no Vulkan device matching '$GPU_MATCH'; set GPU_MATCH or ERINNE_GPU"
fi
echo "using $ERINNE_GPU"
for unit in erinne-generator erinne-embedder; do
    install -d "/etc/systemd/system/$unit.service.d"
    printf '[Service]\n# Written by preinstall.sh: the llama.cpp device for Erinne'"'"'s card.\nEnvironment=ERINNE_GPU=%s\n' \
        "$ERINNE_GPU" > "/etc/systemd/system/$unit.service.d/gpu.conf"
done
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
chown erinne:erinne "$MODELS/$EMBED_MODEL"
if [ -f "$MODELS/$GEN_MODEL" ]; then
    chmod o+r "$MODELS/$GEN_MODEL"
else
    echo "WARNING: $MODELS/$GEN_MODEL is missing; copy it there before starting erinne-generator" >&2
fi

step "Done"
cat <<EOF
llama.cpp $LLAMA_TAG (Vulkan) is in $LLAMA_DIR and Erinne will use $ERINNE_GPU.
Next, from deploy/linux/README.md: uv sync (section 1), then settings (2) and services (3).
EOF
