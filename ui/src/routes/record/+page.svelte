<script lang="ts">
	import { type LoggerCallback, start_logger } from '../../logic/logger-wrapper';
	import { onDestroy, onMount } from 'svelte';
	import Logger from '../../components/create-config/logger.svelte';
	import { get_config, update_config, type Config, type LogType } from '../../components/create-config/config';
	import { recording_state, recording_started_at, packets_seen } from '../../logic/recording-store';
	import StatusDot from '../../components/status-dot.svelte';
	import { goto } from '$app/navigation';

	let logs: LogType[] = [];
	let is_destroyed = false;
	let retry_count = 0;
	let config: Config;
	let now = Date.now();
	let ticker: ReturnType<typeof setInterval>;

	function build_flags(cfg: Config): string {
		return (cfg.all_interfaces ? '-i' : '') +
			(cfg.ip_filter ? ' -p' : '') +
			(cfg.record_pcap ? ' -r' : '');
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
		if (status === 'running') {
			packets_seen.update((n) => n + 1);

			const d = data.split(',');
			if (d.length === 8 && !data.includes('Network Interfaces:')) {
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
				alert('Error while reading network. Please notify me on Discord.');
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
				start_logger(logger_callback, 'analyze', build_flags(config));
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
				start_logger(logger_callback, 'analyze', build_flags(config));
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
		recording_state.set('recording');
		recording_started_at.set(Date.now());
		packets_seen.set(0);
		ticker = setInterval(() => { now = Date.now(); }, 1000);
		start_logger(logger_callback, 'analyze', build_flags(config));
	});

	onDestroy(() => {
		is_destroyed = true;
		recording_state.set('idle');
		clearInterval(ticker);
	});

	function handle_stop() {
		if ($recording_state === 'recording') {
			if (!confirm('Stop recording?')) return;
		}
		goto('/');
	}

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
<div class="h-8 px-4 flex gap-3 items-center border-b border-gray-700 bg-background">
	<label class="flex items-center gap-2 cursor-pointer select-none">
		<input
			type="checkbox"
			class="rounded border-gray-600"
			bind:checked={config.record_pcap}
			on:change={handle_pcap_toggle}
		/>
		<span class="text-caption">Also save raw network capture (.pcap)</span>
		<span class="text-caption text-foreground-secondary">Lets us extract more fields offline. Nothing is uploaded.</span>
	</label>
	{#if config.record_pcap && $recording_state === 'recording'}
		<span class="text-caption text-gold ml-2">Restart capture to apply</span>
	{/if}
</div>
{/if}

<!-- TODO: replace height={165} with get_remaining_height() from utils once utility exists -->
<Logger {logs} height={165} />
