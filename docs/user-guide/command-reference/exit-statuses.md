# Exit Statuses

The `gato-x` and `gatox` commands, `python -m gatox.main`, and the Docker image
share these process statuses:

| Status | Meaning |
| --- | --- |
| `0` | Completed successfully with results, or completed a non-search operation such as validation or help. |
| `1` | Operation failed or was incomplete, including API/authentication errors, failed analysis, and output-write errors. |
| `2` | Invalid arguments or configuration. |
| `3` | Search/enumeration/report conversion completed with no matching resources. |
| `130` | Interrupted with Ctrl-C. |

Results mean matching repository names for `search`, repositories or organizations
for `enumerate`, and installations or repositories for `app`. An enumerated
repository without vulnerability findings is still a result and returns `0`.
For `report`, results mean repository records selected for export; findings are
not required. See [Report Conversion](report.md).
Successful credential validation returns `0` even without organization membership.
An inaccessible requested repository is a failure, not an empty successful scan.
Offline mode intentionally skips API-dependent checks; those skips alone do not
fail the command. An invalid local workflow or failed analysis does.

Status `1` takes precedence over `0` or `3` if a command reports an error, including
legacy handlers that log a failure without returning a result. Partial data may
still be available; do not interpret it as a complete assessment. GitHub search
rate-limit retries are bounded, and incomplete search results return failure.

An empty search still writes an empty file when `--output-text` is requested,
replacing stale results from a previous invocation. Requested output-write
failures return `1`.

```bash
status=0
gato-x search -t my-org --output-text repos.txt || status=$?
case "$status" in
  0) echo "Matching repositories saved to repos.txt" ;;
  3) echo "Search completed without matches" ;;
  *) echo "Search failed with status $status" >&2; exit "$status" ;;
esac
```

Scripts using `set -e` must explicitly handle status `3` if an empty result is
acceptable. MCP tools retain their existing structured return values; these
statuses apply to the CLI process.
