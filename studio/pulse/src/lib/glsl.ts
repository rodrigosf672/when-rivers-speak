import * as THREE from "three";

/**
 * Shared look: one sun, one sky, one cloud field and one haze, used by every
 * material so terrain, water, rivers and clouds agree. All GLSL here is
 * GLSL 3 (three's `glslVersion: THREE.GLSL3`).
 */

// Sun from the north-west (the cartographic convention, so relief reads the
// right way up), fairly low for long, soft game-like shadows.
const SUN_AZIMUTH = THREE.MathUtils.degToRad(318);
const SUN_ELEVATION = THREE.MathUtils.degToRad(31);
export const SUN_DIRECTION = new THREE.Vector3(
  Math.sin(SUN_AZIMUTH) * Math.cos(SUN_ELEVATION),
  Math.sin(SUN_ELEVATION),
  -Math.cos(SUN_AZIMUTH) * Math.cos(SUN_ELEVATION),
).normalize();

/** Share of relief kept for other countries' land (a GLSL float literal). */
export const OTHER_RELIEF = "0.12";

export type AtmosphereUniforms = {
  uSunDir: { value: THREE.Vector3 };
  uSunColor: { value: THREE.Color };
  uAmbient: { value: THREE.Color };
  uSkyZenith: { value: THREE.Color };
  uSkyHorizon: { value: THREE.Color };
  uHazeColor: { value: THREE.Color };
  uHazeStart: { value: number };
  uHazeDensity: { value: number };
  uTime: { value: number };
  uWind: { value: THREE.Vector2 };
  uCloudCoverage: { value: number };
  uCloudAltitude: { value: number };
  uCloudShadow: { value: number };
  uZoom: { value: number };
};

export const createAtmosphere = (): AtmosphereUniforms => ({
  uSunDir: { value: SUN_DIRECTION.clone() },
  uSunColor: { value: new THREE.Color("#ffe8c4") },
  uAmbient: { value: new THREE.Color("#a6add6") },
  uSkyZenith: { value: new THREE.Color("#6f9fd8") },
  uSkyHorizon: { value: new THREE.Color("#dfe8f1") },
  uHazeColor: { value: new THREE.Color("#d6e2ee") },
  uHazeStart: { value: 6000 },
  uHazeDensity: { value: 0.00012 },
  uTime: { value: 0 },
  uWind: { value: new THREE.Vector2(0.012, -0.004) },
  uCloudCoverage: { value: 0.67 },
  uCloudAltitude: { value: 180 },
  uCloudShadow: { value: 1 },
  uZoom: { value: 1 },
});

