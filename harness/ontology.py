"""ONTOLOGY.md's object and link tables, rendered from harness/schema.py, the one declaration of the
store, so the analyst's map and the store's rules are the same text. `drift()` names a table whose
text in the file is not what the schema renders."""

from __future__ import annotations

import json
import re
from pathlib import Path

from harness import schema

KINDS = {schema.TEXT: "text", schema.NUMBER: "number", schema.INTEGER: "integer", schema.DATE: "date",
         schema.DATETIME: "date and time", schema.BOOL: "true or false", schema.TEXTS: "list of texts",
         schema.MAPPING: "object", schema.CONVERSION: "price or yield(duration)",
         schema.ANSWER: "a probability", schema.CHANCE: "a probability from 0 to 1, with its basis",
         schema.FIGURE: "a number, with its basis",
         schema.DERIVATION_FIELD: "a derivation, with its probabilities' bases"}

ENTERS = {
    schema.ANALYST: "`add`",
    schema.NOTE: "`note`",
    schema.VENUE: "from its venue, when a link or a note names it",
    schema.FETCH: "`fetch_source` or `search_company`; kept once cited or linked as evidence",
    schema.HOLDINGS: "the holdings and the look-through",
    schema.STORE: "`add`, `edit`, or `link` with a `source_id`",
    schema.DERIVATION: "the store, from holdings and data",
    schema.JUDGE: "the judge, when structure asks a question",
    schema.ENGINE: "the store, on read",
}
LINK_ENTERS = {**ENTERS, schema.ANALYST: "`link`", schema.NOTE: "`note`, or `link` from a Note"}


def field_text(f: schema.Field) -> str:
    if f.kind == schema.ENUM:
        kind = "one of " + ", ".join(f"`{c}`" for c in f.choices)
    elif f.kind == schema.REF:
        kind = "the id of a " + " or ".join(f.ref)
    else:
        kind = KINDS[f.kind]
    parts = [kind]
    if f.required:
        parts.append("required")
    if f.default is not None:
        parts.append(f"default `{json.dumps(f.default)}`")
    if f.cited:
        parts.append("cites a Source")
    if not f.editable:
        parts.append("fixed")
    text = f"`{f.name}` ({', '.join(parts)})"
    return f"{text}: {f.doc}" if f.doc else text


def _names(names) -> str:
    return ", ".join(f"`{n}`" for n in names)


def objects_table() -> str:
    rows = ["| Type | Is | Id | Stated fields | Derived fields | Enters by |", "|---|---|---|---|---|---|"]
    ids = {"Portfolio": "`portfolio`", "Name": "`name:{TICKER}`", "Metric": "`metric:{id}`; a proxy's price "
           "`metric:price:{TICKER}`", "Market": "`market:{venue}:{id}`"}
    for t in schema.OBJECTS.values():
        derived = [n for n in t.derived if n not in t.watched and n not in t.pointed]
        cell = _names(derived)
        if t.recorded:
            cell += ("; " if cell else "") + "recorded: " + _names(t.recorded)
        if t.pointed:
            cell += "; when watched or read by a derivation: " + _names(t.pointed)
        if t.watched:
            cell += "; when watched: " + _names(t.watched)
        stated = "; ".join(field_text(f) for f in t.stated)
        if t.add_params:
            stated += "; `add` also takes " + _names(t.add_params)
        rows.append(f"| `{t.name}` | {t.doc} | {ids.get(t.name, f'`{t.prefix}:N`')} | {stated} | {cell} | "
                    f"{ENTERS[t.creator]} |")
    return "\n".join(rows)


def links_table() -> str:
    rows = ["| Relation | Is | From | To | Stated fields | Derived fields | Created by |",
            "|---|---|---|---|---|---|---|"]
    for r in schema.RELATIONS.values():
        sources = ", ".join(r.sources) if r.sources else "any derived field"
        targets = "any" if r.targets == ("*",) else ", ".join(r.targets)
        stated = "; ".join(field_text(f) for f in r.stated)
        rows.append(f"| `{r.name}` | {r.doc} | {sources} | {targets} | {stated} | {_names(r.derived)} | "
                    f"{LINK_ENTERS[r.creator]} |")
    return "\n".join(rows)


TABLES = {"objects": objects_table, "links": links_table}


def _block(name: str) -> re.Pattern:
    return re.compile(rf"(<!-- {name}: rendered from harness/schema.py -->)\n(.*?)\n?(<!-- end {name} -->)", re.S)


def render(text: str) -> str:
    """The document with each table replaced by what the schema renders."""
    for name, table in TABLES.items():
        text, n = _block(name).subn(lambda m: f"{m.group(1)}\n{table()}\n{m.group(3)}", text)
        if n != 1:
            raise ValueError(f"ONTOLOGY.md has no single {name} table block")
    return text


def drift(path: str | Path) -> list[str]:
    """The tables whose text in the file is not what the schema renders."""
    text = Path(path).read_text(encoding="utf-8")
    out = []
    for name, table in TABLES.items():
        m = _block(name).search(text)
        if m is None or m.group(2) != table():
            out.append(name)
    return out


if __name__ == "__main__":  # rewrite the tables in place: python -m harness.ontology
    p = Path(__file__).resolve().parent.parent / "knowledge" / "ONTOLOGY.md"
    p.write_text(render(p.read_text(encoding="utf-8")), encoding="utf-8")
