# LHPC test-lab image — builds for the host arch. GitHub Codespaces are x86-only, so there
# the aarch64 lhpc-binaries do not apply and every stack SOURCE-builds (meshcom's
# qemu-system-xtensa compiles in ~minutes on x86); on an ARM host `lhpc install <stack>
# --source binary` fetches and RUNS our real binaries with no compile. Either way the
# toolchain below covers both paths. Everything is installed at build time as root, then an
# unprivileged user with NO sudo — the lab's command-safety boundary is that privilege
# drop, not any argv filter. (containerEnv sets LHPC_QEMU_MIN_FREE_INODES=0 so build-qemu.sh's
# inode pre-check passes on overlay/dynamic-inode filesystems that report df -i favail as 0.)
FROM mcr.microsoft.com/devcontainers/python:3.13-trixie

# Toolchain for the SOURCE stacks (kiss/chat/igate gcc, RadioLib cmake stub, socat serial,
# graywolf .deb unpack, binary-channel zstd, git, gpsd binary for the require probe), the
# real nginx for the stackweb proxies, and the GUI/desktop runtime (Xvfb + software GL +
# SDL2 + a null ALSA default) so the GTK (voice) and Kivy (sideband) apps launch headless.
#
# RUNTIME libraries for the binary-channel artifacts (meshcom's qemu + meshtasticd) — the
# union of their index runtime_deps. reticulum's python bindings are system packages
# (venv --system-site-packages).
#
# PLUS every stack's manifest component REQUIRES — the source-build toolchain (meson/ninja)
# and -dev headers that each stack's start/build gate checks. A real Pi installs these via
# its bootstrap; the Codespace must too, so EVERY stack builds and STARTS here (incl. the
# binary meshcom, whose qemu start still gates on the QEMU build toolchain). This is a
# devcontainer-only concern — loraham-images (the real Pi image) is not touched.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
        --no-install-recommends \
        build-essential cmake socat dpkg-dev zstd nginx git python3-venv gpsd \
        pkg-config ca-certificates curl \
        python3-libgpiod python3-spidev \
        libcodec2-dev libgtk-3-dev libasound2-dev \
        xvfb libgtk-3-0 \
        meson ninja-build python3-dev python3-tk zlib1g-dev \
        libbluetooth-dev libgcrypt20-dev libglib2.0-dev libgpiod-dev libi2c-dev \
        libncurses-dev libpixman-1-dev libslirp-dev libssl-dev \
        libulfius-dev libusb-1.0-0-dev libuv1-dev libx11-dev libyaml-cpp-dev \
        libgl1-mesa-dri libglu1-mesa libegl1 libgl1 \
        libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-ttf-2.0-0 libmtdev1 \
        libasound2-plugins alsa-utils \
        libatomic1 libblkid1 libbrotli1 libc6 libcap2 libcom-err2 \
        libcurl3t64-gnutls libffi8 libgcc-s1 libgcrypt20 libglib2.0-0t64 \
        libgmp10 libgnutls30t64 libgpg-error0 libgpiod3 libgssapi-krb5-2 \
        libhogweed6t64 libi2c0 libidn2-0 libjansson4 libk5crypto3 \
        libkeyutils1 libkrb5-3 libkrb5support0 libldap2 libmicrohttpd12t64 \
        libmount1 libnettle8t64 libnghttp2-14 libnghttp3-9 libngtcp2-16 \
        libngtcp2-crypto-gnutls8 liborcania2.3 libp11-kit0 libpcre2-8-0 \
        libpixman-1-0 libpsl5t64 librtmp1 libsasl2-2 libselinux1 libslirp0 \
        libssh2-1t64 libssl3t64 libstdc++6 libsystemd0 libtasn1-6 libudev1 \
        libulfius2.7t64 libunistring5 libusb-1.0-0 libuv1t64 libyaml-cpp0.8 \
        libyder2.0t64 libzstd1 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && printf 'pcm.!default { type plug slave.pcm "null" }\nctl.!default { type null }\n' \
        > /etc/asound.conf

# The unprivileged lab user: not in sudo, no sudoers drop-ins anywhere (the base image
# ships passwordless sudo for `vscode` — removed), no capabilities. Nested invocations
# of sudo/apt/nft fail in the ENVIRONMENT no matter how they are spawned.
RUN useradd -m -s /bin/bash lhpclab \
    && rm -f /etc/sudoers.d/* \
    && deluser vscode sudo 2>/dev/null || true

USER lhpclab
WORKDIR /home/lhpclab
