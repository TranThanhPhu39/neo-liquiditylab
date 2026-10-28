import pytest

from src.data_providers.demo_provider import DemoProvider


@pytest.fixture
def book():
    return DemoProvider().get_snapshot("HPG")
