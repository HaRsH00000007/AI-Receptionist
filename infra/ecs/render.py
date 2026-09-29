"""Render the ECS task-definition templates into registrable JSON.

    python infra/ecs/render.py OUT_DIR

Reads its variables from the environment (deploy.sh exports deploy.env plus
IMAGE_TAG, BACKEND_IMAGE and FRONTEND_IMAGE), the shared backend environment
from backend.env, and secret *key names* from secrets.txt. Secret values are
never read: each secret becomes a `valueFrom` reference that ECS resolves at
task start.

Standard library only, so it runs anywhere deploy.sh does.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from string import Template
from typing import Any

HERE = Path(__file__).resolve().parent
PLACEHOLDER = re.compile(r"\$\{[A-Z0-9_]+\}")


def _substitute(text: str, where: str) -> str:
    try:
        return Template(text).substitute(os.environ)
    except KeyError as exc:
        sys.exit(f"render: {where} needs ${{{exc.args[0]}}}, which is not set")


def _backend_env() -> list[dict[str, str]]:
    pairs: dict[str, str] = {}
    for number, raw in enumerate((HERE / "backend.env").read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            sys.exit(f"render: backend.env line {number} is not KEY=VALUE")
        pairs[key.strip()] = _substitute(value.strip(), f"backend.env {key.strip()}")
    return [{"name": k, "value": v} for k, v in sorted(pairs.items())]


def secret_names() -> list[str]:
    lines = (HERE / "secrets.txt").read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _backend_secrets() -> list[dict[str, str]]:
    arn = os.environ["SECRET_ARN"]
    return [{"name": name, "valueFrom": f"{arn}:{name}::"} for name in secret_names()]


def _fill(node: Any, env: list[dict[str, str]], secrets: list[dict[str, str]]) -> Any:
    if node == "__BACKEND_ENV__":
        return env
    if node == "__BACKEND_SECRETS__":
        return secrets
    if isinstance(node, dict):
        return {k: _fill(v, env, secrets) for k, v in node.items()}
    if isinstance(node, list):
        return [_fill(v, env, secrets) for v in node]
    return node


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--secret-names":
        print("\n".join(secret_names()))
        return
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    env, secrets = _backend_env(), _backend_secrets()
    for template in sorted((HERE / "taskdefs").glob("*.json")):
        text = _substitute(template.read_text(), template.name)
        rendered = _fill(json.loads(text), env, secrets)
        leftover = PLACEHOLDER.findall(json.dumps(rendered))
        if leftover:
            sys.exit(f"render: {template.name} has unresolved {sorted(set(leftover))}")
        (out / template.name).write_text(json.dumps(rendered, indent=2) + "\n")
        print(f"rendered {template.name}")


if __name__ == "__main__":
    main()
