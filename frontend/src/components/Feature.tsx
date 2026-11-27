import type { ReactNode } from "react";
import type { PosterInput } from "../lib/poster";
import { Poster } from "./Poster";

/**
 * One event, big: its poster beside the page's ticket and panels on a wide screen (the poster
 * stays put while the panels scroll), and a band above them on a phone.
 */
export function Feature({ poster, children }: { poster: PosterInput; children: ReactNode }) {
  return (
    <div className="feature wide">
      <div className="feature__art">
        <Poster event={poster} size="hero" />
      </div>
      <div className="feature__body">{children}</div>
    </div>
  );
}
