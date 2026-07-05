import json, os, sys
from typing import Any, Literal
from pydantic import BaseModel

from google.adk.agents.context import Context
from google.adk.agents.llm_agent import LlmAgent
from google.adk.apps.app import App
from google.adk.workflow import START, Edge, Workflow, node

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_MCP_DIR = os.path.join(_PROJECT_ROOT, "mcp_server")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from . import bird_data
from . import journal_store

try:
    from app.security import sanitize_user_notes
except ImportError:
    try:
        from security import sanitize_user_notes
    except ImportError:
        import re as _re
        def sanitize_user_notes(text: str) -> dict:
            if not text or not text.strip():
                return {"clean_text": "", "flagged": False, "flagged_reasons": [], "pii_types_redacted": []}
            injection = bool(_re.search(
                r"ignore.{0,20}instructions?|you are now|new instructions?:|system prompt",
                text, _re.IGNORECASE))
            clean = "[note removed: instruction-like text]" if injection else text[:500]
            return {"clean_text": clean, "flagged": injection,
                    "flagged_reasons": ["possible_prompt_injection"] if injection else [],
                    "pii_types_redacted": []}

CREATIVE_MODEL = os.environ.get("BIRDSONG_CREATIVE_MODEL", "gemini-2.5-flash-lite")


# ── Output schema (only one Gemini call now) ───────────────────────────

class JournalEntry(BaseModel):
    entry_text: str


# ── Deterministic nodes ────────────────────────────────────────────────

@node
async def intake_node(node_input: Any, context: Context):
    raw = context.state.get("request") or {}
    try:
        input_str = str(node_input)
        if "{" in input_str and "}" in input_str:
            json_str = input_str[input_str.find("{"):input_str.rfind("}")+1]
            raw.update(json.loads(json_str))
    except Exception:
        pass
    if not raw or not (raw.get("audio_path") or raw.get("audio_file")):
        # Use model_dump() if it's a Pydantic model, otherwise fallback to vars()
        state_dict = context.state.model_dump() if hasattr(context.state, "model_dump") else vars(context.state)
        raw = {k: v for k, v in state_dict.items() if not k.startswith("_")}

    audio_path = raw.get("audio_path") or raw.get("audio_file") or raw.get("audio_file_path")
    context.state["audio_path"]     = audio_path
    context.state["lat"]            = raw.get("lat")
    context.state["lon"]            = raw.get("lon")
    context.state["place_name"]     = raw.get("place_name", "Unknown location")
    context.state["city_region"]    = raw.get("city_region", "")
    context.state["ecosystem"]      = raw.get("ecosystem", "other")
    context.state["obs_date"]       = raw.get("obs_date")
    context.state["user_notes_raw"] = raw.get("user_notes", "")
    yield {"step": "intake_done", "audio_path": audio_path}


@node
async def identify_node(node_input: Any, context: Context):
    """BirdNET identification. No Gemini."""
    result = bird_data.identify_bird_call(
        context.state.get("audio_path"),
        lat=context.state.get("lat"),
        lon=context.state.get("lon"),
        date=context.state.get("obs_date"),
    )
    detections = result.get("detections", []) if not result.get("error") else []
    if isinstance(detections, list):
        detections = sorted(detections, key=lambda d: d.get("confidence", 0), reverse=True)
    candidates = [
        {
            "common_name": d.get("common_name", d.get("label", "Unknown")),
            "scientific_name": d.get("scientific_name", ""),
            "confidence": round(float(d.get("confidence", 0)), 4),
        }
        for d in detections
    ][:5]
    context.state["candidates"] = candidates
    context.state["birdnet_error"] = result.get("error", "")
    yield {"step": "identify_done", "candidate_count": len(candidates),
           "birdnet_error": result.get("error", "")}


