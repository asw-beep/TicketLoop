/** Short names for the API's shapes, all generated from the backend's OpenAPI schema. */
import type { components, paths } from "./schema";

type Schemas = components["schemas"];

export type Event = Schemas["EventOut"];
export type EventIn = Schemas["EventIn"];
export type EventPatch = Schemas["EventPatch"];
export type EventStatus = Event["status"];
export type EventCategory = Event["category"];
export type Provider = Schemas["ProviderOut"];
export type Ticket = Schemas["TicketOut"];
export type TicketIn = Schemas["TicketIn"];
export type VerificationStatus = Ticket["verification_status"];
export type Document = Schemas["DocumentOut"];
export type DocumentLink = Schemas["DocumentLinkOut"];
export type VerifyIn = Schemas["VerifyIn"];
export type Listing = Schemas["ListingOut"];
export type PublicListing = Schemas["PublicListingOut"];
export type ListingIn = Schemas["ListingIn"];
export type ListingPatch = Schemas["ListingPatch"];
export type ListingStatus = Listing["status"];
export type HistoryEntry = Schemas["HistoryEntryOut"];
export type RejectIn = Schemas["RejectIn"];
export type Reservation = Schemas["ReservationOut"];
export type ReservationStatus = Reservation["status"];
export type Order = Schemas["OrderOut"];
export type OrderStatus = Order["status"];
/** The search query, without the paging parameters. */
export type ListingQuery = Omit<
  NonNullable<paths["/api/v1/listings"]["get"]["parameters"]["query"]>,
  "cursor" | "limit" | "event_id"
>;
