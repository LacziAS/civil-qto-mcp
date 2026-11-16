"""Pytest shared fixtures for civil-qto-mcp."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.factories.dxf_factory import (
    create_eov_parking_lot_dxf,
    create_utility_network_dxf,
)
from tests.factories.xlsx_factory import create_boq_with_traps_xlsx


@pytest.fixture(scope="session")
def eov_parking_dxf_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped synthetic Hungarian EOV parking lot DXF fixture."""
    tmp_dir = tmp_path_factory.mktemp("dxf_fixtures")
    return create_eov_parking_lot_dxf(tmp_dir / "synthetic_eov_parking_lot.dxf")


@pytest.fixture(scope="session")
def utility_network_dxf_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped synthetic utility network DXF fixture."""
    tmp_dir = tmp_path_factory.mktemp("dxf_utility_fixtures")
    return create_utility_network_dxf(tmp_dir / "synthetic_utility_network.dxf")


@pytest.fixture(scope="session")
def boq_traps_xlsx_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped synthetic Excel BoQ fixture with hidden sheets and formula traps."""
    tmp_dir = tmp_path_factory.mktemp("xlsx_fixtures")
    return create_boq_with_traps_xlsx(tmp_dir / "synthetic_boq_traps.xlsx")

