"""The ``list_columns`` discovery tool.

Before an agent can call ``update_document_metadata`` it must know *what* is settable:
which columns exist, their **internal names** (writes take internal names, not the
human-facing display names), each column's **field type**, and — for ``choice``
columns — the **allowed values**. This tool answers that question for one document
library (spec: **Tools › list_columns**).

It is a thin layer over :mod:`spdoc_mcp.graph`: resolve the site and library
identifiers with the existing resolvers, issue the one ``list columns`` Graph request,
and normalize each raw ``columnDefinition`` into a :class:`ColumnInfo`. Only columns an
agent can actually set are returned — hidden and read-only (system) columns are filtered
out. Graph's ``list columns`` operation returns a *term-set reference* for taxonomy
(managed-metadata) columns but not the enumerated term list — that lives in the term
store, a separate API deliberately out of scope here — so taxonomy columns surface their
term-set id rather than an allowed-values list.

Domain errors from the Graph client (:class:`~spdoc_mcp.errors.NotFoundError` for an
unresolvable site/library, :class:`~spdoc_mcp.errors.GraphError` otherwise) are left to
propagate; the boundary :class:`~spdoc_mcp.middleware.ErrorHandlingMiddleware` surfaces
them cleanly, so this module holds no ``try``/``except``.
"""

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from spdoc_mcp.graph import get_graph_client

# The single type facet Graph puts on a columnDefinition, in the order we probe for it.
# Membership here also distinguishes a type facet from the definition's other keys
# (``name``, ``displayName``, ``hidden``, …), which are never in this tuple.
_TYPE_FACETS: tuple[str, ...] = (
    "text",
    "boolean",
    "choice",
    "number",
    "currency",
    "dateTime",
    "lookup",
    "personOrGroup",
    "calculated",
    "geolocation",
    "term",
    "hyperlinkOrPicture",
    "thumbnail",
    "contentApprovalStatus",
)
# Facet keys whose spec-facing field-type name differs from the raw Graph key.
_FIELD_TYPE_ALIASES: dict[str, str] = {"term": "taxonomy"}
_TAXONOMY_FIELD_TYPE = "taxonomy"
_UNKNOWN_FIELD_TYPE = "unknown"


class ColumnInfo(BaseModel):
    """One user-settable metadata column, normalized for agent consumption."""

    internal_name: str
    display_name: str
    field_type: str
    allowed_values: list[str] | None = None
    term_set_id: str | None = None


def _detect_field_type(column: dict[str, Any]) -> str:
    """Return the spec-facing field type from the single type facet Graph set on the column."""
    for facet in _TYPE_FACETS:
        if facet in column:
            return _FIELD_TYPE_ALIASES.get(facet, facet)
    return _UNKNOWN_FIELD_TYPE


def _choice_values(column: dict[str, Any]) -> list[str] | None:
    """The allowed values of a ``choice`` column, or ``None`` when the column is not a choice."""
    choices = column.get("choice", {}).get("choices")
    return choices if isinstance(choices, list) else None


def _term_set_id(column: dict[str, Any]) -> str | None:
    """The term-set id referenced by a taxonomy column, when Graph provides one."""
    term_set = column.get("term", {}).get("termSet", {})
    term_set_id = term_set.get("id")
    return term_set_id if isinstance(term_set_id, str) else None


def _is_settable(column: dict[str, Any]) -> bool:
    """True for user-defined, writable columns — hidden and read-only system columns are excluded."""
    return not column.get("hidden", False) and not column.get("readOnly", False)


def _to_column_info(column: dict[str, Any]) -> ColumnInfo:
    """Project a raw Graph ``columnDefinition`` into a :class:`ColumnInfo`."""
    internal_name = column["name"]
    field_type = _detect_field_type(column)
    return ColumnInfo(
        internal_name=internal_name,
        display_name=column.get("displayName", internal_name),
        field_type=field_type,
        allowed_values=_choice_values(column),
        term_set_id=_term_set_id(column) if field_type == _TAXONOMY_FIELD_TYPE else None,
    )


def register(app: FastMCP) -> None:
    """Register the ``list_columns`` tool onto ``app``."""

    @app.tool
    async def list_columns(
        site: Annotated[str, Field(description="SharePoint site URL or Graph site ID.")],
        library: Annotated[str, Field(description="Document library display name or Graph list ID.")],
    ) -> list[ColumnInfo]:
        """Discover the settable metadata columns on a document library.

        Returns every user-defined, writable column with its internal name (used for
        writes), display name, and field type; ``choice`` columns also carry their
        allowed values, and taxonomy columns their term-set id. Use this before
        ``update_document_metadata`` to learn what payload the library accepts.
        """
        client = get_graph_client()
        site_id = (await client.resolve_site(site))["id"]
        list_id = await client.resolve_list(site_id, library)
        raw = await client.list_columns(site_id, list_id)
        return [_to_column_info(column) for column in raw.get("value", []) if _is_settable(column)]
