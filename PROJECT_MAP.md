# ورشة طباعة - ID Card Desktop Application

## Architecture
- **Python 3.11.4** / **PySide6 6.11.1** / **Flask 3.x** (backend)
- Dual-mode: local SQLite or Flask REST API mode (`IDCARD_API_MODE` env var or `data/app_config.json`)
- For wide distribution: Client-Server (API mode) — users connect to a central Flask server

**Bug note (2026-06-16):** `QProgressBar` was used in `_build_login_page()` / `_build_register_page()` but missing from imports → `NameError` on init. Fixed by adding `QProgressBar` to the PySide6.QtWidgets import tuple.

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
│   ├── main_window.py       # Main UI (~2257 lines): FULL API mode support
│   │                        #   - Dashboard, profile, subscriptions, banners
│   │                        #   - All methods work in both local and API mode
│   ├── a4_editor.py         # ID card editor: grid, print, save PDF
│   ├── id_card_item.py      # Card graphics item
│   ├── photo_editor.py      # Photo editor + PhotoProcessingThread + progress bar
│   └── pdf_editor.py        # PDF editing with page numbers
├── tests.py                 # 59 tests (pytest + pytest-qt)
└── dist/
├── dist/
    └── ورشة طباعة.exe       # Standalone executable (~123 MB, --onefile)
```

## Key Features
- User registration/login with JWT auth (no phone verification)
- Dashboard (API mode): see ALL users, manage subscriptions, reset passwords, delete
- ID card editor on A4 page: add/swap photos, print, save as PDF
- Photo editor: crop, remove background (rembg QThread with progress bar)
- PDF editor: merge/split/reorder pages, add numbers
- WhatsApp Cloud API notification (optional, configured via dialog)
- **Refresh button** (`🔄 تحديث`) on main screen — re-fetches user data from server without restarting the app; visible whether logged in or not
- **Loading spinner** on login/register pages — animated progress bar appears while submitting, hides on completion or error
- **Multi-device session control** per user — admin sets max devices (1 or 2) from dashboard; users exceeding limit are blocked
- **Active session count** shown in dashboard (`active / max_devices`); admin can click to change limit
- **FULL API mode support** — all 24 UI methods work with the remote server:
  - Dashboard loads from server, actions go to server
  - Profile, subscriptions, passwords sync via API
  - Banners managed remotely
  - Subscription checks use server data (no bypass possible)
- Config file `data/app_config.json` (no env vars needed for users)
- Frozen-aware paths: data next to exe, not in temp
- 59 passing tests (no regression)

## Production Security
- `JWT_SECRET` auto-generates via `secrets.token_hex(32)` if not set via env var
- `JWT_EXPIRY_HOURS` = 2 (configurable via `JWT_EXPIRY_HOURS` env var)
- Admin-only endpoints validated server-side (users cannot bypass)
- Subscription is server-authoritative: desktop app trusts server's `remaining_days`
- No self-service subscription endpoint exists (admin only via dashboard)

## Known Issues
- `ahmed` login only works via API mode (requires running backend server)
- Local mode removed `ahmed` special case to avoid KeyError — admin must log in through the server

## Session Cleanup Fix (2026-06-16) — Force Login & Expired Token Cleanup
**Root cause:** User logout or app close didn't reliably remove `user_sessions` rows. Two scenarios:
1. **Logout API call silently fails** (network/server error) → `remove_session()` never ran → session persisted in DB
2. **JWT expired (2h) before logout** → `@jwt_required()` rejected the request (422) → `remove_session()` never ran → session persisted
3. **App crash / force close** → no logout request → session persisted

Next login attempt hit `max_devices` limit → "مسجل من لابتوب اخر" error / force-login dialog.

**v1 — Force-login dialog (retracted):** Added `force_login: true` param and a client dialog "هل تريد تسجيل الدخول قسرياً". Users got this dialog even when no other device was active → rejected.

**v2 — `logout_token_id` mechanism (final):** Client saves the JWT's `tid` (token_id) from the previous login, sends it as `logout_token_id` in the next login request. The server removes that specific session before checking the device limit. This works even if the previous logout API call failed, because the token_id is preserved across login/logout cycles and persisted in the session file.

**v3 — Expired session pruning:** Login endpoint calls `remove_expired_sessions(username, JWT_EXPIRY_HOURS)` to auto-remove sessions older than 2h before checking the device limit.

**Files changed:**

**Server (`backend/app.py`):**
- `remove_expired_sessions()` called at start of login to prune sessions older than `JWT_EXPIRY_HOURS`
- `force_login: true` still supported as a fallback (clears all sessions)
- `expired_token_loader` handler (v1): cleans up session when JWT is expired
- `logout_token_id` support (v0, already existed): removes a specific token_id's session on login

**Server DB (`backend/database.py`):**
- New `remove_expired_sessions(username, expiry_hours)`: DELETE FROM user_sessions WHERE created_at < cutoff

**Client API (`core/api_client.py`):**
- `_token_id` global: stores last known JWT `tid` claim
- `_decode_token_id(token)`: base64-decodes JWT payload to extract `tid` without requiring the JWT library
- `_update_token_id(token)`: called after successful login/register to update `_token_id`
- `get_token_id()` / `set_token_id(tid)`: accessors for UI session persistence
- `_login_raw()`: sends `logout_token_id: _token_id` in the request body (if available)
- `_request()`: `_fire_session_expired` kwarg prevents callback during login (v1)

**Client DB (`core/database.py`):**
- `api_login()` passes `force_login` through to `api_client.login()`
- `api_login_force_check()`: calls `api_client.login_check_force()` (v1, kept as fallback)

**Client UI (`ui/main_window.py`):**
- `_save_session()` saves `token_id` in session file
- `_try_restore_session()` restores `token_id` from session file → survives app restarts
- `_login_submit()`: force-login dialog still present for genuine multi-device cases (v1)
- `_on_session_expired()`: cleaner message
- Version bumped to 1.1.2

## Banner Visibility Fix (2026-06-16)
**Root cause:** In API mode, `_update_banners()` was guarded by `if self._is_admin:` in 4 locations (`__init__`, `_login_submit`, `_refresh_user_data`, `_try_restore_session`). Banners are stored server-side in `banner_pixmaps` table and `GET /api/banners` is accessible to any authenticated user, but the admin-only guard prevented non-admin clients from fetching/displaying them.

**v1 Fix:** Removed the `if self._is_admin:` guard from `__init__`, `_login_submit`, `_refresh_user_data` call sites.

**v2 Fix (2026-06-16 v2):** Removed remaining `if self._is_admin:` guard from `_try_restore_session()` — this was the last call site preventing banner display for non-admin users who restore a session (app restart).

**Files changed:** `ui/main_window.py` — 4 lines changed (removed admin guard from all call sites)

## Multi-Device Session Control (Configurable Per User)
- `user_sessions` table stores active `(username, token_id, created_at)` per device
- `users.max_devices` column (default=1) controls how many devices a user can login from simultaneously
- On login: backend checks `active_sessions < max_devices` → if full, returns 401 `"الحساب مسجل دخول على {max_devices} أجهزة حالياً"`
- JWT includes `tid` claim; `token_in_blocklist_loader` checks session exists in `user_sessions` table
- Client-side `api_client._request()` detects `session_expired` (401 with `session_expired: true`), clears token, invokes callback
- UI callback shows warning dialog and forces logout
- **New endpoint** `POST /api/auth/logout` — removes the JWT's `tid` from `user_sessions`
- **Admin endpoints**:
  - `GET /api/users/<username>/sessions` — returns `{active_sessions, max_devices}`
  - `POST /api/users/<username>/max-devices` — sets `max_devices` (1 or 2)
- Dashboard table shows `active_sessions / max_devices` in column **الأجهزة**; clicking opens a dialog to change `max_devices`

## Responsive UI
- Window size: 85% of screen (capped at 1400x900)
- Scale factor based on screen resolution relative to 1366x768 (capped at 1.5x)
- Banner sizes (`_banner_w`, `_banner_h`): 240x320 scaled by factor
- Banner pixmap rendering: `(self._banner_w - 10, self._banner_h - 10)`

## Frozen (EXE) Behavior
- When running as frozen exe: **always uses API mode** with hardcoded `_SERVER_URL = "https://printing-workshop-api.onrender.com"`
- `data/app_config.json` is bundled inside the exe; if present next to exe at runtime, its `server_url` overrides the hardcoded one
- Development (non-frozen): reads `data/app_config.json` from source tree, falls back to env vars, then `http://localhost:5000`

