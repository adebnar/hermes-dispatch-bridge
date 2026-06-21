from app.hermes_client import extract_session_token


def test_extracts_token_from_dashboard_html():
    html = '<script>window.__HERMES_SESSION_TOKEN__="abc-123_XYZ";foo=1;</script>'
    assert extract_session_token(html) == "abc-123_XYZ"


def test_handles_whitespace_variants():
    html = 'window.__HERMES_SESSION_TOKEN__ = "tok"'
    assert extract_session_token(html) == "tok"


def test_returns_none_when_absent():
    assert extract_session_token("<html>no token here</html>") is None
    assert extract_session_token("") is None
