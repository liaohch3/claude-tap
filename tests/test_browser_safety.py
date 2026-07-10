"""Browser-safety contracts for the test environment."""

import os
import shutil

import pytest


def test_test_session_disables_external_browser() -> None:
    false_browser = shutil.which("false")
    if false_browser is None:
        pytest.skip("the platform does not provide a false command")

    assert os.environ["BROWSER"] == false_browser
