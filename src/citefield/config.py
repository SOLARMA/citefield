"""Pydantic models for the YAML config, and the pint unit registry."""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, field_validator, model_validator

if TYPE_CHECKING:
    import pint

FieldType = Literal["number", "integer", "string", "boolean", "enum", "date"]
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
NumberLocale = Literal["auto", "en", "it", "de"]

FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


class FieldDef(BaseModel):
    name: str
    description: str
    type: FieldType = "string"
    unit: str | None = None
    values: list[str] | None = None
    aliases: list[str] = []
    examples: list[str] = []

    @field_validator("name")
    @classmethod
    def _snake_case(cls, name: str) -> str:
        if not FIELD_NAME_RE.match(name):
            raise ValueError(f"field name {name!r} must be snake_case")
        return name

    @model_validator(mode="after")
    def _type_rules(self) -> FieldDef:
        if self.type == "enum" and not self.values:
            raise ValueError(f"enum field {self.name!r} needs `values`")
        if self.type != "enum" and self.values is not None:
            raise ValueError(f"field {self.name!r}: `values` is only for enum fields")
        if self.unit is not None and self.type not in ("number", "integer"):
            raise ValueError(f"field {self.name!r}: `unit` is only for number and integer fields")
        return self


class LLMSettings(BaseModel):
    model: str | None = None
    temperature: float | None = 0  # None: not sent, for models that don't accept it
    reasoning_effort: ReasoningEffort | None = None  # None: not sent, the provider's default
    provider: dict[str, Any] | None = None  # OpenRouter provider routing, for openrouter/ models
    max_retries: int = 2

    @model_validator(mode="after")
    def _provider_needs_openrouter(self) -> LLMSettings:
        if self.provider is not None and not (self.model or "").startswith("openrouter/"):
            raise ValueError("settings.llm.provider is only for openrouter/ models")
        return self


class ChunkingSettings(BaseModel):
    max_input_tokens: int | Literal["auto"] = "auto"
    overlap_units: int = 1


class ExtractionSettings(BaseModel):
    field_batch_size: int = 30
    n_runs: int = 1
    number_locale: NumberLocale = "auto"


class ProposalSettings(BaseModel):
    enabled: bool = True
    max_per_document: int = 10


class VerificationSettings(BaseModel):
    fuzzy_min_ratio: float = 0.85
    page_window: int = 1


class Settings(BaseModel):
    parser: str = "pdfplumber"
    llm: LLMSettings = LLMSettings()
    chunking: ChunkingSettings = ChunkingSettings()
    extraction: ExtractionSettings = ExtractionSettings()
    proposals: ProposalSettings = ProposalSettings()
    verification: VerificationSettings = VerificationSettings()


class Config(BaseModel):
    schema_version: Literal[1] = 1
    units: list[str] = []
    fields: list[FieldDef]
    settings: Settings = Settings()

    @model_validator(mode="after")
    def _check(self) -> Config:
        seen: set[str] = set()
        for field in self.fields:
            if field.name in seen:
                raise ValueError(f"duplicate field name {field.name!r}")
            seen.add(field.name)
        unit_fields = [f for f in self.fields if f.unit is not None]
        if not (self.units or unit_fields):
            return self  # no units in use: skip building a pint registry
        registry = unit_registry(tuple(self.units))
        for field in unit_fields:
            try:
                registry.parse_units(field.unit)
            except Exception as exc:
                raise ValueError(f"field {field.name!r}: unknown unit {field.unit!r}") from exc
        return self

    def schema_hash(self) -> str:
        """Hash of the normalized `fields` and `units`."""
        return stable_hash(
            {"fields": [f.model_dump(mode="json") for f in self.fields], "units": self.units}
        )

    def settings_hash(self) -> str:
        """Hash of the settings that change results: parser, model, chunking and extraction."""
        s = self.settings
        return stable_hash(
            {
                "parser": s.parser,
                "llm": _llm_hash_input(s.llm),
                "chunking": s.chunking.model_dump(mode="json"),
                "extraction": s.extraction.model_dump(mode="json"),
            }
        )


def _llm_hash_input(llm: LLMSettings) -> dict[str, Any]:
    data: dict[str, Any] = {"model": llm.model, "temperature": llm.temperature}
    # Added only when set, so hashes recorded before these settings existed still match.
    for key in ("reasoning_effort", "provider"):
        if getattr(llm, key) is not None:
            data[key] = getattr(llm, key)
    return data


def stable_hash(data: object) -> str:
    """SHA-256 of the canonical JSON form of `data`."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@lru_cache(maxsize=8)
def unit_registry(definitions: tuple[str, ...] = ()) -> pint.UnitRegistry:
    """A pint registry with the config's extra unit definitions."""
    import pint  # slow to import; only needed when a config uses units

    registry = pint.UnitRegistry()
    for definition in definitions:
        try:
            registry.define(definition)
        except Exception as exc:
            raise ValueError(f"invalid unit definition {definition!r}: {exc}") from exc
    return registry


def load_config(path: str | Path) -> Config:
    """Load and validate a YAML config, before any LLM call.

    Raises `OSError` if the file can't be read, `yaml.YAMLError` if it isn't valid YAML,
    and `pydantic.ValidationError` if it breaks the schema.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Config.model_validate(data)
