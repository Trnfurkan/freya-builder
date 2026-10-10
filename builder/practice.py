"""Run the kit's practice customers (tools/run_cases.py) unchanged, with the operating system's certificate store.

    python -m builder.practice <agent_id> --brief A [--style pieces] [--out results/<slot>-1.json]

On this machine Python's own certificate check failed for the bank's practice host
(ssl.SSLCertVerificationError: CERTIFICATE_VERIFY_FAILED); the system store, through `truststore` (installed with the
Claude Agent SDK), works. Same arguments and output as tools/run_cases.py.
"""
import pathlib
import runpy
import sys

KIT = pathlib.Path(__file__).resolve().parent.parent


def use_system_certificates():
    """True when Python's ssl now uses the system certificate store."""
    try:
        import truststore
    except ImportError:
        return False
    truststore.inject_into_ssl()
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not use_system_certificates():
        print("truststore not installed: running with Python's own certificates", file=sys.stderr)
    sys.path.insert(0, str(KIT / "tools"))
    sys.argv = [str(KIT / "tools" / "run_cases.py")] + list(argv)
    runpy.run_path(str(KIT / "tools" / "run_cases.py"), run_name="__main__")


if __name__ == "__main__":
    main()
