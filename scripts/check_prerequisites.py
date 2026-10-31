"""Report local tools and fail early when a requested deployment tool is missing."""

from __future__ import annotations

import shutil
import subprocess

TOOLS = {
    "python": ["python", "--version"],
    "uv": ["uv", "--version"],
    "node": ["node", "--version"],
    "npm": ["npm", "--version"],
    "aws": ["aws", "--version"],
    "cdk": ["npx", "cdk", "--version"],
}


def main() -> None:
    missing: list[str] = []
    for name, command in TOOLS.items():
        executable = shutil.which(command[0])
        if not executable:
            missing.append(name)
            print(f"MISSING  {name}")
            continue
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        version = (result.stdout or result.stderr).strip().splitlines()[0]
        print(f"OK       {name}: {version}")
    if missing:
        raise SystemExit("Missing tools: " + ", ".join(missing))


if __name__ == "__main__":
    main()
