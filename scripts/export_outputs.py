"""Convert ``cdk deploy --outputs-file`` JSON into application settings JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MAPPING = {
    "ApiBaseUrl": "api_base_url",
    "ArtifactBucketName": "s3_bucket_name",
    "JobsTableName": "dynamodb_table_name",
    "IntakeQueueUrl": "sqs_queue_url",
    "AgentCoreRuntimeArn": "agentcore_runtime_arn",
    "InputValidatorArn": "input_validator_arn",
    "OutputValidatorArn": "output_validator_arn",
    "AppSecretArn": "app_secret_arn",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("generated/deployment_outputs.json"))
    arguments = parser.parse_args()

    stacks = json.loads(arguments.input.read_text(encoding="utf-8"))
    flattened: dict[str, str] = {}
    for outputs in stacks.values():
        for cloudformation_name, settings_name in MAPPING.items():
            if cloudformation_name in outputs:
                flattened[settings_name] = outputs[cloudformation_name]
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(flattened, indent=2), encoding="utf-8")
    print(f"Wrote {arguments.output}")


if __name__ == "__main__":
    main()
