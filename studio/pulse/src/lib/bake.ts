import * as THREE from "three";

import { OTHER_RELIEF } from "./glsl.ts";

/**
 * Bake soft sun shadows (R) and ambient occlusion (G) for the relief into a
 * texture on the GPU. Re-run when the relief or its exaggeration changes; the
 * terrain then samples the result instead of marching every frame.
 */
const vertexShader = /* glsl */ `
  out vec2 vGrid;
  void main() {
    vGrid = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`;

const fragmentShader = /* glsl */ `
  precision highp float;
  uniform sampler2D uRelief;
  uniform sampler2D uSurface;
  uniform vec2 uSizeKm;
  uniform float uExaggeration;
  uniform vec3 uSunDir;
  in vec2 vGrid;
  out vec4 outColor;

  // Matches the terrain's geometry: other countries are flattened.
  float heightAt(vec2 g) {
    float us = smoothstep(0.3, 0.7, texture(uSurface, g).r);
    return texture(uRelief, g).r * uExaggeration * mix(${OTHER_RELIEF}, 1.0, us);
  }

  void main() {
    // Texel (u, v) of the bake holds grid (u, v): u runs east, v runs south,
    // matching world x (east) and world z (south).
    vec2 grid = vGrid;
    float h0 = heightAt(grid);
    vec2 toSun = normalize(vec2(uSunDir.x, uSunDir.z));
    // Cast as if the sun were ~42° high: shadows a third shorter so valleys
    // (where rivers run) stay open; shading keeps the 31° sun.
    float rise = uSunDir.y / length(uSunDir.xz) * 1.5;

    // Soft shadow: the tightest clearance of the sun ray over the terrain.
    float light = 1.0;
    float t = 3.0;
    for (int i = 0; i < 40; i++) {
      vec2 g = grid + toSun * t / uSizeKm;
      if (g.x < 0.0 || g.x > 1.0 || g.y < 0.0 || g.y > 1.0) break;
      float clearance = h0 + t * rise - heightAt(g);
      light = min(light, clamp(clearance / (t * 0.14) + 0.5, 0.0, 1.0));
      t *= 1.14;
    }

    // Ambient occlusion from how much the surroundings rise above this point.
    float occlusion = 0.0;
    for (int d = 0; d < 10; d++) {
      float angle = float(d) * 0.6283185 + 0.3;
      vec2 dir = vec2(cos(angle), sin(angle));
      float horizon = 0.0;
      for (int r = 1; r <= 3; r++) {
        float reach = 4.0 * pow(3.0, float(r - 1));
        float dh = heightAt(grid + dir * reach / uSizeKm) - h0;
        horizon = max(horizon, dh / reach);
      }
      occlusion += clamp(horizon * 0.6, 0.0, 1.0);
    }
    float ao = 1.0 - occlusion / 10.0;
    outColor = vec4(light, ao, 0.0, 1.0);
  }
`;

export class LightingBaker {
  readonly target: THREE.WebGLRenderTarget;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private readonly material: THREE.ShaderMaterial;

  constructor(width: number, height: number) {
    this.target = new THREE.WebGLRenderTarget(width, height, {
      type: THREE.UnsignedByteType,
      magFilter: THREE.LinearFilter,
      minFilter: THREE.LinearFilter,
      depthBuffer: false,
    });
    this.material = new THREE.ShaderMaterial({
      glslVersion: THREE.GLSL3,
      uniforms: {
        uRelief: { value: null },
        uSurface: { value: null },
        uSizeKm: { value: new THREE.Vector2(1, 1) },
        uExaggeration: { value: 1 },
        uSunDir: { value: new THREE.Vector3(0, 1, 0) },
      },
      vertexShader,
      fragmentShader,
      depthTest: false,
      depthWrite: false,
    });
    this.scene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.material));
  }

  bake(
    renderer: THREE.WebGLRenderer,
    relief: THREE.Texture,
    surface: THREE.Texture,
    sizeKm: THREE.Vector2,
    exaggeration: number,
    sunDir: THREE.Vector3,
  ) {
    const u = this.material.uniforms;
    u.uRelief.value = relief;
    u.uSurface.value = surface;
    u.uSizeKm.value.copy(sizeKm);
    u.uExaggeration.value = exaggeration;
    u.uSunDir.value.copy(sunDir);
    const previous = renderer.getRenderTarget();
    renderer.setRenderTarget(this.target);
    renderer.render(this.scene, this.camera);
    renderer.setRenderTarget(previous);
  }

  dispose() {
    this.target.dispose();
    this.material.dispose();
  }
}
