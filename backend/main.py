from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import json
import os
import re
import uuid
from pathlib import Path
from typing import Optional, Union

app = FastAPI(title="DoodleDungeon")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ────────────────────────────────────────────────────────────────────
OLLAMA_BASE     = os.getenv("OLLAMA_BASE",     "http://localhost:11434")
VISION_MODEL    = os.getenv("VISION_MODEL",    "gemma4:e4b")
NARRATIVE_MODEL = os.getenv("NARRATIVE_MODEL", "gemma4:e4b")  # set NARRATIVE_MODEL=gemma4:26b if you have ≥32GB RAM
OLLAMA_TIMEOUT  = float(os.getenv("OLLAMA_TIMEOUT", "120"))
MOCK_AI         = os.getenv("MOCK_AI") == "1"

# ── Google AI Studio (optional — players can supply their own key) ─────────────
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_MODEL   = os.getenv("GOOGLE_MODEL",   "gemini-2.0-flash")
GOOGLE_BASE    = "https://generativelanguage.googleapis.com/v1beta"

# ── Session persistence ───────────────────────────────────────────────────────
SESSIONS_FILE = Path(__file__).parent / "sessions.json"

def _load_sessions() -> dict:
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text())
        except Exception:
            return {}
    return {}

def _save_sessions() -> None:
    try:
        SESSIONS_FILE.write_text(json.dumps(sessions))
    except Exception:
        pass  # never let a save failure crash a request

sessions: dict = _load_sessions()

# ── Request models ────────────────────────────────────────────────────────────
class NewGameRequest(BaseModel):
    player_name: str
    player_class: str  # warrior | ranger | mage
    difficulty: str = "normal"  # easy | normal | hard
    google_api_key: str = ""    # player-supplied Google AI Studio key

class GoogleKeyRequest(BaseModel):
    key: str

class SketchRequest(BaseModel):
    session_id: str
    image_b64: str

class GenerateWorldRequest(BaseModel):
    session_id: str
    theme: str
    room_count: int

class RepRequest(BaseModel):
    session_id: str
    image_b64: str
    exercise: str

class CombatStartRequest(BaseModel):
    session_id: str
    room_id: str

class EndCombatRequest(BaseModel):
    session_id: str

class NextEnemyRequest(BaseModel):
    session_id: str

class SafeRoomRequest(BaseModel):
    session_id: str
    room_id: str

# ── Game constants ─────────────────────────────────────────────────────────────
CLASS_EXERCISES = {
    "warrior": {"primary": "squats",        "heavy": "push-ups",          "special": "burpees",           "defense": "wall sit hold"},
    "ranger":  {"primary": "jumping jacks", "heavy": "high knees",        "special": "side lunges",       "defense": "bear crawl hold"},
    "mage":    {"primary": "plank hold",    "heavy": "mountain climbers", "special": "deep squat hold",   "defense": "yoga tree pose"},
}

CLASS_STATS = {
    "warrior": {"hp": 120, "strength": 15, "agility": 8,  "endurance": 10},
    "ranger":  {"hp": 100, "strength": 10, "agility": 15, "endurance": 12},
    "mage":    {"hp": 80,  "strength": 8,  "agility": 10, "endurance": 18},
}

QUALITY_MULTIPLIER    = {"perfect": 1.0, "good": 0.75, "sloppy": 0.40, "miss": 0.30}
DIFFICULTY_MULTIPLIERS = {"easy": 0.6, "normal": 1.0, "hard": 1.5}

# keyword → (display label, damage multiplier bonus)
ITEM_EFFECTS = [
    ("crown",   "+15% damage", 0.15),
    ("orb",     "+12% damage", 0.12),
    ("sword",   "+10% damage", 0.10),
    ("axe",     "+10% damage", 0.10),
    ("bow",     "+10% damage", 0.10),
    ("staff",   "+10% damage", 0.10),
    ("spear",   "+10% damage", 0.10),
    ("wand",    "+10% damage", 0.10),
    ("dagger",  "+10% damage", 0.10),
    ("gem",     "+8% damage",  0.08),
    ("crystal", "+8% damage",  0.08),
    ("ring",    "+8% damage",  0.08),
    ("amulet",  "+8% damage",  0.08),
    ("shield",  "+5% damage",  0.05),
    ("helm",    "+5% damage",  0.05),
    ("armor",   "+5% damage",  0.05),
    ("boots",   "+5% damage",  0.05),
    ("gloves",  "+5% damage",  0.05),
    ("cloak",   "+5% damage",  0.05),
    ("torch",   "light source", 0.00),
]

