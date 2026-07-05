import os
import requests
from datetime import datetime
import traceback

def safe_request(method, url, **kwargs):
    try:
        response = requests.request(method, url, timeout=10, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {str(e)}"}
    except ValueError as e:
        return {"error": f"Failed to parse response: {str(e)}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}

# Singleton — loads the BirdNET model once per process, not once per request.
# Without this, every identify call reloads the TFLite model from disk
# which adds 3-8 seconds of delay each time.
_analyzer = None

def _get_analyzer():
    global _analyzer
    if _analyzer is None:
        from birdnetlib.analyzer import Analyzer
        _analyzer = Analyzer()
    return _analyzer

def identify_bird_call(audio_file_path: str, lat: float = None, lon: float = None, date: str = None) -> dict:
    """Runs BirdNET locally via the birdnetlib package to identify a bird call."""
    try:
        from birdnetlib import Recording
        analyzer = _get_analyzer()

        dt = None
        if date:
            try:
                dt = datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                return {"error": "Invalid date format. Use YYYY-MM-DD."}

        recording = Recording(
            analyzer,
            audio_file_path,
            lat=lat,
            lon=lon,
            date=dt,
            min_conf=0.25,
        )
        recording.analyze()
        return {"detections": recording.detections}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": f"BirdNET identification failed: {str(e)}"}
        
def check_regional_plausibility(lat: float, lon: float) -> dict:
    """Returns all species logged near these coordinates in the last 14 days (eBird).
    Use this to check if a candidate species is plausible here — not to look up a
    specific species code, but to see what's actually been recorded nearby recently."""
    api_key = os.environ.get("EBIRD_API_KEY")
    if not api_key:
        return {"error": "EBIRD_API_KEY environment variable is not set."}

    url = "https://api.ebird.org/v2/data/obs/geo/recent"
    headers = {"X-eBirdApiToken": api_key}
    params = {"lat": lat, "lng": lon, "back": 14, "dist": 25}

    result = safe_request("GET", url, headers=headers, params=params)
    if isinstance(result, dict) and "error" in result:
        return result

    common_names = sorted({obs.get("comName") for obs in result if obs.get("comName")})
    return {"common_names": common_names, "raw_count": len(result)}

def get_notable_sightings_nearby(lat: float, lon: float) -> dict:
    """Calls eBird API 2.0 to get notable/rare sightings nearby."""
    api_key = os.environ.get("EBIRD_API_KEY")
    if not api_key:
        return {"error": "EBIRD_API_KEY environment variable is not set."}

    url = "https://api.ebird.org/v2/data/obs/geo/recent/notable"
    headers = {"X-eBirdApiToken": api_key}
    params = {"lat": lat, "lng": lon}

    result = safe_request("GET", url, headers=headers, params=params)
    if isinstance(result, dict) and "error" in result:
        return result

    return {"notable_sightings": result}

def get_species_profile(species_name: str) -> dict:
    """Calls iNaturalist API to get species profile including photo and conservation status."""
    url = "https://api.inaturalist.org/v1/taxa"
    params = {"q": species_name, "rank": "species", "per_page": 1}

    result = safe_request("GET", url, params=params)
    if isinstance(result, dict) and "error" in result:
        return result

    results = result.get("results", [])
    if not results:
        return {"error": f"No species found for '{species_name}'"}

    taxon = results[0]

    profile = {
        "name": taxon.get("name"),
        "preferred_common_name": taxon.get("preferred_common_name")
    }

    default_photo = taxon.get("default_photo")
    if default_photo:
        profile["photo_url"] = default_photo.get("medium_url") or default_photo.get("url")

    # iNaturalist returns conservation_status as a nested object when present
    conservation_status = taxon.get("conservation_status")
    if conservation_status:
        profile["conservation_status"] = (
            conservation_status.get("status_name") or conservation_status.get("status")
        )
    else:
        # Some endpoints return a list; take the first if present
        conservation_statuses = taxon.get("conservation_statuses", [])
        if conservation_statuses:
            profile["conservation_status"] = (
                conservation_statuses[0].get("status_name") or
                conservation_statuses[0].get("status")
            )
        # If neither field exists, omit entirely — never default to "Least Concern"

    return {"profile": profile}

def get_reference_call(species_name: str) -> dict:
    """Calls xeno-canto API v3 to get reference recordings of a species' call."""
    api_key = os.environ.get("XENO_CANTO_API_KEY")

    # Correct canonical domain: xeno-canto.org (with hyphen)
    url = "https://xeno-canto.org/api/3/recordings"
    # q:A filters for quality-A recordings (best available)
    params = {"query": f"{species_name} q:A"}

    # xeno-canto v3 uses key as a query param, NOT a Bearer token
    if api_key:
        params["key"] = api_key

    result = safe_request("GET", url, params=params)
    if isinstance(result, dict) and "error" in result:
        return result

    recordings = result.get("recordings", [])
    if not recordings:
        return {"error": f"No reference calls found for '{species_name}'"}

    return {"recordings": recordings[:3]}
