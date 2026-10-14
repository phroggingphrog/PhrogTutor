"""Data Explorer API — read-only browsing, querying and profiling of local SQLite databases.

What it is
----------
A small, self-contained database explorer for the DeepTutor web app: pick a
database, list its tables with row counts, run one read-only SQL statement and
see the result as a table, or profile a table column by column (nulls,
distinct values, range, mean) and chart the most common values of a column.

Safety posture (this module must never become a write path)
-----------------------------------------------------------
* Every source is a **fixed registry entry**, resolved by id. A caller cannot
  name an arbitrary path.
* The databases themselves are bind-mounted read-only into the container, and
  each connection is opened with ``mode=ro`` plus ``PRAGMA query_only=1``.
* Only a single ``SELECT``/``WITH``/``EXPLAIN`` statement is accepted — no
  stacked statements, no DDL/DML, no ``ATTACH``.
* Results are capped (``max_rows``), the statement deadline is capped, and a
  SQLite progress handler aborts a query that scans too long.
* The health of the whole thing depends on the registry below staying small and
  deliberate. Add a source only after deciding whether that data may leave the
  server.
"""

from __future__ import annotations

import html
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

router = APIRouter()


# ── Source registry ───────────────────────────────────────────
#
# Deliberately explicit: only these files are ever opened. ``finance`` and
# ``clinical`` from the brain data layer are intentionally absent — neither may
# be served remotely (see ~/brain/AGENTS.md).
@dataclass(frozen=True, slots=True)
class Source:
    id: str
    label: str
    path: Path
    description: str = ""


_SOURCES: tuple[Source, ...] = (
    Source(
        id="general",
        label="General",
        path=Path("/sources/data/general.sqlite"),
        description="General personal-data store (read-only snapshot).",
    ),
    Source(
        id="personal",
        label="Personal",
        path=Path("/sources/data/personal.sqlite"),
        description="Personal-data store (read-only snapshot).",
    ),
)

_SOURCE_BY_ID = {s.id: s for s in _SOURCES}

MAX_ROWS = 500
DEFAULT_ROWS = 100
QUERY_TIMEOUT_S = 10.0
MAX_SCAN_OPS = 40_000_000
PROFILE_SAMPLE_ROWS = 50_000

_ALLOWED_PREFIXES = ("select", "with", "explain")


def _resolve_source(source_id: str) -> Source:
    source = _SOURCE_BY_ID.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown source '{source_id}'")
    if not source.path.exists():
        raise HTTPException(status_code=409, detail=f"Source '{source_id}' is not available")
    return source


def _connect(source: Source) -> sqlite3.Connection:
    """Open one read-only connection with the guards applied."""
    uri = f"file:{source.path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = None
    conn.execute("PRAGMA query_only = 1")
    conn.execute("PRAGMA busy_timeout = 5000")
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    counter = {"ops": 0}

    def _guard() -> int:
        counter["ops"] += 1000
        if counter["ops"] > MAX_SCAN_OPS or time.monotonic() > deadline:
            return 1
        return 0

    conn.set_progress_handler(_guard, 1000)
    return conn


def _single_statement(sql: str) -> str:
    """Accept exactly one read-only statement, stripped of a trailing semicolon."""
    statement = (sql or "").strip()
    if statement.endswith(";"):
        statement = statement[:-1].strip()
    if not statement:
        raise HTTPException(status_code=400, detail="Provide a SQL statement")
    if ";" in statement:
        raise HTTPException(status_code=400, detail="Run one statement at a time")
    lowered = statement.lstrip("( \n\t").lower()
    if not lowered.startswith(_ALLOWED_PREFIXES):
        raise HTTPException(
            status_code=400,
            detail="Only read-only SELECT / WITH / EXPLAIN statements are allowed here",
        )
    for banned in ("attach", "pragma writable_schema", "load_extension"):
        if banned in lowered:
            raise HTTPException(status_code=400, detail=f"'{banned}' is not allowed")
    return statement


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _rows_as_dicts(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, params)
    columns = [d[0] for d in (cursor.description or [])]
    out: list[dict[str, Any]] = []
    for row in cursor.fetchall():
        out.append({columns[i]: row[i] for i in range(len(columns))})
    return out


# ── Sources and schema ────────────────────────────────────────


