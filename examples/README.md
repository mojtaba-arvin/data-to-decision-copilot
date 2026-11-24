# End-to-end synthetic sales example

This example creates a disposable local DuckDB catalog with four synthetic
sales rows. It contains no real customer or production data.

From the repository root:

```bash
uv sync --locked --all-groups
uv run python examples/create_demo_catalog.py --output ./analytics.duckdb
d2d schema --catalog ./analytics.duckdb
d2d query --catalog ./analytics.duckdb --question "count rows in sales"
d2d query --catalog ./analytics.duckdb --question "sum amount by region in sales"
```

Expected count: 4 rows. Expected grouped totals:

- north: 335.75
- south: 80.00
- west: 44.75

The CLI query path uses the same schema-grounded planner, SQL guard, bounded
read-only executor, and execution-derived Evidence journal as the REST and MCP
interfaces. The database generator itself is a setup utility and writes only
the explicitly selected demo catalog.
