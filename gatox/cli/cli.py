import argparse
import logging
import os
import re
from pathlib import Path

from colorama import Fore, Style

from gatox.attack.attack import Attacker
from gatox.attack.cicd.release_booby_trap import ReleaseBoobyTrapAttack
from gatox.attack.oidc.oidc_attack import OIDCAttack
from gatox.attack.persistence.persistence_attack import PersistenceAttack
from gatox.attack.runner.webshell import WebShell
from gatox.attack.secrets.secrets_attack import SecretsAttack
from gatox.caching.cache_manager import CacheManager
from gatox.caching.local_cache_manager import LocalCacheFactory
from gatox.cli.app.config import configure_parser_app
from gatox.cli.attack.config import configure_parser_attack
from gatox.cli.colors import RED_DASH
from gatox.cli.config_file import (
    SUBCOMMAND_ALIASES,
    ConfigError,
    apply_env_overrides,
    detect_subcommand,
    load_config_file,
    merge_config,
    resolve_config_path,
    split_by_parser,
)
from gatox.cli.enumeration.config import configure_parser_enumerate
from gatox.cli.output import SPLASH, Output
from gatox.cli.persistence.config import configure_parser_persistence
from gatox.cli.search.config import configure_parser_search
from gatox.enumerate.app_enumerate import AppEnumerator
from gatox.enumerate.enumerate import Enumerator
from gatox.enumerate.finegrained_enumeration import FineGrainedEnumerator
from gatox.github.app_session import AppSession
from gatox.github.credentials import AppAuthError
from gatox.models.execution import Execution
from gatox.search.search import Searcher
from gatox.util.arg_utils import read_file_and_validate_lines