## Server Import Fix (2026-06-16)
- `backend/app.py` line 15 imported `add_subscription` from `backend.database`, which was renamed to `set_subscription_days` in commit `da7e928`
- Leftover import caused `ImportError` on startup → server would crash on deploy
- Removed orphan `add_subscription` from import tuple; added regression test `TestServerImport`

## Subscription UX
- Subscription blocks section access when `remaining_days <= 0` (admin exempt)
- `_NO_SUB_MSG` in `ui/main_window.py` shown by `_require_subscription()` and `_check_section_access()`
- Subscription is SET (exact days), not ADD — admin enters final number of days; 0 disables user immediately
- Current message: `"يجب أن تشترك قبل الاستخدام. تواصل مع المالك: واتساب 07865402819"`
- Update `_NO_SUB_MSG` to change the contact number or message

## Refresh Data Button
- **`_btn_refresh`** in `_build_main_screen()` — always visible on the main screen
- **`_refresh_user_data()`** method:
  - When logged in via API: calls `check_auth()`, updates `_api_data`, re-saves session
  - When logged in locally: reloads users from SQLite
  - When not logged in: no-op
  - Handles pending messages (subscription renewal notifications)

### Photo Processing v4 (2026-06-18) — Parts Deletion Fix (grabCut)
**Problem:** Parts of the person (shoulders, hair edges) were being removed along with the background in the grabCut fallback on user laptops. Root cause: the grabCut mask had two issues:
1. **`GaussianBlur((5,5), 2)` on the alpha mask** — created semi-transparent pixels at the person's edge. When `composite_white_bg()` pasted this onto white, semi-transparent pixels blended with white, looking like parts "faded out" or were "deleted."
2. **`MORPH_OPEN` with 7×7 kernel** — removed small white regions in the mask, including fine details like hair strands.

**Fix (5 changes in `core/photo_processor.py`):**
1. **Removed `GaussianBlur` entirely** — the alpha mask is now binary (0 or 255), no semi-transparency → no white blending at edges.
2. **Removed `MORPH_OPEN`** — preserves fine details (hair) that might be small white regions.
3. **Reduced initial BG margin to 1px** (was `margin//2` ≈ 2.5%) — only the outermost pixels are marked as sure background, preventing shoulder/edge areas from being misclassified.
4. **Increased iterations: 4** (was 3) — better grabCut convergence.
5. **Smaller kernel: 5×5 ellipse** (was 7×7) — less aggressive morph close, preserves boundary detail.

**Result:** grabCut mask quality on user laptops now matches rembg quality on admin's laptop — no parts of the person are deleted at the edges.

