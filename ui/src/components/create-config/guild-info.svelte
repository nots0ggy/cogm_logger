<script lang="ts">
	import { calculate_kd } from './config';

	export let name: string;
	export let players: string[];
	export let kills = 0;
	export let deaths = 0;

	$: kd = calculate_kd(kills, deaths);
</script>

<div class="rounded-lg border border-gray-700 bg-background-secondary p-2.5 flex flex-col gap-1.5 min-w-0">
	<div>
		<p class="text-sm font-semibold text-foreground truncate">{name}</p>
		<p class="text-caption">{players.length} players</p>
	</div>
	<p class="text-xs tabular-nums">
		<span class="text-status-ok">K {kills}</span>
		<span class="text-foreground-secondary mx-1">·</span>
		<span class="text-status-error">D {deaths}</span>
		<span class="text-foreground-secondary mx-1">·</span>
		<span class="text-gold font-semibold">{kd}</span>
	</p>
	<ul class="text-xs h-28 overflow-y-auto space-y-0.5">
		{#each players as player}
			<li class="truncate text-foreground-secondary" title={player}>{player}</li>
		{/each}
	</ul>
</div>
