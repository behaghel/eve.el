# Spec: Eve Segment Bottom Panel

## Problem
Eve needs a faster segment-review workflow for transcript correction, filler handling, b-roll management, and segment metadata edits. Today these actions are spread across source-buffer keybindings and prompts, so the user must visually re-find the active segment and remember which commands affect which metadata. A bottom panel managed by `context-panels` should provide a stable current-segment cockpit: the active segment is always shown in the same place, with canonical transcript words, b-roll state, cache state, and embedded key hints for rapid review/edit/advance loops.

## Context
- `~/ws/context-panels` is the generic Emacs context UI layer. It already supports side panels and advertises bottom-view provider keys, but bottom-view lifecycle semantics need to be made concrete before Eve depends on them.
- Eve already registers an optional `context-panels` provider and uses `C-c C-p` to open the context side panel.
- Eve source buffers already implement segment navigation, transcript/filler editing, b-roll editing, playback, undo/redo, saving, copy/paste, marker insertion, and rendered cache warming.
- Canonical filler classification is `word.edit.kind = "filler"`; accepting a filler removes that kind rather than adding new metadata.
- The TJM manifest should remain the single source of transcript truth. Rephrasing must not introduce a parallel display transcript layer.

## Decisions
| Decision | Choice | Rationale |
|----------|--------|-----------|
| Panel purpose | Current-segment cockpit | Keeps the editing target stable and avoids duplicating the full segment list already present in the source buffer. |
| Opening behavior | Eve `C-c C-p` opens side panel and bottom segment panel, then focuses the bottom panel | The new workflow is segment-edition centered; side panel remains contextual/timeline support. |
| Generic architecture | Implement bottom-view lifecycle in `~/ws/context-panels` first | Window/buffer lifecycle, refresh, focus, and view switching are generic responsibilities, not Eve-specific workarounds. |
| Bottom view multiplicity | One buffer per provider/view, one bottom window visible by default | Each view can own its mode/keymap/local state; multiple providers can be cycled/selected without sharing one buffer layout. |
| Generic `context-panels-open` compatibility | Existing open behavior remains conservative unless explicitly asked to open bottom views | Avoids surprising existing users with new focus-affecting bottom windows. Eve opts into side+bottom behavior. |
| View switching | `context-panels` provides next-bottom-view cycling and completing-read selection when more than two views exist | Makes multiple bottom providers manageable while keeping the common one/two-view path fast. |
| Key-hint rendering | `context-panels` provides a key-hint face/helper; Eve uses it in labels/actions | Styled key hints are reusable generic UI affordances. |
| Token rendering | Eve owns transcript token rendering and word-selection state | Word timing, filler/deleted metadata, and transcript actions are Eve-specific and should not be genericized prematurely. |
| Panel state ownership | Bottom buffer owns UI state; Eve source buffer owns document state | Keeps panel-local word index/selection independent from source mark/region while preserving canonical TJM data in the source buffer. |
| Source synchronization | Source movement drives panel when source is selected; panel commands drive segment state while panel is focused | Prevents the panel from jumping under the user during focused editing. |
| Source point movement | Panel segment navigation moves source point to the segment with minimal reveal, but focus stays in panel | Keeps source/context panels aligned without aggressive source recentering. |
| Transcript shown in panel | Repeat canonical timed words in the bottom panel | The active segment is always visually stable and near related metadata/actions. |
| Selection model | Panel-owned visual word focus/selection; no source region mutation | Avoids cross-window mark/Evil/transient-region coupling. Optional source highlighting may be added later. |
| Word navigation | `w`/`b` move next/previous word; `v` toggles/anchors selection and `w`/`b` grow/shrink it | Optimizes local panel workflow without source-mode keybinding clashes. |
| Segment navigation | `j`/`n` next segment and `k`/`p` previous segment | Mirrors Eve source-mode movement and supports rapid review loops. |
| Transcript edit scope | `e` edits current word/range; `E` edits whole segment | Encourages loss-minimizing range edits while preserving a segment-wide escape hatch. |
| Rephrase data model | Rephrase mutates canonical `words` and segment text, then reconciles timings | Avoids a second transcript/display layer and keeps source buffer semantics clear. |
| Reconciler behavior | Equal token count preserves timings/metadata; unequal count retimes replacement tokens proportionally across the original selected span; empty replacement errors | Simple, predictable, and keeps every canonical token timed. Deletion remains a separate command. |
| Filler learning | `f` marks current word/range as filler and prompts to add the exact phrase to future filler phrases if not already configured | Supports one-key current edit plus future-session learning for contiguous fillers. |
| Accepted filler | `A` removes filler kind from current word/range only | Keeps data model simple; later explicit re-tagging may mark it again. |
| Bulk filler deletion | `F` deletes fillers only in the current segment | Panel actions must not unexpectedly alter other segments. |
| Destructive word actions | After deletion/filler deletion, keep current segment and move to nearest surviving visible word | Maintains review flow and avoids disorienting segment jumps. |
| Deleted visibility | Panel follows `eve-hide-deleted-mode` | Keeps panel transcript consistent with the source view. |
| B-roll v1 | Display rich b-roll status; delegate edits to existing Eve b-roll commands | Delivers immediate visual efficiency without building panel-local b-roll forms before usage data. |
| Cache cue | Show cheap render-cache freshness/status cue; never render as part of panel refresh | Cache visibility is useful, but panel refresh must remain cheap and non-invasive. |
| Playback scope | `SPC` plays current segment source; `R` plays rendered output at/for the current segment when possible | Matches segment-review workflow and avoids overloading word selection in v1. |
| Cache action key | Optional `c` renders/warms cache for current segment; `R` remains rendered playback | Avoids conflict between cache and playback semantics. |
| Saving | Panel edits mark the source dirty; `C-x C-s` in the panel saves the source TJM buffer | Consistent with Eve editing while keeping save available from the focused panel. |
| Undo/redo | `u` and `C-r` in the panel call Eve undo/redo and refresh panel state | Rapid editing needs a local safety loop. |
| Panel exit | `q` returns focus to source; `Q` closes bottom panel | Keeps context visible for normal exits while preserving an explicit close command. |
| Marker insertion | `O` inserts a marker before the current segment | Matches existing `eve-insert-marker` semantics. |
| Merge scope | Merge only adjacent non-marker segments with the same source | Conservative merge semantics avoid timeline/source ambiguity. |
| Merge conflicts | Strict by default with clear conflict prompt and force option/prefix | Prevents silent metadata loss while allowing intentional force merges. |
| Default height | Eve bottom panel defaults to about 10 lines, customizable | Enough room for header, transcript, b-roll, and actions without consuming too much vertical space. |
| Long transcript layout | Wrap text and keep panel point/current word visible; no sticky header in v1 | Simple, readable, and sufficient for first implementation. |

