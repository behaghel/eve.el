# Spec: Eve Context Panels Migration

## Problem
Eve's editing workflow needs a richer contextual UI for rendered timeline landmarks, b-roll status, and current-segment transcript correction. The existing right-margin timestamp ruler is space-constrained and cannot support the planned current-item inspector/actions. Eve should adopt `context-panels` as the path toward a mandatory core UX, while beginning with an optional/local development integration that does not break existing users.

## Context
- `~/ws/context-panels` provides generic Emacs side-panel, bottom-view, overlay, marker, and provider primitives.
- Eve currently has `eve-ruler-mode`, right-margin timestamp overlays, b-roll summary rendering, playback tracking, segment/marker navigation, and rich edit commands in `eve.el`.
- Current relevant keybindings: `C-c C-p` is `eve-play-rendered`, `C-c C-r` is `eve-reload`, and `g` is also `eve-reload`.
- The desired migration path is optional-first, then mandatory/core once the UX stabilizes.

## Decisions
| Decision | Choice | Rationale |
|----------|--------|-----------|
| Dependency strategy | Local dev dependency via `~/ws/context-panels`; no hardcoded path in `eve.el` | Supports immediate interactive/dev use without baking user-local paths into package code. |
| Rollout | Optional-first, destination mandatory | Existing Eve must keep working if `context-panels` is unavailable, but new UX should be designed as future core UI. |
| First implementation slice | Side panel only | Lower-risk migration from timestamp ruler; proves provider registration, viewport anchoring, jumping, glyph rendering, and keybinding migration. |
| Side panel content | Hybrid timestamp markers plus b-roll flags | Preserves fixed rendered-time rhythm while surfacing editorial landmarks between intervals. |
| Side panel positioning | Anchor rows to visible source text rows when possible | Vertical alignment is a core `context-panels` responsibility and must work reliably for Eve. |
| B-roll primary glyph | `🎞` | Compact and recognizable as attached media/video. |
| B-roll config glyphs | `▣` replace, `⊕` overlay, `◱` pip, `🎙` source audio, `♪` b-roll audio, `🔇` mute, `→` continue, `▧` still, `{…}` placeholders, `▭` overlays | Keeps b-roll state scannable in limited side-panel width. |
| Keybinding migration | Move rendered playback from `C-c C-p` to `C-c C-r`; keep `g` for reload; use `C-c C-p` for context panels | Frees mnemonic “panels” binding while preserving ergonomic reload through `g`. |
| Bottom panel destination | Current item inspector + action guide | Needed later for transcript correction efficiency and b-roll/current-segment visibility. |
| Bottom panel rollout | Second slice, not initial side-panel slice | Avoids coupling two UX migrations in one change. |

## Acceptance Criteria
- [ ] AC-1: Given `context-panels` is unavailable, when `eve-mode` starts, then Eve loads and existing editing/playback/ruler commands continue to work.
- [ ] AC-2: Given `context-panels` is available on `load-path`, when `eve-mode` starts, then Eve registers an Eve context-panel provider for the buffer.
- [ ] AC-3: Given an Eve buffer with context support available, when the user presses `C-c C-p`, then the Eve context side panel opens or refreshes for the current buffer.
- [ ] AC-4: Given rendered timeline duration and `eve-ruler-interval`, when the side panel is rendered, then it includes the same fixed-interval timestamp landmarks as the current ruler logic.
- [ ] AC-5: Given a visible segment or marker with b-roll metadata, when the side panel is rendered, then it includes a compact b-roll flag using `🎞`, file basename, duration, and applicable config glyphs.
- [ ] AC-6: Given a side-panel row, when the user activates/jumps from it, then point moves to the corresponding segment or marker in the Eve source buffer.
- [ ] AC-7: Given source rows are visible, when the side panel is rendered, then side-panel rows are anchored to visible source text rows via `context-panels` viewport row alignment.
- [ ] AC-8: Given keybindings are loaded, when inspected, then `C-c C-r` is bound to `eve-play-rendered`, `C-c C-p` is bound to the Eve context-panel command, and `g` remains bound to `eve-reload`.
- [ ] AC-9: Given the legacy ruler is enabled, when the Eve context panel opens, then `eve-ruler-mode` is disabled and its right-margin overlays are cleared.
- [ ] AC-10: Given generated documentation is rebuilt, when users inspect Eve docs/help, then keybindings and context-panel behavior are documented without contradicting the TJM spec.

## Invariants
- TJM manifest semantics must not change.
- B-roll metadata remains stored under `segment.edit.broll` and is preserved on render/edit round trips.
- Existing `eve-ruler-mode` behavior remains available when the context side panel is not open.
- No user-local absolute path such as `~/ws/context-panels` may be hardcoded into `eve.el`.
- Existing playback keybindings other than the agreed `C-c C-p` → `C-c C-r` migration must remain stable.
- Existing tests for marker b-roll, playback tracking, copy/paste, and docs drift must continue to pass.

## Scope
**May modify:**
- `eve.el`
- `test/eve-test.el`
- `docs/eve.org`
- generated docs under `docs/eve.info`, `docs/eve.texi`, `docs/generated/`
- dev/test command configuration if needed to add `~/ws/context-panels` to load path
- user Emacs config only if explicitly requested separately

**Must not modify:**
- `docs/tjm-spec.md` unless an actual TJM semantic ambiguity is discovered
- CLI rendering/transcription behavior
- `~/ws/context-panels` package internals during the Eve integration slice, unless a blocker is discovered and approved
- generated Claude/OpenCode/Pi agent config

## Verification Plan
| Criterion | Method | Automated? |
|-----------|--------|------------|
| AC-1 | Batch-load Eve without `context-panels` on load-path; assert no error and old commands exist | Yes |
| AC-2 | Batch-load Eve with `-L ~/ws/context-panels`; assert provider registration after `eve-mode` | Yes |
| AC-3 | ERT test stubs/loads `context-panels` and calls `eve-context-panels-open`; assert side-panel buffer exists | Yes |
| AC-4 | Unit test provider item collection against known cumulative rendered times | Yes |
| AC-5 | Unit test b-roll item formatting/glyph output for replace/overlay/pip/audio/duration/file basename | Yes |
| AC-6 | ERT jump test from collected/rendered item to segment id | Yes |
| AC-7 | `context-panels` ERT test proves side-panel rows are padded to source viewport rows | Yes |
| AC-8 | Existing keybinding test updated for `C-c C-r`, `C-c C-p`, and `g` | Yes |
| AC-9 | Eve ERT test proves opening context panel disables `eve-ruler-mode` and clears ruler overlays | Yes |
| AC-10 | Run docs generation, doc drift check, `test-elisp`, and `lint` | Yes |

## References
- `~/ws/context-panels/README.md`
- `~/ws/context-panels/context-panels.el`
- `eve.el`: `eve-ruler-mode`, rendered cumulative timeline helpers, b-roll summary helpers, keymap definitions
- `docs/eve.org`: user-facing keybindings and workflow documentation
- `docs/tjm-spec.md`: authoritative TJM schema and b-roll/edit metadata semantics
