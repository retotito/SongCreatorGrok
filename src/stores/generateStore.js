import { writable } from 'svelte/store';

export const currentStep = writable('upload'); // upload, processing, result, correction
export const progress = writable(0);
export const status = writable('');
export const result = writable(null);
export const correctedVocal = writable(null);

// Form data
export const file = writable(null);
export const youtubeUrl = writable('');
export const lyrics = writable('');
export const artist = writable('');
export const title = writable('');
export const referenceVocal = writable(null);
export const voiceType = writable('solo');
export const language = writable('en');
export const skipDemucs = writable(false);
export const skipCrepe = writable(false);
export const skipWhisper = writable(false);