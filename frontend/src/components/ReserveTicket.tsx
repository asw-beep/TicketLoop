import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { api, unwrap } from "../api/client";
import type { PublicListing } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { reserveProblem } from "../lib/reservations";
import type { LoginState } from "./Guards";
import { reservationKey } from "../pages/ReservationPage";

/** Reserve, on the page of a listing that is on sale and not yours. */
export function ReserveTicket({ listing }: { listing: PublicListing }) {
  const { status } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const reserve = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/listings/{listing_id}/reservations", {
          params: { path: { listing_id: listing.id } },
        }),
      ),
    onSuccess: (reservation) => {
      queryClient.setQueryData(reservationKey(reservation.id), reservation);
      void queryClient.invalidateQueries({ queryKey: ["reservations"] });
      void queryClient.invalidateQueries({ queryKey: ["search"] });
      // Instead of this page: Back would lead to a listing nobody can see while it's held.
      navigate(`/reservations/${reservation.id}`, { replace: true });
    },
  });

  if (status === "loading" || status === "unavailable") return null;
  return (
    <section className="panel" aria-labelledby="reserve-heading">
      <h2 id="reserve-heading">Buy this ticket</h2>
      <p className="lede">
        Reserving holds it for 10 minutes while you pay. Nobody else can buy it in that time.
      </p>
      {reserve.error && (
        <div className="message message--error" role="alert">
          <p>{reserveProblem(reserve.error)}</p>
        </div>
      )}
      <div className="actions">
        {status === "signed-in" ? (
          <button
            type="button"
            className="button"
            onClick={() => reserve.mutate()}
            disabled={reserve.isPending}
            aria-busy={reserve.isPending}
          >
            Reserve
          </button>
        ) : (
          <Link
            className="button"
            to="/login"
            state={{ from: `/listings/${listing.id}` } satisfies LoginState}
          >
            Log in to reserve
          </Link>
        )}
      </div>
    </section>
  );
}
