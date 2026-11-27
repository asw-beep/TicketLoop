# Riso redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the whole frontend in the riso zine look: warm paper, two misregistered inks, Bricolage Grotesque, and a generated poster per event. Every flow, word and test anchor stays as it is.

**Architecture:**
- **Look.** It comes from the colour tokens and component classes in `frontend/src/styles.css`, rewritten section by section.
- **Posters.** A pure generator in `src/lib/poster.ts` turns an event's id and category into two layers of shapes. A `Poster` component draws them as two stacked inline SVGs, so the misregistration is a CSS transform in real pixels.
- **Readability.** A Vitest test parses the token blocks in `styles.css` and holds every text pair to WCAG AA.
- **Phone widths, focus and reduced motion.** A new Playwright file checks these anti-goals in the browser.

**Tech Stack:** React 19.3.1, React Router 8.5.1, TanStack Query, Vite 8.3.6, plain CSS, Vitest 5.1 (jsdom), Playwright 1.65 on installed Chrome, `@fontsource-variable/bricolage-grotesque` 5.3.0, `@fontsource/space-mono` 5.3.0.

**Spec:** `docs/superpowers/specs/2026-11-27-riso-redesign-design.md`

## Global Constraints

- **No change to wording.** Headings, button labels, link text and `aria-label`s stay character for character. The one exception is the search page's intro sentence (Task 5 gives the new copy).
- **Test anchors survive.** These must still resolve:
  - every role and accessible name the browser tests use;
  - `list "Tickets on sale"` and every `listitem` `aria-label`;
  - `timer "Time left"`;
  - the badge inside `heading "Status"`, which the tests read with `toContainText`.
- **Free to change:** markup, element order and classes.
- **Fonts and dependencies.** Fonts are self-hosted through Fontsource, nothing loads from a CDN, and Archivo is removed. No CSS framework, animation library, canvas or WebGL.
- **Contrast.** Body text, labels and prices reach WCAG AA (4.5:1, or 3:1 for large text) in both themes. Fluorescent pink is never used as text colour, and no body text sits on poster art.
- **Posters hold no text nodes.** Playwright's `getByText(eventName)` must keep matching exactly one element, and the art stays `aria-hidden`.
- **Motion is CSS only.** Inks settle on first paint and shift on hover or focus, and nothing moves under `prefers-reduced-motion: reduce`. There's no full-page SVG filter, and the grain is a static texture.
- **Browsers.** Only CSS and SVG features in current Chrome, Firefox and Safari (`:has()`, `mix-blend-mode`, `clamp()`, `aspect-ratio`, SVG patterns).
- **No layout breaks at 360 px.** Focus stays clearly visible, and the navigation is never hidden behind a menu.
- **Branch and commits.**
  - Work on the branch `riso-redesign`, one commit per task, titled `Riso: <what>`.
  - The last task fast-forwards `main` and pushes it, which is the project's standing workflow.
  - No co-author, tool or AI attribution in any commit or file. Check each staged diff for attribution lines before committing.
- **Where to run things.**
  - Run `docker compose build`/`up` from PowerShell; Git Bash's bake path fails on Windows.
  - The browser tests need `docker compose up -d db mailpit minio` first, then run from `frontend/` with `npm run e2e`.

## Review Focus

1. **Text the tests find, duplicated or hidden by styling.** An event name drawn inside a poster, a CSS `text-transform` on a badge read with `exact: true`, or a pseudo-element repeating the countdown would each break a browser test or a screen reader. Posters render no text (Task 3 test), and badges and headings get no `text-transform`.
2. **Dark mode drifting from light.** The dark tokens are written twice, under `@media (prefers-color-scheme: dark)` and under `:root[data-theme="dark"]`. If they drift apart, one path is unreadable. The Task 1 test asserts the two blocks are identical.
3. **Halftone pattern ids colliding.** The search grid draws many posters on one page. If two use the same `<pattern id>`, every poster shows the first one's ink. The Task 3 test renders two posters and checks the ids are unique and each `url(#…)` resolves inside its own SVG.
4. **Wide pages overflowing a phone.** The wider search and event layouts, a long event name in display type, or a vertical stub can push the page wider than 360 px. The Task 4 and Task 5 Playwright tests measure `scrollWidth` at 360 px in both themes.
5. **A leftover reference to a removed token** (`--ticket`, `--link`, `--focus`, `--shadow`) silently falls back to nothing. The Task 1 test fails on any `var(--x)` without a fallback that the light block doesn't define.

---

