import type { MarimoTable } from "./marimo-value.ts";

/** One named array from the notebook's `pack_sections` tables. */
export type Section = {
  name: string;
  dtype: string;
  shape: number[];
  bytes: Uint8Array;
};

export type Payload<Meta = Record<string, unknown>> = {
  meta: Meta;
  sections: Map<string, Section>;
};

type PackedRow = {
  name: string;
  dtype: string;
  shape: string;
  codec: string;
  blob: Uint8Array;
};

export const inflate = async (bytes: Uint8Array): Promise<Uint8Array> => {
  const input = new Blob([bytes.slice() as Uint8Array<ArrayBuffer>]);
  const stream = input.stream().pipeThrough(new DecompressionStream("deflate"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
};

/** Decode a packed table: inflate zlib sections and parse the JSON meta row. */
export const decodePayload = async <Meta = Record<string, unknown>>(
  table: MarimoTable,
): Promise<Payload<Meta>> => {
  const rows = table.toArray() as unknown as PackedRow[];
  const sections = new Map<string, Section>();
  let meta = {} as Meta;
  await Promise.all(
    rows.map(async (row) => {
      const bytes = row.codec === "zlib" ? await inflate(row.blob) : row.blob.slice();
      if (row.name === "meta") {
        meta = JSON.parse(new TextDecoder().decode(bytes)) as Meta;
        return;
      }
      sections.set(row.name, {
        name: row.name,
        dtype: row.dtype,
        shape: JSON.parse(row.shape) as number[],
        bytes,
      });
    }),
  );
  return { meta, sections };
};

const section = (payload: Payload<unknown>, name: string): Section => {
  const found = payload.sections.get(name);
  if (!found) throw new Error(`Payload is missing section ${name}`);
  return found;
};

const view = <T>(
  payload: Payload<unknown>,
  name: string,
  dtype: string,
  make: (buffer: ArrayBuffer, offset: number, length: number) => T,
  bytesPer: number,
): T => {
  const found = section(payload, name);
  if (found.dtype !== dtype) {
    throw new Error(`Section ${name} is ${found.dtype}, expected ${dtype}`);
  }
  const { buffer, byteOffset, byteLength } = found.bytes;
  return make(buffer as ArrayBuffer, byteOffset, byteLength / bytesPer);
};

export const u8 = (p: Payload<unknown>, name: string) =>
  view(p, name, "|u1", (b, o, n) => new Uint8Array(b, o, n), 1);
export const u16 = (p: Payload<unknown>, name: string) =>
  view(p, name, "<u2", (b, o, n) => new Uint16Array(b, o, n), 2);
export const f32 = (p: Payload<unknown>, name: string) =>
  view(p, name, "<f4", (b, o, n) => new Float32Array(b, o, n), 4);
export const bytes = (p: Payload<unknown>, name: string) => section(p, name).bytes;
export const shapeOf = (p: Payload<unknown>, name: string) => section(p, name).shape;
