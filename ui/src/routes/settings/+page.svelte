<script lang="ts">
	import { Toggle } from 'flowbite-svelte';
	import Select from '../../components/create-config/select.svelte';
	import { onMount } from 'svelte';
	import {
		get_config,
		type Config,
		type NameOrderSample,
		type CogmRoster,
		update_config,
		get_name_order_sample,
		get_cogm_roster,
		save_cogm_roster,
		clear_cogm_roster,
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

	// Roster state for name-order auto-detection. Pulled from CoGM after the
	// token verifies; drives the hint under the token field and is read by the
	// record screen's detection. 'unconfigured' means no Own Alliance is set up
	// on CoGM (auto-detect falls back to the offline heuristic).
	type RosterStatus = 'none' | 'loading' | 'configured' | 'empty' | 'unconfigured' | 'error';
	let roster_status: RosterStatus = 'none';
	let roster_summary = '';

	// Name order: the captured columns from the last war (for the dropdowns) and
	// the saved Killer/Victim/Guild assignment (config.name_order).
	let name_order_sample: NameOrderSample | null = null;
	$: sample_names = name_order_sample ? name_order_sample.names.map((n) => n.name || '-') : [];
	function col_name(i: number): string {
		return name_order_sample?.names[i]?.name || '-';
	}

	// Reassign Killer/Victim/Guild to a captured column, swapping with whatever
	// currently holds that column so the three stay distinct. Same rule the
	// record-screen panel uses.
	async function set_name_role(role: 'killer' | 'victim' | 'guild', e: Event) {
		if (!config) return;
		const v = parseInt((e.target as HTMLSelectElement).value);
		const ord = { ...config.name_order };
		if (role === 'killer') {
			if (v === ord.victim) ord.victim = ord.killer;
			else if (v === ord.guild) ord.guild = ord.killer;
			ord.killer = v;
		} else if (role === 'victim') {
			if (v === ord.killer) ord.killer = ord.victim;
			else if (v === ord.guild) ord.guild = ord.victim;
			ord.victim = v;
		} else {
			if (v === ord.killer) ord.killer = ord.guild;
			else if (v === ord.victim) ord.victim = ord.guild;
			ord.guild = v;
		}
		config = await update_config({ ...config, name_order: ord });
	}
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
			defaultPath: live_output_path || 'cogm_live.log',
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
			// The stored roster belongs to the previously verified token. Drop it
			// (and its hint) so the record screen never auto-detects against a
			// different guild's roster; re-verifying repopulates it.
			roster_status = 'none';
			roster_summary = '';
			clear_cogm_roster();
		}
		save_cogm_settings();
	}

	// Reflect a roster blob in the hint under the token field.
	function apply_roster_summary(roster: CogmRoster | null) {
		if (!roster) {
			roster_status = 'none';
			roster_summary = '';
			return;
		}
		if (!roster.configured) {
			roster_status = 'unconfigured';
			roster_summary = '';
			return;
		}
		const fam = roster.ownFamilyNames.length;
		const g = roster.guilds.length;
		if (fam === 0) {
			roster_status = 'empty';
			roster_summary = '';
			return;
		}
		roster_status = 'configured';
		roster_summary = `${fam} family ${fam === 1 ? 'name' : 'names'} across ${g} ${
			g === 1 ? 'guild' : 'guilds'
		}`;
	}

	// Pull the alliance roster CoGM uses to auto-detect the name order. Runs
	// after a successful token verify; non-fatal (a failure leaves auto-detect
	// on its offline fallback, the verify itself still succeeded).
	async function fetch_roster() {
		const base = (cogm_url || 'https://cogm.app').replace(/\/$/, '');
		roster_status = 'loading';
		// Drop the prior roster before the refresh so any failure path leaves
		// storage empty (record screen falls back to manual) instead of keeping
		// a stale roster the Settings hint says it could not load.
		await clear_cogm_roster();
		try {
			const res = await fetch(`${base}/api/logger/roster`, {
				headers: { Authorization: `Bearer ${cogm_token}` }
			});
			if (!res.ok) {
				roster_status = 'error';
				return;
			}
			const data = await res.json();
			const a = data.alliance || {};
			const roster: CogmRoster = {
				configured: a.configured === true,
				guildName: data.guild?.name || '',
				guilds: Array.isArray(a.guilds) ? a.guilds : [],
				enemyGuilds: Array.isArray(a.enemyGuilds) ? a.enemyGuilds : [],
				ownFamilyNames: Array.isArray(a.ownFamilyNames) ? a.ownFamilyNames : [],
				enemyFamilyNames: Array.isArray(a.enemyFamilyNames) ? a.enemyFamilyNames : [],
				fetchedAt: new Date().toISOString()
			};
			await save_cogm_roster(roster);
			apply_roster_summary(roster);
		} catch {
			roster_status = 'error';
		}
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
				await fetch_roster();
			} else {
				cogm_guild = '';
				roster_status = 'none';
				roster_summary = '';
				await clear_cogm_roster();
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
		name_order_sample = await get_name_order_sample();
		// Show the status of the roster last fetched for this token (if any).
		apply_roster_summary(await get_cogm_roster());
		// Trust the stored guild label for the stored token/url it was saved
		// against; editing either afterwards clears it via on_cogm_input_change.
		verified_token = cogm_token;
		verified_url = cogm_url;
		initial_load_done = true;
	});

	function get_binary_name() {
		return NL_OS === 'Windows' ? 'cogm-logger-win_x64.exe' : './cogm-logger-linux_x64';
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

		<!-- NAME ORDER -->
		<div class="divider-top">
			<span class="heading-h2">Name order</span>
			<p class="text-caption mt-1">
				Which captured column is the killer, victim, and guild. Set once and it applies to every
				war.
			</p>
			{#if name_order_sample && config}
				<div class="grid grid-cols-3 gap-3 mt-3">
					<div class="flex flex-col gap-1">
						<span class="text-caption text-status-ok">Killer</span>
						<Select
							options={sample_names}
							selected_value={config.name_order.killer}
							on_change={(e) => set_name_role('killer', e)}
						/>
					</div>
					<div class="flex flex-col gap-1">
						<span class="text-caption text-status-error">Victim</span>
						<Select
							options={sample_names}
							selected_value={config.name_order.victim}
							on_change={(e) => set_name_role('victim', e)}
						/>
					</div>
					<div class="flex flex-col gap-1">
						<span class="text-caption text-gold">Guild</span>
						<Select
							options={sample_names}
							selected_value={config.name_order.guild}
							on_change={(e) => set_name_role('guild', e)}
						/>
					</div>
				</div>
				<div class="rounded-md border border-gray-700 bg-background p-2 mt-3">
					<span class="text-caption">Preview</span>
					<p class="text-sm mt-1">
						<span class="text-status-ok">{col_name(config.name_order.killer)}</span>
						<span class="text-gray-400">killed</span>
						<span class="text-status-error">{col_name(config.name_order.victim)}</span>
						<span class="text-gray-400">from</span>
						<span class="text-gold">{col_name(config.name_order.guild)}</span>
					</p>
				</div>
				<p class="text-caption mt-2">
					Columns are from your last capture. Auto-detect runs on the record screen during a war.
				</p>
			{:else}
				<p class="text-caption mt-3">
					Record a war once and the captured names show up here to set the order. You can also set
					it on the record screen during a war.
				</p>
			{/if}
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
				{#if roster_status === 'loading'}
					<p class="text-caption mt-1">Loading alliance roster...</p>
				{:else if roster_status === 'configured'}
					<p class="text-caption mt-1 text-status-ok">Auto-detect ready: {roster_summary}.</p>
				{:else if roster_status === 'empty'}
					<p class="text-caption mt-1 text-gold">
						Alliance found, roster still syncing. Auto-detect uses your family name until it fills
						in.
					</p>
				{:else if roster_status === 'unconfigured'}
					<p class="text-caption mt-1 text-gold">
						Set up your Own Alliance on CoGM for accurate auto-detect. Manual name order still
						works.
					</p>
				{:else if roster_status === 'error'}
					<p class="text-caption mt-1 text-foreground-secondary">
						Could not load the roster. Auto-detect will use the offline fallback.
					</p>
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
