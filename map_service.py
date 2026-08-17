"""Deterministic orchestration boundary for AEL Map Studio."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import requests
from matplotlib.backends.backend_pdf import PdfPages
from pydantic import ValidationError

import epm_locality_map as renderer
from map_spec import TenementMapSpec


RENDERER_VERSION = "ael-qld-map-renderer/1.0"
RENDER_DEPENDENCIES = ("geopandas", "shapely", "matplotlib", "numpy", "pandas", "pyproj")


def _canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode("utf-8")


def _source_endpoints(spec: TenementMapSpec):
    endpoints = [
        {"dataset_id": "qld_mines_permits_granted", "url": f"{renderer.BASE}/3"},
        {"dataset_id": "qld_mines_permits_applications", "url": f"{renderer.BASE}/2"},
    ]
    if spec.map_type != "locality":
        endpoints.append({"dataset_id": "qld_mining_subblocks", "url": f"{renderer.MINING_ADMIN_BASE}/{renderer.SUBBLOCK_LAYER}"})
        if spec.include_standard_context:
            endpoints.extend([
                {"dataset_id": "qld_roads", "url": f"{renderer.ROADS_BASE}/{renderer.ROADS_LAYER}"},
                {"dataset_id": "qld_watercourses", "url": f"{renderer.WATER_BASE}/{renderer.WATERCOURSE_ORDER_LAYER}"},
            ])
    for key in spec.context_layers:
        layer = renderer.LAYER_CATALOG[key]
        endpoints.append({"dataset_id": key, "url": f"{layer['url']}/{layer['layer_id']}"})
    if spec.basemap == "satellite":
        endpoints.append({"dataset_id": "esri_world_imagery", "url": renderer.IMAGERY_EXPORT_URL.rsplit("/export", 1)[0]})
    elif spec.basemap == "greyscale":
        endpoints.extend([
            {"dataset_id": "esri_light_gray_base", "url": renderer.GREY_BASE_URL.rsplit("/export", 1)[0]},
            {"dataset_id": "esri_light_gray_reference", "url": renderer.GREY_REFERENCE_URL.rsplit("/export", 1)[0]},
        ])
    return endpoints


def _source_metadata(endpoint, session=requests):
    response = session.get(endpoint["url"], params={"f": "json"}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError("source metadata endpoint returned an error")
    milliseconds = (payload.get("editingInfo") or {}).get("lastEditDate")
    dataset_date = (datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()
                    if isinstance(milliseconds, (int, float)) else None)
    return {**endpoint, "status": "verified", "dataset_date": dataset_date,
            "copyright_text": payload.get("copyrightText") or None,
            "service_description": payload.get("description") or payload.get("serviceDescription") or None}


def _render_artifact(figures, output):
    buffer = io.BytesIO()
    if output.format == "pdf":
        metadata = {"Creator": RENDERER_VERSION, "CreationDate": None, "ModDate": None}
        with PdfPages(buffer, metadata=metadata) as pdf:
            for figure in figures:
                pdf.savefig(figure, dpi=output.dpi)
        mime = "application/pdf"
    else:
        # PNG is deliberately the first/map page. Overflow legends remain available in PDF.
        figures[0].savefig(buffer, format="png", dpi=output.dpi,
                           metadata={"Software": RENDERER_VERSION})
        mime = "image/png"
    payload = buffer.getvalue()
    return {"format": output.format, "mime_type": mime, "dpi": output.dpi,
            "bytes": payload, "sha256": hashlib.sha256(payload).hexdigest(),
            "page_count": len(figures) if output.format == "pdf" else 1}


def generate_tenement_map(map_spec, *, metadata_session=requests, engine=renderer) -> dict:
    """Validate, resolve official tenure, render, and return artifacts plus evidence manifest.

    Natural language is intentionally not accepted. Callers must translate requests into the
    closed schema before invoking this deterministic boundary.
    """
    try:
        spec = map_spec if isinstance(map_spec, TenementMapSpec) else TenementMapSpec.model_validate(map_spec)
    except ValidationError as exc:
        return {"result_state": "invalid_map_spec", "billable": False,
                "errors": [{"stage": "validation", "details": exc.errors(include_url=False)}],
                "manifest": None, "artifacts": [], "provenance": [], "warnings": []}

    provenance, warnings = [], []
    required_failures = []
    for endpoint in _source_endpoints(spec):
        try:
            provenance.append(_source_metadata(endpoint, metadata_session))
        except Exception as exc:
            row = {**endpoint, "status": "source_failure", "error": type(exc).__name__,
                   "dataset_date": None, "copyright_text": None}
            provenance.append(row)
            if endpoint["dataset_id"].startswith("qld_mines_permits") or endpoint["dataset_id"] == "qld_mining_subblocks":
                required_failures.append(row)
            else:
                warnings.append({"code": "optional_source_metadata_unavailable", "dataset_id": endpoint["dataset_id"]})
    if required_failures:
        return {"result_state": "source_failure", "billable": False, "manifest": None,
                "artifacts": [], "provenance": provenance, "warnings": warnings,
                "errors": [{"stage": "required_source_preflight", "dataset_id": row["dataset_id"]}
                           for row in required_failures]}

    try:
        gdf = engine.fetch_epm_gdf(f"EPM {spec.tenement.number}")
    except ValueError:
        return {"result_state": "tenement_not_found", "billable": False, "manifest": None,
                "artifacts": [], "provenance": provenance, "warnings": warnings,
                "errors": [{"stage": "tenement_resolution", "code": "verified_zero"}]}
    except Exception as exc:
        return {"result_state": "source_failure", "billable": False, "manifest": None,
                "artifacts": [], "provenance": provenance, "warnings": warnings,
                "errors": [{"stage": "tenement_resolution", "error": type(exc).__name__}]}

    figures = []
    render_diagnostics, diagnostics_token = engine.start_render_diagnostics()
    try:
        date_value = datetime.combine(spec.layout.map_date, datetime.min.time())
        if spec.map_type == "locality":
            figure, scale, zone, crs, legend = engine.build_map(
                gdf, spec.layout.author, spec.layout.scale_denominator, spec.basemap,
                extra_layers=spec.context_layers, map_date=date_value)
            figures = [figure] + ([legend] if legend is not None else [])
            subblock_count = relinquished_count = None
        else:
            rendered, subblocks, zone = engine.build_subblock_maps(
                gdf, spec.layout.author,
                relinquish_codes=spec.relinquishment_subblocks or None,
                forced_scale=spec.layout.scale_denominator,
                project_name=spec.layout.project_name, drawn_by=spec.layout.drawn_by,
                report_title=spec.layout.report_title, page_number=spec.layout.page_number,
                company_name=spec.layout.company_name, context_layers=spec.include_standard_context,
                basemap=spec.basemap, extra_layers=spec.context_layers, map_date=date_value)
            figures = [rendered["map"]] + ([rendered.get("legend")] if rendered.get("legend") is not None else [])
            scale = None
            crs = f"EPSG:{7850 + zone - 50}"
            subblock_count = len(subblocks)
            relinquished_count = int(subblocks["relinquished"].sum())
            if spec.map_type == "partial_relinquishment" and relinquished_count != len(spec.relinquishment_subblocks):
                warnings.append({"code": "not_all_requested_subblocks_resolved",
                                 "requested": len(spec.relinquishment_subblocks), "resolved": relinquished_count})
        artifacts = [_render_artifact(figures, output) for output in spec.outputs]
    except Exception as exc:
        return {"result_state": "render_failure", "billable": False, "manifest": None,
                "artifacts": [], "provenance": provenance, "warnings": warnings,
                "errors": [{"stage": "render", "error": type(exc).__name__}]}
    finally:
        engine.finish_render_diagnostics(diagnostics_token)
        for figure in figures:
            plt.close(figure)

    for diagnostic in render_diagnostics:
        if diagnostic["result_state"] == "source_failure":
            warnings.append({"code": "render_source_failure", "dataset_id": diagnostic["dataset_id"]})

    dumped = spec.model_dump(mode="json")
    spec_hash = hashlib.sha256(_canonical_json(dumped)).hexdigest()
    artifact_manifest = [{key: value for key, value in artifact.items() if key != "bytes"}
                         for artifact in artifacts]
    attrs = gdf.iloc[0]
    manifest = {
        "schema_version": spec.schema_version, "renderer_version": RENDERER_VERSION,
        "renderer_dependencies": {name: importlib.metadata.version(name) for name in RENDER_DEPENDENCIES},
        "request_id": str(spec.request_id), "spec_sha256": spec_hash,
        "tenement_resolution": {"requested": f"EPM {spec.tenement.number}",
                                "official_label": attrs.get("displayname"),
                                "status": attrs.get("_status") or attrs.get("permitstatus"),
                                "source": "Queensland Government MinesPermitsCurrent"},
        "map": {"map_type": spec.map_type, "basemap": spec.basemap, "scale_denominator": scale,
                "zone": zone, "crs": crs, "subblock_count": subblock_count,
                "relinquished_subblock_count": relinquished_count},
        "datasets": provenance, "render_layer_results": render_diagnostics,
        "warnings": warnings, "artifacts": artifact_manifest,
        "determinism_boundary": (
            "The renderer uses only controlled map-spec settings and official source responses. "
            "No generative model creates or alters map imagery."
        ),
    }
    return {"result_state": "complete_with_warnings" if warnings else "complete", "billable": True,
            "manifest": manifest, "artifacts": artifacts, "provenance": provenance,
            "warnings": warnings, "errors": []}
