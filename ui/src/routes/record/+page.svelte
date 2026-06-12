<script lang="ts">
	import {
		type LoggerCallback,
		start_logger,
		stop_logger,
		restart_capture_driver,
		relaunch_as_admin
	} from '../../logic/logger-wrapper';
	import { os } from '@neutralinojs/lib';
	import { onDestroy, onMount } from 'svelte';
	import Logger from '../../components/create-config/logger.svelte';
	import { get_config, update_config, type Config, type LogType } from '../../components/create-config/config';
	import { recording_state, recording_started_at, packets_seen } from '../../logic/recording-store';
	import StatusDot from '../../components/status-dot.svelte';
	import Button from '../../svelte-ui/elements/button.svelte';
	import { goto } from '$app/navigation';

	let logs: LogType[] = [];
	let is_destroyed = false;
	let retry_count = 0;
	let config: Config;
	let now = Date.now();
	let ticker: ReturnType<typeof setInterval>;

	// Set when the capture engine reports a classified startup failure
	// (CAPTURE_ERROR). Drives the recovery panel below.
	let capture_error: { code: string; message: string } | null = null;
	let recovering = false;
	// Path of the full-packet .pcap when that capture is enabled, surfaced so
	// the user can find the file and send it in for protocol research.
	let pcap_path = '';
	// Per-session recovery file. A unique name per recording (not the engine's
	// day-default) so same-day sessions never merge in the recovery file, and
	// so the editor can clear exactly this file once the logs are saved.
	let session_path = '';

	function build_flags(cfg: Config): string {
		return (cfg.all_interfaces ? '-i' : '') +
			(cfg.ip_filter ? ' -p' : '') +
			(cfg.record_pcap ? ' -r' : '');
	}

	function spawn_args(): string {
		return `${build_flags(config)} -o "${session_path}"`;
	}

	function format_runtime(started: number | null, current: number): string {
		if (started === null) return '00:00';
		const secs = Math.floor((current - started) / 1000);
		const h = Math.floor(secs / 3600);
		const m = Math.floor((secs % 3600) / 60);
		const s = secs % 60;
		const mm = String(m).padStart(2, '0');
		const ss = String(s).padStart(2, '0');
		if (h > 0) {
			return `${String(h).padStart(2, '0')}:${mm}:${ss}`;
		}
		return `${mm}:${ss}`;
	}

	const logger_callback: LoggerCallback = (data, status) => {
		// Once the page is torn down, ignore every late event. The error and
		// terminated branches already gated on this; the running branch did
		// not, so an orphaned reconnect could flip the shared recording_state
		// back to 'recording' after navigating away.
		if (is_destroyed) return;
		if (status === 'running') {
			// Classified capture failure from the engine (npcap wedged, access
			// denied, etc). Show the recovery panel instead of a dead retry.
			if (data.startsWith('CAPTURE_ERROR|')) {
				const parts = data.split('|');
				capture_error = { code: parts[1] ?? 'UNKNOWN', message: parts.slice(2).join('|') };
				recording_state.set('error');
				return;
			}
			// Full-packet capture path, so the user can find and share the file.
			if (data.startsWith('Saving pcap to ')) {
				pcap_path = data.slice('Saving pcap to '.length).trim();
				return;
			}
			packets_seen.update((n) => n + 1);

			const d = data.split(',');
			if (d.length === 8 && !data.includes('Network Interfaces:')) {
				// A parsed log line means capture is healthy: clear any
				// reconnect debt so transient blips spread across a long war
				// don't accumulate to the 3-strike permanent failure, and
				// restore the live label after a successful reconnect (it
				// otherwise sticks on RECONNECTING forever).
				if (retry_count > 0 || $recording_state !== 'recording') {
					retry_count = 0;
					recording_state.set('recording');
					capture_error = null;
				}
				const new_log = {
					identifier: d[0],
					time: d[1],
					names: d.slice(2, 7).map((name) => {
						const split = name.split(' ');
						return { name: split[0], offset: +split[1] };
					}),
					hex: d[7]
				};

				if (
					logs.find(
						(log) =>
							log.identifier === new_log.identifier &&
							log.time === new_log.time &&
							log.names.length === new_log.names.length &&
							log.names.every((name, i) => name.name === new_log.names[i].name)
					)
				) {
					return;
				}

				logs.push(new_log);
				logs = logs;
			} else if (data.includes('Error while reading network.')) {
				alert('Error while reading network. Please report this in the CoGM support server.');
			}
		} else if (status === ('error' as any)) {
			console.error(data);
			alert(
				'An error occured while trying to start the logger. Error message: ' +
					data +
					'\nLogger will be restarted.'
			);
			if (!is_destroyed && retry_count < 3) {
				recording_state.set('reconnecting');
				start_logger(logger_callback, 'analyze', spawn_args());
				retry_count++;
			} else if (!is_destroyed && retry_count >= 3) {
				recording_state.set('error');
				alert('Tried to start logger 3 times, but failed. Please try again.');
			} else {
				retry_count = 0;
			}
		} else if (status === 'terminated') {
			if (!is_destroyed && retry_count < 3) {
				recording_state.set('reconnecting');
				start_logger(logger_callback, 'analyze', spawn_args());
				retry_count++;
			} else if (!is_destroyed && retry_count >= 3) {
				recording_state.set('error');
				alert('Tried to start logger 3 times, but failed. Please try again.');
			} else {
				retry_count = 0;
			}
		} else {
			alert('Unknown status: ' + status);
		}
	};

	onMount(async () => {
		config = await get_config();
		session_path = `logger/.tmp/session-${Date.now()}.log`;
		recording_state.set('recording');
		recording_started_at.set(Date.now());
		packets_seen.set(0);
		ticker = setInterval(() => { now = Date.now(); }, 1000);
		start_logger(logger_callback, 'analyze', spawn_args());
	});

	onDestroy(() => {
		is_destroyed = true;
		recording_state.set('idle');
		clearInterval(ticker);
		// Stop the sniffer when leaving the page; it otherwise keeps running
		// in the background and its stale events pop an "Invalid Logger" alert.
		stop_logger();
	});

	function handle_stop() {
		if ($recording_state === 'recording') {
			if (!confirm('Stop recording?')) return;
		}
		goto('/');
	}

	// Recovery actions for a classified capture failure.
	function retry_capture() {
		capture_error = null;
		retry_count = 0;
		recording_state.set('reconnecting');
		start_logger(logger_callback, 'analyze', spawn_args());
	}

	async function handle_restart_driver() {
		recovering = true;
		const ok = await restart_capture_driver();
		recovering = false;
		if (ok) {
			retry_capture();
		} else {
			alert('Could not restart the capture driver. You may need to reboot.');
		}
	}

	async function handle_install_npcap() {
		await os.open('https://npcap.com/dist/npcap-1.78.exe');
	}

	// Reveal the full-packet .pcap in the file manager so it's easy to attach
	// and send in. Best-effort: select-in-explorer on Windows, else open the
	// folder; a failure just does nothing.
	async function reveal_pcap() {
		if (!pcap_path) return;
		try {
			if (NL_OS === 'Windows') {
				await os.execCommand(`explorer /select,"${pcap_path}"`);
			} else {
				const dir = pcap_path.replace(/[\\/][^\\/]*$/, '');
				await os.open(dir || pcap_path);
			}
		} catch (e) {
			console.error('reveal_pcap failed', e);
		}
	}

	const CAPTURE_ERROR_INFO: Record<string, { title: string; detail: string; action: 'driver' | 'admin' | 'npcap' | 'retry' }> = {
		NPCAP_MISSING: {
			title: 'Capture driver not installed',
			detail: 'Npcap is required to read game traffic. Install it, then retry.',
			action: 'npcap'
		},
		DRIVER_NOT_RESPONDING: {
			title: "Capture driver isn't responding",
			detail:
				'This usually happens after a PC crash. Restarting the Npcap service clears it without a reboot.',
			action: 'driver'
		},
		ACCESS_DENIED: {
			title: 'Capture needs administrator rights',
			detail: 'Run the app as administrator to capture packets.',
			action: 'admin'
		},
		NO_INTERFACE: {
			title: 'No network interface found',
			detail: 'No usable network adapter was detected. Check your connection and retry.',
			action: 'retry'
		},
		UNKNOWN: {
			title: 'Capture failed to start',
			detail: 'The capture engine reported an error.',
			action: 'retry'
		}
	};

	$: error_info = capture_error
		? CAPTURE_ERROR_INFO[capture_error.code] ?? CAPTURE_ERROR_INFO.UNKNOWN
		: null;

	async function handle_pcap_toggle() {
		if (config) {
			await update_config(config);
		}
	}

	$: runtime_display = format_runtime($recording_started_at, now);

	$: state_label = $recording_state === 'recording'
		? 'RECORDING'
		: $recording_state === 'reconnecting'
		? 'RECONNECTING'
		: $recording_state === 'error'
		? 'ERROR'
		: 'IDLE';
