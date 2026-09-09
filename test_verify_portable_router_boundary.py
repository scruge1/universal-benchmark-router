from __future__ import annotations

from pathlib import Path

import pytest

from verify_portable_router_boundary import BoundaryError, verify_sources


ROOT = Path(__file__).resolve().parent


def test_current_portable_sources_have_no_local_adapter_dependency() -> None:
    result = verify_sources(
        [ROOT / "universal_model_router.py", ROOT / "universal_benchmark_exchange.py"]
    )
    assert result["result"] == "verified"
    assert result["dashboard_is_consumer_only"] is True
    assert result["local_actuation_authority"] is False


def test_local_adapter_import_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "changed.py"
    source.write_text("import model_control\n", encoding="utf-8")
    with pytest.raises(BoundaryError, match="forbidden imports"):
        verify_sources([source])


def test_local_host_marker_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "changed.py"
    source.write_text('ROOT = "/opt/rig-dashboard"\n', encoding="utf-8")
    with pytest.raises(BoundaryError, match="local host markers"):
        verify_sources([source])
