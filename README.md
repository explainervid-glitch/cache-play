# Cache Play v2

After Effects style cached preview for Blender's 3D Viewport. Blender 5.1 or newer.

Cache Play draws each frame of your range once, stores the pictures, and then plays them back on its own clock at exactly the scene frame rate. During playback Blender does not recalculate the scene, so a heavy shot plays as smoothly as a video.

v2 adds a disk cache (like After Effects 25.2+ "Preview from Disk Cache"): every frame is saved to disk, RAM only holds the frames just ahead of the playhead, so a shot can be much longer than your RAM. It also adds **Cache When Idle**.

## Install

1. In Blender: Edit > Preferences > Get Extensions > the drop-down arrow (top right) > **Install from Disk…**
2. Pick `dist/cache_play-0.2.19.zip` (it replaces an older version). Blender asks to allow file access: that is the disk cache, and the feature-use counts for zeploy (see below).
3. The panel is in the 3D Viewport sidebar (press N) under the **Cache Play** tab.

### While developing: link instead of installing

On this machine Blender 5.2 is **linked** to the project folder (since 2026-10-26): Blender's installed `cache_play` folder is a shortcut (a symbolic link) to `cache_play/` here. Every code change shows up after restarting Blender; no zip, no reinstall.

```bash
powershell -ExecutionPolicy Bypass -File dev_link.ps1
```

- `-Version 5.1` links another Blender version. `-Remove` removes the link and puts the previously installed copy back (it was kept in `%APPDATA%\Blender Foundation\Blender\5.2\cache_play_installed_backup`).
- It needs Windows Developer Mode (on here), because the project lives on the network drive Z: and only a real symbolic link can point there.
- While linked, do **not** use Uninstall or Update on Cache Play inside Blender. Use `dev_link.ps1 -Remove`.
- Blender writes a `__pycache__` folder into `cache_play/`; the zip build leaves it out.

To build a zip for other machines:

```bash
"C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" --factory-startup -c extension build --source-dir cache_play --output-dir dist
```

## Use

1. Cache Play always caches **through the scene camera**. If the viewport is not in camera view, Cache & Play, Cache Only and Cache Mode switch it to camera view first (like Numpad 0). The viewport only decides the look (shading, overlays). Zooming, panning or even orbiting the viewport afterwards never makes the cache out of date. No scene camera means no caching: Cache Play says so.
2. Press **Cache & Play**. Cache Play steps through the range (the preview range if one is set), stores each picture, and starts playing when done.
3. Press **Cache & Play** again later: frames already cached are not drawn again.

The bar under the frame numbers works like After Effects. It shows in the Timeline, Dope Sheet, Graph Editor and NLA Editor, and so does the cache playhead (with its frame number) while playing or in Cache Mode:

- **Green**: in RAM, ready to play.
- **Blue**: on disk only. It is loaded into RAM just before the playhead reaches it.
- **Empty**: not cached.

While playing:

| Key | Does |
| --- | --- |
| Space | Pause / resume |
| ← → | Step one frame (pauses) |
| Esc, any other key, or a click | Stop. The scene jumps to the frame that was showing |
| Mouse wheel, Shift, Ctrl, Alt | Ignored, playback continues |

While caching, Esc stops early and keeps the frames done so far.

Shortcut: **F5** (change it in the add-on preferences).

## Cache Mode

