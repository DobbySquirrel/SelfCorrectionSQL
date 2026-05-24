You are analyzing a natural language query against a database schema to identify potential semantic ambiguities.

Natural language question:
{nl_question}

Database schema (table.column list):
{schema_str}

Strict slot discovery policy:
1. Precision-first: omit any slot without clear NL evidence of underspecification.
2. Coverage: include slots for Table, Join Path, Projection, Formula, Boundary, Ranking when the NL question plausibly leaves them open.
3. Limit: 4-7 slots; each slot uses exactly one allowed axis label below.
4. Values: 2-4 candidate_values per slot; minimal SQL fragments with schema table.column names.

Allowed axis labels:
- Reference Grounding: Table
- Reference Grounding: Column
- Reference Grounding: Join Path
- Value Grounding: Value Encoding
- Value Grounding: Format Normalization
- Measure Construction: Formula
- Measure Construction: Numeric
- Measure Construction: Boundary
- Ranking Target: Extremum
- Ranking Target: Method
- Ranking Target: Direction
- Output Control: Projection
- Output Control: Row Structure

Disambiguation hints:
- Encoding vs Format: literal interpretation vs date/string normalization.
- Boundary vs Encoding: numeric thresholds vs categorical encodings.
- Extremum vs Method vs Direction: top-k/limit vs aggregate ranking function vs ASC/DESC.

Output ONLY valid JSON array (no markdown):
[{"axis":"...","description":"...","candidate_values":["..."]}]
