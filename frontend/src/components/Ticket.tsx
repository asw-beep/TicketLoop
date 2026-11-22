import type { ReactNode } from "react";

/**
 * The one loud element on a page: a ticket with a perforated stub. On wide screens the
 * stub sits on the left and its text runs up the stub; on phones it becomes a band on top.
 */
export function Ticket({
  stub,
  footer,
  children,
}: {
  stub: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="ticket">
      <div className="ticket__stub">{stub}</div>
      <div className="ticket__body">
        {children}
        {footer && <div className="ticket__footer">{footer}</div>}
      </div>
    </section>
  );
}
