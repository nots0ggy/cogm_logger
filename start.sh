cd ./dist/cogm-logger/ || echo "[ERROR]: Missing DIST. Did you build the project?"

# WebKitGTK (Neutralino's Linux webview) loops/flickers on startup with the wrong
# compositing setup. Force the X11 backend, which routes around the buggy native
# Wayland DMABUF path (works under XWayland on a Wayland session too), and disable
# the DMABUF renderer, the usual culprit on NVIDIA and some Mesa builds. The old
# script set DISABLE_COMPOSITING and FORCE_COMPOSITING at the same time, which is
# contradictory; this is the sane baseline. If a machine still loops, the next
# fallback is WEBKIT_DISABLE_COMPOSITING_MODE=1, then LIBGL_ALWAYS_SOFTWARE=1.
export GDK_BACKEND=x11
export WEBKIT_DISABLE_DMABUF_RENDERER=1
#./cogm-logger-linux_x64 -- --window-enable-inspector
./cogm-logger-linux_x64
