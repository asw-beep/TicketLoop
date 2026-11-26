import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import type { Reservation } from "../api/types";
import {
  checkoutProblem,
  checkoutUrl,
  isWorthRetrying,
  orderFor,
  useClickKeys,
  type Keys,
} from "../lib/checkout";
import { MY_ORDERS_KEY, orderKey } from "../lib/orders";
import { FormError } from "./Form";

/**
 * Go and pay, then leave for the gateway's page. `children` is the button's text. Give either
 * the hold to order or the order to continue. `onFailed` hears of a try that failed for good;
 * `earlier` is such a failure from another button, shown until this one is pressed.
 */
export function PayButton({
  hold,
  orderId,
  quiet = false,
  onFailed,
  earlier = null,
  children,
}: {
  quiet?: boolean;
  onFailed?: (error: unknown) => void;
  earlier?: unknown;
  children: string;
} & ({ hold: Reservation; orderId?: undefined } | { hold?: undefined; orderId: string })) {
  const queryClient = useQueryClient();
  const keys = useClickKeys();
  const pay = useMutation({
    mutationFn: async ({ order, payment }: Keys) =>
      checkoutUrl(hold ? await orderFor(hold, order) : orderId, payment),
    retry: isWorthRetrying, // with the same keys: it is still the same click
    retryDelay: 1_500,
    onSuccess: (url) => window.location.assign(url),
    onError: (error) => onFailed?.(error),
    onSettled: (_, error) => {
      keys.settle(error);
      // Whatever happened, the pages we may come back to must ask again.
      void queryClient.invalidateQueries({ queryKey: ["reservations"] });
      void queryClient.invalidateQueries({ queryKey: MY_ORDERS_KEY });
      if (hold) void queryClient.invalidateQueries({ queryKey: ["reservation", hold.id] });
      else void queryClient.invalidateQueries({ queryKey: orderKey(orderId) });
    },
  });
  const error = pay.isIdle ? earlier : pay.error;
  const problem = checkoutProblem(error);

  // Back from the gateway may bring this very page back, button still disabled.
  const { reset } = pay;
  useEffect(() => {
    const shown = (event: PageTransitionEvent) => event.persisted && reset();
    window.addEventListener("pageshow", shown);
    return () => window.removeEventListener("pageshow", shown);
  }, [reset]);

  return (
    <>
      {problem ? (
        <div className="message message--error" role="alert">
          <p>{problem}</p>
        </div>
      ) : (
        <FormError error={error} />
      )}
      <div className="actions">
        <button
          type="button"
          className={quiet ? "button button--quiet" : "button"}
          onClick={() => pay.mutate(keys.take())}
          // Still disabled after success: the browser is on its way to another page.
          disabled={pay.isPending || pay.isSuccess}
          aria-busy={pay.isPending}
        >
          {children}
        </button>
      </div>
    </>
  );
}
