<script lang="ts">
	import { app, os, updater } from '@neutralinojs/lib';
	import Button from '../svelte-ui/elements/button.svelte';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import LoadingIndicator from '../svelte-ui/elements/loading-indicator.svelte';
	import { check_status, type LoggerStatus } from '../logic/logger-status';
	import GoMarkGithub from 'svelte-icons/go/GoMarkGithub.svelte';
	import Icon from '../svelte-ui/elements/icon.svelte';
	import FaDiscord from 'svelte-icons/fa/FaDiscord.svelte';
	import StatusDot from '../components/status-dot.svelte';

	let loading = false;
	let status: LoggerStatus;
	let update_available = false;
	let full_update_available = false;
	let version = NL_APPVERSION;

	async function check_for_updates() {
		let url =
			'https://raw.githubusercontent.com/sch-28/ikusa_logger/main/version/version-manifest.json';
		let manifest = await updater.checkForUpdates(url);
		if (manifest.version != NL_APPVERSION) {
			if (
				manifest.version.split('.').length != NL_APPVERSION.split('.').length ||
				manifest.version.split('.')[0] != NL_APPVERSION.split('.')[0] ||
				manifest.version.split('.')[1] != NL_APPVERSION.split('.')[1]
			) {
				full_update_available = true;
			} else {
				update_available = true;
			}
			version = manifest.version;
		}
	}

	async function update() {
		try {
			if (full_update_available) {
				os.execCommand(`update.bat ${version}`, { background: true });
				await app.exit();
			} else if (update_available) {
				await updater.install();
				await app.restartProcess();
			}
		} catch (err) {
			alert(
				'Updating went wrong, check your internet connection. ' + (err as Error).message || err
			);
			console.error(err);
		}
	}

	onMount(async () => {
		try {
			loading = true;
			await check_for_updates().catch((e) => console.error(e));
			status = await check_status();
		} catch (e) {
			console.error(e);
		}
		loading = false;
	});

	$: has_update = update_available || full_update_available;
</script>

<div class="flex flex-col gap-4 max-w-md mx-auto w-full">
	<!-- Status strip -->
	<div class="flex items-center gap-3 h-8 px-1">
		{#if loading}
			<LoadingIndicator />
		{:else if status?.npcap_installed}
			<StatusDot state="ok" size={8} />
			<span class="text-caption">Npcap ready</span>
		{:else}
			<StatusDot state="error" size={8} />
			<span class="text-caption">Npcap missing</span>
			<button
				class="text-caption text-gold underline hover:text-gold-200 transition-colors"
				on:click={() => os.open('https://npcap.com/dist/npcap-1.78.exe')}
			>
				Download
			</button>
		{/if}
	</div>

	<!-- Update banner -->
	{#if has_update}
		<button
			class="flex items-center justify-between gap-3 px-4 h-10 border border-gold rounded-md bg-gold/10 hover:bg-gold/15 transition-colors"
			on:click={update}
		>
			<span class="text-caption text-gold">Version {version} available</span>
			<span class="text-caption text-gold font-semibold">Update now</span>
		</button>
	{/if}

	<!-- Primary CTA -->
	<Button class="w-full h-12" on:click={() => goto('/record')}>Record</Button>

	<!-- Secondary CTAs -->
	<div class="grid grid-cols-2 gap-3">
		<Button color="secondary" on:click={() => goto('/open')}>Open</Button>
		<Button color="secondary" on:click={() => goto('/settings')}>Settings</Button>
	</div>

	<!-- Help text link -->
	<button
		class="text-caption text-foreground-secondary hover:text-foreground transition-colors text-left"
		on:click={() => os.open('https://ikusa.site/docs/introduction')}
	>
		Help &amp; documentation →
	</button>
</div>

<!-- Footer -->
<div class="mt-auto flex items-center justify-between text-caption px-1 pb-2">
	<span>Made by <b class="text-foreground">ORACLE#7672</b></span>
	<div class="flex items-center gap-3">
		<button
			class="text-foreground-secondary hover:text-foreground transition-colors"
			on:click={() => os.open('https://discord.gg/nXSYGnxXJ5')}
			aria-label="Discord"
		>
			<Icon icon={FaDiscord} />
		</button>
		<button
			class="text-foreground-secondary hover:text-foreground transition-colors"
			on:click={() => os.open('https://github.com/sch-28/ikusa')}
			aria-label="GitHub"
		>
			<Icon icon={GoMarkGithub} />
		</button>
	</div>
</div>
