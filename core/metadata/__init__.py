"""Publieke metadata- en finalisatie-API."""

from .finalizer import MetadataFinalizer
from .models import FinalizationResult, MetadataConfig, TrackMetadata

__all__ = ["MetadataConfig", "MetadataFinalizer", "FinalizationResult", "TrackMetadata"]