@node
async def verify_node(node_input: Any, context: Context):
    """Deterministic verification against eBird. No Gemini.
    If the top BirdNET candidate appears in nearby eBird sightings → confirmed.
    Otherwise → needs_confirmation (human picks)."""
    candidates = context.state.get("candidates", [])
    if not candidates:
        context.state["chosen_common_name"] = ""
        context.state["chosen_scientific_name"] = ""
        context.state["confidence_label"] = 0.0
        context.route = "needs_confirmation"
        yield {"route_decision": "needs_confirmation", "reasoning": "No candidates from BirdNET"}
        return

    top = candidates[0]
    lat = context.state.get("lat")
    lon = context.state.get("lon")
    confirmed = False
    reasoning = ""

    if lat and lon:
        regional = bird_data.check_regional_plausibility(lat, lon)
        nearby_names = [n.lower() for n in regional.get("common_names", [])]
        if top["common_name"].lower() in nearby_names:
            confirmed = True
            reasoning = f"{top['common_name']} found in recent eBird sightings nearby"
        else:
            reasoning = f"{top['common_name']} not in recent eBird sightings nearby"
            # Still confirm if BirdNET confidence is very high
            if top["confidence"] >= 0.85:
                confirmed = True
                reasoning += f", but BirdNET confidence {top['confidence']:.0%} is high enough"
    else:
        # No location data — trust BirdNET if confidence is decent
        if top["confidence"] >= 0.5:
            confirmed = True
            reasoning = f"No location data; BirdNET confidence {top['confidence']:.0%}"
        else:
            reasoning = f"No location data and low confidence {top['confidence']:.0%}"

    context.state["chosen_common_name"] = top["common_name"]
    context.state["chosen_scientific_name"] = top["scientific_name"]
    context.state["confidence_label"] = top["confidence"]
    context.state["reasoning"] = reasoning
    route = "confirmed" if confirmed else "needs_confirmation"
    context.route = route
    yield {"route_decision": route, "reasoning": reasoning}


@node
async def confirm_node(node_input: Any, context: Context):
    """Human-in-the-loop: surface candidates for user to pick."""
    cands = context.state.get("candidates", [])
    yield {
        "status": "needs_confirmation",
        "candidates": cands,
        "verifier_reasoning": context.state.get("reasoning", ""),
    }


def _build_enrich_node(name: str):
    @node(name=name)
    async def enrich_node(node_input: Any, context: Context):
        """Pull photo + facts from iNaturalist and reference call from xeno-canto.
        No Gemini — just API calls."""
        sci = context.state.get("chosen_scientific_name", "")
        common = context.state.get("chosen_common_name", "")

        # iNaturalist: photo + conservation status
        profile_result = bird_data.get_species_profile(sci) if sci else {}
        profile = profile_result.get("profile", {})

        photo_url = profile.get("photo_url", "")
        conservation_status = profile.get("conservation_status")
        preferred_name = profile.get("preferred_common_name", common)
        short_fact = f"{preferred_name} ({sci})" if sci else preferred_name

        # xeno-canto: reference recording
        ref_result = bird_data.get_reference_call(sci) if sci else {}
        recordings = ref_result.get("recordings", [])
        ref_call_url = recordings[0].get("url", "") if recordings else ""

        context.state["photo_url"] = photo_url
        context.state["conservation_status"] = conservation_status or ""
        context.state["short_fact"] = short_fact
        context.state["did_you_know"] = ""  # no Gemini hallucination needed
        context.state["ref_call_url"] = ref_call_url
        # eBird explore link (not /species/search which 404s)
        context.state["ebird_url"] = f"https://ebird.org/explore?q={common.replace(' ', '+')}" if common else ""

        yield {
            "step": "enrich_done",
            "photo_url": photo_url,
            "conservation_status": conservation_status or "",
            "ref_call_url": ref_call_url,
        }
    return enrich_node


def _build_sanitize_node(name: str):
    @node(name=name)
    async def sanitize_node(node_input: Any, context: Context):
        sanitized = sanitize_user_notes(context.state.get("user_notes_raw", ""))
        context.state["user_notes_sanitized"] = sanitized["clean_text"]
        context.state["notes_flagged"] = sanitized["flagged"]
        yield {"step": "sanitize_done", "flagged": sanitized["flagged"]}
    return sanitize_node


# ── The ONE Gemini call: journal entry ─────────────────────────────────

def _journal_instruction(ctx):
    common = ctx.state.get("chosen_common_name", "a bird")
    sci = ctx.state.get("chosen_scientific_name", "")
    place = ctx.state.get("place_name", "")
    region = ctx.state.get("city_region", "")
    notes = ctx.state.get("user_notes_sanitized", "")
    return f"""Write exactly 1-2 sentences as a field journal entry.
Species: {common} ({sci}). Place: {place}, {region}.
Notes: {notes if notes else 'none'}.
Be warm and observational. Use the actual species and place names above. No preamble."""


