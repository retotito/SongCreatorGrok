import { writable } from 'svelte/store';

export const trainingData = writable([]);
export const selectedItem = writable(null);
export const isLoading = writable(false);

export async function loadTrainingData() {
  isLoading.set(true);
  try {
    const response = await fetch('http://localhost:8001/list_training_data');
    if (response.ok) {
      const data = await response.json();
      trainingData.set(data.data);
    }
  } catch (error) {
    console.error('Load error:', error);
  } finally {
    isLoading.set(false);
  }
}

// Optional: Persist to localStorage for offline support
trainingData.subscribe(value => {
  if (typeof window !== 'undefined') {
    localStorage.setItem('trainingData', JSON.stringify(value));
  }
});

// Load from localStorage on init
if (typeof window !== 'undefined') {
  const stored = localStorage.getItem('trainingData');
  if (stored) {
    trainingData.set(JSON.parse(stored));
  }
}