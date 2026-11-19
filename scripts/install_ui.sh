#!/usr/bin/env bash
# Install a RammpUI build from a rammp-ui test-build release and point
# /opt/rammp/ui/latest at it, which is what sheppy's unreal_ui node runs.
# Run it on the robot as the user sheppy runs as (not with sudo):
#
#   scripts/install_ui.sh                     # the newest test build
#   scripts/install_ui.sh test-main-25ead58   # a given build; also how to roll back
#
# A build already under /opt/rammp/ui is not downloaded again. Restart
# unreal_ui afterwards (`r` on it in the sheppy TUI) to run the new build.
#
# The test builds are draft releases, which GitHub shows only to accounts with
# write access to rammp-org/rammp-ui: `gh auth login` as one first.
#
# The first run on a host also creates /opt/rammp/ui (sudo, once) and copies in
# the Vulkan loader the UI needs, hand-built from KhronosGroup/Vulkan-Loader
# 1.4.368 into ~/Vulkan-Loader/build (the system loader is too old).
set -euo pipefail

repo=rammp-org/rammp-ui
root=${RAMMP_UI_ROOT:-/opt/rammp/ui}

if [ ! -d "$root" ]; then
    sudo install -d -o "$(id -un)" -g "$(id -gn)" "$root"
fi
if [ ! -e "$root/vulkan-loader/libvulkan.so.1" ]; then
    src=$HOME/Vulkan-Loader/build/loader
    if [ ! -e "$src/libvulkan.so.1" ]; then
        echo "No Vulkan loader at $src: build KhronosGroup/Vulkan-Loader 1.4.368 there first." >&2
        exit 1
    fi
    mkdir -p "$root/vulkan-loader"
    cp -P "$src"/libvulkan.so* "$root/vulkan-loader/"
fi

tag=${1:-$(gh release list -R "$repo" --limit 1 --json tagName --jq '.[0].tagName')}
if [ -z "$tag" ]; then
    echo "No release found in $repo. The test builds are drafts: is gh logged in as an account with write access?" >&2
    exit 1
fi
asset=$(gh release view "$tag" -R "$repo" --json assets --jq '.assets[].name | select(endswith("-LinuxArm64.zip"))')
build=${asset%.zip}

if [ -d "$root/$build" ]; then
    echo "$build is already installed."
else
    tmp=$(mktemp -d "$root/.install-XXXXXX")
    trap 'rm -rf "$tmp"' EXIT
    echo "Downloading $asset from $tag ..."
    gh release download "$tag" -R "$repo" -p "$asset" -D "$tmp"
    unzip -q "$tmp/$asset" -d "$tmp"
    # Zipped on Windows, so nothing is executable. The launch script makes
    # the UI binary executable itself, but not itself.
    chmod +x "$tmp/$build/RammpUI-Arm64.sh"
    mv "$tmp/$build" "$root/$build"
fi

previous=$(readlink "$root/latest" || true)
ln -sfn "$build" "$root/latest"
echo "latest -> $build${previous:+ (was $previous)}"