@router.get("/sources")
async def list_sources() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for source in _SOURCES:
        available = source.path.exists()
        stat = source.path.stat() if available else None
        rows.append(
            {
                "id": source.id,
                "label": source.label,
                "description": source.description,
                "available": available,
                "size_bytes": stat.st_size if stat else 0,
                "modified_at": stat.st_mtime if stat else None,
            }
        )
    return {"sources": rows}


@router.get("/schema")
async def schema(source: str = Query(..., description="Source id")) -> dict[str, Any]:
    entry = _resolve_source(source)
    conn = _connect(entry)
    try:
        tables = _rows_as_dicts(
            conn,
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name",
        )
        out: list[dict[str, Any]] = []
        for table in tables:
            name = str(table["name"])
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {_quote(name)}").fetchone()[0]
            except sqlite3.Error:
                count = None
            columns = _rows_as_dicts(conn, f"PRAGMA table_info({_quote(name)})")
            out.append(
                {
                    "name": name,
                    "kind": table["type"],
                    "row_count": count,
                    "columns": [
                        {
                            "name": c.get("name"),
                            "type": c.get("type") or "",
                            "primary_key": bool(c.get("pk")),
                            "not_null": bool(c.get("notnull")),
                        }
                        for c in columns
                    ],
                }
            )
        return {"source": entry.id, "label": entry.label, "tables": out}
    finally:
        conn.close()


# ── Query ─────────────────────────────────────────────────────


@router.post("/query")
async def run_query(payload: dict[str, Any]) -> dict[str, Any]:
    entry = _resolve_source(str(payload.get("source") or ""))
    statement = _single_statement(str(payload.get("sql") or ""))
    try:
        limit = int(payload.get("limit") or DEFAULT_ROWS)
    except (TypeError, ValueError):
        limit = DEFAULT_ROWS
    limit = max(1, min(limit, MAX_ROWS))

    capped = f"SELECT * FROM ({statement}) LIMIT {limit + 1}"
    started = time.monotonic()
    conn = _connect(entry)
    try:
        cursor = conn.execute(capped)
        columns = [d[0] for d in (cursor.description or [])]
        raw = cursor.fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=400, detail=f"SQLite: {exc}") from None
    finally:
        conn.close()

    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    truncated = len(raw) > limit
    rows = [
        [None if v is None else (v if isinstance(v, (int, float, str)) else str(v)) for v in row]
        for row in raw[:limit]
    ]
    return {
        "source": entry.id,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
    }


# ── Profiling (the "analytics" half) ──────────────────────────


@router.post("/profile")
async def profile_table(payload: dict[str, Any]) -> dict[str, Any]:
    entry = _resolve_source(str(payload.get("source") or ""))
    table = str(payload.get("table") or "").strip()
    if not table:
        raise HTTPException(status_code=400, detail="Provide a table name")

    conn = _connect(entry)
    try:
        existing = {
            str(r["name"])
            for r in _rows_as_dicts(
                conn,
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')",
            )
        }
        if table not in existing:
            raise HTTPException(status_code=404, detail=f"Unknown table '{table}'")
        total = conn.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0]
        columns = _rows_as_dicts(conn, f"PRAGMA table_info({_quote(table)})")

        # Profile over a bounded sample so a 400 MB table cannot stall the page.
        source_sql = (
            f"(SELECT * FROM {_quote(table)} LIMIT {PROFILE_SAMPLE_ROWS})"
            if total and total > PROFILE_SAMPLE_ROWS
            else f"(SELECT * FROM {_quote(table)})"
        )

        profiles: list[dict[str, Any]] = []
        for column in columns:
            name = str(column["name"])
            target = _quote(name)
            try:
                stats = conn.execute(
                    f"SELECT COUNT({target}), COUNT(DISTINCT {target}), "
                    f"MIN({target}), MAX({target}) FROM {source_sql}"
                ).fetchone()
            except sqlite3.Error:
                stats = (None, None, None, None)
            numeric = False
            mean = None
            declared = str(column.get("type") or "").lower()
            looks_numeric = any(
                token in declared for token in ("int", "real", "num", "dec", "double", "float")
            )
            if stats and stats[1] and total and looks_numeric:
                try:
                    mean = conn.execute(f"SELECT AVG(CAST({target} AS REAL)) FROM {source_sql}").fetchone()[0]
                    numeric = mean is not None
                except sqlite3.Error:
                    mean = None
            non_null = stats[0] if stats else 0
            profiles.append(
                {
                    "name": name,
                    "declared_type": column.get("type") or "",
                    "primary_key": bool(column.get("pk")),
                    "non_null": non_null,
                    "nulls": (total or 0) - (non_null or 0),
                    "distinct": stats[1] if stats else None,
                    "min": _json_safe(stats[2]) if stats else None,
                    "max": _json_safe(stats[3]) if stats else None,
                    "mean": round(mean, 4) if numeric else None,
                }
            )
        return {
            "source": entry.id,
            "table": table,
            "row_count": total,
            "sampled": bool(total and total > PROFILE_SAMPLE_ROWS),
            "sample_rows": min(total or 0, PROFILE_SAMPLE_ROWS),
            "columns": profiles,
        }
    finally:
        conn.close()


