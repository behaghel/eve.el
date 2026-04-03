# Learnings: timestamp-ruler

## 2026-04-03 Initial codebase analysis

### Key facts for duration computation
- `eve-preserve-gaps-max` defcustom at line 69-72, default 2.0s
- `eve--edit-deleted-p item` (line 1209-1211): checks `deleted` in nested `edit` alist
- `eve--marker-p segment` (line 1306): marker segments have no timing data
- `eve-hide-deleted-mode` is ON by default (line 1103)
- Word timing: `(alist-get 'start word)` and `(alist-get 'end word)` — plain floats in seconds
- Sample data: seg-1 words alpha(1.0-1.5) beta(1.5-2.0) gamma(2.0-3.0) delta(3.0-4.0)

### Key facts for rendering
- `eve--render` at line 1276-1396
- Loop body ends at line 1391
- After loop: `goto-char`, `eve--goto-segment`, `eve--update-focus-overlay`, `eve--apply-visual-wrap` (lines 1392-1396)
- Add ruler update AFTER line 1395 (after `eve--update-focus-overlay`)
- `eve--segment-bounds id` → (start . end) buffer positions (line 2550)

### Key facts for mode
- `eve-mode` definition: lines 1084-1105
- `eve-hide-deleted-mode` pattern (lines 1075-1081): define-minor-mode, re-renders on toggle
- Post-command hook: `eve--post-command` (line 1100) → `eve--update-focus-overlay` + echo info
- Window change hook: already used at line 1101 for visual wrap

### Key facts for overlays
- Focus overlay: `eve--focus-overlay` buffer-local
- Focus overlay priority: -50 (line 2566)
- Ruler overlay priority must be -100 (below focus)
- Overlay pattern: make-overlay → overlay-put face/before-string/priority/evaporate

### Key facts for mode line
- No custom mode-line-format in eve-mode currently
- Pattern: `(setq-local mode-line-format ...)` in mode setup
- Use `(:eval (fn))` for dynamic content; cached var for perf
- `force-mode-line-update` to refresh

### Test infrastructure
- `eve-test--sample-data`: seg-1 with 4 words, all timing, no deletion
- `eve-test-with-buffer` macro: creates temp buffer, sets eve--data, calls eve--render
- Tests added before `(provide 'eve-test)` at end of file

### defcustom placement
- New `eve-ruler-interval` goes after line 134 (after `eve-filler-regex`)

### defface placement
- New `eve-ruler-face` goes after line 177 (after `eve-current-segment-face`)

## 2026-04-03 Rendered duration engine implementation

- Added pure helpers before `eve--render`: `eve--rendered-segment-duration`, `eve--rendered-total-duration`, and `eve--rendered-cumulative-times`.
- `eve--rendered-segment-duration` treats markers and hidden deleted segments as zero duration, filters deleted words only when requested, and trims leading/trailing gaps beyond `eve-preserve-gaps-max`.
- Gap trimming is subtractive and clamped at zero, so extreme preserved-gap trimming cannot return negative durations.
- Duration tests can stay pure (no `eve-test-with-buffer`) using alist segment fixtures; mutate nested word edits via `(setf (alist-get 'edit (nth idx words)) ...)` when applying changes to all words.

## 2026-04-03 Ruler milestone helpers

- Added `eve--format-ruler-time` for `[hh:mm:ss]` ruler labels and `eve--ruler-milestones` for mapping cumulative end-times to segment ids.
- Milestones always include `t=0` and clamp non-positive intervals to `1.0` second.
- Mapping uses segment ranges `(prev-end, end]`, with the initial `t=0` label pinned to the first segment and overflow clamped to the last segment.
