# Configuration File

Every Gato-X command line option can be set in an optional YAML file, so you
do not have to retype proxies, an API URL, or App credentials on every run.

Gato-X reads `~/.config/gato-x/config.yaml` when it exists. Without one,
behaviour is exactly as it was before.

```yaml
# ~/.config/gato-x/config.yaml
defaults:
  api_url: https://octocorp.ghe.com
  socks_proxy: 127.0.0.1:9050
  log_level: INFO
  app_id: "12345"
  app_key: ~/.config/gato-x/app.pem

enumerate:
  skip_runners: true
  output_json: findings.json

attack:
  author_name: ci-bot
  author_email: ci-bot@example.com
  timeout: 60
```

## Sections

| Section | Applies to |
|---|---|
| `defaults` | Every subcommand |
| `enumerate` | `gato-x enumerate` |
| `attack` | `gato-x attack` |
| `search` | `gato-x search` |
| `app` | `gato-x app` |
| `persistence` | `gato-x persistence` |

A subcommand section overrides `defaults` for that run. Sections for other
subcommands are ignored, which is what lets `target` mean something different
under `enumerate` than under `attack`.

Keys are the flag's long name with dashes turned into underscores, so
`--skip-runners` becomes `skip_runners`. Dashes are accepted too
(`skip-runners`), and both forms mean the same thing.

Gato-X warns about a section or key it does not recognise rather than
ignoring it silently, so a typo surfaces immediately:

```
[!] Ignoring unknown section enumrate in /home/you/.config/gato-x/config.yaml
    (expected 'defaults' or a subcommand name)
```

## Precedence

Highest wins:

1. **Command line flags** — `--api-url https://x`
2. **Environment variables** — `GH_TOKEN`, `GH_APP_ID`, `GH_APP_KEY`, `GH_APP_INSTALLATION_ID`
3. **The config file** — the subcommand section, then `defaults`
4. **Gato-X's built-in defaults**

So a config file can set a convenient default that you override for one run
with a flag, and an environment variable still beats a stale value in the file.

## Choosing a different file

| Option | Purpose |
|---|---|
| `--config PATH` | Read this file instead of the default location |
| `--no-config` | Ignore config files entirely; use only flags and env vars |
| `GATOX_CONFIG` | Environment variable equivalent of `--config` |

Passing `--config` with a path that does not exist is an error. The default
location simply being absent is not.

## Values

Types are ordinary YAML. Booleans work as written — `skip_runners: false`
really does mean false:

```yaml
enumerate:
  skip_runners: false    # boolean
  output_json: out.json  # string
attack:
  timeout: 60            # integer
```

Paths understand `~`:

```yaml
defaults:
  app_key: ~/.config/gato-x/app.pem
```

## Credentials

`gh_token`, `app_id` and `app_key` may be set in the config file:

```yaml
defaults:
  gh_token: ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  app_id: "12345"
  app_key: ~/.config/gato-x/app.pem
```

This puts a credential on disk in plaintext. Gato-X does not enforce anything
about the file's permissions, so if you do this, restrict it yourself and keep
it out of any dotfiles repository you sync:

```bash
chmod 600 ~/.config/gato-x/config.yaml
```

Pointing `app_key` at the PEM path rather than putting a token in the file
keeps the secret in one place that already needs protecting.

## With Docker

Mount your config directory at the location Gato-X reads inside the container:

```bash
docker run --rm -e GH_TOKEN \
  -v "$HOME/.config/gato-x:/home/gatox/.config/gato-x:ro" \
  ghcr.io/mr-pmillz/gato-x enumerate -t acme-corp
```

See [Docker Usage](docker.md).
