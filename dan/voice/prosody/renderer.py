"""Quality-oriented offline/storytelling renderer built on DAN's voice stack."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dan.voice.assets import VersionedVoiceCatalog, load_voice_catalog
from dan.voice.models import RenderSnapshot, SpeechIntent
from dan.voice.tts import TTSEngineError, build_tts_engine

from .audio import (
    AudioProcessingError,
    atomic_write,
    concatenate_wavs,
    postprocess_selected_wav,
    sha256_bytes,
)
from .models import (
    RenderResult,
    ScenePlan,
    SegmentPlan,
    SelectedSegment,
    TakeCandidate,
    UtterancePlan,
)
from .parser import parse_scene_file, parse_scene_text
from .planning import DirectorSettings, ProsodyDirector, ProsodyPlanError
from .quality import TakeSelectionError, analyze_take, select_best_take


_LOGGER = logging.getLogger(__name__)
_RENDERER_VERSION = "2.1"


class ProsodyRenderError(RuntimeError):
    """Offline scene rendering stopped without claiming a false success."""


class OfflineProsodyRenderer:
    """Render a deterministic scene without touching the live speech path."""

    def __init__(
        self,
        *,
        config: Any,
        repo_root: str | Path | None = None,
        voice_root: str | Path | None = None,
        settings: DirectorSettings | None = None,
        ffmpeg_binary: str | None = None,
    ) -> None:
        self.config = config
        self.repo_root = (
            Path(repo_root).expanduser().resolve()
            if repo_root is not None
            else Path(__file__).resolve().parents[3]
        )
        self.voice_root = (
            Path(voice_root).expanduser().resolve()
            if voice_root is not None
            else self.repo_root / "config" / "voice"
        )
        self.catalog: VersionedVoiceCatalog = load_voice_catalog(self.voice_root)
        engine_limit = int(
            getattr(config.voice, "supertonic_serve_max_chunk_length", 400)
        )
        if engine_limit <= 0:
            raise ProsodyPlanError("Supertonic max_chunk_length must be positive")
        director_settings = settings or DirectorSettings()
        if settings is None and director_settings.hard_max_chars > engine_limit:
            director_settings = replace(
                director_settings,
                hard_max_chars=engine_limit,
            )
        if director_settings.hard_max_chars > engine_limit:
            raise ProsodyPlanError(
                "offline hard_max_chars exceeds Supertonic max_chunk_length: "
                f"{director_settings.hard_max_chars} > {engine_limit}"
            )
        self.director = ProsodyDirector(
            self.catalog.personas, settings=director_settings
        )
        # Resolver/engine construction is intentionally deferred until render.
        # `--plan-only` therefore validates authored prosody without requiring
        # model assets or starting any audio execution boundary.
        self.ffmpeg_binary = str(
            ffmpeg_binary
            or getattr(config.voice, "mastering_binary", "ffmpeg")
            or "ffmpeg"
        )

    def plan_file(self, scene_path: str | Path) -> ScenePlan:
        path = Path(scene_path).expanduser().resolve()
        lines = parse_scene_file(path)
        return self.director.plan(lines, source_name=str(path))

    def render_file(
        self,
        scene_path: str | Path,
        *,
        output_dir: str | Path,
        manual_selections: Mapping[str, int] | None = None,
        overwrite: bool = False,
    ) -> RenderResult:
        path = Path(scene_path).expanduser().resolve()
        try:
            source_bytes = path.read_bytes()
            source_text = source_bytes.decode("utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            raise ProsodyRenderError(f"could not read scene {path}: {exc}") from exc
        # One file read owns both the plan and the recorded source hash. A
        # concurrent editor cannot make the manifest describe different bytes
        # than the text that was actually planned.
        lines = parse_scene_text(source_text, source_name=str(path))
        plan = self.director.plan(lines, source_name=str(path))
        return self.render_plan(
            plan,
            source_bytes=source_bytes,
            output_dir=output_dir,
            manual_selections=manual_selections,
            overwrite=overwrite,
        )

    def render_plan(
        self,
        plan: ScenePlan,
        *,
        source_bytes: bytes,
        output_dir: str | Path,
        manual_selections: Mapping[str, int] | None = None,
        overwrite: bool = False,
    ) -> RenderResult:
        if plan.schema_version != 2:
            raise ProsodyRenderError(
                f"unsupported scene plan schema: {plan.schema_version}"
            )
        output = Path(output_dir).expanduser().resolve()
        _prepare_output_directory(output, overwrite=overwrite)
        candidates_root = output / "candidates"
        selected_root = output / "selected"
        utterances_root = output / "utterances"
        for directory in (candidates_root, selected_root, utterances_root):
            directory.mkdir(parents=True, exist_ok=True)

        plan_path = output / "plan.json"
        manifest_path = output / "manifest.json"
        final_path = output / "scene.wav"
        source_copy_path = output / "source.scene.txt"
        _write_json(plan_path, plan.to_dict())
        atomic_write(source_copy_path, source_bytes)

        selections = dict(manual_selections or {})
        unknown_manual = set(selections) - {
            segment.id for utterance in plan.utterances for segment in utterance.segments
        }
        if unknown_manual:
            raise ProsodyRenderError(
                "manual selection refers to unknown segment(s): "
                + ", ".join(sorted(unknown_manual))
            )

        from .integration import build_offline_voice_resolver

        resolver = build_offline_voice_resolver(
            self.config,
            repo_root=self.repo_root,
            voice_root=self.voice_root,
        )
        engine = build_tts_engine(
            "supertonic",
            config=self.config,
            # Offline can reuse the configured server but must not secretly
            # create a second owner when dand already supervises it.
            serve_autostart=False,
        )
        selected_segments: list[SelectedSegment] = []
        utterance_paths: list[Path] = []
        utterance_audio: list[bytes] = []
        render_errors: dict[str, list[dict[str, Any]]] = {}
        warnings: list[str] = []

        try:
            for utterance in plan.utterances:
                segment_payloads: list[tuple[bytes, float]] = []
                for segment in utterance.segments:
                    chosen, payload, errors, segment_warnings = self._render_segment(
                        engine=engine,
                        resolver=resolver,
                        utterance=utterance,
                        segment=segment,
                        candidates_dir=candidates_root / segment.id,
                        selected_dir=selected_root,
                        preferred_seed=selections.get(segment.id),
                    )
                    selected_segments.append(chosen)
                    render_errors[segment.id] = errors
                    warnings.extend(segment_warnings)
                    segment_payloads.append((payload, segment.internal_gap_after))

                # The authored pause belongs to the complete thought, never to
                # every technical chunk. Replace the final internal gap with it.
                if not segment_payloads:
                    raise ProsodyRenderError(f"utterance {utterance.id} has no audio")
                last_payload, _ = segment_payloads[-1]
                segment_payloads[-1] = (last_payload, utterance.pause_after)
                utterance_wav = concatenate_wavs(segment_payloads)
                utterance_path = (
                    utterances_root
                    / f"{utterance.index + 1:03d}-{utterance.persona}.wav"
                )
                atomic_write(utterance_path, utterance_wav)
                utterance_paths.append(utterance_path)
                utterance_audio.append(utterance_wav)
        finally:
            close = getattr(engine, "close", None)
            if callable(close):
                close()

        scene_parts: list[tuple[bytes, float]] = []
        for index, wav_payload in enumerate(utterance_audio):
            next_gap = (
                plan.utterances[index + 1].gap_before
                if index + 1 < len(plan.utterances)
                else 0.0
            )
            scene_parts.append((wav_payload, next_gap))
        scene_wav = concatenate_wavs(
            scene_parts,
            leading_silence_seconds=plan.utterances[0].gap_before,
        )
        atomic_write(final_path, scene_wav)
        final_hash = sha256_bytes(scene_wav)

        manifest = {
            "schema_version": 2,
            "renderer_version": _RENDERER_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "source": {
                "name": plan.source_name,
                "copy": source_copy_path.name,
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
            },
            "repository": _git_identity(self.repo_root),
            "scope": "offline_storytelling_only",
            "engine_settings": {
                "name": "supertonic",
                "model": str(getattr(self.config.voice, "supertonic_serve_model", "supertonic-3")),
                "language": str(getattr(self.config.voice, "supertonic_lang", "pl")),
                "steps": int(getattr(self.config.voice, "supertonic_steps", 14)),
                "max_chunk_length": int(
                    getattr(self.config.voice, "supertonic_serve_max_chunk_length", 400)
                ),
                "seeded_wrapper": True,
            },
            "plan": plan.to_dict(),
            "voice_catalog": {
                "root": str(self.voice_root),
                "revision": self.catalog.voice_catalog.revision,
            },
            "mastering": {
                "normalization": "fixed_gain_per_voice_profile_plus_peak_limiter",
                "gain_units": "dB",
                "per_utterance_loudnorm": False,
                "ffmpeg": shutil.which(self.ffmpeg_binary),
            },
            "selected_segments": [
                _selected_segment_manifest_row(item, output_root=output)
                for item in selected_segments
            ],
            "render_errors": render_errors,
            "warnings": sorted(set(warnings)),
            "utterance_files": [
                _relative_manifest_path(path, output_root=output)
                for path in utterance_paths
            ],
            "final": {
                "path": _relative_manifest_path(final_path, output_root=output),
                "sha256": final_hash,
            },
        }
        _write_json(manifest_path, manifest)
        return RenderResult(
            output_dir=output,
            plan_path=plan_path,
            manifest_path=manifest_path,
            final_wav_path=final_path,
            final_wav_sha256=final_hash,
            utterance_paths=tuple(utterance_paths),
            selected_segments=tuple(selected_segments),
        )

    def replay_manifest(
        self,
        manifest_path: str | Path,
        *,
        output_dir: str | Path,
        overwrite: bool = False,
    ) -> RenderResult:
        source_manifest = Path(manifest_path).expanduser().resolve()
        try:
            payload = json.loads(source_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProsodyRenderError(
                f"could not load render manifest {source_manifest}: {exc}"
            ) from exc
        if not isinstance(payload, dict) or payload.get("scope") != "offline_storytelling_only":
            raise ProsodyRenderError("manifest is not a DAN offline prosody render")
        try:
            plan = ScenePlan.from_dict(payload["plan"])
            selected_rows = payload["selected_segments"]
            expected_hash = str(payload["final"]["sha256"])
            source_info = payload["source"]
            expected_catalog_revision = str(payload["voice_catalog"]["revision"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProsodyRenderError(f"manifest is incomplete: {exc}") from exc
        if self.catalog.voice_catalog.revision != expected_catalog_revision:
            raise ProsodyRenderError(
                "voice catalog revision changed; exact replay is unsafe: "
                f"expected {expected_catalog_revision}, got "
                f"{self.catalog.voice_catalog.revision}"
            )
        _verify_engine_settings(payload.get("engine_settings", {}), self.config)

        source_copy = Path(str(source_info.get("copy", ""))).expanduser()
        if not source_copy.is_absolute():
            source_copy = source_manifest.parent / source_copy
        try:
            source_bytes = source_copy.read_bytes()
        except OSError as exc:
            raise ProsodyRenderError(
                f"manifest source copy is unavailable: {source_copy}: {exc}"
            ) from exc
        actual_source_hash = hashlib.sha256(source_bytes).hexdigest()
        if actual_source_hash != str(source_info.get("sha256", "")):
            raise ProsodyRenderError(
                "manifest source copy SHA-256 mismatch: "
                f"expected {source_info.get('sha256')}, got {actual_source_hash}"
            )

        manual = {
            str(row["segment_id"]): int(row["seed"])
            for row in selected_rows
            if isinstance(row, dict)
        }
        result = self.render_plan(
            plan,
            source_bytes=source_bytes,
            output_dir=output_dir,
            manual_selections=manual,
            overwrite=overwrite,
        )
        old_snapshots = {
            str(row["segment_id"]): str(row.get("snapshot_json", ""))
            for row in selected_rows
            if isinstance(row, dict)
        }
        old_directed_snapshots = {
            str(row["segment_id"]): str(row.get("directed_snapshot_json", ""))
            for row in selected_rows
            if isinstance(row, dict)
        }
        new_snapshots = {
            row.segment_id: row.snapshot_json for row in result.selected_segments
        }
        new_directed_snapshots = {
            row.segment_id: row.directed_snapshot_json
            for row in result.selected_segments
        }
        changed = sorted(
            segment_id
            for segment_id, snapshot_json in old_snapshots.items()
            if (
                new_snapshots.get(segment_id) != snapshot_json
                or new_directed_snapshots.get(segment_id)
                != old_directed_snapshots.get(segment_id)
            )
        )
        if changed:
            raise ProsodyRenderError(
                "render snapshot changed during replay for: " + ", ".join(changed)
            )
        if result.final_wav_sha256 != expected_hash:
            raise ProsodyRenderError(
                "replay completed but final WAV differs: "
                f"expected {expected_hash}, got {result.final_wav_sha256}; "
                f"diagnostic output kept at {result.output_dir}"
            )
        return result

    def _render_segment(
        self,
        *,
        engine: Any,
        resolver: Any,
        utterance: UtterancePlan,
        segment: SegmentPlan,
        candidates_dir: Path,
        selected_dir: Path,
        preferred_seed: int | None,
    ) -> tuple[SelectedSegment, bytes, list[dict[str, Any]], list[str]]:
        candidates_dir.mkdir(parents=True, exist_ok=True)
        is_final_segment = segment.index == len(utterance.segments) - 1
        authored_pause_after = utterance.pause_after if is_final_segment else 0.0
        intent = SpeechIntent(
            text=segment.text,
            persona=utterance.persona,
            source="prosody_offline",
            session="prosody_offline",
            participant=utterance.persona,
            priority=0,
            lane="background",
            interrupt_policy="finish_current",
            utterance_index=utterance.index,
            tempo=segment.tempo_start,
            tempo_end=segment.tempo_end,
            emotion=utterance.emotion,
            tone=utterance.tone,
            pause_after=authored_pause_after,
        )
        resolved = resolver.resolve(intent)
        if abs(resolved.speed - segment.effective_speed) > 1e-6:
            raise ProsodyRenderError(
                f"resolver speed drift for {segment.id}: plan={segment.effective_speed}, "
                f"snapshot={resolved.speed}"
            )

        gain_db = self.catalog.gain_for(
            utterance.voice,
            utterance.mastering_profile,
        )
        segment_warnings: list[str] = []
        if gain_db is None:
            raise ProsodyRenderError(
                f"{segment.id}: missing calibrated gain for "
                f"{utterance.voice}|{utterance.mastering_profile}"
            )

        candidates: list[TakeCandidate] = []
        errors: list[dict[str, Any]] = []
        for seed in segment.seed_candidates:
            raw_path = candidates_dir / f"seed-{seed:010d}.raw.wav"
            raw_snapshot = _raw_candidate_snapshot(resolved, seed=seed)
            try:
                chunk = engine.synthesize(segment.text, raw_snapshot)
                atomic_write(raw_path, chunk.audio)
                metrics = analyze_take(
                    chunk.audio,
                    text=segment.text,
                )
                preview = postprocess_selected_wav(
                    chunk.audio,
                    mastering_profile=utterance.mastering_profile,
                    gain_db=float(gain_db),
                    dsp_chain=utterance.dsp,
                    ffmpeg_binary=self.ffmpeg_binary,
                )
                preview_path = candidates_dir / f"seed-{seed:010d}.preview.wav"
                atomic_write(preview_path, preview)
                candidates.append(
                    TakeCandidate(
                        seed=seed,
                        raw_path=raw_path,
                        raw_sha256=sha256_bytes(chunk.audio),
                        preview_path=preview_path,
                        preview_sha256=sha256_bytes(preview),
                        metrics=metrics,
                    )
                )
            except (TTSEngineError, AudioProcessingError, OSError, ValueError) as exc:
                errors.append({"seed": seed, "error": f"{type(exc).__name__}: {exc}"})
                _LOGGER.warning("prosody seed %s failed for %s: %s", seed, segment.id, exc)

        try:
            selected, reason = select_best_take(
                candidates,
                preferred_seed=preferred_seed,
            )
        except TakeSelectionError as exc:
            raise ProsodyRenderError(f"segment {segment.id}: {exc}") from exc

        processed = selected.preview_path.read_bytes()
        processed_path = selected_dir / f"{segment.id}.seed-{selected.seed}.wav"
        atomic_write(processed_path, processed)

        marked_candidates = tuple(
            replace(
                candidate,
                selected=candidate.seed == selected.seed,
                selection_reason=reason if candidate.seed == selected.seed else "",
            )
            for candidate in candidates
        )
        selected_snapshot = _raw_candidate_snapshot(resolved, seed=selected.seed)
        chosen = SelectedSegment(
            segment_id=segment.id,
            seed=selected.seed,
            raw_path=selected.raw_path,
            processed_path=processed_path,
            raw_sha256=selected.raw_sha256,
            processed_sha256=sha256_bytes(processed),
            text_sha256=hashlib.sha256(segment.text.encode("utf-8")).hexdigest(),
            directed_snapshot_json=resolved.canonical_json(),
            snapshot_json=selected_snapshot.canonical_json(),
            gain_db=float(gain_db),
            mastering_profile=utterance.mastering_profile,
            selection_reason=reason,
            candidates=marked_candidates,
        )
        return chosen, processed, errors, segment_warnings


def _raw_candidate_snapshot(snapshot: RenderSnapshot, *, seed: int) -> RenderSnapshot:
    # Supertonic and the explicit prosody stage receive the authored direction.
    # Only mastering/persona DSP and the trailing pause are deferred so each
    # candidate can be compared from the same directed render and assembled
    # without normalizing every line independently.
    candidate = replace(
        snapshot,
        seed=seed,
        mastering_profile="none",
        dsp="none",
        gain=1.0,
        pause_after=0.0,
    )
    candidate.validate_complete()
    return candidate


def _relative_manifest_path(path: Path, *, output_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(output_root.resolve()))
    except ValueError:
        return str(path)


def _selected_segment_manifest_row(
    selected: SelectedSegment,
    *,
    output_root: Path,
) -> dict[str, Any]:
    payload = selected.to_dict()
    for key in ("raw_path", "processed_path"):
        payload[key] = _relative_manifest_path(
            Path(str(payload[key])), output_root=output_root
        )
    candidates = payload.get("candidates", [])
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in ("raw_path", "preview_path"):
                candidate[key] = _relative_manifest_path(
                    Path(str(candidate[key])), output_root=output_root
                )
    return payload


def _prepare_output_directory(path: Path, *, overwrite: bool) -> None:
    if path.exists():
        if not overwrite and any(path.iterdir()):
            raise ProsodyRenderError(
                f"output directory is not empty: {path}; use --overwrite explicitly"
            )
        if overwrite:
            shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    atomic_write(path, encoded)


def _verify_engine_settings(expected: Any, config: Any) -> None:
    if not isinstance(expected, Mapping):
        raise ProsodyRenderError("manifest engine settings are missing")
    current = {
        "name": "supertonic",
        "model": str(getattr(config.voice, "supertonic_serve_model", "supertonic-3")),
        "language": str(getattr(config.voice, "supertonic_lang", "pl")),
        "steps": int(getattr(config.voice, "supertonic_steps", 14)),
        "max_chunk_length": int(
            getattr(config.voice, "supertonic_serve_max_chunk_length", 400)
        ),
        "seeded_wrapper": True,
    }
    mismatches = [
        f"{key}: expected {expected.get(key)!r}, got {value!r}"
        for key, value in current.items()
        if expected.get(key) != value
    ]
    if mismatches:
        raise ProsodyRenderError(
            "engine settings changed; exact replay is unsafe: " + "; ".join(mismatches)
        )


def _git_identity(repo_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repo_root), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        value = completed.stdout.strip()
        return value if completed.returncode == 0 and value else None

    return {
        "root": str(repo_root),
        "branch": run("branch", "--show-current"),
        "commit": run("rev-parse", "HEAD"),
        "dirty": bool(run("status", "--porcelain")),
    }


def default_output_dir(scene_path: str | Path, *, runtime_dir: str | Path) -> Path:
    source = Path(scene_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(runtime_dir).expanduser() / "prosody" / f"{source.stem}-{stamp}"


__all__ = [
    "OfflineProsodyRenderer",
    "ProsodyRenderError",
    "default_output_dir",
]
