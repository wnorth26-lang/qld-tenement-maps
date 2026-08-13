#!/usr/bin/env python3
"""
epm_locality_map.py

Automates the standard QLD exploration permit (EPM) location map workflow:

    GeoResGlobe search -> download shapefile -> QGIS import -> layout manager ->
    set CRS/MGA zone -> add grid -> zoom to tenement -> legend/scale bar/north
    arrow -> title block (author, date, CRS)

Instead of the manual GeoResGlobe download step, this script queries the
Queensland Government's public "MinesPermitsCurrent" ArcGIS REST service
directly by EPM number, so no manual search/download/unzip is needed. It then
builds a print-ready layout (grid, legend, north arrow, scale bar, title
block) and exports a PDF, matching the layout you'd normally build by hand in
QGIS's Layout Manager.

Usage:
    python epm_locality_map.py --epm "EPM 25210" --author "Will North"
    python epm_locality_map.py --epm 25210 --scale 100000 --output my_map.pdf
    python epm_locality_map.py --input local_permit.geojson --author "Will North"

Requires: requests, geopandas, shapely, matplotlib, pyproj
    pip install requests geopandas shapely matplotlib pyproj
"""

import argparse
import io
import math
import re
import textwrap
from datetime import datetime

import geopandas as gpd
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrow
from matplotlib.patches import Polygon as MplPolygon
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon

# ---------------------------------------------------------------------------
# Queensland Government mines permits REST service (public, no auth required)
# ---------------------------------------------------------------------------
BASE = "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/Economy/MinesPermitsCurrent/MapServer"

# QLD administrative boundaries service, used to name nearby towns/localities
ADMIN_BASE = "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/Boundaries/AdministrativeBoundaries/MapServer"
LOCALITY_LAYER = 2

# QLD mining administrative areas service - the official graticular block/sub-block grid
# used to describe exploration tenures (1 arcmin lat x 1 arcmin lon per sub-block)
MINING_ADMIN_BASE = "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/Boundaries/MiningAdministrativeAreas/MapServer"
SUBBLOCK_LAYER = 3

# Esri World Imagery (public, no auth) - supports export directly in any EPSG, incl. GDA2020 MGA
IMAGERY_EXPORT_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"

# Esri light gray canvas (base + label/reference overlay) - muted grey basemap showing towns,
# roads etc. without satellite imagery's strong colour
GREY_BASE_URL = "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/export"
GREY_REFERENCE_URL = "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/export"

# QLD roads and watercourses, used as light reference context on report-style sub-block maps
ROADS_BASE = "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/Transportation/RoadsAndTracks/MapServer"
ROADS_LAYER = 10
WATER_BASE = "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/InlandWaters/WaterCoursesAndBodies/MapServer"
WATERCOURSE_ORDER_LAYER = 37

# EPM layers: 3 = granted, 2 = application (checked in this order)
EPM_LAYERS = [
    (3, "Granted"),
    (2, "Application"),
]

GDA2020_GEOGRAPHIC = "EPSG:7844"

# MGA zone EPSG codes (GDA2020), QLD spans zones 54-56
MGA_ZONES = {
    54: "EPSG:7854",
    55: "EPSG:7855",
    56: "EPSG:7856",
}


def normalise_epm(epm_input: str) -> str:
    """Turn '25210', 'EPM25210', 'epm 25210' into the service's 'EPM 25210' format."""
    digits = "".join(ch for ch in epm_input if ch.isdigit())
    if not digits:
        raise ValueError(f"Could not find a permit number in '{epm_input}'")
    return f"EPM {digits}"


def _ring_signed_area(ring):
    """Shoelace formula. Negative = clockwise, positive = counter-clockwise (lon/lat order)."""
    area = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        area += (x1 * y2 - x2 * y1)
    return area / 2.0


def esri_rings_to_geometry(rings):
    """Convert Esri JSON polygon rings into a proper shapely Polygon/MultiPolygon.

    Esri permits (like EPMs made of several non-contiguous sub-block groups) can have
    multiple separate parts in one feature. Esri's convention is: clockwise rings are
    outer shells (new parts), counter-clockwise rings are holes in the preceding shell.
    Naively passing all rings straight into a single GeoJSON Polygon treats every ring
    after the first as a hole - so any additional disjoint part silently vanishes from
    the rendered map even though it's still inside the bounding box (looks like the
    tenement got "cut off", when really a whole block just isn't being drawn).
    """
    parts = []  # list of [shell, [holes...]]
    for ring in rings:
        if _ring_signed_area(ring) < 0:  # clockwise -> new outer shell
            parts.append([ring, []])
        elif parts:  # counter-clockwise -> hole in the most recent shell
            parts[-1][1].append(ring)
        else:  # malformed: hole with no preceding shell, treat as its own shell
            parts.append([ring, []])

    polygons = [Polygon(shell, holes) for shell, holes in parts]
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def esri_paths_to_geometry(paths):
    """Convert Esri JSON polyline paths into a shapely LineString/MultiLineString."""
    lines = [LineString(p) for p in paths if len(p) >= 2]
    if not lines:
        return None
    return lines[0] if len(lines) == 1 else MultiLineString(lines)


def _fetch_lines(url, params, empty_cols):
    """Shared helper for the optional context layers (roads, watercourses): never raises -
    a failed fetch just means that layer is left off the map."""
    params = dict(params)
    crs = params.pop("_crs", None)
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        records = []
        for feat in data.get("features", []):
            geom = esri_paths_to_geometry(feat["geometry"]["paths"])
            if geom is None:
                continue
            rec = dict(feat["attributes"])
            rec["geometry"] = geom
            records.append(rec)
        if not records:
            return gpd.GeoDataFrame(columns=empty_cols + ["geometry"], geometry="geometry", crs=crs)
        return gpd.GeoDataFrame(records, geometry="geometry", crs=crs)
    except Exception as exc:  # noqa: BLE001 - these are optional decoration layers
        print(f"Warning: could not fetch context layer ({exc}).")
        return gpd.GeoDataFrame(columns=empty_cols + ["geometry"], geometry="geometry", crs=crs)


def fetch_roads(gdf_proj: gpd.GeoDataFrame, target_crs: str, state_controlled_only=True) -> gpd.GeoDataFrame:
    """State-controlled roads near the tenement, for cartographic reference context."""
    minx, miny, maxx, maxy = gdf_proj.total_bounds
    pad = max(maxx - minx, maxy - miny) * 0.3
    sr = epsg_number(target_crs)
    where = "scr_indicator='Y'" if state_controlled_only else "1=1"
    params = {
        "geometry": f"{minx - pad},{miny - pad},{maxx + pad},{maxy + pad}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": sr,
        "outFields": "road_name,road_type,scr_indicator",
        "outSR": sr,
        "where": where,
        "f": "json",
        "_crs": target_crs,
    }
    gdf = _fetch_lines(f"{ROADS_BASE}/{ROADS_LAYER}/query", params, ["road_name", "road_type"])
    return gdf


def fetch_watercourses(gdf_proj: gpd.GeoDataFrame, target_crs: str, min_order: int = 4) -> gpd.GeoDataFrame:
    """Major watercourses (stream order >= min_order) near the tenement."""
    minx, miny, maxx, maxy = gdf_proj.total_bounds
    pad = max(maxx - minx, maxy - miny) * 0.3
    sr = epsg_number(target_crs)
    params = {
        "geometry": f"{minx - pad},{miny - pad},{maxx + pad},{maxy + pad}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": sr,
        "outFields": "name,stream_order",
        "outSR": sr,
        "where": f"stream_order>={min_order}",
        "f": "json",
        "_crs": target_crs,
    }
    gdf = _fetch_lines(f"{WATER_BASE}/{WATERCOURSE_ORDER_LAYER}/query", params, ["name", "stream_order"])
    return gdf


# ---------------------------------------------------------------------------
# Generic "extra context layer" catalog. Each entry points at one public QLD
# spatial-gis ArcGIS layer and how to draw it once fetched. This is the same
# public services catalog GeoResGlobe itself is built on - a small, curated
# subset of it (there are dozens of folders and hundreds of layers; most are
# either irrelevant to a tenement report, at the wrong scale to be useful, or
# gated behind an API token this script doesn't have - e.g. Native Title /
# Cultural Heritage and National Parks / State Forest layers, both under
# folders that returned "Token Required" when checked). Add more entries here
# in the same shape to extend the picker - no other code needs to change.
# ---------------------------------------------------------------------------

# Standard international chronostratigraphic colour scheme (the same family of colours used
# on most published geological maps worldwide, including GSQ's own), ordered oldest to
# youngest. QLD's "age" attribute is a freeform string (e.g. "LATE CARBONIFEROUS - EARLY
# PERMIAN"), so rather than requiring an exact match, each entry's colour is assigned by the
# first period/era keyword found in that string - good enough to make the map legible and
# group related units sensibly, without needing GSQ's internal colour table (which isn't
# exposed on the public REST service in a form worth trying to scrape).
GEOLOGY_AGE_COLORS = [
    ("ARCHAEAN", "#F0047F"), ("ARCHEAN", "#F0047F"),
    ("PROTEROZOIC", "#F74370"),
    ("CAMBRIAN", "#7FA056"),
    ("ORDOVICIAN", "#009270"),
    ("SILURIAN", "#B3E1B6"),
    ("DEVONIAN", "#CB8C37"),
    ("CARBONIFEROUS", "#67A599"),
    ("PERMIAN", "#F04028"),
    ("TRIASSIC", "#812B92"),
    ("JURASSIC", "#34B2C9"),
    ("CRETACEOUS", "#7FC64E"),
    ("PALEOGENE", "#FD9A52"),
    ("NEOGENE", "#FFE619"),
    ("TERTIARY", "#FD9A52"),
    ("QUATERNARY", "#F9F97F"),
]
GEOLOGY_DEFAULT_COLOR = "#cfcfcf"


def geology_age_color(age_value):
    """Map a raw QLD geology 'age' attribute (e.g. 'LATE PERMIAN', 'LATE CARBONIFEROUS -
    EARLY PERMIAN') to (hex_color, short_legend_label). Falls back to a neutral grey with the
    raw value as its label if nothing recognisable is found, so nothing silently vanishes."""
    if not age_value:
        return GEOLOGY_DEFAULT_COLOR, "Unknown / unclassified age"
    up = str(age_value).upper()
    for keyword, color in GEOLOGY_AGE_COLORS:
        if keyword in up:
            return color, keyword.title()
    return GEOLOGY_DEFAULT_COLOR, str(age_value).title()


