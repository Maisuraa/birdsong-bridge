from __future__ import annotations
import os, sys, tempfile, uuid, json
from typing import List

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from google.adk.runners import InMemoryRunner
from google.genai import types

# abspath is critical on Windows with uvicorn --reload (multiprocessing).
_BACKEND_DIR  = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "mcp_server"))

from app.agent import app as identify_adk_app, confirm_app as confirm_adk_app
from app.soundscape_agent import soundscape_app, SoundscapeBrief
from app import journal_store

api = FastAPI(title="BirdSong Bridge API")
api.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Each InMemoryRunner owns its OWN internal session_service. Sessions MUST be
# created on that same runner's session_service before run_async is called.
_identify_runner   = InMemoryRunner(app=identify_adk_app)
_confirm_runner    = InMemoryRunner(app=confirm_adk_app)
_soundscape_runner = InMemoryRunner(app=soundscape_app)

MAX_AUDIO_BYTES = 15 * 1024 * 1024


# ── shared runner helper ────────────────────────────────────────────────

async def _run_workflow(runner: InMemoryRunner, app_name: str, initial_state: dict) -> dict:
    """Run an ADK workflow. THE SESSION MUST BE CREATED FIRST — InMemoryRunner
    does not auto-create sessions; skipping this raises SessionNotFoundError
    (which manifests as the request hanging / never returning)."""
    user_id    = "birdsong-bridge-user"
    session_id = str(uuid.uuid4())

    # >>> THIS LINE IS THE FIX — do not remove it <<<
    await runner.session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )

    last_output = None
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text="identify")]),
        state_delta={"request": initial_state},
    ):
        if event.output is not None:
            last_output = event.output
        elif event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text and part.text.strip().startswith("{"):
                    try:
                        last_output = json.loads(part.text)
                    except Exception:
                        pass

    if last_output is None:
        return {"error": "workflow produced no output — check GOOGLE_API_KEY is set and the model is reachable"}
    return last_output if isinstance(last_output, dict) else {"result": str(last_output)}


# ── identify ────────────────────────────────────────────────────────────