**Cache Mode** is a toggle, in the panel and at the right end of the Timeline header. While it is on, the viewport shows the cached picture at the yellow-orange cache playhead, and you scrub that playhead instead of Blender's own. Blender's playhead (the blue one, the scene's real frame) never moves, so the scene is never recalculated.

- Turning it on never re-caches. If frames are cached, it shows them straight away, even if only part of the range is cached (gaps are skipped) or the cache was made from another viewport or with other settings. Only when nothing is cached yet does it cache the range first (Esc skips the rest). It pauses at the frame Blender's playhead is on.
- To refresh an out-of-date or partial cache, use **Cache & Play** (or Cache When Idle, outside Cache Mode).
- Pressed in the Timeline header, it uses the 3D Viewport the cache was made from.
- **Drag** anywhere in the Timeline, or in the frame-number bar at the top of a Dope Sheet, Graph Editor or NLA Editor, to move the cache playhead. Clicks lower down in those editors still select keys as usual.
- **Space**: play / pause from the cache playhead. **← →**: step one frame. **Shift ← →**: first / last frame.
- Everything else keeps working: other clicks and shortcuts go to Blender as normal.
- **Esc** or the toggle turns it off. Blender's playhead is exactly where it was.
- If an edit clears the cache, Cache Mode turns itself off.

Difference from **Cache & Play**: Cache & Play stops on any key or click and then moves Blender's playhead to the frame you stopped on (if Move Playhead on Stop is on). Cache Mode stays on while you work and never moves Blender's playhead.

Shortcut: **F6**, press again to leave (change it in the add-on preferences).

## Cache When Idle

Tick **Cache When Idle** in the panel. Like After Effects, after a few seconds without working **in Blender** (Idle Delay, default 3 s), Cache Play fills missing frames one at a time, starting at the playhead. Each frame is drawn by jumping to it and straight back before anything redraws, so the playhead does not move.

What pauses it (and restarts the wait) is your choice: Preferences > Add-ons > Cache Play > **Idle Activity**. Tick any of:

| Idle Activity | Default | Counts as working |
| --- | --- | --- |
| Keys & Clicks in Blender | on | pressing a key or mouse button, or turning the wheel, while Blender is the window in front |
| Edits, Frame & View Changes | on | changing the scene, going to another frame, orbiting / panning / zooming the viewport |
| Mouse Movement over Blender | on | just moving the mouse while Blender is in front |
| Input in Other Programs | off | any key, click or mouse move while another program (After Effects, a browser…) is in front |

With the defaults, anything you do in Blender, even just moving the mouse over it, pauses it; working in other programs does not: when another program is in front, Blender counts as idle and keeps caching. Untick **Mouse Movement** if only keys, clicks and edits should count. Tick **Input in Other Programs** if you'd rather it only run when the whole computer is idle.

- A status line under the checkbox says what it is doing: e.g. "Waiting for 3 s without activity", "Caching frame 42", "All frames cached", or why it is paused.
- It also runs while **Cache Mode** is open and paused, and the new frames can be scrubbed straight away. It pauses while cached playback runs.
- It shoots through the camera too, so it keeps working while you orbit around in a free view, and it never changes your view.
- It never replaces a cache made with other settings (size, shading, colour); the status line then says to use Cache & Play.
- It waits while you transform something, while Blender plays or renders, and in Edit or Sculpt mode (Object and Pose mode only).
- One step costs about two frame calculations. On the 55 ms/frame test scene a step took about 150 ms, so the first moment after you come back can feel a short hitch.
- Keys, clicks, wheel and mouse are read from Windows, because Blender's Python API cannot see them. On macOS (untested) any input in the last half second counts as Keys & Clicks, in any program. On Linux only Edits, Frame & View Changes can be detected.
- Some add-ons keep a background operator running all the time. Cache Play waits 20 s, then treats it as background and ignores it. On this machine `ppf_contact_solver` does this (`PPF_OT_frame_pump`); a permanently running operator like that also stops Blender's own autosave.

## Settings

**Viewport toolbar** (default): a rounded "pill" fixed at the bottom centre of every 3D Viewport. Left to right:

- **⌄ chevron**: folds the pill down to a small round tab (^); click again to open it. Remembered between sessions.
- **▶ Cache & Play** · **🎞 Cache Mode** (orange circle while on; a grey circle shows which button is under the mouse) · **🗑 Clear** · **⚙ Settings**
- how much is cached, e.g. "48/96", then **⤴ Export** (save the cached preview as a video, see Export below).

**⚙ Settings** opens its drop-down on a normal click (no need to hold the button) and it stays open until you move the mouse away. Inside, top to bottom: **Cache Only** | **Export**; Preview Size; **Loop** | **Cache When Idle**; a box with the cached-frames line and a 🗑 Clear button beside it (plus any warning, and what idle caching is doing while frames are missing); **Cache Details** and **Statistics**, folded.

It adapts to the viewport's width: on narrow viewports it first drops the count, then Clear, Settings and Export (▶ and 🎞 always stay). While Cache & Play or Cache Mode runs, a small label sits just above the pill, e.g. **CACHE MODE** 12 · scene 0 · paused (the cache frame, then Blender's own frame). On narrow viewports it shortens to the frame number. Hover a button for its name; hovering never changes the mouse cursor. The toolbar stays visible over the cached picture in Cache Mode. It hides with the viewport's Show Gizmos toggle (the arrow icon in the viewport header).

