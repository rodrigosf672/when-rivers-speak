import * as THREE from "three";

import { ATMOSPHERE_GLSL, type AtmosphereUniforms, OTHER_RELIEF } from "./glsl.ts";
import {
  ABYSS,
  DEEP,
  FOAM,
  glslColor,
  LAKE,
  LAND_RAMP_TOP_KM,
  landRamp,
  OTHER_LAND,
  SHALLOW,
  SHELF,
} from "./palette.ts";
import { type Payload, shapeOf, u16, u8 } from "./payload.ts";

/** Map extent in km: [xmin, ymin, xmax, ymax] of the shared Albers plane. */
export type Extent = [number, number, number, number];

export type ReliefBandMeta = {
  band: number;
  bands: number;
  row0: number;
  rows: number;
  width: number;
  height: number;
  max_m: number;
};

const halfFloatTexture = (data: Uint16Array, width: number, height: number) => {
  const texture = new THREE.DataTexture(data, width, height, THREE.RedFormat, THREE.HalfFloatType);
  texture.magFilter = THREE.LinearFilter;
  texture.minFilter = THREE.LinearFilter;
  texture.needsUpdate = true;
  return texture;
};

/** Land height (km) assembled from the notebook's row bands; north row first. */
export class ReliefField {
  readonly width: number;
  readonly height: number;
  readonly bands: number;
  readonly texture: THREE.DataTexture;
  private readonly half: Uint16Array;
  private readonly received = new Set<number>();

  constructor(meta: ReliefBandMeta) {
    this.width = meta.width;
    this.height = meta.height;
    this.bands = meta.bands;
    this.half = new Uint16Array(meta.width * meta.height);
    this.texture = halfFloatTexture(this.half, meta.width, meta.height);
  }

  fits(meta: ReliefBandMeta) {
    return meta.width === this.width && meta.height === this.height && meta.bands === this.bands;
  }

  addBand(payload: Payload<ReliefBandMeta>) {
    const { row0, rows, band } = payload.meta;
    const delta = u16(payload, "height_delta");
    for (let r = 0; r < rows; r += 1) {
      let value = 0;
      const out = (row0 + r) * this.width;
      for (let c = 0; c < this.width; c += 1) {
        value = (value + delta[r * this.width + c]) & 0xffff;
        this.half[out + c] = THREE.DataUtils.toHalfFloat(value / 1000);
      }
    }
    this.received.add(band);
    this.texture.needsUpdate = true;
  }

  get complete() {
    return this.received.size === this.bands;
  }
}

export type WaterField = {
  surface: THREE.DataTexture;
  shore: THREE.DataTexture;
  shoreUnitsPerPx: number;
  depth: THREE.DataTexture;
  dispose: () => void;
};

/** Surface classes as a smooth RGBA mask (R US land, G other land, B lake), shore distance and sea depth. */
export const decodeWater = (payload: Payload<{ shore_units_per_px: number }>): WaterField => {
  const [height, width] = shapeOf(payload, "surface");
  const classes = u8(payload, "surface");
  const rgba = new Uint8Array(width * height * 4);
  for (let i = 0; i < classes.length; i += 1) {
    const c = classes[i];
    rgba[i * 4] = c === 2 ? 255 : 0;
    rgba[i * 4 + 1] = c === 1 ? 255 : 0;
    rgba[i * 4 + 2] = c === 3 ? 255 : 0;
    rgba[i * 4 + 3] = 255;
  }
  const surface = new THREE.DataTexture(rgba, width, height, THREE.RGBAFormat);
  surface.magFilter = THREE.LinearFilter;
  surface.minFilter = THREE.LinearMipmapLinearFilter;
  surface.generateMipmaps = true;
  surface.needsUpdate = true;

  const shore = new THREE.DataTexture(u8(payload, "shore").slice(), width, height, THREE.RedFormat);
  shore.magFilter = THREE.LinearFilter;
  shore.minFilter = THREE.LinearFilter;
  shore.unpackAlignment = 1;
  shore.needsUpdate = true;

  const [dh, dw] = shapeOf(payload, "depth_delta");
  const deltas = u16(payload, "depth_delta");
  const depthHalf = new Uint16Array(dw * dh);
  for (let r = 0; r < dh; r += 1) {
    let value = 0;
    for (let c = 0; c < dw; c += 1) {
      value = (value + deltas[r * dw + c]) & 0xffff;
      depthHalf[r * dw + c] = THREE.DataUtils.toHalfFloat(value / 1000);
    }
  }
  const depth = halfFloatTexture(depthHalf, dw, dh);
  return {
    surface,
    shore,
    shoreUnitsPerPx: payload.meta.shore_units_per_px,
    depth,
    dispose: () => {
      surface.dispose();
      shore.dispose();
      depth.dispose();
    },
  };
};