def item_bonus(name: str) -> tuple[str, float]:
    lower = name.lower()
    for kw, desc, pct in ITEM_EFFECTS:
        if kw in lower:
            return desc, pct
    return "+5% damage", 0.05

def total_inventory_bonus(inventory: list) -> float:
    return min(sum(item.get("pct", 0.05) for item in inventory), 0.50)

# ── Ollama helpers ─────────────────────────────────────────────────────────────
_EXERCISES = ["squats", "push-ups", "jumping jacks", "plank hold", "mountain climbers", "lunges"]
_ROOM_NAMES = ["Entrance Hall", "Crypt Corridor", "Armory", "Dark Chapel", "Torture Chamber",
               "Wizard's Study", "Barracks", "Hidden Vault"]
_ENEMY_NAMES = ["Skeleton Guard", "Zombie Soldier", "Cursed Knight", "Shadow Wraith",
                "Stone Golem", "Plague Rat Swarm", "Vampire Thrall"]

def _make_mock_world(n: int) -> dict:
    """Generate a mock dungeon with exactly n rooms for MOCK_AI testing."""
    import random
    rooms = []
    for i in range(1, n + 1):
        is_boss = i == n
        ex = _EXERCISES[i % len(_EXERCISES)]
        rooms.append({
            "id": f"room_{i}",
            "name": _ROOM_NAMES[(i - 1) % len(_ROOM_NAMES)] if not is_boss else "Throne Room",
            "description": "A foreboding chamber with crumbling stone walls and eerie torchlight.",
            "enemies": [{
                "name": "Shadow Tyrant" if is_boss else _ENEMY_NAMES[(i - 1) % len(_ENEMY_NAMES)],
                "lore": "The undead lord who rules this forsaken place." if is_boss else "A fierce dungeon guardian reanimated by dark magic.",
                "hp": 30 if is_boss else 10,
                "exercise": "push-ups" if is_boss else ex,
                "reps_required": 15 if is_boss else 6,
            }],
            "items": ["crown" if is_boss else "torch"],
            "connections": ([f"room_{i + 1}"] if i < n else []) + ([f"room_{i - 1}"] if i > 1 else []),
            "is_boss": is_boss,
        })
    return {
        "dungeon_name": "The Shadow Vault",
        "dungeon_lore": "Built by a forgotten cult, this dungeon has claimed many adventurers.",
        "rooms": rooms,
        "entrance_room": "room_1",
        "boss_room": f"room_{n}",
    }

# ── AI routing helpers ─────────────────────────────────────────────────────────

def _get_google_key(session: dict) -> str:
    """Return the Google API key for this session, falling back to env var."""
    return session.get("google_api_key", "") or GOOGLE_API_KEY

async def _google_generate(prompt: str, image_b64: Optional[str], temperature: float, key: str) -> str:
    parts = [{"text": prompt}]
    if image_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
        r = await client.post(
            f"{GOOGLE_BASE}/models/{GOOGLE_MODEL}:generateContent",
            params={"key": key},
            json={"contents": [{"parts": parts}], "generationConfig": {"temperature": temperature}},
        )
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

async def _google_stream(prompt: str, temperature: float, key: str):
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{GOOGLE_BASE}/models/{GOOGLE_MODEL}:streamGenerateContent",
            params={"key": key, "alt": "sse"},
            json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": temperature}},
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if not data_str:
                    continue
                try:
                    chunk = json.loads(data_str)
                    for part in chunk.get("candidates", [{}])[0].get("content", {}).get("parts", []):
                        if part.get("text"):
                            yield part["text"]
                except (json.JSONDecodeError, IndexError):
                    pass

async def ai_vision(prompt: str, image_b64: str, session: dict) -> str:
    """Vision inference — Google AI Studio or Ollama depending on session."""
    if MOCK_AI:
        if "dungeon" in prompt.lower() or "json" in prompt.lower():
            return json.dumps(_make_mock_world(3))
        return '{"quality":"perfect","feedback":"Great depth!","score":100}'
    key = _get_google_key(session)
    if key:
        return await _google_generate(prompt, image_b64, 0.2, key)
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
        r = await client.post(f"{OLLAMA_BASE}/api/chat", json={
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
            "stream": False,
            "options": {"temperature": 0.2},
        })
        r.raise_for_status()
        return r.json()["message"]["content"]