## Acceptance Criteria
- [ ] AC-1: Given a provider supplies bottom views, when generic `context-panels` opens a bottom view, then it creates/reuses a per-view bottom buffer and displays it in one bottom window without disturbing the side panel.
- [ ] AC-2: Given more than one bottom view is available, when the user invokes bottom-view cycling, then the visible bottom window switches to the next view; when more than two views exist, selection by provider/view label is available.
- [ ] AC-3: Given existing callers use `context-panels-open` without bottom options, when they open context panels, then backward-compatible side-panel behavior remains unchanged.
- [ ] AC-4: Given Eve calls its context UI command (`C-c C-p`) with `context-panels` available, when invoked from an Eve source buffer, then both the side panel and the Eve segment bottom panel open/refresh and focus lands in the bottom panel.
- [ ] AC-5: Given the bottom panel is focused, when the source point changes outside panel commands, then the panel does not unexpectedly change segment; given the source window is selected and point moves to another segment, then the panel follows the source segment.
- [ ] AC-6: Given a current Eve segment, when the bottom panel renders, then it shows a compact header with segment identity/time/duration, speaker/tags summary, dirty/save cue, and cheap cache freshness/status cue.
- [ ] AC-7: Given a current Eve segment, when the bottom panel renders, then it shows canonical timed words with distinct visual states for current word, selected range, fillers, and deleted words according to `eve-hide-deleted-mode`.
- [ ] AC-8: Given the bottom panel renders labels/actions, when labels include featured keys (for example Transcript, B-roll, Notes), then the key character or action key is visually distinguished with the generic context-panels key-hint face/helper.
- [ ] AC-9: Given focus is in the bottom panel, when the user presses `w`/`b`, then panel word focus moves to the next/previous visible word within the current segment; when `v` is active, the selected range expands/shrinks visually without setting a source region.
- [ ] AC-10: Given focus is in the bottom panel, when the user presses `j`/`n` or `k`/`p`, then the panel moves to the next/previous segment, source point follows with minimal reveal, and focus remains in the bottom panel.
- [ ] AC-11: Given focus is in the bottom panel, when the user presses `e`, then Eve rephrases the current word or selected word range and reconciles canonical words/text; when the user presses `E`, then Eve rephrases the whole segment.
- [ ] AC-12: Given rephrase replacement has the same token count as the target range, when it is applied, then per-word timings and edit metadata are preserved while tokens/text update.
- [ ] AC-13: Given rephrase replacement has a different non-zero token count, when it is applied, then the replacement tokens are retimed proportionally inside the original target time span, replaced range metadata is not silently misapplied, and segment text updates.
- [ ] AC-14: Given rephrase replacement is empty, when submitted, then Eve reports an error and does not mutate the manifest.
- [ ] AC-15: Given focus is in the bottom panel, when the user presses `d`, then only the current word/range is marked deleted; after refresh the nearest surviving visible word is focused.
- [ ] AC-16: Given focus is in the bottom panel, when the user presses `f`, then the current word/range is marked filler and Eve prompts to add the contiguous phrase to future filler phrases if it is not already configured.
- [ ] AC-17: Given focus is in the bottom panel, when the user presses `A`, then filler kind is removed from the current word/range only; when the user presses `F`, then only fillers in the current segment are marked deleted.
- [ ] AC-18: Given focus is in the bottom panel, when the user presses `s`, then Eve splits the current segment at the focused word using existing split semantics.
- [ ] AC-19: Given focus is in the bottom panel, when the user presses `B`, `C`, or `P`, then Eve invokes b-roll edit, toggle-continue, or preview behavior for the current segment and refreshes the displayed b-roll status.
- [ ] AC-20: Given focus is in the bottom panel, when the user presses metadata/action keys (`N`, `T`, `I`, `G`, `O`, `SPC`, `R`, optional `c`), then each action applies to the current segment and refreshes panel state without changing focus except where explicitly intended.
- [ ] AC-21: Given adjacent non-marker same-source segments, when the user merges next/previous from the panel, then Eve merges them strictly; given metadata conflicts, then Eve reports the conflicts and offers an explicit force path/prefix rather than silently losing data.
- [ ] AC-22: Given focus is in the bottom panel, when the user presses `u`, `C-r`, or `C-x C-s`, then Eve undo, redo, or source-buffer save runs and the panel refreshes with valid segment/word state.
- [ ] AC-23: Given focus is in the bottom panel, when the user presses `q`, then focus returns to the source buffer while panels remain visible; when the user presses `Q`, then the bottom panel closes.
- [ ] AC-24: Given `context-panels` is unavailable, when Eve starts, then existing Eve source-mode behavior remains available and `C-c C-p` reports a clear unavailable/context-panel error rather than breaking Eve load.

