#!/bin/bash
set -e  

log() {
    echo "[INFO] $1"
}

# Loud, to stderr: for non-fatal problems that still need to be seen in a long
# build log (a swallowed setcap failure leaves a binary that can't sniff without
# root, so it must not hide behind an [INFO] line).
warn() {
    echo "[WARNING] $1" >&2
}

error_exit() {
    echo "[ERROR] $1"
    exit 1
}

# Build the logger
log "Building the logger..."
cd logger || error_exit "Logger directory missing"
log "Creating a virtual environment and installing dependencies..."
python3 -m venv .venv || error_exit "Failed to create virtual environment."
source .venv/bin/activate || error_exit "Failed to activate virtual environment."
pip install scapy pyinstaller  || error_exit "Failed to install dependencies."


log "Building the logger..."
{
    pyinstaller logger.spec -y
} || error_exit "PyInstaller build failed."


# Copy everything from logger/dist/logger to dist/cogm-logger/logger/
log "Copying logger files..."
cd .. || error_exit "Failed to return to parent directory."
mkdir -p dist/cogm-logger/logger
cp -r logger/dist/logger/* dist/cogm-logger/logger/ || error_exit "Failed to copy logger files."
# Bundle the packet config so the status check finds it (live capture ignores it).
[ -f config.ini ] && cp config.ini dist/cogm-logger/config.ini


# Install Dependencies for the Frontend
log "Installing frontend dependencies..."
cd ui || error_exit "Missing UI directory"
npm install || error_exit "NPM install failed."


# Compile the program
log "Compiling the program..."
cd .. || error_exit "Failed to return to parent directory."
neu() {
    ./ui/node_modules/.bin/neu "$@"
}

# --latest pulls the current Neutralino binaries. The version pinned in
# neutralino.config.json is too old to emit a Linux app binary at all, so a
# plain `neu update` left `neu build` with nothing to package on Linux. (This
# is build.sh / from-source only; the Windows CI keeps its own pinned neu.)
neu update --latest || error_exit "Neutralino.js update failed."
# The Linux runtime that --latest fetches lands in .tmp/ but `neu build` expects
# it under bin/, so stage it there or the build produces no app binary.
mkdir -p bin
if [ ! -f bin/neutralino-linux_x64 ] && [ -f .tmp/neutralino-linux_x64 ]; then
    log "Installing Neutralino runtime into bin/"
    cp .tmp/neutralino-linux_x64 bin/
    chmod +x bin/neutralino-linux_x64
fi
neu build --release || error_exit "Neutralino.js build failed."

# Patch ELF interpreter if the system uses a non-/lib64 path (e.g. Ubuntu/Debian/Pop!_OS)
INTERP=$(patchelf --print-interpreter ./dist/cogm-logger/logger/logger 2>/dev/null)
if [ -n "$INTERP" ] && [ ! -e "$INTERP" ]; then
    SYSTEM_INTERP=$(find /lib /lib64 /usr/lib -name "ld-linux-x86-64.so.2" 2>/dev/null | head -n1)
    if [ -n "$SYSTEM_INTERP" ]; then
        log "Patching ELF interpreter: $INTERP -> $SYSTEM_INTERP"
        patchelf --set-interpreter "$SYSTEM_INTERP" ./dist/cogm-logger/logger/logger || error_exit "patchelf failed. Install it with: sudo apt install patchelf"
    else
        error_exit "ELF interpreter '$INTERP' not found on this system and no replacement located. Install patchelf and check your libc."
    fi
fi

# Allow scapy to sniff without root. neu build names the app binary per arch
# (cogm-logger-linux_x64 on amd64, cogm-logger-linux_arm64 on ARM), so glob it
# instead of hardcoding _x64, and don't let a missing/odd-named binary abort
# the whole build under `set -e`. The capability only lets the logger capture
# without sudo; if it's skipped the user can still launch the app with sudo.
log "Setting capabilities for the logger binary..."
# Cap EVERY arch binary present, not just the first: dist/ isn't cleaned between
# builds, so a stale x64 binary can sit next to a fresh arm64 one, and capping
# only one would silently ship the other without cap_net_raw. The glob stays
# literal when nothing matches, so the [ -f ] guard handles the none case.
capped_app=0
for app_bin in ./dist/cogm-logger/cogm-logger-linux_*; do
    [ -f "$app_bin" ] || continue
    capped_app=1
    sudo setcap cap_net_raw=eip "$app_bin" || warn "setcap failed on $app_bin (run the app with sudo, or re-run setcap manually)."
done
if [ "$capped_app" -eq 0 ]; then
    warn "no cogm-logger-linux_* binary found in dist/cogm-logger. Check your architecture and that 'neu build' produced it. Skipping setcap on the app binary."
fi
sudo setcap cap_net_raw=eip ./dist/cogm-logger/logger/logger || warn "setcap failed on the bundled python logger binary (capture needs cap_net_raw; run with sudo or re-run setcap manually)."

log "Build completed. Compiled files are in dist/cogm-logger/"
log "All tasks completed successfully."
