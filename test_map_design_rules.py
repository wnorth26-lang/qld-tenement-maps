from map_design_rules import STANDARD_SCALES, design_profile


def test_every_product_profile_is_ordered_and_standard():
    products = (
        "tenement_location", "exploration_index", "life_of_title_activity",
        "surrender_retained", "subblock_tenure", "geology", "drilling_samples",
        "geophysics_surveys", "disturbance_rehabilitation", "environment_land",
        "proposed_work", "overlap_cadastre",
    )
    for product in products:
        profile = design_profile(product, "QLD")
        low, high = profile["recommended_scale_range"]
        assert low <= high
        assert profile["standard_scale_choices"]
        assert all(scale in STANDARD_SCALES for scale in profile["standard_scale_choices"])
        assert profile["output_dpi"] == 300
        assert len(profile["mandatory_elements"]) >= 10


def test_detail_and_context_profiles_are_materially_different():
    context = design_profile("tenement_location", "NSW")
    disturbance = design_profile("disturbance_rehabilitation", "NSW")
    assert context["recommended_scale_range"][1] > disturbance["recommended_scale_range"][1]
    assert context["page_size"] == "A4"
    assert disturbance["page_size"] == "A3"


def test_unknown_inputs_fail_closed():
    for product, state in (("unknown", "QLD"), ("geology", "XX")):
        try:
            design_profile(product, state)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid catalogue key was accepted")
