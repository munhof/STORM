"""Dependency-free components intended for examples, tests and smoke checks."""

from storm.testing.models import (
    ConstantModel,
    IdentityModel,
    MeanRegressor,
    register_dummy_models,
)
from storm.testing.steps import IdentityStep, ScaleStep

__all__ = [
    "ConstantModel",
    "IdentityModel",
    "IdentityStep",
    "MeanRegressor",
    "ScaleStep",
    "register_dummy_models",
]
