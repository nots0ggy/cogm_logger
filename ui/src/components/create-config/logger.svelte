<script lang="ts">
	import VirtualList from '@sveltejs/svelte-virtual-list';
	import { open_save_location } from '../../logic/file';
	import LoadingIndicator from '../../svelte-ui/elements/loading-indicator.svelte';
	import IoMdSettings from 'svelte-icons/io/IoMdSettings.svelte';
	import { find_all_indicies, show_toast } from '../../svelte-ui/util';
	import Button from '../../svelte-ui/elements/button.svelte';
	import Checkbox from '../../svelte-ui/elements/checkbox.svelte';
	import ConfigModal from './config.modal.svelte';
	import CogmUploadModal from '../cogm-upload-modal.svelte';
	import {
		update_config,
		type Config,
		type LogType,
		get_date,
		get_formatted_date,
		get_config,
		hexToString,
		calculate_kd,
		save_name_order_sample,
		PERSONAL_FAMILY_NAME_KEY
	} from '../../components/create-config/config';
	import { filesystem, os, storage } from '@neutralinojs/lib';
	import { onMount } from 'svelte';
	import { ModalManager } from '../../svelte-ui/modal/modal-store';
	import Icon from '../../svelte-ui/elements/icon.svelte';
	import Select from './select.svelte';
	import { dev } from '$app/environment';
	import GuildInfos from './guild-infos.svelte';

	export let logs: LogType[];
	export let height = 155;
	export let loading = false;
	// The on-disk crash-recovery file backing these logs, when there is one
	// (the live record session, or a recovered session). Cleared once the
	// user has preserved the logs via Save or Upload, so the home screen
	// doesn't keep offering to recover an already-saved session.
	export let session_path: string | null = null;
	// The on-disk full-packet .pcap for the live session, when one is being
	// written. Copied next to the saved .log so the raw bytes always travel with
	// the war.
	export let pcap_path = '';

	async function clear_session() {
		if (!session_path) return;
		// TEMPORARY (remove once the kill offset is locked in): before deleting the
		// raw session, stash a copy alongside it (raw-<name>) so a Save/Upload
		// doesn't destroy the per-event hex we need to recalibrate the kill byte.
		// The recover scan only matches session-*.log, so a raw-* copy won't
		// re-surface. Best effort: keeping the copy must never block the save.
		try {
			const raw = await filesystem.readFile(session_path);
			if (raw) {
				const sep = session_path.includes('\\') ? '\\' : '/';
				const folder = session_path.substring(0, session_path.lastIndexOf(sep)) || '.';
				const name = session_path.split(/[\\/]/).pop() || 'session.log';
				await filesystem.writeFile(`${folder}${sep}raw-${name}`, raw);
			}
		} catch {
			/* ignore: the save proceeds regardless */
		}
		try {
			await filesystem.remove(session_path);
		} catch {
			/* best-effort; a leftover file only means an extra recover offer */
		}
		session_path = null;
	}

	let possible_name_offsets: { offset: number; count: number }[][] = [];
	let name_indicies: number[] = [0, 0, 0, 0, 0];

	let player_one_index = 0;
	let player_two_index = 1;
	let guild_index = 2;

	let possible_kill_offsets: number[] = [];
	let kill_index = 0;

	let config: Config;
	let auto_scroll = true;
	let live_output_path = '';
	const personal_stats_storage_key = PERSONAL_FAMILY_NAME_KEY;
	let personal_family_name = '';
	// Saved once per session, after the first kill, so Settings can show the
	// name-order panel without a live war.
	let sample_saved = false;

	onMount(async () => {
		config = await get_config();
		// Restore the saved name order (which captured column is Killer / Victim /
		// Guild) so a choice made in one war, or in Settings, carries to the next.
		// Validate against the 5 columns and require three distinct indices; fall
		// back to the positional default if anything is off. Safe before kills
		// arrive: nothing reads these indices until logs exist and the columns are
		// rebuilt to five.
		const ord = config.name_order;
		if (
			ord &&
			[ord.killer, ord.victim, ord.guild].every(
				(i) => Number.isInteger(i) && i >= 0 && i < 5
			) &&
			new Set([ord.killer, ord.victim, ord.guild]).size === 3
		) {
			player_one_index = ord.killer;
			player_two_index = ord.victim;
			guild_index = ord.guild;
		}
		// Seed the 3-column default only for a fresh live session. A bulk-loaded
		// session (recover/open) already had all of its columns rebuilt from its
		// logs by the init-time logs_changed(); flattening them back to three here
		// is what made get_name(3)/get_name(4) throw on export, so Save and Upload
		// silently failed. Leave the rebuilt columns in place.
		if (logs.length === 0) {
			possible_kill_offsets = [DEFAULT_KILL_OFFSET];
			possible_name_offsets = [
				[{ offset: config.player_one, count: 1 }],
				[{ offset: config.player_two, count: 1 }],
				[{ offset: config.guild, count: 1 }]
			];
		}
		auto_scroll = config.auto_scroll;
		live_output_path = config.live_output_path || '';
		personal_family_name = await storage.getData(personal_stats_storage_key).catch(() => '');
	});

	$: persist_auto_scroll(auto_scroll, config);

	// Persisting auto-scroll also rewrites the whole config blob (update_config
	// replaces it), so apply the same guard as update_config_wrapper: re-read
	// record_pcap, which is owned by the record page's full-capture toggle, so
	// toggling auto-scroll in this editor can never revert that toggle.
	async function persist_auto_scroll(_auto_scroll: boolean, _config: Config | undefined) {
		if (!_config) return;
		const persisted = await get_config().catch(() => null);
		await update_config({
			..._config,
			...(persisted ? { record_pcap: persisted.record_pcap } : {}),
			auto_scroll: _auto_scroll
		});
	}

	$: {
		if (logs.length > 0) {
			logs_changed();
		} else {
			scroll(true);
		}
	}

	$: if (!sample_saved && config && logs.length > 0) {
		sample_saved = true;
		save_name_order_sample({
			names: logs[0].names.map((n) => ({ name: n.name, offset: n.offset }))
		});
	}

	function logs_changed() {
		auto_scroll && setTimeout(scroll);

		// The last clause covers bulk-loaded sessions (recover/open): logs
		// arrive in one shot past 50, so calculate_config would otherwise never
		// run and possible_name_offsets stays at the 3-entry default, making
		// get_name(3/4) throw on export. In the live record flow the offsets
		// are fully built during the 1..49 growth, so this clause is false then.
		if (
			logs.length < 50 ||
			logs.length % 100 === 0 ||
			possible_name_offsets.length < (logs[0]?.names.length ?? 0)
		) {
			possible_kill_offsets = resolve_kill_offset(logs);
			calculate_config();
		} else {
			write_live_output();
		}
	}

	async function calculate_config() {
		possible_name_offsets = possible_name_offsets.map((list) =>
			list.map((n) => ({ ...n, count: 0 }))
		);
		// get all offsets for each name and count how many times they appear
		for (const log of logs) {
			for (let i = 0; i < log.names.length; i++) {
				const name = log.names[i];
				if (possible_name_offsets[i]) {
					const index = possible_name_offsets[i].findIndex((n) => n.offset === name.offset);
					if (index !== -1) {
						possible_name_offsets[i][index].count++;
					} else {
						possible_name_offsets[i].push({ offset: name.offset, count: 1 });
					}
				} else {
					possible_name_offsets[i] = [{ offset: name.offset, count: 1 }];
				}
			}
		}

		// sort by number of times they appear
		for (let i = 0; i < possible_name_offsets.length; i++) {
			possible_name_offsets[i] = possible_name_offsets[i].sort((a, b) => b.count - a.count);
		}

		//get all identifiers and count them
		const identifiers = new Map<string, number>();
		for (const log of logs) {
			if (identifiers.has(log.identifier)) {
				identifiers.set(log.identifier, identifiers.get(log.identifier)! + 1);
			} else {
				identifiers.set(log.identifier, 1);
			}
		}
		// get the most common identifier
		const identifier = Array.from(identifiers.entries())
			.sort((a, b) => b[1] - a[1])
			.map((a) => a[0])[0];

		await update_config_wrapper(identifier);
	}

	async function update_config_wrapper(identifier?: string) {
		// calculate_config() runs once at init, before onMount loads config, for a
		// bulk-loaded session (recover/open). Persisting then would write a config
		// with everything but the offsets undefined and drop the capture settings
		// (all_interfaces, ip_filter, auto_scroll, live_output_path). Skip it; the
		// columns are still built for the view, and the next real save persists a
		// full config.
		if (!config) return;
		// record_pcap is owned by the record page's full-capture toggle, not this
		// editor, which has no UI for it. This editor loaded its config snapshot at
		// mount; if the user flips the toggle after that, the snapshot is stale, and
		// since update_config replaces the whole stored blob, writing it here would
		// revert the toggle. Re-read just that field so we never clobber it.
		const persisted = await get_config().catch(() => null);
		config = {
			...config,
			...(persisted ? { record_pcap: persisted.record_pcap } : {}),
			patch: get_date(),
			identifier: identifier || config.identifier,
			player_one: possible_name_offsets[player_one_index][name_indicies[player_one_index]].offset,
			player_two: possible_name_offsets[player_two_index][name_indicies[player_two_index]].offset,
			guild: possible_name_offsets[guild_index][name_indicies[guild_index]].offset,
			kill: possible_kill_offsets[kill_index],
			// Persist the order as column indices so it carries to the next war and
			// can be read/edited from Settings.
			name_order: { killer: player_one_index, victim: player_two_index, guild: guild_index }
		};
		await update_config(config);
		await write_live_output();
	}

	$: get_name_options = (i: number, log: LogType) => {
		const names = possible_name_offsets
			/* .filter((_, index) => index !== i) */
			.map((list, index) => {
				const selected = name_indicies[index];
				return hexToString(log.hex.slice(list[selected].offset, list[selected].offset + 64))
					.replaceAll('\0', '')
					.replaceAll(' ', '');
			});
		return names;
	};

	$: get_name = (i: number, log: LogType) => {
		const list = possible_name_offsets[i];
		const selected = name_indicies[i];
		// A restored name-order index can momentarily point past the columns
		// before they're rebuilt from the first kill. Return empty rather than
		// throw; the next reactive pass resolves it once the columns exist.
		if (!list || !list[selected]) return '';
		return hexToString(log.hex.slice(list[selected].offset, list[selected].offset + 64))
			.replaceAll('\0', '')
			.replaceAll(' ', '');
	};

	// The resolved name in each captured column for the first kill, shown as
	// chips in the name-order panel so you can see what was captured before
	// assigning Killer/Victim/Guild. Empty columns show as a dash.
	$: captured_names =
		logs.length > 0 && possible_name_offsets.length > 0
			? possible_name_offsets.map((_offsets, i) => get_name(i, logs[0]) || '-')
			: [];

	// The calibrated kill-direction byte (2026-06-14): byte 7, the 3rd byte after
	// the 5-byte packet identifier. hex[15] (its low nibble) is '1' when the
	// subject got the kill, '0' when they died. Validated against a full node-war
	// board (error 4/1033, vs 448 for the old config.ini kill=141). This replaces
	// both the stale 141 and the find_kill_offset heuristic, which ranked a
	// UTF-16 name byte over the real flag (it prefers mostly-'00' bytes, but the
	// direction byte is ~50/50, so it loses).
	const DEFAULT_KILL_OFFSET = 15;

	// True if the byte at hex[off-1..off] is a clean 0/1 flag (0x00 or 0x01)
	// across every log. require_both also demands both values appear (used by the
	// structural backup to auto-discover the flag; the default offset is trusted
	// unless it's proven to be something other than a 0/1 byte).
	function is_binary_flag(logs: LogType[], off: number, require_both: boolean) {
		let saw0 = false;
		let saw1 = false;
		for (const log of logs) {
			if (off >= log.hex.length || log.hex[off - 1] !== '0') return false;
			const c = log.hex[off];
			if (c === '0') saw0 = true;
			else if (c === '1') saw1 = true;
			else return false;
		}
		return require_both ? saw0 && saw1 : true;
	}

	// Structural backup: the lone post-identifier byte (outside the name fields)
	// that reads as a clean {0x00,0x01} flag with both values seen. Self-heals if
	// a future BDO patch shifts the direction byte off offset 15.
	function structural_kill_offset(logs: LogType[]) {
		const out: number[] = [];
		const maxlen = Math.min(...logs.map((l) => l.hex.length));
		const names = logs[0]?.names ?? [];
		for (let off = 11; off < maxlen; off++) {
			if (names.some((n) => off >= n.offset && off < n.offset + 64)) continue;
			if (is_binary_flag(logs, off, true)) out.push(off);
		}
		return out;
	}

	// Default to the calibrated byte (offset 15). If it ever stops being a clean
	// 0/1 flag (a packet-layout shift), fall back to structural detection, then to
	// the legacy heuristic as a last resort.
	function resolve_kill_offset(logs: LogType[]) {
		if (logs.length === 0) return [DEFAULT_KILL_OFFSET];
		if (is_binary_flag(logs, DEFAULT_KILL_OFFSET, false)) return [DEFAULT_KILL_OFFSET];
		const structural = structural_kill_offset(logs);
		if (structural.length) return structural;
		return find_kill_offset(logs);
	}

	function find_kill_offset(logs: LogType[]) {
		const all_indicies: number[] = [];
		for (const log of logs) {
			let indicies = find_all_indicies(log.hex, '01');
			indicies = indicies.filter((index) =>
				log.names.every((n) => index > n.offset + 64 || index < n.offset)
			);
			all_indicies.push(...indicies);
		}
		const possible_kill_offsets = new Map<number, number>();
		for (const log of logs) {
			for (const index of all_indicies) {
				if (log.hex.slice(index, index + 2) === '00') {
					if (possible_kill_offsets.has(index)) {
						possible_kill_offsets.set(index, possible_kill_offsets.get(index)! + 1);
					} else {
						possible_kill_offsets.set(index, 1);
					}
				}
			}
		}
		// creates array sorted by value
		const sorted = Array.from(possible_kill_offsets.entries())
			.sort((a, b) => b[1] - a[1])
			.map((a) => a[0] + 1);

		return sorted;
	}

	function update_names(target: 'player_one' | 'player_two' | 'guild', e: Event) {
		if (target === 'player_one') {
			const new_value = parseInt((e.target as HTMLSelectElement).value);
			if (new_value === player_two_index) {
				player_two_index = player_one_index;
			} else if (new_value === guild_index) {
				guild_index = player_one_index;
			}
			player_one_index = new_value;
		} else if (target === 'player_two') {
			const new_value = parseInt((e.target as HTMLSelectElement).value);
			if (new_value === player_one_index) {
				player_one_index = player_two_index;
			} else if (new_value === guild_index) {
				guild_index = player_two_index;
			}
			player_two_index = new_value;
		} else if (target === 'guild') {
			const new_value = parseInt((e.target as HTMLSelectElement).value);
			if (new_value === player_one_index) {
				player_one_index = guild_index;
			} else if (new_value === player_two_index) {
				player_two_index = guild_index;
			}
			guild_index = new_value;
		}
		update_config_wrapper();
	}

	// Best-effort suggestion for the name order: the guild column repeats the
	// most (many players share a guild), and the player's own family name
	// should land in the subject (Killer/player_one) column. Only sets the
	// indices, which stay fully editable; wrapped so it can never crash.
	function auto_detect() {
		try {
			const numCols = possible_name_offsets.length;
			if (numCols < 3 || logs.length === 0) return;

			const cols: string[][] = Array.from({ length: numCols }, () => []);
			for (const log of logs) {
				for (let i = 0; i < numCols; i++) cols[i].push(get_name(i, log));
			}
			const distinctRatio = (vals: string[]) => {
				const nonEmpty = vals.filter(Boolean);
				if (nonEmpty.length === 0) return 1;
				return new Set(nonEmpty).size / nonEmpty.length;
			};
			// Guild: lowest distinct ratio (most repeated values).
			let g = 0;
			for (let i = 1; i < numCols; i++) {
				if (distinctRatio(cols[i]) < distinctRatio(cols[g])) g = i;
			}
			// Players: the two remaining columns with the most distinct names.
			const remaining: number[] = [];
			for (let i = 0; i < numCols; i++) if (i !== g) remaining.push(i);
			remaining.sort(
				(a, b) =>
					new Set(cols[b].filter(Boolean)).size - new Set(cols[a].filter(Boolean)).size
			);
			let p1 = remaining[0];
			let p2 = remaining[1];

			// Family name should sit in the subject column; if it shows up more
			// in the other, the order is likely reversed, so swap.
			if (personal_family_name) {
				const fn = personal_family_name.toLowerCase();
				let p1Hits = 0;
				let p2Hits = 0;
				for (const log of logs) {
					if (get_name(p1, log).toLowerCase() === fn) p1Hits++;
					if (get_name(p2, log).toLowerCase() === fn) p2Hits++;
				}
				if (p2Hits > p1Hits) {
					const t = p1;
					p1 = p2;
					p2 = t;
				}
			}

			player_one_index = p1;
			player_two_index = p2;
			guild_index = g;
			update_config_wrapper();
			show_toast('Auto-detected. Check the preview and adjust if needed.', 'success');
		} catch (e) {
			console.error('auto_detect failed', e);
		}
	}

	function scroll(top?: boolean) {
		const container = document.querySelector('svelte-virtual-list-viewport');
		if (container && !top) {
			container.scrollTop = container.scrollHeight;
		} else if (container) {
			container.scrollTop = 0;
		}
	}

	function get_logs_string() {
		let output = '';

		for (const log of logs) {
			let characters = '';

			const player_one_name = get_name(player_one_index, log);
			const player_two_name = get_name(player_two_index, log);
			const guild_name = get_name(guild_index, log);
			if (config.include_characters) {
				const remaining_indicies = [0, 1, 2, 3, 4].filter(
					(i) => i !== player_one_index && i !== player_two_index && i !== guild_index
				);
				const remaining_names = remaining_indicies.map((i) => get_name(i, log));
				characters = ` (${remaining_names.join(',')})`;
			}

			if (log.hex[possible_kill_offsets[kill_index]] === '1')
				output += `[${log.time}] ${player_one_name} has killed ${player_two_name} from ${guild_name}${characters}\n`;
			else
				output += `[${log.time}] ${player_one_name} died to ${player_two_name} from ${guild_name}${characters}\n`;
		}

		return output;
	}

	// ── Kill-location coords for the CoGM heatmap ──────────────────────────
	// BDO writes each kill's world position into the packet tail: little-endian
	// float32 at byte 350 (X, east/west) and byte 358 (Z, north/south). Byte 354
	// is height (Y), which the heatmap doesn't use. Captures from before 1.15.0
	// truncated the packet ahead of these bytes, so parse defensively: a kill
	// whose hex is too short, whose floats are non-finite, or whose values fall
	// outside the BDO world is simply skipped. The upload still carries every
	// kill — only the heatmap gets sparser. CoGM never requires these.
	const COORD_X_HEX = 700; // byte 350 * 2
	const COORD_Z_HEX = 716; // byte 358 * 2
	// The map transform is px = 17299 + X/100 over a 32768px map, so real coords
	// sit within roughly ±1.8M. Anything past this is a misparse (wrong offset /
	// partial packet); drop it rather than plot a kill in the void.
	const COORD_WORLD_LIMIT = 3_000_000;

	function read_le_float32(hex: string, hex_offset: number): number | null {
		const slice = hex.slice(hex_offset, hex_offset + 8);
		if (slice.length < 8) return null;
		const bytes = new Uint8Array(4);
		for (let i = 0; i < 4; i++) {
			const byte = parseInt(slice.slice(i * 2, i * 2 + 2), 16);
			if (Number.isNaN(byte)) return null;
			bytes[i] = byte;
		}
		const value = new DataView(bytes.buffer).getFloat32(0, true);
		return Number.isFinite(value) ? value : null;
	}

	function parse_kill_coord(hex: string): { x: number; z: number } | null {
		if (!hex || hex.length < COORD_Z_HEX + 8) return null;
		const x = read_le_float32(hex, COORD_X_HEX);
		const z = read_le_float32(hex, COORD_Z_HEX);
		if (x === null || z === null) return null;
		if (Math.abs(x) > COORD_WORLD_LIMIT || Math.abs(z) > COORD_WORLD_LIMIT) return null;
		return { x, z };
	}

	// Per-kill coords keyed by (time, killer-family, victim-family) so CoGM binds
	// each coord to its kill by identity, never by position. Killer/victim follow
	// the same direction flip as the .log line ("died to" swaps the two names), so
	// the keys line up with what CoGM's log parser extracts. Returns [] when no
	// kill carried a parseable coord (older capture / non-node-war format), in
	// which case the modal sends no coords at all.
	function get_coords() {
		const out: { t: string; k: string; v: string; x: number; z: number }[] = [];
		for (const log of logs) {
			// Per-kill guard: coords are an optional overlay, so a single malformed
			// packet must skip only its own coord, never throw out of get_coords and
			// block the .log upload that carries the kill itself.
			try {
				const coord = parse_kill_coord(log.hex);
				if (!coord) continue;
				const is_kill = log.hex[possible_kill_offsets[kill_index]] === '1';
				const player_one_name = get_name(player_one_index, log);
				const player_two_name = get_name(player_two_index, log);
				// An empty name means the column wasn't resolved; the matching .log
				// line is unparseable too, so the coord could never bind — skip it.
				if (!player_one_name || !player_two_name) continue;
				out.push({
					t: log.time,
					k: is_kill ? player_one_name : player_two_name,
					v: is_kill ? player_two_name : player_one_name,
					x: coord.x,
					z: coord.z
				});
			} catch {
				continue;
			}
		}
		return out;
	}

	async function save_logs() {
		const path = await open_save_location(get_formatted_date(get_date()) + '.log');
		if (!path) return; // user cancelled the dialog
		await filesystem.writeFile(path, get_logs_string());
		// Drop the raw session (with hex) and the full pcap next to the .log
		// automatically, so the bytes for recalibration always travel with the war.
		await save_companions(path);
		// The session is now preserved to a real file; drop the recovery copy.
		await clear_session();
	}

	// Copy the raw session and the pcap into the same folder the user just saved
	// the .log to, named after it. Best effort: a copy failure never blocks the
	// save. The pcap is copied via the shell so a large file isn't loaded into
	// memory; the raw session is small text.
	async function save_companions(log_path: string) {
		const slash = Math.max(log_path.lastIndexOf('/'), log_path.lastIndexOf('\\'));
		const folder = slash >= 0 ? log_path.slice(0, slash) : '.';
		const sep = log_path.includes('\\') ? '\\' : '/';
		const base = log_path.slice(slash + 1).replace(/\.log$/i, '');
		if (session_path) {
			try {
				const raw = await filesystem.readFile(session_path);
				if (raw) await filesystem.writeFile(`${folder}${sep}${base}.raw.log`, raw);
			} catch {
				/* best effort */
			}
		}
		if (pcap_path) {
			try {
				const dst = `${folder}${sep}${base}.pcap`;
				const cmd =
					NL_OS === 'Windows' ? `copy /Y "${pcap_path}" "${dst}"` : `cp "${pcap_path}" "${dst}"`;
				await os.execCommand(cmd);
			} catch {
				/* best effort */
			}
		}
	}

	async function write_live_output() {
		if (!live_output_path || logs.length === 0) return;
		await filesystem.writeFile(live_output_path, get_logs_string());
	}

	async function upload() {
		const website = dev ? 'http://localhost:5174' : 'https://ikusa.site';
		const result = await fetch(website + '/api/create', {
			method: 'POST',
			body: get_logs_string(),
			headers: {
				'Content-Type': 'text/plain'
			}
		});

		if (result.status === 200) {
			const id = (await result.json()).id;
			os.open(`${website}/wars?id=${id}`);
			// The war is preserved on ikusa.site now, so drop the local recovery
			// copy the same way Save and Upload to CoGM do. Only on success: a
			// failed upload should keep the recovery file so the war isn't lost.
			await clear_session();
		} else {
			console.error(result);
		}
	}

	$: disabled = logs.length === 0 || loading;

	$: own_guild_member_count = logs.reduce((players, log) => {
		const name = log.names[player_one_index].name;
		if (!players.includes(name)) {
			players.push(name);
		}
		return players;
	}, [] as string[]).length;

	$: enemy_count = logs.reduce((players, log) => {
		const name = log.names[player_two_index].name;
		if (!players.includes(name)) {
			players.push(name);
		}
		return players;
	}, [] as string[]).length;

	$: alliance_overview = logs.reduce(
		(acc, log) => {
			const is_kill = log.hex[possible_kill_offsets[kill_index]] === '1';
			if (is_kill) {
				acc.own.kills += 1;
				acc.enemy.deaths += 1;
			} else {
				acc.own.deaths += 1;
				acc.enemy.kills += 1;
			}
			return acc;
		},
		{
			own: { kills: 0, deaths: 0 },
			enemy: { kills: 0, deaths: 0 }
		}
	);

	function parse_log_stats(log: LogType) {
		if (
			possible_kill_offsets.length === 0 ||
			possible_kill_offsets[kill_index] === undefined ||
			!possible_name_offsets[player_one_index] ||
			!possible_name_offsets[player_two_index]
		) {
			return { killer: '', victim: '' };
		}
		const is_kill = log.hex[possible_kill_offsets[kill_index]] === '1';
		const killer = is_kill ? get_name(player_one_index, log) : get_name(player_two_index, log);
		const victim = is_kill ? get_name(player_two_index, log) : get_name(player_one_index, log);
		return { killer, victim };
	}

	$: personal_stats = (() => {
		const name = personal_family_name.trim();
		if (!name) return { kills: 0, deaths: 0 };
		return logs.reduce(
			(acc, log) => {
				const { killer, victim } = parse_log_stats(log);
				if (killer === name) acc.kills += 1;
				if (victim === name) acc.deaths += 1;
				return acc;
			},
			{ kills: 0, deaths: 0 }
		);
	})();

	$: ownKillPct = (() => {
		const t = alliance_overview.own.kills + alliance_overview.own.deaths;
		return t > 0 ? (alliance_overview.own.kills / t) * 100 : 0;
	})();
	$: enemyKillPct = (() => {
		const t = alliance_overview.enemy.kills + alliance_overview.enemy.deaths;
		return t > 0 ? (alliance_overview.enemy.kills / t) * 100 : 0;
	})();

	$: personal_kill_share = (() => {
		const total = alliance_overview.own.kills;
		if (total <= 0) return 0;
		return (personal_stats.kills / total) * 100;
	})();

	$: last_log = logs.length > 0 ? logs[logs.length - 1] : null;
	$: last_event = (() => {
		if (!last_log) return null;
		const is_kill = last_log.hex[possible_kill_offsets[kill_index]] === '1';
		const p1 = get_name(player_one_index, last_log);
		const p2 = get_name(player_two_index, last_log);
		const guild = get_name(guild_index, last_log);
		return {
			time: last_log.time,
			actor: is_kill ? p1 : p2,
			target: is_kill ? p2 : p1,
			guild,
			is_kill
		};
	})();

	function update_personal_family_name(value: string) {
		personal_family_name = value;
		storage.setData(personal_stats_storage_key, personal_family_name).catch(() => null);
	}

	function handle_personal_family_name_input(e: Event) {
		update_personal_family_name((e.currentTarget as HTMLInputElement).value);
	}

	async function open_cogm_upload() {
		const cfg = await get_config();
		if (!cfg.cogm_token) {
			show_toast('Set your CoGM token in Settings first', 'error');
			return;
		}
		ModalManager.open(CogmUploadModal, {
			logs_string: get_logs_string(),
			coords: get_coords(),
			on_uploaded: clear_session
		});
	}
