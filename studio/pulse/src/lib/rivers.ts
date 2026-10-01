import * as THREE from "three";

import { ATMOSPHERE_GLSL, type AtmosphereUniforms } from "./glsl.ts";
import { f32, type Payload, u16, u8 } from "./payload.ts";

export type RiverMeta = {
  label: string;
  reaches: number;
  vertices: number;
  quantum_m: number;
  origin_m: [number, number];
};

/** Per-segment instance data in map kilometres. */
export type RiverSegments = {
  count: number;
  ends: Float32Array; // x0, y0, x1, y1
  dist: Float32Array; // map km to the sea at each end
  width: Float32Array; // width code: 40 * (log10 mean m3/s + 2)
  slot: Float32Array; // gauge slot, 65535 = none
};

/** Expand per-reach delta-coded polylines into independent segments. */
export const decodeRivers = (payload: Payload<RiverMeta>): RiverSegments => {
  const nverts = u16(payload, "nverts");
  const dx = u16(payload, "dx");
  const dy = u16(payload, "dy");
  const width = u8(payload, "width");
  const slot = u16(payload, "slot");
  const distDn = f32(payload, "dist_dn");
  const q = payload.meta.quantum_m / 1000;
  const [ox, oy] = payload.meta.origin_m.map((v) => v / 1000);
  const count = payload.meta.vertices - payload.meta.reaches;
  const out: RiverSegments = {
    count,
    ends: new Float32Array(count * 4),
    dist: new Float32Array(count * 2),
    width: new Float32Array(count),
    slot: new Float32Array(count),
  };
  let v = 0;
  let s = 0;
  const xs: number[] = [];
  const ys: number[] = [];
  for (let r = 0; r < nverts.length; r += 1) {
    const n = nverts[r];
    xs.length = 0;
    ys.length = 0;
    let x = 0;
    let y = 0;
    for (let k = 0; k < n; k += 1) {
      x = k === 0 ? dx[v] : (x + dx[v]) & 0xffff;
      y = k === 0 ? dy[v] : (y + dy[v]) & 0xffff;
      xs.push(ox + x * q);
      ys.push(oy + y * q);
      v += 1;
    }
    // Distance to the sea at each vertex: the reach's downstream end plus
    // the remaining length along this reach.
    let toEnd = 0;
    const remaining = new Float32Array(n);
    for (let k = n - 2; k >= 0; k -= 1) {
      toEnd += Math.hypot(xs[k + 1] - xs[k], ys[k + 1] - ys[k]);
      remaining[k] = toEnd;
    }
    for (let k = 0; k < n - 1; k += 1) {
      out.ends.set([xs[k], ys[k], xs[k + 1], ys[k + 1]], s * 4);
      out.dist[s * 2] = distDn[r] + remaining[k];
      out.dist[s * 2 + 1] = distDn[r] + remaining[k + 1];
      out.width[s] = width[r];
      out.slot[s] = slot[r];
      s += 1;
    }
  }
  return out;
};

