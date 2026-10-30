"""Write knowledge/restaurant.md from restaurant.py, or check that the committed copy is current.

    uv run python scripts/generate_knowledge.py          # write the page
    uv run python scripts/generate_knowledge.py --check  # exit 1 if the committed page is stale

--check prints only the path of the stale file. The page contains no secrets, and nothing else is
printed, so the check is safe to run in CI logs.
"""

import argparse
import sys
from pathlib import Path

from maitre_d.knowledge import render_restaurant_page

PAGE = Path(__file__).resolve().parents[1] / "knowledge" / "restaurant.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed page differs from the render, without writing",
    )
    args = parser.parse_args(argv)

    text = render_restaurant_page()
    if args.check:
        committed = PAGE.read_text(encoding="utf-8") if PAGE.exists() else None
        if committed != text:
            print(f"{PAGE} is out of date; run scripts/generate_knowledge.py", file=sys.stderr)
            return 1
        return 0

    # LF and no BOM, whatever the platform: the repo normalises to LF, and a BOM would make the
    # agent's knowledge base start with an invisible character.
    PAGE.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {PAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
