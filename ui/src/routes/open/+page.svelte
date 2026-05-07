<script lang="ts">
	import Button from '../../svelte-ui/elements/button.svelte';
	import { start_logger, type LoggerCallback } from '../../logic/logger-wrapper';
	import Logger from '../../components/create-config/logger.svelte';
	import { open_file } from '../../logic/file';
	import { get_config, type Log, type LogType } from '../../components/create-config/config';
	import { filesystem } from '@neutralinojs/lib';
	import LogEditor from '../../components/create-config/log-editor.svelte';
	let logs: LogType[] = [];
	let combat_logs: Log[] = [];
	let loading = false;

	let is_network = false;
	let loaded_filename: string = '';

	$: total_loaded = is_network ? logs.length : combat_logs.length;
	$: has_file = loaded_filename !== '';

	const log_regex = /\[(.+)\] (\w+) (died to|has killed) (\w+) from (\w+|-1)(?: \((\w+),(\w+)\))?/;

	const logger_callback: LoggerCallback = (data, status) => {
		if (status === 'running') {
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
			}
		} else if (status === ('error' as any)) {
			console.error(data);
			loading = false;
		} else if (status === 'terminated') {
			loading = false;
		}
	};

	async function open_pcap() {
		logs = [];
		combat_logs = [];
		const filePaths = await open_file();
		if (!filePaths || filePaths.length === 0) return;
		const config = await get_config();
		loaded_filename = filePaths[0].split(/[\\/]/).pop() ?? filePaths[0];
		if ((filePaths.length > 0 && filePaths[0].includes('.txt')) || filePaths[0].includes('.log')) {
			const filePath = filePaths[0];
			is_network = false;
			let data = await filesystem.readFile(filePath);
			if (!data) return;
			logs = [];
			const lines = data.split('\n');
			for (const line of lines) {
				const match = line.match(log_regex);
				if (match) {
					const new_combat_log: Log = {
						time: match[1],
						names: [match[2], match[4], match[5], match[6], match[7]],
						kill: match[3] === 'has killed'
					};
					combat_logs.push(new_combat_log);
				}
			}
			combat_logs = combat_logs;
		} else {
			is_network = true;
			start_logger(
				logger_callback,
				'analyze',
				'-f ' + '"' + filePaths + '"' + (config.ip_filter ? ' -p' : '')
			);
			loading = true;
		}
	}
</script>

{#if !has_file}
	<!-- Empty state -->
	<div class="flex-1 flex items-center justify-center w-full">
		<div class="flex flex-col items-center gap-4 px-8 py-10 border border-gray-700 rounded-md bg-background-secondary max-w-sm">
			<p class="heading-display text-foreground text-center">Open a file</p>
			<p class="text-caption text-center">
				Accepts <span class="text-foreground">.log</span>,
				<span class="text-foreground">.txt</span>, and
				<span class="text-foreground">.pcap</span> files captured by the logger.
			</p>
			<Button on:click={open_pcap}>Open File</Button>
		</div>
	</div>
{:else}
	<!-- Loaded state: thin topbar + viewer -->
	<div class="h-10 flex items-center px-1 gap-3 border-b border-gray-700 mb-3">
		<span class="text-foreground tabular-nums">{loaded_filename}</span>
		<span class="text-foreground-secondary text-caption">·</span>
		<span class="text-caption tabular-nums">{total_loaded} logs</span>
		<button
			class="ml-auto px-3 py-1 text-xs rounded bg-background-secondary border border-gray-700 text-foreground hover:border-gray-500 transition-colors"
			on:click={open_pcap}
		>
			Open another
		</button>
	</div>

	{#if is_network}
		<Logger {logs} height={375} {loading} />
	{:else}
		<LogEditor logs={combat_logs} height={375} {loading} />
	{/if}
{/if}
