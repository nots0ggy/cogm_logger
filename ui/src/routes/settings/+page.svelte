<script lang="ts">
	import { Toggle } from 'flowbite-svelte';
	import Select from '../../components/create-config/select.svelte';
	import { onMount } from 'svelte';
	import {
		get_config,
		type Config,
		update_config,
		PERSONAL_FAMILY_NAME_KEY
	} from '../../components/create-config/config';
	import Button from '../../svelte-ui/elements/button.svelte';
	import { app, os, storage } from '@neutralinojs/lib';
	import { dev } from '$app/environment';
	import { show_toast } from '../../svelte-ui/util';

	let config: Config;

	let selected_interface = 0;
	let ip_filter = false;
	let live_output_path = '';
	let personal_family_name = '';
	let initial_load_done = false;

	let cogm_token = '';
	let cogm_url = 'https://cogm.app';
	let cogm_guild = '';
	// The token/url that produced the current cogm_guild label. The label is
	// shown only while the live inputs still match these.
	let verified_token = '';
	let verified_url = '';
	let verifying = false;

	async function update_interface() {
		if (config) {
			// Reassign config, like every other updater here. Without it the
			// local copy stays stale and the next save spreads the old value,
			// silently reverting this toggle.
			config = await update_config({ ...config, all_interfaces: selected_interface == 0 });
		}
	}

	async function update_ip_filter() {
		if (config) {
			config = await update_config({ ...config, ip_filter });
		}
	}

	async function pick_live_output_path() {
		const result = await os.showSaveDialog('Choose live output file location', {
			defaultPath: live_output_path || 'ikusa_live.log',
			filters: [{ name: 'Log file', extensions: ['log'] }]
		});
		if (result) {
			live_output_path = result;
			config = await update_config({ ...config, live_output_path });
		}
	}

	async function clear_live_output_path() {
		live_output_path = '';
		config = await update_config({ ...config, live_output_path: '' });
	}

	async function persist_family_name() {
		if (!initial_load_done) return;
		await storage.setData(PERSONAL_FAMILY_NAME_KEY, personal_family_name).catch(() => null);
	}

	async function save_cogm_settings() {
		if (!initial_load_done || !config) return;
		config = await update_config({ ...config, cogm_token, cogm_url, cogm_guild });
	}

	// The "Linked guild" label is only trustworthy for the exact token/url
	// that was verified. When either changes, drop the stale label so the
	// user re-verifies before relying on it (and so a wrong guild is never
	// shown as confirmed next to a different token).
	function on_cogm_input_change() {
		if (!initial_load_done) return;
		if (cogm_token !== verified_token || cogm_url !== verified_url) {
			cogm_guild = '';
		}
		save_cogm_settings();
	}

	async function verify_token() {
		if (!initial_load_done || verifying) return;
		verifying = true;
		const base = (cogm_url || 'https://cogm.app').replace(/\/$/, '');
		try {
			const res = await fetch(`${base}/api/logger/verify`, {
				headers: { Authorization: `Bearer ${cogm_token}` }
			});
			if (res.ok) {
				const data = await res.json();
				cogm_guild = data.guild?.name || '';
				verified_token = cogm_token;
				verified_url = cogm_url;
				config = await update_config({ ...config, cogm_token, cogm_url, cogm_guild });
				show_toast(`Token valid. Uploads go to ${cogm_guild}.`, 'success');
			} else {
				cogm_guild = '';
				let msg = 'Invalid or revoked token';
				try {
					const data = await res.json();
					if (data?.error) msg = data.error;
				} catch {}
				config = await update_config({ ...config, cogm_token, cogm_url, cogm_guild });
				show_toast(msg, 'error');
			}
		} catch {
			show_toast('Could not reach CoGM', 'error');
		} finally {
			verifying = false;
		}
	}

	$: {
		ip_filter;
		if (initial_load_done) update_ip_filter();
	}

	$: {
		personal_family_name;
		persist_family_name();
	}

	$: {
		cogm_token;
		cogm_url;
		on_cogm_input_change();
	}

	onMount(async () => {
		config = await get_config();
		selected_interface =
			config.all_interfaces === true || config.all_interfaces === undefined ? 0 : 1;
		ip_filter = config.ip_filter === true || config.ip_filter === undefined ? true : false;
		live_output_path = config.live_output_path || '';
		personal_family_name = (await storage.getData(PERSONAL_FAMILY_NAME_KEY).catch(() => '')) || '';
		cogm_token = config.cogm_token || '';
		cogm_url = config.cogm_url || 'https://cogm.app';
		cogm_guild = config.cogm_guild || '';
		// Trust the stored guild label for the stored token/url it was saved
		// against; editing either afterwards clears it via on_cogm_input_change.
		verified_token = cogm_token;
		verified_url = cogm_url;
		initial_load_done = true;
	});

	function get_binary_name() {
		return NL_OS === 'Windows' ? 'ikusa-logger-win_x64.exe' : './ikusa-logger-linux_x64';
	}

	async function restart_dev() {
		if (dev) return;
		await os.execCommand(`${get_binary_name()} --window-enable-inspector`, {
			background: true
		});
		app.exit();
	}

	async function restart_browser() {
		if (dev) return;
		await os.execCommand(`${get_binary_name()} --mode=browser`, {
			background: true
		});
		app.exit();
	}
