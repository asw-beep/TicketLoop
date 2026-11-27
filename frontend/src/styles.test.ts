/**
 * The stylesheet's colour tokens, held to WCAG AA in both themes. A token change that makes
 * text hard to read fails here, not in someone's eyes.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { contrastRatio, tokenBlocks, type Tokens } from "./lib/contrast";

// Read from disk: Vitest serves .css imports as empty strings, `?raw` included.
const css = readFileSync(join(import.meta.dirname, "styles.css"), "utf8");

const { light, darkMedia, darkAttr } = tokenBlocks(css);
const THEMES: Record<string, Tokens> = { light, dark: { ...light, ...darkAttr } };

const BACKGROUNDS = ["--paper", "--surface", "--field"];
const TEXT = ["--ink", "--ink-soft", "--ink-faint", "--blue-deep", "--danger", "--ok"];

/** Body text, labels, prices, badges and button text: 4.5:1. */
const TEXT_PAIRS: [string, string][] = [
  ...TEXT.flatMap((text) => BACKGROUNDS.map((bg): [string, string] => [text, bg])),
  ["--ink", "--danger-soft"],
  ["--ink", "--ok-soft"],
  ["--on-ink", "--ink"], // primary buttons
  ["--on-stub", "--stub"], // the ticket stub
  ["--on-yellow", "--yellow"], // the highlighter
];

/** Display type only (the countdown): 3:1. */
const LARGE_PAIRS: [string, string][] = [
  ["--blue", "--paper"],
  ["--blue", "--surface"],
];

it("the dark theme is written the same both times", () => {
  expect(darkAttr).toEqual(darkMedia);
});

it("every token the stylesheet uses is defined", () => {
  // Properties a component sets inline (--dx, --i …) always carry a fallback, so skip those.
  const used = new Set([...css.matchAll(/var\((--[a-z0-9-]+)\)/g)].map((match) => match[1]));
  const missing = [...used].filter((name) => !(name! in light));
  expect(missing).toEqual([]);
});

it.each(Object.keys(THEMES))("text is readable in the %s theme", (theme) => {
  const tokens = THEMES[theme]!;
  const hex = (name: string) => {
    const value = tokens[name];
    expect(value, `${name} in ${theme}`).toMatch(/^#[0-9a-f]{6}$/i);
    return value!;
  };
  const failures = [
    ...TEXT_PAIRS.map(([fg, bg]) => [fg, bg, 4.5] as const),
    ...LARGE_PAIRS.map(([fg, bg]) => [fg, bg, 3] as const),
  ]
    .map(([fg, bg, needed]) => ({ fg, bg, needed, ratio: contrastRatio(hex(fg), hex(bg)) }))
    .filter(({ ratio, needed }) => ratio < needed)
    .map(({ fg, bg, needed, ratio }) => `${fg} on ${bg}: ${ratio.toFixed(2)} < ${needed}`);
  expect(failures).toEqual([]);
});
