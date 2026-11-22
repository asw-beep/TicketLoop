/**
 * Purchase evidence on the client: a quick check before uploading, the upload itself, and
 * opening a file through the short-lived link the API hands out.
 *
 * The server is the authority. It reads each file's first bytes to decide its type and
 * refuses anything else; the check here only saves a pointless upload.
 */
import { api, unwrap } from "../api/client";
import type { Document } from "../api/types";

export const MAX_DOCUMENT_BYTES = 5 * 1024 * 1024;
const ACCEPTED = new Set(["application/pdf", "image/png", "image/jpeg"]);
export const ACCEPT_ATTRIBUTE = ".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg";

/** Why this file would be refused, or null if it looks fine. */
export function checkFile(file: { size: number; type: string }): string | null {
  if (file.size === 0) return "That file is empty.";
  if (file.size > MAX_DOCUMENT_BYTES) return "Files can be up to 5 MB.";
  // Some browsers report no type at all; the server reads the bytes and decides.
  if (file.type && !ACCEPTED.has(file.type)) return "Upload a PDF, PNG or JPEG.";
  return null;
}

export function uploadDocument(ticketId: string, file: File): Promise<Document> {
  const form = new FormData();
  form.append("file", file);
  return unwrap(
    api.POST("/api/v1/tickets/{ticket_id}/documents", {
      params: { path: { ticket_id: ticketId } },
      // Sent as multipart/form-data; the browser sets the boundary itself.
      body: form as unknown as { file: string },
      bodySerializer: (body) => body as unknown as FormData,
    }),
  );
}

/**
 * Open a document in a new tab. The tab opens at the click, before the link is fetched:
 * a tab opened after waiting for the network is treated as a popup and often blocked.
 */
export async function openDocument(ticketId: string, documentId: string): Promise<void> {
  const tab = window.open("", "_blank");
  try {
    const { url } = await unwrap(
      api.GET("/api/v1/tickets/{ticket_id}/documents/{document_id}", {
        params: { path: { ticket_id: ticketId, document_id: documentId } },
      }),
    );
    if (tab) {
      tab.opener = null; // the file's page gets no handle back to TicketLoop
      tab.location.href = url;
    } else {
      window.open(url, "_blank", "noopener");
    }
  } catch (error) {
    tab?.close();
    throw error;
  }
}
