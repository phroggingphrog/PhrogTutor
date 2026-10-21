from deeptutor.services.llm.request_cache import compare_requests, fingerprint_request


def test_prefix_diagnostics_distinguish_append_history_tools_and_route_changes():
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "question"}]
    route = {"model": "example"}
    previous = fingerprint_request(messages, [], route)
    current = fingerprint_request(
        [*messages, {"role": "assistant", "content": "answer"}], [], route
    )
    assert compare_requests(previous, current)["first_change"] == "append"
    assert compare_requests(previous, current)["shared_prefix_messages"] == 2
    assert "question" not in str(previous)
    assert (
        compare_requests(previous, fingerprint_request(messages[:1], [], route))["first_change"]
        == "history"
    )
    assert (
        compare_requests(previous, fingerprint_request(messages, [{"name": "new"}], route))[
            "first_change"
        ]
        == "tools"
    )
    assert (
        compare_requests(previous, fingerprint_request(messages, [], {"model": "other"}))[
            "first_change"
        ]
        == "route"
    )
