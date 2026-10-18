# Red/Green Evidence

Focused RED runs proved the regressions before production changes:

- Initial retrieval, profile, purpose, and root-order tests: `7 failed, 9 passed`.
- Unknown-course title-term fallback: `3 failed`.
- Canvas course-code suffix: `1 failed`.
- Relevant path weighting: `1 failed`.

Focused GREEN:

```text
python3 -m pytest -q tests/test_examples.py tests/test_ranking.py tests/test_cli.py
18 passed
```

The live default command selected
`hermes_data_system/analytics_ops.py`, not `cli.py`; its excerpt contains
`outcome` and `model` from the categorical profile.

Final verification:

```text
python3 -m pytest -q
21 passed
python3 -m compileall -q learning_tracking tests
exit 0
python3 -m learning_tracking start-learning
Analysis of Categorical Data (2650) -> hermes_data_system/analytics_ops.py
```
