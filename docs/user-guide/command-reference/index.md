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
| `--api-url`, `-u` | GitHub URL to target (defaults to https://api.github.com) |

They may be passed either before or after the subcommand:

```bash
gato-x --api-url https://octocorp.ghe.com enumerate --self-enumeration
gato-x enumerate --self-enumeration --api-url https://octocorp.ghe.com
```

Note: the `persistence` command uses `-p` for `--key-path`, so pass `--http-proxy`
in full after that subcommand.

### Pointing Gato-X at an enterprise

Pass the URL you use in the browser. Gato-X works out the API host and the REST
and GraphQL paths from it, because none of the three deployments serves its API
from the URL you browse to:

| Target | Browser URL (`--api-url`) | Derived REST base | Derived GraphQL endpoint |
|--------|---------------------------|-------------------|--------------------------|
| GitHub.com | omit the flag | `https://api.github.com` | `https://api.github.com/graphql` |
| Enterprise Cloud w/ data residency | `https://SUBDOMAIN.ghe.com` | `https://api.SUBDOMAIN.ghe.com` | `https://api.SUBDOMAIN.ghe.com/graphql` |
| Enterprise Server (GHES) | `https://HOSTNAME` | `https://HOSTNAME/api/v3` | `https://HOSTNAME/api/graphql` |

An explicit API base works too: `https://api.SUBDOMAIN.ghe.com` and
`https://HOSTNAME/api/v3` are both accepted and left alone. A bare hostname
(`octocorp.ghe.com`) is accepted as well and assumed to be HTTPS.

Whenever Gato-X rewrites the URL you gave it, it prints what it resolved to, so
you can confirm it guessed right:

```
[+] Resolved https://octocorp.ghe.com to the REST API at https://api.octocorp.ghe.com and the GraphQL API at https://api.octocorp.ghe.com/graphql.
```

Two details this handles that are easy to get wrong by hand: the `/api/v3`
suffix belongs to GHES only (`https://api.SUBDOMAIN.ghe.com/api/v3` mixes the
two forms and 404s on most routes), and GHES serves GraphQL from
`/api/graphql`, not `/api/v3/graphql`.

Certificate verification stays on for GitHub-operated hosts (`api.github.com`
and `*.ghe.com`), which always present a publicly trusted certificate. It is
relaxed only for GHES, where a private CA is common, and when an intercepting
proxy is configured.

### Debugging a rejected request

When the API rejects a request, Gato-X prints the URL it called, the status code
and GitHub's own explanation, rather than guessing at the cause:

```
[!] Failed to query the acme organization: https://api.octocorp.ghe.com/orgs/acme returned 403 - Although you appear to have the correct authorization credentials, the `acme` organization has an IP allow list enabled, and 203.0.113.5 is not permitted to access this resource. | See https://docs.github.com/rest/orgs/orgs#get-an-organization
```

That message distinguishes the cases that used to look identical: an IP allow
list, SAML SSO enforcement, an expired token, a missing organization and a wrong
`--api-url`. If the body comes back as HTML instead of JSON, Gato-X says so —
that means the URL reached a web interface rather than an API endpoint.

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
