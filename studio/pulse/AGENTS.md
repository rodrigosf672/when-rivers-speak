# Svelte starter instructions

Follow the `marimo-studio` skill for notebook ownership, projection selection,
view lifecycle, and validation. This file covers the Svelte project supplied by
this starter.

## Project intent

Keep this section current with the user's audience, analytical goal, concrete
project details, aesthetic direction, interaction priorities, and approved
library or framework preferences. Preserve decisions that should guide later
agents.

**River Pulse** is a full-screen three.js map of every gauged US river, animated
day by day from January 2021, modelled on "The UK's River Pulse" (serif title,
monospaced small caps, a big date, "discharge to the sea", a play/scrub
timeline, rivers brown when low and blue when high). The rendering aims for
game-like faux-realism, "between Civilization 6 and GTA 6": parchment land
(the user chose to keep it over satellite colour) under a warm sun and cool sky
fill, tall relief with soft cast shadows, stylized water, drifting clouds, sky
and haze. Coverage is the lower 48 plus Alaska and Hawaii as insets in one
shared Albers (EPSG:5070) plane, in kilometres.

- The notebook is the repo's `app.py`; this view is one of its Studio views
  (config in `pyproject.toml`, default view `original` at `/`, this one at
  `/pulse/`). Its "River Pulse" section at the end of `app.py` and the
  `rivers.pulse` package own all data: relief, water depth and shore distance,
  the river network, gauge snapping and spread, colour codes, sea discharge,
  counts and source lines. The view owns presentation: lighting, shadows, water
  shading, clouds, sky, relief exaggeration. Keep numbers and claims out of
  source. Edit `app.py` by hand (not through the notebook editor) to keep
  upstream diffs small; marimo re-saves the whole file when cells are edited
  in the editor.
- Studio sends each defining cell's values in one response of at most 1 MB.
  Every large payload is therefore its own `app.py` cell and its own `mo-value`
  host: `river_pulse_water` (with `river_pulse_relief_3`),
  `river_pulse_relief_0` … `river_pulse_relief_2`, `river_pulse_rivers_major`,
  `river_pulse_rivers_minor`, `river_pulse_year_0` … `river_pulse_year_5`,
  `river_pulse_gauge_points`, and `river_pulse_map_meta` with
  `river_pulse_daily`. The baseline radio is the cell `_river_pulse_baseline`,
  embedded with `<marimo-cell>`. The map animates the last
  `rivers.pulse.YEAR_CELLS` (6) calendar years of discharge. Year cells are
  positional, oldest first; each chunk's meta carries its calendar year and
  `meta.years` maps years to cells, so new data needs no new cells. A spare
  cell (less than six years of data) sends an empty chunk. Changing the count
  means changing `YEAR_CELLS`, the `_river_pulse_year_k` cells in `app.py` and
  the hosts in `App.svelte` together.
- Packed tables come from `rivers.pulse.packing.pack_sections`: rows of name, NumPy
  dtype, shape, codec (`zlib` or `raw`) and a binary blob; `src/lib/payload.ts`
  decodes them with `DecompressionStream("deflate")`. River vertices are
  quantized and delta-coded per reach; flow codes, relief and depth rows are
  delta-coded along their last axis (browsers cannot read 16-bit PNG).
- Flow codes are 1..64 over log2(flow / normal) in [-3, 3]; 0 means no reading.
  The `_river_pulse_baseline` radio (own median vs time-of-year normal) is
  projected with `<marimo-cell>` and re-sends the flow chunks.
- **Data first.** The user rejected a version where realism hid the rivers
  (dark shadows, brown rivers on brown slopes, pale blue on sunlit peaks,
  clouds over the data). Keep these rules:
  - Land stays light and low-chroma: hillshade brightness is remapped into
    `RELIEF_FLOOR`..`RELIEF_FLOOR + RELIEF_SPAN` of albedo (terrain.ts), form
    comes from warm sun vs cool sky colour rather than dark shadows, and cast
    shadows are baked shorter and softer (bake.ts).
  - Every `FLOW_STOPS` colour is darker and more saturated than any land, lake
    or sea colour; the near-normal crossing from orange to blue is narrow so big
    main stems never read grey. Satellite land colour was evaluated and
    rejected (it collides with the ramp in every region).
  - Rivers keep their data colour when zoomed in: at most ~15% sky reflection,
    sparse sun glints (bloom), half-strength haze, no cloud dimming.
  - Two rim passes share the river geometry: a MAX-blended light rim and a
    MIN-blended dark rim, drawn only on rugged ground (local relief around the
    reach), thinner on tributaries, fading in with zoom. Each can only push the
    ground away from the river's value; the plains get no outline.
  - River screen width is capped by the default framing's depth, so far-off
    rivers in tilted views thin out.
  - Clouds are a thin veil over sea and neighbours, thinned over US land, and
    vanish (with their shadows and most haze) between zoom 1.15 and 1.8.
