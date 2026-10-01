import * as THREE from "three";

import { type Payload, u8 } from "./payload.ts";

export type FlowYearMeta = { year: number; day0: number; days: number; slots: number };

/**
 * Daily colour codes for every gauge slot, assembled from yearly chunks into
 * one R8 texture: x = day, y = slot. When slots exceed the GPU's texture
 * height, rows fold into side-by-side blocks of `days` columns.
 */
export class FlowStore {
  readonly days: number;
  readonly slots: number;
  readonly foldRows: number;
  readonly folds: number;
  readonly codes: Uint8Array;
  readonly texture: THREE.DataTexture;
  readonly loadedYears = new Set<number>();

  constructor(days: number, slots: number, maxTextureSize: number) {
    this.days = days;
    this.slots = slots;
    this.folds = Math.max(1, Math.ceil(slots / maxTextureSize));
    if (this.folds * days > maxTextureSize) {
      throw new Error("Flow data exceeds this GPU's texture size");
    }
    this.foldRows = Math.ceil(slots / this.folds);
    this.codes = new Uint8Array(slots * days);
    const texels = new Uint8Array(this.folds * days * this.foldRows);
    this.texture = new THREE.DataTexture(
      texels,
      this.folds * days,
      this.foldRows,
      THREE.RedFormat,
      THREE.UnsignedByteType,
    );
    this.texture.magFilter = THREE.NearestFilter;
    this.texture.minFilter = THREE.NearestFilter;
    this.texture.unpackAlignment = 1;
    this.texture.needsUpdate = true;
  }

  /** Write one year's delta-coded codes (rows = slots, columns = days). */
  addYear(payload: Payload<FlowYearMeta>) {
    const { day0, days, slots } = payload.meta;
    if (days === 0) return; // a spare year cell: no data for it
    if (slots !== this.slots) throw new Error("Flow chunk slot count changed");
    const delta = u8(payload, "codes");
    const texels = this.texture.image.data as Uint8Array;
    const width = this.folds * this.days;
    for (let slot = 0; slot < slots; slot += 1) {
      const fold = Math.floor(slot / this.foldRows);
      const row = slot - fold * this.foldRows;
      const out = row * width + fold * this.days + day0;
      const codesOut = slot * this.days + day0;
      let value = 0;
      for (let d = 0; d < days; d += 1) {
        value = (value + delta[slot * days + d]) & 0xff;
        texels[out + d] = value;
        this.codes[codesOut + d] = value;
      }
    }
    this.loadedYears.add(payload.meta.year);
    this.texture.needsUpdate = true;
  }

  code(slot: number, day: number) {
    return this.codes[slot * this.days + Math.min(this.days - 1, Math.max(0, day))];
  }
}

/** Colour code (1..levels) to log2 ratio vs normal; 0 means no reading. */
export const codeToLog2 = (code: number, levels: number, range: number) =>
  ((code - 1) / (levels - 1)) * 2 * range - range;
