import { dev } from '$app/environment';
import { goto } from '$app/navigation';
import { page } from '$app/stores';
import { events, os } from '@neutralinojs/lib';
import { get } from 'svelte/store';
import { recording_state } from './recording-store';

/**
 * Idle hotkey listener.
 *
 * While nothing is recording, a tiny `logger --hotkey F9` process watches
 * Ctrl+Shift+<key> globally and prints HOTKEY on press, so a war can be
 * started from inside the game. During a capture the engine watches the same
 * key itself (record page handles that line), so this listener only needs to
 * exist while the state is idle.
 *
 * Deliberately spawned OUTSIDE logger-wrapper: that module manages exactly one
 * process and, worse, start_logger taskkills every logger.exe by image name
 * before each spawn — a status check, a self-test or a capture start will kill
 * this listener as collateral. That is fine and expected: the respawn loop
 * below brings it back a few seconds later, but only while the app is idle,
 * so it never fights the capture engine's own watcher for the key.
 */

/** The only keys the engine accepts; anything else is treated as off. Also
 * the injection guard: config.json is user-editable and this value is
 * interpolated into a spawn command line. */
const VALID_KEYS = ['F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12'];

/**
 * What a hotkey press does, defined once so the layout and the settings page
 * can never wire divergent behavior (the settings copy once lacked the
 * /record guard). Recording feedback (the "started" notification) lives on
 * the record page, where capture health is actually known.
 */
export function default_hotkey_action() {
	if (get(page).url.pathname.startsWith('/record')) return;
	goto('/record?hotkey=1');
}

let proc: os.SpawnedProcess | null = null;
let enabled_key = '';
let on_press: (() => void) | null = null;
let respawn_timer: ReturnType<typeof setTimeout> | null = null;
let handler_attached = false;
// Generation token: bumped on every set_hotkey_listener and stop, checked
// after every await, so a re-key racing a pending spawn can never leave two
// listeners alive or track the wrong one.
let generation = 0;
// Fast-exit breaker: a listener that dies instantly (bad key in a hand-edited
// config, an old engine binary) must not respawn a PyInstaller process every
// 3 seconds forever.
let fast_exits = 0;
let last_spawn_at = 0;
const FAST_EXIT_MS = 1500;
const FAST_EXIT_LIMIT = 3;

function handle_process(evt: CustomEvent) {
	if (!proc || evt.detail.id !== proc.id) return;
	switch (evt.detail.action) {
		case 'stdOut':
			for (const line of String(evt.detail.data).split('\n')) {
				if (line.trim() === 'HOTKEY') on_press?.();
			}
			break;
		case 'exit': {
			proc = null;
			const lived = Date.now() - last_spawn_at;
			if (lived < FAST_EXIT_MS) {
				fast_exits++;
				if (fast_exits >= FAST_EXIT_LIMIT) {
					console.error(
						`[hotkey] listener exited ${fast_exits}x within ${FAST_EXIT_MS}ms of spawning; giving up until the hotkey setting changes`
					);
					return;
				}
			} else {
				fast_exits = 0;
			}
			// Collateral kill from start_logger, or a crash. Come back when
			// (and only when) nothing is recording; the capture engine owns
			// the key during a war.
			schedule_respawn();
			break;
		}
	}
}

function schedule_respawn() {
	if (!enabled_key) return;
	if (respawn_timer) clearTimeout(respawn_timer);
	const my = generation;
	respawn_timer = setTimeout(() => {
		respawn_timer = null;
		if (my !== generation || !enabled_key || proc) return;
		if (get(recording_state) !== 'idle') {
			// Still capturing; try again after the war.
			schedule_respawn();
			return;
		}
		void spawn();
	}, 3000);
}

async function spawn() {
	if (NL_OS !== 'Windows' || !enabled_key || proc) return;
	const my = generation;
	const bin = dev ? 'logger\\dist\\logger\\logger' : 'logger\\logger';
	try {
		last_spawn_at = Date.now();
		const spawned = await os.spawnProcess(`${bin} --hotkey ${enabled_key}`);
		if (my !== generation || proc) {
			// Re-keyed or replaced while the spawn was in flight: this process
			// is an orphan, kill it rather than track two.
			try {
				await os.updateSpawnedProcess(spawned.id, 'exit');
			} catch {
				/* already gone */
			}
			return;
		}
		proc = spawned;
		if (!handler_attached) {
			events.on('spawnedProcess', handle_process);
			handler_attached = true;
		}
	} catch (e) {
		console.error('[hotkey] listener spawn failed', e);
		if (my === generation) schedule_respawn();
	}
}

/** Start (or re-key) the idle listener. Pass '' (or any invalid key) to turn it off. */
export async function set_hotkey_listener(key: string, callback: () => void) {
	on_press = callback;
	const safe_key = VALID_KEYS.includes(key) ? key : '';
	if (enabled_key === safe_key && (proc || respawn_timer)) return;
	generation++;
	enabled_key = safe_key;
	fast_exits = 0;
	await stop_process();
	if (safe_key) {
		if (get(recording_state) === 'idle') await spawn();
		else schedule_respawn();
	}
}

/** Kill the listener without disabling it (used right before an update hands
 * off to the installer, so no orphan holds a lock on logger.exe). */
export async function suspend_hotkey_listener() {
	generation++;
	await stop_process();
}

async function stop_process() {
	if (respawn_timer) {
		clearTimeout(respawn_timer);
		respawn_timer = null;
	}
	const current = proc;
	proc = null;
	if (current) {
		try {
			await os.updateSpawnedProcess(current.id, 'exit');
		} catch {
			/* already gone (taskkill got it) */
		}
	}
}
