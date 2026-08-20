<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import Logger from '../../components/create-config/logger.svelte';
	import Button from '../../svelte-ui/elements/button.svelte';
	import LoadingIndicator from '../../svelte-ui/elements/loading-indicator.svelte';
	import { find_last_session, load_session } from '../../logic/recover';
	import { page } from '$app/stores';
	import type { LogType } from '../../components/create-config/config';

	let loading = true;
	let logs: LogType[] = [];
	let filename = '';
	// True when opened from History for one specific file (vs crash recovery).
	let from_history = false;
	// Full path of the recovered file, handed to the editor so a Save/Upload
	// clears it (it's no longer an unsaved session after that).
	let recovered_path: string | null = null;

	onMount(async () => {
		try {
			// History passes ?path= to open one specific session; without it this
			// stays the crash-recovery flow (newest unsaved session).
			const wanted = $page.url.searchParams.get('path');
			from_history = !!wanted;
			const session = wanted ? await load_session(wanted) : await find_last_session();
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
	<!-- Nothing to load: crash recovery found no session, or a History open
	     hit a file with no readable records. Two different situations, two
	     different explanations. -->
	<div class="flex-1 flex items-center justify-center w-full">
		<div
			class="flex flex-col items-center gap-4 px-8 py-10 border border-gray-700 rounded-md bg-background-secondary max-w-sm text-center"
		>
			{#if from_history}
				<p class="heading-display text-foreground">Couldn't open this session</p>
				<p class="text-caption">
					The file has no readable war records. It may be empty, damaged, or all its records were
					filtered out by your current roster.
				</p>
				<Button on:click={() => goto('/history')}>Back to History</Button>
			{:else}
				<p class="heading-display text-foreground">Nothing to recover</p>
				<p class="text-caption">
					No unsaved session was found on disk. Sessions are saved here automatically while you
					record, so a crash won't lose your war.
				</p>
				<Button on:click={() => goto('/')}>Back home</Button>
			{/if}
		</div>
	</div>
{:else}
	<!-- Recovered: load straight into the editor so you can set name order and save/upload -->
	<div class="h-10 flex items-center px-1 gap-3 border-b border-gray-700 mb-3">
		<span class="heading-h2 text-status-ok">{from_history ? 'Session' : 'Recovered session'}</span>
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