# Real official fill colours for the QLD "State Surface Geology" 1:2,000,000 scale dataset -
# the same layer GeoResGlobe itself displays under "Surface Geology" (statewide compilation,
# GeoscientificInformation/GeologyState/MapServer, layer 6). Scraped directly from that
# service's own uniqueValue renderer (keyed on the "ru_name" rock-unit-name field), i.e. these
# are GSQ's actual map colours, not an approximation.
#
# The live renderer covers 300+ rock units and the government service's response is too large
# to retrieve in one request in this environment, so this table has confirmed exact colours for
# the Quaternary through Carboniferous portion of the legend (the units that make up most of
# Queensland's sedimentary basins/cover - Bowen, Galilee, Surat, Eromanga etc). Anything not in
# this table (mostly older Permian-and-below basement units, e.g. around Mount Isa) falls back
# to geology_age_color()'s keyword match on the rock unit's name, then to
# geology_symbol_prefix_color()'s match on its map_symbol code prefix, then to a neutral grey -
# see geology_state_color() below, which chains all of this together.
GEOLOGY_STATE_RU_COLORS = {
    "Airlie Volcanics": "#CCE8FF",
    "Albany Pass beds, Helby beds": "#80FFAB",
    "Allaru Mudstone": "#FFFFFF",
    "Alton Downs Basalt, unnamed basalt": "#D4FFAD",
    "Auburn Subprovince - Carboniferous to Early Permian granitoids": "#FF998A",
    "Back Creek Group": "#80C7FF",
    "Bellthorpe Andesite, Brookfield Volcanics, Gilla Volcanics, QG-unnamed volcanics": "#78F7D1",
    "Berserker Group, Double Mountain Volcanics, Peninsula Range Volcanics": "#FFFFFF",
    "Betts Creek beds": "#FFFFFF",
    "Blackwater Group": "#99D4FF",
    "Blantyre Sandstone, Eulo Queen Group": "#D9FFE6",
    "Brooweena Formation, Keefton Formation, Kin Kin beds, Traveston Formation": "#42F5BF",
    "Bulgonunna Volcanic Group": "#9CABAB",
    "Bundamba Group (including Marburg Subgroup and Woogaroo Subgroup), Landsborough Sandstone": "#80FFAB",
    "Bungil Formation, Gubberamunda Sandstone, Hooray Sandstone, Kumbarilla beds, Longsight Sandstone, Mooga Sandstone, Orallo Formation, Southlands Formation": "#91FF36",
    "Burrum Coal Measures": "#F5FFEB",
    "Calen Coal Measures": "#80C7FF",
    "Camboon Volcanics": "#CCE8FF",
    "Carmila beds": "#FFFFFF",
    "Clarke River Group": "#FFFFFF",
    "Clematis Group": "#5CF5C7",
    "Connors Subprovince - Carboniferous-Cretaceous intrusives": "#FF33FF",
    "Connors Subprovince - Early Permian intrusives": "#FF66FF",
    "Connors Subprovince - Late Carboniferous intrusives": "#FF735C",
    "Connors Subprovince - Late Carboniferous-Early Permian intrusives": "#FFB3FF",
    "Connors Volcanic Group": "#D9DEDE",
    "Cretaceous intrusives - central Queensland": "#FF9EBD",
    "D'Aguilar Subprovince - Carboniferous plutonic rocks": "#FFD6D1",
    "Dinner Creek Conglomerate": "#CCE8FF",
    "Dunda beds": "#FFFFFF",
    "Dundowran Basalt, Gin Gin Basalt, Main Range Volcanics, Minerva Hills Volcanics, Mount Runsome Basalt, Peak Range Volcanics, Waddy Point Volcanics, unnamed basalt and subordinate rhyolite; some plugs": "#FFD4A6",
    "Early to Middle Triassic volcanic and some sedimentary units, SE Queensland": "#5CF5C7",
    "Falloch beds, Lilyvale beds, Wyaaba beds, Yam Creek beds": "#FFFFFF",
    "Glenrock Group": "#8A9C9C",
    "Gloucester Granite": "#FF709C",
    "Good Night beds": "#FFFFFF",
    "Grahams Creek Formation": "#59D985",
    "Griman Creek Formation": "#DEFFC2",
    "Gympie Group": "#CCE8FF",
    "Injune Creek Group, Mulgildie Coal Measures, Walloon Subgroup": "#B3FFCC",
    "Ipswich Coal Measures": "#38D16E",
    "Joe Joe Group": "#D9DEDE",
    "Kennedy Province - Carboniferous volcanic rocks": "#C4CCCC",
    "Kennedy Province - Carboniferous-Permian intrusive rocks": "#FF8A8F",
    "Kennedy Province - Carboniferous-Permian volcanic rocks": "#80C7FF",
    "Kennedy Province - Permian intrusive rocks": "#FF4A2E",
    "Kennedy Province - Permian volcanic rocks": "#99D4FF",
    "Lamington Group": "#FFB363",
    "Late Permian intrusives in central and SE Queensland": "#FF407A",
    "Lizzie Creek Volcanic Group, Mount Wickham Rhyolite": "#B3DEFF",
    "Lorray Formation": "#9CABAB",
    "Marumba beds, Northbrook beds, Cedarton Volcanics, Cambroon beds, Kandanga Creek Megabreccia": "#CCE8FF",
    "Maryborough Formation": "#B3FF73",
    "Middle to Late Triassic volcanic (and some sedimentary units), SE Queensland": "#C9FCED",
    "Moolayember Formation": "#42F5BF",
    "Mount Barney beds, Alice Creek beds": "#FFFFFF",
    "Mount Mulligan Coal Measures": "#33A8FF",
    "Mount Salmon Volcanics, Mount Cooper Trachyte": "#A8FF5E",
    "New England Batholith - Permian plutonic rocks": "#FFFFFF",
    "New England Batholith - Triassic plutonic rocks": "#FF998A",
    "Normanton Formation": "#D4FFAD",
    "Obree Point Volcanics": "#66BDFF",
    "Oligocene-Miocene sediments (Elliott Formation, Austral Downs Limestone,Horse Creek Limestone, Mount Coley Sinter,Mueller Formation,Noranside Limestone,Pomona beds,Poodyea Formation, unnamed sediments": "#FFFFFF",
    "Pepper Pot Sandstone": "#26F2B5",
    "Permian-Triassic intrusives in SE Queensland": "#FFD1D4",
    "Pliocene-Pleistocene allluvial and lacustrine deposits (including Wondoola beds Armraynald beds)": "#FCF0C9",
    "Polland Waterhole Shale": "#9CFF4A",
    "Proserpine Volcanics, Whitsunday Volcanics, unnamed volcanic units": "#A8FF5E",
    "Qd-QLD": "#FFFF4D",
    "Quaternary alluvium and lacustrine deposits": "#FFFFE6",
    "Quaternary basalts, N Queensland": "#FFF59E",
    "Quaternary basalts, S Queensland": "#FFFF80",
    "Rawbelle Batholith - Early Permian plutonic rocks": "#FFFFFF",
    "Rawbelle Batholith - Triassic plutonic rocks": "#FFFFFF",
    "Rewan Group": "#FFFFFF",
    "Rockhampton Group": "#D9DEDE",
    "Rolling Downs Group": "#FFFFFF",
    "Rookwood Volcanics": "#B3DEFF",
    "Styx Coal Measures": "#FFFFFF",
    "TQr-QLD": "#FFF59E",
    "Tarong beds": "#ADFAE3",
    "Td-QLD": "#F2BA26",
    "Tertiary intrusives": "#F0D400",
    "Tertiary-Quaternary basalts, N Queensland": "#FADE94",
    "Texas beds": "#B3BDBD",
    "Torsdale Volcanics": "#C4CCCC",
    "Triassic intrusives in SE and central Queensland": "#FF5E45",
    "Wallumbilla Formation": "#C7FF99",
    "Wildash Group": "#66BDFF",
    "Winton Formation": "#DEFFC2",
    "Yarrol Formation": "#CCE8FF",
}


# QLD's 1:2M map_symbol codes follow the standard Australian geological-map convention of a
# leading era/period letter code (confirmed against the real symbol codes captured above, e.g.
# "Cgd" = Carboniferous, "CPvb" = Carboniferous-Permian, "TQbn" = Tertiary-Quaternary, "Rggc" =
# Triassic). Used as a last-resort colour fallback, checked longest-prefix-first so combined
# codes like "CP"/"TQ"/"JK" aren't shadowed by their single-letter constituents.
GEOLOGY_SYMBOL_PREFIXES = [
    ("TQ", "Tertiary-Quaternary"),
    ("JK", "Jurassic-Cretaceous"),
    ("RJ", "Triassic-Jurassic"),
    ("PR", "Permian-Triassic"),
    ("CP", "Carboniferous-Permian"),
    ("CK", "Carboniferous-Cretaceous"),
    ("Q", "Quaternary"),
    ("T", "Tertiary"),
    ("K", "Cretaceous"),
    ("J", "Jurassic"),
    ("R", "Triassic"),
    ("P", "Permian"),
    ("C", "Carboniferous"),
    ("D", "Devonian"),
    ("S", "Silurian"),
    ("O", "Ordovician"),
]


def geology_symbol_prefix_color(map_symbol):
    """Last-resort colour guess from a map_symbol's leading era-code letters (see
    GEOLOGY_SYMBOL_PREFIXES). Returns None if the symbol doesn't start with a recognised code."""
    if not map_symbol:
        return None
    m = re.match(r"^[A-Za-z]+", str(map_symbol))
    if not m:
        return None
    letters = m.group()
    for prefix, era_label in GEOLOGY_SYMBOL_PREFIXES:
        if letters.startswith(prefix):
            return geology_age_color(era_label)[0]
    return None


def geology_state_color(row):
    """Colour/label a State Surface Geology (1:2M) polygon from its 'ru_name'/'map_symbol'
    attributes. Tries, in order: (1) the real GSQ colour for this exact rock unit, (2) an
    age-keyword match on the rock unit's own name text, (3) a guess from its map_symbol's era
    prefix, (4) a neutral grey. The legend label is always the actual rock unit name (or symbol,
    or "Unknown") straight from the data, regardless of which colour tier matched, so the map
    stays useful even where the exact official colour isn't in our table."""
    ru_name = row.get("ru_name")
    map_symbol = row.get("map_symbol")
    label = str(ru_name) if ru_name else (str(map_symbol) if map_symbol else "Unknown rock unit")

    if ru_name and ru_name in GEOLOGY_STATE_RU_COLORS:
        return GEOLOGY_STATE_RU_COLORS[ru_name], label

    if ru_name:
        color, _kw = geology_age_color(ru_name)
        if color != GEOLOGY_DEFAULT_COLOR:
            return color, label

    prefix_color = geology_symbol_prefix_color(map_symbol)
    if prefix_color:
        return prefix_color, label

    return GEOLOGY_DEFAULT_COLOR, label


LAYER_CATALOG = {
    "cadastral_parcels": {
        "label": "Cadastral (property) boundaries",
        "url": "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/PlanningCadastre/LandParcelPropertyFramework/MapServer",
        "layer_id": 4,
        "geom_type": "polygon",
        "out_fields": "lot,plan,lotplan",
        "where": "1=1",
        "style": {"facecolor": "none", "edgecolor": "#555555", "linewidth": 0.6, "alpha": 0.9},
        # Label each parcel with its lot/plan (e.g. "1RP12345"), small but legible, and only
        # for parcels that actually intersect the tenement - not every parcel fetched in the
        # wider padded map extent, which would clutter the map with irrelevant lots.
        "label_field": "lotplan",
        "label_fontsize": 5.5,
        "label_only_intersecting": True,
    },
    "lga_boundary": {
        "label": "Local Government Area boundary",
        "url": "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/PlanningCadastre/LandParcelPropertyFramework/MapServer",
        "layer_id": 20,
        "geom_type": "polygon",
        "out_fields": "lga",
        "where": "1=1",
        "style": {"facecolor": "none", "edgecolor": "#9b59b6", "linewidth": 1.4, "alpha": 0.9, "linestyle": "--"},
    },
    "nearby_tenements": {
        "label": "Nearby mineral tenements (all types)",
        "url": "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/Economy/MineralTenement/MapServer",
        "layer_id": 0,
        "geom_type": "polygon",
        "out_fields": "tenid,tenname,tentype,tenowner,tenstatus",
        "where": "1=1",
        "style": {"facecolor": "none", "edgecolor": "#2e7d32", "linewidth": 1.0, "alpha": 0.9},
    },
    "contours": {
        "label": "Topographic contours (10 m)",
        "url": "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/Elevation/Contours/MapServer",
        "layer_id": 10,
        "geom_type": "polyline",
        "out_fields": "elevation",
        "where": "1=1",
        "style": {"color": "#a0522d", "linewidth": 0.5, "alpha": 0.6},
    },
    "roads_all": {
        "label": "All roads and tracks (not just state-controlled)",
        "url": "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/Transportation/RoadsAndTracks/MapServer",
        "layer_id": 10,
        "geom_type": "polyline",
        "out_fields": "road_name",
        "where": "1=1",
        "style": {"color": "#555555", "linewidth": 0.7, "alpha": 0.8},
    },
    "infrastructure_lines": {
        "label": "Infrastructure lines (pipelines, powerlines etc.)",
        "url": "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/Structure/PhysicalInfrastructure/MapServer",
        "layer_id": 110,
        "geom_type": "polyline",
        "out_fields": "*",
        "where": "1=1",
        "style": {"color": "#e67e22", "linewidth": 1.2, "alpha": 0.9},
    },
    "infrastructure_points": {
        "label": "Infrastructure points (towers, tanks etc.)",
        "url": "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/Structure/PhysicalInfrastructure/MapServer",
        "layer_id": 40,
        "geom_type": "point",
        "out_fields": "*",
        "where": "1=1",
        "style": {"color": "#e67e22", "marker": "^", "markersize": 26},
    },
    "surface_geology": {
        # This is the same "Surface Geology" layer GeoResGlobe itself shows: the statewide
        # 1:2,000,000 scale compilation (GeologyState/MapServer, layer 6 "State Surface
        # Geology"), not the larger-scale detailed mapping. Coloured per rock unit using real
        # GSQ colours where we have them, with sensible fallbacks otherwise - see
        # geology_state_color() above.
        "label": "Surface geology (State 1:2M, coloured by rock unit)",
        "url": "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/GeoscientificInformation/GeologyState/MapServer",
        "layer_id": 6,
        "geom_type": "polygon",
        "out_fields": "ru_name,map_symbol",
        "where": "1=1",
        "style": {"edgecolor": "#4a4a4a", "linewidth": 0.3, "alpha": 0.65},
        # Row-based colouring (needs both ru_name and map_symbol together) rather than a single
        # category_field - see the category_row_fn handling in plot_extra_layers().
        "category_row_fn": geology_state_color,
    },
    "boreholes_mineral": {
        "label": "Mineral boreholes / drillholes",
        "url": "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/GeoscientificInformation/Boreholes/MapServer",
        "layer_id": 6,
        "geom_type": "point",
        "out_fields": "*",
        "where": "1=1",
        "style": {"color": "#1565c0", "marker": "o", "markersize": 12},
    },
}


