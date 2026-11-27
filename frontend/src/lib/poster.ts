/**
 * A two-ink poster for every event, drawn from its id and category. Events have no pictures,
 * so each gets one generated the way a riso print is made: two layers of shapes, one ink each,
 * one in halftone, the top one printed a few pixels out of register. Pure: the same event
 * always gets the same poster.
 */

export type Motif = "CONCERT" | "SPORTS" | "COLLEGE" | "OTHER" | "PLAIN";
export interface PosterInput {
  id: string;
  category?: string | null;
}
export type Ink = "pink" | "blue";
export type Shape =
  | { kind: "circle"; cx: number; cy: number; r: number; stroke?: number } // stroke set: a ring
  | { kind: "rect"; x: number; y: number; width: number; height: number; rotate: number }
  | { kind: "polygon"; points: [number, number][] }
  | { kind: "line"; x1: number; y1: number; x2: number; y2: number; stroke: number };
export interface Layer {
  ink: Ink;
  halftone: boolean;
  shapes: Shape[];
}
export interface PosterArt {
  motif: Motif;
  layers: [Layer, Layer]; // [0] printed first, [1] overprinted on top
  offset: { dx: number; dy: number }; // whole px, the top layer's misregistration
}

/** Art is drawn in a VIEWBOX × VIEWBOX box. */
export const VIEWBOX = 100;

/** The front page's own poster: not any event's, the same for everyone. */
export const HOUSE_POSTER: PosterInput = { id: "ticketloop-front-page", category: "CONCERT" };

const MOTIFS = new Set<Motif>(["CONCERT", "SPORTS", "COLLEGE", "OTHER"]);

export function motifFor(category?: string | null): Motif {
  if (!category) return "PLAIN";
  const upper = category.toUpperCase() as Motif;
  return MOTIFS.has(upper) ? upper : "OTHER";
}