@router.post("/values")
async def top_values(payload: dict[str, Any]) -> dict[str, Any]:
    """Most frequent values of one column — the chart behind the table profile."""
    entry = _resolve_source(str(payload.get("source") or ""))
    table = str(payload.get("table") or "").strip()
    column = str(payload.get("column") or "").strip()
    if not table or not column:
        raise HTTPException(status_code=400, detail="Provide both a table and a column")
    try:
        limit = int(payload.get("limit") or 15)
    except (TypeError, ValueError):
        limit = 15
    limit = max(1, min(limit, 50))

    conn = _connect(entry)
    try:
        columns = {
            str(r["name"])
            for r in _rows_as_dicts(conn, f"PRAGMA table_info({_quote(table)})")
        }
        if column not in columns:
            raise HTTPException(status_code=404, detail=f"Unknown column '{column}'")
        target = _quote(column)
        rows = conn.execute(
            f"SELECT {target} AS value, COUNT(*) AS n FROM {_quote(table)} "
            f"GROUP BY {target} ORDER BY n DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {
            "source": entry.id,
            "table": table,
            "column": column,
            "values": [
                {"value": _json_safe(row[0]), "count": row[1]} for row in rows
            ],
        }
    except sqlite3.Error as exc:
        raise HTTPException(status_code=400, detail=f"SQLite: {exc}") from None
    finally:
        conn.close()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


# ── The explorer page ─────────────────────────────────────────


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def explorer_ui() -> HTMLResponse:
    """A self-contained explorer page, served from the API side.

    Deliberately one file with no build step: the container ships a compiled
    Next.js bundle, so a page that lives here can be added without rebuilding
    the frontend. The cost is that it renders outside DeepTutor's own shell.
    """
    return HTMLResponse(_UI_HTML)


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value))


