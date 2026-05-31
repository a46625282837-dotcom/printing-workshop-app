# ورشة طباعة - ID Card Desktop Application

## Architecture
- **Python 3.14.2** / **PySide6 6.11.1** / **Flask 3.x** (backend)
- Dual-mode: local SQLite or Flask REST API mode (`IDCARD_API_MODE` env var or `data/app_config.json`)
- For wide distribution: Client-Server (API mode) — users connect to a central Flask server

## Directory Structure
```
idcard_app/
├── main.py                  # Entry point (portable icon/config, reads app_config.json)
├── app.py                   # IDCardApp(MainWindow)
├── requirements.txt         # Dependencies
├── i1.jpeg / i1.ico         # Application icon
├── ورشة طباعة.bat           # Dev launcher (uses `py`)
├── ورشة طباعة.spec          # PyInstaller spec (--onefile, icon bundled)
├── PROJECT_MAP.md
├── backend/
│   ├── app.py               # Flask API (13+ endpoints, JWT auth)
│   ├── config.py
│   ├── database.py
│   └── requirements.txt
├── core/
│   ├── api_client.py        # HTTP client for API mode
│   ├── database.py          # SQLite + API wrappers + frozen-aware DATA_DIR
│   ├── font_utils.py
│   ├── id_extractor.py      # ID card contour detection
│   ├── image_utils.py       # Resize/crop helpers
│   ├── notifier.py          # WhatsApp Cloud API sender
│   ├── pdf_text_engine.py
│   ├── photo_processor.py   # Background removal (rembg) + auto-crop
│   └── printer.py           # QPrinter wrapper
├── data/
│   ├── app.db               # SQLite database (local mode)
│   ├── notifier_config.json # WhatsApp Cloud API config
│   └── app_config.json      # Server URL config (created automatically)
├── ui/
│   ├── main_window.py       # Main UI (~2040 lines): FULL API mode support
│   │                        #   - Dashboard, profile, subscriptions, banners
│   │                        #   - All methods work in both local and API mode
│   ├── a4_editor.py         # ID card editor: grid, print, save PDF
│   ├── id_card_item.py      # Card graphics item
│   ├── photo_editor.py      # Photo editor + PhotoProcessingThread + progress bar
│   └── pdf_editor.py        # PDF editing with page numbers
├── tests.py                 # 45 tests (pytest + pytest-qt)
└── dist/
    └── ورشة طباعة.exe       # Standalone executable (~347 MB, --onefile)
```

## Key Features
- User registration/login with JWT auth (no phone verification)
- Dashboard (API mode): see ALL users, manage subscriptions, reset passwords, delete
- ID card editor on A4 page: add/swap photos, print, save as PDF
- Photo editor: crop, remove background (rembg QThread with progress bar)
- PDF editor: merge/split/reorder pages, add numbers
- WhatsApp Cloud API notification (optional, configured via dialog)
- **FULL API mode support** — all 24 UI methods work with the remote server:
  - Dashboard loads from server, actions go to server
  - Profile, subscriptions, passwords sync via API
  - Banners managed remotely
  - Subscription checks use server data (no bypass possible)
- Config file `data/app_config.json` (no env vars needed for users)
- Frozen-aware paths: data next to exe, not in temp
- 45 passing tests (no regression)

## Production Security
- `JWT_SECRET` auto-generates via `secrets.token_hex(32)` if not set via env var
- `JWT_EXPIRY_HOURS` = 2 (configurable via `JWT_EXPIRY_HOURS` env var)
- Admin-only endpoints validated server-side (users cannot bypass)
- Subscription is server-authoritative: desktop app trusts server's `remaining_days`
- No self-service subscription endpoint exists (admin only via dashboard)

## Known Issues
- `ahmed` login only works via API mode (requires running backend server + `data/app_config.json` with `api_mode: true`)
- Local mode removed `ahmed` special case to avoid KeyError — admin must log in through the server

## Frozen (EXE) Behavior
- When running as frozen exe: **always uses API mode** with hardcoded `_SERVER_URL = "https://printing-workshop-api.onrender.com"`
- `data/app_config.json` is bundled inside the exe; if present next to exe at runtime, its `server_url` overrides the hardcoded one
- Development (non-frozen): reads `data/app_config.json` from source tree, falls back to env vars, then `http://localhost:5000`

## Subscription UX
- `_NO_SUB_MSG` constant in `ui/main_window.py` defines the message shown when subscription is expired (section access + print/save)
- Current message: `"يجب أن تشترك قبل الاستخدام. تواصل مع المالك: واتساب 07865402819"`
- Update `_NO_SUB_MSG` to change the contact number or message

## Build
```bash
cd idcard_app
pyinstaller "ورشة طباعة.spec" --clean
```
Output: `dist/ورشة طباعة.exe` (~52 MB)

## Deploy (Client-Server) — for wide distribution
1. Deploy the Flask backend on a cloud server (see `backend/requirements.txt`)
2. Build the exe (see Build above) — it **always connects to the server** when frozen
3. Distribute the exe (or zip with `data/app_config.json` next to it)
4. Log in as `ahmed` / `Aa511F511fa` → dashboard shows ALL users & subscriptions
5. To update the app: rebuild exe → re-upload for download

## Deploy (Local / Standalone)
Copy the exe to any Windows PC and run it.
No Python required. Data is saved next to the exe in `data/` folder.
On first run, `app.db` is created automatically.