// Land brightness range under lighting, as a share of albedo. Lower floors give
// more relief contrast; river rims (rivers.ts) keep rivers separate where shaded
// ground reaches a river's lightness.
const RELIEF_FLOOR = "0.52";
const RELIEF_SPAN = "0.56";

const WATER_GLSL = /* glsl */ `
  // Depth of the open ocean beyond the map, matching the abyssal plains inside it.
  const float OPEN_SEA_KM = 4.8;
  uniform vec3 uShallow;
  uniform vec3 uShelf;
  uniform vec3 uDeep;
  uniform vec3 uAbyss;
  uniform vec3 uFoam;

  vec3 depthColor(float km) {
    vec3 c = mix(uShallow, uShelf, smoothstep(0.0, 0.07, km));
    c = mix(c, uDeep, smoothstep(0.07, 1.4, km));
    return mix(c, uAbyss, smoothstep(1.4, 4.5, km));
  }

  // Stylized water: depth colour, sky reflection by Fresnel, sun glints.
  vec3 shadeWater(vec3 body, vec2 mapP, vec3 world, float cloud) {
    vec3 V = normalize(cameraPosition - world);
    // Fade wave detail where it gets smaller than a pixel, so distant water
    // turns into a smooth mirror instead of shimmering stripes.
    float scale = 110.0 / uZoom;
    float footprint = length(fwidth(mapP));
    float lod = 1.0 - smoothstep(scale * 0.08, scale * 0.45, footprint);
    // Seen at a grazing angle, water calms into a mirror of the sky.
    float calm = mix(0.3, 1.0, smoothstep(0.1, 0.7, V.y));
    vec3 n = waveNormal(mapP, scale, 0.12 * lod * calm);
    float F = fresnel(dot(n, V));
    vec3 color = body * mix(0.78, 1.0, cloud);
    color = mix(color, skyColor(reflect(-V, n)), F * 0.85);
    vec3 H = normalize(uSunDir + V);
    float nh = max(dot(n, H), 0.0);
    // Glitter only once you are close; far away the sea keeps a soft sheen.
    float glitter = smoothstep(1.3, 4.0, uZoom) * lod;
    color += uSunColor * (pow(nh, 2600.0) * 2.6 * glitter + pow(nh, 160.0) * 0.16) * cloud;
    return color;
  }
`;

