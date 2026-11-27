import { render } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { posterArt } from "../lib/poster";
import { Poster } from "./Poster";

const SVG_NS = "http://www.w3.org/2000/svg";

it("is two inks of art and nothing a reader or a test would find", () => {
  const { container } = render(<Poster event={{ id: "e1", category: "SPORTS" }} size="card" />);
  const root = container.firstElementChild as HTMLElement;
  expect(root).toHaveAttribute("aria-hidden", "true");
  expect(root).toHaveClass("poster", "poster--card");
  expect(root.dataset.motif).toBe("SPORTS");
  expect(root.querySelectorAll("svg")).toHaveLength(2);
  expect(root.textContent).toBe("");
});

it("misregisters the top ink by the poster's own offset", () => {
  const { container } = render(<Poster event={{ id: "e1", category: "SPORTS" }} size="card" />);
  const root = container.firstElementChild as HTMLElement;
  const { dx, dy } = posterArt({ id: "e1", category: "SPORTS" }).offset;
  expect(root.style.getPropertyValue("--dx")).toBe(`${dx}px`);
  expect(root.style.getPropertyValue("--dy")).toBe(`${dy}px`);
});

it("prints the same markup for the same event", () => {
  const poster = <Poster event={{ id: "e1", category: "CONCERT" }} size="hero" />;
  expect(renderToStaticMarkup(poster)).toBe(renderToStaticMarkup(poster));
});

it.each(["CONCERT", "SPORTS", "COLLEGE", "OTHER", undefined])(
  "prints %s as valid SVG",
  (category) => {
    const markup = renderToStaticMarkup(<Poster event={{ id: "e9", category }} size="card" />);
    const svgs = markup.match(/<svg[\s\S]*?<\/svg>/g) ?? [];
    expect(svgs).toHaveLength(2);
    for (const svg of svgs) {
      const parsed = new DOMParser().parseFromString(svg, "image/svg+xml");
      expect(parsed.querySelector("parsererror")).toBeNull();
      expect(parsed.documentElement.namespaceURI).toBe(SVG_NS);
    }
  },
);

it("keeps each poster's halftone to itself", () => {
  const { container } = render(
    <>
      <Poster event={{ id: "a", category: "CONCERT" }} size="card" />
      <Poster event={{ id: "b", category: "CONCERT" }} size="card" />
    </>,
  );
  const ids = [...container.querySelectorAll("pattern")].map((pattern) => pattern.id);
  expect(ids).toHaveLength(2);
  expect(new Set(ids).size).toBe(2);
  for (const svg of container.querySelectorAll("svg")) {
    for (const filled of svg.querySelectorAll("[fill^='url(']")) {
      const id = filled.getAttribute("fill")!.slice(5, -1);
      expect(svg.querySelector(`pattern[id="${id}"]`)).not.toBeNull();
    }
  }
});

it("draws a missing category plain and an unknown one as other", () => {
  const plain = render(<Poster event={{ id: "e2" }} size="thumb" />);
  expect((plain.container.firstElementChild as HTMLElement).dataset.motif).toBe("PLAIN");
  const other = render(<Poster event={{ id: "e3", category: "FESTIVAL" }} size="thumb" />);
  expect((other.container.firstElementChild as HTMLElement).dataset.motif).toBe("OTHER");
});
