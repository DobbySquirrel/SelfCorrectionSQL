"""CTE brick DAG: store LLM step outputs as nodes ``Step{K}_{k}`` with ref edges.

Naming
------
- ``K`` = which sub-question (1-based) in the progressive plan
- ``k`` = ordinal of the unit inside that LLM emission (1-based, CTE order;
  a non-trivial trailing SELECT after WITH counts as its own unit)

Cases handled when ingesting one LLM blob
-----------------------------------------
1. Bare SELECT that refs prior DAG nodes
2. Bare SELECT with no prior-CTE refs (orphan brick)
3. Multi-CTE WITH that refs prior + internal edges
4. Multi-CTE WITH, no prior refs, but internal edges
5. Multi-CTE WITH, no prior refs, no internal edges (orphan forest)
6. WITH whose trailing SELECT is not trivial ``SELECT * FROM name``
   → trailing body becomes another unit (same as a CTE brick)

Policy note
-----------
Generation (beam Yi) should **reject 0-row** step results and re-sample until
non-empty. ``CteDAG.ingest(..., require_nonzero=True)`` mirrors that at store
time: empty bricks are not created (event ``reject_empty``).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import sqlite3
import time

from workflows.mcts_v4.actions.deepeye_cte_plugin import (
    _apply_cte_rename,
    _balanced_paren_end,
    _is_synthetic_cte_name,
    _norm_sql_body,
    extract_cte_defs,
    result_sig,
)


def exec_rows_with_cols(
    db_path: Path,
    sql: str,
    timeout_s: float = 30.0,
) -> Tuple[Optional[List[tuple]], Optional[str], List[str]]:
    """Like exec_rows but also return column names from cursor.description."""
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        return None, "empty", []
    if not Path(db_path).is_file():
        return None, f"missing_db:{db_path}", []
    try:
        conn = sqlite3.connect(str(db_path), timeout=min(float(timeout_s), 30.0))
        try:
            t0 = time.time()
            limit = max(0.5, float(timeout_s))

            def _progress() -> int:
                return 1 if (time.time() - t0) >= limit else 0

            conn.set_progress_handler(_progress, 1000)
            cur = conn.cursor()
            cur.execute(sql)
            cols = [d[0] for d in (cur.description or []) if d and d[0]]
            rows = cur.fetchall()
            conn.set_progress_handler(None, 0)
            return rows, None, cols
        finally:
            conn.close()
    except Exception as e:
        return None, str(e)[:240], []


def format_result_for_prompt(
    columns: Sequence[str],
    rows: Optional[List[tuple]],
    err: Optional[str],
    *,
    max_rows: int = 2,
    max_cell: int = 24,
    max_chars: int = 160,
) -> str:
    """Human preview: columns + a few rows (not just n=)."""
    if err:
        return f"ERR: {re.sub(r'\s+', ' ', str(err))[:100]}"
    if rows is None:
        return "no-rows"
    n = len(rows)
    col_s = ", ".join(columns) if columns else "?"

    def _cell(v: Any) -> str:
        s = str(v).replace("\n", " ")
        return s if len(s) <= max_cell else s[: max_cell - 1] + "…"

    sample_bits: List[str] = []
    for r in rows[:max_rows]:
        if isinstance(r, (list, tuple)):
            sample_bits.append("(" + ", ".join(_cell(x) for x in r) + ")")
        else:
            sample_bits.append(_cell(r))
    sample = "; ".join(sample_bits) if sample_bits else "∅"
    if n > max_rows:
        sample += f" …(+{n - max_rows})"
    text = f"n={n} cols=[{col_s}] sample={sample}"
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text


def abbrev_exec_result(
    rows: Optional[List[tuple]],
    err: Optional[str],
    *,
    max_rows: int = 2,
    max_cell: int = 28,
    max_chars: int = 140,
) -> Tuple[str, Optional[int]]:
    """Short one-line-ish preview for graph labels. Returns (text, n_rows)."""
    if err:
        e = re.sub(r"\s+", " ", str(err))[:80]
        return f"ERR: {e}", None
    if rows is None:
        return "no-rows", None
    n = len(rows)

    def _cell(v: Any) -> str:
        s = str(v)
        s = s.replace("\n", " ").replace("\r", " ")
        if len(s) > max_cell:
            s = s[: max_cell - 1] + "…"
        return s

    parts: List[str] = []
    for r in rows[:max_rows]:
        if isinstance(r, (list, tuple)):
            parts.append("(" + ", ".join(_cell(x) for x in r) + ")")
        else:
            parts.append(_cell(r))
    body = "; ".join(parts) if parts else "∅"
    if n > max_rows:
        body += f" …(+{n - max_rows})"
    text = f"n={n} {body}"
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text, n

# Canonical node id: Step{K}_{k}
_NODE_ID_RE = re.compile(r"^Step(\d+)_(\d+)$")
_STEP_LEGACY_RE = re.compile(r"^step_(\d+)$", re.I)
_SQL_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")


def make_node_id(question_k: int, local_k: int) -> str:
    return f"Step{int(question_k)}_{int(local_k)}"


def parse_node_id(node_id: str) -> Optional[Tuple[int, int]]:
    m = _NODE_ID_RE.match((node_id or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def prior_question_k_from_name(name: str) -> Optional[int]:
    """Map a CTE alias to a prior plan index K (1-based), if it looks like Step*.

    - ``Step3_1`` / ``Step3`` → 3
    - ``step_2`` (legacy) → 3  (0-based step index)
    - generic names (``step_k``, ``revised``, …) → None
    """
    s = (name or "").strip()
    if not s:
        return None
    m = re.match(r"(?i)^step_(\d+)$", s)
    if m:
        return int(m.group(1)) + 1
    m = re.match(r"(?i)^step(\d+)(?:_(\d+))?$", s)
    if m:
        return int(m.group(1))
    return None


def _is_trivial_select_star(sel: str) -> bool:
    """True for ``SELECT * FROM name`` / ``SELECT * FROM name AS a`` only."""
    s = (sel or "").strip().rstrip(";").strip()
    return bool(
        re.match(
            r"(?is)^SELECT\s+\*\s+FROM\s+[A-Za-z_][A-Za-z0-9_]*"
            r"(?:\s+(?:AS\s+)?[A-Za-z_][A-Za-z0-9_]*)?\s*$",
            s,
        )
    )


def extract_with_tail(sql: str) -> Tuple[List[Tuple[str, str]], Optional[str], int]:
    """Parse WITH defs + trailing SELECT.

    Returns (defs, trailing_select_or_None, end_index_of_last_cte_paren).
    """
    raw = (sql or "").strip().rstrip(";")
    defs = extract_cte_defs(raw)
    if not defs:
        return [], None, -1
    m = re.match(r"(?is)^\s*WITH\s+", raw)
    if not m:
        return defs, None, -1
    i = m.end()
    m_rec = re.match(r"(?is)RECURSIVE\s+", raw[i:])
    if m_rec:
        i += m_rec.end()
    n = len(raw)
    last_end = -1
    parsed = 0
    while i < n and parsed < len(defs):
        while i < n and raw[i].isspace():
            i += 1
        mname = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", raw[i:])
        if not mname:
            break
        i += mname.end()
        while i < n and raw[i].isspace():
            i += 1
        if i < n and raw[i] == "(":
            end_cols = _balanced_paren_end(raw, i)
            if end_cols is None:
                break
            i = end_cols + 1
            while i < n and raw[i].isspace():
                i += 1
        mas = re.match(r"(?is)AS\s*\(", raw[i:])
        if not mas:
            break
        i += mas.end() - 1
        end = _balanced_paren_end(raw, i)
        if end is None:
            break
        last_end = end
        i = end + 1
        parsed += 1
        while i < n and raw[i].isspace():
            i += 1
        if i < n and raw[i] == ",":
            i += 1
            continue
        break
    tail = raw[i:].strip() if last_end >= 0 else ""
    if not tail:
        return defs, None, last_end
    # drop leading junk before SELECT
    msel = re.search(r"(?is)\bSELECT\b", tail)
    if not msel:
        return defs, None, last_end
    return defs, tail[msel.start() :].strip(), last_end


def split_llm_units(sql: str) -> List[Tuple[str, str]]:
    """Split one LLM emission into ordered units ``(local_name, body)``.

    - Bare SELECT → one unit named ``_unit``
    - WITH defs → each CTE; non-trivial trailing SELECT → extra unit ``_final``
    """
    raw = (sql or "").strip().rstrip(";")
    if not raw:
        return []
    if not re.match(r"(?is)^\s*WITH\b", raw):
        body = raw
        # strip outer WITH wrapper if model forgot and we only have SELECT
        return [("_unit", body)]
    defs, tail, _ = extract_with_tail(raw)
    units: List[Tuple[str, str]] = list(defs)
    if tail and not _is_trivial_select_star(tail):
        units.append(("_final", tail))
    return units


@dataclass
class CteNode:
    id: str
    body: str
    body_norm: str
    question_k: int
    local_k: int
    refs: List[str] = field(default_factory=list)  # dependency node ids
    result_sig: Optional[str] = None
    exec_ok: Optional[bool] = None
    exec_error: Optional[str] = None
    n_rows: Optional[int] = None
    columns: List[str] = field(default_factory=list)
    sample_rows: List[List[Any]] = field(default_factory=list)  # first few rows
    result_preview: str = ""  # abbreviated exec result for viz / prompt
    sub_question: str = ""  # joined sub-task text(s) for prompt table
    sub_tasks: List[str] = field(default_factory=list)  # "[Qk] …" tags (reuse appends)
    source_names: List[str] = field(default_factory=list)  # model-local names
    related: bool = False  # True if refs non-empty at ingest time

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def add_sub_task(self, question_k: int, sub_q: str) -> None:
        """Record sub-task; on reuse, append later Qk text onto the same node."""
        sq = (sub_q or "").strip()
        if not sq:
            return
        tag = f"[Q{int(question_k)}] {sq}"
        for existing in self.sub_tasks:
            if existing == tag or existing.endswith(sq):
                self.sub_question = " ; ".join(self.sub_tasks)
                return
        self.sub_tasks.append(tag)
        # Use " ; " (not "|") so markdown table cells stay intact.
        self.sub_question = " ; ".join(self.sub_tasks)


@dataclass
class IngestEvent:
    question_k: int
    local_k: int
    source_name: str
    action: str  # "create" | "reuse" | "skip_empty" | "reject_empty"
    node_id: Optional[str]
    reused_from: Optional[str] = None
    refs: List[str] = field(default_factory=list)
    related: bool = False
    body_preview: str = ""


@dataclass
class IngestResult:
    question_k: int
    events: List[IngestEvent] = field(default_factory=list)
    created_ids: List[str] = field(default_factory=list)
    reused_ids: List[str] = field(default_factory=list)
    leaf_id: Optional[str] = None
    local_to_node: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_k": self.question_k,
            "events": [asdict(e) for e in self.events],
            "created_ids": list(self.created_ids),
            "reused_ids": list(self.reused_ids),
            "leaf_id": self.leaf_id,
            "local_to_node": dict(self.local_to_node),
        }


class CteDAG:
    """Accumulating brick store: nodes ``StepK_k``, edges via ``node.refs``."""

    def __init__(self) -> None:
        self.nodes: Dict[str, CteNode] = {}
        # body_norm (+ optional sig) → node_id for dedup
        self._by_norm: Dict[str, List[str]] = {}
        self.history: List[IngestResult] = []

    # ------------------------------------------------------------------ lookup
    def get(self, node_id: str) -> Optional[CteNode]:
        return self.nodes.get(node_id)

    def known_names(self) -> Set[str]:
        names = set(self.nodes.keys())
        for n in self.nodes.values():
            names.update(self.nodes.keys())
            for sn in n.source_names:
                if sn:
                    names.add(sn)
        return names

    def _index_node(self, node: CteNode) -> None:
        self.nodes[node.id] = node
        self._by_norm.setdefault(node.body_norm, []).append(node.id)

    def find_match(
        self,
        body: str,
        *,
        sig: Optional[str] = None,
        require_sig: bool = False,
    ) -> Optional[str]:
        """Return existing node id if body (+ optional exec sig) matches."""
        bn = _norm_sql_body(body)
        if not bn:
            return None
        cands = self._by_norm.get(bn) or []
        if not cands:
            # also scan (in case norm collision policy changes)
            cands = [nid for nid, n in self.nodes.items() if n.body_norm == bn]
        if not cands:
            return None
        if sig is None:
            return None if require_sig else cands[0]
        for nid in cands:
            n = self.nodes[nid]
            if n.result_sig and n.result_sig == sig:
                return nid
        # same body, no sig agreement → still reuse first body match
        return None if require_sig else cands[0]

    # --------------------------------------------------------------- ref scan
    def _ref_candidates(self, local_names: Sequence[str]) -> Set[str]:
        """Identifiers that may count as CTE refs (not base tables)."""
        cands: Set[str] = set(self.nodes.keys())
        for nm in local_names:
            if nm:
                cands.add(nm)
        for n in self.nodes.values():
            for sn in n.source_names:
                if sn and _is_synthetic_cte_name(sn):
                    cands.add(sn)
            # always allow StepK_k / step_N
            cands.add(n.id)
        # legacy step_i always eligible
        for i in range(0, 64):
            cands.add(f"step_{i}")
            cands.add(f"Step{i}")
        return {c for c in cands if c}

    def extract_refs(self, body: str, local_names: Sequence[str]) -> List[str]:
        """Return CTE-like identifiers referenced in body (stable order)."""
        cands = self._ref_candidates(local_names)
        # lower→canonical for matching
        lower_map = {c.lower(): c for c in cands}
        found: List[str] = []
        seen: Set[str] = set()
        for m in _SQL_IDENT_RE.finditer(body or ""):
            tok = m.group(1)
            key = tok.lower()
            if key not in lower_map:
                continue
            # skip self-def noise later; here just collect
            canon = lower_map[key]
            if canon.lower() in seen:
                continue
            seen.add(canon.lower())
            found.append(canon)
        return found

    def _resolve_ref(
        self,
        name: str,
        local_to_node: Dict[str, str],
    ) -> Optional[str]:
        if not name:
            return None
        if name in local_to_node:
            return local_to_node[name]
        if name in self.nodes:
            return name
        # case-insensitive node id
        for nid in self.nodes:
            if nid.lower() == name.lower():
                return nid
        # source name → node
        for n in self.nodes.values():
            for sn in n.source_names:
                if sn.lower() == name.lower():
                    return n.id
        # legacy step_i → prefer node that used that source name, else Step{i+1}_*
        m = _STEP_LEGACY_RE.match(name)
        if m:
            idx = int(m.group(1))
            # Prefer exact source alias
            for n in self.nodes.values():
                if any(s.lower() == name.lower() for s in n.source_names):
                    return n.id
            # Prefer Step{idx+1}_* leaf-ish (question idx 1-based ≈ step_0)
            qk = idx + 1
            hits = [
                n.id
                for n in self.nodes.values()
                if n.question_k == qk
            ]
            if hits:
                # last local of that question
                hits.sort(key=lambda x: parse_node_id(x) or (0, 0))
                return hits[-1]
        return None

    # ---------------------------------------------------------------- ingest
    def ingest(
        self,
        question_k: int,
        llm_sql: str,
        *,
        db_path: Optional[Path] = None,
        timeout_s: float = 15.0,
        exec_for_dedup: bool = True,
        alias_hints: Optional[Dict[str, str]] = None,
        require_nonzero: bool = False,
        sub_question: str = "",
    ) -> IngestResult:
        """Ingest one LLM emission for sub-question ``question_k`` (1-based).

        Dedup: body_norm (+ exec result_sig when available). On hit → reuse.
        On miss → create ``Step{K}_{k}`` with remapped body and ref edges.

        If ``require_nonzero`` and the probe returns 0 rows, do not create a node
        (event ``reject_empty``); caller should regenerate the LLM step.
        """
        K = int(question_k)
        sub_q = (sub_question or "").strip()
        units = split_llm_units(llm_sql)
        result = IngestResult(question_k=K)
        if not units:
            self.history.append(result)
            return result

        local_names = [u[0] for u in units]
        local_to_node: Dict[str, str] = dict(alias_hints or {})
        # Seed aliases: node ids + create-time source name + legacy step_{K-1}→leaf(K)
        by_q: Dict[int, List[CteNode]] = {}
        for nid, n in self.nodes.items():
            local_to_node.setdefault(nid, nid)
            if n.source_names and n.source_names[0]:
                # only the original create name (ignore later reuse aliases)
                local_to_node.setdefault(n.source_names[0], nid)
            by_q.setdefault(n.question_k, []).append(n)
        for qk, ns in by_q.items():
            leaf = max(ns, key=lambda x: x.local_k)
            local_to_node[f"step_{qk - 1}"] = leaf.id

        last_id: Optional[str] = None
        for local_k, (src_name, raw_body) in enumerate(units, start=1):
            preview = _norm_sql_body(raw_body)[:120]
            if not (raw_body or "").strip():
                result.events.append(
                    IngestEvent(
                        question_k=K,
                        local_k=local_k,
                        source_name=src_name,
                        action="skip_empty",
                        node_id=None,
                        body_preview=preview,
                    )
                )
                continue

            # Remap known aliases inside body before ref extract / dedup
            body = _apply_cte_rename(raw_body, local_to_node)

            raw_refs = self.extract_refs(body, local_names)
            # drop self name
            raw_refs = [r for r in raw_refs if r.lower() != src_name.lower()]
            resolved_refs: List[str] = []
            seen_r: Set[str] = set()
            for r in raw_refs:
                rid = self._resolve_ref(r, local_to_node)
                if rid and rid not in seen_r:
                    # do not self-ref
                    if rid.lower() == make_node_id(K, local_k).lower():
                        continue
                    seen_r.add(rid)
                    resolved_refs.append(rid)

            related = bool(resolved_refs)

            # Try body-only match first; refine with exec sig when possible
            match = self.find_match(body, sig=None, require_sig=False)
            sig: Optional[str] = None
            exec_ok: Optional[bool] = None
            exec_err: Optional[str] = None

            rows_probe: Optional[List[tuple]] = None
            cols_probe: List[str] = []
            preview_txt = ""
            n_rows: Optional[int] = None
            if exec_for_dedup and db_path is not None:
                trial_sql = self.compose_sql(
                    extra_body=body,
                    extra_refs=resolved_refs,
                    extra_name="__probe__",
                )
                rows_probe, err, cols_probe = exec_rows_with_cols(
                    Path(db_path), trial_sql, timeout_s=timeout_s
                )
                exec_ok = err is None and rows_probe is not None
                exec_err = err
                preview_txt = format_result_for_prompt(cols_probe, rows_probe, err)
                _, n_rows = abbrev_exec_result(rows_probe, err)
                if exec_ok:
                    sig = result_sig(rows_probe)
                    match_sig = self.find_match(body, sig=sig, require_sig=False)
                    if match_sig:
                        match = match_sig

            if match:
                # Structure exists → reuse; wire local name for later units
                local_to_node[src_name] = match
                local_to_node[make_node_id(K, local_k)] = match
                n = self.nodes[match]
                if src_name and src_name not in n.source_names:
                    n.source_names.append(src_name)
                # Same brick for Q2 and Q3 → keep both sub-tasks on the node
                n.add_sub_task(K, sub_q)
                # backfill preview if we just executed and node lacked one
                if preview_txt and not n.result_preview:
                    n.result_preview = preview_txt
                    n.n_rows = n_rows
                    n.exec_ok = exec_ok
                    n.exec_error = exec_err
                    n.columns = list(cols_probe)
                    n.sample_rows = [list(r) for r in (rows_probe or [])[:3]]
                    if sig:
                        n.result_sig = sig
                result.reused_ids.append(match)
                result.events.append(
                    IngestEvent(
                        question_k=K,
                        local_k=local_k,
                        source_name=src_name,
                        action="reuse",
                        node_id=match,
                        reused_from=match,
                        refs=list(resolved_refs),
                        related=related,
                        body_preview=preview,
                    )
                )
                last_id = match
                continue

            if (
                require_nonzero
                and exec_for_dedup
                and db_path is not None
                and exec_ok
                and (n_rows is not None and n_rows <= 0)
            ):
                result.events.append(
                    IngestEvent(
                        question_k=K,
                        local_k=local_k,
                        source_name=src_name,
                        action="reject_empty",
                        node_id=None,
                        refs=list(resolved_refs),
                        related=related,
                        body_preview=preview,
                    )
                )
                continue

            # Create new brick StepK_k
            nid = make_node_id(K, local_k)
            # Remap sibling / prior aliases → canonical node ids inside body.
            # Do NOT rewrite this unit's own source_name (may collide with columns).
            rename = dict(local_to_node)
            for r in resolved_refs:
                rename[r] = r
            # Prefer resolved ref targets for any raw ref token still in body
            for r in raw_refs:
                rid = self._resolve_ref(r, local_to_node)
                if rid:
                    rename[r] = rid
            if src_name in rename and rename[src_name] != src_name:
                # keep defining name stable inside its own body
                rename.pop(src_name, None)
            body_final = _apply_cte_rename(raw_body, rename)

            node = CteNode(
                id=nid,
                body=body_final,
                body_norm=_norm_sql_body(body_final),
                question_k=K,
                local_k=local_k,
                refs=list(resolved_refs),
                result_sig=sig,
                exec_ok=exec_ok,
                exec_error=exec_err,
                n_rows=n_rows,
                columns=list(cols_probe),
                sample_rows=[list(r) for r in (rows_probe or [])[:3]],
                result_preview=preview_txt,
                source_names=[src_name] if src_name else [],
                related=related,
            )
            node.add_sub_task(K, sub_q)
            self._index_node(node)
            local_to_node[src_name] = nid
            local_to_node[nid] = nid
            result.created_ids.append(nid)
            result.events.append(
                IngestEvent(
                    question_k=K,
                    local_k=local_k,
                    source_name=src_name,
                    action="create",
                    node_id=nid,
                    refs=list(resolved_refs),
                    related=related,
                    body_preview=preview,
                )
            )
            last_id = nid

        result.leaf_id = last_id
        result.local_to_node = {
            k: v
            for k, v in local_to_node.items()
            if k in local_names or _NODE_ID_RE.match(k) or k.startswith("step_")
        }
        self.history.append(result)
        return result

    # --------------------------------------------------------------- compose
    def ancestors(self, node_id: str) -> List[str]:
        """Topo-friendly list of ancestor ids (deps first), excluding self."""
        out: List[str] = []
        seen: Set[str] = set()

        def dfs(u: str) -> None:
            if u in seen or u not in self.nodes:
                return
            seen.add(u)
            for r in self.nodes[u].refs:
                dfs(r)
            out.append(u)

        if node_id in self.nodes:
            for r in self.nodes[node_id].refs:
                dfs(r)
        return out

    def compose_sql(
        self,
        leaf_id: Optional[str] = None,
        *,
        extra_body: Optional[str] = None,
        extra_refs: Optional[Sequence[str]] = None,
        extra_name: str = "__extra__",
        include_ids: Optional[Sequence[str]] = None,
    ) -> str:
        """Build executable ``WITH ... SELECT * FROM leaf``.

        If ``extra_body`` is set (probe / orphan attach), include its deps then
        the extra as last CTE.
        """
        order: List[str] = []
        seen: Set[str] = set()

        def add_with_deps(nid: str) -> None:
            if nid in seen or nid not in self.nodes:
                return
            for r in self.nodes[nid].refs:
                add_with_deps(r)
            if nid not in seen:
                seen.add(nid)
                order.append(nid)

        if include_ids:
            for nid in include_ids:
                add_with_deps(nid)
        if leaf_id:
            add_with_deps(leaf_id)
        if extra_refs:
            for r in extra_refs:
                add_with_deps(r)

        parts: List[str] = []
        for nid in order:
            n = self.nodes[nid]
            parts.append(f"{nid} AS (\n{n.body}\n)")

        if extra_body is not None:
            parts.append(f"{extra_name} AS (\n{extra_body}\n)")
            last = extra_name
        elif leaf_id and leaf_id in self.nodes:
            last = leaf_id
        elif order:
            last = order[-1]
        else:
            return (extra_body or "").strip()

        if not parts:
            return ""
        return f"WITH {', '.join(parts)}\nSELECT * FROM {last}"

    def compose_for_question(self, question_k: int) -> str:
        """Compose using the last created/reused leaf from that question ingest."""
        leaf = None
        for h in reversed(self.history):
            if h.question_k == int(question_k) and h.leaf_id:
                leaf = h.leaf_id
                break
        if not leaf:
            # fallback: max local_k among nodes with this K
            cands = [n for n in self.nodes.values() if n.question_k == int(question_k)]
            if not cands:
                return ""
            cands.sort(key=lambda n: n.local_k)
            leaf = cands[-1].id
        return self.compose_sql(leaf_id=leaf)

    def preceding_cte_blobs(self) -> List[str]:
        """Emit each stored brick as ``WITH StepK_k AS (...)`` (legacy linear list).

        Prefer ``compose_sql`` / ``compose_candidate_sql`` for execution — those
        follow DAG refs instead of dumping every brick into a flat chain.
        """
        out: List[str] = []
        for nid, n in sorted(
            self.nodes.items(),
            key=lambda kv: (kv[1].question_k, kv[1].local_k),
        ):
            out.append(f"WITH {nid} AS (\n{n.body}\n)\nSELECT * FROM {nid}")
        return out

    def _seed_alias_map(self) -> Dict[str, str]:
        """node id / create-name / legacy step_{K-1} → canonical StepK_k."""
        local: Dict[str, str] = {}
        by_q: Dict[int, List[CteNode]] = {}
        for nid, n in self.nodes.items():
            local[nid] = nid
            if n.source_names and n.source_names[0]:
                local.setdefault(n.source_names[0], nid)
            by_q.setdefault(n.question_k, []).append(n)
        for qk, ns in by_q.items():
            leaf = max(ns, key=lambda x: x.local_k)
            local[f"step_{qk - 1}"] = leaf.id
        return local

    def compose_candidate_sql(self, llm_sql: str) -> str:
        """Compose executable SQL for a Yi candidate using **DAG refs**, not a flat chain.

        - Existing bricks that the candidate restates (body match) are reused, not
          re-appended.
        - Only ancestors required by the candidate's refs are pulled in.
        - New units are appended under temporary / model names, bodies remapped to
          ``StepK_k`` ids.
        """
        raw = (llm_sql or "").strip().rstrip(";")
        if not raw:
            return ""
        units = split_llm_units(raw)
        if not units:
            # bare already handled by split; fallback
            if not self.nodes:
                return raw if re.search(r"(?is)^\s*select\b", raw) else ""
            return self.compose_sql(extra_body=raw, extra_name="__cand__")

        local_to_node = self._seed_alias_map()
        local_names = [u[0] for u in units]
        new_defs: List[Tuple[str, str, List[str]]] = []  # name, body, refs
        last_id: Optional[str] = None

        for i, (src_name, raw_body) in enumerate(units, start=1):
            if not (raw_body or "").strip():
                continue
            body = _apply_cte_rename(raw_body, local_to_node)
            # Prefer exact body match → reuse stored brick (no re-append)
            hit = self.find_match(body, sig=None, require_sig=False)
            if hit:
                local_to_node[src_name] = hit
                local_to_node[hit] = hit
                last_id = hit
                continue

            raw_refs = [
                r
                for r in self.extract_refs(body, local_names)
                if r.lower() != (src_name or "").lower()
            ]
            resolved: List[str] = []
            seen_r: Set[str] = set()
            rename = dict(local_to_node)
            for r in raw_refs:
                rid = self._resolve_ref(r, local_to_node)
                if rid and rid not in seen_r:
                    seen_r.add(rid)
                    resolved.append(rid)
                    rename[r] = rid
            body_final = _apply_cte_rename(raw_body, rename)

            temp = src_name if src_name and not str(src_name).startswith("_") else f"__cand_{i}"
            if temp in self.nodes or temp in {d[0] for d in new_defs}:
                temp = f"__cand_{i}"
            new_defs.append((temp, body_final, resolved))
            local_to_node[src_name] = temp
            local_to_node[temp] = temp
            last_id = temp

        if not new_defs and last_id:
            return self.compose_sql(leaf_id=last_id)
        if not new_defs:
            return ""

        # Pull only DAG ancestors required by new units' refs
        need: List[str] = []
        seen_n: Set[str] = set()

        def add_deps(nid: str) -> None:
            if nid in seen_n or nid not in self.nodes:
                return
            for r in self.nodes[nid].refs:
                add_deps(r)
            if nid not in seen_n:
                seen_n.add(nid)
                need.append(nid)

        for _name, _body, refs in new_defs:
            for r in refs:
                add_deps(r)

        parts: List[str] = []
        for nid in need:
            n = self.nodes[nid]
            parts.append(f"{nid} AS (\n{n.body}\n)")
        for name, body, _refs in new_defs:
            parts.append(f"{name} AS (\n{body}\n)")
        leaf = new_defs[-1][0]
        return f"WITH {', '.join(parts)}\nSELECT * FROM {leaf}"

    # --------------------------------------------------------------- serialize
    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": [
                {"from": r, "to": nid}
                for nid, n in self.nodes.items()
                for r in n.refs
            ],
            "history": [h.to_dict() for h in self.history],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CteDAG":
        g = cls()
        for nid, nd in (d.get("nodes") or {}).items():
            node = CteNode(
                id=nd["id"],
                body=nd["body"],
                body_norm=nd.get("body_norm") or _norm_sql_body(nd["body"]),
                question_k=int(nd["question_k"]),
                local_k=int(nd["local_k"]),
                refs=list(nd.get("refs") or []),
                result_sig=nd.get("result_sig"),
                exec_ok=nd.get("exec_ok"),
                exec_error=nd.get("exec_error"),
                n_rows=nd.get("n_rows"),
                columns=list(nd.get("columns") or []),
                sample_rows=[list(r) for r in (nd.get("sample_rows") or [])],
                result_preview=nd.get("result_preview") or "",
                sub_question=nd.get("sub_question") or "",
                sub_tasks=list(nd.get("sub_tasks") or []),
                source_names=list(nd.get("source_names") or []),
                related=bool(nd.get("related")),
            )
            if not node.sub_tasks and node.sub_question:
                node.sub_tasks = [node.sub_question]
            g._index_node(node)
        for h in d.get("history") or []:
            g.history.append(
                IngestResult(
                    question_k=int(h["question_k"]),
                    events=[IngestEvent(**e) for e in (h.get("events") or [])],
                    created_ids=list(h.get("created_ids") or []),
                    reused_ids=list(h.get("reused_ids") or []),
                    leaf_id=h.get("leaf_id"),
                    local_to_node=dict(h.get("local_to_node") or {}),
                )
            )
        return g

    def to_json(self, path: Optional[Path] = None, indent: int = 2) -> str:
        s = json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
        if path is not None:
            Path(path).write_text(s, encoding="utf-8")
        return s

    def fill_exec_previews(
        self,
        db_path: Path,
        *,
        timeout_s: float = 15.0,
        force: bool = False,
    ) -> None:
        """Execute each node (with ancestors) and store abbreviated previews."""
        for nid in sorted(
            self.nodes.keys(),
            key=lambda x: parse_node_id(x) or (0, 0),
        ):
            n = self.nodes[nid]
            if n.result_preview and not force:
                continue
            sql = self.compose_sql(leaf_id=nid)
            rows, err, cols = exec_rows_with_cols(
                Path(db_path), sql, timeout_s=timeout_s
            )
            n.result_preview = format_result_for_prompt(cols, rows, err)
            _, nr = abbrev_exec_result(rows, err)
            n.n_rows = nr
            n.columns = list(cols)
            n.sample_rows = [list(r) for r in (rows or [])[:3]]
            n.exec_ok = err is None and rows is not None
            n.exec_error = err
            if n.exec_ok:
                n.result_sig = result_sig(rows)

    @staticmethod
    def _mermaid_escape(s: str) -> str:
        t = (s or "").replace("\n", " ").replace("\r", " ")
        for ch in ('"', "`", "|", "[", "]", "{", "}", "(", ")", "<", ">", "#"):
            t = t.replace(ch, " ")
        return re.sub(r"\s+", " ", t).strip()

    def to_mermaid(self, *, direction: str = "TD", show_result: bool = True) -> str:
        """Mermaid flowchart: deps → dependents; node label includes exec abbrev."""
        lines = [f"flowchart {direction}"]
        if not self.nodes:
            lines.append("  empty((empty DAG))")
            return "\n".join(lines)
        for nid, n in sorted(
            self.nodes.items(),
            key=lambda kv: (kv[1].question_k, kv[1].local_k),
        ):
            bits = [nid]
            if n.source_names:
                bits.append(f"«{n.source_names[0]}»")
            rel = "rel" if n.related else "orphan"
            bits.append(rel)
            if show_result:
                if n.result_preview:
                    bits.append(self._mermaid_escape(n.result_preview))
                elif n.exec_ok is False and n.exec_error:
                    bits.append(self._mermaid_escape(f"ERR: {n.exec_error}"))
                else:
                    bits.append("exec=?")
            label = self._mermaid_escape(" | ".join(bits))
            # use <br/> via mermaid: put newlines as <br> in HTML labels — use
            # quoted rect labels with \\n
            label_nl = label.replace(" | ", "\\n")
            tag = "related" if n.related else "orphan"
            if n.exec_ok is False:
                tag = "fail"
            shape = f'["{label_nl}"]'
            if not n.related:
                shape = f'("{label_nl}")'
            lines.append(f"  {nid}{shape}")
            lines.append(f"  class {nid} {tag}")
        for nid, n in self.nodes.items():
            for r in n.refs:
                if r in self.nodes:
                    lines.append(f"  {r} --> {nid}")
        lines.append("  classDef related fill:#dbeafe,stroke:#1d4ed8;")
        lines.append("  classDef orphan fill:#f3f4f6,stroke:#6b7280;")
        lines.append("  classDef fail fill:#fee2e2,stroke:#b91c1c;")
        return "\n".join(lines)

    @staticmethod
    def _clip(s: str, n: int) -> str:
        """If n<=0, return full collapsed whitespace string (no truncation)."""
        t = re.sub(r"\s+", " ", (s or "").strip())
        if n is None or int(n) <= 0:
            return t
        return t if len(t) <= n else t[: n - 1] + "…"

    def prompt_block(
        self,
        *,
        next_sub_question: str = "",
        next_question_k: Optional[int] = None,
        upto_question_k: Optional[int] = None,
        max_task_chars: int = 0,
        max_cols_chars: int = 0,
        max_result_chars: int = 0,
        max_body_chars: int = 0,
        include_body: bool = False,
    ) -> str:
        """DAG table for next-step prompts (node / refs / task / cols / result).

        By default all table fields are **untruncated** (max_*=0).
        ``upto_question_k``: only show bricks with ``question_k <= upto``.
        """
        nodes = [
            (nid, n)
            for nid, n in self.nodes.items()
            if upto_question_k is None or n.question_k <= int(upto_question_k)
        ]
        if not nodes:
            return "(empty CTE DAG — no stored bricks yet)"

        def _cols(n: CteNode) -> str:
            if n.columns:
                return self._clip(", ".join(n.columns), max_cols_chars)
            return "?"

        def _res(n: CteNode) -> str:
            if n.exec_ok is False:
                return self._clip(f"ERR: {n.exec_error or '?'}", max_result_chars)
            if n.n_rows is None and not n.sample_rows:
                if n.result_preview:
                    return self._clip(n.result_preview, max_result_chars)
                return "?"
            bits = [f"n={n.n_rows if n.n_rows is not None else '?'}"]
            if n.sample_rows:
                def _cell(v: Any) -> str:
                    return str(v).replace("\n", " ").replace("|", "/")

                samples = []
                for r in n.sample_rows[:3]:
                    samples.append("(" + ", ".join(_cell(x) for x in r) + ")")
                bits.append("sample=" + "; ".join(samples))
                if (n.n_rows or 0) > 3:
                    bits[-1] += f" …(+{(n.n_rows or 0) - 3})"
            return self._clip(" ".join(bits), max_result_chars)

        def _cte_cell(nid: str, n: CteNode) -> str:
            """Single-line CTE for markdown table cell (pipes escaped)."""
            body = re.sub(r"\s+", " ", (n.body or "").strip())
            if max_body_chars and len(body) > max_body_chars:
                body = body[: max_body_chars - 1] + "…"
            cell = f"WITH {nid} AS ( {body} )"
            return cell.replace("|", "\\|")

        def _task(n: CteNode) -> str:
            if n.sub_tasks:
                return self._clip(" ; ".join(n.sub_tasks), max_task_chars)
            return self._clip(n.sub_question or f"(Q{n.question_k})", max_task_chars)

        # refs column already encodes edges (A in B.refs ⇒ A→B); no separate Edges row.
        header = (
            "| node | refs | sub-task (Qi) | columns | exec result | CTE |"
            if include_body
            else "| node | refs | sub-task (Qi) | columns | exec result |"
        )
        sep = (
            "|---|---|---|---|---|---|"
            if include_body
            else "|---|---|---|---|---|"
        )
        lines = [
            "# Stored CTE DAG (bricks you may reuse)",
            "Naming: Step{K}_{k} — K = sub-question index (1-based), "
            "k = unit order inside that LLM output.",
            "refs = upstream bricks this node reads (edge A→B shown as B.refs⊇A).",
            "Prefer `SELECT … FROM StepK_k` over restating a brick's body.",
            "If one brick covers multiple plan steps, sub-task lists all [Qk] texts.",
            "",
            header,
            sep,
        ]
        for nid, n in sorted(nodes, key=lambda kv: (kv[1].question_k, kv[1].local_k)):
            refs = ", ".join(n.refs) if n.refs else "∅"
            task = _task(n).replace("|", "/")
            if include_body:
                lines.append(
                    f"| {nid} | {refs} | {task} | {_cols(n)} | {_res(n)} | `{_cte_cell(nid, n)}` |"
                )
            else:
                lines.append(
                    f"| {nid} | {refs} | {task} | {_cols(n)} | {_res(n)} |"
                )

        nk = next_question_k
        nsq = (next_sub_question or "").strip()
        if nsq or nk is not None:
            lines.append("")
            lines.append("# Current sub-question to solve")
            if nk is not None:
                lines.append(f"- index K = {int(nk)}  → new bricks will be named Step{int(nk)}_*")
            if nsq:
                lines.append(f"- task: {nsq}")
            lines.append(
                "- Output one WITH (or bare SELECT) that advances this sub-question; "
                "reuse upstream Step* nodes via FROM/JOIN when helpful."
            )
        return "\n".join(lines)

    def nodes_for_question(self, question_k: int) -> List[str]:
        """Node ids whose primary question_k matches (1-based plan index)."""
        K = int(question_k)
        return sorted(
            [nid for nid, n in self.nodes.items() if int(n.question_k) == K],
            key=lambda nid: (self.nodes[nid].local_k, nid),
        )

    def plan_dep_hint(
        self,
        *,
        plan_depends_on_0based: Sequence[int],
        plan_steps: Optional[Sequence[str]] = None,
    ) -> str:
        """Prompt fragment: which prior plan bricks this step must read."""
        deps = [int(x) for x in (plan_depends_on_0based or []) if int(x) >= 0]
        if not deps:
            return (
                "# Plan dependency\n"
                "- This step is independent (depends_on=[]). "
                "Prefer base tables; do not require prior Step* refs."
            )
        lines = [
            "# Plan dependency (hard guidance)",
            "- This step MUST reference result bricks from these prior plan steps "
            "(use FROM/JOIN StepK_k):",
        ]
        for d in deps:
            K = d + 1
            nodes = self.nodes_for_question(K)
            task = ""
            if plan_steps and 0 <= d < len(plan_steps):
                task = re.sub(r"\s+", " ", str(plan_steps[d] or "").strip())[:120]
            if nodes:
                lines.append(f"  - plan step {K} → nodes [{', '.join(nodes)}]" + (f"  ({task})" if task else ""))
            else:
                lines.append(
                    f"  - plan step {K} → (no bricks stored yet)"
                    + (f"  ({task})" if task else "")
                )
        lines.append(
            "- If multiple parents are listed, the new brick should combine them "
            "(multi-parent), not ignore any."
        )
        lines.append(
            "- FORBIDDEN: re-declare prior Step* names with a new AS (...) body. "
            "Reuse them only via FROM/JOIN (do not copy their filters from base tables)."
        )
        return "\n".join(lines)

    def detect_prior_rewrite(
        self,
        *,
        question_k: int,
        ingest_result: "IngestResult",
    ) -> Dict[str, Any]:
        """Detect emission units that redefine prior plan bricks under a new body.

        Restating a prior ``Step*`` name is OK when ingest **reuses** (body match).
        Creating a new node while naming it after an earlier plan step means the
        model rewrote that prior step → callers should rollback replay from
        ``from_k``.
        """
        K = int(question_k)
        from_k: Optional[int] = None
        details: List[Dict[str, Any]] = []
        for ev in ingest_result.events or []:
            pk = prior_question_k_from_name(ev.source_name)
            if pk is None or pk >= K:
                continue
            if ev.action != "create":
                continue
            details.append(
                {
                    "source_name": ev.source_name,
                    "prior_k": pk,
                    "new_node": ev.node_id,
                    "action": ev.action,
                    "body_preview": (ev.body_preview or "")[:160],
                }
            )
            from_k = pk if from_k is None else min(from_k, int(pk))
        return {
            "rewritten": from_k is not None,
            "from_k": from_k,
            "at_k": K,
            "details": details,
        }

    def check_plan_deps(
        self,
        *,
        question_k: int,
        plan_depends_on_0based: Sequence[int],
        new_node_ids: Sequence[str],
    ) -> Dict[str, Any]:
        """Whether newly created/touched nodes cover required prior plan steps via refs."""
        deps = [int(x) for x in (plan_depends_on_0based or []) if int(x) >= 0]
        if not deps:
            return {"ok": True, "required": [], "covered": [], "missing": []}
        # Union of direct refs from new nodes (+ their ids if reused priors)
        seen: Set[str] = set()
        for nid in new_node_ids:
            n = self.nodes.get(nid)
            if not n:
                continue
            seen.add(nid)
            for r in n.refs or []:
                seen.add(r)
        covered: List[int] = []
        missing: List[int] = []
        for d in deps:
            K = d + 1
            prior_nodes = set(self.nodes_for_question(K))
            if prior_nodes & seen:
                covered.append(K)
            else:
                # also accept if any new node was itself created under that K (reuse path)
                missing.append(K)
        return {
            "ok": not missing,
            "required": [d + 1 for d in deps],
            "covered": covered,
            "missing": missing,
            "seen_refs": sorted(seen),
        }

    def next_step_prompt(
        self,
        *,
        next_sub_question: str,
        next_question_k: int,
        include_body: bool = True,
        plan_depends_on_0based: Optional[Sequence[int]] = None,
        plan_steps: Optional[Sequence[str]] = None,
    ) -> str:
        """Full draft block to inject when generating the next Yi step."""
        base = self.prompt_block(
            next_sub_question=next_sub_question,
            next_question_k=next_question_k,
            upto_question_k=max(0, int(next_question_k) - 1),
            include_body=include_body,
        )
        if plan_depends_on_0based is None:
            return base
        hint = self.plan_dep_hint(
            plan_depends_on_0based=plan_depends_on_0based,
            plan_steps=plan_steps,
        )
        return base + "\n\n" + hint


def ingest_plan_expansions(
    expansions: Sequence[Dict[str, Any]],
    *,
    db_path: Optional[Path] = None,
    cte_key: str = "yi_cte",
    step_key: str = "step",
    timeout_s: float = 15.0,
    exec_for_dedup: bool = True,
    qs: Optional[Sequence[str]] = None,
) -> Tuple[CteDAG, List[IngestResult]]:
    """Replay progressive expansions into a fresh DAG.

    ``step`` in rows is treated as 1-based question index K.
    ``qs[K-1]`` (if provided) is stored as each node's sub-task text.
    """
    g = CteDAG()
    results: List[IngestResult] = []
    qlist = list(qs or [])
    for e in sorted(
        [x for x in expansions if isinstance(x, dict)],
        key=lambda x: int(x.get(step_key) or 0),
    ):
        cte = (e.get(cte_key) or "").strip()
        if not cte:
            continue
        K = int(e.get(step_key) or 0)
        if K <= 0:
            continue
        sub_q = ""
        if 0 <= K - 1 < len(qlist):
            sub_q = str(qlist[K - 1] or "")
        ir = g.ingest(
            K,
            cte,
            db_path=db_path,
            timeout_s=timeout_s,
            exec_for_dedup=exec_for_dedup and db_path is not None,
            sub_question=sub_q,
        )
        results.append(ir)
    return g, results
