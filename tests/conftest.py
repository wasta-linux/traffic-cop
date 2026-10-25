"""Run every test in English unless it asks for another language."""

from collections.abc import Iterator

import pytest

from trafficcop.common import i18n


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    i18n.setup(["en"])
    yield
    i18n.setup(["en"])
