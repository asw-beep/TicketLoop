# Frontend redesign: riso zine: design

Date: 2026-11-27. Status: draft for approval.

## Intent

The app works but looks plain:
- blue-grey paper, one blue accent, Archivo, cards with thin borders;
- the ticket stub on the hold and order pages is its only distinctive element.

TicketLoop is a resume project. The app is the first thing an interviewer opens, so it should look designed, the way a site featured on Awwwards does, while every flow keeps working exactly as it does today.

From five directions, I chose **riso zine**: the look of risograph printing.
- **Paper and ink.** Warm paper, two fluorescent inks printed slightly out of register, halftone dots and grain.
- **A generated poster for every event.** It is made from the event's category and id, because events have no images.

It suits a ticket marketplace: gig posters and zines are how live events have always been advertised.

## Goals

1. Every screen uses the new visual system: paper, inks, type, buttons, fields, badges, header and footer.
2. The buying flow gets the full treatment, with posters and print effects: search (the front page), event, listing, hold, order, My tickets, My holds and My orders.
3. Each event has a generated two-ink poster, the same every time for the same event, different across events, and recognisably of its category.
4. Motion that feels like printing. The inks slide into register when a page or card appears, and drift slightly out of register on hover and focus. All of it is CSS, and none of it runs under `prefers-reduced-motion`.
5. Dark mode stays, following the system as it does today: the same inks on black stock.
6. The README's two demo screenshots (`documentation/assets/m12-demo-paid.png` and `m12-demo-completed.png`) are retaken in the new look.

## Non-goals

- No change to the backend, the API, the data, or what any screen does. No new pages or features.
- No change to wording. Headings, button labels, link text and the `aria-label`s the browser tests find stay the same, character for character.
- The documentation site (`documentation/`) keeps its own look. The old module screenshots stay as a record of how each module looked.
- No CSS framework, animation library, canvas or WebGL.
- No hand-drawn illustration or stock imagery: posters are generated.

## Anti-goals

These are failures even if everything else works.

- **Unreadable text.** Body text, labels and prices must meet WCAG AA contrast (4.5:1, or 3:1 for large text) on paper in both themes. Fluorescent pink and plain riso blue fail on paper; they are for art, fills and large display only.
- **A theme over a broken app.** No layout may break at 360 px wide, focus must stay clearly visible, and keyboard use must be unchanged.
- **Retro overload.** Grain, misregistration and halftone are seasoning. The ticket, the price and the action stay the loudest things on each page.
- **Slow pages.** No full-page SVG filter that repaints on scroll. Grain is a static texture, posters are small inline SVGs, and scrolling the search results stays smooth on a laptop.
- **Tests bent to fit.** The browser tests change only where the redesign moves something they can't find by name. Every such change is listed in the plan and justified.

## Constraints

- **Stack.** React 19.3, React Router 8, TanStack Query, Vite 8, with plain CSS in `frontend/src/styles.css` and colour tokens on `:root`. Dark mode works through `prefers-color-scheme` and `:root[data-theme="dark"]`.
- **Screens.** 19 pages in `frontend/src/pages`, with components in `frontend/src/components`: `Layout`, `Ticket` (the stub), `ListingResults`, `Badges`, `Form`, `Checkout`, `ReserveTicket`, `TicketListing`.
- **Tests.**
  - The 21 Playwright browser tests (`frontend/e2e`) find elements by role and accessible name.
  - The 160 Vitest tests cover logic in `src/lib` and `src/api`, not markup.
  - CI also runs `tsc`, ESLint, Prettier, the production build and `npm audit --audit-level=high`.
  - *My call:* what has to survive the redesign is every role and accessible name the browser tests use. That covers:
    - headings;
    - the labelled lists (such as "Tickets on sale");
    - each list item's `aria-label`;
    - button and link names;
    - the `timer` named "Time left".
  - Markup, element order and class names are free to change.
