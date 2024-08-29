import pytest


@pytest.fixture
def http_response():
    def _http_response(plugin):
        with open(f'test/fixtures/{plugin}.txt', encoding='utf8') as f:
            return f.read()

    return _http_response
