You are analyzing a natural language query against a database schema to identify potential semantic ambiguities.

Natural language question:
{nl_question}

Database schema (table.column list):
{schema_str}

Calibration rules (strict):
- Emit a slot ONLY with strong textual evidence in the NL question or schema mismatch.
- When uncertain, omit the slot (precision over recall).
- Target 3-6 slots total; never exceed 8 slots.
- One subcategory per slot; never merge Table/Column/Join into one slot.

Allowed axis labels (use exactly one per slot):
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

Additional constraints:
- Every candidate_value MUST reference at least one schema table or column name.
- Do not emit Row Structure slots unless GROUP BY / DISTINCT granularity is genuinely ambiguous.
- Do not emit Column slots for every column in schema; only columns relevant to the NL question.

Output JSON array only:
[
  {"axis": "...", "description": "...", "candidate_values": ["...", "..."]}
]

Each candidate_values: 2-4 SQL fragments (not full queries).
