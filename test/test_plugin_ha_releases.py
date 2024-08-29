from unittest.mock import patch

import pytest

from informa.plugins.ha_releases import NewVersion, fetch_ha_releases


@patch('requests.get')
@pytest.mark.parametrize('version', [None, '2024.8.1', '2024.9.3'])
def test_ha_releases_returns_version_on_diff_version(mock_requests_get, http_response, version):
    '''
    Ensure NewVersion object is returned when no version match is found
    '''
    mock_requests_get.return_value.text = http_response('ha_releases')

    assert fetch_ha_releases(version) == NewVersion(
        '2024.8.3', '/blog/2024/08/07/release-20248/', '2024.8: Beautiful badges!'
    )


@patch('requests.get')
def test_ha_releases_returns_none_on_same_version(mock_requests_get, http_response):
    '''
    Ensure None is returned when the same version is found
    '''
    mock_requests_get.return_value.text = http_response('ha_releases')

    assert fetch_ha_releases('2024.8.3') is None
