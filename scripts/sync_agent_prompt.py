"""Copy the agent's prompt from Markdown into the agent config.

    uv run python scripts/sync_agent_prompt.py          # write the prompt into the config
    uv run python scripts/sync_agent_prompt.py --check  # exit 1 if the config is out of date

The prompt is the most important text in the agent, and inside a JSON string it is one long line
of `\\n` escapes that no one can review in a diff. So it is written as Markdown in
`agent/prompts/` and embedded here, and `tests/test_agent_config.py` fails if the two differ.
Edit the Markdown, run this, then push the config with the ElevenLabs CLI.
"""

import argparse
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_FILE = REPO_ROOT / "agent" / "prompts" / "saffron_table.md"
CONFIG_FILE = REPO_ROOT / "agent" / "agent_configs" / "saffron_table.json"


def read_prompt(path: Path = PROMPT_FILE) -> str:
    # No trailing newline: the editor adds one to the Markdown file, the platform needs none.
    return path.read_text(encoding="utf-8").rstrip("\n")


def render_config(config: dict[str, Any], prompt: str) -> str:
    """The config as JSON text with `prompt` in place, in the layout the rest of the repo uses."""
    config["conversation_config"]["agent"]["prompt"]["prompt"] = prompt
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 instead of writing")
    args = parser.parse_args()

    config: dict[str, Any] = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    rendered = render_config(config, read_prompt())
    if args.check:
        if CONFIG_FILE.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"{CONFIG_FILE} is out of date; run scripts/sync_agent_prompt.py")
        return
    CONFIG_FILE.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"prompt written to {CONFIG_FILE}")


if __name__ == "__main__":
    main()