async def ai_text(prompt: str, temperature: float = 0.8, model: Optional[str] = None, session: Optional[dict] = None) -> str:
    """Text inference — Google AI Studio or Ollama depending on session."""
    if session is None:
        session = {}
    if MOCK_AI:
        if "valid JSON" in prompt or "dungeon_name" in prompt:
            m = re.search(r"exactly (\d+) rooms", prompt)
            n = int(m.group(1)) if m else 3
            return json.dumps(_make_mock_world(n))
        return "You step forward with fierce determination. The dungeon trembles at your approach!"
    key = _get_google_key(session)
    if key:
        return await _google_generate(prompt, None, temperature, key)
    target = model or NARRATIVE_MODEL
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
        r = await client.post(f"{OLLAMA_BASE}/api/chat", json={
            "model": target,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": temperature},
        })
        r.raise_for_status()
        return r.json()["message"]["content"]

async def ai_text_stream(prompt: str, temperature: float = 0.9, session: Optional[dict] = None):
    """Streaming text inference — Google AI Studio or Ollama depending on session."""
    if session is None:
        session = {}
    if MOCK_AI:
        for word in "You strike with fierce precision — the enemy staggers back!".split():
            yield word + " "
        return
    key = _get_google_key(session)
    if key:
        async for token in _google_stream(prompt, temperature, key):
            yield token
        return
    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
        async with client.stream("POST", f"{OLLAMA_BASE}/api/chat", json={
            "model": NARRATIVE_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "options": {"temperature": temperature},
        }) as r:
            async for line in r.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
                except json.JSONDecodeError:
                    pass


def extract_json(text: str) -> dict:
    """Pull JSON out of a model response, handling markdown fences."""
    # Try ```json ... ``` block first
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # Fall back to first { … } in the response
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"No JSON found in model response:\n{text[:300]}")

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.post("/api/new-game")
async def new_game(req: NewGameRequest):
    """Create a new game session and return session_id + character."""
    session_id = str(uuid.uuid4())[:8]
    stats = CLASS_STATS[req.player_class]
    sessions[session_id] = {
        "session_id": session_id,
        "character": {
            "name": req.player_name,
            "class": req.player_class,
            "level": 1,
            "xp": 0,
            "xp_next": 100,
            "hp": stats["hp"],
            "max_hp": stats["hp"],
            "stats": stats,
            "gold": 0,
            "inventory": [],
            "defeated_rooms": [],
        },
        "difficulty": req.difficulty,
        "google_api_key": req.google_api_key or GOOGLE_API_KEY,
        "world": None,
        "current_room_id": None,
        "combat": None,
        "narrative_history": [],
    }
    _save_sessions()
    return {"session_id": session_id, "character": sessions[session_id]["character"]}


@app.post("/api/sketch")
async def process_sketch(req: SketchRequest):
    """Read a hand-drawn dungeon sketch and generate the full world."""
    if req.session_id not in sessions:
        raise HTTPException(404, "Session not found")

    prompt = """Read this hand-drawn dungeon map and generate a fantasy world from it.
Return ONLY valid JSON, no other text:
{"dungeon_name":"Name","dungeon_lore":"1-2 sentences","rooms":[{"id":"room_1","name":"Name","description":"1 sentence","enemies":[{"name":"Enemy","lore":"1 sentence","hp":10,"exercise":"squats","reps_required":8}],"items":["item"],"connections":["room_2"],"is_boss":false}],"entrance_room":"room_1","boss_room":"room_N"}

Rules: 3-5 rooms, one boss room (is_boss:true, hp 30-50, reps 15-20), normal enemies hp 8-15 reps 6-12.
Exercises: squats, push-ups, jumping jacks, plank hold, mountain climbers, lunges, burpees.
If image unclear, invent a dungeon anyway."""

    try:
        world = None
        for attempt in range(3):
            try:
                raw = await ai_vision(prompt, req.image_b64, session)
                world = extract_json(raw)
                break
            except Exception:
                if attempt == 2:
                    world = _make_mock_world(3)

        session = sessions[req.session_id]
        session["world"] = world
        session["current_room_id"] = world["entrance_room"]

        char = session["character"]
        intro = await ai_text(
            f"""DM narration. {char['name']} (level {char['level']} {char['class']}) enters "{world['dungeon_name']}". Lore: {world['dungeon_lore']}
Write 2 atmospheric sentences. Second person. No labels.""",
            temperature=0.9,
            model=VISION_MODEL,
        )
        session["narrative_history"].append({"type": "intro", "text": intro})
        _save_sessions()

        return {"world": world, "intro_narrative": intro}

    except Exception as e:
        raise HTTPException(500, f"Sketch processing failed: {type(e).__name__}: {e}")


