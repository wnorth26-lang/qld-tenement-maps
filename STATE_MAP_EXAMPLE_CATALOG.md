# Australian statutory-report map examples and design model

Research reviewed 17 August 2026. These 35 examples are map patterns taken
from current official reporting guidance and public report systems. They are
design references, not a claim that every map is mandatory for every title.
Activities undertaken, title conditions and current regulator instructions
remain controlling.

## Queensland — five examples

| Report/workflow | Map example | Model in Map Studio |
|---|---|---|
| Activity report | Resource-authority location and regional context | `tenement_location` |
| Activity report | Reporting-period exploration activity index | `exploration_index` |
| Activity report | Geological mapping, structure and mineralisation | `geology` |
| Activity/geophysical report | Survey extent, flight lines and interpreted anomalies | `geophysics_surveys` |
| Partial/final surrender | Relinquished versus retained sub-blocks plus life-of-title work | `surrender_retained` + `life_of_title_activity` |

Official basis: Practice Direction 5 requires the technical summary and maps;
Queensland also requires digital assay, drilling, geochemical, geophysical and
remote-sensing data where applicable. GeoResGlobe and the GSQ collection supply
official context at regional scales including 1:50,000, 1:100,000 and
1:250,000. Representative open-file validation set: EPM 12345 (16 indexed
reports: 13 annual and three final). Do not infer that every indexed attachment
has unrestricted redistribution rights.

## New South Wales — five examples

| Report/workflow | Map example | Model in Map Studio |
|---|---|---|
| Proposed exploration | Proposed activities, authority boundary, towns and infrastructure | `proposed_work` |
| Annual exploration | Reporting-period exploration activity and access | `exploration_index` |
| Technical report | Geology, structures and mineralisation with title boundary | `geology` |
| Technical report | Drill collars, traverses and sample locations | `drilling_samples` |
| Partial/final report | Relinquished area and clear life-of-authority exploration coverage | `surrender_retained` + `life_of_title_activity` |

Official basis: the NSW guide says reports must contain the maps, plans and
data necessary to interpret and evaluate them. Its proposed-work and partial
relinquishment examples expressly include the authority boundary, towns/major
infrastructure, scale and north arrow. Public DIGS validation used EL 7959 and
found a large historical report set; the product must deduplicate and disclose
limits rather than place every historical layer on one unreadable map.

## Victoria — five examples

| Report/workflow | Map example | Model in Map Studio |
|---|---|---|
| Annual/technical | Exploration index map | `exploration_index` |
| Annual/technical | Geological map with licence boundary | `geology` |
| Geochemistry | Sample-location and traverse map | `drilling_samples` |
| Drilling | Collar plan and related cross-section orientation | `drilling_samples` |
| Rehabilitation | Topographic disturbance and rehabilitation plan | `disturbance_rehabilitation` |

Official basis: Resources Victoria supplies the strongest explicit national
cartographic checklist: GDA94/GDA2020 and MGA; metric scale bar; labelled grid;
datum/projection; north/orientation; legend; licence/boundary/inset;
author/sources/date; black-and-white-safe output; and the standard scale ladder
1:500 through 1:250,000. Representative GSV validation: EL006176 returned nine
report references and 36 indexed files; G172562 was used as a readable deep
analysis example.

## Western Australia — five examples

| Report/workflow | Map example | Model in Map Studio |
|---|---|---|
| Annual report | Tenement location and access | `tenement_location` |
| Annual report | Geological mapping and prospects | `geology` |
| Annual report | Drilling and sample locations | `drilling_samples` |
| Annual report | Geophysical survey coverage | `geophysics_surveys` |
| Surrender report | Surrendered versus retained land and life-of-title work | `surrender_retained` + `life_of_title_activity` |

Official basis: the 2025 guideline requires GDA2020, coordinate type,
projection and zone; maps use a metric scale bar and are retained at original
scale, with 300 dpi used for report graphics. Representative WAMEX validation:
E39/1454 returned 24 exact-intersecting report footprints including A5381,
A36043, A36044, A37028 and A45468. WAMEX is link/metadata only in the commercial
product until document-specific reuse rights are established.

## South Australia — five examples

| Report/workflow | Map example | Model in Map Studio |
|---|---|---|
| Annual activity summary | Tenement location and reporting-period activity | `tenement_location` + `exploration_index` |
| Technical report | Geological mapping and interpreted structure | `geology` |
| Technical report | Drill collars and samples | `drilling_samples` |
| Technical report | Geophysical survey footprint | `geophysics_surveys` |
| Partial surrender | Surrendered/retained area and work completed | `surrender_retained` + `life_of_title_activity` |