</script>

<div class="h-full flex flex-col gap-3 max-w-md mx-auto w-full">
	<div class="rounded-md border border-gray-700 bg-background-secondary p-4 flex flex-col">
		<!-- CAPTURE -->
		<span class="heading-h2">Capture</span>

		<div class="grid grid-cols-[1fr_auto] gap-3 items-center mt-3">
			<div>
				<p class="text-foreground">Network interface</p>
				<p class="text-caption">Which interface to sniff for packets</p>
			</div>
			<Select
				options={['All', 'Default']}
				bind:selected_value={selected_interface}
				on_change={update_interface}
			/>
		</div>

		<div class="grid grid-cols-[1fr_auto] gap-3 items-center mt-3">
			<div>
				<p class="text-foreground">IP filter</p>
				<p class="text-caption">Filter packets by known game server IPs</p>
			</div>
			<Toggle bind:checked={ip_filter} />
		</div>

		<!-- OUTPUT -->
		<div class="divider-top">
			<span class="heading-h2">Output</span>
			<div class="mt-3">
				<p class="text-foreground">Live output file</p>
				<p class="text-caption">Logs written here in real time. Leave empty to disable.</p>
				<div class="flex gap-2 items-center mt-2">
					<span
						class="text-xs truncate text-foreground-secondary min-w-0 flex-1 bg-background border border-gray-700 rounded px-2 py-1.5 tabular-nums"
						title={live_output_path}
					>
						{live_output_path
							? live_output_path.split(/[\\/]/).slice(-2).join('/')
							: 'Not set'}
					</span>
					<Button size="sm" on:click={pick_live_output_path}>Browse</Button>
					{#if live_output_path}
						<Button size="sm" color="secondary" on:click={clear_live_output_path}>Clear</Button>
					{/if}
				</div>
			</div>
		</div>

		<!-- PERSONAL -->
		<div class="divider-top">
			<span class="heading-h2">Personal</span>
			<div class="mt-3">
				<p class="text-foreground">Family name</p>
				<p class="text-caption">Highlights your kills and deaths in the live log.</p>
				<input
					class="w-full mt-2 bg-background border border-gray-700 rounded px-2 py-1.5 text-foreground tabular-nums"
					placeholder="Enter your BDO family name"
					bind:value={personal_family_name}
				/>
			</div>
		</div>

		<!-- COGM -->
		<div class="divider-top">
			<span class="heading-h2">CoGM</span>
			<div class="mt-3">
				<p class="text-foreground">Token</p>
				<p class="text-caption">Upload logs to your CoGM community.</p>
				<div class="flex gap-2 items-center mt-2">
					<input
						type="password"
						class="flex-1 min-w-0 bg-background border border-gray-700 rounded px-2 py-1.5 text-foreground text-sm"
						placeholder="Paste your CoGM upload token"
						bind:value={cogm_token}
					/>
					<Button size="sm" on:click={verify_token} disabled={!cogm_token || verifying}>
						{verifying ? 'Verifying...' : 'Verify'}
					</Button>
				</div>
				{#if cogm_guild && cogm_token === verified_token && cogm_url === verified_url}
					<p class="text-caption mt-1">Linked guild: {cogm_guild}</p>
				{/if}
			</div>
		</div>

		<!-- ADVANCED -->
		<details class="divider-top group">
			<summary class="cursor-pointer flex items-center justify-between list-none">
				<span class="heading-h2">Advanced</span>
				<span class="text-caption group-open:rotate-180 transition-transform">▾</span>
			</summary>
			<div class="mt-3 flex gap-2">
				<Button class="flex-1" color="secondary" on:click={restart_dev}>Dev Mode</Button>
				<Button class="flex-1" color="secondary" on:click={restart_browser}>Browser Mode</Button>
			</div>
			<p class="text-caption mt-2">
				Dev Mode opens the WebView inspector. Browser Mode runs the UI in your default browser.
			</p>
			<div class="mt-3">
				<p class="text-foreground text-sm">CoGM server</p>
				<p class="text-caption">Target server for uploads. Change only for local development.</p>
				<input
					class="w-full mt-2 bg-background border border-gray-700 rounded px-2 py-1.5 text-foreground text-sm tabular-nums"
					placeholder="https://cogm.app"
					bind:value={cogm_url}
				/>
			</div>
		</details>
	</div>
</div>
