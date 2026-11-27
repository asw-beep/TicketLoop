import { motifFor, posterArt, seeded, VIEWBOX, type Shape } from "./poster";

describe("motifFor", () => {
  it("draws each category its own motif", () => {
    for (const category of ["CONCERT", "SPORTS", "COLLEGE", "OTHER"]) {
      expect(motifFor(category)).toBe(category);
    }
    expect(motifFor("concert")).toBe("CONCERT");
  });

  it("draws a category it does not know as OTHER", () => {
    expect(motifFor("FESTIVAL")).toBe("OTHER");
  });

  it("draws a missing category as PLAIN", () => {
    expect(motifFor(undefined)).toBe("PLAIN");
    expect(motifFor(null)).toBe("PLAIN");
    expect(motifFor("")).toBe("PLAIN");
  });
});

describe("seeded", () => {
  const take = (random: () => number) => Array.from({ length: 10 }, random);

  it("gives the same numbers for the same seed", () => {
    expect(take(seeded("a"))).toEqual(take(seeded("a")));
  });

  it("stays in [0, 1)", () => {
    for (const value of take(seeded("a"))) {
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThan(1);
    }
  });

  it("gives other numbers for another seed", () => {
    expect(take(seeded("b"))).not.toEqual(take(seeded("a")));
  });
});

describe("posterArt", () => {
  it("draws the same event the same way every time", () => {
    expect(posterArt({ id: "e1", category: "SPORTS" })).toEqual(
      posterArt({ id: "e1", category: "SPORTS" }),
    );
  });

  it("draws every event differently", () => {
    const drawn = Array.from({ length: 20 }, (_, n) =>
      JSON.stringify(posterArt({ id: `event-${n}`, category: "CONCERT" })),
    );
    expect(new Set(drawn).size).toBe(20);
  });

  it("draws another category as another motif", () => {
    const concert = posterArt({ id: "e1", category: "CONCERT" });
    const sports = posterArt({ id: "e1", category: "SPORTS" });
    expect(concert.motif).not.toBe(sports.motif);
    expect(concert.layers).not.toEqual(sports.layers);
  });

  const numbers = (shape: Shape): number[] =>
    Object.values(shape).flatMap((value) =>
      typeof value === "number"
        ? [value]
        : Array.isArray(value)
          ? (value as [number, number][]).flat()
          : [],
    );

  it.each(["CONCERT", "SPORTS", "COLLEGE", "OTHER", undefined])(
    "prints %s in two inks, one in halftone, inside the sheet",
    (category) => {
      for (let n = 0; n < 10; n++) {
        const art = posterArt({ id: `id-${n}`, category });
        expect(art.layers.map((layer) => layer.ink).sort()).toEqual(["blue", "pink"]);
        expect(art.layers.filter((layer) => layer.halftone)).toHaveLength(1);
        for (const layer of art.layers) {
          expect(layer.shapes.length).toBeGreaterThan(0);
          for (const value of layer.shapes.flatMap(numbers)) {
            expect(Number.isFinite(value)).toBe(true);
            expect(value).toBeGreaterThanOrEqual(-VIEWBOX / 2);
            expect(value).toBeLessThanOrEqual(VIEWBOX * 1.5);
          }
        }
        for (const offset of [art.offset.dx, art.offset.dy]) {
          expect(Number.isInteger(offset)).toBe(true);
          expect(Math.abs(offset)).toBeGreaterThanOrEqual(2);
          expect(Math.abs(offset)).toBeLessThanOrEqual(4);
        }
      }
    },
  );
});
