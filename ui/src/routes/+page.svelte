<script lang="ts">
	import { app, os, updater } from '@neutralinojs/lib';
	import Button from '../svelte-ui/elements/button.svelte';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import LoadingIndicator from '../svelte-ui/elements/loading-indicator.svelte';
	import { check_status, type LoggerStatus } from '../logic/logger-status';
	import { start_logger, stop_logger } from '../logic/logger-wrapper';
	import { find_last_session } from '../logic/recover';
	import GoMarkGithub from 'svelte-icons/go/GoMarkGithub.svelte';
	import Icon from '../svelte-ui/elements/icon.svelte';
	import FaDiscord from 'svelte-icons/fa/FaDiscord.svelte';
	import StatusDot from '../components/status-dot.svelte';

	let loading = false;
	let status: LoggerStatus;
	let update_available = false;
	let full_update_available = false;
	let version = NL_APPVERSION;
	let recoverable_logs = 0;
	let update_failed = false;
	let updating = false;

	/** True when a is a strictly newer semver than b. The old check fired on ANY
	 * mismatch, which during the tag-first release window (manifest still behind
	 * the freshly installed build) would have forced a DOWNGRADE. A capture bug
	 * fix must never be able to un-ship itself. */
	function newer_than(a: string, b: string): boolean {
		const pa = a.split('.').map(Number);
		const pb = b.split('.').map(Number);
		for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
			const x = pa[i] ?? 0;
			const y = pb[i] ?? 0;
			if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
			if (x !== y) return x > y;
		}
		return false;
	}

	async function check_for_updates() {
		let url =
			'https://raw.githubusercontent.com/nots0ggy/cogm_logger/main/version/version-manifest.json';
		let manifest = await updater.checkForUpdates(url);
		if (newer_than(manifest.version, NL_APPVERSION)) {
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
		updating = true;
		update_failed = false;
		try {
			if (full_update_available) {
				os.execCommand(`update.bat ${version}`, { background: true });
				await app.exit();
			} else if (update_available) {
				try {
					await updater.install();
					await app.restartProcess();
				} catch (err) {
					// Installed builds live in Program Files, where this
					// unelevated process can never overwrite resources.neu, so
					// updater.install() fails on EVERY installed machine — that
					// is what stranded 1.31.0 users when 1.31.1 shipped as the
					// first patch-level bump. The installer path elevates via
					// UAC and is the one every user has already been through,
					// so hand over to it instead of surfacing a dead Retry.
					// Portable builds run from a writable folder, take the
					// in-place path above, and never reach this.
					console.error('resources update failed, handing over to the installer:', err);
					os.execCommand(`update.bat ${version}`, { background: true });
					await app.exit();
				}
			}
		} catch (err) {
			// Stay blocked (the gate only exists when we KNOW a newer build is
			// out), but say so and offer a retry instead of an alert() loop.
			update_failed = true;
			console.error(err);
		}
		updating = false;
	}

	onMount(async () => {
		try {
			loading = true;
			await check_for_updates().catch((e) => console.error(e));
			status = await check_status();
			// Surface a recover entry if a crash left an unsaved session.
			await find_last_session()
				.then((s) => {
					recoverable_logs = s?.logs.length ?? 0;
				})
				.catch(() => {});
		} catch (e) {
			console.error(e);
		}
		loading = false;
		// Forced update: an outdated logger records worse wars (the 1.30
		// interface + dedup fixes exist because of exactly that), so when a
		// newer build is KNOWN to exist, update immediately instead of asking.
		// Fail-open by construction: if the manifest can't be reached,
		// check_for_updates threw and no flag was set, so an outage or an
		// offline machine can never block recording. Flags read directly (not
		// the $: derived) so this never races the reactive flush.
		if (update_available || full_update_available) {
			await update();
		}
	});

	$: has_update = update_available || full_update_available;

	// ── Pre-war capture self-test ──
	// One click before the war answers "will kills actually be captured":
	// the sniffer looks for the running game's own server traffic through the
	// same interface ladder the real capture uses.
	type TestState = 'idle' | 'running' | 'ok' | 'fail';
	let test_state: TestState = 'idle';
	let test_message = '';
	let test_timeout: ReturnType<typeof setTimeout> | null = null;

	const TEST_FAIL_MESSAGES: Record<string, string> = {
		GAME_NOT_RUNNING: 'Black Desert is not running. Start the game first.',
		NO_CONNECTION: 'Game found, but no server connection yet. Finish loading in, then retest.',
		NO_TRAFFIC:
			'Game traffic is not visible to the capture driver. Try Restart capture driver in Settings, or run as administrator.',
		UNSUPPORTED_OS: 'The self-test is Windows-only.',
		ERROR: 'Self-test failed to run. Try again, or check Settings.'
	};

	async function run_capture_test() {
		if (test_state === 'running') return;
		test_state = 'running';
		test_message = 'Looking for the game connection…';
		// The python side bounds itself (~30s worst case across attempts); this
		// guard is for a wedged process so the button can't stick forever.
		test_timeout = setTimeout(async () => {
			if (test_state === 'running') {
				test_state = 'fail';
				test_message = TEST_FAIL_MESSAGES.ERROR;
				await stop_logger().catch(() => {});
			}
		}, 45_000);

		await start_logger((line, status) => {
			if (status === 'running' && line.startsWith('SELFTEST ')) {
				const rest = line.slice('SELFTEST '.length);
				const code = rest.split(' ')[0];
				if (code === 'OK') {
					const iface = /iface=(.+?) server=/.exec(rest)?.[1] ?? 'capture path';
					const packets = /packets=(\d+)/.exec(rest)?.[1] ?? '?';
					test_state = 'ok';
					test_message = `Capture path confirmed on ${iface} (${packets} game packets seen). You're ready to record.`;
				} else if (code === 'TRYING') {
					test_message = `Listening on ${rest.slice('TRYING '.length)}…`;
				} else if (code in TEST_FAIL_MESSAGES) {
					test_state = 'fail';
					test_message = TEST_FAIL_MESSAGES[code];
				}
			} else if (status === 'terminated') {
				if (test_timeout) clearTimeout(test_timeout);
				if (test_state === 'running') {
					test_state = 'fail';
					test_message = TEST_FAIL_MESSAGES.ERROR;
				}
			}
		}, 'test');
	}
</script>

<div class="flex flex-col gap-4 max-w-md mx-auto w-full">
	<!-- Status strip -->
	<div class="flex items-center gap-3 h-8 px-1">
		{#if loading}
			<LoadingIndicator />
		{:else if status?.npcap_installed}
			<StatusDot state="ok" size={8} />
			<span class="text-caption">Ready to record</span>
		{:else}
			<StatusDot state="error" size={8} />
			<span class="text-caption text-gold">Npcap required to capture</span>
			<button
				class="text-caption text-gold underline hover:text-gold-200 transition-colors"
				on:click={() => os.open('https://npcap.com/dist/npcap-1.78.exe')}
			>
				Download
			</button>
		{/if}
	</div>

	<!-- Update gate: a known-newer build updates itself; recording waits. An
	     old logger captures worse wars, so "skip for now" is not on offer. -->
	{#if has_update}
		{#if updating}
			<div
				class="flex items-center justify-between gap-3 px-4 h-10 border border-gold rounded-md bg-gold/10"
			>
				<span class="text-caption text-gold">Updating to {version}…</span>
				<LoadingIndicator />
			</div>
		{:else if update_failed}
			<button
				class="flex items-center justify-between gap-3 px-4 h-10 border border-gold rounded-md bg-gold/10 hover:bg-gold/15 transition-colors"
				on:click={update}
			>
				<span class="text-caption text-gold"
					>Update to {version} failed. Retry, or reinstall the latest logger.</span
				>
				<span class="text-caption text-gold font-semibold">Retry</span>
			</button>
		{:else}
			<div
				class="flex items-center justify-between gap-3 px-4 h-10 border border-gold rounded-md bg-gold/10"
			>
				<span class="text-caption text-gold">Version {version} required</span>
				<span class="text-caption text-gold font-semibold">Starting update…</span>
			</div>
		{/if}
	{/if}

	<!-- Primary CTA: the app's one job, so make it the clear hero. Gate ONLY on a
	     definitive "Npcap missing" so a stalled/unknown status never disables the
	     button forever (the /record page surfaces the real error and the recovery
	     panel if something is actually wrong). -->
	<div>
		<Button
			class="w-full h-14 text-base font-semibold"
			on:click={() => goto('/record')}
			disabled={status?.npcap_installed === false || has_update}
		>
			Record
		</Button>
		<p class="text-caption text-center mt-1.5">
			{status?.npcap_installed === false
				? 'Install Npcap to start capturing'
				: has_update
				? `Update to ${version} to record. Old versions capture incomplete wars.`
				: 'Start capturing your guild war'}
		</p>
	</div>

	<!-- Recover banner: an attention prompt after a crash, not a success state. -->
	{#if recoverable_logs > 0}
		<button
			class="flex items-center justify-between gap-2 h-12 px-4 rounded-md border border-gold/40 bg-gold/10 hover:bg-gold/15 transition-colors"
			on:click={() => goto('/recover')}
		>
			<span class="text-sm text-gold font-semibold">Recover last session</span>
			<span class="text-caption text-foreground-secondary"
				>{recoverable_logs} events from your last session</span
			>
		</button>
	{/if}

	<!-- Pre-war self-test: confirm the capture path before the war starts. -->
	<div>
		<Button
			color="secondary"
			class="w-full"
			on:click={run_capture_test}
			disabled={test_state === 'running' || status?.npcap_installed === false || has_update}
		>
			{test_state === 'running' ? 'Testing capture…' : 'Test capture'}
		</Button>
		{#if test_state !== 'idle' && test_message}
			<p
				class="text-caption mt-1.5 {test_state === 'ok'
					? 'text-green-400'
					: test_state === 'fail'
					? 'text-gold'
					: 'text-foreground-secondary'}"
				role="status"
			>
				{test_message}
			</p>
		{/if}
	</div>

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
	<span>Made from Ikusa Logger, by <b class="text-foreground">ORACLE#7672</b></span>
	<div class="flex items-center gap-3">
		<button
			class="text-foreground-secondary hover:text-foreground transition-colors"
			on:click={() => os.open('https://discord.gg/rC4JEjEgnh')}
			aria-label="Discord"
		>
			<Icon icon={FaDiscord} />
		</button>
		<button
			class="text-foreground-secondary hover:text-foreground transition-colors"
			on:click={() => os.open('https://github.com/nots0ggy/cogm_logger')}
			aria-label="GitHub"
		>
			<Icon icon={GoMarkGithub} />
		</button>
	</div>
</div>