export const ATMOSPHERE_GLSL = /* glsl */ `
  uniform vec3 uSunDir;
  uniform vec3 uSunColor;
  uniform vec3 uAmbient;
  uniform vec3 uSkyZenith;
  uniform vec3 uSkyHorizon;
  uniform vec3 uHazeColor;
  uniform float uHazeStart;
  uniform float uHazeDensity;
  uniform float uTime;
  uniform vec2 uWind;
  uniform float uCloudCoverage;
  uniform float uCloudAltitude;
  uniform float uCloudShadow;
  uniform float uZoom;

  float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
  }

  float valueNoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    float a = hash12(i);
    float b = hash12(i + vec2(1.0, 0.0));
    float c = hash12(i + vec2(0.0, 1.0));
    float d = hash12(i + vec2(1.0, 1.0));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
  }

  float fbm(vec2 p) {
    float sum = 0.0;
    float amp = 0.5;
    mat2 turn = mat2(1.6, 1.2, -1.2, 1.6);
    for (int i = 0; i < 5; i++) {
      sum += amp * valueNoise(p);
      p = turn * p + 17.3;
      amp *= 0.5;
    }
    return sum;
  }

  // Three octaves, for warps and detail where the full five are wasted.
  float fbm3(vec2 p) {
    mat2 turn = mat2(1.6, 1.2, -1.2, 1.6);
    float sum = 0.5 * valueNoise(p);
    p = turn * p + 17.3;
    sum += 0.25 * valueNoise(p);
    p = turn * p + 17.3;
    return (sum + 0.125 * valueNoise(p)) / 0.875;
  }

  vec3 skyColor(vec3 dir) {
    float up = clamp(dir.y, 0.0, 1.0);
    vec3 sky = mix(uSkyHorizon, uSkyZenith, pow(up, 0.55));
    float sun = max(dot(normalize(dir), uSunDir), 0.0);
    sky += uSunColor * (pow(sun, 8.0) * 0.25 + pow(sun, 400.0) * 1.5);
    return sky;
  }

  // Sparse, slowly rolling cloud cover. The field is a domain-warped fbm in
  // drifting coordinates; lift raises the threshold for thinner upper layers.
  vec2 cloudCoord(vec2 p) {
    return p / 950.0 + uWind * uTime;
  }

  vec2 cloudWarp(vec2 q) {
    return vec2(fbm3(q * 1.3 + 3.1), fbm3(q * 1.3 - 7.7)) * 0.55;
  }

  float cloudCover(float base, float detail, float lift) {
    float threshold = uCloudCoverage + lift;
    return smoothstep(threshold, threshold + 0.24, base * 0.78 + detail * 0.32);
  }

  float cloudDensity(vec2 p, float lift) {
    vec2 q = cloudCoord(p);
    float base = fbm(q + cloudWarp(q));
    float detail = fbm3(q * 4.1 - uWind * uTime * 2.0);
    return cloudCover(base, detail, lift);
  }

  // Cloud shadow on the ground: look up the cloud the sun ray passes through
  // (base shape only; shadows don't need the fine detail).
  float cloudShadowAt(vec3 world, vec2 center) {
    if (uCloudShadow < 0.01) return 1.0;
    vec2 p = vec2(world.x + center.x, center.y - world.z);
    float climb = max(uCloudAltitude - world.y, 0.0) / max(uSunDir.y, 0.1);
    vec2 toSun = normalize(vec2(uSunDir.x, -uSunDir.z));
    vec2 q = cloudCoord(p + toSun * climb);
    float d = cloudCover(fbm(q + cloudWarp(q)), 0.5, 0.0);
    return 1.0 - 0.2 * d * uCloudShadow;
  }

  // k < 1 lets data (rivers) punch through haze the ground takes in full.
  vec3 applyHaze(vec3 color, vec3 world, float k) {
    float dist = length(world - cameraPosition);
    float haze = 1.0 - exp(-max(dist - uHazeStart, 0.0) * uHazeDensity);
    return mix(color, uHazeColor, clamp(haze, 0.0, 0.9) * k);
  }

  vec3 applyHaze(vec3 color, vec3 world) {
    return applyHaze(color, world, 1.0);
  }

  float fresnel(float cosTheta) {
    return 0.02 + 0.98 * pow(1.0 - clamp(cosTheta, 0.0, 1.0), 5.0);
  }

  // Scrolling wave normal for water at map position p (km); scale in km sets
  // the wavelength so waves stay a readable size at any zoom. Three rotated
  // layers moving in different directions avoid a regular pattern.
  float waveHeight(vec2 p, float scale) {
    float t = uTime;
    mat2 r1 = mat2(0.8, 0.6, -0.6, 0.8);
    mat2 r2 = mat2(0.28, -0.96, 0.96, 0.28);
    float h = valueNoise(p / scale + vec2(t * 0.31, t * 0.17));
    h += 0.55 * valueNoise(r1 * p / (scale * 0.47) + vec2(-t * 0.23, t * 0.38));
    h += 0.3 * valueNoise(r2 * p / (scale * 0.21) + vec2(t * 0.52, -t * 0.12));
    return h;
  }

  vec3 waveNormal(vec2 p, float scale, float strength) {
    float e = scale * 0.06;
    float h0 = waveHeight(p, scale);
    vec2 g = vec2(waveHeight(p + vec2(e, 0.0), scale) - h0, waveHeight(p + vec2(0.0, e), scale) - h0);
    g *= strength * scale / e;
    // Map y (north) is world -z.
    return normalize(vec3(-g.x, 1.0, g.y));
  }
`;
