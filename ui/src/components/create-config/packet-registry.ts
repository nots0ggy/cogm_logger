// Calibrated decode config per BDO kill-packet opcode.
//
// Each kill packet begins with a 5-byte opcode (e.g. 640100a115). That opcode
// is the selector: it fully determines the column layout and where the
// kill-direction flag lives. BDO changes the opcode (and the layout) on a
// patch, so the same recorder produces different opcodes across patches.
//
// The frequency/structural heuristics in logger.svelte try to re-derive the
// layout per session, but they scramble when the byte offsets shift mid-war and
// they cannot tell a kill from a death without ground truth. So for opcodes we
// have validated against an in-game node-war warscore (the only ground truth
// for kill direction) we decode DETERMINISTICALLY from this table instead of
// guessing. Unknown opcodes fall back to the heuristic auto-detect.
//
//   name_order  which captured column (0-4) is the subject (the recorder's own
//               family — the "killer" / player_one slot), the other side (the
//               enemy — the "victim" / player_two slot), and the guild tag. The
//               two leftover columns are the characters, emitted as
//               "(otherChar,subjectChar)".
//   kill        hex-CHARACTER index of the direction flag's low nibble in the
//               packet hexdump. The flag byte is 0x01 (subject killed) or 0x00
//               (subject died); the low nibble is char `kill`, so
//               hexdump[kill] === '1' means the subject got the kill.
import { storage } from '@neutralinojs/lib';
import { writable } from 'svelte/store';
import type { LogType } from './config';

export type PacketConfig = {
	name_order: { killer: number; victim: number; guild: number };
	kill: number;
};

export const KNOWN_PACKETS: Record<string, PacketConfig> = {
	// Post-2026-06-18 layout. No header prefix: the guild name starts at byte 5,
	// so the direction flag sits mid-body at byte 129 (hex char 259), the byte
	// right before the `06 00 00 00` separator that prefixes the subject's
	// family column. Family fields are col4 (subject) / col3 (enemy).
	// Validated against two node-war warscores (RAT + a second guild).
	'640100a115': { name_order: { killer: 4, victim: 3, guild: 0 }, kill: 259 },
	// Pre-2026-06-18 layout. 10-byte header prefix, so the direction flag is in
	// the header at byte 7 (hex char 15). Family fields are SWAPPED vs the 640
	// layout: col3 (subject) / col4 (enemy). Validated against a RAT warscore.
	'6b01003f0e': { name_order: { killer: 3, victim: 4, guild: 0 }, kill: 15 },
	// 2026-06-25 patch. Guild moved to the middle (col2) and the columns are
	// interleaved char/char/guild/fam/fam: col0 enemy char, col1 subject char,
	// col2 guild, col3 enemy family, col4 subject family. Direction flag at hex
	// char 265 (byte 132 low nibble), '1' => subject killed. Validated against
	// RAT's in-game WIN screen (14/15 families matched, kills near-exact).
	'680100a40d': { name_order: { killer: 4, victim: 3, guild: 2 }, kill: 265 },
	// Edania node-war server, first seen 2026-07-02 (packet type 0x1CD6).
	// col0 enemy char, col1 guild, col2 subject char, col3 subject family,
	// col4 enemy family; flag at hex char 143 (byte 71), the only strict 0/1
	// byte in the packet. Calibrated from a RAT raw session (100% roster
	// anchoring on col3); direction matches the heuristic decode (704/410).
	'640100d61c': { name_order: { killer: 3, victim: 4, guild: 1 }, kill: 143 }
};

// ── Remote registry ─────────────────────────────────────────────────────────
// cogm.app serves the same table at /api/logger/packet-registry so a new-patch
// calibration reaches installed loggers WITHOUT a release.
// Remote entries take precedence over the compiled table; the compiled table
// is the offline fallback, and the last successful fetch is cached in
// Neutralino storage so an offline launch still gets the newest known table.
//
// THE TABLE IS RE-FETCHED, NOT SNAPSHOT AT LAUNCH. It used to be read exactly
// once on 'ready', which quietly made every calibration useless to anyone
// already running the app: on 2026-07-30 opcode 6f0100000f was calibrated
// server-side at 00:34, and three guilds still lost their wars that night
// because their loggers held a snapshot taken before that and told them to
// restart. A war is recorded in one sitting, so "restart and reopen" is asked
// at the exact moment the user cannot do it. Refreshing on demand costs one
// small GET and removes that whole failure class.

