"""Versioned business vocabulary mapped onto physical schema identifiers."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SemanticFieldKind(StrEnum):
    DIMENSION = "dimension"
    METRIC = "metric"


class SemanticField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    physical_column: str = Field(min_length=1)
    kind: SemanticFieldKind
    synonyms: tuple[str, ...] = ()
    unit: str | None = None
    description: str | None = None

    @field_validator("synonyms")
    @classmethod
    def validate_synonyms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("semantic synonyms must not be empty")
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("semantic synonyms must be unique")
        return values


class SemanticTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    physical_table: str = Field(min_length=1)
    synonyms: tuple[str, ...] = ()
    fields: tuple[SemanticField, ...] = ()


class SemanticLayer(BaseModel):
    """Explicit business vocabulary; no embeddings or fuzzy resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    tables: tuple[SemanticTable, ...] = ()

    def resolve_table(self, requested: str) -> str | None:
        token = requested.casefold()
        matches = {
            table.physical_table
            for table in self.tables
            if token in {
                table.name.casefold(),
                table.physical_table.casefold(),
                *(item.casefold() for item in table.synonyms),
            }
        }
        if len(matches) > 1:
            raise ValueError(f"ambiguous semantic table reference: {requested}")
        return next(iter(matches), None)

    def resolve_field(self, physical_table: str, requested: str) -> SemanticField | None:
        token = requested.casefold()
        matches = [
            field
            for table in self.tables
            if table.physical_table.casefold() == physical_table.casefold()
            for field in table.fields
            if token == field.name.casefold()
            or token in {item.casefold() for item in field.synonyms}
        ]
        physical_columns = {item.physical_column.casefold() for item in matches}
        if len(physical_columns) > 1:
            raise ValueError(f"ambiguous semantic field reference: {requested}")
        return matches[0] if matches else None
