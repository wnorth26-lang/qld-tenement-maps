from report_map_catalog import JURISDICTIONS, REPORT_TYPES, SOURCES, map_products


def test_every_state_and_report_has_a_sourced_product_set():
    for state in JURISDICTIONS:
        assert state in SOURCES and SOURCES[state].startswith("https://")
        for report_type in REPORT_TYPES:
            products = map_products(state, report_type)
            assert products and len({row["id"] for row in products}) == len(products)


def test_explicit_state_reporting_maps_are_present():
    assert "exploration_index" in {row["id"] for row in map_products("VIC", "annual")}
    assert "exploration_index" in {row["id"] for row in map_products("TAS", "annual")}
    nt = {row["id"] for row in map_products("NT", "annual")}
    assert {"drilling_samples", "geophysics_surveys"} <= nt
    assert "surrender_retained" in {row["id"] for row in map_products("WA", "partial_surrender")}


def test_only_implemented_qld_modes_are_marked_available():
    all_rows = [row for state in JURISDICTIONS for report in REPORT_TYPES
                for row in map_products(state, report)]
    assert any(row["render_status"] == "available" for row in all_rows)
    for state in JURISDICTIONS:
        if state != "QLD":
            assert all(row["render_status"] == "adapter_required"
                       for report in REPORT_TYPES for row in map_products(state, report))


if __name__ == "__main__":
    test_every_state_and_report_has_a_sourced_product_set()
    test_explicit_state_reporting_maps_are_present()
    test_only_implemented_qld_modes_are_marked_available()
    print("All report map catalogue tests passed.")
