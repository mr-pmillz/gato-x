# GitHub App Authentication

Gato-X can authenticate as a GitHub App instead of using a personal access
token. Pass the App ID and its private key and Gato-X does the rest: it signs
the JWT, exchanges it for an installation access token, and renews both for as
long as the run lasts.

```bash
gato-x enumerate -t acme-corp --app-id 12345 --app-key ./app-private-key.pem
```

## Why authenticate as an App

- **The run does not expire.** Installation tokens die after an hour. Gato-X
  renews them, so enumerating a large installation is not a race against the
  clock.
- **Higher rate limits.** An App installation gets a limit that scales with the
  size of the installation, rather than the flat 5,000 requests/hour of a user
  token.
- **Scoped, auditable access.** Permissions come from the App's installation
  rather than from whatever the operator's own account can reach, and the audit
  log attributes activity to the App.
- **No user-bound credential.** Nothing breaks when a person leaves the org.

## The two credentials

App authentication is a two-stage exchange, and each stage has its own clock.
Gato-X manages both; the distinction matters only when reading debug output.

| Credential | How it is obtained | Lifetime | Valid on |
|---|---|---|---|
| JWT (`RS256`) | Signed locally with the App's private key | **10 minutes** (GitHub rejects a longer one) | `/app/*` endpoints only |
| Installation token (`ghs_…`) | `POST /app/installations/{id}/access_tokens`, authenticated with the JWT | **1 hour** | Everything the installation grants |

The JWT cannot read a repository — it exists only to mint installation tokens.
All enumeration and attack traffic runs on the installation token. Gato-X
renews the JWT one minute before it lapses and the installation token five
minutes before, and it also renews once and retries if GitHub rejects a request
with a 401.

## Supplying credentials

Flags work on every subcommand and may be passed before or after it:

| Option | Environment variable | Description |
|---|---|---|
| `--app-id` | `GH_APP_ID` | The GitHub App's ID |
| `--app-key` | `GH_APP_KEY` | Path to the private key PEM file, or the PEM contents |
| `--installation-id` | `GH_APP_INSTALLATION_ID` | Use a specific installation instead of resolving one |

Flags take precedence over the environment, so a single shell can be pointed at
a second App without unsetting anything.

```bash
export GH_APP_ID=12345
export GH_APP_KEY=/path/to/app-private-key.pem

gato-x enumerate -t acme-corp
gato-x search -t acme-corp
gato-x attack --workflow -t acme-corp/widgets
```

When both `GH_TOKEN` and App credentials are set, the App wins and Gato-X says
so.

The private key must be an **unencrypted RSA key** — the file GitHub gives you
when you generate a key in the App's settings. If yours is passphrase
protected, decrypt it first:

```bash
openssl rsa -in app-private-key.pem -out app-key-decrypted.pem
```

## Choosing an installation

An App is installed separately on each account, and each installation has its
own token. By default Gato-X resolves the installation from the target:

```
[+] Authenticated as GitHub App: my-scanner (ID 12345)
[+] Permissions: contents:read, metadata:read, actions:read
[+] Resolved acme-corp to installation 98765
```

A bare name is tried as an organization first, then as a user account. An
`owner/repo` target resolves through the repository.

To see every installation:

```bash
gato-x app --app-id 12345 --app-key ./app-private-key.pem --installations
```

To pin one explicitly — necessary for commands with no target, such as
`--self-enumeration`:

```bash
gato-x enumerate --self-enumeration --installation-id 98765
```

### One installation per run

An installation token is scoped to a single account, so a `--repositories` file
spanning accounts cannot be served by one run. Gato-X detects this and stops
rather than returning empty results for the repositories it cannot see:

```
Installation 98765 covers acme-corp, but this run also targets othercorp.
An installation token is scoped to one account -- run Gato-X once per account,
or use `gatox app --installations` to enumerate them all.
```

Run Gato-X once per account, or use `gato-x app --installation <id>` to
enumerate an installation at a time.

## Required permissions

For enumeration, the App needs at least:

| Permission | Level | Why |
|---|---|---|
| `metadata` | Read | Required by every App |
| `contents` | Read | Read workflow files and repository contents |
| `actions` | Read | List workflow runs and download run logs for runner detection |

`secrets` and `environments` read access improve secrets enumeration. Attack
features additionally need `contents:write` and `workflows:write`.

Gato-X prints the App's permissions after authenticating, and skips checks the
installation cannot perform.

## Enterprise

App authentication works against GitHub Enterprise Server and Enterprise Cloud
with data residency. Combine it with `--api-url`:

```bash
gato-x enumerate -t acme-corp \
  --app-id 12345 --app-key ./app-private-key.pem \
  --api-url https://octocorp.ghe.com
```

## MCP server

The MCP server accepts the same credentials through `GH_APP_ID` and
`GH_APP_KEY`, or per-call `app_id` and `app_key` parameters. Tools without a
target (`self_enumeration`, `validate_pat`) need `GH_APP_INSTALLATION_ID`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `The private key is encrypted` | Decrypt it with `openssl rsa` as shown above |
| `GitHub Apps require an RSA private key` | The key is an EC or Ed25519 key; generate a new key in the App's settings |
| `The private key could not be parsed as a PEM private key` | The file is truncated or is not the private key; download a fresh one |
| `Private key file not found` | Check the `--app-key` path |
| `Failed to authenticate as a GitHub App` | The App ID and key belong to different Apps |
| `The GitHub App is not installed on X` | Install the App on that account, or check the spelling |
| `Failed to mint an installation access token` | The installation was removed, or the App's key was revoked |
| Repeated 401s | The installation was suspended; Gato-X renews once and then reports GitHub's own message |

Run with `--log-level DEBUG` to see renewals as they happen. Tokens are never
written to the logs.
