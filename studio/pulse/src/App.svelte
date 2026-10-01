<script lang="ts">
  import Legend from "./components/Legend.svelte";
  import Timeline from "./components/Timeline.svelte";
  import { codeToLog2 } from "./lib/flow.ts";
  import {
    DEFAULT_EXAGGERATION,
    type GaugePoints,
    type Hover,
    MapScene,
    type MapMeta,
    type ScreenLabel,
  } from "./lib/map-scene.ts";
  import { type MarimoTable, observeMarimoValue } from "./lib/marimo-value.ts";
  import { decodePayload } from "./lib/payload.ts";

  type Summary = { sea: Float32Array; high: Float32Array; low: Float32Array; reporting: Int32Array };
  type Status = "waiting" | "loading" | "ready" | "error";

  const SPEEDS = [7, 14, 30, 60];

  let meta = $state<MapMeta>();
  let summary = $state<Summary>();
  let gauges = $state<GaugePoints>();
  let day = $state(0);
  let playing = $state(true);
  let speed = $state(14);
  let hover = $state<Hover | null>(null);
  let labels = $state<ScreenLabel[]>([]);
  let status = $state<Record<string, Status>>({});
  let loadedYears = $state<number[]>([]);
  let panelWidth = $state(0);
  let panelHeight = $state(0);
  let footerHeight = $state(0);
  let viewportWidth = $state(0);
  let exaggeration = $state(DEFAULT_EXAGGERATION);
  let cloudsOn = $state(true);

  let scene = $state.raw<MapScene>();
  let resolveScene: (scene: MapScene) => void = () => {};
  const sceneReady = new Promise<MapScene>((resolve) => (resolveScene = resolve));
  let resolveMeta: () => void = () => {};
  const metaReady = new Promise<void>((resolve) => (resolveMeta = resolve));

  const mountScene = (node: HTMLElement) => {
    const created = new MapScene(node);
    created.onTick = (d) => (day = d);
    created.onHover = (h) => (hover = h);
    created.onLabels = (l) => (labels = l);
    scene = created;
    resolveScene(created);
    return { destroy: () => created.dispose() };
  };

  /** Observe one projected notebook value and hand it to the scene once it can use it. */
  const receive = <T>(key: string, apply: (scene: MapScene, value: T) => Promise<void> | void, needsMeta = true) => ({
    onValue: async (value: T) => {
      status[key] = "loading";
      try {
        const target = await sceneReady;
        if (needsMeta) await metaReady;
        await apply(target, value);
        status[key] = "ready";
      } catch (error) {
        console.error(`River Pulse could not load ${key}`, error);
        status[key] = "error";
      }
    },
    onError: () => {
      status[key] = "error";
    },
  });

  const onMeta = receive<MapMeta>("river_pulse_map_meta", (target, value) => {
    meta = value;
    target.setMeta(value);
    resolveMeta();
  }, false);
  const onWater = receive<MarimoTable>("river_pulse_water", async (target, table) => {
    target.setWater(await decodePayload(table));
  });
  const onReliefBand = (band: number) => receive<MarimoTable>(`river_pulse_relief_${band}`, async (target, table) => {
    target.addReliefBand(await decodePayload(table));
  });
  const onRivers = (key: string) => receive<MarimoTable>(key, async (target, table) => {
    target.setRivers(await decodePayload(table));
  });
  // One chunk per animated year, oldest first; each chunk's meta names its year.
  const onFlowYear = (k: number) => receive<MarimoTable>(`river_pulse_year_${k}`, async (target, table) => {
    target.addFlowYear(await decodePayload(table));
    loadedYears = [...target.flow!.loadedYears];
  });
  const onSummary = receive<MarimoTable>("river_pulse_daily", (_, table) => {
    summary = {
      sea: table.getChild("sea_cms")!.toArray() as Float32Array,
      high: table.getChild("high_share")!.toArray() as Float32Array,
      low: table.getChild("low_share")!.toArray() as Float32Array,
      reporting: table.getChild("reporting")!.toArray() as Int32Array,
    };
  }, false);
  const onGauges = receive<MarimoTable>("river_pulse_gauge_points", (target, table) => {
    const points: GaugePoints = {
      slot: table.getChild("slot")!.toArray() as Int32Array,
      site: Array.from(table.getChild("site_no")!.toArray() as ArrayLike<string>),
      name: Array.from(table.getChild("name")!.toArray() as ArrayLike<string>),
      x: table.getChild("x_km")!.toArray() as Float32Array,
      y: table.getChild("y_km")!.toArray() as Float32Array,
      meanCms: table.getChild("mean_cms")!.toArray() as Float32Array,
    };
    gauges = points;
    target.setGauges(points);
  });

  const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
  const percent = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 0 });
  const formatFlow = (cms: number) =>
    cms >= 100 ? number.format(cms) : cms.toPrecision(2).replace(/\.?0+$/, "");

  const dayIndex = $derived(Math.floor(day));
  const dateLabel = $derived(
    meta ? dateFormat.format(new Date(Date.parse(`${meta.date_start}T00:00:00Z`) + dayIndex * 86_400_000)) : "",
  );
  const seaNow = $derived.by(() => {
    if (!summary) return undefined;
    const i = Math.min(summary.sea.length - 1, dayIndex);
    const j = Math.min(summary.sea.length - 1, i + 1);
    return summary.sea[i] + (summary.sea[j] - summary.sea[i]) * (day - dayIndex);
  });
  const years = $derived(
    (meta?.years ?? []).map(({ year }) => ({
      year,
      day0: Math.max(0, Math.round((Date.UTC(year, 0, 1) - Date.parse(`${meta!.date_start}T00:00:00Z`)) / 86_400_000)),
      loaded: loadedYears.includes(year),
    })),
  );
  const loadingText = $derived.by(() => {
    const pending = Object.entries(status).filter(([, s]) => s === "loading" || s === "waiting").map(([k]) => k);
    const failed = Object.entries(status).filter(([, s]) => s === "error").map(([k]) => k);
    if (failed.length) return `Could not load ${failed.join(", ")}`;
    if (pending.length) return `Loading ${pending.length} layer${pending.length > 1 ? "s" : ""}…`;
    return "";
  });

  const hoverInfo = $derived.by(() => {
    if (!hover || !gauges || !meta || !scene?.flow) return undefined;
    const slot = gauges.slot[hover.index];
    const code = scene.flow.code(slot, dayIndex);
    const log2 = code > 0 ? codeToLog2(code, meta.flow_levels, meta.log2_range) : undefined;
    let reading = "no reading today";
    if (log2 !== undefined) {
      const ratio = 2 ** log2;
      const edge = Math.abs(log2) >= meta.log2_range - 1e-6 ? (log2 > 0 ? "≥ " : "≤ ") : "";
      reading = `${edge}${ratio >= 1 ? ratio.toFixed(1) : ratio.toFixed(2)}× normal`;
    }
    return {
      name: titleCase(gauges.name[hover.index]),
      site: gauges.site[hover.index],
      reading,
      mean: gauges.meanCms[hover.index],
    };
  });

  // Keep the map clear of the summary panel and the timeline.
  $effect(() => {
    if (!scene || !panelWidth || !footerHeight) return;
    const wide = viewportWidth > 720;
    scene.setSafeArea({
      left: wide ? panelWidth + 30 : 8,
      top: wide ? 12 : panelHeight + 18,
      right: 12,
      bottom: footerHeight + 18,
    });
  });

  // USGS names are often all caps; keep a trailing state code upper-case.
  const titleCase = (name: string) =>
    name
      .toLowerCase()
      .replace(/\b[a-z]/g, (c) => c.toUpperCase())
      .replace(/, ([A-Z][a-z])\.?$/, (_, code: string) => `, ${code.toUpperCase()}`);

  $effect(() => {
    scene?.setExaggeration(exaggeration);
  });
  $effect(() => {
    scene?.setClouds(cloudsOn);
  });

  const togglePlay = () => {
    playing = !playing;
    if (scene) scene.playing = playing;
  };
  const scrub = (d: number) => scene?.setDay(d);
  const setSpeed = (s: number) => {
    speed = s;
    if (scene) scene.daysPerSecond = s;
  };
  const onKey = (event: KeyboardEvent) => {
    // Shortcuts apply on the map and the day slider; other controls keep their keys.
    const target = event.composedPath()[0];
    const daySlider = target instanceof HTMLInputElement && target.type === "range" && !!target.closest(".timeline");
    if (!daySlider && target instanceof Element &&
        target.closest("input, button, select, textarea, a, [role=radio], [contenteditable], marimo-cell")) return;
    if (event.code === "Space") {
      event.preventDefault();
      togglePlay();
    } else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      const step = (event.shiftKey ? 30 : 1) * (event.key === "ArrowRight" ? 1 : -1);
      scrub(Math.min((meta?.days ?? 1) - 1, Math.max(0, Math.floor(day) + step)));
    } else if (event.key === "r") {
      scene?.resetView();
    }
  };
