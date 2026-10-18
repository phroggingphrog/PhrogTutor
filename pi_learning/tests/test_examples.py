from pathlib import Path

from learning_tracking.examples import find_example


def test_retriever_returns_one_safe_bounded_example():
    root = Path("tests/fixtures/hermes")

    result = find_example("schedule learning", [root], max_files=20, max_bytes=2_000)

    assert result is not None
    assert result.path.resolve() == (root / "project_example.py").resolve()
    assert 0 < result.score
    assert "schedule_learning" in result.excerpt
    assert len(result.excerpt) <= 500
    assert "synthetic-do-not-return" not in result.excerpt


def test_retriever_obeys_file_and_byte_limits():
    root = Path("tests/fixtures/hermes")

    assert find_example("deadline", [root], max_files=0, max_bytes=2_000) is None
    assert find_example("deadline", [root], max_files=20, max_bytes=0) is None


def test_retriever_rejects_a_blocked_root(tmp_path):
    root = tmp_path / ".git"
    root.mkdir()
    (root / "match.txt").write_text("schedule learning", encoding="utf-8")

    assert find_example("schedule learning", [root]) is None


def test_retriever_skips_broken_files(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "broken.txt").symlink_to("missing.txt")

    assert find_example("schedule learning", [root]) is None


def test_retriever_prefers_distinct_categorical_concepts_and_centers_excerpt(tmp_path):
    root = tmp_path / "hermes"
    root.mkdir()
    (root / "cli.py").write_text(
        ("assessment assignment case studies " * 100) + "categorical",
        encoding="utf-8",
    )
    (root / "analytics_ops.py").write_text(
        ("generic preamble\n" * 80)
        + "outcome classification count diagnostic\n"
        + ("generic suffix\n" * 80),
        encoding="utf-8",
    )

    result = find_example(
        "Categorical Case Studies assessment assignment outcome classification count diagnostic",
        [root],
    )

    assert result is not None
    assert result.path == (root / "analytics_ops.py").resolve()
    assert "outcome classification count diagnostic" in result.excerpt
    assert not result.excerpt.startswith("generic preamble")
    assert len(result.excerpt) <= 500


def test_retriever_breaks_score_ties_by_lexical_path(tmp_path):
    (tmp_path / "z.py").write_text("outcome", encoding="utf-8")
    (tmp_path / "a.py").write_text("outcome", encoding="utf-8")

    result = find_example("outcome", [tmp_path])

    assert result is not None
    assert result.path == (tmp_path / "a.py").resolve()


def test_retriever_rewards_relevant_path_tokens_as_distinct_signals(tmp_path):
    (tmp_path / "analytics_ops.py").write_text(
        "outcome count model", encoding="utf-8"
    )
    (tmp_path / "legacy.py").write_text(
        "outcome classification count diagnostic", encoding="utf-8"
    )

    result = find_example(
        "categorical classification outcome logistic odds count model aic diagnostic regression",
        [tmp_path],
    )

    assert result is not None
    assert result.path == (tmp_path / "analytics_ops.py").resolve()
