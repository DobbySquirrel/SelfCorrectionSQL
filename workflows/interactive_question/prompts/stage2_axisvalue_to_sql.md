You are an expert SQL generator. Materialize the following semantic slot assignments into a single executable SQLite query.

Database schema:
{schema_str}

Slot assignments (axis -> value):
{axis_values_str}

Rules:
- Use ONLY the slot values given; do not invent additional semantics.
- Do NOT use any natural language question; the slot dictionary fully specifies the query intent.
- SQL must be valid SQLite referencing tables/columns from the schema.

Output ONLY the SQL query, no explanation or markdown.
