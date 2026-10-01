import * as THREE from "three";

/**
 * Flow vs normal, from far below (t = -1) through normal (0) to far above (+1).
 * Every stop is more saturated than any land colour (parchment stays
 * low-chroma); where shaded ground matches a river's lightness, the adaptive
 * rims in rivers.ts restore local contrast.
 */
export const FLOW_STOPS: readonly [number, string][] = [
  [-1.0, "#703504"],
  [-0.62, "#954e0d"],
  [-0.3, "#b36b1e"],
  // The orange/blue break sits inside half a flow code (1/63 of the scale) so
  // no code, and no ramp texel, lands on the grey midpoint.
  [-0.008, "#c4884a"],
  [0.0, "#6895c3"],
  [0.3, "#4475ba"],
  [0.64, "#2750a2"],
  [1.0, "#16327f"],
];

/** Rivers no gauge reaches within the spread limit. */
export const NO_DATA = "#9a948c";

/** Parchment land by display height in metres, before lighting. */
export const LAND_STOPS: readonly [number, string][] = [
  [0, "#efe5cc"],
  [350, "#e9dcbd"],
  [900, "#dfcba4"],
  [1700, "#d5bd96"],
  [2600, "#c9b08c"],
  [3300, "#cdb9a0"],
  [4300, "#ece4d6"],
];

/** Stylized water, from surf-zone shallows to the abyss. */
export const SHALLOW = "#8fd6cd";
export const SHELF = "#4fa7c3";
export const DEEP = "#2b64a1";
export const ABYSS = "#1b3f73";
export const FOAM = "#f5faf8";
export const LAKE = "#86bfd2";
export const OTHER_LAND = "#d6d9d2";
export const FRAME = "#8e9db3";
/** Light rim under rivers on shaded ground (MAX-blended, see rivers.ts). */
export const HALO = "#f3ebd6";
/** Dark rim under rivers on bright ground (MIN-blended, see rivers.ts). */
export const RIM_DARK = "#a9967a";

const LAND_TOP_M = 4500;

const rampTexture = (stops: readonly [number, string][], lo: number, hi: number) => {
  const size = 256;
  const data = new Uint8Array(size * 4);
  const colors = stops.map(([at, hex]) => [at, new THREE.Color(hex)] as const);
  for (let i = 0; i < size; i += 1) {
    const at = lo + ((hi - lo) * i) / (size - 1);
    let k = 0;
    while (k < colors.length - 2 && at > colors[k + 1][0]) k += 1;
    const [a, ca] = colors[k];
    const [b, cb] = colors[k + 1];
    const f = Math.min(1, Math.max(0, (at - a) / (b - a)));
    const c = ca.clone().lerp(cb, f);
    data.set([c.r * 255, c.g * 255, c.b * 255, 255], i * 4);
  }
  const texture = new THREE.DataTexture(data, size, 1, THREE.RGBAFormat);
  texture.magFilter = THREE.LinearFilter;
  texture.minFilter = THREE.LinearFilter;
  texture.wrapS = THREE.ClampToEdgeWrapping;
  texture.needsUpdate = true;
  return texture;
};

export const flowRamp = () => rampTexture(FLOW_STOPS, -1, 1);
export const landRamp = () => rampTexture(LAND_STOPS, 0, LAND_TOP_M);
export const LAND_RAMP_TOP_KM = LAND_TOP_M / 1000;

/** CSS gradient for the legend, from the same stops as the shader. */
export const flowGradientCss = () =>
  `linear-gradient(90deg, ${FLOW_STOPS.map(([at, hex]) => `${hex} ${((at + 1) / 2) * 100}%`).join(", ")})`;

export const glslColor = (hex: string) => new THREE.Color(hex);
