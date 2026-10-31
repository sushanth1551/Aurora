"""API token used by the test suite (set by ``conftest.isolated_settings``)."""

TEST_API_TOKEN = "test-api-token-0123456789abcdefghijklmnopqrstuv"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_API_TOKEN}"}
