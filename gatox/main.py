import asyncio
import sys

from gatox.cli import cli
from gatox.cli.exit_codes import ExitCode
from gatox.cli.output import Output


def entry() -> int:
    """Run the CLI, preserving its exit status and handling interruption."""
    try:
        return asyncio.run(cli.cli(sys.argv[1:]))
    except KeyboardInterrupt:
        Output.warn("Interrupted.")
        return ExitCode.INTERRUPTED


if __name__ == "__main__":
    sys.exit(entry())
