"""DAN offline/storytelling prosody pipeline.

The package is intentionally separate from live SpeechPipeline. It preserves
full thoughts, renders deterministic candidates, rejects obvious failures,
uses fixed per-voice/profile gain, and writes a complete reproduction manifest.
"""

from .models import RenderResult, ScenePlan
from .parser import SceneParseError, parse_scene_file, parse_scene_text
from .planning import DirectorSettings, ProsodyDirector, ProsodyPlanError

__all__ = [
    "DirectorSettings",
    "ProsodyDirector",
    "ProsodyPlanError",
    "RenderResult",
    "SceneParseError",
    "ScenePlan",
    "parse_scene_file",
    "parse_scene_text",
]
