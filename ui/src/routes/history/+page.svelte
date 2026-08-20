<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { filesystem, os } from '@neutralinojs/lib';
	import Button from '../../svelte-ui/elements/button.svelte';
	import LoadingIndicator from '../../svelte-ui/elements/loading-indicator.svelte';
	import { list_sessions, type SessionEntry } from '../../logic/recover';
	import { show_toast } from '../../svelte-ui/util';

	let loading = true;
	let sessions: SessionEntry[] = [];

	// Sessions land here two ways: session-*.log is a war that was never saved
	// or uploaded (crash, forgot, or just recorded); raw-*.log is the copy a
	// Save/Upload leaves behind. The prefix IS the saved/unsaved bookkeeping.

	onMount(refresh);

	async function refresh() {
		loading = true;
		try {
			sessions = await list_sessions();
		} catch (e) {
			console.error('history scan failed', e);
			sessions = [];
		}
		loading = false;
	}

	function open_session(s: SessionEntry) {
		goto(`/recover?path=${encodeURIComponent(s.path)}`);
	}

	async function reveal(s: SessionEntry) {
		try {
			if (NL_OS === 'Windows') {
				await os.execCommand(`explorer /select,"${s.path}"`);
			} else {
				const dir = s.path.replace(/[\\/][^\\/]*$/, '');
				await os.open(dir || s.path);
			}
		} catch (e) {
			console.error('reveal failed', e);
		}
	}

	async function remove(s: SessionEntry) {
		const warning = s.saved
			? `Delete ${s.filename}?\n\nThis war was already saved or uploaded; only the local raw copy is removed.`
			: `Delete ${s.filename}?\n\nThis session was never saved or uploaded. Deleting it loses the war.`;
		if (!confirm(warning)) return;
		try {
			await filesystem.remove(s.path);
			sessions = sessions.filter((x) => x.path !== s.path);
		} catch (e) {
			console.error('delete failed', e);
			show_toast('Could not delete the file', 'error');
		}
	}

	function format_when(mtime: number): string {
		if (!mtime) return 'unknown time';
		return new Date(mtime).toLocaleString(undefined, {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit'
		});
	}

	function format_size(bytes: number): string {
		if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
		if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
		return `${bytes} B`;
	}
</script>

<div class="h-10 flex items-center px-1 gap-3 border-b border-gray-700 mb-3">
	<span class="heading-h2">Session history</span>
	{#if !loading}
		<span class="text-foreground-secondary text-caption">·</span>
		<span class="text-caption tabular-nums">{sessions.length} on disk</span>
	{/if}
	<button
		class="ml-auto px-3 py-1 text-xs rounded bg-background-secondary border border-gray-700 text-foreground hover:border-gray-500 transition-colors"
		on:click={() => goto('/')}
	>
		Home
	</button>
</div>

{#if loading}
	<div class="flex-1 flex items-center justify-center w-full">
		<LoadingIndicator />
	</div>
{:else if sessions.length === 0}
	<div class="flex-1 flex items-center justify-center w-full">
		<div
			class="flex flex-col items-center gap-4 px-8 py-10 border border-gray-700 rounded-md bg-background-secondary max-w-sm text-center"
		>
			<p class="heading-display text-foreground">No sessions yet</p>
			<p class="text-caption">
				Every recording saves a session file here as it happens, so a war you recorded is always
				re-openable — even after a crash.
			</p>
			<Button on:click={() => goto('/record')}>Record a war</Button>
		</div>
	</div>
{:else}
	<div class="flex flex-col gap-2 overflow-y-auto pb-4">
		{#each sessions as s (s.path)}
			<div
				class="flex items-center gap-3 px-4 py-3 rounded-md border border-gray-700 bg-background-secondary"
			>
				<div class="flex flex-col min-w-0 flex-1 gap-0.5">
					<div class="flex items-center gap-2">
						<span class="text-sm text-foreground truncate" title={s.filename}>
							{format_when(s.mtime)}
						</span>
						{#if s.saved}
							<span
								class="px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide rounded bg-status-ok/15 text-status-ok"
								>Saved</span
							>
						{:else}
							<span
								class="px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide rounded bg-gold/15 text-gold"
								>Not saved</span
							>
						{/if}
					</div>
					<span class="text-caption text-foreground-secondary truncate tabular-nums">
						{s.filename} · {format_size(s.size)}
					</span>
				</div>
				<button
					class="px-3 py-1 text-xs font-medium rounded bg-background border border-gray-700 text-foreground hover:border-gray-500 transition-colors"
					on:click={() => open_session(s)}
					title="Open in the editor to review, save, or upload"
				>
					Open
				</button>
				<button
					class="px-2 py-1 text-xs rounded bg-background border border-gray-700 text-foreground-secondary hover:border-gray-500 transition-colors"
					on:click={() => reveal(s)}
					title="Show the file"
				>
					Show
				</button>
				<button
					class="px-2 py-1 text-xs rounded bg-background border border-gray-700 text-foreground-secondary hover:border-status-error hover:text-status-error transition-colors"
					on:click={() => remove(s)}
					title="Delete the file"
				>
					Delete
				</button>
			</div>
		{/each}
	</div>
{/if}