@app.post("/api/generate-world")
async def generate_world_from_options(req: GenerateWorldRequest):
    """Generate a dungeon world from theme + size choices, no sketch required."""
    if req.session_id not in sessions:
        raise HTTPException(404, "Session not found")

    session = sessions[req.session_id]
    char    = session["character"]
    n       = max(3, min(7, req.room_count))

    prompt = f"""Create a fantasy dungeon. Theme: {req.theme}. Hero: {char['name']} ({char['class']}).
Return ONLY valid JSON, no other text:
{{"dungeon_name":"Name","dungeon_lore":"1-2 sentences","rooms":[{{"id":"room_1","name":"Name","description":"1 sentence","enemies":[{{"name":"Enemy","lore":"1 sentence","hp":10,"exercise":"squats","reps_required":8}}],"items":["item"],"connections":["room_2"],"is_boss":false}}],"entrance_room":"room_1","boss_room":"room_{n}"}}

Rules: exactly {n} rooms (room_1..room_{n}), last room is boss (is_boss:true, hp 30-50, reps 15-20), others hp 8-15 reps 6-12.
Exercises: squats, push-ups, jumping jacks, plank hold, mountain climbers, lunges, burpees. Match "{req.theme}" theme."""

    try:
        world = None
        for attempt in range(3):
            try:
                raw   = await ai_text(prompt, temperature=0.4, model=VISION_MODEL, session=session)
                world = extract_json(raw)
                break
            except Exception:
                if attempt == 2:
                    world = _make_mock_world(n)

        session["world"]           = world
        session["current_room_id"] = world["entrance_room"]

        intro = await ai_text(
            f"""DM narration. {char['name']} (level {char['level']} {char['class']}) enters "{world['dungeon_name']}". Lore: {world['dungeon_lore']}
Write 2 atmospheric sentences. Second person. No labels.""",
            temperature=0.9,
            model=VISION_MODEL,
        )
        session["narrative_history"].append({"type": "intro", "text": intro})
        _save_sessions()

        return {"world": world, "intro_narrative": intro}

    except Exception as e:
        raise HTTPException(500, f"World generation failed: {type(e).__name__}: {e}")