## Invariants
- TJM remains the single transcript source of truth; no persistent parallel `display_text`/override layer is introduced for rephrasing in this feature.
- Panel refresh must never compile, render, transcribe, or start cache warming implicitly.
- `context-panels` must remain mode-agnostic and must not include Eve-specific concepts.
- Eve must not hardcode user-local paths such as `~/ws/context-panels`.
- Existing side panel behavior, context active highlighting, ruler disabling, and b-roll cue rendering must keep working.
- Existing Eve source-mode keybindings must remain stable except for explicitly agreed context-panel opening behavior.
- Panel-owned word selection must not mutate source mark/region, Evil visual state, or mark-ring.
- Filler acceptance remains represented by removing `word.edit.kind`, not by adding new TJM metadata.
- Bulk actions from the segment panel must not affect segments other than the current segment unless the action is explicitly relational (merge with adjacent segment, insert marker before current segment).
- Merge must not cross markers, source boundaries, or non-adjacent segments.

## Scope
**May modify:**
- `~/ws/context-panels/context-panels.el`
- `~/ws/context-panels/README.md`
- `~/ws/context-panels/test/context-panels-test.el`
- `eve.el`
- `test/eve-test.el`
- `docs/eve.org`
- generated Eve docs (`docs/eve.info`, `docs/eve.texi`, `docs/generated/`) when docs generation changes them
- `docs/internal/segment-bottom-panel-spec.md`

**Must not modify without separate approval:**
- `docs/tjm-spec.md`, unless implementation discovers a real TJM semantic ambiguity requiring spec clarification
- CLI render/transcribe behavior, except if needed for a separately approved cache-status helper
- personal Emacs config under `~/.emacs.d`
- generated Claude/OpenCode/Pi agent config
- `devenv.nix` unless a missing test/tool dependency is discovered and searched/validated through devenv rules

