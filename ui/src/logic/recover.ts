import { filesystem } from '@neutralinojs/lib';
import type { LogType } from '../components/create-config/config';

// The live capture engine writes each parsed record here as it happens
// (logger/src/options/live_capture.py), so a crash leaves the session on
// disk. The record page spawns the engine with no -o, so the default output
// path is logger/.tmp/<date>.log.
const SESSION_DIR = 'logger/.tmp';

export type RecoverableSession = {
	path: string;
	logs: LogType[];
};

/** Parse one raw engine line (identifier,time,n1 off,..,n5 off,hex) to a LogType. */
function parse_line(line: string): LogType | null {
	const d = line.split(',');
	if (d.length !== 8) return null;
	return {
		identifier: d[0],
		time: d[1],
		names: d.slice(2, 7).map((name) => {
			const split = name.split(' ');
			return { name: split[0], offset: +split[1] };
		}),
		hex: d[7]
	};
}

/**
 * Find the most recent recoverable session, if any. Returns null when there's
 * no session dir, no files, or the newest file has no parseable records (a
 * clean session that was already saved leaves nothing useful to recover).
 */
export async function find_last_session(): Promise<RecoverableSession | null> {
	let entries: { entry: string; type: string }[];
	try {
		entries = await filesystem.readDirectory(SESSION_DIR);
	} catch {
		return null; // dir doesn't exist yet
	}

	const files = entries.filter((e) => e.type === 'FILE' && e.entry.endsWith('.log'));
	if (files.length === 0) return null;

	// Pick the newest by modified time.
	let newest: { path: string; mtime: number } | null = null;
	for (const f of files) {
		const path = `${SESSION_DIR}/${f.entry}`;
		try {
			const stats = await filesystem.getStats(path);
			const mtime = stats.modifiedAt ?? 0;
			if (!newest || mtime > newest.mtime) newest = { path, mtime };
		} catch {
			/* skip unreadable */
		}
	}
	if (!newest) return null;

	let content: string;
	try {
		content = await filesystem.readFile(newest.path);
	} catch {
		return null;
	}

	const logs: LogType[] = [];
	const seen = new Set<string>();
	for (const line of content.split('\n')) {
		const trimmed = line.trim();
		if (!trimmed) continue;
		const log = parse_line(trimmed);
		if (!log) continue;
		// Dedup identical records the same way the live view does.
		const key = `${log.identifier}|${log.time}|${log.names.map((n) => n.name).join(',')}`;
		if (seen.has(key)) continue;
		seen.add(key);
		logs.push(log);
	}

	if (logs.length === 0) return null;
	return { path: newest.path, logs };
}