@api.post("/api/identify")
async def identify(
    audio:       UploadFile    = File(...),
    place_name:  str           = Form(...),
    city_region: str           = Form(""),
    lat:         float | None  = Form(None),
    lon:         float | None  = Form(None),
    obs_date:    str | None    = Form(None),
    ecosystem:   str           = Form("other"),
    user_notes:  str           = Form(""),
):
    raw = await audio.read()
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio file too large (max 15 MB)")

    suffix = os.path.splitext(audio.filename or "clip.wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name

    try:
        result = await _run_workflow(
            _identify_runner,
            identify_adk_app.name,
            {
                # intake_node reads audio_path OR audio_file OR audio_file_path
                "audio_path":  tmp_path,
                "place_name":  place_name,
                "city_region": city_region,
                "lat": lat, "lon": lon,
                "obs_date":    obs_date or None,
                "ecosystem":   ecosystem,
                "user_notes":  user_notes,
            },
        )
    finally:
        os.unlink(tmp_path)
    return result


@api.post("/api/identify/confirm")
async def identify_confirm(
    common_name:     str          = Form(...),
    scientific_name: str          = Form(...),
    place_name:      str          = Form(...),
    city_region:     str          = Form(""),
    lat:             float | None = Form(None),
    lon:             float | None = Form(None),
    ecosystem:       str          = Form("other"),
    user_notes:      str          = Form(""),
):
    result = await _run_workflow(
        _confirm_runner,
        confirm_adk_app.name,
        {
            "chosen_common_name":     common_name,
            "chosen_scientific_name": scientific_name,
            "place_name":    place_name,
            "city_region":   city_region,
            "lat": lat, "lon": lon,
            "ecosystem":     ecosystem,
            "user_notes":    user_notes,
            "confidence_label": 1.0,
        },
    )
    return result


# ── field journal ───────────────────────────────────────────────────────

def _transform_sighting(s: dict) -> dict:
    return {
        "common_name":         s.get("species_name", ""),
        "scientific_name":     s.get("scientific_name", ""),
        "confidence":          s.get("confidence") or 0.0,
        "conservation_status": s.get("conservation_status"),
        "journal_entry_text":  s.get("journal_entry_text", ""),
        "recorded_at": (s.get("date") or "").replace(" ", "T"),
    }


@api.get("/api/journal/places")
async def journal_places():
    """Returns a plain JSON array (not {places:[...]}), with field names the frontend reads."""
    result = journal_store.list_journal_places()
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(500, result["error"])
    raw_places = result.get("places", []) if isinstance(result, dict) else result

    transformed = []
    for p in raw_places:
        sightings = [_transform_sighting(s) for s in p.get("sightings", [])]
        transformed.append({
            "place_id":               str(p["id"]),
            "name":                   p.get("name", ""),
            "city_region":            p.get("region", ""),
            "lat":                    p.get("lat"),
            "lon":                    p.get("lon"),
            "ecosystem":              p.get("ecosystem", "other"),
            "first_visited":          p.get("first_visited", ""),
            "species_count":          p.get("species_count", 0),
            "has_conservation_concern": p.get("has_conservation_concern", False),
            "species":                sightings,
            "sightings":              sightings,
        })
    return transformed


@api.get("/api/journal/places/{place_id}")
async def journal_place_detail(place_id: int):
    detail = journal_store.get_journal_place_detail(place_id)
    if "error" in detail:
        raise HTTPException(404, detail["error"])
    return detail


@api.get("/api/journal/stats")
async def journal_stats():
    return journal_store.journal_stats()


# ── soundscape / place memory ───────────────────────────────────────────

def _build_soundscape_prompt(places_data, musical_character, elements) -> str:
    lines = [
        f"Musical character: {musical_character}",
        f"Elements to include: {', '.join(elements) if elements else 'field recording layer, percussion'}",
        "",
        "Places and birds recorded:",
    ]
    for p in places_data:
        sightings = p.get("sightings", [])
        lines += [
            "",
            f"Place: {p.get('name', 'Unknown')} — {p.get('ecosystem', 'other')} habitat — {p.get('region', '')}",
            f"Recorded species ({len(sightings)}):",
        ]
        for s in sightings:
            status = s.get("conservation_status") or ""
            badge  = f" [{status.upper()}]" if status and status.lower() not in ("lc", "least concern") else ""
            lines.append(
                f"  - {s.get('species_name', s.get('common_name', ''))} "
                f"({s.get('scientific_name', '')}){badge}"
            )
    return "\n".join(lines)


@api.post("/api/soundscape")
async def create_soundscape(
    place_ids:         List[str] = Form(...),
    musical_character: str       = Form("Forest Ambient"),
    elements:          List[str] = Form(default=[]),
):
    places_data = []
    for pid in place_ids:
        try:
            detail = journal_store.get_journal_place_detail(int(pid))
        except (ValueError, TypeError):
            continue
        if detail and "error" not in detail:
            places_data.append(detail.get("place_detail", detail))

    if not places_data:
        raise HTTPException(400, "No valid journal places found. Identify some birds first.")

    user_message = _build_soundscape_prompt(places_data, musical_character, elements)

    user_id    = "birdsong-bridge-user"
    session_id = str(uuid.uuid4())
    # Create the session on the SOUNDSCAPE runner's own service (not a separate one).
    await _soundscape_runner.session_service.create_session(
        app_name=soundscape_app.name, user_id=user_id, session_id=session_id
    )

    last_output = None
    async for event in _soundscape_runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=user_message)]),
    ):
        if event.output is not None:
            last_output = event.output
        elif event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text and part.text.strip().startswith("{"):
                    try:
                        last_output = json.loads(part.text)
                    except Exception:
                        pass

    if last_output is None:
        raise HTTPException(500, "Soundscape composer produced no output — check GOOGLE_API_KEY and model.")

    if isinstance(last_output, dict):
        return last_output
    if hasattr(last_output, "model_dump"):
        return last_output.model_dump()
    try:
        return json.loads(str(last_output))
    except Exception:
        return {"result": str(last_output)}


@api.get("/healthz")
async def healthz():
    return {"status": "ok"}