@app.post("/api/analyze-rep")
async def analyze_rep(req: RepRequest):
    """Analyze one rep from a camera frame. Returns quality score and updates combat."""
    if req.session_id not in sessions:
        raise HTTPException(404, "Session not found")

    session = sessions[req.session_id]
    combat  = session.get("combat")

    prompt = f"""Score this {req.exercise} rep. Rate strictly:
- perfect: full range, good alignment
- good: minor flaws, correct movement
- sloppy: shallow range or poor form
- miss: not doing the exercise

Return ONLY valid JSON: {{"quality":"perfect","feedback":"coaching cue max 8 words","score":100}}
Scores: perfect=100, good=75, sloppy=25, miss=0"""

    try:
        raw    = await ai_vision(prompt, req.image_b64, session)
        result = extract_json(raw)
        quality = result.get("quality", "miss")
        mult    = QUALITY_MULTIPLIER.get(quality, 0)

        response = {
            "quality":           quality,
            "feedback":          result.get("feedback", ""),
            "score":             result.get("score", 0),
            "damage_dealt":      0,
            "enemy_hp":          None,
            "enemy_max_hp":      None,
            "reps_completed":    0,
            "reps_required":     0,
            "enemy_defeated":    False,
            "enemies_remaining": 0,
            "player_hp":         session["character"]["hp"],
            "player_max_hp":     session["character"]["max_hp"],
            "player_defeated":   False,
            "counter_damage":    0,
            "narration":         None,
            "narration_pending": False,
        }

        if combat and combat.get("active"):
            inv_bonus = total_inventory_bonus(session["character"].get("inventory", []))
            dmg = int(combat["base_damage"] * mult * (1 + inv_bonus))
            combat["enemy_current_hp"]  = max(0, combat["enemy_current_hp"] - dmg)
            combat["reps_completed"]   += 1
            combat["quality_log"].append(quality)
            combat.setdefault("full_quality_log", []).append(quality)
            combat["total_damage"]     += dmg

            enemy_defeated = combat["enemy_current_hp"] <= 0

            # Counter-attack: bad form lets the enemy hit back
            char         = session["character"]
            counter_dmg  = 0
            if not enemy_defeated:
                if quality == "miss":
                    counter_dmg = max(1, int(combat["enemy_max_hp"] * 0.10))
                elif quality == "sloppy":
                    counter_dmg = max(1, int(combat["enemy_max_hp"] * 0.05))
                if counter_dmg:
                    char["hp"] = max(0, char["hp"] - counter_dmg)

            player_defeated  = char["hp"] <= 0 and not enemy_defeated
            enemies_remaining = len(combat.get("enemy_queue", [])) - combat.get("current_enemy_idx", 0) - 1
            response.update({
                "damage_dealt":      dmg,
                "enemy_hp":          combat["enemy_current_hp"],
                "enemy_max_hp":      combat["enemy_max_hp"],
                "reps_completed":    combat["reps_completed"],
                "reps_required":     combat["reps_required"],
                "enemy_defeated":    enemy_defeated,
                "enemies_remaining": max(0, enemies_remaining),
                "player_hp":         char["hp"],
                "player_max_hp":     char["max_hp"],
                "player_defeated":   player_defeated,
                "counter_damage":    counter_dmg,
            })

            # ── Adaptive difficulty ───────────────────────────────────────
            adaptive_event = None
            if not enemy_defeated:
                if quality in ("perfect", "good"):
                    combat["perfect_streak"] = combat.get("perfect_streak", 0) + 1
                    combat["bad_streak"] = 0
                    if combat["perfect_streak"] >= 3:
                        base    = combat.get("reps_required_base", combat["reps_required"])
                        new_req = min(combat["reps_required"] + 2, base + 6)
                        if new_req > combat["reps_required"]:
                            combat["reps_required"] = new_req
                            response["reps_required"] = new_req
                            adaptive_event = "desperate"
                        combat["perfect_streak"] = 0
                else:
                    combat["bad_streak"] = combat.get("bad_streak", 0) + 1
                    combat["perfect_streak"] = 0
                    if combat["bad_streak"] >= 3:
                        combat["bad_streak"] = 0
                        adaptive_event = "struggling"

            # ── Narration ─────────────────────────────────────────────────
            if enemy_defeated or adaptive_event or combat["reps_completed"] % 3 == 0:
                enemy    = combat["enemy"]
                qlog     = combat["quality_log"]
                q_summary = (
                    f"{qlog.count('perfect')} perfect, "
                    f"{qlog.count('good')} good, "
                    f"{qlog.count('sloppy')} sloppy reps"
                )
                room = next(r for r in session["world"]["rooms"] if r["id"] == session["current_room_id"])

                if enemy_defeated:
                    nar_prompt = (
                        f"Dungeon Master narration. {char['name']} (level {char['level']} {char['class']}) "
                        f"just defeated {enemy['name']} in {room['name']} using {req.exercise}. "
                        f"Performance: {q_summary}. "
                        f"Write 2 punchy sentences describing the killing blow and victory. Second person."
                    )
                elif adaptive_event == "desperate":
                    nar_prompt = (
                        f"Dungeon Master narration. {char['name']} ({char['class']}) is dominating "
                        f"{enemy['name']} in {room['name']} — {q_summary}. The enemy grows desperate "
                        f"and fights back harder: now {combat['reps_required']} reps required! "
                        f"Write 1-2 punchy sentences. Second person. No labels."
                    )
                elif adaptive_event == "struggling":
                    nar_prompt = (
                        f"Dungeon Master narration. {char['name']} ({char['class']}) is struggling "
                        f"against {enemy['name']} in {room['name']} — {q_summary}. "
                        f"The enemy taunts the hero's faltering form. "
                        f"Write 1-2 tense sentences. Second person. No labels."
                    )
                else:
                    hit_desc = "lands a devastating strike" if quality in ("perfect", "good") else "stumbles — the enemy strikes back"
                    nar_prompt = (
                        f"Dungeon Master narration. {char['name']} ({char['class']}) fighting {enemy['name']} "
                        f"in {room['name']}. Enemy HP: {combat['enemy_current_hp']}/{combat['enemy_max_hp']}. "
                        f"Last rep: {quality} — hero {hit_desc}. {q_summary} so far. "
                        f"Write 1-2 punchy sentences. Second person. No labels."
                    )

                session["pending_narration_prompt"] = nar_prompt
                response["narration_pending"] = True

        _save_sessions()
        return response

    except Exception as e:
        raise HTTPException(500, f"Rep analysis failed: {e}")


