<script lang="ts">
	import { init, events, app } from '@neutralinojs/lib';
	import { onMount } from 'svelte';
	import { kill_logger_process } from '../logic/logger-wrapper';
	import { get_config } from '../components/create-config/config';
	import {
		init_remote_registry,
		refresh_remote_registry
	} from '../components/create-config/packet-registry';
	import '../app.css';
	import Modal from '../svelte-ui/modal/modal.svelte';
	import { Toaster } from 'svelte-french-toast';
	import { get_remaining_height } from '../svelte-ui/util';
	import Header from '../components/header.svelte';
	import LoadingIndicator from '../svelte-ui/elements/loading-indicator.svelte';

	let is_ready = false;

	// The app stays open for days, while a war is recorded in a single sitting.
	// Pulling the packet table only at launch meant a calibration published
	// during a session could not reach the person it was published for — they
	// were told to restart at the one moment they could not. Re-pull on this
	// cadence instead; the payload is a few hundred bytes.
	const REGISTRY_REFRESH_MS = 10 * 60 * 1000;
	let registry_timer: ReturnType<typeof setInterval> | null = null;

	onMount(() => {
		init();
		events.on('ready', () => {
			is_ready = true;
			// Pull the calibrated packet registry from cogm.app in the background
			// (cached for offline use; the compiled table is the fallback). Never
			// blocks the UI and never throws.
			get_config()
				.then(async (cfg) => {
					const url = cfg.cogm_url || 'https://cogm.app';
					await init_remote_registry(url);
					registry_timer = setInterval(() => {
						refresh_remote_registry(url).catch(() => {});
					}, REGISTRY_REFRESH_MS);
				})
				.catch(() => {});
		});
		events.on('windowClose', async () => {
			// Kill the sniffer before exiting. Was an unguarded Windows taskkill
			// that no-op'd on Linux, leaving the capture process orphaned on close.
			await kill_logger_process();
			await app.exit();
		});
		return () => {
			if (registry_timer) clearInterval(registry_timer);
		};
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
