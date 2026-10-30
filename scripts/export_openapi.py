"""Write the backend's OpenAPI document, which the frontend's TypeScript types are generated from.

    uv run python scripts/export_openapi.py            # write frontend/openapi.json
    uv run python scripts/export_openapi.py --check    # exit 1 if the committed copy is stale

The Pydantic models are the one definition of the API's shape. The document is committed so the
frontend can generate its types without running Python, and `--check` is the CI gate that fails
when a response model changed and the committed copy did not.
"""

import argparse
import json
from pathlib import Path

from maitre_d.config import Settings
from maitre_d.main import create_app

OUTPUT = Path(__file__).resolve().parents[1] / "frontend" / "openapi.json"


def render() -> str:
    # `_env_file=None`: the document must not depend on whose machine generates it.
    document = create_app(Settings(_env_file=None)).openapi()
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 instead of writing")
    args = parser.parse_args()

    rendered = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != rendered:
            raise SystemExit(f"{OUTPUT} is out of date; run scripts/export_openapi.py")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
