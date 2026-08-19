# Overview

Welcome to the Gato-X Command Reference. This section provides detailed usage information for each command.

For installation instructions, see the [Installation Guide](../installation.md).
For use cases, see the [Use Cases](../use-cases/index.md).
For advanced topics, see the [Advanced Topics](../advanced/index.md).

# Command Reference

Gato-X provides three main commands, each with its own set of options:

1. [Search Command](search.md) - Find repositories with potential vulnerabilities
2. [Enumerate Command](enumerate.md) - Analyze repositories for exploitable issues
3. [Attack Command](attack.md) - Execute attacks against vulnerable repositories
4. [Persistence Command](persistence.md) - Deploy persistence techniques in repositories

## Common Options

These options are available across all commands:

| Option | Description |
|--------|-------------|
| `--log-level` | Set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `--socks-proxy`, `-sp` | SOCKS proxy to use for requests in HOST:PORT format |
| `--http-proxy`, `-p` | HTTPS proxy to use for requests in HOST:PORT format |
| `--no-color`, `-nc` | Removes all color from output |
| `--api-url`, `-u` | GitHub API URL to target (defaults to https://api.github.com) |

They may be passed either before or after the subcommand:

```bash
gato-x --api-url https://ghe.example.com/api/v3 enumerate --self-enumeration
gato-x enumerate --self-enumeration --api-url https://ghe.example.com/api/v3
```

Note: the `persistence` command uses `-p` for `--key-path`, so pass `--http-proxy`
in full after that subcommand.

`--api-url` takes the REST base URL, and the GraphQL endpoint is derived from it:

| Target | `--api-url` | Derived GraphQL endpoint |
|--------|-------------|--------------------------|
| GitHub.com | omit the flag | `https://api.github.com/graphql` |
| Enterprise Cloud w/ data residency | `https://api.SUBDOMAIN.ghe.com` | `https://api.SUBDOMAIN.ghe.com/graphql` |
| Enterprise Server (GHES) | `https://HOSTNAME/api/v3` | `https://HOSTNAME/api/graphql` |

The `/api/v3` suffix belongs to GHES only. `https://api.SUBDOMAIN.ghe.com/api/v3`
mixes the two forms and 404s on most routes, so Gato-X drops the suffix on
`api.*` hosts and prints a notice.

## Basic Usage

The general syntax for Gato-X commands is:

```bash
gato-x [command] [options]
```

Where `[command]` is one of:
- `search` or `s` - Search for repositories
- `enumerate`, `enum`, or `e` - Enumerate repositories for vulnerabilities
- `attack` or `a` - Execute attacks against vulnerable repositories
- `persistence`, `persist`, or `p` - Deploy persistence techniques in repositories

## Getting Help

To see the available options for any command, use the `-h` or `--help` flag:

```bash
gato-x --help
gato-x search --help
gato-x enumerate --help
gato-x attack --help
gato-x persistence --help
```

For detailed information about each command, refer to the specific command pages linked above.