const vertexShader = /* glsl */ `
  uniform sampler2D uRelief;
  uniform sampler2D uSurface;
  uniform float uExaggeration;
  out vec2 vGrid;
  out vec3 vWorld;
  out float vHeightKm;
  void main() {
    vGrid = vec2(uv.x, 1.0 - uv.y);
    vHeightKm = texture(uRelief, vGrid).r;
    // Other countries stay low and quiet so the US reads first.
    float us = smoothstep(0.3, 0.7, texture(uSurface, vGrid).r);
    vec3 p = position;
    p.z += vHeightKm * uExaggeration * mix(${OTHER_RELIEF}, 1.0, us);
    vec4 world = modelMatrix * vec4(p, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const fragmentShader = /* glsl */ `
  precision highp float;
  ${ATMOSPHERE_GLSL}
  ${WATER_GLSL}
  uniform sampler2D uRelief;
  uniform sampler2D uBake;
  uniform sampler2D uSurface;
  uniform sampler2D uShore;
  uniform sampler2D uDepth;
  uniform sampler2D uLandRamp;
  uniform float uLandTopKm;
  uniform float uExaggeration;
  uniform float uShoreScale;
  uniform vec2 uTexel;
  uniform vec2 uCellKm;
  uniform vec2 uCenter;
  uniform vec2 uSizeKm;
  uniform vec3 uOtherLand;
  uniform vec3 uLake;
  in vec2 vGrid;
  in vec3 vWorld;
  in float vHeightKm;
  out vec4 outColor;

  // Lighting uses a gentler slope than the geometry so tall relief stays readable.
  vec3 landNormal(float scale) {
    float hw = texture(uRelief, vGrid - vec2(uTexel.x, 0.0)).r;
    float he = texture(uRelief, vGrid + vec2(uTexel.x, 0.0)).r;
    float hn = texture(uRelief, vGrid - vec2(0.0, uTexel.y)).r;
    float hs = texture(uRelief, vGrid + vec2(0.0, uTexel.y)).r;
    float k = uExaggeration * 0.7 * scale;
    float dx = (he - hw) * k / (2.0 * uCellKm.x);
    float dz = (hs - hn) * k / (2.0 * uCellKm.y);
    return normalize(vec3(-dx, 1.0, -dz));
  }

  void main() {
    vec4 surface = texture(uSurface, vGrid);
    float usA = smoothstep(0.3, 0.7, surface.r);
    float lakeA = smoothstep(0.3, 0.7, surface.b);
    // Other countries fade into open sea toward the map's edges.
    vec2 edgeKm = min(vGrid, 1.0 - vGrid) * uSizeKm;
    float edge = smoothstep(40.0, 160.0, min(edgeKm.x, edgeKm.y));
    float otherRaw = smoothstep(0.3, 0.7, surface.g);
    float otherA = otherRaw * edge;
    float landA = max(usA, otherA);
    vec2 mapP = vec2(vWorld.x + uCenter.x, uCenter.y - vWorld.z);
    // Cloud shadows are kept off the data: over US land they fade with the thinned veil.
    float cloud = mix(cloudShadowAt(vWorld, uCenter), 1.0, 0.7 * usA);
    vec2 bake = texture(uBake, vGrid).rg;

    // Land: painted parchment under a warm sun and a cool sky fill.
    vec3 n = landNormal(mix(${OTHER_RELIEF}, 1.0, usA));
    vec3 albedo = texture(uLandRamp, vec2(clamp(vHeightKm / uLandTopKm, 0.0, 1.0), 0.5)).rgb;
    albedo *= 0.93 + 0.13 * fbm(mapP / 18.0);
    // Up close, painterly detail: fine ridges and colour variation that the
    // 2 km relief grid cannot carry.
    float closeUp = smoothstep(2.0, 8.0, uZoom) * usA;
    if (closeUp > 0.001) {
      float cell = 80.0 / uZoom;
      float d0 = fbm(mapP / cell);
      float dx = fbm((mapP + vec2(cell * 0.15, 0.0)) / cell) - d0;
      float dy = fbm((mapP + vec2(0.0, cell * 0.15)) / cell) - d0;
      n = normalize(n + vec3(-dx, 0.0, dy) * 0.6 * closeUp);
      albedo *= mix(1.0, 0.95 + 0.1 * fbm(mapP / (cell * 0.35) + 11.0), closeUp);
    }
    float steep = smoothstep(0.1, 0.55, 1.0 - n.y);
    albedo = mix(albedo, albedo * vec3(0.95, 0.93, 0.91), steep); // neutral rock, not river-brown
    float sun = max(dot(n, uSunDir), 0.0) * mix(0.6, 1.0, bake.r) * cloud;
    float sky = (0.62 + 0.38 * n.y) * mix(0.75, 1.0, bake.g);
    vec3 light = uSunColor * sun * 0.95 + uAmbient * sky * 0.76;
    // Data-first hillshade: keep the warm-sun / cool-sky tint but squeeze its
    // brightness into [floor, floor + span] of albedo (flat ground stays ~1), so no
    // slope or shadow gets as dark as a river and no lit face reaches bloom.
    float Y = dot(light, vec3(0.2126, 0.7152, 0.0722));
    vec3 us = albedo * light * ((${RELIEF_FLOOR} + ${RELIEF_SPAN} * smoothstep(0.2, 1.4, Y)) / max(Y, 1e-3));
    vec3 other = uOtherLand * (0.82 + 0.18 * sun / 0.52) * mix(0.9, 1.0, cloud);
    vec3 land = mix(other, us, usA / max(landA, 1e-3));

    // Water: sea depth from bathymetry, lakes shallow and fresh-coloured.
    // Ease shelves into open ocean near the map's edges so the outer sea joins seamlessly.
    float offshore = smoothstep(40.0, 350.0, min(edgeKm.x, edgeKm.y));
    float depthKm = mix(OPEN_SEA_KM, texture(uDepth, vGrid).r, offshore);
    vec3 body = mix(depthColor(depthKm), uLake, lakeA);
    vec3 water = shadeWater(body, mapP, vWorld, cloud);

    // Coastal foam: a crisp shoreline plus surf bands rolling toward land.
    float shorePx = texture(uShore, vGrid).r * uShoreScale;
    float grain = fbm(mapP * sqrt(uZoom) / 9.0 + uTime * 0.04);
    float bands = 0.5 + 0.5 * sin(shorePx * 6.0 - uTime * 1.7 + grain * 6.0);
    float surf = smoothstep(1.8, 0.2, shorePx) * smoothstep(0.55, 0.85, bands * (0.6 + grain));
    float line = smoothstep(0.6, 0.15, shorePx);
    float realism = smoothstep(1.3, 5.0, uZoom);
    // Only real water foams, not other countries faded out at the map's edge.
    float foam = clamp(line * 0.75 + surf * mix(0.25, 0.9, realism), 0.0, 1.0)
      * (1.0 - lakeA * 0.5) * (1.0 - otherRaw);
    water = mix(water, uFoam * mix(0.82, 1.0, cloud), foam * 0.85);

    vec3 color = mix(water, land, landA);
    outColor = vec4(applyHaze(color, vWorld), 1.0);
  }
