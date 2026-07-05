import asyncio
import os
import sys

# Point at your project root
sys.path.insert(0, os.path.dirname(__file__))

# FIX: Import base Runner instead of InMemoryRunner
from google.adk.runners import Runner 
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import app  # your App object

async def main():
    print("=== BirdSong Bridge — direct workflow test ===\n")

    session_service = InMemorySessionService()
    # FIX: Bind the runner to the exact session service you just created
    runner = Runner(app=app, session_service=session_service)

    user_id = "test-user"
    session_id = "test-session-001"
    
    await session_service.create_session(
        app_name=app.name, user_id=user_id, session_id=session_id
    )

    # This is state_delta — the correct way to inject structured input
    # into a graph Workflow. This is what backend/main.py does for every
    # real request from the browser.
    test_input = {
        "request": {
            # Hardcoded path for the CLI demo:
            "audio_file": r"C:\Users\Maisura\Documents\birdsong-bridge\live_sessions_recordings\WTKF.wav",
            "place_name": "Wadhwana Bird Sanctuary",
            "city_region": "Vadodara, Gujarat",
            "ecosystem": "wetland",
            "lat": 22.32,
            "lon": 73.46,
            "user_notes": "Heard near water body",
        }
    }

    print("Sending to workflow via state_delta (correct input channel)...\n")

    events = []
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text="identify")]),
        state_delta=test_input,
    ):
        events.append(event)
        node_name = getattr(event, 'author', getattr(event, 'node', 'unknown'))
        data = getattr(event, 'data', None)
        print(f"Event from [{node_name}]: {str(data)[:120]}")

    print(f"\nTotal events: {len(events)}")
    if events:
        last = events[-1]
        print(f"\nFinal output:\n{getattr(last, 'data', last)}")
    else:
        print("\nNo events — check that the MCP server is running on port 8000")

asyncio.run(main())