_UI_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Data Explorer</title>
<style>
  :root { color-scheme: light dark; --line: #8883; --muted: #8a8a8e; --accent: #2f7d5d; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; }
  header { display: flex; gap: 12px; align-items: center; padding: 10px 14px; border-bottom: 1px solid var(--line); }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; }
  header .spacer { flex: 1; }
  main { display: grid; grid-template-columns: 260px 1fr; height: calc(100vh - 47px); }
  aside { border-right: 1px solid var(--line); overflow: auto; padding: 10px; }
  section { display: flex; flex-direction: column; overflow: hidden; }
  .qbar { display: flex; gap: 8px; padding: 10px 14px; border-bottom: 1px solid var(--line); align-items: flex-start; }
  textarea { flex: 1; min-height: 78px; font: 13px/1.4 ui-monospace, SFMono-Regular, Consolas, monospace; padding: 8px; border: 1px solid var(--line); border-radius: 8px; background: transparent; color: inherit; resize: vertical; }
  button { font: inherit; padding: 5px 11px; border: 1px solid var(--line); border-radius: 8px; background: transparent; color: inherit; cursor: pointer; }
  button:hover { border-color: var(--accent); color: var(--accent); }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  select, input[type=number] { font: inherit; padding: 4px 6px; border: 1px solid var(--line); border-radius: 6px; background: transparent; color: inherit; }
  .tables { list-style: none; margin: 0; padding: 0; }
  .tables li { padding: 5px 7px; border-radius: 7px; cursor: pointer; display: flex; justify-content: space-between; gap: 8px; }
  .tables li:hover { background: #8881; }
  .tables li.active { background: #2f7d5d22; }
  .tables small { color: var(--muted); }
  .status { padding: 6px 14px; color: var(--muted); font-size: 12px; min-height: 22px; }
  .status.error { color: #c0392b; }
  .panes { flex: 1; overflow: auto; padding: 0 14px 14px; }
  table { border-collapse: collapse; font-size: 12.5px; width: 100%; }
  th, td { border: 1px solid var(--line); padding: 4px 7px; text-align: left; vertical-align: top; max-width: 34ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  th { position: sticky; top: 0; background: Canvas; }
  .bars { margin-top: 14px; }
  .bar { display: grid; grid-template-columns: minmax(90px, 220px) 1fr 60px; gap: 8px; align-items: center; margin: 3px 0; font-size: 12.5px; }
  .bar .fill { background: var(--accent); height: 13px; border-radius: 3px; min-width: 2px; }
  .bar .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar .n { text-align: right; color: var(--muted); }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 16px 0 6px; }
  .hint { color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>Data Explorer</h1>
  <select id="source"></select>
  <span class="hint" id="sourcedesc"></span>
  <span class="spacer"></span>
  <span class="hint">read-only &middot; single SELECT &middot; max <span id="maxrows">100</span> rows</span>
</header>
<main>
  <aside><ul class="tables" id="tables"></ul></aside>
  <section>
    <div class="qbar">
      <textarea id="sql" spellcheck="false" placeholder="SELECT ..."></textarea>
      <div style="display:flex; flex-direction:column; gap:6px;">
        <button class="primary" id="run">Run (Ctrl+Enter)</button>
        <button id="profile">Profile table</button>
        <label class="hint">rows <input type="number" id="limit" value="100" min="1" max="500" style="width:64px"></label>
      </div>
    </div>
    <div class="status" id="status"></div>
    <div class="panes" id="panes"></div>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
let schema = [], currentTable = null;

async function api(path, options) {
  const res = await fetch('/api/data-explorer' + path, Object.assign({headers: {'Content-Type': 'application/json'}}, options));
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = {detail: text}; }
  if (!res.ok) throw new Error((data && data.detail) || ('HTTP ' + res.status));
  return data;
}
function status(msg, isError) {
  const el = $('status'); el.textContent = msg || ''; el.className = 'status' + (isError ? ' error' : '');
}
function fmt(n) { return typeof n === 'number' ? n.toLocaleString() : (n === null ? '' : String(n)); }

async function loadSources() {
  const data = await api('/sources');
  const sel = $('source'); sel.innerHTML = '';
  data.sources.filter(s => s.available).forEach(s => {
    const o = document.createElement('option'); o.value = s.id; o.textContent = s.label; sel.appendChild(o);
  });
  if (!sel.options.length) { status('No databases are mounted for this deployment.', true); return; }
  sel.onchange = () => { $('sourcedesc').textContent = (data.sources.find(s => s.id === sel.value) || {}).description || ''; loadSchema(); };
  $('sourcedesc').textContent = (data.sources.find(s => s.id === sel.value) || {}).description || '';
  await loadSchema();
}

async function loadSchema() {
  const source = $('source').value;
  status('Loading tables...');
  const data = await api('/schema?source=' + encodeURIComponent(source));
  schema = data.tables;
  const ul = $('tables'); ul.innerHTML = '';
  if (!schema.length) { ul.innerHTML = '<li class="hint">no tables</li>'; }
  schema.forEach(t => {
    const li = document.createElement('li');
    li.innerHTML = '<span>' + t.name + '</span><small>' + (t.row_count === null ? '?' : fmt(t.row_count)) + '</small>';
    li.onclick = () => selectTable(t, li);
    ul.appendChild(li);
  });
  status(schema.length + ' tables in ' + data.label);
  if (!currentTable && schema.length) {
    const first = ul.querySelector('li');
    if (first) selectTable(schema[0], first);
  }
}

function selectTable(t, li) {
  currentTable = t.name;
  document.querySelectorAll('.tables li').forEach(x => x.classList.remove('active'));
  if (li) li.classList.add('active');
  $('sql').value = 'SELECT * FROM "' + t.name.replace(/"/g, '""') + '" LIMIT ' + $('limit').value;
  runQuery();
}

async function runQuery() {
  const source = $('source').value;
  status('Running...');
  try {
    const data = await api('/query', {method: 'POST', body: JSON.stringify({source, sql: $('sql').value, limit: Number($('limit').value) || 100})});
    renderTable(data);
    status(data.row_count + ' row' + (data.row_count === 1 ? '' : 's') + (data.truncated ? ' (truncated)' : '') + ' in ' + data.elapsed_ms + ' ms');
  } catch (e) { status(e.message, true); }
}

function renderTable(data) {
  const panes = $('panes');
  if (!data.columns.length || !data.rows.length) { panes.innerHTML = '<p class="hint">No rows.</p>'; return; }
  let html = '<table><thead><tr>' + data.columns.map(c => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>';
  data.rows.forEach(r => { html += '<tr>' + r.map(v => '<td title="' + String(v ?? '').replace(/"/g, '&quot;') + '">' + (v === null ? '<span class="hint">null</span>' : String(v)) + '</td>').join('') + '</tr>'; });
  panes.innerHTML = html + '</tbody></table>';
}

async function profileTable() {
  if (!currentTable) { status('Pick a table first', true); return; }
  const source = $('source').value;
  status('Profiling ' + currentTable + '...');
  try {
    const data = await api('/profile', {method: 'POST', body: JSON.stringify({source, table: currentTable})});
    let html = '<h2>Columns &middot; ' + fmt(data.row_count) + ' rows' + (data.sampled ? ' (sampled ' + fmt(data.sample_rows) + ')' : '') + '</h2>';
    html += '<table><thead><tr><th>column</th><th>type</th><th>non-null</th><th>nulls</th><th>distinct</th><th>min</th><th>max</th><th>mean</th></tr></thead><tbody>';
    data.columns.forEach(c => {
      html += '<tr><td>' + c.name + (c.primary_key ? ' <small>pk</small>' : '') + '</td><td>' + (c.declared_type || '') + '</td><td>' + fmt(c.non_null) + '</td><td>' + fmt(c.nulls) + '</td><td>' + fmt(c.distinct) + '</td><td>' + fmt(c.min) + '</td><td>' + fmt(c.max) + '</td><td>' + (c.mean === null ? '' : fmt(c.mean)) + '</td></tr>';
    });
    html += '</tbody></table>';
    html += '<h2>Most common values</h2><div class="hint">Click a column above to chart it.</div><div class="bars" id="bars"></div>';
    $('panes').innerHTML = html;
    const rowEls = document.querySelectorAll('#panes tbody tr');
    data.columns.forEach((c, i) => { if (rowEls[i]) { rowEls[i].style.cursor = 'pointer'; rowEls[i].onclick = () => chartColumn(c.name); } });
    status('Profiled ' + currentTable);
  } catch (e) { status(e.message, true); }
}

async function chartColumn(column) {
  const source = $('source').value;
  const bars = $('bars'); if (!bars) return;
  bars.innerHTML = '<p class="hint">Loading ' + column + '...</p>';
  try {
    const data = await api('/values', {method: 'POST', body: JSON.stringify({source, table: currentTable, column})});
    if (!data.values.length) { bars.innerHTML = '<p class="hint">No values.</p>'; return; }
    const max = Math.max.apply(null, data.values.map(v => v.count));
    bars.innerHTML = data.values.map(v => {
      const label = v.value === null ? 'null' : String(v.value);
      return '<div class="bar"><span class="label" title="' + label.replace(/"/g, '&quot;') + '">' + label + '</span><span class="fill" style="width:' + Math.max(2, Math.round(100 * v.count / max)) + '%"></span><span class="n">' + fmt(v.count) + '</span></div>';
    }).join('');
  } catch (e) { bars.innerHTML = '<p class="status error">' + e.message + '</p>'; }
}

window.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runQuery(); } });
$('run').onclick = runQuery;
$('profile').onclick = profileTable;
$('limit').oninput = () => { $('maxrows').textContent = $('limit').value; };
loadSources().catch(e => status(e.message, true));
</script>
</body>
</html>
"""