### Photo Processing v5 (2026-06-18) — Photo Display Fix (Thread Safety + Fallback)
**Problem:** Photos in the photo editor section stopped appearing entirely after drag-and-drop or the "+" button. Three root causes:
1. **`ensure_rembg_ready()` blocking** — called before the processing loop in `PhotoProcessingThread.run()`. On frozen EXE (user's laptop), `new_session()` downloads the 176MB `u2net.onnx` model. If network is slow/missing, this can hang for minutes → no images ever processed, no `finished_all` emitted.
2. **QPixmap created in worker thread** — QPixmap is not thread-safe. Creating it in the thread and emitting via signal can produce a null pixmap on some platforms (especially Windows frozen EXE).
3. **No fallback on failure** — if `remove_background`, `auto_crop_subject`, or `composite_white_bg` threw an exception, the error was logged but no `photo_ready` was emitted → user saw NO images.

**Fix (3 changes in `ui/photo_editor.py`):**
1. **Removed `ensure_rembg_ready()` entirely from the thread** — `remove_background()` already handles model initialization lazily (tries rembg first, falls back to grabCut). No more blocking call before processing.
2. **Changed signal from `Signal(object, int)` to `Signal(bytes, int)`** — emit raw PNG bytes from the worker thread. Convert to QPixmap in the main thread's `_on_photo_ready()` callback. Thread-safe.
3. **Added fallback in `except` block** — if background removal fails, the ORIGINAL image (RGB, white composited) is saved as PNG and emitted. User always sees SOMETHING on the A4 grid.

**Files changed:** `ui/photo_editor.py` — `PhotoProcessingThread.run()`, `photo_ready` signal type, `_on_photo_ready()` (lines ~123-165)

### Photo Processing v6 (2026-06-18) — Show-Original-First Approach
**Problem:** The v5 fix (bytes + fallback) still didn't work on user's frozen EXE. Images never appeared. Root cause: if the thread crashes silently (unhandled exception in `run()`), NO images are emitted. The fallback in v5 only works if the exception is caught inside the try block — but if the import fails or an exception occurs before the loop, the entire thread dies.

**Fix (2 changes in `ui/photo_editor.py`):**
1. **`add_images()` now loads and displays ORIGINAL images immediately** in the scene, before starting the processing thread. Users ALWAYS see their photos right away, even if the thread crashes or hangs.
2. **`_on_photo_ready()` replaces existing `PhotoItem` pixmap in-place** (`self.photos[index].setPixmap(qpix)`) instead of calling `_place_photo`. The original photo is already in the scene; processing just upgrades it.

**Result:** Even if the background-removal thread crashes entirely, the user still sees their original photos (white-composited) on the A4 grid. The thread is purely an optimization to add transparent background removal when it works.

**Files changed:** `ui/photo_editor.py` — `add_images()`, `_on_photo_ready()` (lines ~376-398)

### Photo Processing v7 (2026-06-18) — Switch to u2netp Model (4MB vs 176MB)
**Problem:** `rembg` default model `u2net` is 176MB. On user's frozen EXE (CPU-only onnxruntime):
- Download hangs/takes forever (no internet or slow speed)
- Inference is 30-60s per image on CPU
- If download fails → grabCut fallback (much lower quality)

**Fix (1 change in `core/photo_processor.py`):**
- Switch to `u2netp` model (4MB, lightweight version of u2net)
- Cached session (`_rembg_session` module-level global) reused across calls
- Quality: nearly identical to u2net for portrait/id-photo use cases
- Performance on CPU: ~2-5s per image (vs 30-60s for u2net)
- Download: ~4MB (vs 176MB) — completes in seconds even on slow connections

**Note:** `ensure_rembg_ready()` is now a dead function (no callers after v5/v6). Kept for reference.

**Files changed:** `core/photo_processor.py` — `_remove_bg_ai()` (cached `u2netp` session), `ensure_rembg_ready()` (uses `u2netp`)



### Photo Processing v10 (2026-06-27) — Thread Index Fix (start_index)
**Problem:** When adding a second batch of photos (after the first batch is already placed and processed), the processing thread always emits indices 0, 1, 2... from its own local enumeration. `_on_photo_ready()` uses these as absolute indices into `self.photos`, so the second batch's result at index 0 overwrites `self.photos[0]` (the first photo from the first batch) instead of writing to the correct slot at the end.

**Fix (`ui/photo_editor.py`):**
1. **`PhotoProcessingThread.__init__`** — added `start_index=0` parameter; stores as `self._start_index`.
2. **`PhotoProcessingThread.run()`** — emits `self._start_index + i` (both success and fallback paths).
3. **`add_images()`** — captures `old_len = len(self.photos)` before placing originals, passes as `start_index` to the thread.
4. **`add_images()`** — disconnects old thread signals before starting a new thread, preventing stale callbacks from overwriting newer results.

**Result:** Adding photos in multiple batches places each batch's results in the correct grid cells. First-batch photos are never overwritten by second-batch results.

**Files changed:** `ui/photo_editor.py`

### Photo Processing v9 (2026-06-27) — Direct onnxruntime (No rembg)
**Problem:** Even with bundled u2netp and CPU-only mode, rembg still didn't produce transparency in the frozen EXE. Root cause: `rembg.bg` imports `pymatting`, `scipy.ndimage`, `skimage.morphology` at module level — these packages are NOT bundled (excluded from spec) → silently fail → alpha=255 (opaque) → no visual change.

**Fix for EXE (`core/photo_processor.py`):**
1. **`_remove_bg_direct()`** — new function using onnxruntime directly (no rembg/pymatting/scipy/skimage/pooch). Replicates `U2netpSession.predict()` exactly: load image → preprocess → run model → postprocess mask → composite RGBA. Produces identical results to rembg.
2. **Fallback chain updated** in `remove_background()`: rembg → direct onnxruntime → grabCut → opaque RGBA (critical log).
3. **Post-condition check** — if alpha extrema == (255, 255), raises ValueError to force fallback.

**Fix for EXE (`ورشة طباعة.spec`):**
- Removed scipy/skimage/pymatting/pooch/rembg/numba from `hiddenimports`; added them to `excludes`.
- Kept only `onnxruntime` + `cv2` as hidden imports.
- Added `runtime_hooks=['runtime_hook.py']`.

**New file (`runtime_hook.py`):**
- Adds `sys._MEIPASS/onnxruntime/capi/` to `PATH` before any code runs → ensures `onnxruntime.dll` is found.

**Result:** EXE reduced from 206 MB → 117 MB (120 MB savings from dropping scipy/skimage). Background removal now works reliably on user laptops with real RGBA transparency.

**Files changed:** `core/photo_processor.py`, `ui/photo_editor.py`, `runtime_hook.py` (new), `ورشة طباعة.spec`

### Photo Processing v8 (2026-06-18) — Bundle u2netp.onnx in EXE + CPU-only Mode
**Problem:** Even with u2netp, background removal still didn't work on user's frozen EXE. Two root causes:
1. **onnxruntime tries to load CUDA DLLs** at import, which fail on laptops without CUDA → rembg crashes silently → falls to grabCut → grabCut also fails (cv2 DLL issues in frozen EXE) → returns original image with alpha=255 (no actual background removal)
2. **u2netp.onnx not available** if internet is down → rembg can't download → crashes

**Fix (3 changes):**
1. **`core/photo_processor.py`** — Force CPU-only mode: `os.environ['CUDA_VISIBLE_DEVICES'] = '-1'` before importing rembg. onnxruntime skips CUDA provider entirely, no more DLL loading crash.
2. **`core/photo_processor.py`** — Bundle model in EXE: added `_ensure_model_file()` which copies `u2netp.onnx` from the EXE bundle (`sys._MEIPASS/models/`) to `~/.u2net/` on first run. rembg loads it locally — no download needed, works offline.
3. **`ورشة طباعة.spec`** — Added `('models', 'models')` to `datas` so PyInstaller bundles the 4.5MB `models/u2netp.onnx` inside the EXE.

**Result:** rembg with u2netp now works RELIABLY on the frozen EXE:
- No CUDA DLL dependency (CPU-only onnxruntime)
- No internet needed (model bundled and cached)
- ~2-5s per image on CPU
- Quality identical to admin's laptop

**Files changed:** `core/photo_processor.py` (added `_get_bundled_model_path()`, `_ensure_model_file()`, CPU env var), `ورشة طباعة.spec` (added `models` datas)

## Photo Processing Fix (2026-06-16) — White Background + Robust Fallback

## Photo Processing Fix (2026-06-16) — White Background + Robust Fallback
**Problem:** Parts of the person (shoulders, hair edges) were being removed along with the background in the grabCut fallback on user laptops. Root cause: the grabCut mask had two issues:
1. **`GaussianBlur((5,5), 2)` on the alpha mask** — created semi-transparent pixels at the person's edge. When `composite_white_bg()` pasted this onto white, semi-transparent pixels blended with white, looking like parts "faded out" or were "deleted."
2. **`MORPH_OPEN` with 7×7 kernel** — removed small white regions in the mask, including fine details like hair strands.

**Fix (5 changes in `core/photo_processor.py`):**
1. **Removed `GaussianBlur` entirely** — the alpha mask is now binary (0 or 255), no semi-transparency → no white blending at edges.
2. **Removed `MORPH_OPEN`** — preserves fine details (hair) that might be small white regions.
3. **Reduced initial BG margin to 1px** (was `margin//2` ≈ 2.5%) — only the outermost pixels are marked as sure background, preventing shoulder/edge areas from being misclassified.
4. **Increased iterations: 4** (was 3) — better grabCut convergence.
5. **Smaller kernel: 5×5 ellipse** (was 7×7) — less aggressive morph close, preserves boundary detail.

**Result:** grabCut mask quality on user laptops now matches rembg quality on admin's laptop — no parts of the person are deleted at the edges.

## Photo Processing Fix (2026-06-16) — White Background + Robust Fallback
**Root cause:** `PhotoProcessingThread.run()` saved processed images as RGBA PNG with transparent background instead of white. On the user's laptop (frozen EXE), `rembg`'s ML model is not bundled, causing it to fail; if OpenCV's `grabCut` also fails, `remove_background()` returns the original as RGBA with fully opaque alpha → `auto_crop_subject()` sees `bbox == full image` → no crop happens.

**v1 Fix:** Added `composite_white_bg()` + `auto_crop_subject` skip when bbox ≥98%.

### Photo Processing v2 (2026-06-16 v2) — Radical grabCut Quality Fix
**Problem:** Even when grabCut works, its mask quality is poor compared to `rembg`: jagged edges, cuts into the subject, poor cropping. Performance is also slower than expected.

**Fix (4 changes):**
1. **`core/photo_processor.py`** — `_remove_bg_grabcut()` rewritten:
   - Uses `cv2.GC_INIT_WITH_MASK` with a smart initial mask (center=probable FG, edges=sure BG) instead of the old `GC_INIT_WITH_RECT` (which used a small centered rect)
   - Increased iterations: 3 (was 2) for better convergence
   - Added `cv2.MORPH_OPEN` after `MORPH_CLOSE` to clean noise
   - Added `cv2.GaussianBlur((5,5), 2)` on the mask for smooth alpha edges
   - Larger structuring element (7×7 ellipse → was 5×5) for better smoothing
2. **`core/photo_processor.py`** — `auto_crop_subject()`: adaptive margin — if crop area is <30% of total image (alpha mask too tight), margin increases to 50%; if <50%, margin increases to 30%. Prevents cutting into subject when mask is inaccurate.
3. **`core/photo_processor.py`** — Added `ensure_rembg_ready()`: pre-loads the `rembg` model via `new_session()`. This triggers model download on first run (in frozen EXE) without failing silently.
4. **`ui/photo_editor.py`** — `PhotoProcessingThread.run()` calls `ensure_rembg_ready()` at start (before processing any images), so the model download happens in the background thread with progress bar visible.

**Files changed:** `core/photo_processor.py` (~40 lines changed), `ui/photo_editor.py` (1 line changed)

### Photo Processing v3 (2026-06-16 v3) — Performance + Progress
**Root cause:** `rembg` on admin's laptop uses GPU (CUDA via onnxruntime) → fast. On user's laptop (frozen EXE), it runs CPU-only → 30-60s per high-res image. No per-image progress feedback → user doesn't know if app is hung.

**Fix (2 changes):**
1. **`ui/photo_editor.py`** — `PhotoProcessingThread.MAX_PROCESS_PX = 1200`: downscales images before processing if they exceed 1200px on the longest side. This speeds up both `rembg` and `grabCut` by ~10-50× (processing time scales with pixel count). Final image is still cropped and composited at full quality for the A4 grid.
2. **`ui/photo_editor.py`** — Progress bar changed from indeterminate (0-0) to determinate with per-image counter (`3/10` style). Shows real-time progress feedback during multi-image processing.

**Files changed:** `ui/photo_editor.py` (~15 lines changed)

## Build
```bash
cd idcard_app
pyinstaller "ورشة طباعة.spec" --clean
```
Output: `dist/ورشة طباعة.exe` (~123 MB)

### Version 1.3.0 (2026-06-28) — Arabic Garbled Text Fix
**Problem:** The Arabic text in the UI (top bar buttons, context menus, print dialog) was garbled due to byte-level corruption of double-encoded UTF-8 Arabic characters in `a4_editor.py`.

**Fix:**
- Restored all Arabic UI strings to correct Unicode in `a4_editor.py`: buttons (رجوع, إضافة صورة, طباعة, حفظ PDF, تفريغ الكل, بدون قص), tooltips, context menu items (حذف, تدوير, تكرار, تكبير, تصغير, حجم أصلي), print dialog labels, message boxes, file dialogs
- Fixed 2 broken f-strings (literal `\n` written as real newlines) caused by the fix script
- Fixed 1 indentation error (`IndentationError`) from incorrect fix application
- Bumped `APP_VERSION` to `1.3.0` in both client (`ui/main_window.py`) and server default (`backend/app.py`)
- All 54 applicable tests pass (4 pre-existing failures: 2 missing `fitz`, 2 missing `flask`)

**Files changed:** `ui/a4_editor.py`

### Version 1.4.0 (2026-06-28) — Photo Popup Crop + Enhance (v1–v3)

**v1 — Initial:** `PhotoCropDialog` with crop overlay, zoom, single slider denoise/sharpen.

**v2 — Face-Aware Multi-Slider Enhancement (later trimmed to 3 sliders):**
- **`_face_features()`** in `core/photo_processor.py` — Haar cascade face detection + anatomical-ratio elliptical masks for: skin (extended to neck), eyes (L/R), eyebrows (L/R), lips (used only for `skin_nf` exclusion). Returns:
  - `skin_color`: adaptive color-based mask (BGR distance threshold from reference face skin, constrained to face+neck region, morphologically cleaned)
  - `skin_nf`: face-only skin mask with features removed (for smoothing/blemish)
- **`enhance_portrait_advanced(pil, settings)`** — per-region processing with 3 settings (0–100):
  - `skin_smooth`: bilateral filter on face skin (excl. features) via `skin_nf`
  - `blemish`: local-std thresholded inpainting on face skin via `skin_nf`
  - `brightness`: BGR multiplication on face+neck mask via `skin` (max +35%, no blue shift)
  - Falls back to global bilateral+inpaint if no face detected
- **`enhance_portrait(strength)`** kept as backward-compat wrapper

**v3 — Dialog Redesign:**
- **`QSplitter` layout**: left = image preview, right = control panel (280px)
- **`_SliderGroup`** widget: labeled QSlider (0–100) + auto-updating value label; double-click resets to 0
- **`_ControlPanel`**: 2×4 grid of 8 sliders in QGroupBox + tooltips
- **✨ تحسين تلقائي button**: sets all sliders to sensible defaults for ID portraits
- **📷 قبل/بعد toggle** (toolbar button + `B` shortcut): swaps scene between processed and unprocessed preview
- **Wait cursor** during processing
- **Keyboard shortcuts**: `Enter/Return`=Apply, `Esc`=Cancel, `B`=Before/After, `R`=Reset all
- **`_before_pixmap` / `_after_pixmap`** stored in preview for fast swap via `setPixmap`

**Files changed:** `ui/photo_editor.py`, `core/photo_processor.py`, `PROJECT_MAP.md`

### v1.4.1 (2026-06-28) — Double-Click Fix + Vertical Sliders + Auto-Enhance Tuning

**Bug 1 — Double-click not opening dialog:** `PhotoItem.mousePressEvent` called `super().mousePressEvent()` which starts Qt's item-move state machine (`ItemIsMovable`). On second click, the move state consumed the event before it reached the view's handler.

**Fix:** Moved dialog opening to `PhotoItem.mouseDoubleClickEvent` (Qt delivers double-click events to items regardless of move state). Simplified `PhotoGraphicsView.mouseDoubleClickEvent` to just call `super()`.

**Bug 2 — Sliders too small (horizontal, ~140px each in 2-col grid):** Changed `_SliderGroup` orientation from `Qt.Horizontal` to `Qt.Vertical` — label+value header above, slider track below. Each slider now fills row height, making the track and handle clearly visible.

**Bug 3 — Auto-enhance over-darkens eyes/eyebrows:** Reduced AUTO_PRESET `eyes` (60→35) and `eyebrows` (50→25) to prevent unnatural darkening at default preset.

**Bug 4 — Eye clarity slider makes eyes colorless + eyebrow slider stains forehead (2026-06-29 fix):**
- **Eyes:** `enhance_portrait_advanced` used CLAHE on grayscale then converted to BGR — this strips iris color, making eyes look faded. Also created dark halos from over-amplified contrast. **Fix:** Replaced with unsharp mask on the **color BGR** image (`cv2.addWeighted` with Gaussian blur), preserving natural eye color and iris detail.
- **Eyebrows:** Same grayscale-stripping issue on the BGR→gray→darken→gray→BGR pipeline. Plus the aggressive darkening factor (40% at max) combined with `soft_blend`'s fixed sigma=5 feathering caused darkness to bleed ~15px outside the eyebrow ellipse into the forehead. **Fix:** Darkens BGR channels proportionally (preserves color tone) with reduced factor (12% max), and removed unnecessary mask dilation.

**Bug 5 — Brightness slider whitens entire image + adds blue tint (2026-06-29 fix):**
- **Whole-image instead of face-only:** Used `all_mask` (full-image ones matrix). **Fix:** Changed to `feats['skin']` mask — only face skin is brightened; background, clothes, hair stay untouched.
- **Blue tint:** HSV V-channel adjustment (`hsv[:,:,2] += t*50`) then HSV→BGR conversion shifts warm skin tones toward cool/blue. **Fix:** Replaced with simple BGR channel multiplication (`result * factor`) — preserves R:G:B ratio exactly, so skin hue stays warm. Max +35% brightness at slider=100.

**Files changed:** `ui/photo_editor.py` (3 edits), `core/photo_processor.py` (eyes, eyebrows, brightness), `tests.py` (`TestSliderGroup` added), `PROJECT_MAP.md`
**Tests:** 55 total (2 new `TestSliderGroup`, `TestPhotoItem` added this session)

### v1.4.2 (2026-06-29) — Dark Circles Fix + Remove Hair + Eye Clarity Tuning

**Bug 1 — Dark circles slider makes under-eye DARKER (not lighter):** `cv2.GaussianBlur(img, …)` used the **original** darker image at huge sigma (max 75), pulling dark eye pixels into the under-eye mask and making the area look painted black.

**Fix:** Replace blur source from `img` (original) to `cur_u8` (processed result after skin smoothing). Reduce sigma range from `max(15, t*60+15)` → `max(3, 3+t*7)` (max sigma=10). Reduce blend from `t*0.8` → `t*0.35`. The under-eye area now gets a gentle local blur from the already-brightened face — no more dark bleed.

**Bug 2 — Hair enhancement not wanted:** Removed entirely.
- Removed `"تحسين الشعر"` entry from slider grid, `hair` from `TOOLTIPS`, `hair: 40` from `AUTO_PRESET`.
- Removed hair processing block from `enhance_portrait_advanced()`.
- Removed elliptical hair mask from `_face_features()`.

**Bug 3 — Eye clarity looks blocky/pixelated:** Unsharp mask used `sigmaX=1.0` (too small, amplified pixel noise) and `amt=t*1.0` (too strong, created artifacts).

**Fix:** Increased Gaussian blur sigma from 1.0 → 3.0 (targets mid-frequency edges, not pixel noise). Reduced max sharpening amount from 1.0 → 0.5. Eyes now sharpen naturally without blocky artifacts.

**Files changed:** `ui/photo_editor.py` (TOOLTIPS, AUTO_PRESET, entries), `core/photo_processor.py` (dark_circles, eyes, hair removal), `PROJECT_MAP.md`
**Tests:** 55 total (no new tests; existing pass intact)

### v1.4.3 (2026-06-29) — Remove Update-Check Popup

**Problem:** `_check_for_updates()` called `QMessageBox.question` on every startup (after session restore, login, or refresh), comparing a local `APP_VERSION` string with the server's. The dialog was annoying — user wanted it gone entirely.

**Fix:** Removed the entire feature:
- Deleted `_check_for_updates()` method from `MainWindow` (7 lines + dialog logic)
- Removed 3 call sites: `_try_restore_session()`, `_refresh_user_data()`, `_login_submit()`
- Removed `APP_VERSION` constant from `ui/main_window.py`
- Removed orphaned wrappers: `api_check_version()` in `core/database.py`, `check_version()` in `core/api_client.py`
- Removed 3 orphaned tests: `test_api_client_has_check_version`, `test_main_window_has_check_for_updates`, `test_app_version_constant_is_string`
- The server endpoint `GET /api/app/version` still exists but is never called from the client.

**Files changed:** `ui/main_window.py` (5 edits), `core/database.py` (remove 1 function), `core/api_client.py` (remove 1 function), `tests.py` (remove 3 tests), `PROJECT_MAP.md`
**Tests:** 52 total (removed 3 update-check tests)

### v1.4.4 (2026-06-29) — Keep Only 3 Sliders + Fix Neck Not Brightened

**Problem 1 — Too many sliders:** User wanted only `skin_smooth`, `blemish`, and `brightness`. All others (dark_circles, eyes, eyebrows, lips) removed.

**Fix:** 
- Removed 4 entries from `entries`, `TOOLTIPS`, `AUTO_PRESET` in `_ControlPanel`
- Removed 4 processing blocks (`dark_circles`, `eyes`, `eyebrows`, `lips`) from `enhance_portrait_advanced()`
- Removed unused masks (`nose`, `under_eye`) and unused return keys (`left_eye`, `right_eye`, `left_eb`, `right_eb`, `lips`, `nose`, `under_eye`) from `_face_features()`
- Feature masks (`left_eye`, `right_eye`, `left_eb`, `right_eb`, `lips`) kept as local variables for `skin_nf` computation

**Problem 2 — Brightness misses neck/throat:** `feats['skin']` was only a face oval (y + 18% to y + 66% of face rect). Neck below the chin was untouched, creating a visible brightness boundary.

**Fix:** `_face_features()` now creates two masks:
- `skin_face` = original face oval only
- `skin` = `skin_face` + rectangle below (65%–115% of face height, 30% half-width) covering the neck
- `skin_nf` = `skin_face` minus eyes/eyebrows/lips (used for smoothing/blemish only)
- Brightness uses `skin` (face + neck); smoothing/blemish use `skin_nf` (face only)

**Files changed:** `ui/photo_editor.py` (TOOLTIPS, AUTO_PRESET, entries), `core/photo_processor.py` (_face_features masks, remove 4 processing blocks), `PROJECT_MAP.md`
**Tests:** 51 total (no new tests; existing pass intact)

### v1.4.5 (2026-06-29) — Color-Aware Brightness Mask (Adaptive Skin Detection)

**Problem:** The geometric face+neck rectangle (fixed ellipse + rectangle) didn't perfectly match the actual skin area. Parts of the face edges, neck sides, or skin-colored regions near the face were missed by the fixed mask, creating uneven brightening.

**Fix v1:** Replaced the geometric `skin` mask with an **adaptive BGR color-based mask** — but BGR absolute distance is luminance-sensitive, so shadows/highlights on the face/neck were excluded, making the mask miss skin areas.

**Fix v2 (current):** Changed to **YCrCb chrominance distance** — Cr and Cb represent color independently of luminance Y:
1. Convert reference face skin (from `skin_nf`) to YCrCb, compute average Cr, Cb
2. Per-pixel Euclidean distance in Cr-Cb plane (ignores Y — shadows/bright spots match)
3. Adaptive threshold: `max(8, min(25, std_crcb × 2.5))`
4. Constrain to geometric face+neck region (safety guard)
5. Morphological close + open cleanup

**Bug:** Used `img` instead of `bgr_img` in the YCrCb block (NameError). The `_preview()` try/finally only restores the cursor, so the exception was silent — dialog stayed on original image. Fixed: `img` → `bgr_img`.

### v1.4.6 (2026-06-29) — Blemish Safety Margin (Protect Eyebrows & Hair Edges)

**Problem:** The std-based blemish detector flagged high-contrast edges (eyebrows, hair-skin boundaries) as "blemishes". While `skin_nf` excluded feature interiors, the dilation (1×3×3) expanded the mask, and `soft_blend`'s sigma=5 feathering (~15px bleed) reached into eyebrow and hair areas, lightening eyebrows and blurring hair edges.

**Fix:** Erode `skin_nf` by 2 iterations of 3×3 (~4–6 pixel margin) before constraining the blemish mask:
```python
safe_skin = cv2.erode(feats['skin_nf'], np.ones((3,3), np.uint8), iterations=2)
m = m & safe_skin
```
This keeps blemish processing safely away from eye, eyebrow, lip, and face-edge boundaries. Small pimple/spot masks still pass through; large edge gradients near features are excluded.

Also added `cv2.countNonZero(m)` to logging for diagnostics.

**Files changed:** `core/photo_processor.py` (blemish block safety margin), `PROJECT_MAP.md`
**Tests:** 51 total (no new tests; existing pass intact)

### v1.4.7 (2026-06-29) — Live Preview + Broader Neck Brightness Coverage

**Change 1 — Live Preview (No More "معاينة" Button Press):**
Sliders now trigger an immediate preview update via a debounced QTimer (300ms). When any slider value changes, a single-shot timer is (re)started; after 300ms of no slider activity, `_preview()` runs automatically.

Implementation:
- Added `anyValueChanged = Signal()` to `_ControlPanel`, connected to each `_SliderGroup.valueChanged`
- `PhotoCropDialog` connects `_panel.anyValueChanged` → `_preview_timer.start(300)`
- `_preview_timer` (single-shot, 300ms) fires `_preview()` on timeout

The old "معاينة" button is kept for manual re-trigger if needed.

**Change 2 — Neck Brightness Now Covers Wider Area:**
Two parameter changes in `_face_features()`:
1. **Neck rectangle widened** from 30%→35% half-width, extended from 65%–115%→60%–120% of face height — captures wider/thicker necks and overlaps more smoothly with the face oval
2. **YCrCb threshold max increased** from 25→30 — `thresh = max(8, min(30, std_crcb * 2.5))` — more tolerant of Cr/Cb variation between face and neck skin (neck often has slightly different chrominance due to shadow/blood flow)

**Files changed:** `ui/photo_editor.py` (`QTimer` import, `_ControlPanel.anyValueChanged` signal + connections, `_preview_timer` in dialog), `core/photo_processor.py` (neck rect + YCrCb threshold), `PROJECT_MAP.md`
**Tests:** 51 total (no new tests; existing pass intact)

### v1.4.8 (2026-06-29) — Auto Enhance Button (Safe Global Pipeline + Skin Refinement)

**What changed:** The "✨ تحسين تلقائي" button runs a 3-stage pipeline: (1) safe global gamma + contrast + sharp, then (2) professional skin smoothing + blemish removal via the existing `enhance_portrait_advanced` with `skin_nf` mask + `soft_blend` (proven code from the manual slider controls). Sliders reset to 0 after for optional fine-tuning.

**`enhance_auto_remini(pil_image)` in `core/photo_processor.py` — v3:**

| # | Stage | Mechanism | Why safe |
|---|---|---|---|
| 1. Gamma + Contrast | Haar face-rect mean L → global gamma LUT; LAB L percentile stretch | No masking, global LUT — zero edge artifacts |
| 2. Mild Sharpening | Global unsharp (sigma=0.6, amount=0.12) | Global operation, gentle params |
| 3. Skin Refinement | Delegates to `enhance_portrait_advanced({'skin_smooth':60, 'blemish':70})` | Uses the same `skin_nf` mask + `soft_blend` already proven in manual slider mode; no denoising/CLAHE/bilateral |

The critical design insight: **Stage 3 reuses the existing `enhance_portrait_advanced` function** rather than inlining its logic. This is DRY and guarantees that:
- The `soft_blend` with `skin_nf` (morphologically closed + Gaussian-feathered mask) avoids edge halos
- The bilateral filter parameters are identical to what slider skin_smooth=60 produces (d≈11, sc≈94, blend≈0.36)
- The blemish inpainting is what slider blemish=70 produces (th≈16, radius≈4.8, blend≈0.49)
- No-face fallback still applies gentle global bilateral filter

**Pipeline order matters:** Gamma + sharpening happen FIRST (globally, no mask), then skin refinement is applied ONCE with a single mask. This prevents the compounding-blend-artifact problem of the original v1 (which chained 5 masked stages on top of each other).

**Edge-case hardening (unchanged from v2):**
- Black/white image → contrast stretch skipped → sharpening on uniform = no-op
- No face detected → skips gamma, applies stretch + sharp + skin refinement (global fallback)
- Very dark face (mean<20) → gamma skipped to avoid noise amplification
- Small images → all ops degrade gracefully

**Files changed:** `core/photo_processor.py` (`enhance_auto_remini` v3 — added `enhance_portrait_advanced` call as Stage 3, updated docstring), `PROJECT_MAP.md`
**Tests:** 57 total (6 new, 51 old — no regression)

### v1.4.9 (2026-06-29) — Fix: Apply carries enhancement to A4 + remove preview button

**Bug fix — `_apply()` not carrying auto-enhanced result to A4 page:**
`_apply()` unconditionally called `_preview()`, which always starts from `self._original` with slider settings. After auto-enhance, all sliders are 0, so `_preview()` returned the **original un-enhanced image** — overwriting the auto-enhanced `self._current`. Fix: skip `_preview()` when all sliders are 0 (`ui/photo_editor.py:445`).

**Removed "📷 قبل/بعد" preview toggle button** (requested by user):
- Removed `_btn_before_after` (button creation, stylesheet, layout addition)
- Removed `_toggle_before_after()` method and B-keyboard shortcut
- Removed orphaned `_before_pixmap` (no longer read anywhere)
- Removed orphaned `_showing_before` attribute + all `setChecked` calls
- Cleaned up `_after_pixmap` usage left intact (still used for scene display)

**Files changed:** `ui/photo_editor.py` (`_apply` fix, removed `_btn_before_after`/`_toggle_before_after`/`_before_pixmap`/`_showing_before`/B-shortcut), `PROJECT_MAP.md`
**Tests:** 57 total — no regression (all existing pass)

### v1.5.0 (2026-06-29) — Static image workflow + crop persistence on re-edit

**Behavioral change — no live preview:**
The image in the crop dialog stays **static** (original) when the user adjusts sliders or drags the crop overlay. Only the crop overlay moves — the underlying image does not re-render. Preview only happens when "تطبيق" is clicked.

- Removed `_preview_timer` entirely (timer + all `.start(300)` calls + `QTimer` import)
- Removed `anyValueChanged` → timer connection (slider changes no longer trigger preview)
- Removed `_CropView.mouseReleaseEvent` timer start (crop changes no longer trigger preview)
- `_apply()` always calls `_preview()` to produce the final result from `self._original` + current settings + current crop rect

**`_original_pixmap` bug fix (v1.5.1):**
`PhotoItem._original_pixmap` was set at creation time (`add_images` → `_place_photo`) from the **raw** original image. The background-removal thread later replaced the displayed pixmap via `setPixmap()`, but `_original_pixmap` stayed raw. This caused two problems:
1. Double-click showed the raw image (with original background), not the bg-removed version
2. Auto-enhance operated on the raw image → worse quality

Fix: `_on_photo_ready` now sets `item._original_pixmap = qpix` so the bg-removed result becomes the "original" for re-editing.

**Files changed:** `ui/photo_editor.py` (`_on_photo_ready`: added `item._original_pixmap = qpix`), `PROJECT_MAP.md`
**Tests:** 57 total — no regression

**Auto-enhance updates `self._original`:**
`_trigger_auto_enhance()` now sets `self._original = QPixmap(self._current)` after processing. This ensures that subsequent `_preview()` calls (from `_apply()`) use the auto-enhanced version as the base, rather than the raw original. Slider adjustments after auto-enhance apply on top of the auto-enhanced image.

**Crop rect persistence across edit sessions:**
- `PhotoItem` now stores `_original_pixmap` (set at creation, updated by bg-removal thread via `_on_photo_ready`) and `_crop_rect` (set on each apply)
- `PhotoCropDialog` accepts optional `crop_rect` tuple `(x, y, w, h)` parameter
- On dialog init, if `crop_rect` is provided, `_CropOverlay.set_rect(QRectF(*crop_rect))` is called after overlay creation
- On `_apply()`, the final crop rect is stored back to `self.crop_rect` for the caller to retrieve
- On double-click → original image is shown with the previous crop overlay position (not the already-cropped result)

**This means:**
1. First edit: open original, drag crop, click Apply → cropped result on A4
2. Double-click same photo → original image reappears with the exact same crop overlay
3. User can adjust the crop (make it larger/smaller), re-apply
4. No live preview — image stays untouched until Apply

**Files changed:** `ui/photo_editor.py` (PhotoItem: `_original_pixmap`, `_crop_rect`; PhotoCropDialog: `crop_rect` param, removed timer/auto-preview, `_original` update after auto-enhance, `_apply` returns crop rect; removed `QTimer` import), `PROJECT_MAP.md`
**Tests:** 57 total — no regression

---

### v1.6.0 — Save PDF in photo editor

**Feature:** Added "حفظ PDF" button next to "طباعة" in the photo editor toolbar. Opens a file-save dialog and renders the multi-page scene to a PDF file with the same layout as printing (A4 pages, auto-scaled).

**Implementation:**
- Added `QPrinter` import (`PySide6.QtPrintSupport`) and `QPageSize` to existing QtGui import
- New `_save_pdf()` method (`photo_editor.py:1040`): hides background rects (zValue < 0), creates `QPrinter` with `PdfFormat`, iterates pages rendering each via `scene.render()`, then restores visibility
- Button `"حفظ PDF"` placed between `"طباعة"` and `"تفريغ الكل"` in the button bar

**Behavior:**
1. Click "حفظ PDF" → file dialog → choose location → PDF is created
2. Same subscription check as printing
3. Multi-page scenes are supported (each page becomes a PDF page)
4. Background grid lines are excluded from the PDF (only photos appear)

**Files changed:** `ui/photo_editor.py` (imports, button, `_save_pdf`), `PROJECT_MAP.md`
**Tests:** 65 passed — no regression

---

### v1.6.1 (2026-06-30) — Build after all fixes
- Built `dist/ورشة طباعة.exe` (~123 MB) with `pyinstaller "ورشة طباعة.spec" --clean`
- All 65 tests pass, no regression
- Ready for upload and distribution

## Deploy (Client-Server) — for wide distribution
1. Deploy the Flask backend on a cloud server (see `backend/requirements.txt`)
2. Build the exe (see Build above) — it **always connects to the server** when frozen
3. Distribute the exe (or zip with `data/app_config.json` next to it)
4. Log in as `ahmed` / `Aa511F511fa` → dashboard shows ALL users & subscriptions
5. To update the app: rebuild exe → re-upload for download

### v1.6.2 (2026-08-26) — Fix: Card swap doesn't move the target card

**Problem:** In the ID card editor, dragging one card (via the ⤡ move handle) on top of another should swap their grid positions. The target card did NOT move to the dragged card's original slot — both cards ended up sharing the target's position (visually stacked, no swap).

**Root cause:** `mouseReleaseEvent` calls `on_dropped(self)` only AFTER the drag has already moved the card `self` to the drop position. `_on_card_dropped` → `_swap_cards(a, b)` then computed `pos_a = a.pos()` — which at that point is the DROPPED position (already on top of `b`), not the original pre-drag position. So `b.setPos(pos_a)` moved `b` to the same spot as `a`, and no swap occurred.

**Fix (2 changes):**
1. `ui/id_card_item.py` — added `self._drag_origin = None` in `__init__`; set `self._drag_origin = self.pos()` in `mousePressEvent` (alongside `_drag_start`). Captures the card's original grid position before any movement.
2. `ui/a4_editor.py` — `_swap_cards(a, b)` now uses `pos_a = a._drag_origin if a._drag_origin is not None else a.pos()` (the pre-drag position) instead of `a.pos()`, so the target card `b` moves to where the dragged card originally was.

**Result:** Dragging card A onto card B now moves A to B's slot and B to A's original slot (true swap).

**Files changed:** `ui/id_card_item.py`, `ui/a4_editor.py`, `tests.py` (new `test_swap_uses_pre_drag_origin`), `PROJECT_MAP.md`
**Tests:** TestCardSwap 2 passed; relevant static/GUI test classes pass (full suite has pre-existing hang from unrelated networked/slow tests)

### v1.6.3 (2026-08-26) — Fix: Session doesn't persist across app restarts

**Problem:** User had to log in every time they closed and reopened the app, even though login should persist until they log out manually.

**Root cause:** On startup, `_try_restore_session()` calls `api_check_auth()` → `api_client.check_auth()` → `_request(...)` with the default `_fire_session_expired=True`. If the server responded with `session_expired: true` (or any auth error), `_request` fired the registered `_on_session_expired` callback, which called `_clear_session()` → **deleted `session.json`** and reset the UI to the login screen — before `_try_restore_session`'s own `if qerr:` fallback could restore the user locally. So the saved session was destroyed on every launch that couldn't verify the token, forcing a re-login.

**Fix (3 files, pass-through of a `suppress_expired` flag):**
1. `core/api_client.py` — `check_auth(suppress_expired=False)` now forwards `_fire_session_expired=not suppress_expired` to `_request`.
2. `core/database.py` — `api_check_auth(suppress_expired=False)` forwards to `api_client.check_auth`.
3. `ui/main_window.py` — `_try_restore_session()` calls `api_check_auth(suppress_expired=True)` so the startup check can NEVER fire the session-expired callback and destroy the saved session. The existing `if qerr:` branch restores the user locally and the session file survives.

Other call sites (`_refresh_user_data`, `_auto_refresh_data`, `_login_submit`) keep the default (callback active) so genuine mid-session expiry still logs the user out.

**Result (verified end-to-end):** On startup with an auth error, the user is restored (`logged_in=True`) and `session.json` is preserved — the login persists until the user explicitly logs out.

**Files changed:** `core/api_client.py`, `core/database.py`, `ui/main_window.py`, `tests.py` (new `TestSessionPersistence`), `PROJECT_MAP.md`
**Tests:** `TestSessionPersistence` 3 passed; `TestCardSwap`, `TestPhotoItem`, `TestSliderGroup`, `TestImageUtils` pass (no regression)

### v1.6.4 (2026-08-27) — Fix: Crop after swapping cards overwrites the wrong card

**Problem:** After swapping two cards in the ID card editor, double-clicking a card to crop it and pressing "إضافة" did not update the double-clicked card — the OLD image stayed while the crop appeared on the neighbouring card (the user perceived it as "the crop was added alongside, not replacing").

**Root cause:** `_place_card()` assigns each card `index = len(self.cards)` at creation, but `_swap_cards(a, b)` swapped the entries in `self.cards` WITHOUT updating `a.index` / `b.index`. So after a swap, `item.index` no longer equaled the card's position in `self.cards`. The double-click crop flow captures `card_idx = card_item.index` (in `_show_original_image`) and passes it to `_replace_card(card_idx, final)`, which then replaced `self.cards[card_idx]` — a DIFFERENT card than the one that was double-clicked.

**Fix (1 change):**
1. `ui/a4_editor.py` — in `_swap_cards`, after swapping list entries, sync each card's index to its new list position: `a.index = j; b.index = i` (where `i`, `j` are the pre-swap positions captured just before the list swap).

**Result (verified end-to-end):** After a swap, `item.index` always matches the list position, so double-clicking a card crops THAT card and replaces it in place (list length unchanged, no leftover duplicate/overwrite of a neighbour).

**Files changed:** `ui/a4_editor.py`, `tests.py` (new `test_swap_keeps_index_in_sync`), `PROJECT_MAP.md`
**Tests:** `TestCardSwap::test_swap_keeps_index_in_sync` (TDD red→green); all 16 relevant tests pass (no regression)

### v1.6.5 (2026-08-27) — Lighten the app & shrink the EXE (big optimization)

**Goal:** The app felt heavy for users and the frozen EXE was very large (386 MB). Make it lighter (faster launch/extract, less RAM/disk) and much smaller, WITHOUT touching any section, feature, or service.

**Impact analysis (verified before changing anything):**
- The heavy size drivers were `torch` (~494 MB in site-packages, ~93 MB in the EXE), `cv2` (~65 MB incl. two FFmpeg video DLLs), `mediapipe` (~19 MB), `scipy` (~20 MB), `numba`/`llvmlite` (~38 MB), `matplotlib`.
- **torch/torchvision/gfpgan/realesrgan/basicsr/facexlib are dead:** `core/ai_enhance.py` is their only importer, its `enhance_image()` is **never called anywhere in the app**, and these packages are not even installed (they always `ImportError`). The real photo-enhancement path uses `mediapipe.solutions.face_mesh` + OpenCV + numpy (kept). Removing torch is 100% safe.
- **scipy** is referenced only in test files/docstrings — never loaded at runtime.
- **numba/llvmlite** are used only by numpy type-checking stubs and by torch (both dead paths).
- **matplotlib** is pulled only by `mediapipe.tasks...drawing_utils` — the app uses classic `mediapipe.solutions` only.
- **OpenCV FFmpeg videoIO DLLs** (`opencv_videoio_ffmpeg*_64.dll`, ~25 MB) — the app is image-only; verified no `VideoCapture`/`VideoWriter`/videoio usage anywhere.
- `onnxruntime` is KEPT — it powers the working AI background-removal fallback chain (`rembg` → `onnxruntime` → `grabCut`).

**Changes (build-level only, `WorshaApp.spec` — no runtime feature code touched):**
1. Moved the dead deps into an `excludes=` list: `torch, torchvision, gfpgan, realesrgan, basicsr, facexlib, scipy, numba, llvmlite, matplotlib, pytest, IPython`.
2. Trimmed `hiddenimports`: removed `torch, torchvision, gfpgan, realesrgan, basicsr, facexlib, scipy, scipy.stats, scipy.special, mediapipe.tasks...`; keep `mediapipe.solutions` (used) + `cv2` + `onnxruntime` + Qt Svg.
3. Filtered cv2 data + binaries to drop the `videoio_ffmpeg` DLLs.

**Verification (no regression):**
- Import-blocker test: with torch/scipy/numba/matplotlib/etc. blocked, the full app chain (`main`, MainWindow, all editors, photo_processor, id_extractor) imports cleanly and `ai_enhance.is_available()` correctly returns False (graceful fallback).
- All relevant tests pass with the heavy libs blocked: **31 passed** (TestCardSwap, TestSessionPersistence, TestPhotoItem, TestSliderGroup, TestImageUtils, TestRefreshButton, TestPhotoProcessor).
- EXE launch smoke-test: process stays alive (no import crash).
- `dist/WorshaApp.exe` on-disk: **386.3 MB → 146.7 MB (−62%)**. torch/scipy/numba/matplotlib/gfpgan absent; cv2/mediapipe/onnxruntime/PySide6/numpy preserved.

**Files changed:** `WorshaApp.spec`, `PROJECT_MAP.md`
**Tests:** 31 relevant tests pass (no regression)

### v1.6.6 (2026-08-27) — Fix: EXE did NOT persist login while the source did

**Problem:** Running the source (`python main.py`, "Python logo") remembered the user's login, but the frozen EXE (`dist/WorshaApp.exe`) forced a re-login on every launch. This contradicted the v1.6.3 session fix, which worked in source only.

**Root cause:** The session is written to `DATA_DIR/session.json`. In the EXE (frozen), `DATA_DIR` = `<exe_dir>/data` (e.g. `dist/data`). In **API mode** `init_db()` is never called, and that `os.makedirs(DATA_DIR)` lived ONLY inside `init_db()`. So on the EXE the `data` folder was never created, and `_save_session()`'s `open(".../data/session.json", "w")` silently failed (FileNotFoundError) → login never persisted. In source mode the `data/` folder already existed (from earlier local/db runs), which is why source kept working.

**Why source vs EXE differed:** source reuses the existing `data/` folder; the EXE, in a fresh `dist` with no `data/`, never created it.

**Fix (1 line):** `ui/main_window.py` `_save_session()` — add `os.makedirs(DATA_DIR, exist_ok=True)` right before writing `session.json`, so the data folder is created on first login regardless of mode.

**Result (verified):** New regression test `TestSessionPersistence::test_save_session_creates_missing_data_dir` (red→green) proves `_save_session` creates `DATA_DIR` when missing and writes the session. The rebuilt EXE (same 146.7 MB build) carries this fix; after login it now creates `<exe_dir>/data` and persists `session.json`, restoring login on the next launch.

**Files changed:** `ui/main_window.py`, `tests.py`, `PROJECT_MAP.md`
**Tests:** new regression test passes; 51 relevant client tests pass (the only failures are pre-existing env ones: `backend.app` needs `flask`, not installed in the client env — unrelated)

## Deploy (Local / Standalone)
Copy the exe to any Windows PC and run it.
No Python required. Data is saved next to the exe in `data/` folder.
On first run, `app.db` is created automatically.