const vertexShader = /* glsl */ `
  precision highp float;
  precision highp int;
  precision highp sampler2D;
  in vec4 aEnds;
  in vec2 aDist;
  in float aWidth;
  in float aSlot;
  uniform sampler2D uRelief;
  uniform sampler2D uFlow;
  uniform sampler2D uRamp;
  uniform vec4 uExtent;
  uniform vec2 uCenter;
  uniform float uExaggeration;
  uniform vec2 uViewport;
  uniform float uWidthScale;
  uniform float uDay;
  uniform int uDays;
  uniform int uFoldRows;
  uniform float uLevels;
  uniform float uFlowReady;
  uniform vec3 uNoData;
  uniform float uLift;
  uniform float uDepthBias;
  uniform float uCasingPx;
  uniform sampler2D uSurface;
  uniform vec2 uReliefTexel;
  uniform float uCellKm;
  uniform float uDefaultDepth;
  uniform float uPixelRatio;
  uniform float uScreenScale;
  uniform vec3 uSunDir;
  uniform sampler2D uBake;
  out vec3 vColor;
  out float vCasing;
  out vec3 vWorld;
  out vec2 vTangent;
  out float vAlpha;
  out float vFlow;
  out float vHasFlow;
  out vec2 vLocal;
  out float vLength;
  out float vHalf;
  out float vDist;

  float heightAt(vec2 p) {
    vec2 uv = (p - uExtent.xy) / uExtent.zw;
    return texture(uRelief, vec2(uv.x, 1.0 - uv.y)).r * uExaggeration;
  }

  vec3 toWorld(vec2 p) {
    return vec3(p.x - uCenter.x, heightAt(p) + uLift, -(p.y - uCenter.y));
  }

  float codeAt(int slot, int day) {
    int fold = slot / uFoldRows;
    int row = slot - fold * uFoldRows;
    return texelFetch(uFlow, ivec2(fold * uDays + day, row), 0).r * 255.0;
  }

  void main() {
    // Flow vs normal for this segment's gauge, blended between days.
    vHasFlow = 0.0;
    vFlow = 0.0;
    if (aSlot < 65000.0 && uFlowReady > 0.5) {
      int slot = int(aSlot + 0.5);
      int d0 = int(floor(uDay));
      int d1 = min(d0 + 1, uDays - 1);
      float c0 = codeAt(slot, d0);
      float c1 = codeAt(slot, d1);
      if (c0 > 0.5 || c1 > 0.5) {
        if (c0 < 0.5) c0 = c1;
        if (c1 < 0.5) c1 = c0;
        float c = mix(c0, c1, fract(uDay));
        vFlow = (c - 1.0) / (uLevels - 1.0) * 2.0 - 1.0;
        vHasFlow = 1.0;
      }
    }
    vColor = vHasFlow > 0.5 ? texture(uRamp, vec2(vFlow * 0.5 + 0.5, 0.5)).rgb : uNoData;
#ifdef CASING
    // Rim only under gauged rivers on US land (not across lakes, sea or ungauged reaches).
    vec2 mid = ((aEnds.xy + aEnds.zw) * 0.5 - uExtent.xy) / uExtent.zw;
    if (vHasFlow < 0.5 || textureLod(uSurface, vec2(mid.x, 1.0 - mid.y), 0.0).r < 0.5) {
      gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      return;
    }
#endif

    vec3 wa = toWorld(aEnds.xy);
    vec3 wb = toWorld(aEnds.zw);
    vec4 ca = projectionMatrix * modelViewMatrix * vec4(wa, 1.0);
    vec4 cb = projectionMatrix * modelViewMatrix * vec4(wb, 1.0);

    // Width in pixels: grows with modelled mean flow and swells with today's flow.
    float dis = pow(10.0, aWidth / 40.0 - 2.0);
    float nominal = (0.3 + 0.27 * pow(dis, 0.3));
    nominal *= vHasFlow > 0.5 ? mix(0.8, 1.45, smoothstep(-0.7, 0.8, vFlow)) : 0.7;
    // Screen width follows the zoom, but never exceeds what the river would
    // be at the default framing for its distance, so far-off networks in tilted
    // views thin out instead of turning into ribbons over the relief.
    float depth = max(0.5 * (ca.w + cb.w), 1e-3);
    float px = nominal * min(uWidthScale, uPixelRatio * uScreenScale * uDefaultDepth / depth);
    if (vHasFlow > 0.5) {
      vAlpha = 1.0;
      if (px < 1.0) {
        vAlpha *= px;
        px = 1.0;
      }
    } else {
      // Ungauged reaches: one steady mid-grey hairline, never fading toward white.
      vAlpha = 0.55;
      px = max(px, 1.0);
    }

    vCasing = 0.0;
#ifdef CASING
    // Rims only where the ground around the river is rugged (valley walls),
    // thinner on tributaries; the plains keep their clean lines.
    vec2 g = vec2(mid.x, 1.0 - mid.y);
    float reach = 4.0;
    float hw = texture(uRelief, g - vec2(uReliefTexel.x * reach, 0.0)).r;
    float he = texture(uRelief, g + vec2(uReliefTexel.x * reach, 0.0)).r;
    float hn = texture(uRelief, g - vec2(0.0, uReliefTexel.y * reach)).r;
    float hs = texture(uRelief, g + vec2(0.0, uReliefTexel.y * reach)).r;
    float localRelief = (max(max(hw, he), max(hn, hs)) - min(min(hw, he), min(hn, hs)));
    float rugged = smoothstep(0.08, 0.35, localRelief * uExaggeration / (2.0 * reach * uCellKm));
    vCasing = rugged * clamp(px / 2.5, 0.5, 1.0);
    // Exactly one rim per reach, chosen by how lit the ground right beside it
    // is: shaded ground gets the light rim, sunlit ground the dark one. (Drawn
    // together, MAX then MIN would collapse to the dark colour everywhere.)
    float gw = texture(uRelief, g - vec2(uReliefTexel.x, 0.0)).r;
    float ge = texture(uRelief, g + vec2(uReliefTexel.x, 0.0)).r;
    float gn = texture(uRelief, g - vec2(0.0, uReliefTexel.y)).r;
    float gs = texture(uRelief, g + vec2(0.0, uReliefTexel.y)).r;
    float k = uExaggeration * 0.7 / (2.0 * uCellKm);
    vec3 ground = normalize(vec3(-(ge - gw) * k, 1.0, -(gs - gn) * k));
    float lit = max(dot(ground, uSunDir), 0.0) * mix(0.6, 1.0, texture(uBake, g).r) / max(uSunDir.y, 0.1);
  #if CASING == 1
    vCasing *= step(lit, 0.95);
  #else
    vCasing *= 1.0 - step(lit, 0.95);
  #endif
    if (vCasing < 0.03) {
      gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      return;
    }
#endif

    vec2 sa = ca.xy / ca.w * 0.5 * uViewport;
    vec2 sb = cb.xy / cb.w * 0.5 * uViewport;
    vec2 along = sb - sa;
    float len = length(along);
    vec2 dir = len > 1e-4 ? along / len : vec2(1.0, 0.0);
    vec2 nrm = vec2(-dir.y, dir.x);
    float halfW = px * 0.5 + 0.75;
#ifdef CASING
    halfW += uCasingPx * vCasing;
#endif
    float t = position.x;
    vec2 screen = mix(sa, sb, t) + dir * (t * 2.0 - 1.0) * halfW + nrm * position.y * halfW;
    vec4 clip = mix(ca, cb, t);
    // Pull rivers slightly toward the camera so they win against the valley
    // floor they lie on, while ridges in front still hide them.
    gl_Position = vec4(screen / (0.5 * uViewport) * clip.w, clip.z - uDepthBias * clip.w, clip.w);
    vWorld = mix(wa, wb, t);
    vec2 flat2 = wb.xz - wa.xz;
    vTangent = length(flat2) > 1e-5 ? normalize(flat2) : vec2(1.0, 0.0);

    vLocal = vec2(t * len + (t * 2.0 - 1.0) * halfW, position.y * halfW);
    vLength = len;
    vHalf = px * 0.5;
    vDist = mix(aDist.x, aDist.y, t);
  }
`;