### Task 1: Inks, type and the contrast check

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json`
- Modify: `frontend/src/main.tsx:1` (font imports)
- Modify: `frontend/src/styles.css:1-134`: the three token blocks and the Base section, plus every later reference to a removed token
- Modify: `frontend/public/favicon.svg`
- Create: `frontend/src/lib/contrast.ts`, `frontend/src/lib/contrast.test.ts`, `frontend/src/styles.test.ts`

**Interfaces:**
- Produces, in `src/lib/contrast.ts`:
  - `type Tokens = Record<string, string>`;
  - `contrastRatio(foreground: string, background: string): number`, which takes `#rrggbb` only and throws `Error` on anything else;
  - `tokenBlocks(css: string): { light: Tokens; darkMedia: Tokens; darkAttr: Tokens }`. It reads the custom properties of the `:root {` block, of the `:root:not([data-theme="light"]) {` block inside `@media (prefers-color-scheme: dark)`, and of the `:root[data-theme="dark"] {` block. It throws if any of the three is missing.
- Produces the token names every later task uses:
  - **Colours:** `--paper`, `--surface`, `--field`, `--ink`, `--ink-soft`, `--ink-faint`, `--rule`, `--pink`, `--blue`, `--blue-deep`, `--yellow`, `--on-yellow`, `--danger`, `--danger-soft`, `--ok`, `--ok-soft`, `--stub`, `--on-stub`, `--on-ink`.
  - **Effects:** `--blend` (`multiply` in light, `screen` in dark) and `--grain` (an opacity).
  - **Type and shape:** `--font`, `--mono`, `--radius`.
- Removed: `--ticket`, `--ticket-deep`, `--ticket-ink`, `--perforation`, `--link`, `--focus`, `--shadow`.
  - Links and focus outlines use `--blue-deep`.
  - The ticket stub uses `--stub` and `--on-stub`.
  - Primary buttons are `--ink`-filled with `--on-ink` text.

- [ ] **Step 1: Create the branch**

Run: `git switch -c riso-redesign`, then `git add docs/superpowers/specs/2026-11-27-riso-redesign-design.md docs/superpowers/plans/2026-11-27-riso-redesign.md` and `git commit -m "Riso: design and plan"`
Expected: a commit on `riso-redesign` holding the spec and this plan.

- [ ] **Step 2: Write the failing tests**

`src/lib/contrast.test.ts`:
- `contrastRatio("#000000", "#ffffff")` is close to `21` (2 decimal places).
- `contrastRatio("#767676", "#ffffff")` is close to `4.54`.
- `contrastRatio("#777777", "#ffffff")` is close to `4.48`.
- Swapping the arguments gives the same ratio.
- `contrastRatio("red", "#ffffff")` and `contrastRatio("#fff", "#ffffff")` throw.
- `tokenBlocks` on a small CSS string holding all three blocks returns each block's `--name: value` pairs, with values trimmed and `;` dropped.
- `tokenBlocks("")` throws.

`src/styles.test.ts` imports `css from "./styles.css?raw"` and checks:
- `the dark theme is written the same both times`: `expect(darkAttr).toEqual(darkMedia)`.
- `every token the stylesheet uses is defined`: each `var(--name)` with no fallback anywhere in `css` is a key of `light`. Properties set inline by components (`--dx`, `--dy`, `--i`, `--mis`) are always written with a fallback, `var(--i, 0)`, so this check skips them.
- `text is readable in light and dark`. The dark tokens are `{ ...light, ...darkAttr }`. Every token named below must be a `#rrggbb` literal in both themes, and the failure message names the theme, the pair and the ratio.
  - Each of `--ink`, `--ink-soft`, `--ink-faint`, `--blue-deep`, `--danger` and `--ok` on each of `--paper`, `--surface` and `--field`: at least 4.5.
  - These pairs, also at least 4.5: `--ink` on `--danger-soft`, `--ink` on `--ok-soft`, `--on-ink` on `--ink`, `--on-stub` on `--stub`, and `--on-yellow` on `--yellow`.
  - Large display only, at least 3: `--blue` on `--paper` and `--blue` on `--surface`.

- [ ] **Step 3: Run them to see them fail**

Run: `npx vitest run src/lib/contrast.test.ts src/styles.test.ts` (from `frontend/`)
Expected: FAIL, because `contrast.ts` doesn't exist and the tokens are missing.

- [ ] **Step 4: Implement `contrast.ts`, the fonts and the tokens**

`contrastRatio` uses the WCAG 2.x relative luminance: sRGB channels linearised with the 0.04045 threshold, weights 0.2126, 0.7152 and 0.0722, then `(L1 + 0.05) / (L2 + 0.05)` with the lighter colour on top.

Fonts:
- `npm uninstall @fontsource-variable/archivo`.
- `npm install --save-exact @fontsource-variable/bricolage-grotesque@5.3.0 @fontsource/space-mono@5.3.0`.
- In `main.tsx`, import `@fontsource-variable/bricolage-grotesque/standard.css`, `@fontsource/space-mono/400.css` and `@fontsource/space-mono/700.css`.
- `--font: "Bricolage Grotesque Variable", "Segoe UI", system-ui, sans-serif`
- `--mono: "Space Mono", Consolas, ui-monospace, monospace`

