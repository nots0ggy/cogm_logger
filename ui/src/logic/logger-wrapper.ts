import { dev } from '$app/environment';
import { events, os } from '@neutralinojs/lib';

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

export async function start_logger(
	clb: LoggerCallback,
	arg: keyof typeof arg_mapping,
	data?: string
) {
	if (logger) {
		try {
			await os.updateSpawnedProcess(logger.id, 'exit');
		} catch (e) {
			console.error(e);
		}
	}
	console.log('Killing previous instances');
	try {
		const kill_timouet = new Promise((resolve, reject) => {
			setTimeout(() => resolve('Kill timeout'), 1000);
		});
		const kill_promise = os.execCommand('taskkill /F /IM logger.exe ');

		await Promise.race([kill_promise, kill_timouet]);
	} catch (e) {
		console.error(e);
	}

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
	// Belt-and-suspenders on Windows: the spawned-process exit can miss a
	// child that detached from the parent handle. No-op (and harmless error)
	// on other platforms.
	if (NL_OS === 'Windows') {
		try {
			const kill_timeout = new Promise((resolve) => setTimeout(() => resolve('timeout'), 1000));
			await Promise.race([os.execCommand('taskkill /F /IM logger.exe'), kill_timeout]);
		} catch (e) {
			console.error(e);
		}
	}
}