def esri_point_to_geometry(geom_dict):
    if not geom_dict or "x" not in geom_dict or "y" not in geom_dict:
        return None
    return Point(geom_dict["x"], geom_dict["y"])


def fetch_generic_layer(key: str, minx: float, miny: float, maxx: float, maxy: float,
                         target_crs: str, pad_frac: float = 0.3) -> gpd.GeoDataFrame:
    """Fetch one LAYER_CATALOG entry within (a slightly padded) map extent. Never raises -
    a failed fetch just means that layer is left off the map, same policy as roads/
    watercourses, since these are all optional decoration/context layers."""
    entry = LAYER_CATALOG.get(key)
    empty = gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=target_crs)
    if entry is None:
        print(f"Warning: unknown extra layer '{key}' - skipped.")
        return empty

    pad = max(maxx - minx, maxy - miny) * pad_frac
    sr = epsg_number(target_crs)
    geom_type_map = {
        "polygon": "esriGeometryEnvelope",
        "polyline": "esriGeometryEnvelope",
        "point": "esriGeometryEnvelope",
    }
    params = {
        "geometry": f"{minx - pad},{miny - pad},{maxx + pad},{maxy + pad}",
        "geometryType": geom_type_map.get(entry["geom_type"], "esriGeometryEnvelope"),
        "inSR": sr,
        "outFields": entry.get("out_fields", "*"),
        "outSR": sr,
        "where": entry.get("where", "1=1"),
        "f": "json",
    }
    url = f"{entry['url']}/{entry['layer_id']}/query"
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            # Esri services sometimes return HTTP 200 with an {"error": {...}} body (e.g. a bad
            # out_fields name) rather than an HTTP error status, so this needs its own check -
            # otherwise it silently looks like "no features found" instead of a real problem.
            print(f"Warning: extra layer '{key}' query failed - {data['error']}.")
            return empty
        records = []
        for feat in data.get("features", []):
            g = feat.get("geometry")
            if entry["geom_type"] == "polygon":
                geom = esri_rings_to_geometry(g.get("rings", [])) if g else None
            elif entry["geom_type"] == "polyline":
                geom = esri_paths_to_geometry(g.get("paths", [])) if g else None
            else:
                geom = esri_point_to_geometry(g)
            if geom is None:
                continue
            rec = dict(feat.get("attributes", {}))
            rec["geometry"] = geom
            records.append(rec)
        if not records:
            return empty
        return gpd.GeoDataFrame(records, geometry="geometry", crs=target_crs)
    except Exception as exc:  # noqa: BLE001 - optional decoration layer, never fatal
        print(f"Warning: could not fetch extra layer '{key}' ({exc}).")
        return empty


def _ray_exit_point(tenement_geom, centroid, target_point, map_span):
    """Cast a ray from the tenement's centroid through `target_point` and find where it exits
    the tenement boundary, however large or oddly-shaped the tenement is. Returns
    (exit_point, unit_dx, unit_dy), or None if no crossing was found (e.g. a pathological
    non-star-shaped polygon relative to its own centroid) - callers should fall back to a
    smaller fixed push in that case."""
    dx, dy = target_point.x - centroid.x, target_point.y - centroid.y
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        dx, dy, dist = 1.0, 0.0, 1.0
    ux, uy = dx / dist, dy / dist
    ray_len = map_span * 3
    ray = LineString([(centroid.x, centroid.y), (centroid.x + ux * ray_len, centroid.y + uy * ray_len)])
    try:
        inter = ray.intersection(tenement_geom.boundary)
    except Exception:
        return None
    if inter.is_empty:
        return None
    if inter.geom_type == "Point":
        pts = [inter]
    elif inter.geom_type == "MultiPoint":
        pts = list(inter.geoms)
    else:
        # Ray clipped along an edge rather than crossing it cleanly - pull out endpoints.
        try:
            pts = [Point(c) for c in inter.coords]
        except Exception:
            pts = [Point(c) for geom in getattr(inter, "geoms", []) for c in getattr(geom, "coords", [])]
    if not pts:
        return None
    farthest = max(pts, key=lambda p: (p.x - centroid.x) ** 2 + (p.y - centroid.y) ** 2)
    return farthest, ux, uy


def _place_labels_around_tenement(ax, gdf, label_field, tenement_geom, minx, miny, maxx, maxy,
                                   fontsize, color, halo, zorder):
    """Label each feature in `gdf`, keeping the tenement itself clear of clutter: the EPM is
    the main feature of the map, so a label never sits directly on top of it if that can be
    avoided. For each feature:
      1. If its label point doesn't fall inside the tenement, label it in place as normal.
      2. If it does, but part of the same feature lies outside the tenement, move the label
         onto that outside portion instead - still on the real feature, just off the EPM.
      3. If the feature sits entirely inside the tenement with nowhere clear to put the label,
         push the label out beyond the tenement boundary and draw a thin call-out line back to
         the feature's true location, rather than crowding text over the EPM fill.
    """
    if halo is None:
        halo = []
    halo_effect = halo or [pe.withStroke(linewidth=1.6, foreground="white")]
    map_span = max(maxx - minx, maxy - miny)
    push_dist = map_span * 0.035  # how far a call-out label is pushed beyond the tenement

    if tenement_geom is not None and not tenement_geom.is_empty:
        tenement_centroid = tenement_geom.centroid
    else:
        tenement_centroid = None

    for _, r in gdf.iterrows():
        val = r.get(label_field)
        if not val:
            continue
        geom = r.geometry
        point = geom.representative_point()
        use_callout = False
        label_pos = point

        if tenement_geom is not None and not tenement_geom.is_empty and tenement_geom.contains(point):
            # Label point lands on the main feature - try to find room on the same parcel
            # outside the tenement first, before resorting to a call-out.
            try:
                outside_part = geom.difference(tenement_geom)
            except Exception:
                outside_part = None
            if outside_part is not None and not outside_part.is_empty and outside_part.area > geom.area * 0.02:
                label_pos = outside_part.representative_point()
            else:
                use_callout = True
                exit_info = _ray_exit_point(tenement_geom, tenement_centroid, point, map_span) \
                    if tenement_centroid is not None else None
                if exit_info is not None:
                    exit_pt, ux, uy = exit_info
                    label_pos = Point(exit_pt.x + ux * push_dist, exit_pt.y + uy * push_dist)
                elif tenement_centroid is not None:
                    # Fallback if the ray-cast couldn't find a clean boundary crossing (very
                    # irregular/multi-part tenement shape) - push a fixed distance instead of
                    # leaving the label stranded inside the EPM.
                    dx, dy = point.x - tenement_centroid.x, point.y - tenement_centroid.y
                    dist = math.hypot(dx, dy)
                    if dist < 1e-6:
                        dx, dy = 1.0, 0.0
                        dist = 1.0
                    label_pos = Point(point.x + dx / dist * push_dist, point.y + dy / dist * push_dist)
                else:
                    label_pos = Point(point.x + push_dist, point.y)

        if use_callout:
            ax.annotate(str(val), xy=(point.x, point.y), xytext=(label_pos.x, label_pos.y),
                        ha="center", va="center", fontsize=fontsize, color=color,
                        fontweight="bold", zorder=zorder, clip_on=True,
                        arrowprops=dict(arrowstyle="-", color=color, linewidth=0.5, alpha=0.8),
                        path_effects=halo_effect)
        else:
            txt = ax.text(label_pos.x, label_pos.y, str(val), ha="center", va="center",
                           fontsize=fontsize, color=color, fontweight="bold",
                           zorder=zorder, clip_on=True)
            txt.set_path_effects(halo_effect)


def plot_extra_layers(ax, keys, minx, miny, maxx, maxy, target_crs, tenement_geom=None,
                       start_zorder=2.2, text_color="black", halo=None):
    """Fetch and draw each requested LAYER_CATALOG layer onto `ax`. Returns a list of
    (kind, color, label) legend entries in the same shape draw_report_legend_box() expects,
    so callers can just extend their existing legend_entries list with the result.

    `tenement_geom` (a single shapely geometry, in the same projected CRS as the map) is used
    for any layer configured with "label_only_intersecting": True - only features that
    actually intersect the tenement get a text label, even though the fetched layer itself
    covers a wider padded area around it for visual context.
    """
    legend_entries = []
    for i, key in enumerate(keys or []):
        entry = LAYER_CATALOG.get(key)
        if entry is None:
            print(f"Warning: unknown extra layer '{key}' - skipped.")
            continue
        gdf = fetch_generic_layer(key, minx, miny, maxx, maxy, target_crs)
        if gdf.empty:
            continue
        style = entry["style"]
        z = start_zorder + i * 0.01
        if entry["geom_type"] == "polygon":
            category_field = entry.get("category_field")
            category_color_fn = entry.get("category_color_fn")
            category_row_fn = entry.get("category_row_fn")
            if category_row_fn is not None or (category_field and category_color_fn and category_field in gdf.columns):
                # Colour each feature by a mapped category (e.g. geological age/rock unit)
                # instead of a single flat style. Group by the resolved (color, label) pair so
                # every distinct category present in the fetched data gets exactly one legend
                # entry - no fixed exhaustive list, and nothing actually on the map is left off
                # the legend. category_row_fn gets the whole feature row (for layers like
                # surface geology that need more than one attribute to pick a colour);
                # category_color_fn gets just the single category_field value.
                gdf = gdf.copy()
                if category_row_fn is not None:
                    mapped = gdf.apply(category_row_fn, axis=1)
                else:
                    mapped = gdf[category_field].apply(category_color_fn)
                gdf["_cat_color"] = mapped.apply(lambda t: t[0])
                gdf["_cat_label"] = mapped.apply(lambda t: t[1])
                base_style = {k: v for k, v in style.items() if k not in ("facecolor", "edgecolor")}
                edge = style.get("edgecolor", "#4a4a4a")
                seen_labels = set()
                for (cat_color, cat_label), sub in gdf.groupby(["_cat_color", "_cat_label"]):
                    sub.plot(ax=ax, zorder=z, facecolor=cat_color, edgecolor=edge, **base_style)
                    if cat_label not in seen_labels:
                        legend_entries.append(("fill", cat_color, cat_label))
                        seen_labels.add(cat_label)
            else:
                gdf.plot(ax=ax, zorder=z, **style)
                legend_entries.append(("outline" if style.get("facecolor") == "none" else "fill",
                                        style.get("edgecolor", style.get("facecolor")), entry["label"]))

            label_field = entry.get("label_field")
            if label_field and label_field in gdf.columns:
                to_label = gdf
                if entry.get("label_only_intersecting") and tenement_geom is not None:
                    to_label = gdf[gdf.geometry.intersects(tenement_geom)]
                label_fontsize = entry.get("label_fontsize", 5.5)
                label_color = entry.get("label_color", style.get("edgecolor", text_color))
                _place_labels_around_tenement(ax, to_label, label_field, tenement_geom,
                                               minx, miny, maxx, maxy, label_fontsize,
                                               label_color, halo, z + 0.05)
        elif entry["geom_type"] == "polyline":
            gdf.plot(ax=ax, zorder=z, **style)
            legend_entries.append(("line", style.get("color"), entry["label"]))
        else:  # point
            ax.scatter(gdf.geometry.x, gdf.geometry.y, color=style.get("color"),
                       marker=style.get("marker", "o"), s=style.get("markersize", 12),
                       zorder=z, edgecolors="black", linewidths=0.3)
            legend_entries.append(("point", style.get("color"), entry["label"]))
    return legend_entries


