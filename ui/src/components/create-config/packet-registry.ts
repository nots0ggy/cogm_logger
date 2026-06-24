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
	'6b01003f0e': { name_order: { killer: 3, victim: 4, guild: 0 }, kill: 15 }
};

/** The decode config for an opcode, or null when it isn't calibrated yet. */
export function lookup_packet(identifier: string): PacketConfig | null {
	return KNOWN_PACKETS[identifier] ?? null;
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
