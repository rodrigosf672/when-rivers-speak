import * as THREE from "three";
import { MapControls } from "three/addons/controls/MapControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

import { LightingBaker } from "./bake.ts";
import { FlowStore, type FlowYearMeta } from "./flow.ts";
import { type AtmosphereUniforms, createAtmosphere } from "./glsl.ts";
import { FRAME, flowRamp, glslColor, HALO, NO_DATA, RIM_DARK } from "./palette.ts";
import type { Payload } from "./payload.ts";
import {
  createRiverMaterial,
  createRiverMesh,
  decodeRivers,
  type RiverMeta,
  type RiverUniforms,
} from "./rivers.ts";
import { type CloudLayers, createClouds, createSky } from "./sky.ts";
import {
  createOuterSea,
  createTerrain,
  decodeWater,
  type Extent,
  ReliefField,
  type ReliefBandMeta,
  type Terrain,
  type WaterField,
} from "./terrain.ts";

// Colours are authored as display sRGB and written straight to the screen.
THREE.ColorManagement.enabled = false;

export type MapMeta = {
  title: string;
  extent_km: Extent;
  insets: { key: string; label: string; frame_km: Extent }[];
  date_start: string;
  date_end: string;
  days: number;
  years: { year: number; cell: string }[];
  flow_levels: number;
  log2_range: number;
  baseline: string;
  baseline_label: string;
  counts: { reaches: number; gauges: number; gauges_total: number; sea_outlets: number; river_km: number };
  sea_gauged_share: number;
  sources: string[];
};

export type GaugePoints = {
  slot: Int32Array;
  site: string[];
  name: string[];
  x: Float32Array;
  y: Float32Array;
  meanCms: Float32Array;
};

export type Hover = { left: number; top: number; index: number };
export type ScreenLabel = { key: string; label: string; left: number; top: number };

export const DEFAULT_EXAGGERATION = 16;
const TILT = 0.34;
const GRID_KM = 60;
// Cloud deck at about 5.5 km, exaggerated like the relief below it.
const CLOUD_BASE_KM = 5.5;

export class MapScene {
  day = 0;
  playing = true;
  daysPerSecond = 14;
  flow: FlowStore | undefined;
  gauges: GaugePoints | undefined;
  onTick: ((day: number) => void) | undefined;
  onHover: ((hover: Hover | null) => void) | undefined;
  onLabels: ((labels: ScreenLabel[]) => void) | undefined;

  private readonly container: HTMLElement;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly composer: EffectComposer;
  private readonly bloom: UnrealBloomPass;
  private readonly scene = new THREE.Scene();
  private readonly camera: THREE.PerspectiveCamera;
  private readonly controls: MapControls;
  private readonly timer = new THREE.Timer();
  private readonly resizeObserver: ResizeObserver;
  private readonly atmosphere: AtmosphereUniforms = createAtmosphere();
  private readonly riverUniforms: RiverUniforms;
  private readonly riverMaterial: THREE.ShaderMaterial;
  private readonly rimMaterials: THREE.ShaderMaterial[];
  private readonly rivers = new Map<string, THREE.Mesh>();
  private readonly rims = new Map<string, THREE.Mesh[]>();
  private readonly ramp = flowRamp();
  private readonly sky: THREE.Mesh;
  private meta: MapMeta | undefined;
  private relief: ReliefField | undefined;
  private water: WaterField | undefined;
  private baker: LightingBaker | undefined;
  private needsBake = false;
  private terrain: Terrain | undefined;
  private outerSea: THREE.Mesh | undefined;
  private clouds: CloudLayers | undefined;
  private cloudsOn = true;
  private exaggeration = DEFAULT_EXAGGERATION;
  private frames: THREE.LineSegments | undefined;
  private gaugeGrid = new Map<string, number[]>();
  private defaultDistance = 5000;
  private safe = { left: 0, top: 0, right: 0, bottom: 0 };
  private userMoved = false;
  private frameHandle = 0;
  private elapsed = 0;

