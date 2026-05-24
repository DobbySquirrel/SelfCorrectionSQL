<!-- Locked prompt: best dev version = v0 (Path B) -->

You are analyzing a natural language query against a database schema to identify potential semantic ambiguities.

Natural language question:
{nl_question}

Database schema (table.column list):
{schema_str}

Identify all underspecified semantic slots in this query along the following 5-axis taxonomy:

1. Reference Grounding: Table / Column / Join Path ambiguity
2. Value Grounding: Value Encoding / Format Normalization ambiguity
3. Measure Construction: Formula / Numeric / Boundary ambiguity
4. Ranking Target: Extremum / Method / Direction ambiguity
5. Output Control: Projection / Row Structure ambiguity

For each slot you identify, output a JSON object with:
{
  "axis": "<one of the 5 categories + subcategory>",
  "description": "<short NL description of what is ambiguous>",
  "candidate_values": [
    "<value 1, e.g., a SQL fragment>",
    "<value 2, ...>",
    ...
  ]
}

Output a JSON list of all identified slots. If a slot has no real ambiguity, do not include it. Each candidate_values list should contain 2-5 plausible options. Do NOT generate full SQL; only the relevant fragment for each slot.

Output format:
[
  {"axis": "...", "description": "...", "candidate_values": ["...", ...]},
  ...
]
