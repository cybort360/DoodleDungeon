# DoodleDungeon — Project Spec for Claude Code

This document is the full handoff for Claude Code. Read it entirely before touching any file.

---

## Project overview

DoodleDungeon is a local AI-powered RPG built for the Gemma 4 Challenge (dev.to × Google AI, deadline May 24 2026). The core concept: players draw a dungeon map on paper, photograph it, Gemma 4 generates a living world from the sketch, then players fight through that world using real physical exercise. Every attack is a rep; Gemma 4 checks form and only counts clean reps.

Two Gemma 4 models do distinct jobs:
- **Gemma 4B (vision)** — reads the sketch image to generate the world; scores each exercise rep from a camera frame
- **Gemma 4 27B (text)** — dungeon master brain; holds the full world state in 128K context and narrates every event

Everything runs locally via Ollama. No cloud. No data leaves the device.

---

## Current state

### What is fully built

- `backend/main.py` — FastAPI server with all 7 routes (see API spec below)
- `frontend/index.html` — complete single-file game UI with all screens
- `start.sh` — setup and launch script
- `backend/requirements.txt`

### What works end to end

1. New game → character class selection (Warrior/Ranger/Mage)
2. Sketch upload → Gemma 4B reads image → world JSON generated → world screen populated
3. Room selection → combat starts → Gemma 27B narrates opening
4. Tap-to-rep combat loop → Gemma 4B scores form → HP bar updates → narrative every 3 reps
5. Victory/defeat screens with XP gain, level-up detection, battle report
6. Session state persisted in-memory across all calls

### What is NOT yet built (priority order)

See the TODO section below for full details.

---

## Architecture

```
bodyquest/
├── backend/
│   ├── main.py            # All backend logic — single file intentionally
│   └── requirements.txt
├── frontend/
│   └── index.html         # All frontend logic — single file intentionally
├── start.sh
├── README.md
└── CLAUDE.md              # This file
```

### Why single files?

This is a hackathon project. Keeping everything in `main.py` and `index.html` makes it fast to iterate, easy to demo, and simple for judges to read. Do not split into multiple files unless a feature genuinely requires it.

### Data flow

```
iPhone/browser
  │  (base64 JPEG frames)
  ▼
FastAPI backend  ──► Gemma 4B via Ollama   (vision: sketch + form)
                 ──► Gemma 4 27B via Ollama (text: narrative + world)
  │  (JSON responses)
  ▼
Frontend JS (updates DOM, camera, HP bars, narrative)
```

### Session state shape

```python
sessions[session_id] = {
    "session_id": str,
    "character": {
        "name": str,
        "class": "warrior" | "ranger" | "mage",
        "level": int,
        "xp": int,
        "xp_next": int,       # XP needed for next level
        "hp": int,
        "max_hp": int,
        "stats": {"hp": int, "strength": int, "agility": int, "endurance": int},
        "gold": int,
        "defeated_rooms": [str],  # list of room IDs already cleared
    },
    "world": {                # None until sketch processed
        "dungeon_name": str,
        "dungeon_lore": str,
        "rooms": [
            {
                "id": str,
                "name": str,
                "description": str,
                "enemies": [
                    {
                        "name": str,
                        "lore": str,
                        "hp": int,
                        "exercise": str,
                        "reps_required": int,
                    }
                ],
                "items": [str],
                "connections": [str],   # room IDs
                "is_boss": bool,
            }
        ],
        "entrance_room": str,
        "boss_room": str,
    },
    "current_room_id": str | None,
    "combat": {               # None when not in combat
        "active": bool,
        "room_id": str,
        "enemy": dict,
        "enemy_max_hp": int,
        "enemy_current_hp": int,
        "exercise": str,
        "reps_required": int,
        "reps_completed": int,
        "quality_log": [str],   # "perfect"|"good"|"sloppy"|"miss" per rep
        "total_damage": int,
        "base_damage": float,   # hp / reps_required
    },
    "narrative_history": [
        {"type": "intro"|"encounter"|"combat"|"victory", "text": str}
    ],
}
```

---

## API spec

All endpoints accept/return JSON. CORS is open (`allow_origins=["*"]`).