- **Fonts.** Self-hosted through Fontsource packages, as Archivo is today; nothing loads from a CDN. *My call:* Fontsource's default `font-display: swap` is fine; a brief fallback flash on first load is acceptable.
- **Browsers.** *My call:* verified in Chromium, which the browser tests run. The design uses only CSS and SVG features supported by current Chrome, Firefox and Safari (`mix-blend-mode`, `clamp()`, SVG patterns and filters).
- **Event data.** An event's poster can use only `id`, `name`, `category` (`CONCERT`, `SPORTS`, `COLLEGE` or `OTHER`), `venue`, `city` and `starts_at`.
  - **Category isn't always there.** Listing results carry the category as a plain string. My tickets gets its event from a smaller summary with no category at all.
- **The app image.** The nginx-served build in `frontend/Dockerfile` must still build.

## Approaches

| | Approach | Trade-off |
| --- | --- | --- |
| **A, chosen** | Rework the existing stylesheet and components, and add a `Poster` component that draws SVG | It fits the stack and needs no new runtime dependencies. SVG gives crisp art, blend modes, patterns and noise natively, and posters are testable as pure functions of the event. |
| B | Rewrite the styling in Tailwind or a component kit | Churn across 19 screens for no visual gain; the look comes from tokens and a few components either way. |
| C | Canvas or WebGL posters, with GSAP for motion | Heavier, worse for accessibility and harder to test, and riso effects don't need it. |

## The design, as decided

### Palette (riso inks)

| Token | Light | Use |
| --- | --- | --- |
| `--paper` | warm off-white, about `#F3EEE4` | page |
| `--ink` | near-black with a warm cast, about `#1E1B18` | text |
| `--pink` | Fluorescent Pink `#FF48B0` | art, fills, large display, the second ink of shadows |
| `--blue` | Riso Blue `#0078BF` | art, fills, large display |
| `--blue-deep` | Federal Blue, about `#3D5588` | links, focus, small blue text (passes AA on paper) |
| `--yellow` | Riso Yellow `#FFE800` | highlight behind text only, sparingly |

Dark ("night print"): paper about `#161413`, ink about `#F1ECE2`, the same pink, blue and yellow. Overprints use `mix-blend-mode: screen` instead of `multiply`, and `--blue-deep` is replaced by a light blue that passes AA on the dark stock.

Danger and success keep their meaning, in ink-compatible tones that pass AA.

**Contrast pairs** (*my call*). These pairs must reach AA in both themes:
- Text colours: `--ink`, `--ink-soft`, `--blue-deep` (links), `--danger`, `--ok`, and the text colours used on badges and buttons.
- Backgrounds: `--paper`, `--surface`, `--field`, and each badge and button fill.

No body text is ever set over poster art. A Vitest test reads the custom properties straight from `styles.css`, for light and for dark, and checks each listed pair's WCAG ratio, so a token change that breaks contrast fails CI.

### Type

- **Bricolage Grotesque Variable** (`@fontsource-variable/bricolage-grotesque`, OFL) for everything. Display sizes use its condensed width and heavy weight; body uses its text optical size.
- **Space Mono** (`@fontsource/space-mono`, OFL) for serial numbers, times, prices in tables and small labels.
- Archivo is removed.
- Display type is big: the front page headline and event names on posters run at poster scale, fluid with `clamp()`.

### Print effects

- **Grain.** A static noise texture over the whole page, at low opacity, generated once (an SVG `feTurbulence` rendered to a data URI, or a small PNG) and fixed.
- **Misregistration.** Overprinted elements carry their two ink layers 2–4 px apart. On hover and focus the offset grows by a pixel or two over 150–250 ms.
- **Halftone.** A dot pattern (an SVG pattern, or a CSS `radial-gradient`) fills one ink layer of posters and some large shapes.
- **Buttons and fields.** Solid ink borders (2 px). Primary buttons are filled with ink and carry a hard offset shadow in the second ink, which closes to zero when pressed. Focus is a thick `--blue-deep` outline, offset from the element.

