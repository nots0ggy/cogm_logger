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

	let config: Config;

	let selected_interface = 0;
	let ip_filter = false;
	let live_output_path = '';
	let personal_family_name = '';
	let initial_load_done = false;

	async function update_interface() {
		if (config) {
			await update_config({ ...config, all_interfaces: selected_interface == 0 });
		}
	}

	async function update_ip_filter() {
		if (config) {
			await update_config({ ...config, ip_filter });
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

	$: {
		ip_filter;
		if (initial_load_done) update_ip_filter();
	}

	$: {
		personal_family_name;
		persist_family_name();
	}

	onMount(async () => {
		config = await get_config();
		selected_interface =
			config.all_interfaces === true || config.all_interfaces === undefined ? 0 : 1;
		ip_filter = config.ip_filter === true || config.ip_filter === undefined ? true : false;
		live_output_path = config.live_output_path || '';
		personal_family_name = (await storage.getData(PERSONAL_FAMILY_NAME_KEY).catch(() => '')) || '';
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
		</details>
	</div>
</div>