Starting values. The test is the gate, so adjust any value that fails.

| Token | Light | Dark |
| --- | --- | --- |
| `--paper` | `#f3eee4` | `#161413` |
| `--surface` | `#faf7f0` | `#1f1c1a` |
| `--field` | `#fffdf9` | `#121110` |
| `--ink` | `#1e1b18` | `#f1ece2` |
| `--ink-soft` | `#4d463e` | `#c9c0b2` |
| `--ink-faint` | `#685f55` | `#a39a8c` |
| `--rule` | `#d3c9b8` | `#3a3531` |
| `--pink` | `#ff48b0` | same |
| `--blue` | `#0078bf` | same |
| `--blue-deep` | `#3d5588` | `#8fb0ff` |
| `--yellow` | `#ffe800` | same |
| `--on-yellow` | `#1e1b18` | same |
| `--danger` | `#b0261c` | `#ff8f80` |
| `--danger-soft` | `#fbe3dc` | `#3b1d1a` |
| `--ok` | `#23663c` | `#7ed69c` |
| `--ok-soft` | `#dff0e2` | `#16301f` |
| `--stub` | `#3d5588` | same |
| `--on-stub` | `#f3eee4` | `#f1ece2` |
| `--on-ink` | `#f3eee4` | `#161413` |
| `--blend` | `multiply` | `screen` |
| `--grain` | about `0.07` | about `0.05` |

Base CSS:
- **Body.** It keeps its explicit `background: var(--paper)`.
- **Links.** `--blue-deep`, underlined with `text-decoration-thickness: 2px`.
- **Focus.** `:focus-visible` gets a 3 px solid `--blue-deep` outline at a 3 px offset.
- **Grain.**
  - A `body::before` at `position: fixed; inset: 0; pointer-events: none`.
  - Its background is a tiled data-URI SVG with `feTurbulence`, at `opacity: var(--grain)` and `mix-blend-mode: var(--blend)`.
  - It's an image, so it never repaints on scroll.
- **Old tokens.** Rename every reference to a removed token below the Base section mechanically: `--link`/`--focus` become `--blue-deep`, `--ticket` becomes `--stub`, `--ticket-ink` becomes `--on-stub`, and `--shadow` becomes `none`. The redesign of those sections comes in Task 4.
- **Favicon.** Re-ink it: an `--ink` ticket with a `--pink` copy offset by 1.5 units, in the same 32×32 viewBox.

- [ ] **Step 5: Check the font files are what the CSS names**

Run: `grep -m1 "font-family" node_modules/@fontsource-variable/bricolage-grotesque/standard.css` and `grep -m1 "font-stretch" node_modules/@fontsource-variable/bricolage-grotesque/standard.css`
Expected: `font-family: 'Bricolage Grotesque Variable'` and a `font-stretch` range that includes 75%. If the family name differs, use that name in `--font`.

- [ ] **Step 6: Run the tests and the checks**

Run: `npx vitest run` and `npm run typecheck` and `npm run lint` and `npm run format:check`
Expected: all pass. The Vitest count is 160 plus the new tests.

- [ ] **Step 7: Commit**

`git add -A frontend` then `git commit -m "Riso: inks, type and the contrast check"`

---

### Task 2: The poster generator

**Files:**
- Create: `frontend/src/lib/poster.ts`, `frontend/src/lib/poster.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export type Motif = "CONCERT" | "SPORTS" | "COLLEGE" | "OTHER" | "PLAIN";
  export interface PosterInput { id: string; category?: string | null }
  export type Ink = "pink" | "blue";
  export type Shape =
    | { kind: "circle"; cx: number; cy: number; r: number; stroke?: number } // stroke set: a ring
    | { kind: "rect"; x: number; y: number; width: number; height: number; rotate: number }
    | { kind: "polygon"; points: [number, number][] }
    | { kind: "line"; x1: number; y1: number; x2: number; y2: number; stroke: number };
  export interface Layer { ink: Ink; halftone: boolean; shapes: Shape[] }
  export interface PosterArt {
    motif: Motif;
    layers: [Layer, Layer]; // [0] printed first, [1] overprinted on top
    offset: { dx: number; dy: number }; // whole px, the top layer's misregistration
  }
  export const VIEWBOX = 100; // art is drawn in a 100 × 100 box
  export const HOUSE_POSTER: PosterInput = { id: "ticketloop-front-page", category: "CONCERT" };
  export function motifFor(category?: string | null): Motif;
  export function seeded(seed: string): () => number; // deterministic, values in [0, 1)
  export function posterArt(input: PosterInput): PosterArt;
  ```

