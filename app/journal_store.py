import sqlite3
import os
from datetime import datetime
from typing import Dict, Any

DB_PATH = os.environ.get("JOURNAL_DB_PATH", os.path.join(os.path.dirname(__file__), "journal.db"))

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS places (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            region TEXT,
            lat REAL,
            lon REAL,
            ecosystem TEXT DEFAULT 'other',
            first_visited TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, region)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sightings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            place_id INTEGER NOT NULL,
            species_name TEXT NOT NULL,
            scientific_name TEXT,
            confidence REAL,
            conservation_status TEXT,
            journal_entry_text TEXT,
            date TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY (place_id) REFERENCES places (id)
        )
    ''')

    conn.commit()
    conn.close()

def save_sighting(
    species_name: str,
    place_name: str,
    region: str = None,
    lat: float = None,
    lon: float = None,
    ecosystem: str = "other",
    scientific_name: str = None,
    confidence: float = None,
    conservation_status: str = None,
    journal_entry_text: str = None,
    date: str = None,
    notes: str = None
) -> Dict[str, Any]:
    init_db()

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    region = region or ""

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM places WHERE name = ? AND region = ?", (place_name, region))
        row = cursor.fetchone()

        if row:
            place_id = row['id']
        else:
            cursor.execute(
                "INSERT INTO places (name, region, lat, lon, ecosystem) VALUES (?, ?, ?, ?, ?)",
                (place_name, region, lat, lon, ecosystem)
            )
            place_id = cursor.lastrowid

        cursor.execute(
            "INSERT INTO sightings (place_id, species_name, scientific_name, confidence, "
            "conservation_status, journal_entry_text, date, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (place_id, species_name, scientific_name, confidence, conservation_status,
             journal_entry_text, date, notes)
        )
        sighting_id = cursor.lastrowid

        conn.commit()
        return {"status": "success", "sighting_id": sighting_id, "place_id": place_id}
    except Exception as e:
        return {"error": f"Failed to save sighting: {str(e)}"}
    finally:
        conn.close()

def list_journal_places() -> Dict[str, Any]:
    init_db()
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, region, lat, lon, ecosystem, first_visited FROM places")
        places = []
        for p in cursor.fetchall():
            p = dict(p)
            sightings = cursor.execute(
                "SELECT species_name, scientific_name, confidence, conservation_status, journal_entry_text "
                "FROM sightings WHERE place_id = ?", (p["id"],)
            ).fetchall()
            p["species_count"] = len({s["species_name"] for s in sightings})
            p["has_conservation_concern"] = any(
                s["conservation_status"] and s["conservation_status"].lower() not in ("lc", "least concern")
                for s in sightings
            )
            p["sightings"] = [dict(s) for s in sightings]
            places.append(p)
        return {"places": places}
    except Exception as e:
        return {"error": f"Failed to list places: {str(e)}"}
    finally:
        conn.close()

def get_journal_place_detail(place_id: int) -> Dict[str, Any]:
    init_db()
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM places WHERE id = ?", (place_id,))
        place_row = cursor.fetchone()

        if not place_row:
            return {"error": f"Place with id {place_id} not found."}

        place = dict(place_row)

        cursor.execute(
            "SELECT id, species_name, scientific_name, confidence, conservation_status, "
            "journal_entry_text, date, notes FROM sightings WHERE place_id = ? ORDER BY date DESC",
            (place_id,)
        )
        place["sightings"] = [dict(row) for row in cursor.fetchall()]
        return {"place_detail": place}
    except Exception as e:
        return {"error": f"Failed to get place detail: {str(e)}"}
    finally:
        conn.close()

def journal_stats() -> Dict[str, Any]:
    """Powers the stats strip at the top of the Field Journal tab."""
    init_db()
    try:
        conn = get_connection()
        cursor = conn.cursor()
        species = cursor.execute("SELECT COUNT(DISTINCT species_name) FROM sightings").fetchone()[0]
        places  = cursor.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        ecosys  = cursor.execute(
            "SELECT COUNT(DISTINCT ecosystem) FROM places WHERE ecosystem IS NOT NULL"
        ).fetchone()[0]
        concern = cursor.execute(
            "SELECT COUNT(DISTINCT species_name) FROM sightings "
            "WHERE conservation_status IS NOT NULL "
            "AND LOWER(conservation_status) NOT IN ('lc', 'least concern')"
        ).fetchone()[0]
        return {
            "species_spotted": species,
            "places_visited": places,
            "habitat_types": ecosys,
            "conservation_concern_species": concern
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()