`;

export type TerrainUniforms = {
  uExaggeration: { value: number };
  uBake: { value: THREE.Texture | null };
};

export type Terrain = {
  mesh: THREE.Mesh;
  uniforms: TerrainUniforms;
  dispose: () => void;
};

const waterColors = () => ({
  uShallow: { value: glslColor(SHALLOW) },
  uShelf: { value: glslColor(SHELF) },
  uDeep: { value: glslColor(DEEP) },
  uAbyss: { value: glslColor(ABYSS) },
  uFoam: { value: glslColor(FOAM) },
});

export const createTerrain = (
  extent: Extent,
  relief: ReliefField,
  water: WaterField,
  bake: THREE.Texture,
  atmosphere: AtmosphereUniforms,
  exaggeration: number,
  segmentsWide: number,
): Terrain => {
  const width = extent[2] - extent[0];
  const height = extent[3] - extent[1];
  const segX = Math.min(relief.width - 1, segmentsWide);
  const segY = Math.round((segX * relief.height) / relief.width);
  const geometry = new THREE.PlaneGeometry(width, height, segX, segY);
  geometry.deleteAttribute("normal");
  const ramp = landRamp();
  const uniforms = {
    ...atmosphere,
    ...waterColors(),
    uRelief: { value: relief.texture },
    uBake: { value: bake },
    uSurface: { value: water.surface },
    uShore: { value: water.shore },
    uDepth: { value: water.depth },
    uLandRamp: { value: ramp },
    uLandTopKm: { value: LAND_RAMP_TOP_KM },
    uExaggeration: { value: exaggeration },
    uShoreScale: { value: 255 / water.shoreUnitsPerPx },
    uTexel: { value: new THREE.Vector2(1 / relief.width, 1 / relief.height) },
    uCellKm: { value: new THREE.Vector2(width / relief.width, height / relief.height) },
    uCenter: { value: new THREE.Vector2((extent[0] + extent[2]) / 2, (extent[1] + extent[3]) / 2) },
    uSizeKm: { value: new THREE.Vector2(width, height) },
    uOtherLand: { value: glslColor(OTHER_LAND) },
    uLake: { value: glslColor(LAKE) },
  };
  const material = new THREE.ShaderMaterial({
    glslVersion: THREE.GLSL3,
    uniforms,
    vertexShader,
    fragmentShader,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.rotation.x = -Math.PI / 2;
  mesh.frustumCulled = false;
  return {
    mesh,
    uniforms,
    dispose: () => {
      geometry.dispose();
      material.dispose();
      ramp.dispose();
    },
  };
};

const outerVertex = /* glsl */ `
  out vec3 vWorld;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const outerFragment = /* glsl */ `
  precision highp float;
  ${ATMOSPHERE_GLSL}
  ${WATER_GLSL}
  uniform vec2 uCenter;
  in vec3 vWorld;
  out vec4 outColor;
  void main() {
    vec2 mapP = vec2(vWorld.x + uCenter.x, uCenter.y - vWorld.z);
    float cloud = cloudShadowAt(vWorld, uCenter);
    vec3 color = shadeWater(depthColor(OPEN_SEA_KM), mapP, vWorld, cloud);
    outColor = vec4(applyHaze(color, vWorld), 1.0);
  }
`;

/** Open sea around the map, out to the horizon. */
export const createOuterSea = (extent: Extent, atmosphere: AtmosphereUniforms) => {
  const size = Math.max(extent[2] - extent[0], extent[3] - extent[1]) * 24;
  const geometry = new THREE.PlaneGeometry(size, size, 1, 1);
  const material = new THREE.ShaderMaterial({
    glslVersion: THREE.GLSL3,
    uniforms: {
      ...atmosphere,
      ...waterColors(),
      uCenter: { value: new THREE.Vector2((extent[0] + extent[2]) / 2, (extent[1] + extent[3]) / 2) },
    },
    vertexShader: outerVertex,
    fragmentShader: outerFragment,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.y = -0.3;
  mesh.frustumCulled = false;
  return mesh;
};
