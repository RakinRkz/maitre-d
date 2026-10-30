"""Point the `backend_host` ElevenLabs environment variable at the backend's public address.

    uv run python scripts/set_backend_host.py abc-def.trycloudflare.com
    uv run python scripts/set_backend_host.py localhost:8000 --environment development --dry-run

The agent's tool URLs read this variable (docs/decisions.md #12), so one agent can serve several
environments, each with its own backend. Run it again whenever the tunnel restarts: a quick tunnel
gets a new hostname every time it starts.

--environment defaults to production, which is what a web session uses unless ELEVENLABS_ENVIRONMENT
names another. With no deployed backend there is no second host, so production is the tunnel. Once
there is a deployment, point production at it and use --environment development for the tunnel.

Give the bare hostname, with an optional port and no scheme or path. Anything else is refused,
not corrected, so a typo shows up here and not as a failed tool call during a call.
"""

import argparse
import asyncio

import httpx

from maitre_d.config import Settings
from maitre_d.elevenlabs import (
    ElevenLabsError,
    make_client,
    set_environment_variable,
    validate_backend_host,
)

LABEL = "backend_host"
DEFAULT_ENVIRONMENT = "production"


async def set_host(api_key: str, host: str, environment: str) -> None:
    async with make_client(api_key) as client:
        await set_environment_variable(client, label=LABEL, environment=environment, value=host)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "hostname", help="bare hostname, such as abc-def.trycloudflare.com or localhost:8000"
    )
    parser.add_argument(
        "--environment",
        default=DEFAULT_ENVIRONMENT,
        help=f"ElevenLabs environment to set (default: {DEFAULT_ENVIRONMENT})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the change; make no network call"
    )
    args = parser.parse_args()

    try:
        host = validate_backend_host(args.hostname)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    if args.dry_run:
        print(f"dry run: would set {LABEL}[{args.environment}] = {host} (no network call)")
        return

    key = Settings().elevenlabs_api_key
    if key is None or not key.get_secret_value().strip():
        raise SystemExit("ELEVENLABS_API_KEY must be set in .env or the environment.")
    try:
        asyncio.run(set_host(key.get_secret_value(), host, args.environment))
    except ElevenLabsError as exc:
        raise SystemExit(str(exc)) from None
    except ValueError as exc:
        # Raised for a missing variable outside production, with a message written for the user.
        raise SystemExit(str(exc)) from None
    except httpx.HTTPError as exc:
        # Only the class is printed: the exception text can include the URL.
        raise SystemExit(f"Could not reach ElevenLabs ({type(exc).__name__}).") from None
    print(f"{LABEL}[{args.environment}] = {host}")


if __name__ == "__main__":
    main()