## Verification Plan
| Criterion | Method | Automated? |
|-----------|--------|------------|
| AC-1 | `context-panels` ERT opens a provider bottom view; asserts per-view buffer and bottom window while side panel remains valid | Yes |
| AC-2 | `context-panels` ERT registers 2–3 views; asserts next-view cycling and completing-read selection path | Yes |
| AC-3 | Existing/new `context-panels` tests assert default open remains side-compatible unless bottom requested | Yes |
| AC-4 | Eve ERT with `context-panels` on load-path calls `eve-context-panels-open`; asserts side and bottom buffers/windows exist and bottom is selected | Yes |
| AC-5 | Eve ERT simulates source-selected movement vs bottom-focused movement; asserts panel segment-follow rules | Yes |
| AC-6 | Eve ERT renders bottom panel and checks header includes time/duration/speaker/tags/dirty/cache strings | Yes |
| AC-7 | Eve ERT renders filler/deleted/current/selected words and checks text properties/faces and hide-deleted behavior | Yes |
| AC-8 | `context-panels` unit test checks key-hint propertization; Eve ERT checks panel labels include key-hint face | Yes |
| AC-9 | Eve ERT drives `w`/`b`/`v`; asserts panel-local word index and selection range without source mark activation | Yes |
| AC-10 | Eve ERT drives `j`/`k`; asserts panel segment id and source point update while selected window remains bottom | Yes |
| AC-11 | Eve ERT drives `e` and `E` through stubbed prompts; asserts canonical words/text update | Yes |
| AC-12 | Unit/ERT reconciler test for equal token count preserving timings/edit metadata | Yes |
| AC-13 | Unit/ERT reconciler test for unequal token count proportional retiming and safe metadata handling | Yes |
| AC-14 | Unit/ERT reconciler test for empty replacement error/no mutation | Yes |
| AC-15 | Eve ERT deletes current word/range from panel and asserts nearest surviving focus | Yes |
| AC-16 | Eve ERT tags word/range as filler and stubs persistence prompt/customize-save-variable | Yes |
| AC-17 | Eve ERT accepts filler and current-segment-only deletes fillers; asserts other segments unchanged | Yes |
| AC-18 | Eve ERT splits at panel focused word and asserts existing split invariants | Yes |
| AC-19 | Eve ERT stubs b-roll commands and asserts panel keys delegate with current segment and refresh | Yes |
| AC-20 | Eve ERT stubs metadata/play/cache commands and asserts current-segment scoping/focus behavior | Yes |
| AC-21 | Eve ERT covers strict merge success, source/marker conflict errors, metadata conflict prompt, and force path | Yes |
| AC-22 | Eve ERT stubs undo/redo/save and asserts panel refresh/revalidation | Yes |
| AC-23 | Eve ERT checks `q` focus-source and `Q` bottom close | Yes |
| AC-24 | Batch-load Eve without `context-panels`; assert no load failure and unavailable command path is clear | Yes |
| All docs | Run Eve docs generation/doc drift and `context-panels` README checks if available | Yes |
| Full validation | Run relevant `context-panels` tests plus Eve `test-elisp`, `docs`, and `lint` through `devenv -q shell -- ...` | Yes |

## Implementation Slices
1. **Generic context-panels bottom plumbing**
   - Bottom view lifecycle: open/reuse/close/focus/refresh.
   - Per-view buffers and one visible bottom window by default.
   - Bottom-view next/select commands.
   - Key-hint face/helper.
   - README and `context-panels` tests.

2. **Eve bottom panel display and navigation**
   - `C-c C-p` opens side + bottom and focuses bottom.
   - Eve segment panel mode/keymap and panel-local state.
   - Header, canonical transcript, b-roll status, other action rows.
   - `j/k/n/p`, `w/b`, `v`, `q/Q`, wrapping, hide-deleted consistency.
   - Source point follow/minimal reveal.

3. **Eve panel actions**
   - Transcript reconciler and `e`/`E` actions.
   - Word/range actions `d/f/A/F/s`.
   - B-roll delegates `B/C/P`.
   - Metadata/play/cache/save/undo/redo actions.
   - Strict merge previous/next with conflict prompt/force path.

## References
- `~/ws/context-panels/README.md`
- `~/ws/context-panels/context-panels.el`
- `~/ws/context-panels/test/context-panels-test.el`
- `docs/internal/context-panels-migration-spec.md`
- `eve.el`: context provider registration/opening, segment navigation, filler actions, b-roll commands, playback commands, render cache commands, undo/redo/save behavior
- `test/eve-test.el`: current Eve mode/keymap, filler, context panel, b-roll, playback, and render-cache tests
- `docs/eve.org`: user-facing Eve workflow/keybinding documentation
- `docs/tjm-spec.md`: authoritative TJM edit metadata semantics