- Rendering modules: `glsl.ts` (shared sun, sky, cloud field, haze, noise and
  one atmosphere uniform set every material shares); `bake.ts` (GPU pass that
  bakes soft sun shadows and ambient occlusion from the relief whenever relief
  or exaggeration changes); `terrain.ts` (relief bands, water textures, land
  and water shading, open sea to the horizon); `sky.ts` (sky dome, three thin
  cloud layers); `rivers.ts` (one instanced screen-space capsule per segment,
  coloured from an R8 day × slot texture in `flow.ts`, depth-tested with a
  small bias, streaks and close-up ripples travelling toward the sea along
  `dist_dn`, plus the two rim variants via the `CASING` define);
  `map-scene.ts` (renderer, controls, adaptive near/far, per-frame zoom fades,
  bloom via EffectComposer). `palette.ts` is the single source for shader and
  legend colours. Colour management is off: hex values go straight to the
  screen.
- Other countries' land is flattened (`OTHER_RELIEF`) and faded near the map's
  edges; shelves ease to open-ocean depth near the edges so the outer sea joins
  without a seam. The sun sits in the north-west so relief reads right way up.
- View-only controls: relief exaggeration (default ×16) and clouds on/off.
- `MapScene.setSafeArea` keeps the map clear of the panel and timeline; the
  camera re-fits until the user moves it.
- Dependencies: `three` (and `@types/three`) from npm, pinned.

## Use the supplied Studio integration

The target array in the `{#each ... as name}` block in `src/App.svelte` starts
with each enabled notebook cell that may display output, including literal
Markdown, in document order. Edit that array and its surrounding markup to keep,
reorder, group, or replace targets as the component design develops.

- `src/app.d.ts` adds Studio attributes to Svelte's element types.
- `src/lib/marimo-value.ts` supplies the `observeMarimoValue` action. Attach it
  to an explicit `mo-value` host so Studio can inspect and authorize the
  selector.

```svelte
<script lang="ts">
  import {
    type MarimoTable,
    observeMarimoValue,
  } from "./lib/marimo-value.ts";

  type Row = { id: string; label: string };

  let rows = $state<MarimoTable<Row>>();
</script>

<span
  id="rows-data"
  hidden
  mo-value="rows"
  use:observeMarimoValue={{
    onValue: (value: MarimoTable<Row>) => {
      rows = value;
    },
  }}
></span>

<output data-marimo-lens-inputs="rows-data">{rows?.numRows ?? 0}</output>
```

Use the supplied declaration and action as the integration contract. Keep
page-specific value handling in the component that consumes it.

Eager dataframes arrive as a shared `MarimoTable` backed by Flechette. Use
[https://github.com/uwdata/flechette](https://github.com/uwdata/flechette) as
the table API reference. Keep data columnar with `getChild()`, `select()`, and
`toColumns()`. Call `toArray()` when a component needs row objects.

Treat the table as immutable. `getMarimoDataSource(table)` returns its codec,
fingerprint, and shared Arrow IPC bytes. Copy the bytes before mutating them.

## Add dependencies

Use the Deno supplied by `marimo-studio[deno]` in the notebook's Python
environment so dependency updates and Studio builds use the same version.

Run from the view root. Use `--package-json` so Vite resolves application
dependencies through `package.json` and the installed `node_modules` tree:

```console
uv run -- deno add --package-json --frozen=false --save-exact \
  npm:d3@7 \
  npm:@observablehq/plot@0.6 \
  npm:arquero@8 \
  jsr:@std/csv@1
```

Import the package names or explicit alias written to `package.json`:

```ts
import * as d3 from "d3";
import * as Plot from "@observablehq/plot";
import * as aq from "arquero";
import { parse as parseCsv } from "@std/csv";
```

Choose the packages the page actually needs. D3 and Observable Plot render
visualizations, Arquero transforms tabular data, and `@std/csv` parses CSV
through JSR. Deno also accepts registry package subpaths and explicit local
aliases when a package's documentation calls for them.

Keep `minimumDependencyAge` and the frozen lockfile policy intact. Commit
`package.json` and `deno.lock` after adding or changing an application
dependency. Use `--frozen=false` for that intentional update. Normal builds
remain frozen.

## Work within the Svelte project

- Use Svelte 5 runes such as `$state` and `$derived` for local browser state.
- Keep the application entry in `src/main.ts` and compose the page from
  `src/App.svelte` or focused components under `src/`.
- Keep page styles in `src/style.css` or component-owned `<style>` blocks.
- Put static files under `public/` and reference them from the page. Vite copies
  that directory into the built artifact.
- Use the versions pinned by `package.json`, `deno.json`, and `deno.lock`.
  TypeScript source imports may retain their `.ts` suffix.

Studio's Svelte build runs `svelte-check` before Vite. Treat that build as the
acceptance boundary for actions, runes, imports, and packaged assets.

## Link custom results to notebook inputs

Keep projection hosts explicit in authored source. Custom regions need every
kernel input, a readable label, and a rendering-source reference such as
`{"path":"src/App.svelte"}`. Keep these attributes on authored elements outside
native output subtrees. Follow the installed Studio skill's
`references/projections.md` for the shared contract:

```python
import marimo_studio

print(marimo_studio.agent.skill().file("references/projections.md").read_text())
```

## Maintain project ignore rules

You own this view project's `.gitignore`. When adding libraries, extensions, or
build tools, ignore their generated files, caches, local configuration, and
secrets. Keep authored source, dependency manifests, and lockfiles tracked.
Studio supplies workspace rules for its own artifacts and locks. Check
`git status --short --ignored` after running new tooling and update the view's
ignore rules before committing.
