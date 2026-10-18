import json

from learning_tracking.progress import append_attempt, derive_mastery


def test_attempts_append_and_mastery_uses_only_explicit_evidence(tmp_path):
    path = tmp_path / "progress.jsonl"
    append_attempt(
        path,
        {
            "assignment_id": "essay",
            "completed": True,
            "evidence": {"source analysis": {"recall": True}},
        },
    )
    append_attempt(
        path,
        {
            "assignment_id": "essay",
            "score": 100,
            "evidence": {"source analysis": {"application": False}},
        },
    )

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    mastery = derive_mastery(path)
    assert mastery == {
        "source analysis": {"recall": True, "application": False}
    }
    assert "transfer" not in mastery["source analysis"]
