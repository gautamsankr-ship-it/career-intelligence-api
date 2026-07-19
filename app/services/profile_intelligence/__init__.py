"""
Profile Intelligence Package

This package builds and serves the intelligent candidate profile.

Modules
-------

Capability Graph
    Defines professional capability relationships.

Capability Inference
    Infers additional capabilities from explicit evidence.

Profile Builder
    Builds the intelligence profile from the master profile.

Profile Cache
    Rebuilds the intelligence profile only when required.

Profile Matcher
    Provides a simple interface for querying the intelligence profile.
"""

from .capability_graph import CAPABILITY_GRAPH
from .capability_inference import CapabilityInference
from .profile_builder import ProfileBuilder
from .profile_cache import ProfileCache
from .profile_matcher import ProfileMatcher

__all__ = [

    "CAPABILITY_GRAPH",

    "CapabilityInference",

    "ProfileBuilder",

    "ProfileCache",

    "ProfileMatcher",

]