const fragmentShader = /* glsl */ `
  precision highp float;
  ${ATMOSPHERE_GLSL}
  uniform float uWave;
  uniform float uCasingPx;
  uniform vec3 uHalo;
  uniform vec3 uRimDark;
  in vec3 vColor;
  in float vCasing;
  in float vAlpha;
  in float vFlow;
  in float vHasFlow;
  in vec2 vLocal;
  in float vLength;
  in float vHalf;
  in float vDist;
  in vec3 vWorld;
  in vec2 vTangent;
  out vec4 outColor;
  void main() {
    // Capsule around the segment gives round joins and caps.
    float along = clamp(vLocal.x, 0.0, vLength);
    float d = length(vec2(vLocal.x - along, vLocal.y));
#ifdef CASING
    // Adaptive rim in two passes. CASING 1 is MAX-blended light: invisible on
    // lit ground, a sunlit bank on shade. CASING 2 is MIN-blended dark: an
    // edge on bright snow and sunlit faces, nothing on shaded ground.
    float rim = uCasingPx * vCasing;
    float cov = (1.0 - smoothstep(vHalf + rim - 0.5, vHalf + rim + 0.5, d)) * vAlpha;
    if (cov <= 0.0) discard;
  #if CASING == 2
    outColor = vec4(mix(vec3(1.0), applyHaze(uRimDark, vWorld, 0.5), cov * 0.8), 1.0);
  #else
    outColor = vec4(applyHaze(uHalo, vWorld, 0.5) * cov, 1.0);
  #endif
    return;
#endif
    float edge = 1.0 - smoothstep(vHalf - 0.5, vHalf + 0.5, d);
    if (edge <= 0.0) discard;
    vec3 color = vColor;
    float across = clamp(vLocal.y / max(vHalf, 1.0), -1.0, 1.0);
    float channel = clamp(1.0 - across * across, 0.0, 1.0);
    float wet = vFlow * 0.5 + 0.5;
    float realism = smoothstep(1.8, 6.0, uZoom) * step(1.2, vHalf);
    if (vHasFlow > 0.5 && vHalf > 0.9) {
      // Streaks of light on the current, travelling toward the sea: faster
      // and brighter when flow runs high, strongest mid-channel.
      float speed = mix(0.25, 1.2, wet);
      float phase = vDist / uWave + uTime * speed;
      float streak = pow(0.5 + 0.5 * sin(6.2831853 * phase), 14.0);
      color = mix(color, vec3(1.0), streak * channel * mix(0.06, 0.18, wet) * (1.0 - 0.8 * realism));
    }

    // Up close the flow colour stays the body of the water: ripples running
    // downstream, a touch of sky and sparse sun glints (which bloom), but the
    // data colour itself is never washed toward the paper.
    if (realism > 0.001) {
      float ripple = uWave * 0.22;
      float speed = mix(0.6, 2.2, wet) * (vHasFlow > 0.5 ? 1.0 : 0.5);
      // Ripple cells stretched along the current, as real rivers streak.
      vec2 q = vec2(vDist / (ripple * 2.5) + uTime * speed * 0.4, across * 4.0);
      float e = 0.07;
      float h0 = valueNoise(q) + 0.5 * valueNoise(q * 2.3 + 5.1);
      float hs = valueNoise(q + vec2(e, 0.0)) + 0.5 * valueNoise((q + vec2(e, 0.0)) * 2.3 + 5.1);
      float hc = valueNoise(q + vec2(0.0, e)) + 0.5 * valueNoise((q + vec2(0.0, e)) * 2.3 + 5.1);
      vec3 T = vec3(vTangent.x, 0.0, vTangent.y);
      vec3 B = normalize(cross(vec3(0.0, 1.0, 0.0), T));
      vec3 n = normalize(vec3(0.0, 1.0, 0.0) + T * (hs - h0) / e * 0.16 + B * (hc - h0) / e * 0.1);
      vec3 V = normalize(cameraPosition - vWorld);
      float F = fresnel(dot(n, V));
      vec3 water = mix(color, skyColor(reflect(-V, n)), min(F, 0.5) * 0.3);
      float nh = max(dot(n, normalize(uSunDir + V)), 0.0);
      water += uSunColor * pow(nh, 900.0) * 3.0 * channel;
      color = mix(color, water, realism);
    }
    outColor = vec4(applyHaze(color, vWorld, 0.5), vAlpha * edge);
  }
`;