const REGISTRY_CACHE_KEY = 'packet_registry_cache';
let remote_packets: Record<string, PacketConfig> | null = null;

/**
 * Bumped on every refresh that CHANGES the table.
 *
 * `remote_packets` is a module-level variable, so Svelte cannot see it move —
 * a component that computed "this opcode is unknown" would keep saying so
 * forever after a refresh made it known. Reactive consumers subscribe to this
 * instead, which is what turns a live calibration into a live unblock.
 */
export const registry_version = writable(0);

function valid_packet_config(v: unknown): v is PacketConfig {
	if (typeof v !== 'object' || v === null) return false;
	const p = v as PacketConfig;
	return (
		typeof p.kill === 'number' &&
		typeof p.name_order === 'object' &&
		p.name_order !== null &&
		[p.name_order.killer, p.name_order.victim, p.name_order.guild].every(
			(n) => Number.isInteger(n) && n >= 0 && n <= 4
		)
	);
}

function sanitize_registry(raw: unknown): Record<string, PacketConfig> | null {
	if (typeof raw !== 'object' || raw === null) return null;
	const out: Record<string, PacketConfig> = {};
	for (const [id, cfg] of Object.entries(raw as Record<string, unknown>)) {
		if (/^[0-9a-f]{10}$/.test(id) && valid_packet_config(cfg)) out[id] = cfg;
	}
	return Object.keys(out).length > 0 ? out : null;
}

/**
 * A stable identity for a table, so a refresh that returns the same entries
 * does not look like a change and spam reactive consumers. Key order from JSON
 * is not guaranteed, hence the sort.
 */
function registry_fingerprint(packets: Record<string, PacketConfig> | null): string {
	if (!packets) return '';
	return Object.keys(packets)
		.sort()
		.map((id) => {
			const { name_order: n, kill } = packets[id];
			return `${id}:${n.killer},${n.victim},${n.guild},${kill}`;
		})
		.join('|');
}

/** GET the table. Returns null on any failure; never throws. */
async function fetch_remote(cogm_url: string): Promise<Record<string, PacketConfig> | null> {
	try {
		const res = await fetch(`${cogm_url.replace(/\/$/, '')}/api/logger/packet-registry`, {
			// Without this the webview can satisfy a refresh from its own cache and
			// hand back the very snapshot we are refreshing to escape.
			cache: 'no-store'
		});
		if (!res.ok) return null;
		const body = await res.json();
		return sanitize_registry(body?.packets);
	} catch {
		return null;
	}
}

/** Swap the table in. Returns whether anything actually changed. */
function apply_registry(packets: Record<string, PacketConfig> | null): boolean {
	if (registry_fingerprint(packets) === registry_fingerprint(remote_packets)) return false;
	remote_packets = packets;
	registry_version.update((n) => n + 1);
	return true;
}

/**
 * Fetch the calibrated registry from cogm.app and cache it. Never throws:
 * on any failure it falls back to the cached copy, and failing that the
 * compiled table stays in effect. Call once at app start.
 */
export async function init_remote_registry(cogm_url: string): Promise<void> {
	const fresh = await fetch_remote(cogm_url);
	if (fresh) {
		apply_registry(fresh);
		await storage.setData(REGISTRY_CACHE_KEY, JSON.stringify(fresh)).catch(() => {});
		return;
	}
	try {
		const cached = await storage.getData(REGISTRY_CACHE_KEY);
		const packets = sanitize_registry(JSON.parse(cached));
		if (packets) apply_registry(packets);
	} catch {
		/* no cache — compiled table stays in effect */
	}
}

