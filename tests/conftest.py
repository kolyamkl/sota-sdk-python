"""Shared fixtures for SOTA SDK tests."""
import pytest


@pytest.fixture
def api_key():
    """Test API key."""
    return "test-api-key-12345"


@pytest.fixture
def base_url():
    """Test base URL."""
    return "https://test.sota.app"
