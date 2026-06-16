<script lang="ts">
	import { init, events, app } from '@neutralinojs/lib';
	import { onMount } from 'svelte';
	import { kill_logger_process } from '../logic/logger-wrapper';
	import '../app.css';
	import Modal from '../svelte-ui/modal/modal.svelte';
	import { Toaster } from 'svelte-french-toast';
	import { get_remaining_height } from '../svelte-ui/util';
	import Header from '../components/header.svelte';
	import LoadingIndicator from '../svelte-ui/elements/loading-indicator.svelte';

	let is_ready = false;

	onMount(() => {
		init();
		events.on('ready', () => {
			is_ready = true;
		});
		events.on('windowClose', async () => {
			// Kill the sniffer before exiting. Was an unguarded Windows taskkill
			// that no-op'd on Linux, leaving the capture process orphaned on close.
			await kill_logger_process();
			await app.exit();
		});
	});

	let container: HTMLElement;
</script>

{#if is_ready}
	<div class="h-full w-full">
		<div class="h-screen w-full max-w-4xl mx-auto flex flex-col">
			<Header />
			<div
				class="mt-4 px-4 flex-1 flex flex-col"
				bind:this={container}
				style="height: {get_remaining_height(container, 16)}px;"
			>
				<slot />
			</div>
		</div>
		<Modal />
		<Toaster />
	</div>
{:else}
	<div class="h-screen w-screen flex items-center justify-center">
		<LoadingIndicator />
	</div>
{/if}
