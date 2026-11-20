// The new-order chime, synthesized so there is no audio file to ship.
// Browsers only allow sound after a tap, so `unlockSound` runs on the first one.

let context: AudioContext | null = null;

export function unlockSound(): void {
  if (typeof AudioContext === "undefined") return;
  context ??= new AudioContext();
  if (context.state === "suspended") void context.resume();
}

export function soundReady(): boolean {
  return context !== null && context.state === "running";
}

export function chime(): void {
  if (context === null || context.state !== "running") return;
  const start = context.currentTime;
  for (const [offset, frequency] of [
    [0, 880],
    [0.18, 1175],
    [0.36, 1568],
  ]) {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, start + offset);
    gain.gain.exponentialRampToValueAtTime(0.6, start + offset + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + 0.4);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start(start + offset);
    oscillator.stop(start + offset + 0.45);
  }
}
