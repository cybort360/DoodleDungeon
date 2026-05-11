# DoodleDungeon

Draw a dungeon on paper. Photograph it. Fight through it with real exercise.

Gemma 4 reads your sketch and generates a living world. Every monster costs real reps — squats, push-ups, planks. The AI watches your form and only counts clean ones.

---

## Quick start — mobile, no local setup needed

1. Get a free key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Run the server on any machine:

```bash
cd backend
pip install -r requirements.txt
python3 main.py
```

3. Open `https://YOUR_MACHINE_IP:8000` on your phone
4. Tap **Settings → Google AI Studio**, paste your key, hit **Verify**
5. Draw a dungeon, snap a photo, start sweating

Safari will warn about the self-signed cert — tap **Show Details → Visit Website** once.

---

## Local-only mode — 100% offline with Ollama

No data leaves your machine.

```bash
# Pull the model
ollama pull gemma4:e4b

# Start everything
bash start.sh
```

Opens at `https://localhost:8000`. In Settings, choose **Local (Ollama)**.

> For richer narrative on machines with 32GB+ RAM: `NARRATIVE_MODEL=gemma4:26b bash start.sh`

---

## How it works

```
Draw dungeon on paper → snap photo
    ↓
Gemma 4 vision reads the sketch
    ↓
World generated: rooms, enemies, lore, boss
    ↓
Enter rooms → combat starts
    ↓
Each enemy demands reps of a real exercise
    ↓
Gemma 4 vision scores form from your camera
    ↓
Perfect = full damage · Sloppy = enemy hits back
    ↓
Boss defeated → epic finale, 128K context remembers every room
```

**Two models, two jobs:**
- `gemma4:e4b` — vision: reads sketches, scores form. Fast.
- `gemma4:26b` — narrative: dungeon master with 128K context. Optional.
- Google AI Studio: `gemini-2.0-flash` handles both.

---

## Classes

| Class | Exercises | Strengths |
|-------|-----------|-----------|
| Warrior | Squats, Push-ups, Burpees | Max HP, high damage |
| Ranger | Jumping Jacks, High Knees, Lunges | Max agility, cardio |
| Mage | Plank Hold, Mountain Climbers | Max endurance, core |

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_API_KEY` | — | Pre-configure Google AI Studio key server-side |
| `GOOGLE_MODEL` | `gemini-2.0-flash` | Google model |
| `VISION_MODEL` | `gemma4:e4b` | Ollama vision model |
| `NARRATIVE_MODEL` | `gemma4:e4b` | Ollama narrative model |
| `OLLAMA_BASE` | `http://localhost:11434` | Ollama URL |
| `MOCK_AI` | — | Set `1` to skip AI (UI dev mode) |
| `PORT` | `8000` | Server port |
| `SSL_KEYFILE` / `SSL_CERTFILE` | — | TLS cert paths |

---

## Development

```bash
# Hot reload
cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000

# UI work without AI
MOCK_AI=1 python3 main.py

# Test Google key
curl -sk -X POST https://localhost:8000/api/verify-google-key \
  -H "Content-Type: application/json" -d '{"key":"AIza..."}'
```

---

## Privacy

Ollama mode: workout video and biometric data never leave your device.
Google AI Studio mode: frames are sent to Google's API per their [privacy policy](https://policies.google.com/privacy).