**Shortcuts**: **F5** = Cache & Play, **F6** = Cache Mode (press again to leave). Blender 5.1/5.2 leave F5–F8 unused by default. They work in any editor. Change them in Preferences > Add-ons > Cache Play > Shortcuts.

**Timeline bar** (optional: Preferences > Toolbar > Timeline Header or Both): in the Timeline header between Blender's **Playback** drop-down and the Auto Keying button, left to right:

Joined into small groups:

- **Cache & Play** | **Cache Mode** (toggle)
- how much is cached, e.g. "48/96" (a red warning icon if the cache is full or the disk fails) | **Export** | **Clear** (bin)
- **Preview Size** drop-down | **Loop** | **Cache When Idle** (toggles)
- **⚙ drop-down**: Cache Only | Export, the cached-frames box with Clear, Cache Details and Statistics (folded).

On a Timeline narrower than about 1750 px, Cache & Play and Cache Mode show icons only (▶ and film strip) so the whole bar fits; hover them for the name. Other Dope Sheet modes show only the Cache Mode toggle.

Sidebar panel (3D Viewport sidebar, **Cache Play** tab), always there, with the same controls, top to bottom:

- One joined 2 × 2 block: **Cache & Play** | **Cache Mode** (toggle), then **Cache Only** | **Export**.
- **Preview Size**: Full | Half | Third | Quarter, joined to the **Loop** | **Cache When Idle** toggles below it. Full is the render resolution fitted inside 1920×1080 (1080×1920 for vertical); the others divide it. Smaller caches faster and uses less space.
- A status box: "All 250 frames cached", "48 of 96 frames cached" or "Nothing cached yet" with the 🗑 **Clear** button at its right end, a red line if the cache is full or the disk fails, and, while frames are missing, what idle caching is doing.
- **Cache Details** (folded): camera, picture size, MB per frame, RAM and disk use as two neat columns, and a **Limits & Folder…** button that opens the add-on preferences.
- **Statistics** (folded): how smoothly the last cached playback ran.

Add-on preferences:

