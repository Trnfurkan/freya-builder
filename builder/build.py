"""The final's command: case folder and mock address in, the exported phone agent and its trace out.

    python builder/build.py <case folder> <mock URL> [--openapi <tool API file>] [any builder.run option]

The same as `python -m builder.run <case folder> --mock-url <mock URL> --openapi <file> --slot final --export`, with
the final's defaults: slot "final", 100 minutes (the case is open for three hours, and the mock comes first), no
practice customers (the practice set is not the final case). The tool API file defaults to mock/openapi.json, written from the case's
service contract next to the mock (mock/README.md). Everything else is builder/run.py.
"""
import pathlib
import sys

KIT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))

from builder import run  # noqa: E402

DEFAULTS = {"--slot": "final", "--budget-min": "100", "--openapi": str(KIT / "mock" / "openapi.json")}


def to_run_args(argv):
    """builder.run's arguments for `build.py <case folder> <mock URL> [options]`."""
    if len(argv) < 2 or argv[0].startswith("-") or argv[1].startswith("-"):
        sys.exit(__doc__)
    folder, mock_url, rest = argv[0], argv[1], list(argv[2:])
    if not pathlib.Path(folder).is_dir():
        sys.exit(f"not a case folder: {folder}")
    if not mock_url.startswith("https://"):
        sys.exit(f"the mock URL must start with https://: {mock_url}")
    args = [folder, "--mock-url", mock_url]
    for flag, value in DEFAULTS.items():
        if flag not in rest:
            args += [flag, value]
    if "--export" not in rest:
        args.append("--export")
    return args + rest


if __name__ == "__main__":
    sys.exit(run.main(to_run_args(sys.argv[1:])))