async def cli(args):
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            f"{Fore.YELLOW}This tool requires a GitHub access token to"
            f" function!{Style.RESET_ALL}\n\nThis can be passed via the"
            ' "GH_TOKEN" environment variable, or if it is not set,\nthen the'
            " application will prompt you for one. App enumeration requires a GitHub App ID and PEM file.\n\n"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    configure_parser_general(parser)

    attack_parser = subparsers.add_parser(
        "attack",
        help="CI/CD Attack Capabilities",
        aliases=["a"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    attack_parser.set_defaults(func=attack)

    enumerate_parser = subparsers.add_parser(
        "enumerate",
        help="Enumeration Capabilities",
        aliases=["enum", "e"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    enumerate_parser.set_defaults(func=enumerate)

    search_parser = subparsers.add_parser(
        "search",
        help="Search Capabilities Using GitHub's API",
        aliases=["s"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    search_parser.set_defaults(func=search)

    app_parser = subparsers.add_parser(
        "app",
        help=f"{Output.red('[Experimental]')} GitHub App Enumeration Capabilities",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    app_parser.set_defaults(func=app)

    persistence_parser = subparsers.add_parser(
        "persistence",
        help="Deploy persistence techniques in GitHub repositories",
        aliases=["persist", "p"],
        formatter_class=argparse.RawTextHelpFormatter,
    )
    persistence_parser.set_defaults(func=persistence)

    configure_parser_attack(attack_parser)
    configure_parser_enumerate(enumerate_parser)
    configure_parser_search(search_parser)
    configure_parser_app(app_parser)
    configure_parser_persistence(persistence_parser)

    # Registered after the subcommand's own flags so a subcommand that already
    # claims a short flag (persistence -p) keeps it.
    for subparser in (
        attack_parser,
        enumerate_parser,
        search_parser,
        app_parser,
        persistence_parser,
    ):
        configure_parser_general(subparser, subcommand=True)

    config_values = apply_config_defaults(
        args,
        parser,
        {
            "attack": attack_parser,
            "enumerate": enumerate_parser,
            "search": search_parser,
            "app": app_parser,
            "persistence": persistence_parser,
        },
    )

    arguments = parser.parse_args(args)
    arguments.config_values = config_values

    Output(color=not arguments.no_color)
    validate_arguments(arguments, parser)
    print(Output.blue(SPLASH))

    try:
        await arguments.func(arguments, subparsers)
    except AppAuthError as exc:
        # Bad App ID, unusable private key, App not installed on the target.
        # The message already says what to do; a traceback would not.
        Output.error(str(exc))
        raise SystemExit(1) from None


def save_workflow_ymls(output_directory):
    for repo in CacheManager().get_repos():
        Path(os.path.join(output_directory, f"{repo}")).mkdir(
            parents=True, exist_ok=True
        )
        for workflow in CacheManager().get_workflows(repo):
            with open(
                os.path.join(output_directory, f"{repo}/{workflow.workflow_name}"), "w"
            ) as wf_out:
                wf_out.write(workflow.workflow_contents)


def apply_config_defaults(argv, parser, subparsers_by_name):
    """Fold config-file values into the parsers' defaults before parsing.

    Doing it this way means an explicit command line flag beats the config
    file for free, with no special casing at the point of use.

    Args:
        argv: Raw argument list, scanned for ``--config`` and the subcommand.
        parser: The top level parser.
        subparsers_by_name: Canonical subcommand name to its parser.

    Returns:
        The flattened config values that were applied, so later stages can
        read settings that have no command line flag (``gh_token``).
    """
    explicit_path, disabled = _preparse_config_flags(argv)

    if disabled:
        return {}

    path = resolve_config_path(explicit_path)
    try:
        raw = load_config_file(path)
    except ConfigError as exc:
        parser.error(f"{Fore.RED}[!]{Style.RESET_ALL} {exc}")
        return {}

    if not raw:
        # An explicitly requested file that is not there is a mistake worth
        # reporting; the default location simply being absent is normal.
        if explicit_path and not path.exists():
            parser.error(
                f"{Fore.RED}[!]{Style.RESET_ALL} Config file not found: {path}"
            )
        return {}

    subcommand = detect_subcommand(argv)
    try:
        values, unknown_sections = merge_config(raw, subcommand)
    except ConfigError as exc:
        parser.error(f"{Fore.RED}[!]{Style.RESET_ALL} {exc}")
        return {}

    for section in unknown_sections:
        Output.warn(
            f"Ignoring unknown section {Output.bright(section)} in {path} "
            f"(expected 'defaults' or a subcommand name)."
        )

    values = apply_env_overrides(values)

    general_dests = {action.dest for action in parser._actions}
    subparser = subparsers_by_name.get(subcommand) if subcommand else None
    subcommand_dests = (
        {action.dest for action in subparser._actions} if subparser else set()
    )

    general, specific, unknown_keys = split_by_parser(
        values, general_dests, subcommand_dests
    )

    for key in sorted(unknown_keys):
        # gh_token has no flag but is still honoured, so it is not unknown.
        if key == "gh_token":
            continue
        Output.warn(
            f"Ignoring unknown setting {Output.bright(key)} in {path}"
            + (f" (not an option of '{subcommand}')" if subcommand else "")
        )

    if general:
        parser.set_defaults(**general)
    if specific and subparser:
        subparser.set_defaults(**specific)

    if general or specific:
        Output.info(f"Loaded configuration from {Output.bright(str(path))}")

    return values


def _preparse_config_flags(argv):
    """Pull ``--config`` / ``--no-config`` out of argv before the real parse."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config")
    pre.add_argument("--no-config", action="store_true")
    try:
        known, _ = pre.parse_known_args(argv)
    except SystemExit:
        # Malformed --config is reported properly by the real parser.
        return None, False
    return known.config, known.no_config


def app_credentials_from_args(args):
    """Return ``(app_id, private_key)`` for App auth, or ``None``.

    Command line flags win over the environment so an operator can point a
    single shell at a second App without unsetting anything. A partially
    supplied pair is returned with the missing half empty so the caller can
    report precisely which flag is absent.
    """
    app_id = getattr(args, "app_id", None) or os.environ.get("GH_APP_ID")
    app_key = getattr(args, "app_key", None) or os.environ.get("GH_APP_KEY")

    if not app_id and not app_key:
        return None
    return (app_id or "", app_key or "")


def installation_id_from_args(args):
    """Return an explicitly requested installation id, or ``None``."""
    return getattr(args, "installation_id", None) or os.environ.get(
        "GH_APP_INSTALLATION_ID"
    )


def _enum_target(args):
    """Best target to resolve an App installation from, or ``None``.

    For a ``--repositories`` file the first entry stands in for the whole
    list; entries belonging to another account are rejected later by
    :meth:`AppSession.check_installation_covers`.
    """
    for attribute in ("target", "repository", "organization"):
        value = getattr(args, attribute, None)
        if value:
            return value

    repositories = getattr(args, "repositories", None)
    if repositories:
        try:
            with open(repositories) as handle:
                for line in handle:
                    if line.strip():
                        return line.strip()
        except OSError:
            # The handler validates and reports this file properly; here a
            # failure simply means we have no target to resolve from.
            return None
    return None


async def build_app_api(args, target=None):
    """Build an installation-authenticated ``Api`` from App credentials.

    Args:
        args: Parsed arguments carrying ``app_credentials``.
        target: Org, ``owner/repo`` or user to resolve an installation from.
            Ignored when an installation id was supplied explicitly.

    Returns:
        ``(session, api)``. The caller must ``await session.close()``.

    Raises:
        AppAuthError: If no installation can be determined.
    """
    app_id, app_key = args.app_credentials
    session = AppSession(
        app_id,
        app_key,
        socks_proxy=args.socks_proxy,
        http_proxy=args.http_proxy,
        github_url=args.api_url,
    )
    await session.validate()

    installation_id = getattr(args, "resolved_installation_id", None)
    if installation_id:
        api = await session.api_for_installation(installation_id)
    else:
        if not target:
            raise AppAuthError(
                "App authentication needs a target to resolve an installation "
                "from. Pass --installation-id, or give the command a target."
            )
        api = await session.api_for_target(target)

    return session, api


def validate_arguments(args, parser: argparse.ArgumentParser):
    logging.basicConfig(level=args.log_level)

    app_credentials = app_credentials_from_args(args)

    if app_credentials is not None:
        app_id, app_key = app_credentials
        if not app_id:
            parser.error(
                f"{Fore.RED}[!]{Style.RESET_ALL} --app-key was supplied without"
                " --app-id (or GH_APP_ID)."
            )
        if not app_key:
            parser.error(
                f"{Fore.RED}[!]{Style.RESET_ALL} --app-id was supplied without"
                " --app-key (or GH_APP_KEY)."
            )
        # App authentication replaces the PAT entirely: Gato-X mints and
        # renews its own credentials for the whole run.
        gh_token = None
        if os.environ.get("GH_TOKEN"):
            Output.info(
                "Both GH_TOKEN and GitHub App credentials are set; using the App."
            )
    # The app command authenticates with an App ID and private key, never a
    # PAT, so it must not fall through to the prompt below.
    elif SUBCOMMAND_ALIASES.get(getattr(args, "command", "") or "") == "app":
        gh_token = None
        if args.app is None or args.pem is None:
            parser.error(
                f"{Fore.RED}[!]{Style.RESET_ALL} The app command needs an App ID"
                " and a private key: pass --app-id/--app-key (or -a/--pem), or"
                " set GH_APP_ID and GH_APP_KEY."
            )
    elif getattr(args, "local", None):
        # Local-only enumeration: no GitHub token is required because the
        # local enumerator never reaches out to the API. Allow it to be
        # absent without prompting.
        gh_token = os.environ.get("GH_TOKEN")
    else:
        # Regular commands need a PAT, from the environment or the config file.
        config_token = getattr(args, "config_values", {}).get("gh_token")
        gh_token: str | None = os.environ.get("GH_TOKEN") or (
            str(config_token) if config_token else None
        )
        if not gh_token:
            try:
                gh_token = input(
                    "No 'GH_TOKEN' environment variable set! "
                    "Please enter a GitHub PAT.\n"
                )
            except EOFError:
                # No interactive terminal -- a container, a cron job, CI. A
                # traceback here is useless; say what is actually missing.
                parser.error(
                    f"{Fore.RED}[!]{Style.RESET_ALL} No GitHub credentials found"
                    " and no terminal to prompt on. Set GH_TOKEN, or pass"
                    " --app-id/--app-key for GitHub App authentication."
                )

        if re.match(r"github_pat_[A-Za-z0-9_]{22}_[A-Za-z0-9_]{59}$", gh_token):
            logging.info("Using fine-grained token.")
        elif not (
            re.match("gh[po]_[A-Za-z0-9]{36}$", gh_token)
            or re.match("^[a-fA-F0-9]{40}$", gh_token)
        ):
            if re.match("gh[s]_[A-Za-z0-9]{36}$", gh_token):
                if not (args.machine and args.repository):
                    parser.error(
                        f"{Fore.RED}[!]{Style.RESET_ALL} Gato-X does"
                        " not support App tokens without machine flag."
                    )
                else:
                    Output.info(
                        "Allowing the use of a GitHub App token for single repo enumeration."
                    )
            else:
                parser.error(
                    f"{Fore.RED}[!]{Style.RESET_ALL} Provided GitHub PAT is malformed or unsupported!"
                )

    args_dict = vars(args)
    args_dict["gh_token"] = gh_token
    args_dict["app_credentials"] = app_credentials
    args_dict["resolved_installation_id"] = installation_id_from_args(args)

    if args.socks_proxy and args.http_proxy:
        parser.error(
            f"{Fore.RED}[-]{Style.RESET_ALL} You cannot use a SOCKS and HTTP"
            " proxy at the same time!"
        )


async def attack(args, parser):
    """Handler for the attack command.

    Owns the GitHub App session lifecycle so the real body can stay a
    straight-line sequence of attack modes.
    """
    app_session = None
    api_client = None
    if getattr(args, "app_credentials", None):
        app_session, api_client = await build_app_api(args, target=args.target)

    try:
        await _attack(args, parser, api_client)
    finally:
        if app_session:
            await app_session.close()


async def _attack(args, parser, api_client=None):
    parser = parser.choices["attack"]

    if not args.target and not (args.interact or args.payload_only):
        parser.error(
            f"{Fore.RED}[!] You must select a target unless you are interacting with an implant or generating payloads!"
        )

    if not (
        args.workflow
        or args.runner_on_runner
        or args.secrets
        or args.oidc
        or args.release_booby_trap
        or args.interact
        or args.payload_only
    ):
        parser.error(
            f"{Fore.RED}[!] You must select one of the attack modes, "
            "workflow, runner_on_runner, secrets, oidc, release_booby_trap, or interact."
        )

    if args.custom_file and (args.command or args.name):
        parser.error(
            f"{Fore.RED}[!] A shell command or workflow name"
            f" cannot be used with a custom workflow."
        )

    if args.secrets and args.command:
        parser.error(f"{Fore.RED}[!] A command cannot be used with secrets exfil!.")

    if args.oidc and args.command:
        parser.error(f"{Fore.RED}[!] A command cannot be used with OIDC exfil!.")

    if args.oidc and not hasattr(args, "file_name"):
        parser.error(f"{Fore.RED}[!] --file-name is required for OIDC attacks.")

    if args.runner_on_runner and args.command:
        parser.error(
            f"{Fore.RED}[!] A command cannot be used with runner-on-runner attacks!."
        )

    if not args.custom_file:
        args.command = args.command if args.command else "whoami"
        args.name = args.name if args.name else "test"
        if not hasattr(args, "file_name"):
            args.file_name = "test"

    if args.runner_on_runner and not (args.target_os and args.target_arch):
        parser.error(
            f"{Fore.RED}[!] You must specify a target OS and architecture for runner-on-runner attacks!"
        )

    if args.payload_only and not (args.target_os and args.target_arch):
        parser.error(
            f"{Fore.RED}[!] You must specify a target OS and architecture for runner-on-runner payload generation!"
        )

    if args.runner_on_runner:
        if (
            args.target_os == "windows" or args.target_os == "osx"
        ) and args.target_arch == "arm":
            parser.error(
                f"{Fore.RED}[!] Windows and OSX do not support arm32 architecture!"
            )

    timeout = int(args.timeout)

    if args.runner_on_runner or args.payload_only or args.interact:
        if not args.branch:
            args.branch = "main"

        gh_attack_runner = WebShell(
            args.gh_token,
            author_email=args.author_email,
            author_name=args.author_name,
            socks_proxy=args.socks_proxy,
            http_proxy=args.http_proxy,
            timeout=timeout,
            github_url=args.api_url,
            api_client=api_client,
        )

        if args.payload_only:
            await gh_attack_runner.payload_only(
                args.target_os,
                args.target_arch,
                args.labels,
                c2_repo=args.c2_repo,
                keep_alive=args.keep_alive,
            )
        elif args.runner_on_runner:
            await gh_attack_runner.runner_on_runner(
                args.target,
                args.branch,
                args.pr_title,
                args.source_branch,
                args.message,
                args.target_os,
                args.target_arch,
                args.labels,
                keep_alive=args.keep_alive,
                yaml_name=args.file_name,
                run_name=args.name,
                workflow_name=args.name,
                c2_repo=args.c2_repo,
            )
        elif args.interact:
            if args.c2_repo:
                await gh_attack_runner.interact_webshell(args.c2_repo)
            else:
                parser.error(
                    f"{Fore.RED}[!] You must specify a C2 repo to interact with!"
                )

    elif args.workflow:
        gh_attack_runner = Attacker(
            args.gh_token,
            author_email=args.author_email,
            author_name=args.author_name,
            socks_proxy=args.socks_proxy,
            http_proxy=args.http_proxy,
            timeout=timeout,
            github_url=args.api_url,
            api_client=api_client,
        )
        await gh_attack_runner.push_workflow_attack(
            args.target,
            args.command,
            args.custom_file,
            args.branch,
            args.message,
            args.delete_run,
            args.file_name,
        )
    elif args.secrets:
        scopes = None
        if args.gh_token and args.gh_token.startswith("github_pat_"):
            gh_enumeration_runner = FineGrainedEnumerator(
                pat=args.gh_token,
                socks_proxy=args.socks_proxy,
                http_proxy=args.http_proxy,
                github_url=args.api_url,
            )
            scopes = await gh_enumeration_runner.detect_scopes(args.target)
            if "contents:write" not in scopes and "workflows:write" not in scopes:
                parser.error(
                    f"{Fore.RED}[-]{Style.RESET_ALL} The provided fine-grained token does not have the necessary 'contents:write' and 'workflows:write' permission on the targeted repository!"
                )
                return

        gh_attack_runner = SecretsAttack(
            args.gh_token,
            author_email=args.author_email,
            author_name=args.author_name,
            socks_proxy=args.socks_proxy,
            http_proxy=args.http_proxy,
            timeout=timeout,
            github_url=args.api_url,
            api_client=api_client,
        )

        await gh_attack_runner.secrets_dump(
            args.target,
            args.branch,
            args.message,
            args.delete_run,
            args.file_name,
            scopes,
            environments=args.environments,
            runner=args.runner_override,
        )

    elif args.release_booby_trap:
        gh_attack_runner = ReleaseBoobyTrapAttack(
            args.gh_token,
            author_email=args.author_email,
            author_name=args.author_name,
            socks_proxy=args.socks_proxy,
            http_proxy=args.http_proxy,
            timeout=timeout,
            github_url=args.api_url,
            api_client=api_client,
        )

        await gh_attack_runner.plant_release_booby_trap(
            target_repo=args.target,
            payload_path=args.booby_payload,
            publish=args.booby_publish,
            dry_run=args.dry_run,
        )

    elif args.oidc:
        scopes = None
        if args.gh_token and args.gh_token.startswith("github_pat_"):
            gh_enumeration_runner = FineGrainedEnumerator(
                pat=args.gh_token,
                socks_proxy=args.socks_proxy,
                http_proxy=args.http_proxy,
                github_url=args.api_url,
            )
            scopes = await gh_enumeration_runner.detect_scopes(args.target)
            if "contents:write" not in scopes and "workflows:write" not in scopes:
                parser.error(
                    f"{Fore.RED}[-]{Style.RESET_ALL} The provided fine-grained token does not have the necessary 'contents:write' and 'workflows:write' permission on the targeted repository!"
                )
                return

        gh_attack_runner = OIDCAttack(
            args.gh_token,
            author_email=args.author_email,
            author_name=args.author_name,
            socks_proxy=args.socks_proxy,
            http_proxy=args.http_proxy,
            timeout=timeout,
            github_url=args.api_url,
            api_client=api_client,
        )

        await gh_attack_runner.oidc_exfil(
            args.target,
            args.branch,
            args.message,
            args.delete_run,
            args.file_name,
            args.oidc_audience,
            scopes,
            environments=args.environments,
            runner=args.runner_override,
        )


async def enumerate_finegrained(args, parser):
    """Execute fine-grained token enumeration workflow."""
    gh_enumeration_runner = FineGrainedEnumerator(
        pat=args.gh_token,
        socks_proxy=args.socks_proxy,
        http_proxy=args.http_proxy,
        skip_log=args.skip_runners,
        skip_secrets=args.skip_secrets,
        skip_admin_runners=args.skip_admin_runners,
        github_url=args.api_url,
        ignore_workflow_run=args.ignore_workflow_run,
    )

    if args.validate:
        # For validation, just check token validity
        await gh_enumeration_runner.validate_token_and_get_user()
        exec_wrapper = Execution()
        exec_wrapper.set_user_details(gh_enumeration_runner.user_perms)
    elif args.self_enumeration:
        # Fine-grained self enumeration
        result = await gh_enumeration_runner.enumerate_fine_grained_token()
        exec_wrapper = Execution()
        exec_wrapper.set_user_details(gh_enumeration_runner.user_perms)
        if isinstance(result, list):
            exec_wrapper.add_repositories(result)
    else:
        # Fine-grained tokens only support self enum and single repo
        parser.error(
            f"{Fore.RED}[-]{Style.RESET_ALL} Fine-grained tokens only support "
            "--self-enumeration or -validate modes!"
        )
        return

    # Handle output
    try:
        if args.output_json:
            Output.write_json(exec_wrapper, args.output_json)
    except Exception:
        Output.error(
            "Encountered an error writing the output JSON, this is likely a Gato-X bug."
        )


async def enumerate_classic(args, parser):
    """Execute classic enumeration workflow."""
    app_session = None
    api_client = None
    app_permissions = None

    if getattr(args, "app_credentials", None):
        app_session, api_client = await build_app_api(args, target=_enum_target(args))
        app_permissions = app_session.app_permissions

    try:
        await _enumerate_classic(args, parser, api_client, app_permissions, app_session)
    finally:
        if app_session:
            await app_session.close()


async def _enumerate_classic(args, parser, api_client, app_permissions, app_session):
    """Body of the classic enumeration workflow.

    Split out so the App session opened by :func:`enumerate_classic` is
    always closed, whatever the enumeration does.
    """
    gh_enumeration_runner = Enumerator(
        args.gh_token,
        socks_proxy=args.socks_proxy,
        http_proxy=args.http_proxy,
        skip_log=args.skip_runners,
        skip_secrets=args.skip_secrets,
        skip_admin_runners=args.skip_admin_runners,
        github_url=args.api_url,
        ignore_workflow_run=args.ignore_workflow_run,
        save_runlogs=args.save_runlogs,
        finegrained_permisions=set(app_permissions) if app_permissions else None,
        api_client=api_client,
    )

    exec_wrapper = Execution()
    orgs = []
    repos = []

    if args.validate:
        orgs = await gh_enumeration_runner.validate_only() or []
    elif args.self_enumeration:
        result = await gh_enumeration_runner.self_enumeration()
        if result:
            orgs, repos = result
    elif args.target:
        # First, determine if the target is an organization or a repository.
        if (
            await gh_enumeration_runner.api.user.get_user_type(args.target)
            == "Organization"
        ):
            org = await gh_enumeration_runner.enumerate_organization(args.target)
            orgs = [org] if org else []
        else:
            # Otherwise, simply enumerate all repositories belonging to the user.
            repos = await gh_enumeration_runner.enumerate_user(args.target)
    elif args.repositories:
        try:
            repo_list = read_file_and_validate_lines(
                args.repositories, r"[A-Za-z0-9-_.]+\/[A-Za-z0-9-_.]+"
            )
            if app_session and repo_list:
                # One installation token is scoped to one account, so a list
                # spanning accounts must fail loudly rather than 404 silently.
                installation = await app_session.resolve_installation(repo_list[0])
                app_session.check_installation_covers(installation, repo_list)
            repos = await gh_enumeration_runner.enumerate_repos(repo_list)
        except argparse.ArgumentError as e:
            parser.error(
                f"{RED_DASH} The file contained an invalid repository name!"
                f"{Output.bright(str(e))}"
            )
    elif args.commit:
        if not args.repository:
            parser.error("--commit requires --repository to be specified")
        repo_obj = await gh_enumeration_runner.enumerate_commit(
            args.repository, args.commit
        )
        repos = [repo_obj] if repo_obj else []
    elif args.repository:
        repos = await gh_enumeration_runner.enumerate_repos([args.repository])

    if args.output_yaml:
        save_workflow_ymls(args.output_yaml)

    exec_wrapper.set_user_details(gh_enumeration_runner.user_perms)
    if isinstance(orgs, list):
        exec_wrapper.add_organizations(orgs)
    else:
        exec_wrapper.add_organizations([orgs])
    if isinstance(repos, list):
        exec_wrapper.add_repositories(repos)

    try:
        if args.output_json:
            Output.write_json(exec_wrapper, args.output_json)
    except Exception:
        Output.error(
            "Encountered an error writing the output JSON, this is likely a Gato-X bug."
        )

    if args.cache_save_file:
        LocalCacheFactory.dump_cache(args.cache_save_file)
        Output.info(f"Cache saved to file:{args.cache_save_file}")


async def enumerate(args, parser):
    parser = parser.choices["enumerate"]

    local_path = getattr(args, "local", None)

    # --local is mutually exclusive with all GitHub-API enumeration modes.
    if local_path and (
        args.target
        or args.self_enumeration
        or args.repository
        or args.repositories
        or args.validate
        or args.commit
    ):
        parser.error(
            f"{Fore.RED}[-]{Style.RESET_ALL} --local cannot be combined with "
            "--target/-t, --repository/-r, --repositories/-R, "
            "--self-enumeration, --validate or --commit."
        )

    if args.skip_runners and args.save_runlogs:
        parser.error("--skip-runners and --save-runlogs are mutually exclusive")

    if not (
        local_path
        or args.target
        or args.self_enumeration
        or args.repository
        or args.repositories
        or args.validate
        or args.commit
    ):
        parser.error(
            f"{Fore.RED}[-]{Style.RESET_ALL} No enumeration type was specified!"
        )

    # Count enumeration types, treating commit as a modifier for repository
    enumeration_count = sum(
        bool(x)
        for x in [
            local_path,
            args.target,
            args.self_enumeration,
            args.repository or args.commit,  # repository and commit work together
            args.repositories,
            args.validate,
        ]
    )

    if enumeration_count != 1:
        parser.error(
            f"{Fore.RED}[-]{Style.RESET_ALL} You must only select one enumeration type."
        )

    if args.cache_restore_file:
        LocalCacheFactory.load_cache_from_file(args.cache_restore_file)
        Output.info(f"Cache restored from file:{args.cache_restore_file}")

    if local_path:
        await enumerate_local(args, parser)
        return

    # App installation tokens are not fine-grained PATs; they take the
    # classic path with the App's own permissions supplied by the session.
    if args.gh_token and "github_pat" in args.gh_token:
        await enumerate_finegrained(args, parser)
    else:
        await enumerate_classic(args, parser)


async def enumerate_local(args, parser):
    """Drive the offline local-repository enumerator.

    No network requests are issued; checks that depend on the GitHub API are
    skipped and counted in a banner emitted when the run finishes. This
    handler exists alongside the classic/fine-grained enumerators so the
    existing code paths stay untouched.
    """
    from gatox.enumerate.local_enumerate import LocalEnumerator
    from gatox.models.execution import Execution

    runner = LocalEnumerator(
        args.local,
        recursive=getattr(args, "local_recursive", False),
        ignore_workflow_run=args.ignore_workflow_run,
    )

    try:
        repos = await runner.enumerate()
    except (FileNotFoundError, NotADirectoryError) as e:
        parser.error(f"{Fore.RED}[-]{Style.RESET_ALL} {e}")
        return

    if args.output_yaml:
        save_workflow_ymls(args.output_yaml)

    exec_wrapper = Execution()
    exec_wrapper.set_user_details(runner.user_perms)
    exec_wrapper.add_repositories(repos)

    try:
        if args.output_json:
            Output.write_json(exec_wrapper, args.output_json)
    except Exception:
        Output.error(
            "Encountered an error writing the output JSON, this is likely a Gato-X bug."
        )

    if args.cache_save_file:
        LocalCacheFactory.dump_cache(args.cache_save_file)
        Output.info(f"Cache saved to file:{args.cache_save_file}")


async def search(args, parser):
    parser = parser.choices["search"]

    app_session = None
    api_client = None
    if getattr(args, "app_credentials", None):
        app_session, api_client = await build_app_api(args, target=args.target)

    try:
        await _search(args, parser, api_client)
    finally:
        if app_session:
            await app_session.close()


async def _search(args, parser, api_client=None):
    """Body of the search command."""
    gh_search_runner = Searcher(
        args.gh_token,
        socks_proxy=args.socks_proxy,
        http_proxy=args.http_proxy,
        github_url=args.api_url,
        api_client=api_client,
    )
    if args.sourcegraph:
        if args.query and args.target:
            parser.error(
                f"{Fore.RED}[-]{Style.RESET_ALL} You cannot select an organization "
                "with a custom query!"
            )

        results = await gh_search_runner.use_sourcegraph_api(
            organization=args.target, query=args.query
        )
    else:
        if not (args.query or args.target):
            parser.error(
                f"{Fore.RED}[-]{Style.RESET_ALL} You must select an organization "
                "or pass a custom query!."
            )
        if args.query:
            results = await gh_search_runner.use_search_api(
                organization=args.target, query=args.query
            )
        else:
            results = await gh_search_runner.use_search_api(organization=args.target)

    if results:
        gh_search_runner.present_results(results, args.output_text)


async def app(args, parser):
    """Handler for the app command."""
    parser = parser.choices["app"]

    # Credentials may arrive through this subcommand's own -a/--pem or
    # through the shared --app-id/--app-key flags and their env vars.
    app_id = args.app
    app_key = args.pem
    if getattr(args, "app_credentials", None):
        app_id = app_id or args.app_credentials[0]
        app_key = app_key or args.app_credentials[1]

    if not (app_id and app_key):
        parser.error(
            f"{Fore.RED}[!]{Style.RESET_ALL} The app command needs an App ID and "
            "a private key: pass --app-id/--app-key (or -a/--pem)."
        )

    # A session gives the JWT and installation tokens self-renewal, so a
    # long installation enumeration survives the one-hour token expiry.
    app_session = AppSession(
        app_id,
        app_key,
        socks_proxy=args.socks_proxy,
        http_proxy=args.http_proxy,
        github_url=args.api_url,
    )

    app_enumerator = AppEnumerator(
        app_id=app_id,
        private_key_path=app_key,
        socks_proxy=args.socks_proxy,
        http_proxy=args.http_proxy,
        skip_log=args.skip_runners,
        skip_secrets=args.skip_secrets,
        skip_admin_runners=args.skip_admin_runners,
        github_url=args.api_url,
        session=app_session,
    )

    exec_wrapper = Execution()

    try:
        await app_enumerator.validate_app()
        if args.installations:
            # List all installations with metadata
            installations = await app_enumerator.list_installations()
            app_enumerator.report_installations(installations)

        elif args.installation:
            # Enumerate specific installation
            installation_repos = await app_enumerator.enumerate_installation(
                args.installation
            )
            if installation_repos:
                Output.result(
                    f"Successfully enumerated installation {args.installation}"
                )
                exec_wrapper.add_repositories(installation_repos)
            else:
                Output.error(f"Failed to enumerate installation {args.installation}")
        # Save output if requested
        if args.output_json:
            try:
                Output.write_json(exec_wrapper, args.output_json)
            except Exception:
                Output.error(
                    "Encountered an error writing the output JSON, this is likely a Gato-X bug."
                )

    except Exception as e:
        Output.error(f"App enumeration failed: {str(e)}")
    finally:
        await app_session.close()


async def persistence(args, parser):
    """Handler for the persistence command."""
    # Validate deploy key requirements
    if args.deploy_key and not args.key_path:
        Output.error("--key-path is required when using --deploy-key")
        return

    parser = parser.choices["persistence"]

    app_session = None
    api_client = None
    if getattr(args, "app_credentials", None):
        app_session, api_client = await build_app_api(args, target=args.target)

    gh_persistence_runner = PersistenceAttack(
        args.gh_token,
        author_email=args.author_email,
        author_name=args.author_name,
        socks_proxy=args.socks_proxy,
        http_proxy=args.http_proxy,
        github_url=args.api_url,
        api_client=api_client,
    )

    try:
        if args.collaborator:
            await gh_persistence_runner.invite_collaborators(
                args.target, args.collaborator, args.permission
            )
        elif args.deploy_key:
            await gh_persistence_runner.create_deploy_key(
                args.target, args.key_title, args.key_path
            )
    except Exception as e:
        Output.error(f"Persistence attack failed: {str(e)}")


def _add_general_argument(parser, flags, **kwargs):
    """Add a shared argument, dropping the short flag if it is already taken."""
    try:
        parser.add_argument(*flags, **kwargs)
    except argparse.ArgumentError:
        parser.add_argument(flags[0], **kwargs)


def configure_parser_general(parser, subcommand=False):
    """Helper method to add the arguments shared by every command.

    These are registered on the top level parser and on each subparser so they
    may be passed either before or after the subcommand.

    Args:
        parser: The parser to add the arguments to.
        subcommand: True when registering on a subparser. Subparsers parse into
            their own namespace which is then merged over the top level one, so
            their defaults are suppressed to avoid clobbering a value that was
            passed before the subcommand.
    """

    def default(value):
        return argparse.SUPPRESS if subcommand else value

    _add_general_argument(
        parser,
        ["--log-level"],
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=default("CRITICAL"),
        required=False,
    )

    _add_general_argument(
        parser,
        ["--socks-proxy", "-sp"],
        help=(
            "SOCKS proxy to use for requests, in"
            f" {Fore.GREEN}HOST{Style.RESET_ALL}:{Fore.GREEN}PORT"
            f" {Style.RESET_ALL}format"
        ),
        default=default(None),
        required=False,
    )

    _add_general_argument(
        parser,
        ["--http-proxy", "-p"],
        help=(
            "HTTPS proxy to use for requests, in"
            f" {Fore.GREEN}HOST{Style.RESET_ALL}:{Fore.GREEN}PORT"
            f" {Style.RESET_ALL}format."
        ),
        default=default(None),
        required=False,
    )

    _add_general_argument(
        parser,
        ["--no-color", "-nc"],
        help="Removes all color from output.",
        action="store_true",
        default=default(False),
    )

    _add_general_argument(
        parser,
        ["--config"],
        help=(
            "Path to a YAML config file setting any command line option.\n"
            "Defaults to ~/.config/gato-x/config.yaml when present.\n"
            "Environment variable: GATOX_CONFIG"
        ),
        metavar="PATH/TO/config.yaml",
        default=default(None),
        required=False,
    )

    _add_general_argument(
        parser,
        ["--no-config"],
        help="Ignore the config file entirely and use only flags and env vars.",
        action="store_true",
        default=default(False),
    )

    _add_general_argument(
        parser,
        ["--app-id"],
        help=(
            "GitHub App ID to authenticate as, instead of a PAT.\n"
            "Requires --app-key. Gato-X signs the JWT, exchanges it for an\n"
            "installation token, and renews both for the life of the run.\n"
            f"Environment variable: {Fore.GREEN}GH_APP_ID{Style.RESET_ALL}"
        ),
        metavar=f"{Fore.RED}APP_ID{Style.RESET_ALL}",
        default=default(None),
        required=False,
    )

    _add_general_argument(
        parser,
        ["--app-key"],
        help=(
            "Path to the GitHub App private key PEM file. The PEM contents\n"
            "may be passed directly instead of a path.\n"
            f"Environment variable: {Fore.GREEN}GH_APP_KEY{Style.RESET_ALL}"
        ),
        metavar=f"{Fore.RED}PATH/TO/PRIVATE_KEY.pem{Style.RESET_ALL}",
        default=default(None),
        required=False,
    )

    _add_general_argument(
        parser,
        ["--installation-id"],
        help=(
            "Use this App installation rather than resolving one from the\n"
            "target. Run 'gatox app --installations' to list them.\n"
            f"Environment variable: {Fore.GREEN}GH_APP_INSTALLATION_ID"
            f"{Style.RESET_ALL}"
        ),
        metavar=f"{Fore.RED}INSTALLATION_ID{Style.RESET_ALL}",
        default=default(None),
        required=False,
    )

    _add_general_argument(
        parser,
        ["--api-url", "-u"],
        help=(
            f"{Fore.RED}{Output.bright('[Experimental]')}\n"
            "GitHub URL to target. Paste the URL you use in the browser and\n"
            "gato-x derives the REST and GraphQL endpoints from it:\n"
            "  https://SUBDOMAIN.ghe.com  -> https://api.SUBDOMAIN.ghe.com\n"
            "  https://ghes.example.com   -> https://ghes.example.com/api/v3\n"
            "An explicit API base is accepted too.\n"
            "Defaults to 'https://api.github.com'"
        ),
        metavar="https://subdomain.ghe.com",
        default=default(None),
        required=False,
    )
