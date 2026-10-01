<script lang="ts">
  type Props = {
    days: number;
    day: number;
    playing: boolean;
    speed: number;
    speeds: number[];
    sea: Float32Array | undefined;
    years: { year: number; day0: number; loaded: boolean }[];
    onScrub: (day: number) => void;
    onToggle: () => void;
    onSpeed: (speed: number) => void;
  };

  let { days, day, playing, speed, speeds, sea, years, onScrub, onToggle, onSpeed }: Props = $props();

  const WIDTH = 1000;
  const HEIGHT = 40;

  // Sparkline of discharge to the sea, one point per ~3 days.
  const path = $derived.by(() => {
    if (!sea || sea.length < 2) return "";
    let lo = Infinity;
    let hi = -Infinity;
    for (const v of sea) {
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }
    const step = Math.max(1, Math.floor(sea.length / 700));
    const parts: string[] = [];
    for (let i = 0; i < sea.length; i += step) {
      const x = (i / (days - 1)) * WIDTH;
      const y = HEIGHT - 3 - ((sea[i] - lo) / (hi - lo || 1)) * (HEIGHT - 8);
      parts.push(`${parts.length ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return `${parts.join("")}L${WIDTH},${HEIGHT}L0,${HEIGHT}Z`;
  });
  const cursor = $derived((day / Math.max(1, days - 1)) * 100);
</script>

<div class="timeline">
  <button type="button" class="play" onclick={onToggle} aria-label={playing ? "Pause" : "Play"}>
    {#if playing}
      <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="2" width="3.5" height="12" /><rect x="9.5" y="2" width="3.5" height="12" /></svg>
    {:else}
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2 L14 8 L4 14 Z" /></svg>
    {/if}
  </button>

  <div class="track">
    <svg class="spark" viewBox="0 0 {WIDTH} {HEIGHT}" preserveAspectRatio="none" aria-hidden="true">
      {#each years as y (y.year)}
        {#if !y.loaded}
          <rect x={(y.day0 / days) * WIDTH} y="0" width={(365 / days) * WIDTH} height={HEIGHT} class="pending" />
        {/if}
      {/each}
      <path d={path} />
    </svg>
    <div class="cursor" style:left="{cursor}%"></div>
    <div class="years">
      {#each years as y (y.year)}
        <span style:left="{(y.day0 / days) * 100}%">{y.year}</span>
      {/each}
    </div>
    <input
      type="range"
      min="0"
      max={days - 1}
      step="1"
      value={Math.floor(day)}
      aria-label="Day"
      oninput={(event) => onScrub(Number(event.currentTarget.value))}
    />
  </div>

  <div class="speed" role="group" aria-label="Playback speed">
    {#each speeds as s (s)}
      <button type="button" class:active={s === speed} onclick={() => onSpeed(s)}>{s}d/s</button>
    {/each}
  </div>
</div>

<style>
  .timeline {
    display: flex;
    align-items: center;
    gap: 14px;
  }
  .play {
    flex: none;
    width: 34px;
    height: 34px;
    border-radius: 50%;
    border: 1px solid var(--rule);
    background: var(--panel);
    display: grid;
    place-items: center;
    cursor: pointer;
    color: var(--ink);
  }
  .play svg {
    width: 13px;
    height: 13px;
    fill: currentColor;
  }
  .track {
    position: relative;
    flex: 1;
    height: 48px;
  }
  .spark {
    position: absolute;
    inset: 0 0 12px 0;
    width: 100%;
    height: 36px;
  }
  .spark path {
    fill: color-mix(in srgb, var(--accent) 22%, transparent);
    stroke: var(--accent);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
  }
  .spark .pending {
    fill: color-mix(in srgb, var(--ink) 7%, transparent);
  }
  .cursor {
    position: absolute;
    top: 0;
    bottom: 12px;
    width: 1.5px;
    background: var(--ink);
    transform: translateX(-50%);
    pointer-events: none;
  }
  .years {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 12px;
    font: 10px/1 var(--mono);
    color: var(--muted);
  }
  .years span {
    position: absolute;
    padding-left: 3px;
    border-left: 1px solid var(--rule);
  }
  input[type="range"] {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    margin: 0;
    opacity: 0;
    cursor: ew-resize;
  }
  .speed {
    flex: none;
    display: flex;
    gap: 2px;
  }
  .speed button {
    font: 10px/1 var(--mono);
    padding: 5px 6px;
    border: 1px solid transparent;
    border-radius: 3px;
    background: none;
    color: var(--muted);
    cursor: pointer;
  }
  .speed button.active {
    border-color: var(--rule);
    color: var(--ink);
    background: var(--panel);
  }
  @media (max-width: 640px) {
    .speed {
      display: none;
    }
  }
</style>
