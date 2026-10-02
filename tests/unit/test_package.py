from importlib.metadata import version

import pricewright


def test_package_version_matches_distribution_metadata() -> None:
    assert pricewright.__version__ == version("pricewright-api")
