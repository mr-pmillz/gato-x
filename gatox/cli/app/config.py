from gatox.cli.output import Fore, Output, Style
from gatox.util.arg_utils import ReadableFile, StringType


def configure_parser_app(parser):
    """Helper method to add arguments to the app subparser.

    Args:
        parser: sub parser to add arguments to.
    """

    parser.add_argument(
        "--app",
        "-a",
        help=(
            "GitHub App ID to authenticate with.\n"
            "The shared --app-id flag (or GH_APP_ID) works here too."
        ),
        metavar=f"{Fore.RED}APP_ID{Style.RESET_ALL}",
        type=int,
        required=False,
    )

    parser.add_argument(
        "--pem",
        help=(
            "Path to the GitHub App private key PEM file.\n"
            "The shared --app-key flag (or GH_APP_KEY) works here too."
        ),
        metavar=f"{Fore.RED}PATH/TO/PRIVATE_KEY.pem{Style.RESET_ALL}",
        type=ReadableFile(),
        required=False,
    )

    # Create mutually exclusive group for the different operation modes
    operation_group = parser.add_mutually_exclusive_group(required=True)

    operation_group.add_argument(
        "--installations",
        help="List all app installations with metadata including repositories and permissions.",
        action="store_true",
    )

    operation_group.add_argument(
        "--installation",
        "-i",
        help="Enumerate a specific installation by ID.",
        metavar=f"{Fore.RED}INSTALLATION_ID{Style.RESET_ALL}",
        type=int,
    )

    parser.add_argument(
        "--skip-runners",
        "-sr",
        help=(
            f"Do {Output.bright('NOT')} enumerate runners via run-log analysis, this will\n"
            "speed up the enumeration, but will miss self-hosted runners for\n"
            "non-admin users."
        ),
        action="store_true",
    )

    parser.add_argument(
        "--skip-secrets",
        "-ss",
        action="store_true",
        help="Skip secrets enumeration for repositories and organizations.",
    )

    parser.add_argument(
        "--skip-admin-runners",
        "-sar",
        action="store_true",
        help=(
            "Skip checking for self-hosted runners via the administrative API.\n"
            "This is useful when the App has admin permissions but you want to\n"
            "avoid the additional API calls."
        ),
    )

    parser.add_argument(
        "--output-json",
        "-oJ",
        help=("Save enumeration output to JSON file."),
        metavar="JSON_FILE",
        type=StringType(256),
    )
