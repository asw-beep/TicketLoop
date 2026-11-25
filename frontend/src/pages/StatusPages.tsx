import { Link } from "react-router";
import { Ticket } from "../components/Ticket";

export function ApiUnavailablePage() {
  return (
    <Ticket stub={<h1>Offline</h1>}>
      <div className="message message--error" role="alert">
        <p>Couldn't reach the TicketLoop API.</p>
      </div>
      <p className="lede">Start it from the project folder, then reload this page:</p>
      <pre className="command">uv run uvicorn app.main:app --reload</pre>
      <div className="actions">
        <button type="button" className="button" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    </Ticket>
  );
}

export function NotFoundPage() {
  return (
    <Ticket stub={<h1>Not found</h1>}>
      <p className="lede">There's no page at this address.</p>
      <div className="actions">
        <Link className="button" to="/">
          Go to the start
        </Link>
      </div>
    </Ticket>
  );
}
