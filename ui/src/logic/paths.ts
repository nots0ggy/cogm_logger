import { filesystem, os } from '@neutralinojs/lib';

// All capture artifacts (the always-on .pcap and the raw recovery session) live
// in a per-user writable folder: Documents/CoGM Logger. The old paths (captures/
// and logger/.tmp/, relative to the install dir) sat under Program Files, which
// non-elevated users can't write: the engine crashed on the unguarded
// folder-create and nothing saved. Documents is always writable and easy for
// users to find the files they share for calibration.

let _dir: string | null = null;

export function path_sep(): string {
	return NL_OS === 'Windows' ? '\\' : '/';
}

/** The Documents/CoGM Logger folder, created if missing. Resolved once and cached. */
export async function get_capture_dir(): Promise<string> {
	if (_dir) return _dir;
	const sep = path_sep();
	let base = '';
	try {
		base = await os.getPath('documents');
	} catch {
		try {
			const home = await os.getEnv(NL_OS === 'Windows' ? 'USERPROFILE' : 'HOME');
			if (home) base = `${home}${sep}Documents`;
		} catch {
			/* fall through to temp */
		}
	}
	// Last resort: a writable temp dir, so captures never fall back to a
	// non-writable install dir again.
	if (!base) {
		try {
			base = await os.getPath('temp');
		} catch {
			base = '.';
		}
	}
	const dir = `${base}${sep}CoGM Logger`;
	try {
		await filesystem.createDirectory(dir);
	} catch {
		/* already exists */
	}
	_dir = dir;
	return dir;
}

/** Absolute path to a file inside the capture folder. */
export async function capture_path(filename: string): Promise<string> {
	return `${await get_capture_dir()}${path_sep()}${filename}`;
}