/**
 * Re-fetch the table mid-session. Returns true when the table CHANGED, which
 * is the caller's signal that an opcode blocked a moment ago may now decode.
 *
 * Network-only on purpose: falling back to the on-disk cache here would return
 * the same stale table we are trying to get past, and report "no change" when
 * the truth is "could not reach the server".
 */
export async function refresh_remote_registry(cogm_url: string): Promise<boolean> {
	const fresh = await fetch_remote(cogm_url);
	if (!fresh) return false;
	const changed = apply_registry(fresh);
	if (changed) await storage.setData(REGISTRY_CACHE_KEY, JSON.stringify(fresh)).catch(() => {});
	return changed;
}

/** The decode config for an opcode, or null when it isn't calibrated yet. */
export function lookup_packet(identifier: string): PacketConfig | null {
	return remote_packets?.[identifier] ?? KNOWN_PACKETS[identifier] ?? null;
}

/**
 * Reactive-safe "we cannot decode this opcode" test.
 *
 * Takes the registry version purely so a Svelte `$:` statement has a dependency
 * it can actually see: the table lives in module scope, which Svelte cannot
 * observe, so without this a war judged uncalibrated stays judged that way even
 * after a refresh made it decodable.
 */
export function is_uncalibrated(identifier: string | null, registry_version: number): boolean {
	// Referenced so the argument is not "unused" to the type checker; the value
	// itself carries no meaning beyond "the table may have moved".
	void registry_version;
	return identifier != null && !lookup_packet(identifier);
}

/** Every calibrated opcode (remote merged over compiled). */
function known_identifiers(): string[] {
	return [...new Set([...Object.keys(KNOWN_PACKETS), ...Object.keys(remote_packets ?? {})])];
}

// ── Mis-framed packet recovery ───────────────────────────────────────────────
// The capture engine anchors packets with a loose regex; leftover tail bytes
// of the previous packet can make it anchor a few bytes early, producing a
// line whose "opcode" is stray bytes with the real packet right behind it
// (e.g. 6001000068 0100a40d…). The raw line is preserved as captured; this
// recovers it at decode time by re-slicing the hex at the embedded known
// opcode and shifting the name offsets to match.

const MAX_REFRAME_SHIFT = 20; // hex chars (10 bytes) of stray prefix at most

/**
 * If the log's opcode is unknown but a calibrated opcode starts within the
 * first few bytes of its hex, return a re-framed copy decoded as that packet.
 * Returns the log unchanged when it's already known or can't be re-framed.
 */
export function reframe_log(log: LogType): LogType {
	if (!log.hex || lookup_packet(log.identifier)) return log;
	for (const id of known_identifiers()) {
		const idx = log.hex.indexOf(id);
		// Even index = byte-aligned; 0 would mean the frame was already right.
		if (idx <= 0 || idx > MAX_REFRAME_SHIFT || idx % 2 !== 0) continue;
		if (log.names.some((n) => n.offset < idx)) continue;
		return {
			...log,
			identifier: id,
			hex: log.hex.slice(idx),
			names: log.names.map((n) => ({ name: n.name, offset: n.offset - idx }))
		};
	}
	return log;
}

/**
 * Session dedup key for a raw record. Includes a hex tail sample so two real
 * kills in the same second between the same five names (direction/coords
 * differ in the payload) are not collapsed into one.
 */
export function log_dedup_key(log: LogType): string {
	return `${log.identifier}|${log.time}|${log.names.map((n) => n.name).join(',')}|${
		log.hex ? log.hex.slice(-24) : ''
	}`;
}

/** The most common opcode across a set of captured logs (the war's primary). */
export function dominant_identifier(identifiers: string[]): string | null {
	const counts = new Map<string, number>();
	for (const id of identifiers) counts.set(id, (counts.get(id) ?? 0) + 1);
	let best: string | null = null;
	let bestN = 0;
	for (const [id, n] of counts) {
		if (n > bestN) {
			best = id;
			bestN = n;
		}
	}
	return best;
}
