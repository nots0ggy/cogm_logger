<script lang="ts">
	import { onDestroy } from 'svelte';
	import { os } from '@neutralinojs/lib';
	import Button from '../svelte-ui/elements/button.svelte';
	import { ModalManager } from '../svelte-ui/modal/modal-store';
	import { show_toast } from '../svelte-ui/util';
	import { get_config } from './create-config/config';

	export let logs_string: string;
	// Optional per-kill world coords for the kill-location heatmap. Only the live
	// capture path supplies these (it has the raw packet hex); a loaded .log file
	// has no hex, so it opens the modal without coords and the heatmap stays empty
	// for that upload. Content-keyed by (t, killer, victim) — see logger.svelte.
	export let coords: { t: string; k: string; v: string; x: number; z: number }[] = [];
	// Called once the event is created server-side (the war is preserved on
	// CoGM), so the caller can drop its local crash-recovery copy.
	export let on_uploaded: (() => void) | null = null;

	type UploadState = 'form' | 'uploading' | 'done';
	let state: UploadState = 'form';

	// Build date strings from local calendar parts, never via
	// new Date('YYYY-MM-DD') (which parses as UTC midnight and shows
	// yesterday for NA users during evening wars). local_date() feeds the
	// date input; local_name() is the default event name in DD.MM.YYYY.
	function local_parts() {
		const d = new Date();
		return {
			yyyy: String(d.getFullYear()),
			mm: String(d.getMonth() + 1).padStart(2, '0'),
			dd: String(d.getDate()).padStart(2, '0')
		};
	}
	function local_date() {
		const { yyyy, mm, dd } = local_parts();
		return `${yyyy}-${mm}-${dd}`;
	}
	function local_name() {
		const { yyyy, mm, dd } = local_parts();
		return `${dd}.${mm}.${yyyy}`;
	}

	// Form fields. All optional: name is the event title, prefilled with the date
	// (DD.MM.YYYY) as a default the user can overwrite; the rest carry defaults
	// too, so a war can be uploaded in one click.
	let event_name = local_name();
	let event_type: 'nodewar' | 'siege' | 'gvg' | 'other' = 'nodewar';
	let tier: 't1' | 'capped' | 'uncapped' = 'capped';
	let event_date = local_date();
	let result: '' | 'win' | 'loss' | 'draw' = '';

	// Upload/polling state
	let progress_note = 'Queued - waiting for worker...';
	let form_error = '';
	let event_url = '';
	let poll_result: Record<string, any> | null = null;
	let failed = false;
	let fail_message = '';
	let timed_out = false;

	let poll_interval: ReturnType<typeof setInterval> | null = null;
	const POLL_MAX_MS = 5 * 60 * 1000;
	let poll_start = 0;
	// Set once the component is gone (close / ESC / backdrop). The POST in
	// do_upload may still be in flight when that happens; this flag stops it
	// from starting an orphan poll interval that nothing can clear.
	let destroyed = false;

	function clear_poll() {
		if (poll_interval !== null) {
			clearInterval(poll_interval);
			poll_interval = null;
		}
	}

	onDestroy(() => {
		destroyed = true;
		clear_poll();
	});

	function tier_payload(t: string): { isCapped: boolean | null; isT1: boolean } {
		if (t === 't1') return { isCapped: true, isT1: true };
		if (t === 'capped') return { isCapped: true, isT1: false };
		return { isCapped: false, isT1: false };
	}

	// Synchronous re-entry guard. do_upload awaits get_config before it sets
	// state to 'uploading', so without this a fast double-click fires two
	// POSTs and creates two duplicate events in CoGM.
	let in_flight = false;

	async function do_upload() {
		if (in_flight) return;
		in_flight = true;
		try {
			await run_upload();
		} finally {
			in_flight = false;
		}
	}

	async function run_upload() {
		const config = await get_config();
		if (!config.cogm_token) {
			show_toast('Set your CoGM token in Settings first', 'error');
			ModalManager.close();
			return;
		}

		const base = (config.cogm_url || 'https://cogm.app').replace(/\/$/, '');
		const { isCapped, isT1 } = tier_payload(tier);
		const body: Record<string, any> = {
			// Blank name falls back to the date as the title; blank/invalid date
			// falls back to today, so a one-click upload still has both.
			name: event_name.trim() || local_name(),
			type: event_type,
			date: /^\d{4}-\d{2}-\d{2}$/.test(event_date) ? event_date : local_date(),
			isCapped,
			isT1,
			logContent: logs_string
		};
		if (result) body.result = result;
		// Only attach coords when the capture produced some — an empty array would
		// just be dropped server-side anyway, and omitting it keeps the body lean.
		if (coords.length > 0) body.coords = coords;

		state = 'uploading';
		form_error = '';
		progress_note = 'Queued - waiting for worker...';

		let post_res: Response;
		try {
			post_res = await fetch(`${base}/api/logger/upload`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${config.cogm_token}`
				},
				body: JSON.stringify(body)
			});
		} catch {
			state = 'form';
			form_error = 'Could not reach CoGM';
			show_toast('Could not reach CoGM', 'error');
			return;
		}

		if (!post_res.ok) {
			let msg = 'Upload failed';
			try {
				const data = await post_res.json();
				if (data?.error) msg = data.error;
			} catch {}
			state = 'form';
			form_error = msg;
			show_toast(msg, 'error');
			return;
		}

		// A 200 with a non-JSON body (proxy/edge HTML, empty response) would
		// throw here and leave the modal wedged on "Uploading..." with no way
		// back. The event is already created server-side, so report and stop.
		let post_data: { jobId?: string; eventUrl?: string };
		try {
			post_data = await post_res.json();
		} catch {
			state = 'form';
			form_error = 'CoGM returned an unexpected response. Check the dashboard.';
			show_toast('Upload failed', 'error');
			return;
		}
		const job_id: string | undefined = post_data.jobId;
		event_url = post_data.eventUrl || '';
		if (!job_id) {
			state = 'form';
			form_error = 'CoGM did not return a job id. Check the dashboard.';
			show_toast('Upload failed', 'error');
			return;
		}

		// The war is now created on CoGM, so the local crash-recovery copy is
		// no longer needed. Fire-and-forget; never let it affect the upload.
		try {
			on_uploaded?.();
		} catch {
			/* ignore */
		}

		// The user may have closed the modal while the POST was in flight.
		// The event is already created server-side (Open in CoGM will work
		// from the dashboard), but don't start a poll loop on a dead
		// component.
		if (destroyed) return;

		poll_start = Date.now();
		poll_interval = setInterval(async () => {
			if (Date.now() - poll_start > POLL_MAX_MS) {
				clear_poll();
				timed_out = true;
				state = 'done';
				return;
			}
			try {
				const job_res = await fetch(`${base}/api/logger/jobs/${job_id}`, {
					headers: { Authorization: `Bearer ${config.cogm_token}` }
				});
				// A 4xx is terminal: the token was revoked (401) or the event
				// was deleted from the dashboard mid-ingest (404). Stop and
				// report it instead of spinning to the 5-minute timeout and
				// then claiming it might still be processing. Keep retrying on
				// 429 (rate limit) and 5xx (transient).
				if (job_res.status >= 400 && job_res.status < 500 && job_res.status !== 429) {
					let msg = 'Upload status no longer available';
					try {
						const data = await job_res.json();
						if (data?.error) msg = data.error;
					} catch {}
					clear_poll();
					failed = true;
					fail_message = msg;
					state = 'done';
					return;
				}
				if (!job_res.ok) return;
				const job_data = await job_res.json();
				if (job_data.progressNote) progress_note = job_data.progressNote;
				if (job_data.status === 'completed' || job_data.status === 'failed') {
					clear_poll();
					poll_result = job_data.result ?? null;
					failed = job_data.status === 'failed';
					fail_message = job_data.errorMessage || 'Processing failed';
					state = 'done';
				}
			} catch {
				// transient network error; keep polling
			}
		}, 1500);
	}

	function close() {
		clear_poll();
		ModalManager.close();
	}

	function open_in_cogm() {
		if (event_url) os.open(event_url);
	}

	const type_options: { label: string; value: 'nodewar' | 'siege' | 'gvg' | 'other' }[] = [
		{ label: 'Node War', value: 'nodewar' },
		{ label: 'Siege', value: 'siege' },
		{ label: 'GvG', value: 'gvg' },
		{ label: 'Other', value: 'other' }
	];

	const tier_options: { label: string; value: 't1' | 'capped' | 'uncapped' }[] = [
		{ label: 'T1', value: 't1' },
		{ label: 'Capped', value: 'capped' },
		{ label: 'Uncapped', value: 'uncapped' }
	];

	// Everything is optional so a war can be uploaded in one click. Blanks get
	// sensible defaults at send time (name -> date, date -> today), so the
	// button stays enabled.
	$: upload_disabled = false;
</script>

<div class="w-80 flex flex-col gap-4">
	{#if state === 'form'}
		<h3 class="font-bold text-foreground">Upload to CoGM</h3>
		<p class="text-caption -mt-2">All optional. Upload as-is for a quick one.</p>

		<div>
			<p class="text-sm text-foreground mb-1">
				Event name <span class="text-foreground-secondary">(optional)</span>
			</p>
			<input
				class="w-full bg-background border border-gray-700 rounded px-2 py-1.5 text-foreground text-sm"
				placeholder="Title for this war, defaults to the date"
				bind:value={event_name}
			/>
		</div>

		<div>
			<p class="text-sm text-foreground mb-1">Type</p>
			<div class="flex gap-1">
				{#each type_options as opt}
					<button
						class="flex-1 text-xs px-2 py-1.5 rounded border transition-colors
							{event_type === opt.value
								? 'bg-gold-300 border-gold text-black font-medium'
								: 'bg-background border-gray-700 text-foreground-secondary hover:border-foreground'}"
						on:click={() => (event_type = opt.value)}
					>
						{opt.label}
					</button>
				{/each}
			</div>
		</div>

		<div>
			<p class="text-sm text-foreground mb-1">Tier</p>
			<div class="flex gap-1">
				{#each tier_options as opt}
					<button
						class="flex-1 text-xs px-2 py-1.5 rounded border transition-colors
							{tier === opt.value
								? 'bg-gold-300 border-gold text-black font-medium'
								: 'bg-background border-gray-700 text-foreground-secondary hover:border-foreground'}"
						on:click={() => (tier = opt.value)}
					>
						{opt.label}
					</button>
				{/each}
			</div>
		</div>

		<div>
			<p class="text-sm text-foreground mb-1">Date</p>
			<input
				type="date"
				class="w-full bg-background border border-gray-700 rounded px-2 py-1.5 text-foreground text-sm"
				bind:value={event_date}
			/>
		</div>

		<div>
			<p class="text-sm text-foreground mb-1">Result</p>
			<select
				class="w-full bg-background border border-gray-700 rounded px-2 py-1.5 text-foreground text-sm"
				bind:value={result}
			>
				<option value="">Unknown</option>
				<option value="win">Win</option>
				<option value="loss">Loss</option>
				<option value="draw">Draw</option>
			</select>
		</div>

		{#if form_error}
			<p class="text-xs text-status-error">{form_error}</p>
		{/if}

		<div class="flex gap-2 justify-end pt-1">
			<Button color="secondary" on:click={close}>Cancel</Button>
			<Button disabled={upload_disabled} on:click={do_upload}>Upload</Button>
		</div>

	{:else if state === 'uploading'}
		<h3 class="font-bold text-foreground">Uploading...</h3>
		<div class="h-1 w-full rounded-full bg-gold/30 animate-pulse-rec"></div>
		<p class="text-sm text-foreground-secondary">{progress_note}</p>
		<p class="text-caption">
			The event is already created in CoGM. Closing here just stops the progress view. Check the
			CoGM dashboard for the result.
		</p>
		<div class="flex justify-end pt-1">
			<Button color="secondary" on:click={close}>Close</Button>
		</div>

	{:else if state === 'done'}
		{#if timed_out}
			<h3 class="font-bold text-foreground">Still processing</h3>
			<p class="text-sm text-foreground-secondary">
				The upload may still be processing. Check the CoGM dashboard for results.
			</p>
		{:else if failed}
			<h3 class="font-bold text-status-error">Upload failed</h3>
			<p class="text-sm text-status-error">{fail_message}</p>
		{:else}
			<h3 class="font-bold text-status-ok">Upload complete</h3>
			{#if poll_result}
				{#if poll_result.parsed !== undefined && poll_result.guildsFound !== undefined}
					<p class="text-sm text-foreground-secondary">
						{poll_result.parsed} kill events parsed across {poll_result.guildsFound.length} guild{poll_result.guildsFound.length !== 1 ? 's' : ''}
					</p>
				{/if}
				{#if poll_result.participants !== undefined}
					<p class="text-sm text-foreground-secondary">{poll_result.participants} participants</p>
				{/if}
			{/if}
		{/if}
		<div class="flex gap-2 justify-end pt-1">
			{#if failed}
				<Button
					color="secondary"
					on:click={() => {
						failed = false;
						timed_out = false;
						fail_message = '';
						state = 'form';
					}}>Try again</Button
				>
			{/if}
			{#if !timed_out && !failed && event_url}
				<Button on:click={open_in_cogm}>Open in CoGM</Button>
			{/if}
			<Button color="secondary" on:click={close}>Close</Button>
		</div>
	{/if}
</div>