</script>

<!-- Topbar -->
<div class="sticky top-0 z-10 h-10 flex items-center px-4 gap-3 bg-background border-b border-gray-700">
	<StatusDot state={$recording_state} pulse={$recording_state === 'recording'} size={8} />
	<span class="heading-h2">{state_label}</span>
	<span class="text-foreground-secondary text-caption mx-0.5">·</span>
	<span class="tabular-nums text-caption">{runtime_display}</span>
	<span class="text-foreground-secondary text-caption mx-0.5">·</span>
	<span class="text-caption tabular-nums">{$packets_seen.toLocaleString()} packets</span>
	<span class="text-foreground-secondary text-caption mx-0.5">·</span>
	<span class="text-caption tabular-nums">{logs.length} logs</span>

	<div class="ml-auto flex items-center gap-2">
		<button
			class="px-3 py-1 text-xs font-medium rounded bg-background-secondary border border-gray-700 text-foreground hover:border-gray-500 transition-colors"
			on:click={handle_stop}
		>
			Stop
		</button>
		<!-- TODO Phase 4: extract inline ConfigModal from logger.svelte and open it here instead of navigating -->
		<button
			class="px-2 py-1 text-xs rounded bg-background-secondary border border-gray-700 text-foreground hover:border-gray-500 transition-colors"
			on:click={() => goto('/settings')}
			aria-label="Settings"
		>
			&#9881;
		</button>
	</div>
