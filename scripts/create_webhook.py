"""Register the post-call webhook, so a finished call is sent to this backend.

    uv run python scripts/create_webhook.py abc-def.trycloudflare.com
    uv run python scripts/create_webhook.py abc-def.trycloudflare.com --dry-run

The platform's webhook URL can't be edited, and a quick tunnel gets a new hostname every time it
starts, so this replaces the webhook called "Saffron Table post-call" each time it runs. The new
webhook has a new signing secret. It is written straight to ELEVENLABS_WEBHOOK_SECRET in `.env` and
never printed, so restart the backend afterwards: it reads the secret when it starts.

It also tells the platform to send finished-call transcripts, and only those, to this webhook, and
turns on retries for failed deliveries.
"""

import argparse
import asyncio
from pathlib import Path

import httpx

from maitre_d.config import Settings
from maitre_d.elevenlabs import (
    ElevenLabsError,
    create_webhook,
    delete_webhooks_named,
    make_client,
    use_for_post_call_transcripts,
    validate_backend_host,
)
from maitre_d.envfile import set_env_value

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
WEBHOOK_NAME = "Saffron Table post-call"
WEBHOOK_PATH = "/webhooks/elevenlabs"


async def replace_webhook(api_key: str, url: str) -> tuple[str, str]:
    """Replace the webhook and return `(webhook_id, secret)`."""
    async with make_client(api_key) as client:
        removed = await delete_webhooks_named(client, WEBHOOK_NAME)
        created = await create_webhook(client, name=WEBHOOK_NAME, url=url)
        await use_for_post_call_transcripts(
            client, webhook_id=created.webhook_id, name=WEBHOOK_NAME
        )
    print(f"webhook replaced ({removed} removed), id {created.webhook_id}")
    return created.webhook_id, created.secret


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("hostname", help="bare public hostname of the backend")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the URL; make no network call"
    )
    args = parser.parse_args()

    try:
        host = validate_backend_host(args.hostname)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    url = f"https://{host}{WEBHOOK_PATH}"

    if args.dry_run:
        print(f"dry run: would point the post-call webhook at {url} (no network call)")
        return

    key = Settings().elevenlabs_api_key
    if key is None or not key.get_secret_value().strip():
        raise SystemExit("ELEVENLABS_API_KEY must be set in .env or the environment.")
    try:
        _webhook_id, secret = asyncio.run(replace_webhook(key.get_secret_value(), url))
    except ElevenLabsError as exc:
        raise SystemExit(str(exc)) from None
    except httpx.HTTPError as exc:
        raise SystemExit(f"Could not reach ElevenLabs ({type(exc).__name__}).") from None

    set_env_value(ENV_FILE, "ELEVENLABS_WEBHOOK_SECRET", secret)
    print("ELEVENLABS_WEBHOOK_SECRET written to .env. Restart the backend to pick it up.")


if __name__ == "__main__":
    main()
