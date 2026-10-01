import * as THREE from "three";

import { ATMOSPHERE_GLSL, type AtmosphereUniforms } from "./glsl.ts";

const skyVertex = /* glsl */ `
  out vec3 vDir;
  void main() {
    vDir = position;
    vec4 world = modelMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const skyFragment = /* glsl */ `
  precision highp float;
  ${ATMOSPHERE_GLSL}
  in vec3 vDir;
  out vec4 outColor;
  void main() {
    vec3 dir = normalize(vDir);
    vec3 color = dir.y > 0.0 ? skyColor(dir) : uHazeColor;
    color = mix(uHazeColor, color, smoothstep(0.0, 0.12, dir.y));
    outColor = vec4(color, 1.0);
  }
`;

/** Sky dome that follows the camera; drawn first, behind everything. */
export const createSky = (atmosphere: AtmosphereUniforms) => {
  const material = new THREE.ShaderMaterial({
    glslVersion: THREE.GLSL3,
    uniforms: { ...atmosphere },
    vertexShader: skyVertex,
    fragmentShader: skyFragment,
    side: THREE.BackSide,
    depthTest: false,
    depthWrite: false,
  });
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 24), material);
  mesh.renderOrder = -10;
  mesh.frustumCulled = false;
  return mesh;
};

const cloudVertex = /* glsl */ `
  out vec3 vWorld;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const cloudFragment = /* glsl */ `
  precision highp float;
  ${ATMOSPHERE_GLSL}
  uniform vec2 uCenter;
  uniform float uLift;
  uniform float uLayerAlpha;
  uniform float uFade;
  uniform sampler2D uSurface;
  uniform vec4 uExtent;
  in vec3 vWorld;
  out vec4 outColor;
  void main() {
    vec2 p = vec2(vWorld.x + uCenter.x, uCenter.y - vWorld.z);
    // Clouds gather over sea and neighbours; over US land (the data) they thin to a veil.
    vec2 g = (p - uExtent.xy) / uExtent.zw;
    float land = textureLod(uSurface, clamp(vec2(g.x, 1.0 - g.y), 0.0, 1.0), 5.0).r;
    float thin = 1.0 - 0.7 * smoothstep(0.1, 0.5, land);
    vec2 q = cloudCoord(p);
    vec2 warp = cloudWarp(q);
    float detail = fbm3(q * 4.1 - uWind * uTime * 2.0);
    float d = cloudCover(fbm(q + warp), detail, uLift) * thin;
    if (d < 0.01) discard;
    // Brighter on the side facing the sun, blue-grey where the puff thickens
    // away from it (same warp and detail, base shape shifted toward the sun).
    vec2 toSun = normalize(vec2(uSunDir.x, -uSunDir.z));
    float toward = cloudCover(fbm(q + toSun * (70.0 / 950.0) + warp), detail, uLift) * thin;
    float shade = clamp(1.0 - max(toward - d, 0.0) * 1.8 + uLift * 2.5, 0.0, 1.0);
    vec3 color = mix(vec3(0.74, 0.79, 0.88), vec3(1.0, 0.985, 0.955), shade);
    color = applyHaze(color, vWorld);
    outColor = vec4(color, d * uLayerAlpha * uFade);
  }
`;

export type CloudLayers = {
  group: THREE.Group;
  setAltitude: (km: number) => void;
  setFade: (fade: number) => void;
  setSurface: (texture: THREE.Texture) => void;
  dispose: () => void;
};

/** Three stacked, thinning sheets read as puffy volume when the camera tilts. */
export const createClouds = (
  extent: [number, number, number, number],
  atmosphere: AtmosphereUniforms,
): CloudLayers => {
  const group = new THREE.Group();
  const size = Math.max(extent[2] - extent[0], extent[3] - extent[1]) * 2.2;
  const geometry = new THREE.PlaneGeometry(size, size, 1, 1);
  const center = new THREE.Vector2((extent[0] + extent[2]) / 2, (extent[1] + extent[3]) / 2);
  const fade = { value: 1 };
  const none = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1);
  none.needsUpdate = true;
  const surface = { value: none as THREE.Texture };
  const extentKm = {
    value: new THREE.Vector4(extent[0], extent[1], extent[2] - extent[0], extent[3] - extent[1]),
  };
  const materials = [0, 1, 2].map((k) => {
    const material = new THREE.ShaderMaterial({
      glslVersion: THREE.GLSL3,
      uniforms: {
        ...atmosphere,
        uCenter: { value: center },
        uLift: { value: k * 0.045 },
        uLayerAlpha: { value: [0.24, 0.16, 0.1][k] },
        uFade: fade,
        uSurface: surface,
        uExtent: extentKm,
      },
      vertexShader: cloudVertex,
      fragmentShader: cloudFragment,
      transparent: true,
      depthWrite: false,
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.rotation.x = -Math.PI / 2;
    mesh.renderOrder = 10 + k;
    mesh.frustumCulled = false;
    group.add(mesh);
    return material;
  });
  return {
    group,
    setAltitude: (km: number) => {
      group.children.forEach((child, k) => {
        child.position.y = km + k * km * 0.07;
      });
    },
    setFade: (value: number) => {
      fade.value = value;
      group.visible = value > 0.001;
    },
    setSurface: (texture: THREE.Texture) => {
      surface.value = texture;
    },
    dispose: () => {
      geometry.dispose();
      none.dispose();
      materials.forEach((m) => m.dispose());
    },
  };
};