- **RAM Limit** (default 4 GB): the most RAM the frames may use. With the disk cache on, older frames move out to disk; with it off, caching stops here.
- **Disk Cache** (on), **Cache Folder** (empty = Blender's temporary folder, else the system temp folder, in a `CachePlay` subfolder), **Disk Limit** (default 20 GB; caching stops there). Use a fast SSD.
- **Idle Delay** (3 s) and **Idle Activity** (see Cache When Idle), **Move Playhead on Stop** (on).
- **Fast GPU Caching** (on): the speed-ups below (graphics card packs the pixels, overlapped steps). Off = the older, slower way, as a fallback if cached frames ever look wrong with a graphics driver. Both give identical pictures in tests.
- **Toolbar**: 3D Viewport (default), Timeline Header, or Both.
- **Shortcuts**: F5 and F6, editable.

Size per frame: Full 1080p ≈ 8 MB, Half ≈ 2 MB, Quarter ≈ 0.5 MB. 10 seconds at 24 fps is about 2 GB at Full, 0.5 GB at Half.

The v1 **Storage: RAM / GPU** option is gone. Tests showed no difference in smoothness, and the RAM tier now does the job.

## Export

**Export** saves the cached preview as an **MP4 video (H.264)** at the scene's frame rate and the cached picture size, like After Effects' "Save Cached Preview". It opens a file browser; the default name is `<blend name>_preview.mp4` next to the .blend file.

- Only cached pictures are used, nothing is rendered again, so it is quick.
- Frames that are not cached repeat the nearest cached frame before them, so the video keeps the right length and timing. The report at the bottom of the window says how many frames were filled in that way.
- Colours are copied as they look in the viewport (no extra colour transform).
- No sound yet.
- It is disabled while caching or playing; pause or stop first.
- Your scene is not changed: Cache Play builds the video in a temporary scene and deletes it afterwards.

## When the cache is thrown away

- Any real edit: moving keys, changing a material, a modifier, a light, and so on. One edit clears everything (smart per-range clearing is v3).
- Not cleared by: changing frames, scrubbing, normal Blender playback, selecting things, changing Cache Play settings, opening or closing a window such as the Export file browser.
- A different view, preview size, shading mode, colour management or render resolution makes the next **Cache & Play** start over, because the old pictures no longer match.
- Not caught automatically: render-engine settings (e.g. EEVEE options in Rendered shading). Press **Clear** after changing those.
- Disk files belong to one Blender session. They are deleted when the cache is cleared and when Blender closes. Leftovers from a crash are deleted the next time Cache Play uses the disk. Reusing frames after reopening a file is v3 ("name tags").

What you see is what gets cached, including overlays (grid, outlines, bones). Turn overlays off first for a clean picture.

## Usage counts for zeploy

On PCs where the studio's deployment tool zeploy is installed, Cache Play counts which features get used, so the zeploy dashboard can show how much it helps. Each press of **Cache & Play**, **Cache Only** or **Cache Mode** (from any button, the toolbar or F5/F6), **Export** and **Clear** adds one line to `%LOCALAPPDATA%\zeploy\usage\cache_play.jsonl`; zeploy's agent collects the file every minute. A failure while caching or exporting counts as an error. Cache When Idle is not counted: it runs by itself. No file names, scene data or user names are recorded, Blender sends nothing over the network, and on a PC without zeploy nothing is written. `cache_play/zeploy_usage.py` is zeploy's helper, copied unchanged; the counting is in `ops.py` and `export.py`.

## Test results

Test scene: 24 subdivided, wave-deformed monkeys (760 000 vertices). Blender needs 55 ms to calculate each frame, so normal playback cannot reach 24 fps (best case about 18 fps). 10 seconds of looping playback, RTX 4060, clean Blender 5.2, Vulkan:

| Size | RAM limit | Disk | In RAM | Frames dropped | Timing jitter | Worst frame | Late disk reads |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Half | 4 GB | on | all 120 | 0 of 240 | 0.8 ms | 2.1 ms | 0 |
| Full 1080p | 0.25 GB | on | 32 of 120, rest streamed | 0 of 240 | 0.8 ms | 2.4 ms | 0 |
| Half | 4 GB | off | all 120 | 0 of 240 | 1.0 ms | 5.8 ms | 0 |

**Caching speed** (v0.2.17, same scene, Cache Only, clean Blender 5.2, `tests/profile_fill.py`). Where one frame's time goes:

| Step | Vulkan | OpenGL |
| --- | --- | --- |
| Blender works out the frame (the floor: one main thread) | 54 ms | 54 ms |
| Drawing it (Blender prepares the meshes for the graphics card) | 20–30 ms | 55–65 ms |
| Waiting for the graphics card to finish | 12–35 ms | 20–45 ms |
| Copying the picture back and packing it | 3–8 ms | 2–6 ms |
| Blender redrawing its window between steps | ~30 ms | small |
| **Cache Only, before (v0.2.16)** | **~127 ms** | **~157 ms** |
| **Cache Only, now** | **~86–96 ms** | **~104–131 ms** |

Three changes (Preferences > Fast GPU Caching turns the first two off):

- **Overlap**: a frame is drawn, then the next frame is worked out while the graphics card finishes the drawing, and only then is the picture copied back. The wait mostly disappears.
- **No packing on the CPU**: the graphics card writes the packed pixels directly (it stamps the unused alpha byte), checked bit for bit against the old packing on Vulkan and OpenGL.
- **Fewer window redraws**: Blender redraws the screen every 0.25 s while caching instead of every 0.1 s. Esc still answers within a quarter second.

Blender's own frame calculation (54 ms here) stays the limit: one Blender cannot cache this scene faster than about 18 frames per second. Lighter scenes gain more in proportion. Numbers jump around by ±15 ms between runs on this machine.

"Timing jitter" is how far each frame lands from its ideal moment (at 24 fps a frame lasts 41.7 ms). "Late disk reads" counts frames the reader thread had not loaded in time. Filling 120 frames took about 14 seconds.

Caveat: the frames had just been written, so Windows probably still had them in its file cache (reads measured ~3.8 GB/s). Reading from a cold disk is slower. Full 1080p at 24 fps needs about 200 MB/s, which any SSD manages.

Automatic tests, all passing on Blender 5.1.1 and 5.2.0:

- `test_controls.py`: 17 of 17. Keys, clicks, pause, step, stop, Esc while caching, refill on size change.
- `test_idle.py`: 10 of 10. Starts by itself; playhead never moves; pauses on input; finishes the range; saves to disk; rebuilds after an edit; keeps working in a free view without changing it. 
- `test_cache_mode.py`: 19 of 19. Enter (with caching), drag in the Timeline and in the Graph Editor frame bar, a lower Graph Editor click left to Blender, play, click elsewhere, step, jump, exit by toggle and by Esc, edit clears. Blender's playhead stayed on its frame throughout. These tests send events straight to Cache Play; a real mouse drag over the Timeline has not been tested by hand yet.
- `test_cache_mode_reuse.py`: 6 of 6. Cache Mode reuses the cache when pressed in another viewport, in the Timeline header, from a free view (switching back to camera), and with a partial cache; it caches only when the cache is empty.
- `test_idle_cache_mode.py`: 10 of 10. Idle caching fills a partial cache while Cache Mode is open and paused; new frames become scrubbable; Blender's playhead stays put; the status line shows "Caching frame …" and "Waiting …"; by default moving the mouse counts and other programs don't, unticking Mouse Movement stops it counting, and unticking Edits stops frame changes counting. (Both idle tests replace the keyboard/mouse check with a value they control, so they pass while the computer is in use.)

v1 results (Blender 5.1 and 5.2, OpenGL and Vulkan, 0 dropped frames, ≤ 1 ms jitter) still hold.

In the everyday Blender 5.2 setup on this machine, another add-on freezes the main thread for about 35 ms several times a second, even when nothing is playing. With that, timing jitter rises to about 10 ms. Suspect: `ppf_contact_solver`, which makes network requests on the main thread. Turn add-ons off one at a time to find the culprit.

The panel's **Statistics** section shows these numbers after each playback, so you can check any scene.

## Files

- `cache_play/` – the add-on (Blender extension package)
  - `capture.py` – draws the viewport into an offscreen picture and reads it back
  - `store.py` – RAM tier, disk tier, writer and reader threads, when the cache is out of date
  - `disk.py` – the session folder and frame files
  - `display.py` – turns a stored frame back into a picture on the graphics card
  - `player.py` – playback clock and timing statistics
  - `ops.py` – the Cache & Play, Cache Mode and Clear buttons
  - `idle.py` – Cache When Idle
  - `export.py` – Export to MP4
  - `toolbar.py` – the toolbar pill at the bottom of the 3D Viewport
  - `draw.py` – the picture over the viewport, the green/blue bar and yellow-orange cache playhead in the Timeline, Dope Sheet, Graph Editor and NLA Editor
  - `ui.py` – sidebar panel, settings, preferences
  - `zeploy_usage.py` – zeploy's usage helper (see "Usage counts for zeploy")
- `dist/` – installable zip
- `tests/` – `bench_playback.py` (playback timing on the heavy scene), `profile_fill.py` (where caching time goes, GPU packing check), `test_controls.py` (keys, clicks, cancel, refill), `test_cache_mode.py` and `test_cache_mode_reuse.py` (Cache Mode), `test_idle.py` (idle caching), `test_idle_cache_mode.py` (idle caching inside Cache Mode). Each runs in a clean Blender and writes a JSON report.
- `dev_link.ps1` – links Blender's installed extension to `cache_play/` (see "While developing").
