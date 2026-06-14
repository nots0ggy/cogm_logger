<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import Logger from '../../components/create-config/logger.svelte';
	import Button from '../../svelte-ui/elements/button.svelte';
	import LoadingIndicator from '../../svelte-ui/elements/loading-indicator.svelte';
	import { find_last_session } from '../../logic/recover';
	import type { LogType } from '../../components/create-config/config';

	let loading = true;
	let logs: LogType[] = [];
	let filename = '';
	// Full path of the recovered file, handed to the editor so a Save/Upload
	// clears it (it's no longer an unsaved session after that).
	let recovered_path: string | null = null;

	onMount(async () => {
		try {
			const session = await find_last_session();
			if (session) {
				logs = session.logs;
				recovered_path = session.path;
				filename = session.path.split(/[\\/]/).pop() ?? session.path;
			}
		} catch (e) {
			console.error('recover failed', e);
		}
		loading = false;
	});
</script>

{#if loading}
	<div class="flex-1 flex items-center justify-center w-full">
		<LoadingIndicator />
	</div>
{:else if logs.length === 0}
	<!-- Nothing to recover -->
	<div class="flex-1 flex items-center justify-center w-full">
		<div
			class="flex flex-col items-center gap-4 px-8 py-10 border border-gray-700 rounded-md bg-background-secondary max-w-sm text-center"
		>
			<p class="heading-display text-foreground">Nothing to recover</p>
			<p class="text-caption">
				No unsaved session was found on disk. Sessions are saved here automatically while you
				record, so a crash won't lose your war.
			</p>
			<Button on:click={() => goto('/')}>Back home</Button>
		</div>
	</div>
{:else}
	<!-- Recovered: load straight into the editor so you can set name order and save/upload -->
	<div class="h-10 flex items-center px-1 gap-3 border-b border-gray-700 mb-3">
		<span class="heading-h2 text-status-ok">Recovered session</span>
		<span class="text-foreground-secondary text-caption">·</span>
		<span class="text-caption tabular-nums">{logs.length} logs</span>
		<span class="text-foreground-secondary text-caption truncate">· {filename}</span>
		<button
			class="ml-auto px-3 py-1 text-xs rounded bg-background-secondary border border-gray-700 text-foreground hover:border-gray-500 transition-colors"
			on:click={() => goto('/')}
		>
			Home
		</button>
	</div>
	<Logger {logs} height={375} session_path={recovered_path} />
{/if}