</div>

<!-- Options bar -->
{#if config}
<div class="min-h-8 px-4 py-1.5 flex flex-col gap-1 border-b border-gray-700 bg-background">
	<div class="flex gap-3 items-center">
		<label class="flex items-center gap-2 cursor-pointer select-none">
			<input
				type="checkbox"
				class="rounded border-gray-600"
				bind:checked={config.record_pcap}
				on:change={handle_pcap_toggle}
			/>
			<span class="text-caption">Full packet capture (.pcap)</span>
			<span class="text-caption text-foreground-secondary"
				>Saves the complete raw traffic so the team can find new fields. Share the file for
				protocol research. Nothing is uploaded automatically.</span
			>
		</label>
		{#if config.record_pcap && $recording_state === 'recording' && !pcap_path}
			<span class="text-caption text-gold ml-2">Restart capture to apply</span>
		{/if}
	</div>
	{#if pcap_path}
		<div class="flex items-center gap-2">
			<span class="text-caption text-status-ok">Full capture:</span>
			<span class="text-caption text-foreground-secondary truncate flex-1 tabular-nums" title={pcap_path}
				>{pcap_path}</span
			>
			<button
				class="text-caption text-gold hover:text-gold-200 transition-colors"
				on:click={reveal_pcap}>Show file</button
			>
		</div>
	{/if}
</div>
{/if}

<!-- Capture recovery panel -->
{#if capture_error && error_info}
	<div class="m-4 rounded-lg border border-rose-500/30 bg-rose-500/5 p-4 flex flex-col gap-3">
		<div class="flex flex-col gap-1">
			<span class="heading-h2 text-rose-300">{error_info.title}</span>
			<span class="text-caption text-foreground-secondary">{error_info.detail}</span>
		</div>
		<div class="flex flex-wrap gap-2">
			{#if error_info.action === 'driver'}
				<Button on:click={handle_restart_driver} disabled={recovering}>
					{recovering ? 'Restarting driver...' : 'Restart capture driver'}
				</Button>
			{:else if error_info.action === 'admin'}
				<Button on:click={relaunch_as_admin}>Run as administrator</Button>
			{:else if error_info.action === 'npcap'}
				<Button on:click={handle_install_npcap}>Install Npcap</Button>
			{/if}
			<Button color="secondary" on:click={retry_capture}>Retry</Button>
		</div>
		{#if capture_error.message}
			<details class="text-caption text-foreground-secondary">
				<summary class="cursor-pointer">Technical details</summary>
				<p class="mt-1 break-all font-mono text-xs">{capture_error.message}</p>
			</details>
		{/if}
	</div>
{/if}

<!-- TODO: replace height={165} with get_remaining_height() from utils once utility exists -->
<Logger {logs} height={165} {session_path} />