@app.post("/api/start-combat")
async def start_combat(req: CombatStartRequest):
    """Enter a room and start a combat encounter."""
    if req.session_id not in sessions:
        raise HTTPException(404, "Session not found")

    session = sessions[req.session_id]
    world   = session.get("world")
    if not world:
        raise HTTPException(400, "No world loaded — process a sketch first")

    room = next((r for r in world["rooms"] if r["id"] == req.room_id), None)
    if not room:
        raise HTTPException(404, "Room not found")

    enemies = room.get("enemies", [])
    if not enemies:
        raise HTTPException(400, "No enemies in this room")

    enemy         = enemies[0]
    exercise      = enemy.get("exercise", CLASS_EXERCISES[session["character"]["class"]]["primary"])
    diff_mult     = DIFFICULTY_MULTIPLIERS.get(session.get("difficulty", "normal"), 1.0)
    reps_required = max(3, round(enemy.get("reps_required", 10) * diff_mult))
    base_damage   = enemy["hp"] / reps_required  # perfect reps always win

    session["combat"] = {
        "active":             True,
        "room_id":            req.room_id,
        "enemy":              enemy,
        "enemy_max_hp":       enemy["hp"],
        "enemy_current_hp":   enemy["hp"],
        "exercise":           exercise,
        "reps_required":      reps_required,
        "reps_completed":     0,
        "quality_log":        [],
        "total_damage":       0,
        "base_damage":        base_damage,
        "enemy_queue":        enemies,
        "current_enemy_idx":  0,
        "enemies_total":      len(enemies),
        "full_quality_log":   [],
        "reps_required_base": reps_required,
        "perfect_streak":     0,
        "bad_streak":         0,
    }
    session["current_room_id"] = req.room_id

    char = session["character"]
    opening = await ai_text(
        f"""DM narration. {char['name']} ({char['class']}) enters {room['name']}. Faces {enemy['name']} (HP:{enemy['hp']}). Must do {reps_required} {exercise}.
Write 2 tense sentences. Second person. No labels.""",
        temperature=0.85,
        model=VISION_MODEL,
    )
    session["narrative_history"].append({"type": "encounter", "text": opening})
    _save_sessions()

    return {"combat": session["combat"], "room": room, "opening_narration": opening}


@app.post("/api/next-enemy")
async def next_enemy(req: NextEnemyRequest):
    """Award XP for the just-defeated enemy and advance to the next one in the room queue."""
    if req.session_id not in sessions:
        raise HTTPException(404, "Session not found")

    session = sessions[req.session_id]
    combat  = session.get("combat")
    if not combat or not combat.get("active"):
        raise HTTPException(400, "No active combat")

    next_idx = combat["current_enemy_idx"] + 1
    queue    = combat.get("enemy_queue", [])
    if next_idx >= len(queue):
        raise HTTPException(400, "No more enemies in queue")

    # Award XP for the enemy just defeated
    qlog         = combat["quality_log"]
    perfect_ratio = qlog.count("perfect") / max(len(qlog), 1)
    xp_gained    = int(combat["enemy_max_hp"] * 5 * (1 + perfect_ratio * 0.5))
    char         = session["character"]
    char["xp"]  += xp_gained
    leveled_up   = False
    if char["xp"] >= char["xp_next"]:
        char["level"]  += 1
        char["xp_next"] = char["level"] * 100
        char["max_hp"] += 10
        char["hp"]      = char["max_hp"]
        leveled_up      = True

    # Reset combat state for the next enemy
    next_enemy_data = queue[next_idx]
    exercise        = next_enemy_data.get("exercise", CLASS_EXERCISES[char["class"]]["primary"])
    diff_mult       = DIFFICULTY_MULTIPLIERS.get(session.get("difficulty", "normal"), 1.0)
    reps_required   = max(3, round(next_enemy_data.get("reps_required", 10) * diff_mult))
    base_damage     = next_enemy_data["hp"] / reps_required

    combat.update({
        "enemy":             next_enemy_data,
        "enemy_max_hp":      next_enemy_data["hp"],
        "enemy_current_hp":  next_enemy_data["hp"],
        "exercise":          exercise,
        "reps_required":     reps_required,
        "reps_completed":    0,
        "quality_log":       [],
        "total_damage":      0,
        "base_damage":       base_damage,
        "current_enemy_idx": next_idx,
    })

    room = next((r for r in session["world"]["rooms"] if r["id"] == session["current_room_id"]), {})
    opening = await ai_text(
        f"""DM narration. {char['name']} ({char['class']}) defeated one foe. Now {next_enemy_data['name']} (HP:{next_enemy_data['hp']}) charges. Must do {reps_required} {exercise}.
Write 1-2 punchy sentences. Second person. No labels.""",
        temperature=0.85,
        model=VISION_MODEL,
    )
    session["narrative_history"].append({"type": "encounter", "text": opening})
    _save_sessions()

    return {
        "enemy":             next_enemy_data,
        "enemy_max_hp":      next_enemy_data["hp"],
        "enemy_current_hp":  next_enemy_data["hp"],
        "exercise":          exercise,
        "reps_required":     reps_required,
        "reps_completed":    0,
        "current_enemy_idx": next_idx,
        "enemies_total":     combat["enemies_total"],
        "enemies_remaining": len(queue) - next_idx - 1,
        "opening_narration": opening,
        "xp_gained":         xp_gained,
        "leveled_up":        leveled_up,
        "character":         char,
    }


