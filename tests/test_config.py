from __future__ import annotations

import json

from app.common.config import load_settings


def test_settings_precedence(tmp_path):
    env_file = tmp_path / ".env"
    output_file = tmp_path / "outputs.json"
    env_file.write_text("AWS_REGION=us-east-1\nMAX_MODEL_CALLS=3\n", encoding="utf-8")
    output_file.write_text(
        json.dumps({"aws_region": "eu-west-1", "max_model_calls": 4}), encoding="utf-8"
    )

    settings = load_settings(
        env_file=env_file,
        output_file=output_file,
        environ={"AWS_REGION": "ap-south-1", "MAX_MODEL_CALLS": "5"},
        overrides={"max_model_calls": 6},
    )

    assert settings.aws_region == "ap-south-1"
    assert settings.max_model_calls == 6


def test_aws_mode_is_default_and_has_no_guessed_model_id(tmp_path):
    settings = load_settings(
        env_file=tmp_path / "missing.env",
        output_file=tmp_path / "missing.json",
        environ={},
    )
    assert settings.app_mode == "aws"
    assert settings.bedrock_model_id == ""
