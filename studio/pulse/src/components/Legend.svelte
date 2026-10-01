<script lang="ts">
  import { flowGradientCss } from "../lib/palette.ts";

  let { range }: { range: number } = $props();

  const ratioLabel = (log2: number) => {
    const ratio = 2 ** log2;
    if (Math.abs(log2) < 1e-6) return "normal";
    return ratio < 1 ? `1/${Math.round(1 / ratio)}×` : `${Math.round(ratio)}×`;
  };
  const ticks = $derived([-range, -1, 0, 1, range].map((v) => ({
    at: (v / range + 1) * 50,
    label: ratioLabel(v),
  })));
</script>

<figure class="legend" aria-label="Colour key: flow compared with normal">
  <figcaption>Flow vs normal</figcaption>
  <div class="bar" style:background={flowGradientCss()}></div>
  <div class="ticks">
    {#each ticks as tick (tick.label)}
      <span style:left="{tick.at}%">{tick.label}</span>
    {/each}
  </div>
  <p class="no-data"><span class="swatch"></span> no gauge within reach</p>
</figure>

<style>
  .legend {
    margin: 0;
  }
  figcaption {
    font: 500 10px/1 var(--mono);
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 7px;
  }
  .bar {
    height: 9px;
    border-radius: 2px;
  }
  .ticks {
    position: relative;
    height: 16px;
    font: 10px/1 var(--mono);
    color: var(--ink);
  }
  .ticks span {
    position: absolute;
    top: 5px;
    transform: translateX(-50%);
    white-space: nowrap;
  }
  .ticks span:first-child {
    transform: none;
  }
  .ticks span:last-child {
    transform: translateX(-100%);
  }
  .no-data {
    margin: 6px 0 0;
    font: 10px/1.2 var(--mono);
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 6px;
  }
  @media (max-width: 720px) {
    .no-data {
      display: none;
    }
  }
  .swatch {
    width: 16px;
    height: 3px;
    border-radius: 2px;
    background: var(--no-data);
  }
</style>