</script>

<div class="flex flex-col gap-2 items-center w-full flex-1 min-h-0 relative">
	<!-- Name order: set which captured column is the Killer, Victim, and Guild
	     once. Manual dropdowns are the source of truth; Auto-detect just fills
	     them in. The same update_names/get_name logic the rows always used. -->
	{#if logs.length > 0 && config}
		<div class="w-full rounded-lg border border-gray-700 bg-background-secondary p-3 flex flex-col gap-3">
			<div class="flex items-center justify-between">
				<span class="heading-h2">Name order</span>
				<button
					class="flex items-center gap-2 text-xs font-semibold text-gold border border-gold/40 bg-gold/10 rounded-md px-3 py-1.5 hover:bg-gold/20 transition-colors"
					on:click={auto_detect}
				>
					Auto-detect{personal_family_name ? ` from "${personal_family_name}"` : ''}
				</button>
			</div>
			<p class="text-caption leading-relaxed">
				The logger captured {logs[0].names.length} names on each kill but not which is the killer, victim,
				or guild. Set it once, or let Auto-detect guess{personal_family_name
					? ` from "${personal_family_name}"`
					: ''}. You can change it any time.
			</p>
			<div class="flex flex-col gap-1.5">
				<span class="text-caption uppercase tracking-wide text-foreground-secondary"
					>Captured names (sample kill)</span
				>
				<div class="flex flex-wrap gap-2">
					{#each captured_names as name, i}
						<span
							class="inline-flex items-center gap-1.5 rounded-md border border-gray-700 bg-background px-2.5 py-1 text-sm tabular-nums"
						>
							<span class="text-gray-500 text-xs">{i + 1}</span>
							<span class="text-foreground">{name}</span>
						</span>
					{/each}
				</div>
			</div>
			<div class="grid grid-cols-3 gap-3">
				<div class="flex flex-col gap-1">
					<span class="text-caption text-status-ok">Killer</span>
					<Select
						options={get_name_options(player_one_index, logs[0])}
						selected_value={player_one_index}
						on_change={(e) => update_names('player_one', e)}
					/>
				</div>
				<div class="flex flex-col gap-1">
					<span class="text-caption text-status-error">Victim</span>
					<Select
						options={get_name_options(player_two_index, logs[0])}
						selected_value={player_two_index}
						on_change={(e) => update_names('player_two', e)}
					/>
				</div>
				<div class="flex flex-col gap-1">
					<span class="text-caption text-gold">Guild</span>
					<Select
						options={get_name_options(guild_index, logs[0])}
						selected_value={guild_index}
						on_change={(e) => update_names('guild', e)}
					/>
				</div>
			</div>
			<p class="text-caption text-foreground-secondary">
				Not sure? Auto-detect makes a best guess from the most repeated names. Tweak any dropdown if
				the preview below reads backwards.
			</p>
			<div class="rounded-md border border-gray-700 bg-background p-2">
				<span class="text-caption">Live preview</span>
				<p class="text-sm tabular-nums mt-1">
					<span class="text-gray-400">[{logs[0].time}]</span>
					<span class="text-status-ok">{get_name(player_one_index, logs[0])}</span>
					<span class="text-gray-400"
						>{logs[0].hex[possible_kill_offsets[kill_index]] === '1'
							? 'has killed'
							: 'died to'}</span
					>
					<span class="text-status-error">{get_name(player_two_index, logs[0])}</span>
					<span class="text-gray-400">from</span>
					<span class="text-gold">{get_name(guild_index, logs[0])}</span>
				</p>
			</div>
		</div>
	{/if}
	<div class="flex gap-1 items-center justify-start w-full px-1">
		<p class="text-xs sm:text-sm text-gray-300">{logs.length} logs</p>
		<div class="ml-2">
			<Checkbox bind:checked={auto_scroll} />
			<span>Auto scroll</span>
		</div>
		<button
			class="ml-auto"
			on:click={() =>
				ModalManager.open(ConfigModal, {
					config: config,
					options: {
						possible_kill_offsets,
						possible_name_offsets,
						name_indicies,
						player_one_index,
						player_two_index,
						guild_index,
						kill_index
					},
					onChange: async (options) => {
						possible_kill_offsets = options.possible_kill_offsets;
						possible_name_offsets = options.possible_name_offsets;
						name_indicies = options.name_indicies;
						player_one_index = options.player_one_index;
						player_two_index = options.player_two_index;
						guild_index = options.guild_index;
						kill_index = options.kill_index;
						config.include_characters = options.include_characters;
						await update_config_wrapper();
					}
				})}
		>
			<Icon icon={IoMdSettings} />
		</button>
	</div>
	<div class="w-full flex gap-2 pb-20 flex-1 min-h-0" style="min-height: {height}px;">
		<div class="w-[550px] flex-shrink-0 rounded-lg border border-gray-700 overflow-hidden p-2 relative h-full">
		{#if loading && logs.length === 0}
			<div class="absolute inset-0 flex justify-center items-center mb-14">
				<LoadingIndicator />
			</div>
		{:else if logs.length === 0 && !loading}
			<div class="absolute inset-0 flex items-center justify-center">
				<p class="text-gray-400">Waiting for logs...</p>
			</div>
		{/if}
		{#key logs.length === 0}
			<VirtualList items={logs} let:item={log}>
				<!-- Rows display the resolved line; the order is set once in the
				     panel above. Same get_name logic the dropdowns used. -->
				<div class="flex gap-2 group py-1 items-center px-1 text-sm tabular-nums">
					<span class="text-gray-400">{log.time}</span>
					<span class="text-status-ok font-medium">{get_name(player_one_index, log)}</span>
					<span class="w-14 text-center">
						{#if log.hex[possible_kill_offsets[kill_index]] === '1'}
							<span class="text-status-ok">killed</span>
						{:else}
							<span class="text-status-error">died to</span>
						{/if}
					</span>
					<span class="text-status-error font-medium">{get_name(player_two_index, log)}</span>
					<span class="text-gray-400">from</span>
					<span class="text-gold font-medium">{get_name(guild_index, log)}</span>
				</div>
			</VirtualList>
		{/key}
		</div>
		<!-- Stats panel fills the remaining right space -->
		<div class="flex-1 flex flex-col text-xs h-full overflow-y-auto rounded-md border border-gray-700 bg-background-secondary p-3">
			<!-- YOUR K/D -->
			<div class="flex items-center justify-between">
				<span class="heading-h2">Your K/D</span>
				<button
					class="text-caption hover:text-foreground transition-colors"
					on:click={() => {
						ModalManager.open(GuildInfos, {
							logs: logs.map((l) => ({
								names: l.names.map((n) => n.name),
								kill: l.hex[possible_kill_offsets[kill_index]] === '1'
							})),
							guild_index,
							player_one_index,
							player_two_index
						});
					}}>Details</button
				>
			</div>
			{#if !personal_family_name.trim()}
				<p class="text-caption mt-2">Set your family name in Settings to see personal stats</p>
			{:else}
				<div class="flex items-baseline justify-between mt-2">
					<span class="tabular-nums">
						<span class="text-submarine-400">{personal_stats.kills}</span>
						<span class="text-foreground-secondary mx-1">/</span>
						<span class="text-cerise-400">{personal_stats.deaths}</span>
					</span>
					<span class="heading-display text-gold tabular-nums">
						{calculate_kd(personal_stats.kills, personal_stats.deaths)}
					</span>
				</div>
				<div class="mt-2 h-1.5 rounded-full bg-gold/20 overflow-hidden">
					<div class="h-full bg-gold transition-all" style="width: {personal_kill_share}%"></div>
				</div>
				<p class="text-caption mt-1 tabular-nums">{personal_kill_share.toFixed(0)}% of alliance kills</p>
			{/if}

			<!-- ALLIANCE -->
			<div class="divider-top">
				<span class="heading-h2">Alliance</span>
				{#if logs.length === 0}
					<p class="text-caption mt-2">Waiting for combat events...</p>
				{:else}
					<p class="mt-2 tabular-nums">
						<span class="text-submarine-400">K {alliance_overview.own.kills}</span>
						<span class="text-foreground-secondary mx-2">·</span>
						<span class="text-cerise-400">D {alliance_overview.own.deaths}</span>
						<span class="text-foreground-secondary mx-2">·</span>
						<span class="text-gold font-semibold">K/D {calculate_kd(alliance_overview.own.kills, alliance_overview.own.deaths)}</span>
					</p>
					<p class="text-caption mt-1 tabular-nums">{own_guild_member_count} vs {enemy_count} players</p>
				{/if}
			</div>

			<!-- ENEMY -->
			<div class="divider-top">
				<span class="heading-h2">Enemy</span>
				{#if logs.length === 0}
					<p class="text-caption mt-2">Waiting for combat events...</p>
				{:else}
					<p class="mt-2 tabular-nums">
						<span class="text-cerise-400">K {alliance_overview.enemy.kills}</span>
						<span class="text-foreground-secondary mx-2">·</span>
						<span class="text-submarine-400">D {alliance_overview.enemy.deaths}</span>
						<span class="text-foreground-secondary mx-2">·</span>
						<span class="font-semibold">K/D {calculate_kd(alliance_overview.enemy.kills, alliance_overview.enemy.deaths)}</span>
					</p>
				{/if}
			</div>

			<!-- LAST EVENT -->
			<div class="divider-top">
				<span class="heading-h2">Last Event</span>
				{#if last_event === null}
					<p class="text-caption mt-2">Waiting for combat events...</p>
				{:else}
					<p class="mt-2">
						<span class="text-caption tabular-nums mr-2">{last_event.time}</span>
						<span class="text-foreground">{last_event.actor}</span>
						{#if last_event.is_kill}
							<span class="text-submarine-400 font-semibold mx-1">killed</span>
						{:else}
							<span class="text-cerise-400 font-semibold mx-1">died to</span>
						{/if}
						<span class="text-foreground">{last_event.target}</span>
					</p>
					<p class="text-caption mt-1">from {last_event.guild}</p>
				{/if}
			</div>

			<!-- FAMILY NAME -->
			<div class="divider-top">
				<span class="heading-h2">Family name</span>
				{#if personal_family_name.trim()}
					<p class="mt-2 text-foreground tabular-nums">{personal_family_name}</p>
					<p class="text-caption mt-1">Change in Settings</p>
				{:else}
					<input
						class="w-full mt-2 bg-background border border-gray-700 rounded px-2 py-1 text-foreground"
						placeholder="Family name"
						value={personal_family_name}
						on:input={handle_personal_family_name_input}
					/>
					<p class="text-caption mt-1">Persists across sessions. Also editable in Settings.</p>
				{/if}
			</div>
		</div>
	</div>
	<!-- Real bottom action bar: a full-width opaque, blurred strip with a top
	     border so the feed and stats no longer scroll behind floating buttons.
	     CoGM is the prominent (gold) action; Save secondary; the legacy
	     ikusa.site upload is demoted to a grey secondary on the right. -->
	<div
		class="fixed bottom-0 left-0 right-0 z-20 border-t border-gray-700 bg-background/95 backdrop-blur"
	>
		<div class="max-w-4xl mx-auto flex gap-2 justify-center py-3 px-4">
			<Button class="w-40" on:click={open_cogm_upload} {disabled}>Upload to CoGM</Button>
			<Button class="w-32" on:click={save_logs} color="secondary" {disabled}>Save</Button>
			<Button class="w-36" on:click={upload} color="secondary" {disabled}>Upload (Ikusa)</Button>
		</div>
	</div>
</div>