@app.post("/api/end-combat")
async def end_combat(req: EndCombatRequest):
    """Resolve combat — award XP, handle level-up, mark room cleared."""
    if req.session_id not in sessions:
        raise HTTPException(404, "Session not found")

    session = sessions[req.session_id]
    combat  = session.get("combat")
    if not combat:
        raise HTTPException(400, "No active combat")

    qlog    = combat["quality_log"]
    victory = combat["enemy_current_hp"] <= 0

    xp_gained  = 0
    leveled_up = False
    items_found = []

    if victory:
        perfect_ratio = qlog.count("perfect") / max(len(qlog), 1)
        xp_gained = int(combat["enemy_max_hp"] * 5 * (1 + perfect_ratio * 0.5))
        char = session["character"]
        char["xp"] += xp_gained
        char["defeated_rooms"].append(combat["room_id"])

        if char["xp"] >= char["xp_next"]:
            char["level"]   += 1
            char["xp_next"]  = char["level"] * 100
            char["max_hp"]  += 10
            char["hp"]       = char["max_hp"]
            leveled_up       = True

        # Loot items from the cleared room
        world = session.get("world") or {}
        room  = next((r for r in world.get("rooms", []) if r["id"] == combat["room_id"]), {})
        for item_name in room.get("items", []):
            desc, pct   = item_bonus(item_name)
            item_obj    = {"name": item_name, "bonus": desc, "pct": pct}
            char["inventory"].append(item_obj)
            items_found.append(item_obj)

    combat["active"] = False

    boss_room_id    = (session.get("world") or {}).get("boss_room")
    is_boss_victory = victory and combat.get("room_id") == boss_room_id

    full_log = combat.get("full_quality_log", qlog)
    _save_sessions()

    return {
        "victory":         victory,
        "is_boss_victory": is_boss_victory,
        "xp_gained":       xp_gained,
        "leveled_up":      leveled_up,
        "items_found":     items_found,
        "quality_log":     full_log,
        "perfect_reps":    full_log.count("perfect"),
        "good_reps":       full_log.count("good"),
        "sloppy_reps":     full_log.count("sloppy"),
        "miss_reps":       full_log.count("miss"),
        "character":       session["character"],
    }


