/** Short names for the API's shapes, all generated from the backend's OpenAPI schema. */
import type { components } from "./schema";

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