/** A deterministic random stream: FNV-1a over the seed, then mulberry32. Values in [0, 1). */
export function seeded(seed: string): () => number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < seed.length; i++) {
    hash ^= seed.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  let state = hash >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Random = () => number;
/** Two decimals is plenty for a 100-unit sheet and keeps the markup short. */
const round = (n: number) => Math.round(n * 100) / 100;
const between = (random: Random, min: number, max: number) => round(min + random() * (max - min));
const whole = (random: Random, min: number, max: number) =>
  min + Math.floor(random() * (max - min + 1));
const sign = (random: Random) => (random() < 0.5 ? -1 : 1);

/** Points around a centre: `spikes` outer points alternating with inner ones. */
function star(cx: number, cy: number, outer: number, inner: number, spikes: number, turn: number) {
  return Array.from({ length: spikes * 2 }, (_, i): [number, number] => {
    const r = i % 2 === 0 ? outer : inner;
    const angle = turn + (Math.PI * i) / spikes;
    return [round(cx + r * Math.sin(angle)), round(cy - r * Math.cos(angle))];
  });
}

/** Concentric rings off-centre, and a row of sound bars with a disc. */
function concert(random: Random): [Shape[], Shape[]] {
  const cx = between(random, 25, 75);
  const cy = between(random, 25, 75);
  const step = between(random, 6, 10);
  const stroke = between(random, 2.5, 5);
  const rings: Shape[] = Array.from({ length: whole(random, 4, 7) }, (_, i) => ({
    kind: "circle",
    cx,
    cy,
    r: round(step * (i + 1)),
    stroke,
  }));
  rings.push({ kind: "circle", cx, cy, r: between(random, 3, 6) });

  const bars = whole(random, 6, 10);
  const base = between(random, 60, 85);
  const width = between(random, 3, 5);
  const sound: Shape[] = Array.from({ length: bars }, (_, i) => {
    const height = between(random, 10, 45);
    return {
      kind: "rect",
      x: round(8 + (i * (84 - width)) / (bars - 1)),
      y: round(base - height),
      width,
      height,
      rotate: 0,
    };
  });
  sound.push({
    kind: "circle",
    cx: between(random, 20, 80),
    cy: between(random, 15, 45),
    r: between(random, 12, 20),
  });
  return [rings, sound];
}

/** Track lanes on a slope, and broad diagonal stripes with a ball. */
function sports(random: Random): [Shape[], Shape[]] {
  const slope = between(random, -30, 30);
  const first = between(random, 10, 30);
  const gap = between(random, 8, 12);
  const stroke = between(random, 1.5, 2.5);
  const lanes: Shape[] = Array.from({ length: whole(random, 5, 7) }, (_, i) => {
    const y = round(first + i * gap);
    return { kind: "line", x1: -20, y1: y, x2: 120, y2: round(y + slope), stroke };
  });

  const angle = between(random, 35, 55) * sign(random);
  const thickness = between(random, 8, 14);
  const stripes: Shape[] = Array.from({ length: whole(random, 3, 5) }, (_, i) => ({
    kind: "rect",
    x: -20,
    y: round(10 + i * between(random, 18, 26)),
    width: 140,
    height: thickness,
    rotate: angle,
  }));
  stripes.push({
    kind: "circle",
    cx: between(random, 20, 80),
    cy: between(random, 20, 80),
    r: between(random, 8, 14),
  });
  return [lanes, stripes];
}

/** Blocks on a coarse grid, and pennants on a pole. */
function college(random: Random): [Shape[], Shape[]] {
  const blocks: Shape[] = [];
  for (let row = 0; row < 5; row++) {
    for (let col = 0; col < 5; col++) {
      if (random() < 0.4) {
        blocks.push({
          kind: "rect",
          x: col * 20 + 2,
          y: row * 20 + 2,
          width: 16,
          height: 16,
          rotate: 0,
        });
      }
    }
  }
  if (blocks.length === 0)
    blocks.push({ kind: "rect", x: 42, y: 42, width: 16, height: 16, rotate: 0 });

  const pole = between(random, 12, 30);
  const pennants: Shape[] = [{ kind: "rect", x: pole, y: 8, width: 2.5, height: 84, rotate: 0 }];
  let top = between(random, 10, 20);
  for (let i = 0, count = whole(random, 2, 4); i < count; i++) {
    const width = between(random, 30, 55);
    const height = between(random, 14, 22);
    const x = pole + 2.5;
    pennants.push({
      kind: "polygon",
      points: [
        [x, top],
        [x, round(top + height)],
        [round(x + width), round(top + height / 2)],
      ],
    });
    top = round(top + height + between(random, 4, 10));
  }
  return [blocks, pennants];
}

/** Soft blobs, and stars. */
function other(random: Random): [Shape[], Shape[]] {
  const blobs: Shape[] = Array.from({ length: whole(random, 2, 3) }, () => {
    const cx = between(random, 20, 80);
    const cy = between(random, 20, 80);
    const r = between(random, 15, 30);
    const count = whole(random, 10, 14);
    return {
      kind: "polygon",
      points: Array.from({ length: count }, (_, i): [number, number] => {
        const angle = (2 * Math.PI * i) / count;
        const reach = r * (0.75 + random() * 0.5);
        return [round(cx + reach * Math.cos(angle)), round(cy + reach * Math.sin(angle))];
      }),
    };
  });
  const stars: Shape[] = Array.from({ length: whole(random, 2, 4) }, () => {
    const outer = between(random, 8, 18);
    return {
      kind: "polygon",
      points: star(
        between(random, 15, 85),
        between(random, 15, 85),
        outer,
        round(outer * 0.45),
        5,
        random() * Math.PI,
      ),
    };
  });
  return [blobs, stars];
}

/** No category to go on: halftone bands and one star. */
function plain(random: Random): [Shape[], Shape[]] {
  let y = between(random, 0, 12);
  const bands: Shape[] = [];
  for (let i = 0, count = whole(random, 3, 5); i < count; i++) {
    const height = between(random, 8, 16);
    bands.push({ kind: "rect", x: -10, y, width: 120, height, rotate: 0 });
    y = round(y + height + between(random, 6, 14));
  }
  const outer = between(random, 14, 24);
  return [
    bands,
    [
      {
        kind: "polygon",
        points: star(
          between(random, 30, 70),
          between(random, 30, 70),
          outer,
          round(outer * 0.45),
          5,
          random() * Math.PI,
        ),
      },
    ],
  ];
}

const DRAW: Record<Motif, (random: Random) => [Shape[], Shape[]]> = {
  CONCERT: concert,
  SPORTS: sports,
  COLLEGE: college,
  OTHER: other,
  PLAIN: plain,
};

export function posterArt({ id, category }: PosterInput): PosterArt {
  const random = seeded(id);
  const motif = motifFor(category);
  const [first, second] = DRAW[motif](random);
  const top: Ink = random() < 0.5 ? "pink" : "blue";
  const under: Ink = top === "pink" ? "blue" : "pink";
  // The bands are the point of the plain poster, so they are always the halftone.
  const halftoneFirst = motif === "PLAIN" || random() < 0.5;
  return {
    motif,
    layers: [
      { ink: under, halftone: halftoneFirst, shapes: first },
      { ink: top, halftone: !halftoneFirst, shapes: second },
    ],
    offset: { dx: sign(random) * whole(random, 2, 4), dy: sign(random) * whole(random, 2, 4) },
  };
}
