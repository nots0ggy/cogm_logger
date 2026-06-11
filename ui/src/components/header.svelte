<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import Icon from '../svelte-ui/elements/icon.svelte';
	import IoMdArrowRoundBack from 'svelte-icons/io/IoMdArrowRoundBack.svelte';
	import StatusDot from './status-dot.svelte';
	import { recording_state } from '../logic/recording-store';

	$: show_arrow = $page.route.id !== '/';

	const version = NL_APPVERSION;

	function handle_back() {
		goto('/');
	}
</script>

<header class="h-12 flex items-center px-4 border-b border-gray-700 gap-3">
	{#if show_arrow}
		<button
			class="flex items-center justify-center w-6 h-6 text-foreground-secondary hover:text-foreground transition-colors"
			on:click={handle_back}
			aria-label="Back to home"
		>
			<Icon icon={IoMdArrowRoundBack} />
		</button>
	{/if}

	<a href={'/'} class="heading-display text-gold leading-none no-underline">CoGM Logger</a>
	<span class="text-caption tabular-nums leading-none">{version}</span>

	<div class="ml-auto flex items-center gap-2">
		{#if $recording_state === 'recording'}
			<StatusDot state="recording" pulse={true} size={8} />
			<span class="heading-h2 leading-none">REC</span>
		{:else if $recording_state === 'reconnecting'}
			<StatusDot state="reconnecting" pulse={true} size={8} />
			<span class="heading-h2 leading-none">RECONNECT</span>
		{:else if $recording_state === 'error'}
			<StatusDot state="error" pulse={false} size={8} />
			<span class="heading-h2 leading-none">ERROR</span>
		{/if}
	</div>
</header>