Official basis: MG13 covers annual activity summaries, technical reports and
partial surrender reports and points to the national digital exploration-data
standard. The exact map set remains activity-dependent until each MG13 rule is
encoded. Representative SARIG validation: ML 4830 returned four exact report
records; mesac29160 was readable while mesac28322 was scan-only, an important
reason to show `source_failure` rather than a false zero.

## Tasmania — five examples

| Report/workflow | Map example | Model in Map Studio |
|---|---|---|
| Annual report | Topographic activity-summary map | `exploration_index` |
| Annual report | Survey and drilling locations | `drilling_samples` |
| Annual report | Geology and mineralisation | `geology` |
| Partial report | Surrendered versus retained area | `surrender_retained` |
| Final report | Life-of-licence work over the ceased area | `life_of_title_activity` |

Official basis: MRT’s August 2025 guidance asks the annual summary map to show
the licence/report area, location and type of surveys and topographic features;
partial/final reporting distinguishes surrendered and retained ground and the
life of the licence. EL19/2001 returned five indexed report references,
including 18_8053 and historical-overlap record 15_7110. MRT attachment pages
were Cloudflare-protected during validation; the product links to them and does
not bypass that control.

## Northern Territory — five examples

| Report/workflow | Map example | Model in Map Studio |
|---|---|---|
| Annual report | Title location and regional context | `tenement_location` |
| Annual report | Exploration locations relative to title boundaries | `exploration_index` |
| Survey report | Survey boundaries and spatial-data coverage | `geophysics_surveys` |
| Drilling report | Drill-hole locations | `drilling_samples` |
| Sampling report | Geological sample recovery locations and geology | `drilling_samples` + `geology` |

Official basis: NT mineral-title reporting rules expressly call for maps that
locate exploration, survey boundaries, drilling and geological sample recovery
relative to title boundaries. EL33088 / CR2025-0181 was used as the public
report-chain validation example. NT remains held/nonbillable because report
reuse and authoritative historical-polygon access require resolution; examples
are metadata/design evidence, not permission to republish documents.

## The ideal map model

1. Fit the complete subject plus 12% padding to a standard scale; never clip it
   or shrink labels to force a preferred scale.
2. Use the Victoria standard metric ladder: 1:500, 1:1,000, 1:2,000, 1:5,000,
   1:10,000, 1:25,000, 1:50,000, 1:100,000 or 1:250,000.
3. Use A4 landscape for simple location/index maps and A3 landscape for dense
   geology, drill/sample, environmental and disturbance maps.
4. Export report graphics at 300 dpi; preserve vector text and linework in PDF.
5. Keep body text at least 8 pt and minor labels at least 7 pt at final size.
6. Use colourblind-safe symbols that remain distinguishable in black and white.
7. Include title/figure number, reporting period, title boundary/inset, scale
   bar, north, grid, CRS/datum/zone, legend, author/date and source/licence.
8. Separate government facts, client-supplied observations and geological
   interpretation in the legend and manifest.
9. If the legend obscures data, move it to a second page rather than reducing
   the map frame or type below the minimum.
10. A verified zero, unavailable source and unimplemented adapter are three
    different results and must never be displayed as the same blank map.

## Primary official sources

- [Queensland activity reporting and Practice Direction 5](https://www.business.qld.gov.au/industries/mining-energy-water/resources/minerals-coal/reports-notices/activity-reporting)
- [Queensland maps and spatial data](https://www.business.qld.gov.au/industries/mining-energy-water/resources/geoscience-information/maps-datasets/maps-data)
- [NSW exploration reporting guide](https://www.resourcesregulator.nsw.gov.au/sites/default/files/2022-12/exploration-reporting-a-guide-for-reporting-on-exploration-and-prospecting-in-New-South-Wales.pdf)
- [Victoria exploration reporting guidance](https://resources.vic.gov.au/legislation-and-regulations/guidelines-and-codes-of-practice/exploration-reporting-guidelines)
- [WA mineral exploration reporting guidelines](https://www.wa.gov.au/government/publications/guidelines-mineral-exploration-reports-mining-tenements)
- [SA exploration reporting and MG13](https://www.energymining.sa.gov.au/industry/minerals-and-mining/exploration/exploration-reporting)
- [Tasmania reporting guidelines](https://mrt.tas.gov.au/__data/assets/pdf_file/0008/598445/MRT-Reporting-Guidelines-August-2025.pdf)
- [NT mineral-title reporting](https://nt.gov.au/industry/mining/applications-and-processes/mineral-title/complying-mineral-title/report-on-your-mineral-title)