</script>

<svelte:window onkeydown={onKey} bind:innerWidth={viewportWidth} />

<div class="pulse">
  <div
    class="stage"
    use:mountScene
    aria-busy={loadingText.startsWith("Loading")}
    role="img"
    aria-label="3D map of United States rivers coloured by flow compared with normal"
    data-marimo-lens-label="River flow map"
    data-marimo-lens-detail="Terrain, river network and daily gauge flow drawn with three.js"
    data-marimo-lens-inputs="map-meta terrain-water terrain-relief-0 terrain-relief-1 terrain-relief-2 terrain-relief-3 rivers-major rivers-minor year-0 year-1 year-2 year-3 year-4 year-5 gauge-points"
    data-marimo-lens-render-source={JSON.stringify({ path: "src/lib/map-scene.ts", symbol: "MapScene" })}
  ></div>

  {#each labels as label (label.key)}
    <span class="inset-label" style:left="{label.left}px" style:top="{label.top}px">{label.label}</span>
  {/each}

  {#if hover && hoverInfo}
    <div class="gauge-dot" style:left="{hover.left}px" style:top="{hover.top}px"></div>
    <div class="tooltip" style:left="{hover.left}px" style:top="{hover.top}px" role="status">
      <strong>{hoverInfo.name}</strong>
      <span class="reading">{hoverInfo.reading}</span>
      <span class="detail">USGS {hoverInfo.site} · mean {formatFlow(hoverInfo.mean)} m³/s</span>
    </div>
  {/if}

  <section class="panel" aria-label="River Pulse summary" bind:offsetWidth={panelWidth} bind:offsetHeight={panelHeight}>
    <header>
      <h1>{meta?.title ?? "River Pulse"}</h1>
      <p class="kicker">Every gauged river, every day since {meta ? dateFormat.format(new Date(`${meta.date_start}T00:00:00Z`)).replace(/^\d+ /, "") : "…"}</p>
    </header>

    <div class="now" data-marimo-lens-label="Selected day" data-marimo-lens-inputs="map-meta daily-summary">
      <p class="date">{dateLabel}</p>
      <p class="label">Discharge to the sea</p>
      <p class="sea">
        {seaNow === undefined ? "—" : number.format(seaNow)}<span class="unit"> m³/s</span>
      </p>
      <p class="note">
        {meta ? `${number.format(meta.counts.sea_outlets)} outlets combined` : ""}
        {#if summary}
          · {percent.format(summary.high[Math.min(summary.high.length - 1, dayIndex)])} of gauges above 2×,
          {percent.format(summary.low[Math.min(summary.low.length - 1, dayIndex)])} below ½×
        {/if}
      </p>
    </div>

    <Legend range={meta?.log2_range ?? 3} />

    <div class="baseline">
      <marimo-cell name="_river_pulse_baseline"></marimo-cell>
    </div>

    <div class="view-controls" role="group" aria-label="View">
      <label class="relief">
        <span>Relief ×{exaggeration}</span>
        <input type="range" min="4" max="50" step="1" bind:value={exaggeration} />
      </label>
      <label class="clouds">
        <input type="checkbox" bind:checked={cloudsOn} />
        <span>Clouds</span>
      </label>
    </div>

    {#if loadingText}
      <p class="loading" role="status">{loadingText}</p>
    {/if}
  </section>

  <footer class="bottom" bind:offsetHeight={footerHeight}>
    {#if meta}
      <Timeline
        days={meta.days}
        {day}
        {playing}
        {speed}
        speeds={SPEEDS}
        sea={summary?.sea}
        {years}
        onScrub={scrub}
        onToggle={togglePlay}
        onSpeed={setSpeed}
      />
    {/if}
    <p class="credits">
      {#each meta?.sources ?? [] as line (line)}<span>{line}</span>{/each}
      <span>Drag to pan · right-drag to tilt · scroll to zoom · space to play · R to reset</span>
    </p>
  </footer>
</div>

<!-- Notebook values this view consumes. -->
<span id="map-meta" hidden mo-value="river_pulse_map_meta" use:observeMarimoValue={onMeta}></span>
<span id="terrain-water" hidden mo-value="river_pulse_water" use:observeMarimoValue={onWater}></span>
<span id="terrain-relief-0" hidden mo-value="river_pulse_relief_0" use:observeMarimoValue={onReliefBand(0)}></span>
<span id="terrain-relief-1" hidden mo-value="river_pulse_relief_1" use:observeMarimoValue={onReliefBand(1)}></span>
<span id="terrain-relief-2" hidden mo-value="river_pulse_relief_2" use:observeMarimoValue={onReliefBand(2)}></span>
<span id="terrain-relief-3" hidden mo-value="river_pulse_relief_3" use:observeMarimoValue={onReliefBand(3)}></span>
<span id="rivers-major" hidden mo-value="river_pulse_rivers_major" use:observeMarimoValue={onRivers("river_pulse_rivers_major")}></span>
<span id="rivers-minor" hidden mo-value="river_pulse_rivers_minor" use:observeMarimoValue={onRivers("river_pulse_rivers_minor")}></span>
<span id="year-0" hidden mo-value="river_pulse_year_0" use:observeMarimoValue={onFlowYear(0)}></span>
<span id="year-1" hidden mo-value="river_pulse_year_1" use:observeMarimoValue={onFlowYear(1)}></span>
<span id="year-2" hidden mo-value="river_pulse_year_2" use:observeMarimoValue={onFlowYear(2)}></span>
<span id="year-3" hidden mo-value="river_pulse_year_3" use:observeMarimoValue={onFlowYear(3)}></span>
<span id="year-4" hidden mo-value="river_pulse_year_4" use:observeMarimoValue={onFlowYear(4)}></span>
<span id="year-5" hidden mo-value="river_pulse_year_5" use:observeMarimoValue={onFlowYear(5)}></span>
<span id="daily-summary" hidden mo-value="river_pulse_daily" use:observeMarimoValue={onSummary}></span>
<span id="gauge-points" hidden mo-value="river_pulse_gauge_points" use:observeMarimoValue={onGauges}></span>