- [ ] **Step 1: Write the failing tests** (`poster.test.ts`)

- `motifFor` maps `"CONCERT"`, `"SPORTS"`, `"COLLEGE"` and `"OTHER"` to themselves, and `"concert"` to `"CONCERT"`. `"FESTIVAL"` maps to `"OTHER"`, and `undefined`, `null` and `""` map to `"PLAIN"`.
- `seeded("a")` called twice yields the same first 10 values. Every value is in `[0, 1)`, and `seeded("b")` gives a different sequence.
- `posterArt({ id: "e1", category: "SPORTS" })` deep-equals a second call with the same input.
- 20 ids `event-0` to `event-19` with the same category give 20 distinct `JSON.stringify` results.
- The same id with `CONCERT` and with `SPORTS` gives different `motif` and different `layers`.
- For every motif (categories `CONCERT`, `SPORTS`, `COLLEGE`, `OTHER` and none) and 10 ids each:
  - one layer is `pink` and the other `blue`;
  - exactly one layer has `halftone: true`;
  - each layer has at least one shape;
  - every number in every shape is finite and within `[-VIEWBOX / 2, VIEWBOX * 1.5]`;
  - `offset.dx` and `offset.dy` are integers with absolute value from 2 to 4.

- [ ] **Step 2: Run them to see them fail**

Run: `npx vitest run src/lib/poster.test.ts`
Expected: FAIL, because the module doesn't exist yet.

- [ ] **Step 3: Implement `poster.ts`**

`seeded`: any 32-bit string hash (FNV-1a over the UTF-16 code units) feeding mulberry32. One generator per poster, seeded with `id` alone, so a category change on the same id reuses the random stream but draws another motif.

What each motif draws:
- **`CONCERT`:** concentric rings off-centre, plus a few sound-wave arcs drawn as `line` or `polygon` segments.
- **`SPORTS`:** parallel track lanes as rotated thin `rect`s, plus diagonal stripes.
- **`COLLEGE`:** pennant triangles (`polygon`) and block `rect`s on a coarse grid.
- **`OTHER`:** blobs (rough `polygon`s or overlapping circles) and stars (`polygon`).
- **`PLAIN`:** halftone bands and one star.

The seed also picks the counts, rotations, positions and which ink goes on top.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run src/lib/poster.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

`git add frontend/src/lib/poster.ts frontend/src/lib/poster.test.ts` then `git commit -m "Riso: the poster generator"`

---

### Task 3: The `Poster` component and its print effects

**Files:**
- Create: `frontend/src/components/Poster.tsx`, `frontend/src/components/Poster.test.tsx`
- Modify: `frontend/src/styles.css`: a new `/* ---------- Posters ---------- */` section

**Interfaces:**
- Consumes: `posterArt`, `PosterInput` and `VIEWBOX` from Task 2.
- Produces: `export function Poster({ event, size, index }: { event: PosterInput; size: "thumb" | "card" | "hero"; index?: number })`.
- The root element is `<div className="poster poster--{size}" aria-hidden="true" data-motif={motif}>`.
- Its inline style sets `--dx` and `--dy`, the offset in px, and `--i`, `index` capped at 8 and defaulting to 0, which staggers the settle.
- Inside are two `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid slice">` layers. They have the classes `poster__ink poster__ink--a` (first) and `poster__ink poster__ink--b` (top), plus `poster__ink--pink` or `poster__ink--blue`.
- Shapes use `fill="currentColor"` or `stroke="currentColor"`. The halftone layer fills through a `<pattern>` in its own `<defs>`, whose dots are `currentColor`. Its id comes from `useId()` with every character outside `[A-Za-z0-9_-]` removed.
- Classes later tasks rely on: `.poster`, `.poster--thumb`, `.poster--card`, `.poster--hero` and `.poster__ink--b`. The settle animation is named `ink-settle`.

- [ ] **Step 1: Write the failing tests** (`Poster.test.tsx`)

- **The card render.** `render(<Poster event={{ id: "e1", category: "SPORTS" }} size="card" />)` gives a root with `aria-hidden="true"`, the class `poster--card` and `data-motif="SPORTS"`. It holds exactly 2 `svg` elements and its `textContent` is `""`.
- **The offset.** The root's inline `--dx` and `--dy` equal `${posterArt(...).offset.dx}px` and `${posterArt(...).offset.dy}px`.
- **Repeatable markup.** `renderToStaticMarkup` (from `react-dom/server`) of the same element twice gives identical strings.
- **Valid SVG.** Each `<svg…</svg>` in that markup, parsed with `new DOMParser().parseFromString(svg, "image/svg+xml")`, has no `parsererror` element and has `documentElement.namespaceURI === "http://www.w3.org/2000/svg"`.
- **Unique pattern ids.** Render two posters with different ids together. Every `pattern[id]` is unique, and every `url(#x)` in a `fill` resolves to a `pattern` inside the same `svg`.
- **Missing and unknown categories.** `{ id: "e2" }` gives `data-motif="PLAIN"`, and `{ id: "e3", category: "FESTIVAL" }` gives `data-motif="OTHER"`.