def fetch_epm_gdf(epm_number: str) -> gpd.GeoDataFrame:
    """Query the QLD mines permits service for a given EPM (granted, then application)."""
    display_name = normalise_epm(epm_number)
    for layer_id, status in EPM_LAYERS:
        url = f"{BASE}/{layer_id}/query"
        params = {
            "where": f"displayname='{display_name}'",
            "outFields": "*",
            "f": "json",
            "outSR": 4326,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        feats = data.get("features", [])
        if feats:
            feat = feats[0]
            geom = esri_rings_to_geometry(feat["geometry"]["rings"])
            attrs = dict(feat["attributes"])
            attrs["_status"] = status
            return gpd.GeoDataFrame([attrs], geometry=[geom], crs=GDA2020_GEOGRAPHIC)
    raise ValueError(
        f"No granted or application EPM found for '{display_name}'. "
        "Check the permit number, or pass --input with a local geojson/shapefile."
    )


def load_local_gdf(path: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(GDA2020_GEOGRAPHIC)
    else:
        gdf = gdf.to_crs(GDA2020_GEOGRAPHIC)
    if "_status" not in gdf.columns:
        gdf["_status"] = gdf.get("permitstatus", "Granted")
    return gdf.iloc[[0]]  # first feature only


def mga_zone_for_lon(lon: float) -> int:
    return int((lon + 180) // 6) + 1  # standard UTM/MGA zone formula


def nice_number(value: float, round_up: bool = True) -> float:
    """Round to a '1-2-5' style nice number, e.g. 1,2,5,10,20,50,100..."""
    if value <= 0:
        return value
    exponent = math.floor(math.log10(value))
    fraction = value / (10 ** exponent)
    if round_up:
        nice_fraction = 1 if fraction <= 1 else 2 if fraction <= 2 else 5 if fraction <= 5 else 10
    else:
        nice_fraction = 1 if fraction < 1.5 else 2 if fraction < 3.5 else 5 if fraction < 7.5 else 10
    return nice_fraction * (10 ** exponent)


def choose_scale(width_m: float, height_m: float, map_width_mm: float, map_height_mm: float) -> int:
    """Pick a round map scale (e.g. 1:100000) that fits the extent on the page, with margin."""
    scale_x = (width_m * 1000) / map_width_mm
    scale_y = (height_m * 1000) / map_height_mm
    required_scale = max(scale_x, scale_y) * 1.15  # 15% margin so the tenement isn't edge-to-edge
    return int(nice_number(required_scale, round_up=True))


def draw_north_arrow(ax, x=0.94, y=0.90, text_color="black", halo=None):
    txt = ax.annotate("N", xy=(x, y + 0.05), xycoords="axes fraction",
                       ha="center", va="center", fontsize=13, fontweight="bold", color=text_color)
    if halo:
        txt.set_path_effects(halo)
    arrow = FancyArrow(x, y - 0.045, 0, 0.045, width=0.006, head_width=0.022,
                        head_length=0.025, transform=ax.transAxes, color=text_color,
                        length_includes_head=True)
    arrow.set_path_effects(halo or [])
    ax.add_patch(arrow)


def draw_scale_bar(ax, x0=0.06, y0=0.05, backing_box=True):
    xlim = ax.get_xlim()
    map_width_m = xlim[1] - xlim[0]
    axes_width_frac = 0.34

    bar_width_m = map_width_m * axes_width_frac
    bar_km_nice = nice_number(bar_width_m / 1000, round_up=False) or 1
    seg_km = bar_km_nice / 4
    bar_frac_width = (bar_km_nice * 1000) / map_width_m

    n_segments = 4
    seg_frac = bar_frac_width / n_segments
    bar_height = 0.012
    label_h = 0.02   # rough height taken up by the number labels above the bar
    km_label_w = 0.035  # rough width of the trailing "km" label

    if backing_box:
        pad_x, pad_y = 0.012, 0.008
        box = plt.Rectangle(
            (x0 - pad_x, y0 - pad_y),
            bar_frac_width + km_label_w + 2 * pad_x,
            bar_height + label_h + 2 * pad_y,
            transform=ax.transAxes, facecolor="white", edgecolor="black",
            linewidth=0.5, alpha=0.75, zorder=4.5,
        )
        ax.add_patch(box)

    for i in range(n_segments):
        color = "black" if i % 2 == 0 else "white"
        ax.add_patch(plt.Rectangle((x0 + i * seg_frac, y0), seg_frac, bar_height,
                                    transform=ax.transAxes, facecolor=color,
                                    edgecolor="black", linewidth=0.6, zorder=5))
    for i in range(n_segments + 1):
        ax.text(x0 + i * seg_frac, y0 + bar_height + 0.008, f"{seg_km * i:g}",
                 transform=ax.transAxes, ha="center", va="bottom", fontsize=6.5, zorder=6)
    ax.text(x0 + bar_frac_width + 0.02, y0 + bar_height / 2, "km",
             transform=ax.transAxes, ha="left", va="center", fontsize=7, zorder=6)


def _frange(start, stop, step):
    n0 = math.ceil(start / step)
    val = n0 * step
    while val <= stop:
        yield val
        val += step


def epsg_number(crs_string: str) -> int:
    return int(crs_string.split(":")[1])


def _export_esri_image(url, minx, miny, maxx, maxy, target_crs, width_px, height_px,
                        fmt="png32", transparent=False):
    """Low-level Esri MapServer /export fetch, returning a PIL Image (or None on failure)."""
    from PIL import Image

    sr = epsg_number(target_crs)
    params = {
        "bbox": f"{minx},{miny},{maxx},{maxy}",
        "bboxSR": sr,
        "imageSR": sr,
        "size": f"{width_px},{height_px}",
        "format": fmt,
        "f": "image",
    }
    if transparent:
        params["transparent"] = "true"
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content))


def fetch_basemap_image(minx, miny, maxx, maxy, target_crs: str, width_px=2000, height_px=1364):
    """Fetch a satellite image for the given extent directly in the target CRS via Esri's
    World Imagery export service (no reprojection needed client-side). Returns an (H,W,3)
    uint8 array, or None if the fetch fails (e.g. no internet)."""
    try:
        img = _export_esri_image(IMAGERY_EXPORT_URL, minx, miny, maxx, maxy, target_crs,
                                  width_px, height_px, fmt="jpg")
        return np.array(img.convert("RGB"))
    except Exception as exc:  # noqa: BLE001 - imagery is optional, never fail the map for it
        print(f"Warning: could not fetch satellite imagery ({exc}). Continuing without it.")
        return None


def fetch_greyscale_basemap(minx, miny, maxx, maxy, target_crs: str, width_px=2000, height_px=1364):
    """Fetch Esri's light gray canvas basemap (base + label/reference overlay composited
    together), which shows towns, roads and admin boundaries in muted grey tones without
    the strong colour of satellite imagery. Returns an (H,W,3) uint8 array, or None on
    failure."""
    try:
        base = _export_esri_image(GREY_BASE_URL, minx, miny, maxx, maxy, target_crs,
                                   width_px, height_px, fmt="png32").convert("RGBA")
        try:
            ref = _export_esri_image(GREY_REFERENCE_URL, minx, miny, maxx, maxy, target_crs,
                                      width_px, height_px, fmt="png32", transparent=True).convert("RGBA")
            base.alpha_composite(ref)
        except Exception as exc:  # noqa: BLE001 - labels are a nice-to-have on top of the base
            print(f"Warning: could not fetch grey basemap labels ({exc}). Using unlabelled base.")
        return np.array(base.convert("RGB"))
    except Exception as exc:  # noqa: BLE001 - imagery is optional, never fail the map for it
        print(f"Warning: could not fetch grey basemap ({exc}). Continuing without it.")
        return None


def fetch_nearby_localities(minx, miny, maxx, maxy, target_crs: str, limit=6):
    """Return names of QLD localities (suburbs/towns) intersecting the map extent, so the
    reader can see what's nearby without needing to read satellite imagery alone."""
    sr = epsg_number(target_crs)
    url = f"{ADMIN_BASE}/{LOCALITY_LAYER}/query"
    params = {
        "geometry": f"{minx},{miny},{maxx},{maxy}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": sr,
        "outFields": "locality",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        names = sorted({f["attributes"]["locality"] for f in data.get("features", [])})
        return names[:limit]
    except Exception as exc:  # noqa: BLE001 - never fail the map over this
        print(f"Warning: could not fetch nearby localities ({exc}).")
        return []


def draw_compass_arrow(ax, x=0.93, y=0.88, size=0.06):
    """A two-tone kite/compass style north arrow, matching typical consultant report maps
    (as opposed to the simple line arrow used on the satellite locality map)."""
    left = MplPolygon(
        [(x, y + size), (x - size * 0.32, y - size * 0.55), (x, y - size * 0.15)],
        transform=ax.transAxes, facecolor="#595959", edgecolor="black", linewidth=0.8, zorder=10,
    )
    right = MplPolygon(
        [(x, y + size), (x + size * 0.32, y - size * 0.55), (x, y - size * 0.15)],
        transform=ax.transAxes, facecolor="#e6e6e6", edgecolor="black", linewidth=0.8, zorder=10,
    )
    ax.add_patch(left)
    ax.add_patch(right)
    ax.text(x, y + size + 0.025, "N", transform=ax.transAxes, ha="center", va="bottom",
            fontsize=11, fontweight="bold", zorder=10)


def setup_report_frame(gdf_proj: gpd.GeoDataFrame, target_crs: str, forced_scale: int = None,
                        map_width_mm: int = 190, map_height_mm: int = 195,
                        rect=(0.08, 0.32, 0.84, 0.60), basemap: str = "none"):
    """Build a bordered report-style map frame: coordinate labels outside the frame (eastings
    on top, northings on both sides, rotated), a compass-style north arrow, and an optional
    background - 'none' (plain white, best contrast for print), 'satellite' (Esri World
    Imagery), or 'greyscale' (Esri Light Gray Canvas, shows towns/roads without imagery's
    strong colour) - matching a typical consultant partial-relinquishment report figure.
    """
    minx, miny, maxx, maxy = gdf_proj.total_bounds
    width_m, height_m = maxx - minx, maxy - miny
    if forced_scale:
        scale = forced_scale
    else:
        # Unrounded "fits the frame snugly" scale (with a small margin), rather than a nice
        # round number - this matches how a fixed print-layout frame in QGIS/ArcGIS behaves,
        # and is why report figures often show odd scales like "1:148.3361k".
        scale_x = (width_m * 1000) / map_width_mm
        scale_y = (height_m * 1000) / map_height_mm
        scale = max(scale_x, scale_y) * 1.08

    half_w_m = (scale * map_width_mm / 1000) / 2
    half_h_m = (scale * map_height_mm / 1000) / 2
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    xlim = (cx - half_w_m, cx + half_w_m)
    ylim = (cy - half_h_m, cy + half_h_m)

    fig = plt.figure(figsize=(8.27, 11.69))  # A4 portrait, inches - matches the reference layout
    ax = fig.add_axes(rect)

    has_imagery = False
    if basemap == "satellite":
        img_w = 1800
        img_h = int(img_w * (map_height_mm / map_width_mm))
        img = fetch_basemap_image(xlim[0], ylim[0], xlim[1], ylim[1], target_crs, img_w, img_h)
        if img is not None:
            ax.imshow(img, extent=(xlim[0], xlim[1], ylim[0], ylim[1]), origin="upper",
                      zorder=0, interpolation="bilinear")
            has_imagery = True
    elif basemap == "greyscale":
        img_w = 1800
        img_h = int(img_w * (map_height_mm / map_width_mm))
        img = fetch_greyscale_basemap(xlim[0], ylim[0], xlim[1], ylim[1], target_crs, img_w, img_h)
        if img is not None:
            ax.imshow(img, extent=(xlim[0], xlim[1], ylim[0], ylim[1]), origin="upper",
                      zorder=0, interpolation="bilinear")
            has_imagery = True

    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")

    grid_color = "#e8e8e8" if has_imagery else "#c9c9c9"
    text_color = "white" if basemap == "satellite" and has_imagery else "black"
    halo = [pe.withStroke(linewidth=2.2, foreground="black")] if (basemap == "satellite" and has_imagery) else []

    grid_interval = nice_number((xlim[1] - xlim[0]) / 4, round_up=False)
    ax.set_xticks(list(_frange(xlim[0], xlim[1], grid_interval)))
    ax.set_yticks(list(_frange(ylim[0], ylim[1], grid_interval)))
    ax.grid(True, color=grid_color, linewidth=0.5, linestyle="-", zorder=1, alpha=0.8)

    ax.xaxis.set_major_formatter(lambda v, pos: f"{v:,.0f}")
    ax.yaxis.set_major_formatter(lambda v, pos: f"{v:,.0f}")
    ax.tick_params(axis="x", top=True, labeltop=True, bottom=False, labelbottom=False, labelsize=8, colors="black")
    ax.tick_params(axis="y", left=True, labelleft=True, right=True, labelright=True, labelsize=8, colors="black")
    for label in ax.get_yticklabels():
        label.set_rotation(90)
        label.set_va("center")

    for spine in ax.spines.values():
        spine.set_linewidth(1.1)
        spine.set_color("black")

    draw_compass_arrow(ax)

    return fig, ax, xlim, ylim, scale, has_imagery, text_color, halo


def _measure_text_width_in(fig, text, fontsize, bold=False):
    """Exact rendered width (inches) of `text` at `fontsize`, using matplotlib's own font
    metrics rather than an estimated average character width. Long, wide, all-caps company
    names (lots of W/M/G/Q) render noticeably wider than a simple per-character estimate
    predicts, which is what let earlier holder names still spill past the title-block border."""
    if not text:
        return 0.0
    FigureCanvasAgg(fig)  # make sure fig.canvas has a real Agg renderer available
    renderer = fig.canvas.get_renderer()
    t = fig.text(0, 0, text, fontsize=fontsize, fontweight="bold" if bold else "normal")
    width_px = t.get_window_extent(renderer=renderer).width
    t.remove()
    return width_px / fig.dpi


def _fit_text(fig, text, avail_width_in, base_fontsize, min_fontsize=7.5, bold=False, max_lines=2):
    """Fit `text` inside a box `avail_width_in` inches wide: shrink the font size first
    (in 0.5pt steps down to min_fontsize, measuring the actual rendered width each time),
    and only if it's still too wide at the minimum size, wrap onto up to `max_lines` lines
    (breaking on whole words), truncating with an ellipsis if it still doesn't fit. Returns
    (fontsize, [line, ...]). This keeps title-block/legend text from running outside its
    bordered box, however long the company or permit name is."""
    text = (text or "").strip()
    if not text:
        return base_fontsize, [""]

    fontsize = base_fontsize
    while fontsize > min_fontsize:
        if _measure_text_width_in(fig, text, fontsize, bold) <= avail_width_in:
            return fontsize, [text]
        fontsize -= 0.5
    fontsize = min_fontsize

    if _measure_text_width_in(fig, text, fontsize, bold) <= avail_width_in:
        return fontsize, [text]

    # Still too wide even at the smallest allowed size - wrap word-by-word using real
    # measurements so each line actually fits, then stop at max_lines.
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or _measure_text_width_in(fig, candidate, fontsize, bold) <= avail_width_in:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    else:
        if current:
            lines.append(current)
    if not lines:
        lines = [text]

    consumed_words = sum(len(l.split()) for l in lines)
    last_too_wide = bool(lines) and _measure_text_width_in(fig, lines[-1], fontsize, bold) > avail_width_in
    if consumed_words < len(words) or last_too_wide:
        # Either words were left over, or the final line itself is still too wide (e.g. one
        # very long word) - trim the last line down, character by character, until it plus an
        # ellipsis actually fits, so nothing ever extends past the box.
        last = lines[-1] if lines else ""
        while last and _measure_text_width_in(fig, last + "…", fontsize, bold) > avail_width_in:
            last = last[:-1].rstrip()
        truncated = (last.rstrip() + "…") if last else "…"
        if lines:
            lines[-1] = truncated
        else:
            lines = [truncated]
    return fontsize, lines[:max_lines]


def draw_report_title_block(fig, rect, project_name, title_lines, scale, zone,
                             drawn_by, date_str, company_name=None):
    """A bordered drawing title-block table (project / page / scale / grid / drawn / date on
    the left, the figure title on the right, company name/imprint underneath) - the standard
    layout used at the bottom of a consultant's report map figure."""
    x0, y0, w, h = rect
    fig_w_in, fig_h_in = fig.get_size_inches()
    outer = plt.Rectangle((x0, y0), w, h, transform=fig.transFigure, facecolor="white",
                           edgecolor="black", linewidth=1.1, zorder=9)
    fig.add_artist(outer)

    left_w = w * 0.34
    divider = plt.Line2D([x0 + left_w, x0 + left_w], [y0, y0 + h], transform=fig.transFigure,
                          color="black", linewidth=1.0, zorder=10)
    fig.add_artist(divider)

    pad = 0.01
    left_avail_in = (left_w - 2 * pad) * fig_w_in
    right_avail_in = (w - left_w - 0.015 - pad) * fig_w_in

    # Project name (left column heading) - shrink/wrap to stay inside its column.
    pn_fontsize, pn_lines = _fit_text(fig, project_name, left_avail_in, 11, bold=True)
    pn_line_h = (pn_fontsize * 1.25 / 72) / fig_h_in
    for i, line in enumerate(pn_lines):
        fig.text(x0 + pad, y0 + h - 0.02 - i * pn_line_h, line,
                  fontsize=pn_fontsize, fontweight="bold", va="top", zorder=11)

    rows_top = y0 + h - 0.02 - len(pn_lines) * pn_line_h - 0.008
    rows = [
        ("Page", "A4"),
        ("Scale", f"1:{scale / 1000:,.4f}k"),
        ("Grid", f"MGA94 z{zone}"),
        ("Drawn", drawn_by),
        ("Date", date_str),
    ]
    row_h = (rows_top - y0 - 0.01) / len(rows)
    for i, (label, value) in enumerate(rows):
        ry = rows_top - i * row_h
        row_fontsize, row_lines = _fit_text(fig, f"{label}: {value}", left_avail_in, 7.5, min_fontsize=6)
        fig.text(x0 + pad, ry, row_lines[0], fontsize=row_fontsize, va="top", zorder=11)

    # Title lines (right column) - each line independently shrunk/wrapped to the column width,
    # stacked with spacing derived from each line's own fitted font size.
    ry = y0 + h - 0.025
    for i, line in enumerate(title_lines):
        base_fs = 13 if i == 0 else 11
        fs, lines = _fit_text(fig, line, right_avail_in, base_fs, min_fontsize=8, bold=True)
        line_h = (fs * 1.2 / 72) / fig_h_in
        for line_part in lines:
            fig.text(x0 + left_w + 0.015, ry, line_part, fontsize=fs, fontweight="bold", va="top", zorder=11)
            ry -= line_h
        ry -= 0.006  # small gap between title lines

    if company_name:
        cn_fontsize, cn_lines = _fit_text(fig, company_name, right_avail_in, 12, min_fontsize=7.5, bold=True)
        cn_line_h = (cn_fontsize * 1.2 / 72) / fig_h_in
        base_y = y0 + 0.015
        for i, line in enumerate(reversed(cn_lines)):
            fig.text(x0 + left_w + 0.015, base_y + i * cn_line_h, line,
                      fontsize=cn_fontsize, fontweight="bold", va="bottom", zorder=11)


# A legend box only has so much room before entries either overlap or shrink past legibility.
# Beyond this many entries (e.g. a surface-geology layer pulling in a dozen-plus distinct rock
# units under one tenement), the legend is moved to its own dedicated page instead of being
# crammed into/over the map - see build_legend_page_figure() and the overflow handling in
# build_map()/build_subblock_maps() below. Ordinary maps (tenement + maybe roads/watercourses/
# one or two extra layers) sit well under this and are never affected.
LEGEND_MAX_INLINE_ENTRIES = 6


def _draw_legend_swatch(fig, sx, ry, swatch_w, swatch_h, kind, color, zorder=10):
    """Draw just the colour/pattern swatch for one legend entry at position (sx, ry) (ry is the
    swatch's vertical centre in figure fraction coords) - shared by the inline legend box and
    the full legend page so both use identical swatch styling."""
    if kind == "fill":
        fig.add_artist(plt.Rectangle((sx, ry - swatch_h / 2), swatch_w, swatch_h,
                                      transform=fig.transFigure, facecolor=color,
                                      edgecolor="black", linewidth=0.8, zorder=zorder))
    elif kind == "outline":
        fig.add_artist(plt.Rectangle((sx, ry - swatch_h / 2), swatch_w, swatch_h,
                                      transform=fig.transFigure, facecolor="none",
                                      edgecolor=color, linewidth=1.4, zorder=zorder))
    elif kind == "line":
        fig.add_artist(plt.Line2D([sx, sx + swatch_w], [ry, ry],
                                   transform=fig.transFigure, color=color, linewidth=1.8, zorder=zorder))
    elif kind == "point":
        fig.add_artist(plt.Line2D([sx + swatch_w / 2], [ry], marker="o",
                                   transform=fig.transFigure, color=color, markersize=6,
                                   markeredgecolor="black", markeredgewidth=0.4, linestyle="none", zorder=zorder))


def draw_report_legend_box(fig, rect, entries, overflow_note=None):
    """entries: list of (kind, color, label) where kind is 'fill', 'outline', 'line' or 'point'.
    If overflow_note is given, the entries aren't drawn at all - instead the box just shows that
    note (used when the legend has been moved to its own page instead, so this box still tells
    the reader where to find it rather than being left confusingly blank)."""
    x0, y0, w, h = rect
    fig_w_in, _fig_h_in = fig.get_size_inches()
    outer = plt.Rectangle((x0, y0), w, h, transform=fig.transFigure, facecolor="white",
                           edgecolor="black", linewidth=1.1, zorder=9)
    fig.add_artist(outer)
    fig.text(x0 + 0.012, y0 + h - 0.02, "Legend", fontsize=12, va="top", zorder=11)

    if overflow_note:
        note_avail_in = (w - 0.024) * fig_w_in
        fs, lines = _fit_text(fig, overflow_note, note_avail_in, 9, min_fontsize=7, max_lines=3)
        line_h = (fs * 1.3 / 72) / fig.get_size_inches()[1]
        start_y = y0 + h - 0.06
        for i, line in enumerate(lines):
            fig.text(x0 + 0.012, start_y - i * line_h, line, fontsize=fs, va="top",
                      style="italic", color="dimgrey", zorder=11)
        return

    swatch_w, swatch_h = 0.022, 0.016
    row_h = (h - 0.045) / max(len(entries), 1)
    label_avail_in = (w - 0.015 - swatch_w - 0.012 - 0.01) * fig_w_in
    for i, (kind, color, label) in enumerate(entries):
        ry = y0 + h - 0.045 - i * row_h
        sx = x0 + 0.015
        _draw_legend_swatch(fig, sx, ry - swatch_h / 2, swatch_w, swatch_h, kind, color)
        fs, lines = _fit_text(fig, label, label_avail_in, 8.5, min_fontsize=6.5)
        fig.text(sx + swatch_w + 0.012, ry - swatch_h / 2, lines[0], fontsize=fs, va="center", zorder=11)


def build_legend_page_figure(entries, heading, subheading=None):
    """A dedicated A4 landscape page just for a legend that's too big to fit inline on the map
    itself - laid out in columns so it stays readable even with dozens of entries (e.g. every
    distinct rock unit a surface-geology layer pulled in under the tenement)."""
    fig = plt.figure(figsize=(11.69, 8.27))
    fig_w_in, fig_h_in = fig.get_size_inches()

    fig.text(0.5, 0.94, heading, fontsize=18, fontweight="bold", ha="center")
    top = 0.86
    if subheading:
        fig.text(0.5, 0.895, subheading, fontsize=11, ha="center", color="dimgrey")
        top = 0.83

    n = len(entries)
    rows_per_col = 24
    n_cols = max(1, min(4, math.ceil(n / rows_per_col)))
    rows_per_col = math.ceil(n / n_cols)

    margin_x = 0.06
    usable_w = 1 - 2 * margin_x
    col_w = usable_w / n_cols
    bottom = 0.07
    row_h = (top - bottom) / max(rows_per_col, 1)
    swatch_w, swatch_h = 0.018, 0.014
    label_avail_in = (col_w - swatch_w - 0.05) * fig_w_in

    for i, (kind, color, label) in enumerate(entries):
        col = i // rows_per_col
        row = i % rows_per_col
        x0 = margin_x + col * col_w
        ry = top - row * row_h - row_h / 2
        _draw_legend_swatch(fig, x0, ry, swatch_w, swatch_h, kind, color)
        fs, lines = _fit_text(fig, label, label_avail_in, 10, min_fontsize=7.5, max_lines=2)
        line_h = (fs * 1.15 / 72) / fig_h_in
        ly = ry + (line_h * (len(lines) - 1)) / 2
        for line in lines:
            fig.text(x0 + swatch_w + 0.014, ly, line, fontsize=fs, va="center")
            ly -= line_h

    fig.add_artist(plt.Line2D([0.06, 0.94], [0.035, 0.035], transform=fig.transFigure,
                               color="black", linewidth=0.6))
    fig.text(0.5, 0.02, f"{n} legend entr{'y' if n == 1 else 'ies'}", fontsize=8, ha="center", color="dimgrey")
    return fig


def add_figure_caption_and_footer(fig, caption, footer_left=None, page_number=None, footer_right=None):
    fig_w_in, _fig_h_in = fig.get_size_inches()
    if caption:
        fs, lines = _fit_text(fig, caption, 0.86 * fig_w_in, 10, min_fontsize=7.5, bold=True, max_lines=2)
        line_h_frac = (fs * 1.2 / 72) / _fig_h_in
        start_y = 0.095 + (len(lines) - 1) * line_h_frac / 2
        for i, line in enumerate(lines):
            fig.text(0.5, start_y - i * line_h_frac, line, fontsize=fs, fontweight="bold",
                      ha="center", zorder=11)
    if footer_left or page_number or footer_right:
        fig.add_artist(plt.Line2D([0.06, 0.94], [0.035, 0.035], transform=fig.transFigure,
                                   color="black", linewidth=0.6))
        # Reserve the centre ~18% of the footer width for the page number so long left/right
        # text can't run into it; each side gets shrunk to fit its own available width.
        side_avail_in = 0.36 * fig_w_in
        if footer_left:
            fs_l, lines_l = _fit_text(fig, footer_left, side_avail_in, 8.5, min_fontsize=6.5)
            fig.text(0.06, 0.02, lines_l[0], fontsize=fs_l, ha="left")
        if page_number is not None:
            fig.text(0.5, 0.02, str(page_number), fontsize=8.5, ha="center")
        if footer_right:
            fs_r, lines_r = _fit_text(fig, footer_right, side_avail_in, 8.5, min_fontsize=6.5)
            fig.text(0.94, 0.02, lines_r[0], fontsize=fs_r, ha="right")


def determine_zone_and_project(gdf: gpd.GeoDataFrame):
    centroid = gdf.geometry.iloc[0].centroid
    zone = mga_zone_for_lon(centroid.x)
    if zone not in MGA_ZONES:
        raise ValueError(f"Computed MGA zone {zone} from longitude {centroid.x:.3f} is outside QLD's zones (54-56).")
    target_crs = MGA_ZONES[zone]
    return zone, target_crs, gdf.to_crs(target_crs)


def setup_map_frame(gdf_proj: gpd.GeoDataFrame, target_crs: str, forced_scale: int = None,
                     basemap: str = "satellite", map_width_mm: int = 220, map_height_mm: int = 150):
    """Build the page, basemap, extent/scale, grid and standard furniture (north arrow, scale
    bar) shared by every map variant. Returns everything the caller needs to draw its own
    thematic content (tenement fill, sub-block grid, etc.) and title block on top."""
    minx, miny, maxx, maxy = gdf_proj.total_bounds
    width_m, height_m = maxx - minx, maxy - miny
    scale = forced_scale or choose_scale(width_m, height_m, map_width_mm, map_height_mm)

    half_w_m = (scale * map_width_mm / 1000) / 2
    half_h_m = (scale * map_height_mm / 1000) / 2
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    xlim = (cx - half_w_m, cx + half_w_m)
    ylim = (cy - half_h_m, cy + half_h_m)

    fig = plt.figure(figsize=(11.69, 8.27))  # A4 landscape, inches
    ax = fig.add_axes([0.06, 0.22, 0.88, 0.72])

    has_imagery = False
    if basemap == "satellite":
        img_w = 2000
        img_h = int(img_w * (map_height_mm / map_width_mm))
        img = fetch_basemap_image(xlim[0], ylim[0], xlim[1], ylim[1], target_crs, img_w, img_h)
        if img is not None:
            ax.imshow(img, extent=(xlim[0], xlim[1], ylim[0], ylim[1]), origin="upper",
                      zorder=0, interpolation="bilinear")
            has_imagery = True

    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")

    grid_color = "white" if has_imagery else "grey"
    text_color = "white" if has_imagery else "black"
    halo = [pe.withStroke(linewidth=2.2, foreground="black")] if has_imagery else []

    grid_interval = nice_number((xlim[1] - xlim[0]) / 6, round_up=False)
    ax.set_xticks(list(_frange(xlim[0], xlim[1], grid_interval)))
    ax.set_yticks(list(_frange(ylim[0], ylim[1], grid_interval)))
    ax.grid(True, color=grid_color, linewidth=0.6, linestyle="-", alpha=0.7, zorder=2)
    ax.xaxis.set_major_formatter(lambda v, pos: f"{v:,.0f}mE")
    ax.yaxis.set_major_formatter(lambda v, pos: f"{v:,.0f}mN")
    ax.tick_params(labelsize=7, colors=text_color)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_path_effects(halo)
        label.set_color(text_color)

    draw_north_arrow(ax, text_color=text_color, halo=halo)
    draw_scale_bar(ax)

    return fig, ax, xlim, ylim, scale, has_imagery, text_color, halo


def add_title_block(fig, lines, author, scale, zone, target_crs, extra_source=None):
    """Standard title block: first line bold/large, rest as body text, then a footer row of
    scale/CRS/author/date/source."""
    today = datetime.now().strftime("%d %B %Y")
    fig.text(0.06, 0.16, lines[0], fontsize=16, fontweight="bold")
    y = 0.125
    for line in lines[1:]:
        fig.text(0.06, y, line, fontsize=9)
        y -= 0.025
    fig.text(0.06, 0.05, f"Scale 1:{scale:,}  (at A4)", fontsize=9)
    fig.text(0.38, 0.05, f"CRS: GDA2020 / MGA Zone {zone} ({target_crs})", fontsize=9)
    fig.text(0.68, 0.05, f"Author: {author}", fontsize=9)
    fig.text(0.68, 0.03, f"Date: {today}", fontsize=9)
    source_note = "Source: Queensland Government, Dept. of Resources"
    if extra_source:
        source_note += f"; {extra_source}"
    fig.text(0.06, 0.02, source_note, fontsize=6.5, color="dimgrey")


def _mpl_legend_handle(kind, color):
    """Build a matplotlib legend handle for a (kind, color, label) entry - the same triples
    plot_extra_layers()/draw_report_legend_box() use - so build_map()'s plain ax.legend() can
    show extra layers alongside the tenement swatch without a second legend system."""
    if kind == "line":
        return plt.Line2D([0], [0], color=color, linewidth=1.8)
    if kind == "point":
        return plt.Line2D([0], [0], marker="o", color=color, markeredgecolor="black",
                           markeredgewidth=0.4, linestyle="none")
    if kind == "outline":
        return plt.Rectangle((0, 0), 1, 1, facecolor="none", edgecolor=color, linewidth=1.4)
    return plt.Rectangle((0, 0), 1, 1, facecolor=color, edgecolor="black", linewidth=0.8)


def build_map(gdf: gpd.GeoDataFrame, author: str, forced_scale: int = None, basemap: str = "satellite",
              extra_layers=None):
    row = gdf.iloc[0]
    attrs = {k: row[k] for k in gdf.columns if k != "geometry"}

    zone, target_crs, gdf_proj = determine_zone_and_project(gdf)
    fig, ax, xlim, ylim, scale, has_imagery, text_color, halo = setup_map_frame(
        gdf_proj, target_crs, forced_scale, basemap)

    # Optional extra context layers (cadastre, nearby tenements, contours, infrastructure,
    # geology, drillholes, etc.) - drawn first so the tenement outline/fill and its label sit
    # on top of them.
    extra_legend_entries = plot_extra_layers(ax, extra_layers, xlim[0], ylim[0], xlim[1], ylim[1],
                                              target_crs, tenement_geom=gdf_proj.geometry.iloc[0],
                                              start_zorder=2.2, text_color=text_color, halo=halo)

    # Tenement boundary: solid outline always; fill is more transparent over imagery so the
    # imagery stays visible underneath.
    fill_alpha = 0.22 if has_imagery else 0.35
    gdf_proj.plot(ax=ax, facecolor="#ff00c5", edgecolor="#ffea00" if has_imagery else "#800062",
                  alpha=fill_alpha, linewidth=2.0, zorder=3)
    gdf_proj.boundary.plot(ax=ax, edgecolor="#ffea00" if has_imagery else "#800062",
                            linewidth=2.0, zorder=4)

    # Label the EPM number directly on the tenement itself so it's identifiable at a glance
    # on the map, not just in the title block below it. If the tenement is split into several
    # non-contiguous blocks, label only the largest one rather than cluttering every part.
    permit_label = attrs.get("displayname", "EPM ?")
    tenement_geom = gdf_proj.geometry.iloc[0]
    tenement_parts = tenement_geom.geoms if tenement_geom.geom_type == "MultiPolygon" else [tenement_geom]
    largest_part = max(tenement_parts, key=lambda p: p.area)
    label_pt = largest_part.representative_point()  # guaranteed to fall inside the polygon
    txt = ax.text(label_pt.x, label_pt.y, permit_label, ha="center", va="center",
                   fontsize=8.5, fontweight="bold", color="#800062", zorder=6)
    txt.set_path_effects([pe.withStroke(linewidth=3, foreground="white")])

    legend_patch = plt.Rectangle((0, 0), 1, 1, facecolor="#ff00c5",
                                  edgecolor="#ffea00" if has_imagery else "#800062",
                                  alpha=fill_alpha, linewidth=2.0)
    tenement_label = f"{attrs.get('displayname', 'EPM')} ({attrs.get('_status', '')})"
    all_entries = [("fill", "#ff00c5", tenement_label)] + list(extra_legend_entries)

    # A long extra-layer legend (e.g. a dozen-plus distinct surface-geology rock units) would
    # either overlap a meaningful chunk of the map or shrink past legibility if crammed into the
    # usual corner box - past LEGEND_MAX_INLINE_ENTRIES it gets its own page instead, and only
    # the tenement's own entry (always just one line) stays on the map itself.
    legend_fig = None
    if len(all_entries) > LEGEND_MAX_INLINE_ENTRIES:
        ax.legend([legend_patch], [tenement_label], loc="lower right", fontsize=7.5, framealpha=0.9)
        # The upper-left corner is the one spot the north arrow/scale bar/legend never use, so
        # this note can't collide with them regardless of tenement shape or map extent.
        ax.text(0.01, 0.99, "Full legend on separate page", transform=ax.transAxes,
                fontsize=7, ha="left", va="top", style="italic", color=text_color,
                path_effects=halo, zorder=6,
                bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="none", alpha=0.75))
        legend_fig = build_legend_page_figure(
            all_entries, f"Legend - {attrs.get('displayname', 'EPM')}",
            attrs.get("permitname"))
    else:
        handles = [legend_patch] + [_mpl_legend_handle(k, c) for k, c, _ in extra_legend_entries]
        labels = [tenement_label] + [lbl for _, _, lbl in extra_legend_entries]
        ax.legend(handles, labels, loc="lower right", fontsize=7.5, framealpha=0.9)

    permit_no = attrs.get("displayname", "EPM ?")
    permit_name = attrs.get("permitname") or "-"
    holder = attrs.get("authorisedholdername") or "-"
    status = attrs.get("_status") or attrs.get("permitstatus") or "-"
    minerals = attrs.get("permitminerals") or "-"
    area_ha = gdf_proj.geometry.iloc[0].area / 10000

    localities = fetch_nearby_localities(xlim[0], ylim[0], xlim[1], ylim[1], target_crs)
    nearby_line = f"Nearby localities: {', '.join(localities)}" if localities else None

    extra_source = "MinesPermitsCurrent"
    if has_imagery:
        extra_source += "; Imagery (c) Esri, Maxar, Earthstar Geographics"

    add_title_block(
        fig,
        [
            f"{permit_no} - {permit_name}",
            f"Holder: {holder}    |    Status: {status}    |    Mineral(s): {minerals}",
            f"Area: {area_ha:,.0f} ha (recalculated from boundary)" + (f"    |    {nearby_line}" if nearby_line else ""),
        ],
        author, scale, zone, target_crs, extra_source,
    )

    return fig, scale, zone, target_crs, legend_fig


def fetch_subblocks(gdf_proj: gpd.GeoDataFrame, target_crs: str) -> gpd.GeoDataFrame:
    """Fetch the official QLD graticular sub-blocks (1' lat x 1' lon each) that make up this
    tenement, from the Mining Administrative Areas service. Candidates are found by an
    envelope query, then kept only if they substantially overlap the actual tenement
    geometry (not just its bounding box) - since QLD exploration permits are legally defined
    as a union of whole sub-blocks, this recovers exactly the tenement's sub-block schedule.
    """
    minx, miny, maxx, maxy = gdf_proj.total_bounds
    sr = epsg_number(target_crs)
    url = f"{MINING_ADMIN_BASE}/{SUBBLOCK_LAYER}/query"
    params = {
        "geometry": f"{minx},{miny},{maxx},{maxy}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": sr,
        "outFields": "subblockdesc,blockno,subblockletter,bimname",
        "outSR": sr,
        "f": "json",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    feats = data.get("features", [])
    if not feats:
        raise ValueError("No sub-blocks were found covering this tenement's extent.")

    records = []
    for feat in feats:
        geom = esri_rings_to_geometry(feat["geometry"]["rings"])
        rec = dict(feat["attributes"])
        rec["geometry"] = geom
        records.append(rec)
    subblocks = gpd.GeoDataFrame(records, geometry="geometry", crs=target_crs)

    tenement_geom = gdf_proj.geometry.iloc[0]
    overlap_frac = subblocks.geometry.apply(
        lambda g: (g.intersection(tenement_geom).area / g.area) if g.area > 0 else 0
    )
    subblocks = subblocks[overlap_frac > 0.05].reset_index(drop=True)
    if subblocks.empty:
        raise ValueError("Sub-blocks were found nearby, but none actually overlap the tenement geometry.")

    def _clean_int_str(val):
        # Esri number fields sometimes come back as e.g. 291.0 instead of 291 depending on the
        # underlying field type - strip a spurious ".0" so codes match cleanly either way.
        try:
            f = float(val)
            if f == int(f):
                return str(int(f))
        except (TypeError, ValueError):
            pass
        return str(val)

    blockno_str = subblocks["blockno"].apply(_clean_int_str)
    # "bare" code = block number + sub-block letter, e.g. "291J" - what most people type.
    subblocks["bare_code"] = blockno_str + subblocks["subblockletter"].astype(str)
    # "full" code = the official code including the Block Index Map name prefix (e.g.
    # "TOWN291J"), taken straight from the government's own subblockdesc field where present,
    # since block numbers are only unique *within* a BIM, not across the whole state.
    if "subblockdesc" in subblocks.columns and subblocks["subblockdesc"].notna().all():
        subblocks["code"] = subblocks["subblockdesc"].astype(str)
    else:
        # subblockdesc wasn't returned for some reason - fall back to the bare code. Note
        # "bimname" is a human-readable name (e.g. "Townsville"), not the short code prefix
        # used in subblockdesc (e.g. "TOWN"), so it can't be used to reconstruct a real code.
        subblocks["code"] = subblocks["bare_code"]
    return subblocks


def fetch_subblocks_by_desc(codes, target_crs: str) -> gpd.GeoDataFrame:
    """Fetch specific sub-blocks by their official code (subblockdesc), with no geometry/
    overlap filter at all. This is needed for a partial relinquishment report: the sub-blocks
    being reported on have, by definition, already been dropped from the tenement by the time
    the report is compiled, so they no longer overlap the current (now smaller) EPM boundary
    and fetch_subblocks() alone will never find them. This recovers their geometry directly so
    they can still be drawn on the map and clearly marked as relinquished.
    """
    codes = [c for c in dict.fromkeys(codes) if c]  # de-duplicate, preserve order
    if not codes:
        return gpd.GeoDataFrame(
            columns=["subblockdesc", "blockno", "subblockletter", "bimname", "geometry"],
            geometry="geometry", crs=target_crs)

    sr = epsg_number(target_crs)
    url = f"{MINING_ADMIN_BASE}/{SUBBLOCK_LAYER}/query"
    quoted = ",".join("'" + c.replace("'", "''") + "'" for c in codes)
    params = {
        "where": f"UPPER(subblockdesc) IN ({quoted})",
        "outFields": "subblockdesc,blockno,subblockletter,bimname",
        "outSR": sr,
        "returnGeometry": "true",
        "f": "json",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    feats = data.get("features", [])
    if not feats:
        return gpd.GeoDataFrame(
            columns=["subblockdesc", "blockno", "subblockletter", "bimname", "geometry"],
            geometry="geometry", crs=target_crs)

    records = []
    for feat in feats:
        geom = esri_rings_to_geometry(feat["geometry"]["rings"])
        rec = dict(feat["attributes"])
        rec["geometry"] = geom
        records.append(rec)
    return gpd.GeoDataFrame(records, geometry="geometry", crs=target_crs)


def build_subblock_maps(gdf: gpd.GeoDataFrame, author: str, relinquish_codes=None,
                         forced_scale: int = None, project_name: str = None,
                         drawn_by: str = None, report_title: str = None,
                         page_number=None, company_name: str = None, context_layers: bool = True,
                         basemap: str = "none", extra_layers=None):
    """Build a single report-style sub-block map, matching a typical consultant partial-
    relinquishment report figure: sub-blocks as thin gold-outlined cells labelled by letter,
    roads/watercourses as light reference context, a bordered title-block table and legend
    box, a figure caption, and a report-style footer. `basemap` is 'none' (plain white,
    highest contrast for print), 'satellite' (Esri World Imagery) or 'greyscale' (Esri Light
    Gray Canvas, shows towns/roads without imagery's strong colour).

    With no relinquish_codes: shows the tenement's full current sub-block schedule (suitable
    for an annual report). With relinquish_codes: the same map, with the nominated sub-blocks
    filled bright red/orange with a dark outline so they read clearly as "being given up"
    against any background, including satellite imagery.
    """
    row = gdf.iloc[0]
    attrs = {k: row[k] for k in gdf.columns if k != "geometry"}
    permit_no = attrs.get("displayname", "EPM ?")
    permit_name = attrs.get("permitname") or ""
    short_name = permit_name or permit_no
    # If the caller didn't explicitly pass --company-name, pull the tenement's actual
    # authorised holder from the government data itself, so it always matches the EPM being
    # mapped rather than needing to be typed in (and rather than defaulting to whatever the
    # last EPM's holder happened to be).
    company_name = company_name or attrs.get("authorisedholdername")

    zone, target_crs, gdf_proj = determine_zone_and_project(gdf)
    subblocks = fetch_subblocks(gdf_proj, target_crs)  # already restricted to sub-blocks that
    # actually overlap the tenement - nothing outside the EPM boundary is ever included here.

    # Normalise relinquish codes against the sub-blocks actually fetched. Sub-block codes on
    # the government's own maps include a Block Index Map (BIM) name prefix (e.g. "TOWN291J"),
    # since plain block numbers repeat across different BIMs - but people naturally type the
    # short form they can see/remember (e.g. "291J", or just a letter "J" if the block is
    # obvious from context). Try, in order: exact full code -> bare "block+letter" code ->
    # bare letter (only if the tenement has one unique block number) -> match by suffix against
    # the full code (recovers cases like "291J" typed for the real code "TOWN291J").
    raw_codes = [c.strip().upper().replace(" ", "") for c in (relinquish_codes or []) if c.strip()]
    full_codes = subblocks["code"].str.upper()
    bare_codes = subblocks["bare_code"].str.upper()
    known_full = set(full_codes)
    known_bare = set(bare_codes)
    bare_to_full = {}  # only for bare codes that map to exactly one full code
    for bare, full in zip(bare_codes, full_codes):
        bare_to_full.setdefault(bare, set()).add(full)
    unique_blocknos = list(subblocks["blockno"].astype(str).unique())

    resolved_codes, unresolved, ambiguous = set(), [], []
    for c in raw_codes:
        if c in known_full:
            resolved_codes.add(c)
            continue
        if c in known_bare:
            matches = bare_to_full[c]
            if len(matches) == 1:
                resolved_codes.add(next(iter(matches)))
            else:
                ambiguous.append((c, sorted(matches)))
            continue
        if c.isalpha() and len(unique_blocknos) == 1:
            bare_guess = f"{unique_blocknos[0]}{c}"
            if bare_guess in bare_to_full:
                resolved_codes.add(next(iter(bare_to_full[bare_guess])))
                continue
        suffix_matches = [fc for fc in known_full if fc.endswith(c)]
        if len(suffix_matches) == 1:
            resolved_codes.add(suffix_matches[0])
        elif len(suffix_matches) > 1:
            ambiguous.append((c, sorted(suffix_matches)))
        else:
            unresolved.append(c)

    if ambiguous:
        for c, matches in ambiguous:
            print(f"Warning: relinquish code '{c}' matches more than one sub-block "
                  f"({', '.join(matches)}) - use the full code shown on the map/legend to "
                  f"pick the right one.")

    # Anything still unresolved at this point isn't part of the *current* tenement boundary -
    # which, for a partial relinquishment report, is exactly what's expected: the sub-blocks
    # being reported on have already been dropped from the EPM by the time the report is
    # compiled, so a geometry/overlap query against today's (smaller) boundary will never find
    # them. Fetch them directly by code instead, regardless of whether they still overlap the
    # tenement, so they can still be drawn on the map and marked as relinquished.
    already_relinquished_codes = []
    if unresolved:
        # Derive the actual short code prefix used in subblockdesc (e.g. "TOWN") by comparing
        # each current sub-block's full code against its bare "block+letter" code and taking
        # the leftover prefix - NOT the same thing as the human-readable "bimname" field (which
        # is the full name, e.g. "Townsville"), so that field can't be used to guess the code.
        code_prefixes = set()
        for code_val, bare_val in zip(full_codes, bare_codes):
            if code_val.endswith(bare_val) and len(code_val) > len(bare_val):
                code_prefixes.add(code_val[: -len(bare_val)])

        candidate_full_codes = []
        code_for_raw = {}
        for c in unresolved:
            if re.match(r"^\d+[A-Z]$", c) and len(code_prefixes) == 1:
                # bare "block+letter" (e.g. "291J") - assume the same code prefix as the rest
                # of this tenement's sub-block schedule, since a permit essentially always sits
                # within a single Block Index Map.
                guess = f"{next(iter(code_prefixes))}{c}"
            else:
                # already looks like a full code (e.g. "TOWN291J"), or the prefix is ambiguous/
                # unknown - try it exactly as typed.
                guess = c
            code_for_raw[c] = guess
            candidate_full_codes.append(guess)

        historical = fetch_subblocks_by_desc(candidate_full_codes, target_crs)
        found_full = set(historical["subblockdesc"].astype(str).str.upper()) if not historical.empty else set()

        still_missing = []
        for c in unresolved:
            guess = code_for_raw[c].upper()
            if guess in found_full:
                resolved_codes.add(guess)
                already_relinquished_codes.append(guess)
            else:
                still_missing.append(c)
        unresolved = still_missing

        if already_relinquished_codes:
            print(f"Note: {', '.join(sorted(already_relinquished_codes))} "
                  f"{'is' if len(already_relinquished_codes) == 1 else 'are'} no longer part of "
                  f"{permit_no}'s current boundary (already relinquished) - fetched directly by "
                  f"code and added to the map as relinquished sub-block(s).")

        if not historical.empty:
            new_rows = historical[historical["subblockdesc"].astype(str).str.upper().isin(already_relinquished_codes)].copy()
            if not new_rows.empty:
                blockno_str = new_rows["blockno"].apply(
                    lambda v: str(int(v)) if str(v).replace(".", "", 1).isdigit() and float(v) == int(float(v)) else str(v))
                new_rows["bare_code"] = blockno_str + new_rows["subblockletter"].astype(str)
                new_rows["code"] = new_rows["subblockdesc"].astype(str)
                # Only keep columns the current sub-blocks already have (plus geometry), so the
                # two frames line up cleanly. "relinquished" isn't included here - it gets
                # (re)computed for every row, old and new, right after this merge.
                keep_cols = [c for c in subblocks.columns if c in new_rows.columns]
                if "geometry" not in keep_cols:
                    keep_cols.append("geometry")
                subblocks = gpd.GeoDataFrame(
                    pd.concat([subblocks, new_rows[keep_cols]], ignore_index=True),
                    geometry="geometry", crs=target_crs)

    if unresolved:
        print(f"Warning: these relinquished sub-block codes weren't found within {permit_no}'s "
              f"current or historical sub-blocks and were ignored: {', '.join(unresolved)}. "
              f"Available current codes: {', '.join(sorted(known_full))}")

    subblocks["relinquished"] = subblocks["code"].str.upper().isin(resolved_codes)

    roads = fetch_roads(gdf_proj, target_crs) if context_layers else gpd.GeoDataFrame()
    watercourses = fetch_watercourses(gdf_proj, target_crs) if context_layers else gpd.GeoDataFrame()

    # Frame the map to fit the tenement AND every sub-block being shown - including any
    # already-relinquished ones recovered above, which may sit outside the current (smaller)
    # tenement boundary and would otherwise be cropped out of the map entirely.
    extent_gdf = gpd.GeoDataFrame(
        geometry=[gdf_proj.geometry.iloc[0]] + list(subblocks.geometry), crs=target_crs)
    fig, ax, xlim, ylim, scale, has_imagery, text_color, halo = setup_report_frame(
        extent_gdf, target_crs, forced_scale, basemap=basemap)

    # Optional extra context layers (cadastre, nearby tenements, contours, infrastructure,
    # geology, drillholes, etc.) - drawn first, underneath the sub-block grid/roads/watercourses.
    extra_legend_entries = plot_extra_layers(ax, extra_layers, xlim[0], ylim[0], xlim[1], ylim[1],
                                              target_crs, tenement_geom=gdf_proj.geometry.iloc[0],
                                              start_zorder=1.5, text_color=text_color, halo=halo)

    if not watercourses.empty:
        watercourses.plot(ax=ax, color="#3fa9dc", linewidth=1.3, zorder=2)
    if not roads.empty:
        roads.plot(ax=ax, color="#8b1a1a", linewidth=1.6, zorder=2.5,
                   path_effects=([pe.withStroke(linewidth=2.6, foreground="white")] if has_imagery else []))
        # label each distinct named road once, roughly along its longest visible segment
        road_label_color = text_color if has_imagery else "#5c0f0f"
        if "road_name" in roads.columns:
            for name, group in roads[roads["road_name"].notna()].groupby("road_name"):
                if not name:
                    continue
                longest = group.geometry.iloc[group.geometry.length.values.argmax()]
                pt = longest.interpolate(0.5, normalized=True)
                dx, dy = 1, 0
                try:
                    coords = list(longest.coords)
                    mid_i = len(coords) // 2
                    if len(coords) > 1:
                        dx = coords[min(mid_i + 1, len(coords) - 1)][0] - coords[mid_i][0]
                        dy = coords[min(mid_i + 1, len(coords) - 1)][1] - coords[mid_i][1]
                except Exception:
                    pass
                angle = math.degrees(math.atan2(dy, dx))
                if angle > 90 or angle < -90:
                    angle += 180
                txt = ax.text(pt.x, pt.y, name, fontsize=6.5, color=road_label_color, rotation=angle,
                               ha="center", va="bottom", zorder=6.5, style="italic", fontweight="bold")
                if halo:
                    txt.set_path_effects(halo)

    kept = subblocks[~subblocks["relinquished"]]
    relinq = subblocks[subblocks["relinquished"]]

    # Bright gold outline for every sub-block (reads clearly on white, grey or satellite
    # backgrounds), and a hot red/orange fill - not blue or another dark/muted tone that could
    # be mistaken for water, shadow or vegetation in the satellite version - for the ones
    # being relinquished, with a dark outline and hatch so it's unambiguous even in black/white.
    kept.plot(ax=ax, facecolor="none", edgecolor="#ffd400", linewidth=1.3, zorder=3)
    if not relinq.empty:
        relinq.plot(ax=ax, facecolor="#ff3b30", edgecolor="#7a0000", linewidth=2.0,
                    hatch="//", alpha=0.88, zorder=3.5)

    label_color = text_color if has_imagery else "black"
    for _, r in subblocks.iterrows():
        pt = r.geometry.representative_point()
        txt = ax.text(pt.x, pt.y, r["subblockletter"], ha="center", va="center",
                       fontsize=8.5, fontweight="bold",
                       color="white" if (r["relinquished"] and not has_imagery) else label_color, zorder=6)
        if halo or r["relinquished"]:
            txt.set_path_effects([pe.withStroke(linewidth=2.2, foreground="black" if not has_imagery else "black")])

    draw_scale_bar(ax, backing_box=has_imagery)

    legend_entries = []
    if not relinq.empty:
        legend_entries.append(("fill", "#ff3b30", "Sub-blocks for relinquishment"))
    legend_entries.append(("outline", "#ffd400", f"{short_name} sub-blocks"))
    if not roads.empty:
        legend_entries.append(("line", "#8b1a1a", "State controlled roads"))
    if not watercourses.empty:
        legend_entries.append(("line", "#3fa9dc", "Major watercourse"))
    legend_entries.extend(extra_legend_entries)

    project_name = project_name or short_name
    drawn_by = drawn_by or "".join(w[0] for w in author.split()[:2]).upper()
    date_str = datetime.now().strftime("%d/%m/%Y")

    if resolved_codes:
        title_lines = ["Sub-block Relinquishment", f"{permit_no} {permit_name}".strip()]
        caption = f"Figure 1: Location and Sub-block Map of {permit_no} {permit_name}".strip()
    else:
        title_lines = ["Sub-block Plan", f"{permit_no} {permit_name}".strip()]
        caption = f"Figure 1: Sub-block Plan of {permit_no} {permit_name}".strip()

    draw_report_title_block(fig, (0.44, 0.13, 0.48, 0.17), project_name, title_lines,
                             scale, zone, drawn_by, date_str, company_name)

    # Same overflow rule as build_map(): a legend with more than LEGEND_MAX_INLINE_ENTRIES
    # entries (typically from an extra layer like surface geology pulling in many distinct rock
    # units) doesn't fit legibly in the fixed-size legend box, so it gets its own page instead -
    # the box on the map page just points to it rather than being left blank.
    legend_fig = None
    if len(legend_entries) > LEGEND_MAX_INLINE_ENTRIES:
        draw_report_legend_box(fig, (0.08, 0.13, 0.32, 0.17), legend_entries,
                                overflow_note=f"{len(legend_entries)} entries - see separate legend page.")
        legend_fig = build_legend_page_figure(
            legend_entries, f"Legend - {permit_no} {permit_name}".strip(), report_title)
    else:
        draw_report_legend_box(fig, (0.08, 0.13, 0.32, 0.17), legend_entries)

    footer_left = f"{permit_no} {permit_name}".strip()
    footer_right = report_title or f"Partial Relinquishment Report {datetime.now().year}"
    add_figure_caption_and_footer(fig, caption, footer_left, page_number, footer_right)

    return {"map": fig, "legend": legend_fig}, subblocks, zone


def _save_pdf_with_optional_legend(map_fig, legend_fig, out_path, dpi=300):
    """Save the map as a PDF, adding the overflow legend (if any) as a second page in the same
    file rather than a separate deliverable - a plain single-page savefig when there's no
    overflow legend, unchanged from before this feature existed."""
    if legend_fig is None:
        map_fig.savefig(out_path, dpi=dpi)
        return
    with PdfPages(out_path) as pdf:
        pdf.savefig(map_fig, dpi=dpi)
        pdf.savefig(legend_fig, dpi=dpi)


def main():
    p = argparse.ArgumentParser(description="Generate a QLD EPM locality/tenement map PDF.")
    p.add_argument("--epm", help="EPM number, e.g. 'EPM 25210', '25210', 'EPM25210'")
    p.add_argument("--input", help="Local GeoJSON/shapefile with a single EPM feature (alternative to --epm)")
    p.add_argument("--author", default="Will North", help="Author name for the title block")
    p.add_argument("--scale", type=int, default=None, help="Force a specific map scale denominator, e.g. 100000")
    p.add_argument("--basemap", choices=["satellite", "none"], default="satellite",
                    help="Background imagery: 'satellite' (default, Esri World Imagery) or 'none' for a plain background")
    p.add_argument("--output", default=None, help="Output PDF path (single-map runs only)")
    p.add_argument("--subblocks", action="store_true",
                    help="Generate a report-style sub-block map instead of the standard "
                         "locality map (for annual reports / partial relinquishment reports)")
    p.add_argument("--relinquish", default=None,
                    help="Comma-separated sub-block codes being relinquished, e.g. "
                         "'2428C,2428B,2427A' or bare letters like 'D,E' if the tenement only "
                         "spans one block number (used with --subblocks; those sub-blocks are "
                         "filled bright red/orange and called out in the legend). For a partial "
                         "relinquishment report compiled after the sub-blocks have already been "
                         "dropped from the EPM, this still works - they're fetched directly by "
                         "code and drawn on the map even though they're no longer part of the "
                         "current tenement boundary.")
    p.add_argument("--subblock-basemap", choices=["satellite", "greyscale", "none", "both"], default="both",
                    help="Background for sub-block maps (used with --subblocks): 'satellite', "
                         "'greyscale' (Esri Light Gray Canvas, shows towns/roads without imagery "
                         "colours), 'none' (plain white, highest contrast for print), or 'both' "
                         "(default - saves a satellite AND a greyscale version)")
    p.add_argument("--project-name", default=None,
                    help="Short project name for the title block (defaults to the permit name)")
    p.add_argument("--drawn-by", default=None, help="Initials for the title block (defaults to your author initials)")
    p.add_argument("--report-title", default=None,
                    help="Footer text, e.g. 'Partial Relinquishment Report 2026' (used with --subblocks)")
    p.add_argument("--page-number", default=None, help="Page number for the footer (used with --subblocks)")
    p.add_argument("--company-name", default=None,
                    help="Company name shown in the title block (used with --subblocks). "
                         "If omitted, this is pulled automatically from the EPM's authorised "
                         "holder as recorded by the QLD Government for that permit.")
    p.add_argument("--no-context", action="store_true",
                    help="Skip fetching roads/watercourses context layers on sub-block maps (faster, offline-friendly)")
    p.add_argument("--extra-layers", default=None,
                    help="Comma-separated extra context layers to draw on the map, e.g. "
                         "'cadastral_parcels,nearby_tenements,contours'. Run with "
                         "--list-layers to see everything available.")
    p.add_argument("--list-layers", action="store_true",
                    help="Print the available --extra-layers keys and exit")
    args = p.parse_args()

    if args.list_layers:
        for key, entry in LAYER_CATALOG.items():
            print(f"{key:22s} {entry['label']}")
        return

    if args.input:
        gdf = load_local_gdf(args.input)
    elif args.epm:
        gdf = fetch_epm_gdf(args.epm)
    else:
        p.error("Provide either --epm EPM_NUMBER or --input path/to/file.geojson")
        return

    default_name = normalise_epm(args.epm).replace(" ", "_") if args.epm else "epm"
    extra_layers = [k.strip() for k in args.extra_layers.split(",") if k.strip()] if args.extra_layers else []
    for k in extra_layers:
        if k not in LAYER_CATALOG:
            print(f"Warning: '{k}' isn't a known extra layer. Run --list-layers to see the options.")

    if args.subblocks:
        relinquish_codes = args.relinquish.split(",") if args.relinquish else []
        basemaps = ["satellite", "greyscale"] if args.subblock_basemap == "both" else [args.subblock_basemap]

        for i, bm in enumerate(basemaps):
            figs, subblocks, zone = build_subblock_maps(
                gdf, args.author, relinquish_codes, args.scale,
                args.project_name, args.drawn_by, args.report_title,
                args.page_number, args.company_name, not args.no_context,
                basemap=bm, extra_layers=extra_layers,
            )
            n_relinq = int(subblocks["relinquished"].sum())
            suffix = "_relinquishment" if n_relinq else "_subblock_plan"
            bm_tag = f"_{bm}" if len(basemaps) > 1 else ""
            if args.output and len(basemaps) == 1:
                out = args.output
            elif args.output:
                base, dot, ext = args.output.rpartition(".")
                out = f"{base}{bm_tag}.{ext}" if dot else f"{args.output}{bm_tag}"
            else:
                out = f"{default_name}{suffix}{bm_tag}.pdf"
            _save_pdf_with_optional_legend(figs["map"], figs.get("legend"), out)
            legend_note = " + separate legend page" if figs.get("legend") is not None else ""
            if n_relinq:
                print(f"Saved {out}  (GDA2020 MGA Zone {zone}, {n_relinq} of {len(subblocks)} sub-block(s) relinquished, basemap={bm}{legend_note})")
            else:
                print(f"Saved {out}  (GDA2020 MGA Zone {zone}, {len(subblocks)} sub-block(s) total, basemap={bm}{legend_note})")
        return

    fig, scale, zone, crs, legend_fig = build_map(gdf, args.author, args.scale, args.basemap, extra_layers=extra_layers)
    out = args.output or f"{default_name}_locality_map.pdf"
    _save_pdf_with_optional_legend(fig, legend_fig, out)
    legend_note = " + separate legend page" if legend_fig is not None else ""
    print(f"Saved {out}  (scale 1:{scale:,}, GDA2020 MGA Zone {zone}, {crs}{legend_note})")


if __name__ == "__main__":
    main()
