"""Versioned, closed map specification accepted by the deterministic renderer."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from epm_locality_map import LAYER_CATALOG


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TenementSpec(ClosedModel):
    jurisdiction: Literal["AU-QLD"] = "AU-QLD"
    type: Literal["EPM"] = "EPM"
    number: int = Field(gt=0, le=9_999_999)
    source: Literal["official_live"] = "official_live"


class LayoutSpec(ClosedModel):
    author: str = Field(min_length=1, max_length=100)
    map_date: date
    company_name: str | None = Field(default=None, max_length=200)
    project_name: str | None = Field(default=None, max_length=200)
    drawn_by: str | None = Field(default=None, max_length=12)
    report_title: str | None = Field(default=None, max_length=250)
    page_number: str | None = Field(default=None, max_length=20)
    scale_denominator: int | None = Field(default=None, ge=1_000, le=10_000_000)

    @field_validator("author", "company_name", "project_name", "drawn_by", "report_title", "page_number")
    @classmethod
    def no_control_characters(cls, value):
        if value is not None and any(ord(char) < 32 for char in value):
            raise ValueError("layout text cannot contain control characters")
        return value.strip() if isinstance(value, str) else value


class OutputSpec(ClosedModel):
    format: Literal["pdf", "png"]
    dpi: int = Field(ge=72, le=600)


class TenementMapSpec(ClosedModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    tenement: TenementSpec
    map_type: Literal["locality", "annual_subblock", "partial_relinquishment"]
    basemap: Literal["none", "satellite", "greyscale"] = "none"
    context_layers: list[str] = Field(default_factory=list, max_length=9)
    relinquishment_subblocks: list[str] = Field(default_factory=list, max_length=1000)
    include_standard_context: bool = True
    layout: LayoutSpec
    outputs: list[OutputSpec] = Field(min_length=1, max_length=2)

    @field_validator("context_layers")
    @classmethod
    def known_unique_layers(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("context_layers cannot contain duplicates")
        unknown = sorted(set(values) - set(LAYER_CATALOG))
        if unknown:
            raise ValueError(f"unknown context layer(s): {', '.join(unknown)}")
        return values

    @field_validator("relinquishment_subblocks")
    @classmethod
    def valid_unique_subblocks(cls, values):
        cleaned = []
        for value in values:
            code = re.sub(r"\s+", "", value.upper())
            if not re.fullmatch(r"(?:[A-Z]{2,12})?\d{1,5}[A-Z]", code):
                raise ValueError("sub-block codes must resemble 291J or TOWN291J")
            if code not in cleaned:
                cleaned.append(code)
        return cleaned

    @model_validator(mode="after")
    def conditional_controls(self):
        if self.map_type == "locality" and self.basemap == "greyscale":
            raise ValueError("greyscale basemap is only supported for sub-block maps")
        if self.map_type == "partial_relinquishment" and not self.relinquishment_subblocks:
            raise ValueError("partial_relinquishment requires at least one sub-block code")
        if self.map_type != "partial_relinquishment" and self.relinquishment_subblocks:
            raise ValueError("relinquishment_subblocks are only valid for partial_relinquishment")
        formats = [output.format for output in self.outputs]
        if len(formats) != len(set(formats)):
            raise ValueError("only one output per format is allowed")
        return self


def tenement_map_json_schema() -> dict:
    """Stable JSON Schema for MCP/API tool registration."""
    return TenementMapSpec.model_json_schema()