@app.post("/api/safe-room")
async def safe_room_rest(req: SafeRoomRequest):
    """Rest in an enemy-free room, recovering 30% max HP."""
    if req.session_id not in sessions:
        raise HTTPException(404, "Session not found")

    session = sessions[req.session_id]
    world   = session.get("world") or {}
    room    = next((r for r in world.get("rooms", []) if r["id"] == req.room_id), None)

    if not room:
        raise HTTPException(404, "Room not found")
    if room.get("enemies"):
        raise HTTPException(400, "Room has enemies — fight them first")

    char  = session["character"]
    heal  = max(0, min(int(char["max_hp"] * 0.30), char["max_hp"] - char["hp"]))
    char["hp"] += heal

    if req.room_id not in char["defeated_rooms"]:
        char["defeated_rooms"].append(req.room_id)
    session["current_room_id"] = req.room_id

    narration = await ai_text(
        f"""DM narration. {char['name']} ({char['class']}) finds respite in {room['name']} and recovers {heal} HP.
Write 1 atmospheric sentence. Second person. No labels.""",
        temperature=0.85,
        model=VISION_MODEL,
    )
    session["narrative_history"].append({"type": "rest", "text": narration})
    _save_sessions()

    return {
        "hp":        char["hp"],
        "max_hp":    char["max_hp"],
        "healed":    heal,
        "narration": narration,
        "character": char,
    }


@app.get("/api/state/{session_id}")
async def get_state(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")
    return sessions[session_id]


@app.get("/api/narrative/{session_id}")
async def get_narrative(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")
    return {"narrative": sessions[session_id]["narrative_history"]}


@app.get("/api/narrative-stream/{session_id}")
async def narrative_stream(session_id: str):
    """SSE endpoint — streams pending narration token by token, then saves it to history."""
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")

    session = sessions[session_id]
    prompt  = session.pop("pending_narration_prompt", None)

    async def generate():
        if not prompt:
            yield "data: [DONE]\n\n"
            return
        full_text = ""
        async for token in ai_text_stream(prompt, session=session):
            full_text += token
            yield f"data: {json.dumps({'token': token})}\n\n"
        if full_text:
            session["narrative_history"].append({"type": "combat", "text": full_text})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/boss-finale/{session_id}")
async def boss_finale_stream(session_id: str):
    """Streams an epic victory speech using the full narrative history — 128K context showcase."""
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")

    session = sessions[session_id]
    world   = session.get("world") or {}
    char    = session["character"]
    history = session.get("narrative_history", [])

    full_history = "\n\n".join(
        f"[{e['type'].upper()}] {e['text']}" for e in history
    )

    prompt = f"""You are the Dungeon Master. A hero has just completed their quest.

Hero: {char['name']}, Level {char['level']} {char['class']}
Dungeon: {world.get('dungeon_name', 'The Dungeon')}
Lore: {world.get('dungeon_lore', '')}

Their complete journey:
{full_history}

Write an epic 4-6 sentence victory speech. Reference specific moments from the journey above.
Celebrate the defeat of the final boss. Speak of their legend echoing through the dungeon forever.
Write in second person. Be epic and emotional. Return only the speech, no labels."""

    async def generate():
        async for token in ai_text_stream(prompt, temperature=0.9, session=session):
            yield f"data: {json.dumps({'token': token})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/config")
async def get_config():
    """Return which AI backends are available so the frontend can show the right UI."""
    return {
        "google_preconfigured": bool(GOOGLE_API_KEY),
        "google_model": GOOGLE_MODEL,
        "ollama_base": OLLAMA_BASE,
        "vision_model": VISION_MODEL,
        "narrative_model": NARRATIVE_MODEL,
    }


@app.post("/api/verify-google-key")
async def verify_google_key(req: GoogleKeyRequest):
    """Quick ping to validate a Google AI Studio key before starting a game."""
    if not req.key:
        raise HTTPException(400, "No key provided")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{GOOGLE_BASE}/models/{GOOGLE_MODEL}:generateContent",
                params={"key": req.key},
                json={"contents": [{"parts": [{"text": "Hi"}]}],
                      "generationConfig": {"temperature": 0, "maxOutputTokens": 4}},
            )
            if r.status_code == 401 or r.status_code == 403:
                raise HTTPException(401, "Invalid API key")
            r.raise_for_status()
            return {"valid": True, "model": GOOGLE_MODEL}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Key check failed: {e}")


# ── Serve frontend ─────────────────────────────────────────────────────────────
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    port         = int(os.getenv("PORT", "8000"))
    ssl_keyfile  = os.getenv("SSL_KEYFILE")
    ssl_certfile = os.getenv("SSL_CERTFILE")
    kwargs: dict = {"host": "0.0.0.0", "port": port, "reload": False}
    if ssl_keyfile and ssl_certfile:
        kwargs["ssl_keyfile"]  = ssl_keyfile
        kwargs["ssl_certfile"] = ssl_certfile
    uvicorn.run(app, **kwargs)
