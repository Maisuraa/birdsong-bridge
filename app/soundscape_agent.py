from __future__ import annotations

import os
from typing import Literal

from google.adk.agents.llm_agent import LlmAgent
from google.adk.apps.app import App
from pydantic import BaseModel, Field

CREATIVE_MODEL = os.environ.get("BIRDSONG_CREATIVE_MODEL", "gemini-2.5-flash-lite")


class SoundscapeSection(BaseModel):
    timestamp_label: str = Field(description='e.g. "Opening (0:00-0:42)"')
    text: str = Field(description="2-3 sentences of poetic, musically-specific description.")


class SoundscapeBrief(BaseModel):
    title: str = Field(description='A short evocative title in quotes, e.g. "Wadhwana at Dawn".')
    sections: list[SoundscapeSection] = Field(description="3-4 sections forming an arc: "
                                                            "opening, rising, heart, resolution.")
    key_or_mode: str = Field(description="A musical key or mode that fits the mood, e.g. 'C major'.")
    instrumentation: str = Field(description="2-5 instruments, separated by ' · '.")
    mood: str = Field(description="2-4 mood words, separated by ' · '.")


soundscape_agent = LlmAgent(
    name="soundscape_agent",
    model=CREATIVE_MODEL,
    instruction="""You are a poetic nature-sound composer's brief-writer for
BirdSong Bridge's "Place Memory" feature.

You will be given: one or more places, each with its recorded species,
habitat/ecosystem type, and country/region; a requested musical character
(e.g. "Forest Ambient", "Rhythmic"); and a list of elements to include (e.g.
"percussion", "field recording layer").

Write a SoundscapeBrief: a short title, then 3-4 sections forming an arc
(opening / rising / heart / resolution for a single place, or one movement
per place plus a coda for multiple places). Each section should translate
the ACTUAL recorded species and habitat into musical language — a kingfisher
becomes a quick bright phrase, a heron's stillness becomes a held note, a
wetland's openness becomes wide reverb — using only species and places that
were actually provided to you, never inventing sightings.

Pull musical vocabulary (instruments, modes, ornamentation) from the
place's own region and culture when you know it, but only when you're
confident — when uncertain, default to globally-recognizable instruments
(piano, strings, flute, percussion) rather than guessing at a tradition you
aren't sure fits. This feature is used by birders and travellers anywhere
on Earth, not any single country's audience.

This is explicitly NOT a request to generate audio — you are writing a
human-readable creative brief a musician could read and compose from.""",
    tools=[],
    output_schema=SoundscapeBrief,
)

soundscape_app = App(name="birdsong_bridge_soundscape", root_agent=soundscape_agent)
