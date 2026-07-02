import { filesystem } from '@neutralinojs/lib';
import type { LogType } from '../components/create-config/config';
import { log_dedup_key, reframe_log } from '../components/create-config/packet-registry';
import { get_capture_dir } from './paths';

// The live capture engine writes each parsed record to a session-<ts>.log in
// the capture folder (Documents/CoGM Logger) as it happens, so a crash leaves
// the session on disk to recover.

export type RecoverableSession = {
	path: string;
	logs: LogType[];
};

/** Parse one raw engine line (identifier,time,n1 off,..,n5 off,hex) to a LogType. */
function parse_line(line: string): LogType | null {
	const d = line.split(',');
	if (d.length !== 8) return null;
	// reframe_log recovers records the capture engine anchored a few bytes
	// early (unknown opcode with the real packet embedded right behind it).
	return reframe_log({
		identifier: d[0],
		time: d[1],
		names: d.slice(2, 7).map((name) => {
			const split = name.split(' ');
			return { name: split[0], offset: +split[1] };
		}),
		hex: d[7]
	});
}

/**
 * Find the most recent recoverable session, if any. Returns null when there's
 * no session dir, no files, or the newest file has no parseable records (a
 * clean session that was already saved leaves nothing useful to recover).
 */
export async function find_last_session(): Promise<RecoverableSession | null> {
	// Scan the current capture folder (Documents/CoGM Logger) plus the legacy
	// install-dir location, so a session left by a pre-1.14 crash (when sessions
	// lived in logger/.tmp) is still offered for recovery after updating. The
	// legacy scan can be dropped in a later release.
	const dirs = [await get_capture_dir(), 'logger/.tmp'];

	// Pick the newest session-*.log across all scanned dirs by modified time.
	// session- prefix excludes the raw-*/.pcap companions that share the folder.
	let newest: { path: string; mtime: number } | null = null;
	for (const dir of dirs) {
		let entries: { entry: string; type: string }[];
		try {
			entries = await filesystem.readDirectory(dir);
		} catch {
			continue; // dir doesn't exist
		}
		const dir_sep = dir.includes('\\') ? '\\' : '/';
		const files = entries.filter(
			(e) => e.type === 'FILE' && e.entry.startsWith('session-') && e.entry.endsWith('.log')
		);
		for (const f of files) {
			const path = `${dir}${dir_sep}${f.entry}`;
			try {
				const stats = await filesystem.getStats(path);
				const mtime = stats.modifiedAt ?? 0;
				if (!newest || mtime > newest.mtime) newest = { path, mtime };
			} catch {
				/* skip unreadable */
			}
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
		const key = log_dedup_key(log);
		if (seen.has(key)) continue;
		seen.add(key);
		logs.push(log);
	}

	if (logs.length === 0) return null;
	return { path: newest.path, logs };
}