def _build_journal_agent(name: str) -> LlmAgent:
    return LlmAgent(
        name=name,
        model=CREATIVE_MODEL,
        instruction=_journal_instruction,
        tools=[],
        output_schema=JournalEntry,
        output_key="journal_result",
    )


def _build_journal_transfer(name: str):
    @node(name=name)
    async def journal_transfer(node_input: Any, context: Context):
        jr = context.state.get("journal_result")
        if isinstance(jr, dict):
            text = jr.get("entry_text", "")
        elif isinstance(jr, BaseModel):
            text = jr.model_dump().get("entry_text", "")
        elif isinstance(jr, str):
            try:
                text = json.loads(jr).get("entry_text", jr)
            except Exception:
                text = jr
        else:
            text = str(jr) if jr else ""
        context.state["entry_text"] = text
        yield {"step": "journal_transfer_done"}
    return journal_transfer


def _build_save_node(name: str):
    @node(name=name)
    async def save_node(node_input: Any, context: Context):
        result = journal_store.save_sighting(
            species_name=context.state.get("chosen_common_name"),
            place_name=context.state.get("place_name"),
            region=context.state.get("city_region", ""),
            lat=context.state.get("lat"),
            lon=context.state.get("lon"),
            ecosystem=context.state.get("ecosystem", "other"),
            scientific_name=context.state.get("chosen_scientific_name"),
            confidence=float(context.state.get("confidence_label") or 0.0),
            conservation_status=context.state.get("conservation_status"),
            journal_entry_text=context.state.get("entry_text", ""),
        )
        yield {
            "status":          "saved" if not result.get("error") else "save_failed",
            "sighting_id":     result.get("sighting_id"),
            "place_id":        result.get("place_id"),
            "save_error":      result.get("error", ""),
            "common_name":     context.state.get("chosen_common_name"),
            "scientific_name": context.state.get("chosen_scientific_name"),
            "entry_text":      context.state.get("entry_text", ""),
            "photo_url":       context.state.get("photo_url", ""),
            "ebird_url":       context.state.get("ebird_url", ""),
            "ref_call_url":    context.state.get("ref_call_url", ""),
            "conservation_status": context.state.get("conservation_status", ""),
        }
    return save_node


# ── Graph assembly ──────────────────────────────────────────────────────

def _make_pipeline(suffix=""):
    enrich  = _build_enrich_node(f"enrich_node{suffix}")
    san     = _build_sanitize_node(f"sanitize_node{suffix}")
    journal = _build_journal_agent(f"journal_writer{suffix}")
    jt      = _build_journal_transfer(f"journal_transfer{suffix}")
    save    = _build_save_node(f"save_node{suffix}")
    return enrich, san, journal, jt, save


_enrich, _san, _journal, _jt, _save = _make_pipeline("")

identify_workflow = Workflow(
    name="birdsong_bridge_workflow",
    edges=[
        Edge(from_node=START,          to_node=intake_node),
        Edge(from_node=intake_node,    to_node=identify_node),
        Edge(from_node=identify_node,  to_node=verify_node),
        Edge(from_node=verify_node,    to_node=_enrich,      route="confirmed"),
        Edge(from_node=verify_node,    to_node=confirm_node,  route="needs_confirmation"),
        Edge(from_node=_enrich,        to_node=_san),
        Edge(from_node=_san,           to_node=_journal),
        Edge(from_node=_journal,       to_node=_jt),
        Edge(from_node=_jt,            to_node=_save),
    ],
)

_ce, _csan, _cj, _cjt, _csave = _make_pipeline("_confirm")

confirm_workflow = Workflow(
    name="birdsong_bridge_confirm_workflow",
    edges=[
        Edge(from_node=START,  to_node=_ce),
        Edge(from_node=_ce,    to_node=_csan),
        Edge(from_node=_csan,  to_node=_cj),
        Edge(from_node=_cj,    to_node=_cjt),
        Edge(from_node=_cjt,   to_node=_csave),
    ],
)

app         = App(name="app",         root_agent=identify_workflow)
confirm_app = App(name="app_confirm", root_agent=confirm_workflow)
