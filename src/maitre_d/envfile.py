"""Set one value in a `.env` file without disturbing the rest of it.

The setup scripts learn two things only after they have called ElevenLabs: the agent's ID and the
webhook's signing secret. Writing them straight into `.env` means the secret is never printed or
copied by hand.
"""

from pathlib import Path


def set_env_value(path: Path, key: str, value: str) -> None:
    """Replace the line for `key`, or append one. Every other line is left exactly as it was."""
    if "\n" in value or "\r" in value:
        raise ValueError("an environment value must be a single line")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for index, line in enumerate(lines):
        name, separator, _ = line.partition("=")
        if separator and name.strip() == key:
            lines[index] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