### POST `/api/new-game`
```json
// Request
{"player_name": "Kira", "player_class": "warrior"}

// Response
{"session_id": "a1b2c3d4", "character": {...}}
```

### POST `/api/sketch`
```json
// Request
{"session_id": "a1b2c3d4", "image_b64": "<base64 JPEG string>"}

// Response
{
  "world": {dungeon object},
  "intro_narrative": "You descend into the..."
}
```
Calls Gemma 4B vision to read the sketch. Returns full world JSON and an opening narration from Gemma 27B.

### POST `/api/start-combat`
```json
// Request
{"session_id": "a1b2c3d4", "room_id": "room_2"}

// Response
{
  "combat": {combat object},
  "room": {room object},
  "opening_narration": "A grotesque orc steps from the shadows..."
}
```

### POST `/api/analyze-rep`
```json
// Request
{
  "session_id": "a1b2c3d4",
  "image_b64": "<base64 JPEG of camera frame>",
  "exercise": "squats"
}

// Response
{
  "quality": "perfect"|"good"|"sloppy"|"miss",
  "feedback": "Drive knees out at the bottom",
  "score": 100,
  "damage_dealt": 5,
  "enemy_hp": 5,
  "enemy_max_hp": 10,
  "reps_completed": 6,
  "reps_required": 8,
  "enemy_defeated": false,
  "narration": "Your squat is deep and powerful — the goblin stumbles..."
}
```
Narration is generated every 3 reps or on enemy defeat. May be `null` otherwise.

### POST `/api/end-combat`
```json
// Request
{"session_id": "a1b2c3d4"}

// Response
{
  "victory": true,
  "xp_gained": 62,
  "leveled_up": false,
  "perfect_reps": 4,
  "good_reps": 2,
  "sloppy_reps": 1,
  "miss_reps": 0,
  "character": {...}
}
```

### GET `/api/state/{session_id}`
Returns full session state object.

### GET `/api/narrative/{session_id}`
Returns `{"narrative": [{type, text}, ...]}` — full narrative history.

---

## Ollama integration details

```python
OLLAMA_BASE     = "http://localhost:11434"
VISION_MODEL    = "gemma4:4b"
NARRATIVE_MODEL = "gemma4:27b"
OLLAMA_TIMEOUT  = 120.0  # seconds — increase if 27B is slow on the machine
```

Vision calls use the `images` field on the message:
```python
{
    "model": "gemma4:4b",
    "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
    "stream": False,
    "options": {"temperature": 0.2}
}
```

Text calls use standard chat:
```python
{
    "model": "gemma4:27b",
    "messages": [{"role": "user", "content": prompt}],
    "stream": False,
    "options": {"temperature": 0.8}
}
```

The `extract_json()` helper handles markdown fences (` ```json ``` `) that models sometimes wrap JSON in. Always use this when parsing model JSON responses.

---

## Frontend details

Single file: `frontend/index.html`. All CSS and JS are inline — no build step, no npm, no imports.

### Screens
| Screen ID | When shown |
|---|---|
| `screen-home` | App launch |
| `screen-character` | After "Begin Quest" |
| `screen-sketch` | After character creation |
| `screen-world` | After world generation |
| `screen-combat` | After entering a room |
| `screen-victory` | After enemy defeated |
| `screen-defeat` | After running out of reps |

Switch screens with `showScreen('name')` — it deactivates all screens and activates the named one.

### Camera
Uses `navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' } })`. Captures frames to a hidden `<canvas>` on button tap, converts to base64 JPEG at 75% quality, sends to backend.

**iPhone HTTPS issue:** Safari on iPhone blocks `getUserMedia` on non-localhost HTTP. Solutions (see TODO):
1. Use `ngrok` to tunnel with HTTPS
2. Add a self-signed cert to uvicorn
3. Demo on laptop browser only (acceptable for hackathon)

### API base URL
Stored in `localStorage` as `bq_api`. Defaults to `http://localhost:8000`. User can change it via the ⚙️ settings panel on the home screen.