- [ ] **Step 2: Run them to see them fail**

Run: `npx vitest run src/components/Poster.test.tsx`
Expected: FAIL, because the component doesn't exist yet.

- [ ] **Step 3: Implement `Poster.tsx` and the Posters CSS**

The CSS (exact sizes are left to the builder):
- **`.poster`.** `position: relative; overflow: hidden`, with a `--surface` background and a 2 px `--ink` border.
- **Sizes.** `.poster--thumb` is a square about 4rem wide and sets `--mis: 0.5`. `.poster--card` is full width at `aspect-ratio: 4 / 3`. `.poster--hero` is full width at `aspect-ratio: 4 / 5`.
- **`.poster__ink`.** `position: absolute; inset: 0; width: 100%; height: 100%`. `.poster__ink--pink { color: var(--pink) }` and `.poster__ink--blue { color: var(--blue) }`.
- **`.poster__ink--b`:**
  - `mix-blend-mode: var(--blend)`;
  - `transform: translate(calc(var(--dx, 0px) * var(--mis, 1)), calc(var(--dy, 0px) * var(--mis, 1)))`;
  - `transition: transform 200ms ease-out`;
  - `animation: ink-settle 400ms ease-out both`, with `animation-delay: calc(var(--i, 0) * 60ms)`.
