"""Global record hotkey (Windows).

Polls GetAsyncKeyState for Ctrl+Shift+<F-key> and prints a single "HOTKEY"
line (flushed) on each press. Polling instead of RegisterHotKey on purpose:
two processes can watch the same combination without ERROR_HOTKEY_ALREADY_
REGISTERED, which matters here because the idle listener (spawned by the UI
while nothing records) and the capture engine's own thread (during a war)
take turns owning the key, and the handover is not atomic. GetAsyncKeyState
also reads fine while the game holds focus, which is the whole point.

Two entry points:
  - run_hotkey_listener(key): foreground loop, the idle listener process.
  - start_hotkey_thread(key): daemon thread alongside a live capture, so the
    same key stops the war without alt-tabbing.
"""

import sys
import time
import threading

# Virtual-key codes for the allowed function keys. Modifiers are fixed at
# Ctrl+Shift: enough to never collide with game binds, small enough to hit
# mid-fight.
_VK_FKEYS = {
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
}
_VK_CONTROL = 0x11
_VK_SHIFT = 0x10

# GetAsyncKeyState is level-sampled here (the "pressed since last call" low
# bit is unreliable across processes), so a tap shorter than one poll gap is
# lost. 15ms makes that physically implausible and still costs nothing.
_POLL_S = 0.015


def _pressed(user32, vk: int) -> bool:
    # High bit set = key currently down.
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def _poll_loop(key: str) -> None:
    if sys.platform != "win32":
        return
    vk = _VK_FKEYS.get(key.upper())
    if vk is None:
        return
    import ctypes

    user32 = ctypes.windll.user32
    was_down = False
    while True:
        down = (
            _pressed(user32, _VK_CONTROL)
            and _pressed(user32, _VK_SHIFT)
            and _pressed(user32, vk)
        )
        # Edge-triggered: one line per press, however long the keys are held.
        if down and not was_down:
            # One atomic write, not print(): print writes the text and the
            # newline separately, and this thread shares stdout with the
            # sniffer's kill lines — an interleave would eat the stop AND
            # corrupt one kill record.
            sys.stdout.write("HOTKEY\n")
            sys.stdout.flush()
        was_down = down
        time.sleep(_POLL_S)


def start_hotkey_thread(key: str) -> None:
    """Watch the hotkey alongside a live capture. Daemon so it never keeps
    the process alive after the sniffer stops."""
    if sys.platform != "win32" or not key:
        return
    threading.Thread(target=_poll_loop, args=(key,), daemon=True).start()


def run_hotkey_listener(key: str) -> None:
    """Standalone idle listener: block on the poll loop until killed."""
    if sys.platform != "win32":
        print("HOTKEY_UNSUPPORTED", flush=True)
        return
    if key.upper() not in _VK_FKEYS:
        print("HOTKEY_BADKEY", flush=True)
        return
    _poll_loop(key)