### Key JS globals
```javascript
let API           // server base URL
let sessionId     // current session ID
let selectedClass // 'warrior' | 'ranger' | 'mage'
let gameState     // full state object from last /api/state call
let currentCombat // combat object from last /api/start-combat
let cameraStream  // MediaStream — stop all tracks before leaving combat screen
let currentRoomId // room ID of current/last combat
```

---

## TODO — priority order for hackathon submission

### P0 — Must have before submission

**1. HTTPS for iPhone camera**
Safari blocks camera on HTTP. Easiest fix: add `--ssl-keyfile` / `--ssl-certfile` to uvicorn, or document the `ngrok` tunnel approach in README. Add to `start.sh`.
```bash
# Option A: ngrok (easiest)
ngrok http 8000
# Then open the https:// ngrok URL on iPhone

# Option B: self-signed cert
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'
# Then in main.py: uvicorn.run(app, host="0.0.0.0", port=8000, ssl_keyfile="key.pem", ssl_certfile="cert.pem")
```

**2. Streaming narration**
Currently the narrative text appears all at once after a ~5 second wait. Switch Ollama calls to `stream: true` and use SSE or chunked responses so text streams in word by word. Massively improves the demo feel.
- Add a `POST /api/narrative-stream/{session_id}` SSE endpoint
- Use `EventSource` on the frontend to receive streamed tokens
- Show a blinking cursor while streaming

**3. Persistent world state across page reloads**
Session IDs are in-memory and lost on server restart. Store `sessionId` in `localStorage` and add a `GET /api/state/{session_id}` call on page load to restore state. Already have the endpoint — just wire up the frontend restore logic.

### P1 — High impact for judges

**4. World map visualization**
Currently rooms are a list. Add a simple canvas-based or SVG dungeon map that shows rooms as nodes connected by corridors. Highlight cleared rooms in green, boss room in red, current location with a blinking marker. This is a major visual wow moment.
- Generate node positions from the sketch image dimensions or assign a simple grid layout
- Draw connectors between rooms listed in `room.connections`
- Tap a room node to enter it

**5. SketchQuest visual feedback**
After the sketch is processed, show the original sketch image side-by-side with the generated dungeon name and room list. Let users see Gemma "reading" their drawing in real time. Add a loading animation that shows a scanning effect over the sketch image while Gemma processes it.

**6. Multiple enemies per room**
The current backend picks `enemies[0]` only. Add a combat queue so rooms with multiple enemies run them sequentially. The world screen already renders all enemies in a room — just need the backend to handle queuing and the frontend to chain combats.

**7. Loot and inventory**
Rooms have `items` arrays already in the world JSON. After victory, show what items were found and add them to `character.inventory`. Show inventory on a character screen. Items can provide simple bonuses (e.g. "Iron Boots: +10% squat damage").

**8. Boss finale**
When the boss room enemy is defeated, trigger a special ending sequence: Gemma 27B generates a full victory speech based on the entire dungeon run (pull full `narrative_history` into the prompt — this is where 128K context shines). Show it on a dedicated "You Win" screen with the dungeon name and final character stats.

### P2 — Nice to have

**9. Sound effects**
- Metronome tick on attack button tap (helps with exercise pacing)
- Victory fanfare (Web Audio API — no external files needed)
- Ambient dungeon drone (low-frequency oscillator)

**10. Rep history chart**
On the victory screen, show a small bar chart of rep quality over the fight. Each bar = one rep, colored by quality (green/yellow/orange/red). Shows visually how form improved or degraded during the fight.

**11. Adaptive difficulty**
After 3+ perfect reps in a row, Gemma narrates the enemy getting desperate and reps_required increases by 2. After 3+ sloppy/miss reps, add a "struggling" narrative beat. Makes the fight feel dynamic.

**12. Mobile haptic feedback**
Use `navigator.vibrate()` on rep result:
```javascript
if (quality === 'perfect') navigator.vibrate([50, 30, 50]);
else if (quality === 'miss') navigator.vibrate(200);
```

**13. Session persistence to disk**
Replace in-memory `sessions` dict with a simple JSON file store so sessions survive server restarts. Use Python's `json` module, write on every state change.

**14. Multi-enemy room narration**
When a room has multiple enemies, Gemma should narrate them all entering the scene at once before the first combat starts.

