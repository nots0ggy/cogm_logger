import { dev } from '$app/environment';
import { app, events, os } from '@neutralinojs/lib';

function handle_process(evt: CustomEvent) {
	if (logger && logger.id == evt.detail.id) {
		switch (evt.detail.action) {
			case 'stdOut':
				// Neutralino batches stdout, so one event can carry several
				// newline-joined log lines during a kill burst. Each consumer
				// parses a single comma record per call and drops anything
				// that isn't exactly 8 fields, so split here and deliver one
				// line at a time or whole bursts get silently lost.
				for (const line of evt.detail.data.split('\n')) {
					const trimmed = line.trim();
					if (!trimmed) continue;
					console.log(trimmed);
					callback?.(trimmed, 'running');
				}
				break;
			case 'stdErr':
				alert(
					'Something went wrong. Please report this in the CoGM support server with the following error message:\n\n' +
						evt.detail.data
				);
				console.error(evt.detail.data);
				callback?.(evt.detail.data.trim(), 'error');
				break;
			case 'exit':
				console.log(`Logger process terminated with exit code: ${evt.detail.data}`);
				logger = null;
				callback?.(evt.detail.data, 'terminated');
				events.off('spawnedProcess', handle_process);
				break;
		}
	} else {
		console.log('Invalid logger', logger, evt.detail.id);
		alert('Something went wrong. Invalid Logger');
	}
}

const arg_mapping = {
	sniff: '',
	open_file: '-f',
	status: '-s',
	update: '-u',
	record: '-r',
	analyze: '-a'
} as const;

let logger: os.SpawnedProcess | null = null;

export type LoggerCallback = (data: string, status: 'running' | 'terminated' | 'error') => void;
let callback: LoggerCallback | null = null;

// Set by stop_logger, cleared at the start of start_logger. A reconnect
// dispatches start_logger without awaiting it, and start_logger then awaits
// ~1s on its kill before spawning; if stop_logger ran during that window
// we must abort the spawn so a torn-down page doesn't leak a background
// capture.
let stopped = false;

/**
 * Force-kill any lingering sniffer process. os.updateSpawnedProcess(id, 'exit')
 * is the primary, cross-platform stop; this is the belt-and-suspenders for a
 * child that detached from the parent handle. Windows kills by exe name with
 * taskkill; Linux uses pkill on the sniffer's path (`logger/logger`) so a
 * leftover scapy capture can't keep holding the network interface. Bounded by a
 * 1s race so a missing command or no-match never stalls the caller. Previously
 * the taskkill ran unconditionally and silently no-op'd on Linux, which is why
 * captures lingered there.
 */
export async function kill_logger_process(): Promise<void> {
	const cmd =
		NL_OS === 'Windows' ? 'taskkill /F /IM logger.exe' : 'pkill -f logger/logger';
	try {
		const timeout = new Promise((resolve) => setTimeout(() => resolve('timeout'), 1000));
		await Promise.race([os.execCommand(cmd), timeout]);
	} catch (e) {
		console.error(e);
	}
}

export async function start_logger(
	clb: LoggerCallback,
	arg: keyof typeof arg_mapping,
	data?: string
) {
	stopped = false;
	if (logger) {
		try {
			await os.updateSpawnedProcess(logger.id, 'exit');
		} catch (e) {
			console.error(e);
		}
	}
	console.log('Killing previous instances');
	await kill_logger_process();

	const extra_args = data ? ' ' + data : '';

	let logger_command = 'logger\\logger ';

	switch (NL_OS) {
		case 'Windows': {
			if (dev) {
				logger_command = 'logger\\dist\\logger\\logger ';
			} else {
				logger_command = 'logger\\logger ';
			}
			break;
		}
		case 'Linux': {
			if (dev) {
				logger_command = './logger/dist/logger/logger ';
			} else {
				logger_command = './logger/logger ';
			}
			break;
		}
	}

	// stop_logger may have fired during the taskkill await above (a reconnect
	// races with navigating away). Abort before spawning so we don't leak a
	// detached capture and re-attach a stale callback.
	if (stopped) {
		return;
	}

	console.log('Starting logger with command: ' + logger_command + arg_mapping[arg] + extra_args);

	logger = await os.spawnProcess(logger_command + arg_mapping[arg] + extra_args);
	callback = clb;
	events.on('spawnedProcess', handle_process);
}

/**
 * Stop a running capture and detach. Without this, leaving the record page
 * left the Python sniffer running in the background, and its later process
 * events fired the stale-id "Invalid Logger" alert. Safe to call when
 * nothing is running. Detaches the callback first so the exit event this
 * triggers doesn't reach a torn-down page.
 */
export async function stop_logger() {
	stopped = true;
	callback = null;
	events.off('spawnedProcess', handle_process);
	const current = logger;
	logger = null;
	if (current) {
		try {
			await os.updateSpawnedProcess(current.id, 'exit');
		} catch (e) {
			console.error(e);
		}
	}
	// Belt-and-suspenders: os.updateSpawnedProcess can miss a child that
	// detached from the parent handle. Cross-platform now (taskkill on Windows,
	// pkill on Linux) so a leftover capture is actually killed on either OS.
	await kill_logger_process();
}

/**
 * Restart the Npcap capture driver. After an unclean PC shutdown the driver
 * service commonly wedges: the files are still installed so the app thinks
 * Npcap is present, but the adapter won't open and capture fails. Restarting
 * the service clears that without a full reboot. Requires elevation (the
 * UAC prompt is expected). The service is "npcap", or "npf" when installed
 * in WinPcap-compatible mode, so we cycle both. Fail-safe: if the user
 * denies UAC or the command errors, the app keeps running unchanged.
 */
export async function restart_capture_driver(): Promise<boolean> {
	if (NL_OS !== 'Windows') return false;
	try {
		const cmd =
			'net stop npcap & net start npcap & net stop npf & net start npf';
		const ps = `Start-Process cmd -Verb RunAs -ArgumentList '/c ${cmd}'`;
		await os.execCommand(`powershell -NoProfile -Command "${ps}"`);
		// The elevated cmd runs detached; give the service a moment to settle.
		await new Promise((r) => setTimeout(r, 2500));
		return true;
	} catch (e) {
		console.error('restart_capture_driver failed', e);
		return false;
	}
}

/**
 * Relaunch the app elevated. For the access-denied capture failure (Npcap
 * installed admin-only, app running non-elevated). Starts an elevated copy
 * via UAC and exits this one. Fail-safe: if UAC is denied or the spawn
 * errors, this instance keeps running.
 */
export async function relaunch_as_admin(): Promise<void> {
	if (NL_OS !== 'Windows') return;
	try {
		const exe = 'cogm-logger-win_x64.exe';
		// -PassThru + a non-zero exit on failure so we only close THIS instance
		// when the elevated copy actually launched. execCommand resolves even
		// when the inner Start-Process throws or the user cancels UAC, so
		// exiting unconditionally would close the app on a routine UAC decline.
		const r = await os.execCommand(
			`powershell -NoProfile -Command "try { Start-Process '${exe}' -Verb RunAs -ErrorAction Stop; exit 0 } catch { exit 1 }"`
		);
		if (r.exitCode === 0) {
			await app.exit();
		} else {
			alert('Could not relaunch as administrator. The app will keep running.');
		}
	} catch (e) {
		console.error('relaunch_as_admin failed', e);
	}
}