export type RiverUniforms = {
  uRelief: { value: THREE.Texture | null };
  uFlow: { value: THREE.Texture | null };
  uRamp: { value: THREE.Texture | null };
  uExtent: { value: THREE.Vector4 };
  uCenter: { value: THREE.Vector2 };
  uExaggeration: { value: number };
  uViewport: { value: THREE.Vector2 };
  uWidthScale: { value: number };
  uDay: { value: number };
  uDays: { value: number };
  uFoldRows: { value: number };
  uLevels: { value: number };
  uFlowReady: { value: number };
  uNoData: { value: THREE.Color };
  uWave: { value: number };
  uLift: { value: number };
  uDepthBias: { value: number };
  uCasingPx: { value: number };
  uHalo: { value: THREE.Color };
  uRimDark: { value: THREE.Color };
  uSurface: { value: THREE.Texture | null };
  uReliefTexel: { value: THREE.Vector2 };
  uCellKm: { value: number };
  uDefaultDepth: { value: number };
  uPixelRatio: { value: number };
  uScreenScale: { value: number };
  uBake: { value: THREE.Texture | null };
};

export type RiverPass = "core" | "rim-light" | "rim-dark";

/**
 * River core material, or one of the two rim passes drawn beneath it. Rims use
 * MAX (light) and MIN (dark) blending, so each only ever pushes the ground away
 * from the river's value, and overlapping round caps at joints don't bead.
 */
export const createRiverMaterial = (
  uniforms: RiverUniforms,
  atmosphere: AtmosphereUniforms,
  pass: RiverPass = "core",
) =>
  new THREE.ShaderMaterial({
    glslVersion: THREE.GLSL3,
    uniforms: { ...atmosphere, ...uniforms },
    vertexShader,
    fragmentShader,
    defines: pass === "core" ? {} : { CASING: pass === "rim-light" ? 1 : 2 },
    transparent: true,
    depthTest: true,
    depthWrite: false,
    ...(pass === "core"
      ? {}
      : {
          blending: THREE.CustomBlending,
          blendEquation: pass === "rim-light" ? THREE.MaxEquation : THREE.MinEquation,
        }),
  });

/** One instanced quad per segment; the vertex shader extrudes it on screen. */
export const createRiverMesh = (segments: RiverSegments, material: THREE.ShaderMaterial) => {
  const geometry = new THREE.InstancedBufferGeometry();
  const quad = new Float32Array([0, -1, 0, 1, -1, 0, 1, 1, 0, 0, 1, 0]);
  geometry.setAttribute("position", new THREE.BufferAttribute(quad, 3));
  geometry.setIndex([0, 1, 2, 0, 2, 3]);
  geometry.setAttribute("aEnds", new THREE.InstancedBufferAttribute(segments.ends, 4));
  geometry.setAttribute("aDist", new THREE.InstancedBufferAttribute(segments.dist, 2));
  geometry.setAttribute("aWidth", new THREE.InstancedBufferAttribute(segments.width, 1));
  geometry.setAttribute("aSlot", new THREE.InstancedBufferAttribute(segments.slot, 1));
  geometry.instanceCount = segments.count;
  const mesh = new THREE.Mesh(geometry, material);
  mesh.frustumCulled = false;
  return mesh;
};
