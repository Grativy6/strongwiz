"""Canonical exact and interval quantities for float-free scientific evidence."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from pydantic import Field, model_validator

from strongwiz.contracts import ContractModel, NonNegativeInt, PositiveInt


class MeasurementError(ValueError):
    pass


class ExactQuantity(ContractModel):
    schema_id: str = Field(default="strongwiz.exact-quantity.v1", alias="schema")
    numerator: int
    denominator: PositiveInt
    unit: str

    @model_validator(mode="after")
    def validate_quantity(self) -> ExactQuantity:
        if not self.unit.strip():
            raise ValueError("quantity unit is required")
        if self.numerator == 0 and self.denominator != 1:
            raise ValueError("canonical zero has denominator one")
        if math.gcd(abs(self.numerator), self.denominator) != 1:
            raise ValueError("exact quantity must be in lowest terms")
        return self

    @classmethod
    def from_decimal(cls, value: str, *, unit: str) -> ExactQuantity:
        """Parse a finite decimal string without passing through binary float."""

        try:
            decimal = Decimal(value)
        except InvalidOperation as error:
            raise MeasurementError("invalid decimal quantity") from error
        if not decimal.is_finite():
            raise MeasurementError("quantity must be finite")
        numerator, denominator = decimal.as_integer_ratio()
        return cls(numerator=numerator, denominator=denominator, unit=unit)

    def compare(self, other: ExactQuantity) -> int:
        if self.unit != other.unit:
            raise MeasurementError("cannot compare quantities with different units")
        left = self.numerator * other.denominator
        right = other.numerator * self.denominator
        return (left > right) - (left < right)


class IntervalQuantity(ContractModel):
    schema_id: str = Field(default="strongwiz.interval-quantity.v1", alias="schema")
    lower: ExactQuantity
    upper: ExactQuantity
    resolution: ExactQuantity | None = None
    method_ref: str
    evidence_refs: tuple[str, ...]
    scope_id: str
    subject_version: NonNegativeInt
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_interval(self) -> IntervalQuantity:
        if not self.method_ref or not self.scope_id or not self.evidence_refs:
            raise ValueError("measurement interval requires method, scope, and evidence")
        if self.lower.unit != self.upper.unit:
            raise ValueError("measurement interval units disagree")
        if self.lower.compare(self.upper) > 0:
            raise ValueError("measurement interval is inverted")
        if self.resolution is not None and (
            self.resolution.unit != self.lower.unit or self.resolution.numerator <= 0
        ):
            raise ValueError("measurement resolution must be positive in the same unit")
        return self

    def contains(self, value: ExactQuantity) -> bool:
        return self.lower.compare(value) <= 0 and self.upper.compare(value) >= 0