### The poster (`frontend/src/components/Poster.tsx`)

- **Pure and seeded.** `<Poster event={…} size="thumb" | "card" | "hero" />` renders an inline SVG, `aria-hidden` (the event's name is always also real text nearby). A small seeded generator, seeded from the event id, picks the layout, rotation, offsets and which ink goes on top. The same event always gives the same poster.
- **Category is optional** (*my call*). The poster takes `id` plus an optional `name` and `category`. A category outside the four known values draws as `OTHER`. With no category, as on My tickets, it draws a neutral motif seeded from the id: halftone bands and a star. This needs no backend change.
- **A motif per category:**
  - `CONCERT`: concentric rings and sound waves;
  - `SPORTS`: track lanes, field lines, diagonal stripes;
  - `COLLEGE`: pennant and block shapes on a grid;
  - `OTHER`: blobs and stars.
- **Two layers.** Pink and blue, overprinted with a blend mode, one layer in halftone, both slightly misregistered.
- **The thumbnail stays art-only.** At thumb size it is shapes alone; the `card` and `hero` sizes may set the event's name (and date) in display type inside the art, still `aria-hidden`.
- **Lighter in tests.** A unit test checks the generator is deterministic, that categories differ, and that its output is valid SVG markup without the DOM.

### Screens

- **Header.** A wordmark set in display type with a misregistered second ink, and the navigation as an ink-underlined row. On a phone the navigation wraps or scrolls; it is never hidden behind a menu, so the browser tests' links stay visible.
- **Search, the front page.**
  - A poster-scale headline about fair prices (reworded from the current intro sentence, which is not a test anchor), beside a large hero poster.
  - *My call:* the hero poster is a fixed house poster with a set seed, not tied to any event, because the front page isn't about one event and must look the same before results load.
  - The filters follow, in the new field style.
  - Results become a grid of poster cards, one column on a phone and two or three on wider screens. Each card has the poster, the event name, seat line, venue, city and date, and the price with its face value struck through.
  - Same `ul` labelled "Tickets on sale", same links and same text.
- **Event page.** A hero poster with the event's details, then its tickets as poster cards (or a compact list) in the same style.
- **Listing page.** A large poster beside the ticket's details and the reserve area.
- **Hold and order pages.**
  - The ticket stub stays, re-inked: paper ticket, ink outline, perforations, a poster strip on the stub.
  - The countdown is set in huge display numerals, overprinted pink on blue.
  - The status badge looks like a rubber stamp.
- **My tickets, My holds, My orders.** Rows with a thumbnail poster.
- **Auth, account and admin screens.** The visual system only: type, fields, buttons, badges and the paper. No posters.

### Motion

- **On first paint.** Each card's two ink layers start about 8 px apart and settle to their resting offset over about 400 ms, staggered across the first screenful.
- **On hover and focus.** A small offset change.
- **Nothing else moves.** No scroll-jacking, no parallax, nothing that runs continuously.
- **Reduced motion.** Under `prefers-reduced-motion: reduce` everything is static at the resting offset.

## Left to the builder

- Exact token values within the stated ranges, as long as they pass the contrast rule in both themes.
- The poster generator's shapes, counts and randomness within each category's motif.
- How the grain texture is produced, and the halftone technique.
- Breakpoints and grid column counts.
- The order of the screen work.
- Whether the event page lists its tickets as cards or as a compact list.

## Done when

- **Every screen restyled.** Every screen is checked in both themes at 1280 px and at 360 px. *My call:* those screenshots are for review only and are not committed.
- **All checks green.** The 21 browser tests pass, as do Vitest (with the poster tests added), `tsc`, ESLint, Prettier, the build, `npm audit --audit-level=high` and the web image build.
- **Contrast checked.** Every text token pair passes AA in both themes, checked by a script or test, not by eye.
- **README screenshots retaken.** The two demo shots are retaken with the existing demo script, against the Docker stack and the seed data, and committed in place of the old ones.
