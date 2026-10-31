from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score predictions against a human-labeled JSON set"
    )
    parser.add_argument("predictions", type=Path)
    parser.add_argument("labels", type=Path)
    args = parser.parse_args()
    predicted = json.loads(args.predictions.read_text(encoding="utf-8"))["restrictions"]
    expected = json.loads(args.labels.read_text(encoding="utf-8"))["restrictions"]
    predicted_keys = {
        (item["fund_name"], item["category"], item["restriction_text"]) for item in predicted
    }
    expected_keys = {
        (item["fund_name"], item["category"], item["restriction_text"]) for item in expected
    }
    true_positive = len(predicted_keys & expected_keys)
    precision = true_positive / len(predicted_keys) if predicted_keys else 0.0
    recall = true_positive / len(expected_keys) if expected_keys else 0.0
    print(
        json.dumps(
            {"precision": precision, "recall": recall, "human_labels": len(expected_keys)}, indent=2
        )
    )


if __name__ == "__main__":
    main()