- **`@keyframes ink-settle`.** It starts the top layer about 8 px further out along its offset direction and ends at the resting offset.
- **Hover and focus.** `:is(.poster-card, .card--thumb):is(:hover, :focus-within)` sets `--mis` to about 1.5 times its resting value.
- **Reduced motion.** `@media (prefers-reduced-motion: reduce)` gives `.poster__ink--b` `animation: none; transition: none`.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run`, `npm run typecheck` and `npm run lint`
Expected: all pass.

- [ ] **Step 5: Commit**

`git add frontend/src/components/Poster.tsx frontend/src/components/Poster.test.tsx frontend/src/styles.css` then `git commit -m "Riso: the Poster component and its print effects"`

---

### Task 4: The visual system: shell, ticket, forms, buttons and badges

**Files:**
- Modify: `frontend/src/styles.css`: the Shell, The ticket, The account pass, Panels, Forms, Lists and badges, Holds and tabs sections
- Modify: `frontend/src/components/Layout.tsx` (the wordmark)
- Modify: `frontend/src/components/Ticket.tsx` (an optional `art` prop)
- Modify: `frontend/src/pages/ReservationPage.tsx:61`, `frontend/src/pages/OrderPage.tsx:40`, `frontend/src/pages/TicketPage.tsx:55` (pass `art`)
- Create: `frontend/e2e/look.spec.ts`

**Interfaces:**
- Consumes: `Poster` and `PosterInput` (Tasks 2 and 3).
- Produces: `Ticket({ stub, footer, children, art }: { stub: ReactNode; footer?: ReactNode; children: ReactNode; art?: PosterInput })`. When `art` is set, the stub holds a `.ticket__art` strip that draws `<Poster event={art} size="thumb" />` sized to the strip. The stub's text always sits on solid `--stub`, never on the art.
- Produces `e2e/look.spec.ts` with a helper `pageWidth(page: Page): Promise<number>`, which returns `document.documentElement.scrollWidth`. Task 5 adds to this file.

- [ ] **Step 1: Write the failing browser tests** (`e2e/look.spec.ts`)

```ts
test("focus is easy to see", async ({ page }) => {
  await page.goto("/login");
  for (const name of ["Skip to content", "TicketLoop home"]) {
    await page.keyboard.press("Tab");
    const focused = page.locator(":focus");
    await expect(focused).toHaveAccessibleName(name);
    const outline = await focused.evaluate((el) => {
      const style = getComputedStyle(el);
      return { style: style.outlineStyle, width: parseFloat(style.outlineWidth) };
    });
    expect(outline.style).not.toBe("none");
    expect(outline.width).toBeGreaterThanOrEqual(3);
  }
  // and the same outline check on page.getByLabel("Email") after .focus()
});
```

The second test, `the signed-out pages fit a 360 px phone (light|dark)`:
- one test per scheme, at viewport 360 × 800, after `page.emulateMedia({ colorScheme })`;
- for each of `/`, `/login`, `/register`, `/forgot-password` and `/no-such-page`: `goto`, wait for the page's first heading to be visible, then `expect(await pageWidth(page), path).toBeLessThanOrEqual(360)`.

The third test, `the wordmark is printed in two inks`, checks that the `TicketLoop home` link's computed `text-shadow` is not `none`. This one exists to fail first.

- [ ] **Step 2: Run them to see them fail**

Run: `npx playwright test e2e/look.spec.ts` (from `frontend/`, with `docker compose up -d db mailpit minio`)
Expected: the wordmark test fails because the shadow is `none`. The focus test may already pass; it guards against the redesign regressing focus. Note in the ledger which tests passed before the change.

- [ ] **Step 3: Restyle the shell and components**

**Shell:**
- The masthead uses the 64rem width.
- The wordmark is display type (heavy, condensed `font-stretch: 75%`) in `--ink`, with a pink offset `text-shadow` about 2 px out as the second ink. Its ticket mark is drawn in `--ink` with a `--pink` offset copy.
- The navigation is a wrapping row with a 2 px ink underline on `.active` and on hover. It wraps at 360 px; it doesn't scroll and isn't hidden.
- The devbar uses Space Mono.

**The ticket:**
- `--surface` stock with a 2 px `--ink` border, no soft shadow.
- The stub is `--stub` with `--on-stub` text.
- The perforation is a 2 px dashed `--ink` line on the seam. The notches are `--paper` discs with the same ink border, so they read as punched through.
- The stub `h1` is set in display type, still vertical on wide screens and a band on phones as today. The serial is in Space Mono.
- With `art`, the strip fills the stub's free length (or the band's right end on phones), with at least 4rem of art.

**The pass and the panels:**
- `.pass__name` is display type.
- Panels are divided by a 2 px ink rule.
- Panel headings are display type at a moderate size.

**Forms:**
- Fields have a 2 px `--ink` border, square corners (≤ 4 px radius) and a `--field` background.
- Focus is the 3 px `--blue-deep` outline.
- Invalid fields get a `--danger` border.
- Labels are bold, and hints use `--ink-faint`.

**Buttons:**
- `.button` is `--ink`-filled with `--on-ink` text and a hard `4px 4px 0 var(--pink)` shadow. `:active` translates by the shadow and drops it.
- `.button--quiet` is transparent with `--ink` text and a 2 px ink border.
- `.button--small` is scaled down.
- Disabled buttons keep `opacity: .65`.

**Badges and messages:**
- Badges are rubber stamps, with no `text-transform`:
  - Space Mono at a small size, 0.04em letter-spacing;
  - a 2 px `currentColor` border, small radius;
  - a slight rotation (about −2°), `--surface` background.
- Modifiers keep their current colour meanings: `--ok` for ok, `--danger` for bad, `--blue-deep` for live.
- Messages are `--danger-soft` or `--ok-soft` with a 4 px ink left border.

**The countdown and tabs:**
- `.countdown` is display numerals at `clamp(3.5rem, 14vw, 7rem)` in `--blue`, with `text-shadow: 0.05em 0.04em 0 var(--pink)` and `tabular-nums`.
- The active tab gets a 3 px `--ink` underline.

**Pages:** `ReservationPage`, `OrderPage` and `TicketPage` pass `art={listing.event}`, `art={order.listing.event}` and `art={ticket.event}` respectively (`ticket.event` has no category, so it draws `PLAIN`).

- [ ] **Step 4: Run the browser tests and the full suite**

Run: `npx playwright test e2e/look.spec.ts`, then `npm run e2e`
Expected: `look.spec.ts` passes, and so do all 21 existing tests. If an existing test fails, the redesign is wrong, not the test, unless it can't find something by name because markup moved. Fix the CSS or markup first. Any edit to an existing test gets a `Ruling:` line in the ledger.

- [ ] **Step 5: Unit checks and commit**

Run: `npx vitest run`, `npm run typecheck`, `npm run lint`, `npm run format:check`
Expected: all pass.
Commit: `git add -A frontend` then `git commit -m "Riso: shell, ticket, forms, buttons and stamps"`

---

### Task 5: The front page, event and listing pages

**Files:**
- Create: `frontend/src/components/Feature.tsx`
- Modify: `frontend/src/pages/SearchPage.tsx:47-53` (the head and intro), `frontend/src/components/ListingResults.tsx`, `frontend/src/pages/EventPage.tsx`, `frontend/src/pages/ListingPage.tsx`
- Modify: `frontend/src/styles.css`: the Search section, plus new `.wide`, `.hero`, `.poster-grid`, `.poster-card` and `.feature` rules
- Modify: `frontend/e2e/look.spec.ts`

**Interfaces:**
- Consumes: `Poster` and `HOUSE_POSTER` (Tasks 2 and 3), and `pageWidth` (Task 4).
- Produces: `Feature({ poster, children }: { poster: PosterInput; children: ReactNode })`.
  - It renders `<div className="feature wide">` with a `.feature__art` holding `<Poster event={poster} size="hero" />`, and a `.feature__body` holding the children.
  - At 48rem and wider it is two columns, about 2fr art to 3fr body, with the art `position: sticky` at the top.
  - On phones the body is stacked under a shorter art band (`aspect-ratio: 16 / 9` override).
- Produces: `.page:has(.wide) { max-width: 64rem }`. Every other page keeps the 46rem column.

- [ ] **Step 1: Write the failing browser test** (added to `look.spec.ts`)

The test is `the buying pages fit a phone and hold still when motion is reduced`, with `test.setTimeout(90_000)`.

Set up data with the existing helpers:
- `signInAsAdmin`, then `publishEvent` with `Look ${Date.now()}`;
- a seller from `newPage` and `register`;
- `registerTicket`, `verifyTicket` (eligible), `seller.reload()`, `listTicket("1,200")` and `approveListing`.

As a guest at 1280 px:
- `/` shows `.poster` elements: the hero and at least one card in `list "Tickets on sale"`.
- The card for the event has a `.poster--card` whose `data-motif` is not `PLAIN`.
- Its `.poster__ink--b` has a computed `animationName` of `ink-settle`. This is the negative control for the reduced-motion check.

Then, at 360 × 800 in both `light` and `dark`, `pageWidth` must be ≤ 360 on:
- `/`;
- the event page (follow `All tickets for this event`);
- the listing page (follow the card's title link).

With `emulateMedia({ reducedMotion: "reduce" })` on `/`, every `.poster__ink--b` has `animationName` of `none` and `transitionDuration` of `0s`.

- [ ] **Step 2: Run it to see it fail**

Run: `npx playwright test e2e/look.spec.ts -g "buying pages"`
Expected: FAIL, because `/` has no `.poster`.

- [ ] **Step 3: Build the pages**

**The search page:**
- Wrap the page in `<div className="wide">`.
- `.hero` keeps `<h1>Find tickets</h1>`, now set as a small Space Mono kicker. Below it:
  - the display headline `<p className="hero__headline">Face value. Never a rupee more.</p>`, poster scale with `clamp()`, in `--ink` with a pink offset `text-shadow`;
  - the lede `Every ticket here was checked by an admin before it went on sale.`;
  - `<Poster event={HOUSE_POSTER} size="hero" />` beside the text at 48rem and wider, and below it on phones at a shorter `aspect-ratio`.
- The filters follow in the Task 4 field style.

**`ListingResults`.** When `showEvent` is true:
- The `ul` gets `className="poster-grid"`: one column on phones, two from about 40rem, three from about 60rem.
- Each `li` gets `className="poster-card"`, keeping `aria-label={event.name}`. It holds `<Poster event={event} size="card" index={i} />`, then the title link, seat line, meta line and price, in the same text and order as today.
- The price is the loudest text in the card: display weight, with the struck face value and its visually hidden "Face value " kept.

When `showEvent` is false (the event page), it keeps compact rows with no poster per row, because every row is the same event. That is the builder's call the spec left open; record it in the ledger.

**`EventPage`.** Render `<Feature poster={event.data}>{the existing Ticket}</Feature>`, then `EventListings` inside the same `.wide`, so the listings panel spans the width.

**`ListingPage`.** Wrap both branches, public (`ListingCard` and `ReserveTicket`) and managed (`ManagedListing`), in `<Feature poster={listing.data.event}>`.

- [ ] **Step 4: Run the browser tests**

Run: `npx playwright test e2e/look.spec.ts` then `npm run e2e`
Expected: all pass (the 21 existing tests plus the `look.spec.ts` tests).

- [ ] **Step 5: Unit checks and commit**

Run: `npx vitest run`, `npm run typecheck`, `npm run lint`, `npm run format:check`
Expected: all pass.
Commit: `git add -A frontend` then `git commit -m "Riso: poster cards on the front page, event and listing"`

---

### Task 6: Thumbnails in My tickets, holds, orders and listings

**Files:**
- Modify: `frontend/src/pages/MyTicketsPage.tsx:76-92`, `frontend/src/pages/MyHoldsPage.tsx:77-95`, `frontend/src/pages/MyOrdersPage.tsx:67-82`, `frontend/src/pages/MyListingsPage.tsx:88-104`
- Modify: `frontend/src/styles.css`: a `.card--thumb` rule
- Modify: `frontend/e2e/look.spec.ts`

**Interfaces:**
- Consumes: `Poster` (Task 3).
- Produces: `.card--thumb`, a grid of `auto 1fr` with the thumbnail on the left and the existing card content in a `div`. Each `li` keeps its `className` of `card` (adding `card--thumb`) and its `aria-label`.

My listings isn't in the spec's list. It gets the same row because it shares the component and would look broken next to the others. That is my call; record it in the ledger.

- [ ] **Step 1: Write the failing browser test** (extend the Task 5 test, as the seller)

- `/tickets`: the `list "Your tickets"`'s first item holds a `.poster--thumb` with `data-motif="PLAIN"`, because the ticket's event summary has no category.
- `/listings`: the `listitem` named after the event holds a `.poster--thumb` whose `data-motif` equals the event page hero's `data-motif`.
- Both pages at 360 px in both themes: `pageWidth` ≤ 360.

- [ ] **Step 2: Run it to see it fail**

Run: `npx playwright test e2e/look.spec.ts -g "buying pages"`
Expected: FAIL, because there is no `.poster--thumb` on `/tickets`.

- [ ] **Step 3: Add the thumbnails**

Each row renders `<Poster event={…} size="thumb" />`, using `ticket.event` on My tickets, `reservation.listing.event` on My holds, `order.listing.event` on My orders, and `listing.event` on My listings. The rest of the row's content is unchanged.

- [ ] **Step 4: Run the browser tests**

Run: `npm run e2e`
Expected: all pass.

- [ ] **Step 5: Commit**

Run: `npx vitest run`, `npm run typecheck`, `npm run lint`, `npm run format:check`. All pass.
Commit: `git add -A frontend` then `git commit -m "Riso: poster thumbnails in My tickets, holds, orders and listings"`

---

### Task 7: Every screen checked, the README shots retaken, shipped

**Files:**
- Create: `documentation/assets/riso-demo-paid.png`, `documentation/assets/riso-demo-completed.png`
- Modify: `README.md:99-100`
- Scratch only, not committed: a screenshot script, copied in as `frontend/_x.mjs` and deleted after use

**Why the new file names.** The spec's goal 6 names `m12-demo-paid.png` and `m12-demo-completed.png`. Those same files illustrate the M12 revision page, and the spec's non-goals keep old module screenshots as a record. So the new shots get new names, the README points at them, and the M12 page keeps its originals. Record this as a ruling in the ledger.

- [ ] **Step 1: Bring up the demo stack on the new build**

Run, from PowerShell in the repo root:
- `docker compose build web`;
- `$env:POSTGRES_DB="ticketloop_perf"; docker compose up -d` (after `uv run python -m scripts.load_test seed` if the perf database is missing).

Expected: `docker compose ps` shows `web` healthy on 5173, and `http://localhost:5173/` serves the new look.

