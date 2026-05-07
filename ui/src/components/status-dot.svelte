<script lang="ts">
	export let state: 'idle' | 'recording' | 'reconnecting' | 'ok' | 'warn' | 'error' = 'idle';
	export let pulse: boolean = false;
	export let size: number = 8;

	const color_for = {
		idle: 'bg-status-idle',
		recording: 'bg-status-recording',
		reconnecting: 'bg-status-warn',
		ok: 'bg-status-ok',
		warn: 'bg-status-warn',
		error: 'bg-status-error'
	} as const;
</script>

<span
	class="inline-block rounded-full {color_for[state]} {pulse ? 'animate-pulse-rec' : ''}"
	style="width: {size}px; height: {size}px;"
	aria-hidden="true"
/>
