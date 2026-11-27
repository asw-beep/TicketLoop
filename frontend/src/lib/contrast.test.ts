import { contrastRatio, tokenBlocks } from "./contrast";

describe("contrastRatio", () => {
  it("gives 21 for black on white", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 2);
  });

  it("matches the WCAG reference greys either side of 4.5", () => {
    expect(contrastRatio("#767676", "#ffffff")).toBeCloseTo(4.54, 2);
    expect(contrastRatio("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
  });

  it("does not care which colour is on top", () => {
    expect(contrastRatio("#ffffff", "#3d5588")).toBe(contrastRatio("#3d5588", "#ffffff"));
  });

  it("takes six-digit hex only", () => {
    expect(() => contrastRatio("red", "#ffffff")).toThrow();
    expect(() => contrastRatio("#fff", "#ffffff")).toThrow();
  });
});

describe("tokenBlocks", () => {
  const css = `
:root {
  --paper: #f3eee4;
  --ink:   #1e1b18 ;
  --font: "Bricolage", sans-serif;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #161413;
  }
}

:root[data-theme="dark"] {
  --paper: #161413;
}

.card {
  --local: 1px;
}
`;

  it("reads each block's custom properties, trimmed", () => {
    expect(tokenBlocks(css)).toEqual({
      light: { "--paper": "#f3eee4", "--ink": "#1e1b18", "--font": '"Bricolage", sans-serif' },
      darkMedia: { "--paper": "#161413" },
      darkAttr: { "--paper": "#161413" },
    });
  });

  it("refuses a stylesheet without the theme blocks", () => {
    expect(() => tokenBlocks("")).toThrow();
    expect(() => tokenBlocks(":root { --paper: #ffffff; }")).toThrow();
  });
});
