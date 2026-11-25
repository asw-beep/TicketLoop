const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Is this address segment an ID? Anything else (like "me") must not reach the API, where the
 * same path can mean something different: GET /listings/me is the seller's list.
 */
export function isUuid(text: string): boolean {
  return UUID.test(text);
}
