import { writable } from 'svelte/store';

export type RecordingState = 'idle' | 'recording' | 'reconnecting' | 'error';

export const recording_state = writable<RecordingState>('idle');
export const recording_started_at = writable<number | null>(null);
export const packets_seen = writable<number>(0);
