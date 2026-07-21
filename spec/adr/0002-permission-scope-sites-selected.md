# ADR-0002: Graph permission scope — `Sites.Selected` preferred

| Field | Value |
|---|---|
| Status | accepted |

## Context

Updating a metadata column value on a document is, in Graph terms, a write to a list item
(`PATCH .../items/{id}/fields`). Graph offers no narrower "metadata-only write" scope — any
list-item field update requires a permission that allows writes, so a read-only scope is
categorically insufficient. Among write-capable application permissions, the choice is
between one scoped to specific sites and a tenant-wide one. This decision only concerns the
*scope* of the permission; the auth *model* is [ADR-0001](0001-app-only-client-credentials-auth.md).

## Decision

Prefer **`Sites.Selected`** — an application permission that grants access only to the
specific SharePoint sites explicitly consented for this app. Allow **`Sites.ReadWrite.All`**
(tenant-wide) as a documented, broader interim option to simplify initial setup, with the
intent to tighten to `Sites.Selected` once the target sites are known. Whether to start
broad and tighten later is deferred to per-issue implementation planning.

## Alternatives

- **`Sites.Read.All` / `Sites.Selected` (read).** Rejected: read-only cannot satisfy the
  metadata-write requirement, which is the reason this server exists.
- **`Sites.ReadWrite.All` permanently.** Rejected as the *target* state: it grants
  read/write to every site in the tenant — far more than this server needs. Retained only
  as a simpler starting point, not the end state.
- **`Sites.FullControl.All` / `Sites.Manage.All`.** Rejected: grants permission-management
  and structural capabilities this server never uses; violates least privilege.

## Tradeoffs

- **`Sites.Selected` — gain:** least privilege; blast radius limited to named sites; easiest
  to justify in a security review. **Give up:** an extra per-site grant step at setup
  (site access must be provisioned before the app can touch a site).
- **`Sites.ReadWrite.All` — gain:** zero per-site provisioning; fastest to a working
  prototype. **Give up:** tenant-wide write reach the server does not exercise — a standing
  over-grant until tightened.

## Consequences

- Regardless of scope, the server's *tool surface* stays metadata-only (list/read/update
  column values); file upload, deletion, and permission management are never exposed, so the
  effective capability is narrower than even `Sites.Selected` allows.
- Choosing `Sites.Selected` adds a documented setup step (grant the app access to each target
  site); choosing `Sites.ReadWrite.All` adds a follow-up task to tighten later.
- This decision is referenced from the **Required Graph permissions** subsection of
  `spec/spec.md`, and relates to the deferred item "whether to start with
  `Sites.ReadWrite.All` and tighten to `Sites.Selected` later."
