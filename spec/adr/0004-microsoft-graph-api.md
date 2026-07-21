# ADR-0004: Microsoft Graph REST v1.0 as the primary API surface

| Field | Value |
|---|---|
| Status | accepted |

## Context

SharePoint Online document libraries can be reached through more than one API. The server
needs to resolve a site, enumerate a library's columns, list items, and read and write list
item field values. The chosen API determines the request shapes, the auth model's
compatibility, and which column types can be fully supported.

## Decision

Use the **Microsoft Graph REST API v1.0** as the primary surface for all operations:

| Operation | Endpoint |
|---|---|
| Resolve site | `GET /sites/{hostname}:/{site-path}` |
| List columns | `GET /sites/{site-id}/lists/{list-id}/columns` |
| List documents | `GET /sites/{site-id}/lists/{list-id}/items?expand=fields` |
| Get metadata | `GET /sites/{site-id}/lists/{list-id}/items/{item-id}/fields` |
| Update metadata | `PATCH /sites/{site-id}/lists/{list-id}/items/{item-id}/fields` |

## Alternatives

- **SharePoint REST API (`_api/`) / CSOM.** Rejected as the primary surface: an older
  model with a different auth story and heavier payloads. Graph is the strategic, better
  documented, app-only-friendly surface for cross-service Microsoft 365 access.

## Tradeoffs

- **Gain:** one modern, well-documented API; clean fit with app-only client credentials
  ([ADR-0001](0001-app-only-client-credentials-auth.md)); v1.0 stability guarantees.
- **Give up:** some SharePoint-specific capabilities are thinner or absent in Graph — most
  notably managed-metadata (taxonomy) term resolution.

## Consequences

- **Known limitation:** managed-metadata (taxonomy) column *writes* may require the
  SharePoint REST API rather than Graph for term (`TermGuid`) resolution. This is captured as
  a deferred implementation specific in `spec/spec.md`; if taxonomy write support is built, a
  follow-up ADR will record introducing SharePoint REST as a secondary surface for that
  narrow case. Graph remains primary for everything else.
- Default pagination is whatever Graph returns (~200 items); larger-library pagination is a
  deferred specific, not part of this decision.
- This decision is referenced from the **Graph API surface** section of `spec/spec.md`.