- [ ] **Step 2: Screenshot every screen for review**

Write a scratchpad Playwright script, run against the stack in installed Chrome:
- At 1280 × 860 and 360 × 800, in `light` and `dark`, capture `/`, an event page, a listing page, `/login`, `/register` and `/forgot-password`.
- Signed in as `buyer-0001@perf.example.in` (password `load test password 123`), capture `/tickets`, `/listings`, `/reservations`, `/orders`, a hold page and an order page.
- Signed in as an admin, capture `/account` and `/admin/events`.

Read every image. Check each against the spec's anti-goals:
- the text is readable;
- the ticket, the price and the action are the loudest things on the page;
- there is no overflow at 360 px;
- the grain and misregistration stay seasoning.

Fix what fails, rerun Task 4's checks, and commit fixes as `Riso: fixes from the screen review`.

- [ ] **Step 3: Retake the README demo shots**

Run the existing demo script (`demo.mjs`, kept in the scratch folder from M12) as `frontend/_x.mjs` with an output folder in the scratchpad. If the scratchpad was cleared, rewrite it from the README's 5-minute demo: buy, stop RabbitMQ, buy, see Paid, start RabbitMQ, see Completed.
Expected: `demo-5-paid-broker-down.png` and `demo-6-completed-after.png`. Copy them to `documentation/assets/riso-demo-paid.png` and `riso-demo-completed.png`, and point `README.md:99-100` at them, keeping the alt text.

- [ ] **Step 4: Every check, as CI runs them**

Run, from `frontend/`:
- `npm run typecheck`;
- `npm run lint`;
- `npm run format:check`;
- `npx vitest run`;
- `npm run build`;
- `npm audit --audit-level=high`;
- `npm run e2e`.

Then, from PowerShell in the repo root, `docker compose build web`.
Expected: all pass, with the Vitest and Playwright counts written into the ledger.

- [ ] **Step 5: Commit, merge and push**

- `git add README.md documentation/assets/riso-demo-paid.png documentation/assets/riso-demo-completed.png`
- Check the staged diff for attribution lines. Expected: none.
- `git commit -m "Riso: README screenshots in the new look"`.
- `git switch main`, `git merge --ff-only riso-redesign`, `git push origin main`.
- Watch the run with `gh run watch <id> --exit-status`.

Expected: CI green on every job, with the log showing the full Vitest and Playwright counts passed.
