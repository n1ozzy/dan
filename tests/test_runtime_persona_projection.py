from dan.api.routes_runtime import _available_persona_profiles, _resolve_persona_profile
from dan.brain.context_builder import DEFAULT_PERSONA_PROFILE


def test_runtime_projection_exposes_only_the_shared_canonical_persona() -> None:
    assert _resolve_persona_profile(DEFAULT_PERSONA_PROFILE) == (
        DEFAULT_PERSONA_PROFILE,
        "ok",
    )
    assert _available_persona_profiles() == [DEFAULT_PERSONA_PROFILE]
