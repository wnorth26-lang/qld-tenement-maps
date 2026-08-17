"""Production-boundary tests for versioned map specs and deterministic artifacts."""

from __future__ import annotations

from copy import deepcopy

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from map_service import generate_tenement_map
from map_spec import TenementMapSpec, tenement_map_json_schema


BASE_SPEC = {
    "schema_version": "1.0",
    "request_id": "12345678-1234-5678-1234-567812345678",
    "tenement": {"jurisdiction": "AU-QLD", "type": "EPM", "number": 25210, "source": "official_live"},
    "map_type": "locality",
    "basemap": "none",
    "context_layers": [],
    "relinquishment_subblocks": [],
    "include_standard_context": True,
    "layout": {"author": "AEL", "map_date": "2026-08-17"},
    "outputs": [{"format": "png", "dpi": 100}],
}


class Response:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


class MetadataSession:
    @staticmethod
    def get(url, params, timeout):
        return Response({"copyrightText": "Official source; CC BY 4.0", "editingInfo": {"lastEditDate": 1704067200000}})


class Engine:
    BASE = "https://official.example/permits"
    MINING_ADMIN_BASE = "https://official.example/grid"
    SUBBLOCK_LAYER = 3
    ROADS_BASE = "https://official.example/roads"
    ROADS_LAYER = 10
    WATER_BASE = "https://official.example/water"
    WATERCOURSE_ORDER_LAYER = 37
    IMAGERY_EXPORT_URL = "https://official.example/imagery/export"
    GREY_BASE_URL = "https://official.example/grey/export"
    GREY_REFERENCE_URL = "https://official.example/labels/export"
    LAYER_CATALOG = {}
    diagnostics = []

    @staticmethod
    def start_render_diagnostics(): return Engine.diagnostics, None

    @staticmethod
    def finish_render_diagnostics(token): pass

    @staticmethod
    def fetch_epm_gdf(label):
        return pd.DataFrame([{"displayname": label, "_status": "Granted"}])

    @staticmethod
    def build_map(gdf, author, scale, basemap, extra_layers, map_date):
        fig, ax = plt.subplots(figsize=(2, 2))
        ax.plot([0, 1], [0, 1], color="black")
        ax.set_title(f"{gdf.iloc[0]['displayname']} {map_date:%Y-%m-%d}")
        return fig, 100000, 55, "EPSG:7855", None


def test_closed_schema_and_conditionals():
    assert tenement_map_json_schema()["additionalProperties"] is False
    invalid = deepcopy(BASE_SPEC)
    invalid["invented_style"] = "watercolour"
    result = generate_tenement_map(invalid, metadata_session=MetadataSession, engine=Engine)
    assert result["result_state"] == "invalid_map_spec" and result["billable"] is False

    invalid = deepcopy(BASE_SPEC)
    invalid["map_type"] = "partial_relinquishment"
    result = generate_tenement_map(invalid, metadata_session=MetadataSession, engine=Engine)
    assert result["result_state"] == "invalid_map_spec"


def test_deterministic_artifact_manifest_and_provenance():
    first = generate_tenement_map(BASE_SPEC, metadata_session=MetadataSession, engine=Engine)
    second = generate_tenement_map(BASE_SPEC, metadata_session=MetadataSession, engine=Engine)
    assert first["result_state"] == "complete"
    assert first["manifest"]["spec_sha256"] == second["manifest"]["spec_sha256"]
    assert first["artifacts"][0]["sha256"] == second["artifacts"][0]["sha256"]
    assert first["manifest"]["datasets"][0]["dataset_date"].startswith("2024-01-01")
    assert "generative model" in first["manifest"]["determinism_boundary"]


def test_verified_zero_and_source_failure_are_distinct():
    class Missing(Engine):
        @staticmethod
        def fetch_epm_gdf(label): raise ValueError("not found")
    missing = generate_tenement_map(BASE_SPEC, metadata_session=MetadataSession, engine=Missing)
    assert missing["result_state"] == "tenement_not_found"
    assert missing["errors"][0]["code"] == "verified_zero"

    class BrokenMetadata:
        @staticmethod
        def get(url, params, timeout): raise RuntimeError("private detail")
    failed = generate_tenement_map(BASE_SPEC, metadata_session=BrokenMetadata, engine=Engine)
    assert failed["result_state"] == "source_failure"
    assert failed["billable"] is False
    assert "private detail" not in str(failed)

    Engine.diagnostics = [{"dataset_id": "surface_geology", "result_state": "source_failure",
                           "error": "RuntimeError"}]
    warned = generate_tenement_map(BASE_SPEC, metadata_session=MetadataSession, engine=Engine)
    Engine.diagnostics = []
    assert warned["result_state"] == "complete_with_warnings"
    assert warned["manifest"]["render_layer_results"][0]["dataset_id"] == "surface_geology"


if __name__ == "__main__":
    test_closed_schema_and_conditionals()
    test_deterministic_artifact_manifest_and_provenance()
    test_verified_zero_and_source_failure_are_distinct()
    print("All map service tests passed.")
