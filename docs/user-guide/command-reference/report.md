# Offline Report Conversion

Convert saved enumeration results to SARIF 2.1.0 for code scanning, or JSONL for
processing repository records. Conversion does not authenticate or contact GitHub.

```bash
gato-x enumerate -r acme/example --output-json scan.json
gato-x report --input scan.json --format sarif --repository acme/example --output results.sarif
gato-x report --input scan.json --format jsonl --output repositories.jsonl
```

`--input` accepts native enumeration JSON, repository JSONL produced by this
command, or JSON saved from an enumeration MCP tool. `--output` must differ from
the input. Parent directories must already exist. `--repository owner/repo`
optionally selects a repository; the same option works for both output formats.

JSONL emits one complete repository object per line, including risks, runner
information and secret metadata. Exact duplicate records are collapsed. Different
records for the same repository are retained. Organization-only metadata and
installation records are not part of this repository format; retain the native
JSON when those records are needed. Treat exported scan results as sensitive.

SARIF contains workflow vulnerability findings, with rule help, repository
context, confidence, and stable fingerprints. It does not export secret metadata
or runner inventory. Findings retain warning level; confidence is separate from
severity. Locations point to the initial workflow, without inventing a sink line
for paths crossing workflows/repositories. Unsafe paths are omitted, while the
finding itself is retained. Each repository has a separate SARIF run.

Before uploading to GitHub Code Scanning, use `--repository` to select the same
repository and analyze the matching checkout/ref. A multi-repository export is
for offline aggregation, not uploading all findings into one repository.
See [GitHub SARIF support](https://docs.github.com/en/code-security/reference/code-scanning/sarif-files/sarif-support).

Exit status is `0` when repository records were exported, `3` when no repository
records matched, `1` for malformed reports or read/write failures, and `2` for
invalid CLI arguments. A repository with no vulnerabilities still returns `0`;
an empty valid SARIF document is written for a result with no repositories.
Failed enumeration objects are rejected rather than exported as clean scans.

The MCP tool `export_enumeration_report(data, format, repository=None)` provides
the same conversion without filesystem access. See [MCP Server](../mcp-server/mcp.md).
