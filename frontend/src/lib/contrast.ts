/** WCAG contrast, and the colour tokens read from the stylesheet, for the contrast test. */

export type Tokens = Record<string, string>;

const HEX = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i;

/** WCAG 2.x relative luminance of a `#rrggbb` colour. */
function luminance(color: string): number {
  const match = HEX.exec(color);
  if (!match) throw new Error(`Not a #rrggbb colour: ${color}`);
  const [r, g, b] = match.slice(1).map((pair) => {
    const channel = parseInt(pair, 16) / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** How far apart two colours are, from 1 (the same) to 21 (black on white). */
export function contrastRatio(foreground: string, background: string): number {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (lighter! + 0.05) / (darker! + 0.05);
}

/** The declarations of the first rule whose selector is exactly `selector`. */
function block(css: string, selector: string): Tokens {
  const escaped = selector.replace(/[[\]().:"=]/g, "\\$&");
  const match = new RegExp(`(?:^|[\\s}])${escaped}\\s*\\{([^}]*)\\}`).exec(css);
  if (!match) throw new Error(`No ${selector} block in the stylesheet`);
  const tokens: Tokens = {};
  for (const [, name, value] of match[1]!.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    tokens[name!] = value!.trim();
  }
  return tokens;
}

/** The light theme, and the dark theme as written for the system setting and for the switch. */
export function tokenBlocks(css: string): { light: Tokens; darkMedia: Tokens; darkAttr: Tokens } {
  const media = /@media \(prefers-color-scheme: dark\)\s*\{([\s\S]*?\})\s*\}/.exec(css);
  if (!media) throw new Error("No dark-mode @media block in the stylesheet");
  return {
    light: block(css, ":root"),
    darkMedia: block(media[1]!, ':root:not([data-theme="light"])'),
    darkAttr: block(css, ':root[data-theme="dark"]'),
  };
}