---

## Known issues

1. **Ollama model names may vary** — `gemma4:4b` and `gemma4:27b` are assumed names. Verify with `ollama list` after pulling. If names differ, update `VISION_MODEL` and `NARRATIVE_MODEL` constants at the top of `main.py`.

2. **JSON parsing brittleness** — The `extract_json()` helper handles most cases but Gemma sometimes returns malformed JSON for complex sketch inputs. Add a retry loop (max 2 retries) around the `extract_json` call in `/api/sketch`.

3. **Camera frame timing** — The tap-to-rep system requires the user to tap at the bottom of each rep (maximum contraction point). Add a tip on the combat screen explaining this: "Tap at the bottom of your squat / top of your push-up."

4. **27B response latency** — On machines with <16GB RAM, Gemma 27B may take 15–30 seconds per narrative call. Add a visible loading indicator in the combat narrative area while waiting. Alternatively, allow switching to 4B for narrative via an environment variable: `NARRATIVE_MODEL=gemma4:4b python main.py`.

5. **No error recovery on sketch failure** — If Gemma returns malformed JSON for the sketch, the frontend shows a generic error. Add a retry button and a fallback: if JSON parsing fails twice, generate a default dungeon (3 rooms, generic enemies) so the player can still proceed.

---

## Hackathon submission checklist

- [ ] Demo video showing: sketch → world generation → combat with real exercise → victory
- [ ] Dev.to article explaining the technical choices (why Gemma 4B for vision, why 27B + 128K for narrative, why local/offline)
- [ ] Repo is public on GitHub
- [ ] README has clear setup instructions
- [ ] Explicitly call out model selection justification in the article — judges score this

### Article talking points (for the dev.to submission post)
1. The core insight: Gemma 4's vision encoder doesn't care what kind of image it sees — we feed it exercise frames AND hand-drawn sketches, two completely different visual inputs, with the same model
2. Why 128K context matters: the entire dungeon world state, every NPC conversation, every combat event, fits in one context window — the DM never "forgets" earlier rooms
3. Why local/offline is a feature not a limitation: workout video, biometric rep data, and player behavior never leave the device — this is something a cloud API literally cannot offer
4. The model selection story: 4B for latency-critical vision tasks (form must score in <10s to feel interactive), 27B for quality narrative where a few extra seconds is fine

---

## Running in development

```bash
# Start backend with auto-reload
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Frontend is served by FastAPI — just open http://localhost:8000
# Or open frontend/index.html directly for UI-only development
# (API calls will fail without the backend but screen switching works)
```

### Testing individual endpoints with curl

```bash
# New game
curl -X POST http://localhost:8000/api/new-game \
  -H "Content-Type: application/json" \
  -d '{"player_name":"Test Hero","player_class":"warrior"}'

# Get state (replace SESSION_ID)
curl http://localhost:8000/api/state/SESSION_ID

# Analyze a rep (replace SESSION_ID and B64_IMAGE)
curl -X POST http://localhost:8000/api/analyze-rep \
  -H "Content-Type: application/json" \
  -d '{"session_id":"SESSION_ID","image_b64":"B64_IMAGE","exercise":"squats"}'
```

### Testing without Ollama (mock mode)

To develop frontend without running Gemma, add a `MOCK_AI=1` env var and stub out the Ollama calls in `main.py` to return hardcoded responses. This lets you iterate on UI without waiting for model inference.

```python
import os
MOCK_AI = os.getenv("MOCK_AI") == "1"

async def ollama_vision(prompt, image_b64):
    if MOCK_AI:
        return '{"quality":"perfect","feedback":"Great depth","score":100}'
    # ... real call
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `VISION_MODEL` | `gemma4:4b` | Ollama model for vision tasks |
| `NARRATIVE_MODEL` | `gemma4:27b` | Ollama model for narrative |
| `OLLAMA_BASE` | `http://localhost:11434` | Ollama server URL |
| `MOCK_AI` | unset | Set to `1` to skip Ollama calls (dev mode) |
| `PORT` | `8000` | Server port |

All can be overridden at runtime: `NARRATIVE_MODEL=gemma4:4b bash start.sh`
