from __future__ import annotations

from pathlib import Path

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply the scenario marker to all tests in the scenario test directory."""
    scenario_test_directory = Path(__file__).parent
    for item in items:
        if scenario_test_directory in item.path.parents:
            item.add_marker(pytest.mark.scenario)