  constructor(container: HTMLElement) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
    this.renderer.toneMapping = THREE.NoToneMapping;
    // Phones get a lighter pixel budget for the per-pixel water and clouds.
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, container.clientWidth < 720 ? 1.5 : 2));
    this.renderer.setClearColor(this.atmosphere.uHazeColor.value);
    container.appendChild(this.renderer.domElement);
    this.camera = new THREE.PerspectiveCamera(24, 1, 5, 60000);
    this.controls = new MapControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.09;
    this.controls.maxPolarAngle = 1.36;
    this.controls.minDistance = 60;
    this.controls.maxDistance = 14000;
    this.controls.zoomToCursor = true;
    this.controls.addEventListener("change", () => this.emitLabels());
    this.controls.addEventListener("start", () => (this.userMoved = true));

    // Multisampled HDR target so sun glints can exceed 1 and bloom softly.
    const target = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4 });
    this.composer = new EffectComposer(this.renderer, target);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.28, 0.35, 1.15);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    this.sky = createSky(this.atmosphere);
    this.scene.add(this.sky);

    const empty = new THREE.DataTexture(new Uint8Array([0, 0, 0, 0]), 1, 1);
    empty.needsUpdate = true;
    this.riverUniforms = {
      uRelief: { value: empty },
      uFlow: { value: empty },
      uRamp: { value: this.ramp },
      uExtent: { value: new THREE.Vector4(0, 0, 1, 1) },
      uCenter: { value: new THREE.Vector2() },
      uExaggeration: { value: this.exaggeration },
      uViewport: { value: new THREE.Vector2(1, 1) },
      uWidthScale: { value: 1 },
      uDay: { value: 0 },
      uDays: { value: 1 },
      uFoldRows: { value: 1 },
      uLevels: { value: 64 },
      uFlowReady: { value: 0 },
      uNoData: { value: glslColor(NO_DATA) },
      uWave: { value: 30 },
      uLift: { value: 0.6 },
      uDepthBias: { value: 0.0015 },
      uCasingPx: { value: 0 },
      uHalo: { value: glslColor(HALO) },
      uRimDark: { value: glslColor(RIM_DARK) },
      uSurface: { value: empty },
      uReliefTexel: { value: new THREE.Vector2(1, 1) },
      uCellKm: { value: 1 },
      uDefaultDepth: { value: 5000 },
      uPixelRatio: { value: 1 },
      uScreenScale: { value: 1 },
      uBake: { value: empty },
    };
    this.riverMaterial = createRiverMaterial(this.riverUniforms, this.atmosphere);
    this.rimMaterials = [
      createRiverMaterial(this.riverUniforms, this.atmosphere, "rim-light"),
      createRiverMaterial(this.riverUniforms, this.atmosphere, "rim-dark"),
    ];

    this.renderer.domElement.addEventListener("pointermove", this.handlePointer);
    this.renderer.domElement.addEventListener("pointerleave", () => this.onHover?.(null));
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.resize();
    this.frameHandle = requestAnimationFrame(this.loop);
  }

  /** Screen margins (CSS px) covered by overlays; the map fits inside the rest. */
  setSafeArea(safe: { left: number; top: number; right: number; bottom: number }) {
    this.safe = safe;
    this.applyViewOffset();
    if (!this.userMoved) this.resetView();
  }

  setMeta(meta: MapMeta) {
    this.meta = meta;
    const [xmin, ymin, xmax, ymax] = meta.extent_km;
    this.riverUniforms.uExtent.value.set(xmin, ymin, xmax - xmin, ymax - ymin);
    this.riverUniforms.uCenter.value.set((xmin + xmax) / 2, (ymin + ymax) / 2);
    this.riverUniforms.uLevels.value = meta.flow_levels;
    this.buildFrames();
    if (!this.outerSea) {
      this.outerSea = createOuterSea(meta.extent_km, this.atmosphere);
      this.outerSea.renderOrder = -1;
      this.scene.add(this.outerSea);
      this.clouds = createClouds(meta.extent_km, this.atmosphere);
      if (this.water) this.clouds.setSurface(this.water.surface);
      this.scene.add(this.clouds.group);
      this.setExaggeration(this.exaggeration);
    }
    this.buildTerrain();
    if (!this.userMoved) this.resetView();
  }

  addReliefBand(payload: Payload<ReliefBandMeta>) {
    if (!this.relief || !this.relief.fits(payload.meta)) {
      this.relief?.texture.dispose();
      this.baker?.dispose();
      this.relief = new ReliefField(payload.meta);
      this.baker = new LightingBaker(payload.meta.width, payload.meta.height);
      this.riverUniforms.uBake.value = this.baker.target.texture;
      this.riverUniforms.uRelief.value = this.relief.texture;
      this.riverUniforms.uReliefTexel.value.set(1 / payload.meta.width, 1 / payload.meta.height);
      if (this.meta) {
        const [xmin, , xmax] = this.meta.extent_km;
        this.riverUniforms.uCellKm.value = (xmax - xmin) / payload.meta.width;
      }
      this.buildTerrain();
    }
    this.relief.addBand(payload);
    this.needsBake = true;
  }

  setWater(payload: Payload<{ shore_units_per_px: number }>) {
    this.water?.dispose();
    this.water = decodeWater(payload);
    this.riverUniforms.uSurface.value = this.water.surface;
    this.clouds?.setSurface(this.water.surface);
    this.buildTerrain();
  }

  setRivers(payload: Payload<RiverMeta>) {
    const label = payload.meta.label;
    const previous = this.rivers.get(label);
    if (previous) {
      this.scene.remove(previous);
      previous.geometry.dispose();
    }
    this.rims.get(label)?.forEach((rim) => this.scene.remove(rim));
    const mesh = createRiverMesh(decodeRivers(payload), this.riverMaterial);
    mesh.renderOrder = label === "major" ? 3 : 2;
    this.rivers.set(label, mesh);
    this.scene.add(mesh);
    // Every rim draws before every river core, sharing the core's geometry.
    const rims = this.rimMaterials.map((material, k) => {
      const rim = new THREE.Mesh(mesh.geometry, material);
      rim.frustumCulled = false;
      rim.renderOrder = 1.4 + k * 0.1;
      this.scene.add(rim);
      return rim;
    });
    this.rims.set(label, rims);
    // Compile the rim programs now (while visible) so zooming past the rim
    // threshold later doesn't hitch.
    this.renderer.compileAsync(this.scene, this.camera).catch(() => {});
  }

  addFlowYear(payload: Payload<FlowYearMeta>) {
    if (!this.meta) throw new Error("Map metadata must arrive before flow data");
    const { slots } = payload.meta;
    if (!this.flow || this.flow.slots !== slots || this.flow.days !== this.meta.days) {
      this.flow?.texture.dispose();
      this.flow = new FlowStore(this.meta.days, slots, this.renderer.capabilities.maxTextureSize);
      this.riverUniforms.uFlow.value = this.flow.texture;
      this.riverUniforms.uDays.value = this.flow.days;
      this.riverUniforms.uFoldRows.value = this.flow.foldRows;
    }
    this.flow.addYear(payload);
    this.riverUniforms.uFlowReady.value = 1;
  }

  setGauges(points: GaugePoints) {
    this.gauges = points;
    this.gaugeGrid.clear();
    for (let i = 0; i < points.x.length; i += 1) {
      const key = `${Math.floor(points.x[i] / GRID_KM)},${Math.floor(points.y[i] / GRID_KM)}`;
      const cell = this.gaugeGrid.get(key);
      if (cell) cell.push(i);
      else this.gaugeGrid.set(key, [i]);
    }
  }

  /** Vertical exaggeration of the relief (display km per km of height). */
  setExaggeration(value: number) {
    this.exaggeration = value;
    this.riverUniforms.uExaggeration.value = value;
    this.riverUniforms.uLift.value = 0.3 + value * 0.012;
    if (this.terrain) this.terrain.uniforms.uExaggeration.value = value;
    this.atmosphere.uCloudAltitude.value = CLOUD_BASE_KM * value;
    this.clouds?.setAltitude(CLOUD_BASE_KM * value);
    this.needsBake = true;
  }

  setClouds(on: boolean) {
    this.cloudsOn = on;
  }

  setDay(day: number) {
    this.day = day;
    this.onTick?.(day);
  }

  resetView() {
    if (!this.meta) return;
    this.userMoved = false;
    const [xmin, ymin, xmax, ymax] = this.meta.extent_km;
    const halfW = (xmax - xmin) / 2;
    const halfH = (ymax - ymin) / 2;
    const tanHalf = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
    const { width, height, safeWidth, safeHeight } = this.safeSize();
    const fit = Math.max(
      (halfH / tanHalf) * (height / safeHeight) * 1.06,
      (halfW / (tanHalf * this.camera.aspect)) * (width / safeWidth),
    ) * 1.02;
    this.defaultDistance = fit;
    this.controls.maxDistance = fit * 1.6;
    this.controls.target.set(0, 0, 0);
    this.camera.position.set(0, fit * Math.cos(TILT), fit * Math.sin(TILT));
    this.camera.lookAt(0, 0, 0);
    this.controls.update();
    this.emitLabels();
  }

  dispose() {
    cancelAnimationFrame(this.frameHandle);
    this.resizeObserver.disconnect();
    this.controls.dispose();
    this.terrain?.dispose();
    this.water?.dispose();
    this.baker?.dispose();
    this.clouds?.dispose();
    this.riverMaterial.dispose();
    this.rimMaterials.forEach((m) => m.dispose());
    this.rivers.forEach((mesh) => mesh.geometry.dispose());
    this.composer.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  private buildTerrain() {
    if (!this.meta || !this.relief || !this.water || !this.baker) return;
    if (this.terrain) {
      this.scene.remove(this.terrain.mesh);
      this.terrain.dispose();
    }
    // Full relief detail comes from the textures; the mesh only needs enough
    // vertices for silhouettes. Small screens get a lighter mesh.
    const segments = this.container.clientWidth < 720 ? 700 : 1300;
    this.terrain = createTerrain(
      this.meta.extent_km,
      this.relief,
      this.water,
      this.baker.target.texture,
      this.atmosphere,
      this.exaggeration,
      segments,
    );
    this.terrain.mesh.renderOrder = 0;
    this.scene.add(this.terrain.mesh);
    this.needsBake = true;
  }

  private buildFrames() {
    if (!this.meta) return;
    if (this.frames) {
      this.scene.remove(this.frames);
      this.frames.geometry.dispose();
    }
    const [xmin, ymin, xmax, ymax] = this.meta.extent_km;
    const cx = (xmin + xmax) / 2;
    const cy = (ymin + ymax) / 2;
    const points: number[] = [];
    for (const inset of this.meta.insets) {
      const [x0, y0, x1, y1] = inset.frame_km;
      const corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
      corners.forEach(([x, y], k) => {
        const [nx, ny] = corners[(k + 1) % 4];
        points.push(x - cx, 0.4, -(y - cy), nx - cx, 0.4, -(ny - cy));
      });
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
    const material = new THREE.LineBasicMaterial({ color: glslColor(FRAME), depthTest: false });
    this.frames = new THREE.LineSegments(geometry, material);
    this.frames.renderOrder = 1;
    this.scene.add(this.frames);
  }

  private safeSize() {
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);
    const safeWidth = Math.max(120, width - this.safe.left - this.safe.right);
    const safeHeight = Math.max(120, height - this.safe.top - this.safe.bottom);
    return { width, height, safeWidth, safeHeight };
  }

  /** Shift the projection so the view's centre sits in the middle of the safe area. */
  private applyViewOffset() {
    const { width, height, safeWidth, safeHeight } = this.safeSize();
    const cx = this.safe.left + safeWidth / 2;
    const cy = this.safe.top + safeHeight / 2;
    this.camera.setViewOffset(width, height, width / 2 - cx, height / 2 - cy, width, height);
    this.emitLabels();
  }

  private resize() {
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(width, height, false);
    this.composer.setPixelRatio(this.renderer.getPixelRatio());
    this.composer.setSize(width, height);
    this.camera.aspect = width / height;
    this.applyViewOffset();
    const size = this.renderer.getDrawingBufferSize(new THREE.Vector2());
    this.riverUniforms.uViewport.value.copy(size);
    if (!this.userMoved) this.resetView();
    this.emitLabels();
  }

  private toScreen(xKm: number, yKm: number, liftKm = 0) {
    if (!this.meta) return null;
    const [xmin, ymin, xmax, ymax] = this.meta.extent_km;
    const p = new THREE.Vector3(xKm - (xmin + xmax) / 2, liftKm, -(yKm - (ymin + ymax) / 2));
    p.project(this.camera);
    if (p.z > 1) return null;
    return {
      left: ((p.x + 1) / 2) * this.container.clientWidth,
      top: ((1 - p.y) / 2) * this.container.clientHeight,
    };
  }

  private emitLabels() {
    if (!this.meta || !this.onLabels) return;
    const labels: ScreenLabel[] = [];
    for (const inset of this.meta.insets) {
      const at = this.toScreen(inset.frame_km[0], inset.frame_km[3]);
      if (at) labels.push({ key: inset.key, label: inset.label, ...at });
    }
    this.onLabels(labels);
  }

  private readonly handlePointer = (event: PointerEvent) => {
    if (!this.meta || !this.gauges || event.buttons !== 0) {
      if (event.buttons !== 0) this.onHover?.(null);
      return;
    }
    const rect = this.renderer.domElement.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;
    const ndc = new THREE.Vector2((px / rect.width) * 2 - 1, -(py / rect.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, this.camera);
    const hit = ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), new THREE.Vector3());
    if (!hit) {
      this.onHover?.(null);
      return;
    }
    const [xmin, ymin, xmax, ymax] = this.meta.extent_km;
    const mx = hit.x + (xmin + xmax) / 2;
    const my = -hit.z + (ymin + ymax) / 2;
    const gx = Math.floor(mx / GRID_KM);
    const gy = Math.floor(my / GRID_KM);
    let best = -1;
    let bestDistance = 14;
    for (let i = gx - 1; i <= gx + 1; i += 1) {
      for (let j = gy - 1; j <= gy + 1; j += 1) {
        for (const index of this.gaugeGrid.get(`${i},${j}`) ?? []) {
          const at = this.toScreen(this.gauges.x[index], this.gauges.y[index]);
          if (!at) continue;
          const d = Math.hypot(at.left - px, at.top - py);
          if (d < bestDistance) {
            bestDistance = d;
            best = index;
          }
        }
      }
    }
    if (best < 0) {
      this.onHover?.(null);
      return;
    }
    const at = this.toScreen(this.gauges.x[best], this.gauges.y[best]);
    this.onHover?.(at ? { ...at, index: best } : null);
  };

  private readonly loop = (timestamp: number) => {
    this.frameHandle = requestAnimationFrame(this.loop);
    this.timer.update(timestamp);
    const dt = Math.min(this.timer.getDelta(), 0.1);
    this.elapsed += dt;
    if (this.playing && this.flow && this.meta) {
      let next = this.day + dt * this.daysPerSecond;
      if (next > this.meta.days - 1) next = 0;
      this.setDay(next);
    }
    this.controls.update();

    if (this.needsBake && this.relief && this.water && this.baker && this.meta) {
      const [xmin, ymin, xmax, ymax] = this.meta.extent_km;
      this.baker.bake(
        this.renderer,
        this.relief.texture,
        this.water.surface,
        new THREE.Vector2(xmax - xmin, ymax - ymin),
        this.exaggeration,
        this.atmosphere.uSunDir.value,
      );
      this.needsBake = false;
    }

    const distance = this.camera.position.distanceTo(this.controls.target);
    const zoom = this.defaultDistance / distance;
    // Keep depth precision where the camera is looking.
    this.camera.near = Math.max(0.5, distance * 0.02);
    this.camera.far = distance * 12 + 4000;
    this.camera.updateProjectionMatrix();
    this.sky.position.copy(this.camera.position);
    this.sky.scale.setScalar(this.camera.far * 0.9);

    const a = this.atmosphere;
    a.uTime.value = this.elapsed;
    a.uZoom.value = zoom;
    // Atmosphere belongs to the wide view: clouds, their shadows and most of the
    // haze clear away as soon as you zoom in, so the rivers stay unobstructed.
    const wide = 1 - THREE.MathUtils.smoothstep(zoom, 1.15, 1.8);
    const close = THREE.MathUtils.smoothstep(zoom, 1.0, 2.5);
    a.uHazeStart.value = distance * THREE.MathUtils.lerp(1.02, 4.0, close);
    a.uHazeDensity.value = (0.9 / distance) * THREE.MathUtils.lerp(1.0, 0.2, close);
    const clouds = this.cloudsOn ? wide : 0;
    a.uCloudShadow.value = clouds;
    this.clouds?.setFade(clouds);

    const u = this.riverUniforms;
    u.uDay.value = this.day;
    // Thinner lines when the whole map is squeezed onto a small screen.
    const screen = THREE.MathUtils.clamp(this.safeSize().safeWidth / 1100, 0.55, 1);
    u.uWidthScale.value =
      THREE.MathUtils.clamp(zoom ** 0.38, 0.8, 2.8) * screen * this.renderer.getPixelRatio();
    u.uScreenScale.value = screen;
    u.uWave.value = 150 / zoom;
    // Rims appear as you zoom in, where relief gets busy around the rivers.
    u.uCasingPx.value =
      this.renderer.getPixelRatio() * THREE.MathUtils.lerp(0.0, 1.4, THREE.MathUtils.smoothstep(zoom, 1.3, 3.0));
    u.uDefaultDepth.value = this.defaultDistance;
    u.uPixelRatio.value = this.renderer.getPixelRatio();
    this.rims.forEach((rims) => rims.forEach((rim) => (rim.visible = u.uCasingPx.value > 0.05)));
    this.composer.render(dt);
  };
}
