"""Build Linux-compatible deployment directories for Lambda and AgentCore.

This script is intentionally explicit instead of hiding packaging inside CDK Docker
bundling.  Students can inspect ``generated/packages`` before deployment and see
exactly which source and dependencies each AWS compute boundary receives.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "generated" / "packages"
LAMBDA_PLATFORM = "x86_64-manylinux_2_28"
# AgentCore direct-code runtimes use Linux ARM64 even though the five Lambda functions
# in this project deliberately use x86-64. Native wheels must match each compute target.
AGENTCORE_PLATFORM = "aarch64-manylinux2014"
PYTHON_VERSION = "3.12"

PACKAGE_DEPENDENCIES = {
    "api": ["boto3>=1.40,<2", "pydantic>=2.11,<3"],
    "dispatch": ["boto3>=1.40,<2"],
    "reconcile": ["boto3>=1.40,<2"],
    "validate_input": [
        "boto3>=1.40,<2",
        "pydantic>=2.11,<3",
        "pymupdf>=1.26,<2",
        "python-dotenv>=1.1,<2",
    ],
    "validate_output": ["boto3>=1.40,<2", "pydantic>=2.11,<3"],
    "agentcore": [
        "bedrock-agentcore>=1.25,<1.26",
        "boto3>=1.40,<2",
        "langgraph>=1.2,<1.3",
        "pillow>=11,<13",
        "pydantic>=2.11,<3",
        "pymupdf>=1.26,<2",
        "python-dotenv>=1.1,<2",
        "pyyaml>=6,<7",
        "tenacity>=9,<10",
    ],
}


def _copy_source(destination: Path, package_name: str) -> None:
    """Copy first-party code after dependencies so our modules take precedence."""

    shutil.copytree(ROOT / "app", destination / "app", dirs_exist_ok=True)
    shutil.copytree(ROOT / "config", destination / "config", dirs_exist_ok=True)
    if package_name == "agentcore":
        shutil.copy2(ROOT / "runtime_entrypoint.py", destination / "runtime_entrypoint.py")
    else:
        shutil.copytree(
            ROOT / "lambdas" / package_name,
            destination / "lambdas" / package_name,
            dirs_exist_ok=True,
        )
        (destination / "lambdas" / "__init__.py").write_text("", encoding="utf-8")


def build(package_names: list[str], *, skip_dependencies: bool = False) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for package_name in package_names:
        destination = OUTPUT / package_name
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)

        if not skip_dependencies:
            target_platform = (
                AGENTCORE_PLATFORM if package_name == "agentcore" else LAMBDA_PLATFORM
            )
            command = [
                "uv",
                "pip",
                "install",
                "--target",
                str(destination),
                "--python-platform",
                target_platform,
                "--python-version",
                PYTHON_VERSION,
                "--only-binary",
                ":all:",
                *PACKAGE_DEPENDENCIES[package_name],
            ]
            subprocess.run(command, cwd=ROOT, check=True)
        _copy_source(destination, package_name)
        print(f"Built {package_name}: {destination}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "packages",
        nargs="*",
        choices=sorted(PACKAGE_DEPENDENCIES),
        help="Optional subset; defaults to every deployment package.",
    )
    parser.add_argument(
        "--skip-dependencies",
        action="store_true",
        help="Copy first-party source only. Useful for CDK unit tests, never for deploy.",
    )
    arguments = parser.parse_args()
    build(
        arguments.packages or list(PACKAGE_DEPENDENCIES),
        skip_dependencies=arguments.skip_dependencies,
    )


if __name__ == "__main__":
    main()
