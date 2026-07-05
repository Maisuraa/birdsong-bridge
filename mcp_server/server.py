from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
import os
import sys

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bird_data  # noqa: E402
import journal_store  # noqa: E402

_PORT = int(os.environ.get("PORT", os.environ.get("MCP_SERVER_PORT", "8000")))
mcp = FastMCP("BirdSong Bridge Data Server", port=_PORT)


@mcp.tool()
def identify_bird_call(
    audio_path: str,
    lat: float | None = None,
    lon: float | None = None,
    obs_date: str | None = None,
) -> dict:
    """Identify bird species present in a local audio recording (runs BirdNET locally).

    Args:
        audio_path: absolute path to a local audio file (wav, mp3, m4a, flac, ogg).
        lat, lon: decimal degrees (optional; improves accuracy by down-weighting
            species not expected at this location).
        obs_date: observation date "YYYY-MM-DD" (optional; accounts for seasonality).

    Returns:
        {"detections": [ {common_name, scientific_name, confidence, start_time,
        end_time, ...}, ... ]} sorted by confidence desc, or {"error": "..."}.
    """
    result = bird_data.identify_bird_call(audio_path, lat=lat, lon=lon, date=obs_date)
    # bird_data returns {"detections": [...]} or {"error": ...}. Sort for the caller.
    if isinstance(result, dict) and "detections" in result and isinstance(result["detections"], list):
        result["detections"] = sorted(
            result["detections"],
            key=lambda d: d.get("confidence", 0),
            reverse=True,
        )
    return result


@mcp.tool()
def check_regional_plausibility(lat: float, lon: float) -> dict:
    """What species have other birders logged near here recently (eBird, last 14 days)?

    Use this to sanity-check an acoustic ID: if a candidate species does not appear
    nearby at all, treat the identification with more suspicion.

    Returns: {"common_names": [...], "raw_count": int} or {"error": "..."}.
    """
    return bird_data.check_regional_plausibility(lat, lon)


@mcp.tool()
def get_notable_sightings_nearby(lat: float, lon: float) -> dict:
    """Rare / notable species reported recently near this location (eBird).

    Returns: {"notable_sightings": [...]} or {"error": "..."}.
    """
    return bird_data.get_notable_sightings_nearby(lat, lon)


@mcp.tool()
def get_species_profile(scientific_name: str) -> dict:
    """Reference facts about a species from iNaturalist: photo + conservation status.

    Args:
        scientific_name: binomial name, e.g. "Phoenicopterus roseus". Pass the
            scientific name, not the common name.

    Returns: {"profile": {name, preferred_common_name, photo_url?, conservation_status?}}
        or {"error": "..."}.  conservation_status is OMITTED (never defaulted to
        "Least Concern") when iNaturalist has no assessment on file.
    """
    return bird_data.get_species_profile(scientific_name)


@mcp.tool()
def get_reference_call(scientific_name: str, country: str | None = None) -> dict:
    """A real, playable reference recording of this species' call (xeno-canto v3).

    Args:
        scientific_name: binomial name, e.g. "Alcedo atthis".
        country: currently unused (kept for forward compatibility); the underlying
            data layer does not yet filter by country.

    Returns: {"recordings": [ up to 3 quality-A recordings ]} or {"error": "..."}.
    """
    return bird_data.get_reference_call(scientific_name)


@mcp.tool()
def save_sighting(
    place_name: str,
    city_region: str,
    common_name: str,
    scientific_name: str,
    confidence: float,
    journal_entry_text: str,
    lat: float | None = None,
    lon: float | None = None,
    ecosystem: str = "other",
    conservation_status: str | None = None,
) -> dict:
    """Persist a confirmed sighting to the Field Journal (SQLite).

    Call only AFTER a species is confirmed; this tool does no verification itself.

    Returns: {"sighting_id": ..., "place_id": ...}
    """
    # journal_store.save_sighting's real parameter names are species_name / region
    # (NOT common_name / city_region) and species_name is REQUIRED.
    return journal_store.save_sighting(
        species_name=common_name,
        place_name=place_name,
        region=city_region,
        lat=lat,
        lon=lon,
        ecosystem=ecosystem,
        scientific_name=scientific_name,
        confidence=confidence,
        conservation_status=conservation_status,
        journal_entry_text=journal_entry_text,
    )


@mcp.tool()
def list_journal_places() -> dict:
    """List every place in the Field Journal with its species and summary stats."""
    return journal_store.list_journal_places()


@mcp.tool()
def get_journal_place_detail(place_id: str) -> dict:
    """Full sighting history for one place (powers the expanded place card).

    Args:
        place_id: the id returned by save_sighting / list_journal_places.
    """
    # journal_store keys places by integer id; coerce defensively.
    try:
        pid = int(place_id)
    except (TypeError, ValueError):
        pid = place_id
    detail = journal_store.get_journal_place_detail(pid)
    return detail if detail is not None else {"error": f"no place with id {place_id}"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
