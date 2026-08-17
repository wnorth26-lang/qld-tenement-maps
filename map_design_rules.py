"""Deterministic, regulator-informed cartographic profiles for report maps."""

from __future__ import annotations

STANDARD_SCALES = (500, 1000, 2000, 5000, 10000, 25000, 50000, 100000, 250000)

PRODUCT_PROFILES = {
    "tenement_location": (50000, 250000, "A4", "landscape"),
    "exploration_index": (25000, 100000, "A4", "landscape"),
    "life_of_title_activity": (25000, 100000, "A4", "landscape"),
    "surrender_retained": (10000, 50000, "A4", "landscape"),
    "subblock_tenure": (10000, 50000, "A4", "landscape"),
    "geology": (10000, 50000, "A3", "landscape"),
    "drilling_samples": (2000, 25000, "A3", "landscape"),
    "geophysics_surveys": (5000, 50000, "A3", "landscape"),
    "disturbance_rehabilitation": (500, 10000, "A3", "landscape"),
    "environment_land": (5000, 50000, "A3", "landscape"),
    "proposed_work": (10000, 50000, "A4", "landscape"),
    "overlap_cadastre": (10000, 100000, "A3", "landscape"),
}

MANDATORY_ELEMENTS = (
    "figure number and descriptive title",
    "report type, title identifier and reporting period",
    "authoritative tenement boundary and appropriate location inset",
    "metric scale bar and representative fraction",
    "north arrow or section orientation",
    "labelled coordinate grid",
    "projection, datum and MGA zone where applicable",
    "clear ordered legend",
    "author/drafter, map date and data currency dates",
    "source, licence and attribution statement",
)


def design_profile(product_id: str, jurisdiction: str) -> dict:
    """Return a closed design recommendation; it is not a regulatory approval."""
    if product_id not in PRODUCT_PROFILES:
        raise ValueError(f"unknown map product: {product_id}")
    if jurisdiction not in {"QLD", "NSW", "VIC", "WA", "SA", "TAS", "NT"}:
        raise ValueError(f"unknown jurisdiction: {jurisdiction}")
    minimum, maximum, page_size, orientation = PRODUCT_PROFILES[product_id]
    return {
        "product_id": product_id,
        "jurisdiction": jurisdiction,
        "recommended_scale_range": [minimum, maximum],
        "standard_scale_choices": [s for s in STANDARD_SCALES if minimum <= s <= maximum],
        "page_size": page_size,
        "orientation": orientation,
        "output_dpi": 300,
        "extent_padding_percent": 12,
        "minimum_text_pt": 8,
        "minor_label_minimum_pt": 7,
        "black_and_white_safe": True,
        "colour_accessible": True,
        "mandatory_elements": list(MANDATORY_ELEMENTS),
        "selection_rule": (
            "Choose the largest standard scale denominator that fits the complete subject "
            "geometry plus 12% padding in the usable map frame; use an inset or larger page "
            "instead of clipping or shrinking labels below the minimum."
        ),
        "limitations": [
            "Scale is recommended from purpose and extent; the final reviewer must confirm readability.",
            "The title instrument and current regulator guidance control the submitted map set.",
            "Interpreted geology and professional conclusions require qualified review.",
        ],
    }
