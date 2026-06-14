<script lang="ts">
	export let selected_value: number|string = 0;
	export let options: (string | number)[] = [];
	export let on_change: (e: Event) => void = (e) => {};

</script>

<select
	bind:value={selected_value}
	class="!w-32 p-1 rounded-lg border border-gray-700 bg-background text-foreground truncate"
	on:change={on_change}
>
	{#each options as option, i}
		<!-- Dark options so the dropdown popup matches the theme instead of
		     flashing a light-gray panel. Also gives the select an explicit dark
		     surface now that the global black input background is gone. -->
		<option value={i} class="bg-background text-foreground">
			{option}
		</option>
	{/each}
</select>
