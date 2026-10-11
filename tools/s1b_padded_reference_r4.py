"""Capture a fresh padded SDC-ON/recovery-OFF L_ref before the original scenes.

Only the new padded envelope is accepted; the reviewed runner and all its gates
are reused. Historical references cannot be supplied or resumed.
"""
import argparse
import json
from pathlib import Path

import s1b_system_r5 as runner
from s1b_padded_rebind_r4 import validate_profile

W = Path(__file__).resolve().parents[1]


def main(evidence=None, results=None):
    evidence = Path(evidence) if evidence is not None else W / "evidence/PaddedS1bR4"
    results = Path(results) if results is not None else W / "results" / evidence.name
    predictions = evidence / "profile/predictions.json"
    sealed = json.loads(predictions.read_text())
    validate_profile(sealed)
    plan = runner.scene_plan(sealed)
    assert plan[0][2]["sdc_enable"] == 1 and plan[0][2]["recovery_enable"] == 0 and plan[0][3]
    assert not (evidence / "system").exists() and not results.exists()
    original_freeze = runner.freeze_reference

    def freeze_padded(directory, eligibility, dispatch, context, provenance):
        validate_profile(sealed)
        assert context["image_id"] == sealed["image"]["image_id"]
        assert context["source_hashes"] == sealed["source_hashes"]
        provenance = dict(provenance, padded_profile=sealed["padded_profile"],
                          profile_fingerprint_sha256=sealed["profile_fingerprint_sha256"])
        return original_freeze(directory, eligibility, dispatch, context, provenance)

    runner.E, runner.ROOT, runner.PRED = evidence / "system", results, predictions
    runner.freeze_reference = freeze_padded
    return runner.main()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--results", type=Path)
    args = parser.parse_args()
    raise SystemExit(main(args.evidence, args.results))
