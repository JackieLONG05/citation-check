from app.main import _candidate_search_jobs


def test_author_year_candidate_search_does_not_leak_previous_sentence_title_quote():
    text = (
        "The joystick or keyboard can move “Little Red-Cap” through the route. "
        "Kucirkova argues that children’s digital books need evaluation in relation to interaction and age (2019)."
    )

    jobs = _candidate_search_jobs(text)

    assert jobs
    assert "Little Red-Cap" not in jobs[0].query
    assert "Kucirkova 2019" in jobs[0].query
