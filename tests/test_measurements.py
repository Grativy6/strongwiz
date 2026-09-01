from __future__ import annotations

import pytest
from pydantic import ValidationError

from strongwiz.measurements import (
    ExactQuantity,
    IntervalQuantity,
    MeasurementError,
)
from tests.support import ref


def test_decimal_measurements_are_exact_float_free_and_canonical() -> None:
    quantity = ExactQuantity.from_decimal("0.125", unit="mol/L")
    assert (quantity.numerator, quantity.denominator) == (1, 8)
    assert quantity.model_dump(mode="json")["numerator"] == 1
    with pytest.raises(MeasurementError, match="finite"):
        ExactQuantity.from_decimal("NaN", unit="mol/L")
    with pytest.raises(ValidationError, match="lowest terms"):
        ExactQuantity(numerator=2, denominator=4, unit="mol/L")


def test_interval_preserves_method_evidence_resolution_and_exact_order() -> None:
    lower = ExactQuantity.from_decimal("1.20", unit="angstrom")
    upper = ExactQuantity.from_decimal("1.25", unit="angstrom")
    interval = IntervalQuantity(
        lower=lower,
        upper=upper,
        resolution=ExactQuantity.from_decimal("0.01", unit="angstrom"),
        method_ref=ref("assay-v1"),
        evidence_refs=(ref("measurement"),),
        scope_id="protein-candidate-7",
        subject_version=0,
        limitations=("instrument calibration is externally supplied",),
    )
    assert interval.contains(ExactQuantity.from_decimal("1.23", unit="angstrom"))
    assert not interval.contains(ExactQuantity.from_decimal("1.30", unit="angstrom"))
    with pytest.raises(MeasurementError, match="different units"):
        interval.contains(ExactQuantity.from_decimal("1.23", unit="nm"))


def test_measurement_contract_rejects_noncanonical_or_unbound_intervals() -> None:
    with pytest.raises(MeasurementError, match="invalid decimal"):
        ExactQuantity.from_decimal("not-a-number", unit="m")
    with pytest.raises(ValidationError, match="unit is required"):
        ExactQuantity(numerator=1, denominator=1, unit=" ")
    with pytest.raises(ValidationError, match="canonical zero"):
        ExactQuantity(numerator=0, denominator=2, unit="m")

    lower = ExactQuantity(numerator=1, denominator=1, unit="m")
    upper = ExactQuantity(numerator=2, denominator=1, unit="m")
    required = {
        "lower": lower,
        "upper": upper,
        "method_ref": ref("method"),
        "evidence_refs": (ref("evidence"),),
        "scope_id": "scope",
        "subject_version": 0,
    }
    with pytest.raises(ValidationError, match="method, scope, and evidence"):
        IntervalQuantity.model_validate({**required, "evidence_refs": ()})
    with pytest.raises(ValidationError, match="units disagree"):
        IntervalQuantity.model_validate(
            {**required, "upper": ExactQuantity(numerator=2, denominator=1, unit="s")}
        )
    with pytest.raises(ValidationError, match="inverted"):
        IntervalQuantity.model_validate({**required, "lower": upper, "upper": lower})
    with pytest.raises(ValidationError, match="resolution"):
        IntervalQuantity.model_validate(
            {
                **required,
                "resolution": ExactQuantity(numerator=-1, denominator=1, unit="m"),
            }
        )
