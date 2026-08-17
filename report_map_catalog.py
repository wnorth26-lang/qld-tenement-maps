"""Research-backed Australian statutory-report map catalogue.

The catalogue distinguishes regulator-explicit maps from conditional activity
maps and useful commercial context. It does not claim that every listed map is
mandatory for every report; the work performed and current title conditions
control the final set.
"""

from __future__ import annotations


JURISDICTIONS = {
    "QLD": "Queensland",
    "NSW": "New South Wales",
    "VIC": "Victoria",
    "WA": "Western Australia",
    "SA": "South Australia",
    "TAS": "Tasmania",
    "NT": "Northern Territory",
}

REPORT_TYPES = {
    "annual": "Annual technical / exploration report",
    "partial_surrender": "Partial relinquishment / surrender report",
    "final": "Final / complete surrender report",
    "renewal": "Renewal / work-program evidence pack",
    "activity_approval": "Activity approval / environmental work plan",
    "application": "Tenement application / project context",
    "due_diligence": "Due-diligence factual map pack",
}

PRODUCTS = {
    "tenement_location": {
        "label": "Tenement location and regional context",
        "purpose": "Shows the title boundary, regional location, access and nearby places.",
        "engine_mode": "locality",
    },
    "exploration_index": {
        "label": "Exploration activity index",
        "purpose": "Indexes the areas and types of work completed during the reporting period.",
    },
    "life_of_title_activity": {
        "label": "Life-of-title exploration activity",
        "purpose": "Shows all reportable exploration completed over the surrendered or ceased area.",
    },
    "surrender_retained": {
        "label": "Surrendered versus retained area",
        "purpose": "Clearly distinguishes land being relinquished from land retained.",
        "engine_mode": "partial_relinquishment",
    },
    "subblock_tenure": {
        "label": "Tenure sub-block plan",
        "purpose": "Shows the official tenure and sub-block framework used for QLD area reporting.",
        "engine_mode": "annual_subblock",
    },
    "geology": {
        "label": "Regional and prospect geology",
        "purpose": "Shows geology, structure, mineralisation and interpreted features with the title boundary.",
    },
    "drilling_samples": {
        "label": "Drilling and sample locations",
        "purpose": "Shows drill collars, traverses, sample sites and relevant sections/results.",
    },
    "geophysics_surveys": {
        "label": "Geophysical and remote-sensing surveys",
        "purpose": "Shows survey extents, lines, anomalies and interpreted products where undertaken.",
    },
    "disturbance_rehabilitation": {
        "label": "Disturbance and rehabilitation",
        "purpose": "Shows surface disturbance, access, waterways and rehabilitation status or proposals.",
    },
    "environment_land": {
        "label": "Environmental and land constraints",
        "purpose": "Indicative published constraints and specialist-evidence gaps for planning and approvals.",
    },
    "proposed_work": {
        "label": "Proposed work program",
        "purpose": "Shows proposed activity areas for renewal or forward work planning.",
    },
    "overlap_cadastre": {
        "label": "Tenure, land and overlap context",
        "purpose": "Shows neighbouring/overlapping tenure and available public land context.",
    },
}

COMMON = {
    "annual": ["tenement_location", "exploration_index", "geology", "drilling_samples",
               "geophysics_surveys", "disturbance_rehabilitation"],
    "partial_surrender": ["surrender_retained", "life_of_title_activity", "geology",
                          "drilling_samples", "disturbance_rehabilitation"],
    "final": ["tenement_location", "life_of_title_activity", "geology", "drilling_samples",
              "disturbance_rehabilitation"],
    "renewal": ["tenement_location", "exploration_index", "proposed_work"],
    "activity_approval": ["tenement_location", "environment_land", "disturbance_rehabilitation",
                          "drilling_samples"],
    "application": ["tenement_location", "overlap_cadastre", "environment_land"],
    "due_diligence": ["tenement_location", "overlap_cadastre", "life_of_title_activity",
                      "geology", "environment_land"],
}

STATE_OVERRIDES = {
    # QLD's existing renderer can presently generate these three controlled products.
    "QLD": {
        "annual": ["tenement_location", "subblock_tenure", "exploration_index", "geology",
                   "drilling_samples", "geophysics_surveys", "disturbance_rehabilitation"],
        "partial_surrender": ["surrender_retained", "life_of_title_activity", "geology",
                              "drilling_samples", "disturbance_rehabilitation"],
    },
    # Victoria explicitly calls for an exploration index map and geology map.
    "VIC": {"annual": ["exploration_index", "geology", "drilling_samples",
                       "geophysics_surveys", "disturbance_rehabilitation"]},
    # Tasmania explicitly calls for a topographic activity-summary map.
    "TAS": {"annual": ["exploration_index", "geology", "drilling_samples",
                       "disturbance_rehabilitation"]},
    # NT regulations expressly locate exploration, surveys, drilling and samples.
    "NT": {"annual": ["tenement_location", "exploration_index", "drilling_samples",
                      "geophysics_surveys", "geology"]},
}

SOURCES = {
    "QLD": "https://www.business.qld.gov.au/industries/mining-energy-water/resources/minerals-coal/reports-notices/surrender",
    "NSW": "https://www.resourcesregulator.nsw.gov.au/sites/default/files/2022-12/exploration-reporting-a-guide-for-reporting-on-exploration-and-prospecting-in-New-South-Wales.pdf",
    "VIC": "https://resources.vic.gov.au/legislation-and-regulations/guidelines-and-codes-of-practice/exploration-reporting-guidelines",
    "WA": "https://www.wa.gov.au/government/publications/guidelines-mineral-exploration-reports-mining-tenements",
    "SA": "https://www.energymining.sa.gov.au/industry/minerals-and-mining/exploration/exploration-reporting",
    "TAS": "https://www.mrt.tas.gov.au/forms_and_information/reporting_guidelinesreporting_guidelines",
    "NT": "https://nt.gov.au/industry/mining/applications-and-processes/mineral-title/complying-mineral-title/report-on-your-mineral-title",
}


def map_products(jurisdiction: str, report_type: str) -> list[dict]:
    keys = STATE_OVERRIDES.get(jurisdiction, {}).get(report_type, COMMON[report_type])
    output = []
    for key in keys:
        product = {"id": key, **PRODUCTS[key]}
        product["render_status"] = (
            "available" if jurisdiction == "QLD" and product.get("engine_mode") else "adapter_required"
        )
        output.append(product)
    return output
