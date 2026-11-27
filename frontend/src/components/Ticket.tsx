import type { ReactNode } from "react";
import type { PosterInput } from "../lib/poster";
import { Poster } from "./Poster";

/** Copies in the stub's poster strip: enough to fill a phone-wide band. */
const STRIP = 12;

/**
 * The one loud element on a page: a ticket with a perforated stub. On wide screens the
 * stub sits on the left and its text runs up the stub; on phones it becomes a band on top.
 * With `art`, the event's poster runs as a strip along the stub's edge.
 */
export function Ticket({
  stub,
  footer,
  art,
  children,
}: {
  stub: ReactNode;
  footer?: ReactNode;
  art?: PosterInput;
  children: ReactNode;
}) {
  return (
    <section className="ticket">
      <div className={art ? "ticket__stub ticket__stub--art" : "ticket__stub"}>
        {art && (
          // A border of small copies, like a printed strip; the overflow is cut off.
          <div className="ticket__art">
            {Array.from({ length: STRIP }, (_, i) => (
              <Poster key={i} event={art} size="thumb" index={i} />
            ))}
          </div>
        )}
        {stub}
      </div>
      <div className="ticket__body">
        {children}
        {footer && <div className="ticket__footer">{footer}</div>}
      </div>
    </section>
  );
}
