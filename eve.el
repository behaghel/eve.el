;;; eve.el --- Text-driven video editing for TJM manifests -*- lexical-binding: t; -*-

;; Copyright (C) 2026 Hubert J. Behaghel <behaghel@gmail.com>
;; Author: Hubert J. Behaghel <behaghel@gmail.com>
;; Maintainer: Hubert J. Behaghel <behaghel@gmail.com>
;; URL: https://github.com/behaghel/eve.el
;; Version: 0.1.0
;; Package-Requires: ((emacs "29.1"))
;; Keywords: multimedia, tools, video
;; SPDX-License-Identifier: GPL-3.0-or-later

;; eve is free software: you can redistribute it and/or modify
;; it under the terms of the GNU General Public License as published
;; by the Free Software Foundation, either version 3 of the License,
;; or (at your option) any later version.
;;
;; eve is distributed in the hope that it will be useful,
;; but WITHOUT ANY WARRANTY; without even the implied warranty of
;; MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
;; GNU General Public License for more details.
;;
;; You should have received a copy of the GNU General Public License
;; along with eve.  If not, see <https://www.gnu.org/licenses/>.

;;; Commentary:
;; Major mode for manipulating Textual Join Manifest (TJM) files that describe
;; text-driven video edits. Segments are presented as readable text while the
;; underlying JSON manifest is kept in sync. Inspired by subed / transcript
;; editing workflows.

;;; Code:

(require 'json)
(require 'cl-lib)
(require 'seq)
(require 'subr-x)
(require 'ansi-color)
(require 'hydra nil t)
(require 'image)
(require 'outline)

(defvar eve--context-panels-available (require 'context-panels nil t)
  "Non-nil when optional context-panels support is available.")

(declare-function image-file-name-p "image-mode" (filename))
(defvar context-panels-side-panel-buffer)
(defvar context-panels-bottom-panel-buffer)
(defvar context-panels-source-buffer)

(declare-function context-panels-mode "context-panels" (&optional arg))
(declare-function context-panels-open "context-panels" (&optional source-buffer))
(declare-function context-panels-close "context-panels" ())
(declare-function context-panels-open-bottom-view "context-panels" (view-id &optional source-buffer select))
(declare-function context-panels-refresh "context-panels" ())
(declare-function context-panels-refresh-bottom-view "context-panels" ())
(declare-function context-panels-close-bottom-view "context-panels" ())
(declare-function context-panels-register-provider "context-panels" (provider))
(declare-function context-panels-propertize-key "context-panels" (label key &optional face))
(declare-function dired-get-marked-files "dired"
                  (&optional localp arg filter distinguish-one-marked
                             error-if-none-p))

(defgroup eve nil
  "Editing spoken-word video manifests in Emacs."
  :group 'multimedia)

(defcustom eve-play-program "mpv"
  "Executable used for segment playback."
  :type 'string
  :group 'eve)

(defcustom eve-cli-program "eve"
  "Executable used for `eve` CLI commands."
  :type 'string
  :group 'eve)

(defcustom eve-preserve-gaps-max 2.0
  "Maximum gap duration preserved during `eve text-edit` compilation."
  :type 'float
  :group 'eve)

(defcustom eve-play-args '("--quiet" "--no-terminal" "--really-quiet")
  "Additional arguments passed to `eve-play-program'."
  :type '(repeat string)
  :group 'eve)

(defcustom eve-play-after-compile nil
  "Whether to play the rendered video automatically after compilation.
When non-nil, `eve-compile' (\\`C-c C-c') plays the output file on
success.  When nil (the default), compilation only produces the file."
  :type 'boolean
  :group 'eve)

(defcustom eve-video-layout nil
  "When non-nil, arrange the screen for video editing on the first play command.
The Emacs frame is resized to the bottom portion of the display and mpv is
positioned directly above it — borderless, on top, aspect-ratio-preserving.
The original frame geometry is restored when playback stops.
Skipped when Emacs is in macOS native fullscreen."
  :type 'boolean
  :group 'eve)

(defcustom eve-video-layout-ratio 0.6
  "Fraction of screen height reserved for the video window (0.0–1.0).
The remaining fraction is given to the Emacs frame.  Default 0.6 means
60% video on top, 40% Emacs on the bottom."
  :type 'float
  :group 'eve)

(defcustom eve-validation-on-save t
  "Whether TJM buffers should be validated automatically on save."
  :type 'boolean
  :group 'eve)

(defcustom eve-validation-time-tolerance 0.1
  "Permitted slack (in seconds) when comparing segment and word timings.
This accounts for rounding in source manifests so that we only flag
meaningful timing inconsistencies."
  :type 'float
  :group 'eve)

(defcustom eve-validation-suspicious-segment-duration 10.0
  "Segment duration in seconds above which validation flags a warning.
Set to nil to disable long-segment warnings.  Long transcript segments are often
ASR failures because normal Eve transcript segments are expected to stay short."
  :type '(choice (const :tag "Disabled" nil) float)
  :group 'eve)

(defcustom eve-transcribe-offer-reprocess-suspicious t
  "Whether to offer retranscription of suspiciously long/gapped regions after transcription."
  :type 'boolean
  :group 'eve)

(defcustom eve-transcribe-suspicious-gap-duration 5.0
  "Source gap duration in seconds above which transcription cleanup is offered.
Set to nil to disable suspicious-gap reprocessing."
  :type '(choice (const :tag "Disabled" nil) float)
  :group 'eve)

(defcustom eve-show-words-by-default nil
  "If non-nil, show per-word timings when rendering segments."
  :type 'boolean
  :group 'eve)

(defcustom eve-media-extensions '("mp4" "mov" "wav" "mp3" "mkv" "m4a")
  "Supported media extensions for transcription inputs."
  :type '(repeat string)
  :group 'eve)

(defcustom eve-transcribe-backend "faster-whisper"
  "Backend passed to `eve transcribe'."
  :type 'string
  :group 'eve)

(defcustom eve-transcribe-model "medium.en"
  "Model passed to `eve transcribe'."
  :type 'string
  :group 'eve)

(defcustom eve-transcribe-verbatim t
  "Whether `eve transcribe' should preserve verbatim output."
  :type 'boolean
  :group 'eve)

(defcustom eve-transcribe-hotwords nil
  "Optional hotwords string passed to `eve transcribe'.
When nil, the CLI supplies backend-specific defaults for verbatim mode."
  :type '(choice (const :tag "Use CLI default" nil) string)
  :group 'eve)

(defcustom eve-transcribe-tag-fillers t
  "Whether `eve transcribe' should tag filler words."
  :type 'boolean
  :group 'eve)

(defcustom eve-seek-short-seconds 5
  "Seconds used by short seek commands."
  :type 'integer
  :group 'eve)

(defcustom eve-seek-long-backward-seconds 30
  "Seconds used by long backward seek commands."
  :type 'integer
  :group 'eve)

(defcustom eve-seek-long-forward-seconds 10
  "Seconds used by long forward seek commands."
  :type 'integer
  :group 'eve)

(defcustom eve-filler-phrases '("um" "uh")
  "Filler words/phrases to tag in transcripts.
Each entry is a plain string; single words (\"um\") and multi-word
phrases (\"you know\") are both supported. Case, punctuation, and
repeated whitespace are ignored during matching."
  :type '(repeat string)
  :group 'eve)

(defcustom eve-filler-regex '("\\`um\\'" "\\`uh\\'")
  "Regexps matched against word text when tagging filler words.
Each regexp is checked against both `spoken' and legacy `token' values when
present."
  :type '(repeat regexp)
  :group 'eve)

(defcustom eve-filler-exception-phrases nil
  "Phrases that should not be auto-tagged as fillers.
Each entry is a plain string.  Exceptions win over `eve-filler-phrases', so an
exception such as 'would like' prevents tagging 'like' inside that phrase."
  :type '(repeat string)
  :group 'eve)

(defcustom eve-ruler-interval 30.0
  "Interval in seconds between right-margin timestamp ruler markers."
  :type 'float
  :group 'eve)

(defcustom eve-line-spacing 2
  "Additional pixel spacing between lines in `eve-mode' buffers."
  :type 'natnum
  :group 'eve)

(defcustom eve-context-panels-auto-open nil
  "When non-nil, open Eve context panels automatically in `eve-mode'.
Context panels require the optional `context-panels' package."
  :type 'boolean
  :group 'eve)

(defcustom eve-compile-quality "draft"
  "Default encoding quality passed to `eve text-edit --quality'.
\\='draft\\=' (ultrafast/crf28) renders ~15x faster for quick preview.
\\='final\\=' (medium/crf18) produces delivery-quality output.
Use \\[universal-argument] \\[eve-compile] to compile at the opposite quality."
  :type '(choice (const "draft") (const "final"))
  :group 'eve)

(defcustom eve-compile-jobs 0
  "Number of segments to encode in parallel, passed to `eve text-edit --jobs'.
0 (the default) lets the CLI auto-detect based on CPU count, capped at 8."
  :type 'integer
  :group 'eve)

(defcustom eve-compile-scale 1.0
  "Resolution scale factor passed to `eve text-edit --scale'.
Values less than 1.0 reduce resolution during draft renders (e.g. 0.5 = half).
Only effective with `eve-compile-quality' set to \\='draft\\='.
Ignored for final renders."
  :type 'float
  :group 'eve)

(defcustom eve-compile-confirm-threshold 30
  "Seconds above which `eve-compile' asks before starting.
Set to 0 to always prompt, or a large number to never prompt."
  :type 'integer
  :group 'eve)

(defcustom eve-compile-cache t
  "Whether to use the persistent segment cache during compilation.
When non-nil (default), unchanged segments are reused from .eve-cache/ next to
the manifest, making re-renders after small edits nearly instant.
When nil, `--no-cache' is passed and every segment is re-encoded from scratch."
  :type 'boolean
  :group 'eve)

(defface eve-heading-face
  '((t :inherit font-lock-keyword-face :weight bold))
  "Face for segment headings."
  :group 'eve)

(defface eve-text-face
  '((t :inherit default))
  "Face for segment prose."
  :group 'eve)

(defface eve-filler-face
  '((t :inherit font-lock-warning-face :slant italic))
  "Face used for filler words."
  :group 'eve)

(defface eve-deleted-face
  '((t :inherit shadow :strike-through t))
  "Face used for deleted content when shown."
  :group 'eve)

(defface eve-segment-panel-current-word-face
  '((t :weight bold :underline t :box t))
  "Face used for the focused word in the Eve segment panel.
This intentionally avoids warning-like colors so focus is not confused with
filler syntax highlighting."
  :group 'eve)

(defface eve-segment-panel-selection-face
  '((t :inherit region))
  "Face used for the selected word range in the Eve segment panel."
  :group 'eve)

(defface eve-segment-panel-label-face
  '((t :inherit shadow))
  "Muted face used for labels in the Eve segment panel."
  :group 'eve)

(defface eve-segment-panel-value-face
  '((t :inherit font-lock-constant-face))
  "Face used for values in the Eve segment panel."
  :group 'eve)

(defface eve-segment-panel-broll-value-face
  '((t :inherit font-lock-string-face))
  "Face used for b-roll values in the Eve segment panel."
  :group 'eve)

(defface eve-segment-panel-action-face
  '((t :inherit font-lock-doc-face))
  "Face used for action indicators in the Eve segment panel."
  :group 'eve)

(defface eve-broll-face
  '((t :inherit font-lock-warning-face :weight bold))
  "Face to highlight segments with b-roll metadata."
  :group 'eve)

(defface eve-meta-face
  '((t :inherit font-lock-comment-face))
  "Face used for metadata lines (tags, notes, etc.)."
  :group 'eve)

(defface eve-marker-face
  '((t :inherit outline-1 :weight bold))
  "Face used for marker segments that act as headings."
  :group 'eve)

(defface eve-current-segment-face
  '((((background dark)) :background "#1a2e1a" :extend t)
    (t                   :background "#e0edcf" :extend t))
  "Face used to highlight the segment under point.
Uses a green tint so it remains visually distinct from the region face,
which is typically blue or grey in most themes."
  :group 'eve)

(defface eve-ruler-face
  '((t :inherit font-lock-comment-face :slant italic))
  "Face used for right-margin timestamp ruler markers."
  :group 'eve)

(defface eve-playback-face
  '((((background dark)) :background "#5c3a00" :foreground "#ffdd88" :extend t :weight bold)
    (t                   :background "#fff3c4" :foreground "#6b4400" :extend t :weight bold))
  "Face used to highlight the segment currently playing in mpv."
  :group 'eve)

(defvar eve--source-directory
  (file-name-directory (or load-file-name (locate-library "eve") ""))
  "Directory from which `eve' was loaded.")

(defun eve--info-directory ()
  "Return Eve's Info documentation directory, when available."
  (let ((docs-dir (expand-file-name "docs" eve--source-directory)))
    (when (file-readable-p (expand-file-name "eve.info" docs-dir))
      docs-dir)))

(defun eve--register-info-directory ()
  "Add Eve's bundled manual to Emacs Info search paths."
  (when-let* ((docs-dir (eve--info-directory)))
    ;; `Info-directory-list' is used to build the top-level Info menu from dir
    ;; files, while `Info-additional-directory-list' supports direct lookup.
    (when (boundp 'Info-directory-list)
      (add-to-list 'Info-directory-list docs-dir))
    (when (boundp 'Info-additional-directory-list)
      (add-to-list 'Info-additional-directory-list docs-dir))
    ;; If Info has already built its directory menu, force it to notice Eve.
    (when (boundp 'Info-dir-contents)
      (setq Info-dir-contents nil))))

(with-eval-after-load 'info
  (eve--register-info-directory))

;;;###autoload
(defun eve-info ()
  "Open the Eve manual in Info."
  (interactive)
  (require 'info)
  (eve--register-info-directory)
  (info "(eve)"))

(defun eve--resolve-cli ()
  "Find the eve CLI executable.
Try `executable-find' on `eve-cli-program' first; fall back to
scripts/run-cli.sh relative to the package source directory."
  (or (executable-find eve-cli-program)
      (when (string= eve-cli-program "eve")
        (let ((run-cli (expand-file-name "scripts/run-cli.sh"
                                         eve--source-directory)))
          (when (file-executable-p run-cli)
            run-cli)))))

(defvar eve-playback-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "SPC") #'eve-playback-pause-resume)
    (define-key map (kbd "<left>") #'eve-seek-short-backward)
    (define-key map (kbd "<right>") #'eve-seek-short-forward)
    (define-key map (kbd "S-<left>") #'eve-seek-long-backward)
    (define-key map (kbd "S-<right>") #'eve-seek-long-forward)
    (define-key map "q" #'eve-stop-playback)
    map)
  "Keymap active during mpv playback.
Overrides SPC to pause/resume instead of playing a single segment.")

(define-minor-mode eve-playback-mode
  "Transient minor mode active while mpv playback is tracking.
Provides a higher-priority SPC binding for pause/resume that overrides
the major-mode binding in `eve-mode-map' and evil state maps."
  :keymap eve-playback-mode-map
  (if eve-playback-mode
      ;; Push into emulation-mode-map-alists so we override evil state maps
      (push (list (cons 'eve-playback-mode eve-playback-mode-map))
            emulation-mode-map-alists)
    (setq emulation-mode-map-alists
          (cl-remove-if (lambda (entry)
                          (and (listp entry)
                               (assq 'eve-playback-mode entry)))
                        emulation-mode-map-alists))))

(defvar eve-mode-map
  (let ((map (make-sparse-keymap)))
    (set-keymap-parent map special-mode-map)
    ;; Navigation
    (define-key map "j" #'eve-next-segment)
    (define-key map "k" #'eve-previous-segment)
    (define-key map "n" #'eve-next-segment)
    (define-key map "p" #'eve-paste-segment)
    (define-key map "P" #'eve-paste-segment-before)
    (define-key map "J" #'eve-move-segment-down)
    (define-key map "K" #'eve-move-segment-up)
    (define-key map "{" #'eve-previous-marker)
    (define-key map "}" #'eve-next-marker)
    (define-key map (kbd "TAB") #'eve-toggle-marker-fold)
    (define-key map (kbd "<backtab>") #'eve-toggle-all-marker-folds)
    (define-key map (kbd "S-TAB") #'eve-toggle-all-marker-folds)
    ;; Editing
    (define-key map "d" #'eve-delete-word)
    (define-key map "D" #'eve-delete-segment)
    (define-key map "s" #'eve-split-segment)
    (define-key map "|" #'eve-split-segment)
    (define-key map "m" #'eve-merge-with-next)
    (define-key map ")" #'eve-merge-with-next)
    (define-key map "u" #'eve-undo)
    (define-key map (kbd "C-r") #'eve-redo)
    (define-key map "y" #'eve-copy-segment)
    (define-key map (kbd "C-y") #'eve-paste-segment)
    (define-key map "e" #'eve-dwim-edit)
    (define-key map "o" #'eve-insert-marker)
    ;; Metadata
    (define-key map "t" #'eve-toggle-tag)
    (define-key map "b" #'eve-edit-broll)
    (define-key map "B" #'eve-toggle-broll-continue)
    (define-key map "i" #'eve-edit-speaker)
    (define-key map "r" #'eve-edit-start-end)
    ;; Fillers
    (define-key map "f" #'eve-dwim-filler)
    (define-key map "F" #'eve-delete-fillers)
    (define-key map "A" #'eve-accept-filler)
    (define-key map (kbd "] f") #'eve-next-filler)
    (define-key map (kbd "[ f") #'eve-previous-filler)
    ;; Playback
    (define-key map (kbd "SPC") #'eve-play-segment)
    (define-key map (kbd "RET") #'eve-play-segment)
    (define-key map (kbd "<left>") #'eve-seek-short-backward)
    (define-key map (kbd "<right>") #'eve-seek-short-forward)
    (define-key map (kbd "S-<left>") #'eve-seek-long-backward)
    (define-key map (kbd "S-<right>") #'eve-seek-long-forward)
    ;; Structure
    (define-key map "_" #'eve-toggle-separator)
    (define-key map (kbd "C-RET") #'eve-toggle-separator)
    ;; Display / buffer
    (define-key map "g" #'eve-reload)
    (define-key map "q" #'eve-dwim-quit)
    (define-key map "?" #'eve--show-help)
    ;; Global C-c C-x (convention-compliant)
    (define-key map (kbd "C-c C-c") #'eve-compile)
    (define-key map (kbd "C-c C-v") #'eve-validate)
    (define-key map (kbd "C-c C-r") #'eve-play-rendered)
    (define-key map (kbd "C-c C-p") #'eve-context-panels-open)
    (define-key map (kbd "C-c C-o") #'eve-open-raw-json)
    (define-key map (kbd "C-c C-h") #'eve-hide-deleted-mode)
    (define-key map (kbd "C-c C-w") #'eve-toggle-words)
    (define-key map (kbd "C-c C-l") #'eve-ruler-mode)
    (define-key map (kbd "C-c C-t") #'eve-transcribe)
    (define-key map (kbd "C-c C-s") #'eve-play-source)
    (define-key map (kbd "C-c SPC") #'eve-play)
    (define-key map (kbd "C-c C-b") #'eve-edit-broll-placeholders)
    (define-key map (kbd "C-c C-n") #'eve-edit-notes)
    (define-key map (kbd "C-c C-d") #'eve-delete-fillers)
    map)
  "Keymap used in `eve-mode'.")

(defvar-local eve--data nil
  "In-memory representation of the current TJM file.")

(defvar-local eve--dirty nil
  "Whether `eve--data' has diverged from disk.")

(defvar-local eve--words-visible eve-show-words-by-default
  "Display flag controlling whether word-level timing is shown.")

(defvar-local eve--mpv-process nil
  "Handle to a running mpv playback process, if any.")

(defvar-local eve--saved-frame-geometry nil
  "Saved Emacs frame geometry before video layout was applied.
Stored as a plist (:left L :top T :width W :height H :fullscreen FS).
Nil when no layout is active.")

(defvar-local eve--visual-separators nil
  "List of segment ids that should be followed by a blank line.")

(defvar-local eve--focus-overlay nil
  "Overlay highlighting the current segment.")

(defvar-local eve--ruler-overlays nil
  "List of right-margin ruler overlays for the current buffer.")

(defvar-local eve--context-tick-overlays nil
  "List of source tick overlays shown while the context panel is open.")

(defvar-local eve--context-thumbnail-queue nil
  "Pending context thumbnail jobs for the current Eve buffer.")

(defvar-local eve--context-thumbnail-active 0
  "Number of active context thumbnail jobs for the current Eve buffer.")

(defvar-local eve--context-thumbnail-pending nil
  "Hash table of thumbnail cache keys already queued or running.")

(defvar-local eve--context-thumbnail-failed nil
  "Hash table of thumbnail cache keys that failed this session.")

(defvar-local eve--context-thumbnail-refresh-timer nil
  "Debounced timer for refreshing context panels after thumbnail generation.")

(defvar-local eve--ruler-total-duration 0.0
  "Cached total rendered duration in seconds, updated by `eve--update-ruler'.")

(defvar-local eve--ipc-socket-path nil
  "Path to the mpv IPC Unix socket for the current buffer.")

(defvar-local eve--ipc-process nil
  "Network process connected to the mpv IPC socket.")

(defvar-local eve--playback-timer nil
  "Repeating timer polling mpv playback position.")

(defvar-local eve--playback-overlay nil
  "Overlay highlighting the currently-playing segment.")

(defvar-local eve--playback-mode nil
  "Current playback mode: \\='source or \\='rendered.")

(defvar-local eve--playback-time-map nil
  "Snapshot of cumulative-times alist at play-start (rendered mode).")

(defvar-local eve--playback-source-segments nil
  "Source-mode: ordered list of segments from the active source file.")

(defvar-local eve--context-panels-disabled-ruler nil
  "Non-nil when Eve disabled `eve-ruler-mode' while a context panel is open.")

(defvar-local eve--last-echo-id nil
  "Segment id that was last echoed in the minibuffer.")

(defvar-local eve--undo-stack nil
  "Stack of TJM snapshots for undo operations.")

(defvar-local eve--render-status nil
  "Current render state for mode-line display.
One of: nil (idle), `speculative' (auto-render in flight),
`compiling' (eve-compile running), `cached' (all segments warm).")

(defvar-local eve--render-progress nil
  "Cons (COMPLETED . TOTAL) for the current compilation, or nil.")

(defvar-local eve--render-eta nil
  "Estimated seconds remaining for the current compilation, or nil.")

(defvar-local eve--compile-start-time nil
  "Time (`float-time') when the current compilation started, or nil.")

(defvar-local eve--redo-stack nil
  "Stack of TJM snapshots for redo operations.")

(defvar-local eve-hide-deleted-mode nil
  "Non-nil when deleted segments are hidden.
Forward-declared for the byte-compiler.")

(defvar-local eve-ruler-mode nil
  "Non-nil when the timestamp ruler is displayed.
Forward-declared for the byte-compiler.")

(defun eve--auto-save-blocker (&rest _)
  "Predicate that prevents auto-saving visited files for TJM buffers."
  nil)

(defvar-local eve--pending-output nil
  "Absolute path to the compiled video awaiting playback.")

(defvar-local eve--pending-temp nil
  "Temporary TJM manifest generated for section compilation.")

(defvar-local eve--pending-origin nil
  "Origin buffer associated with a compilation run.")

(defvar-local eve--pending-play-socket nil
  "Socket path for IPC playback to start after compilation completes.")

(defconst eve--transcribe-buffer-name "*eve transcribe*"
  "Buffer name used for async `eve transcribe` runs.")

(defvar eve--transcribe-mode-line-string nil
  "Global mode-line indicator for in-flight Eve transcriptions.")

(defvar eve--transcribe-process-count 0
  "Number of active Eve transcription processes.")

(defvar eve--segment-clipboard nil
  "Last Eve segment copied for `eve-paste-segment'.")

(defvar-local eve--folded-marker-ids nil
  "Marker ids whose rendered section bodies are folded.")

(defvar-local eve--fold-overlays nil
  "Active overlays hiding folded marker section bodies.")

(defconst eve--transcribe-mode-line-entry
  'eve--transcribe-mode-line-string
  "Mode-line form used to display global Eve transcription activity.")

(defun eve--ensure-transcribe-mode-line-entry ()
  "Ensure `global-mode-string' includes the Eve transcription indicator."
  (unless (member eve--transcribe-mode-line-entry global-mode-string)
    (setq global-mode-string
          (append global-mode-string (list eve--transcribe-mode-line-entry)))))

(defun eve--update-transcribe-mode-line ()
  "Refresh the global mode-line indicator for active Eve transcriptions."
  (eve--ensure-transcribe-mode-line-entry)
  (setq eve--transcribe-mode-line-string
        (when (> eve--transcribe-process-count 0)
          (propertize " Eve: transcribing…"
                      'face 'mode-line-emphasis
                      'help-echo "Eve is transcribing in the background. The manifest opens automatically on success.")))
  (force-mode-line-update t))

(defun eve--transcribe-started ()
  "Record that an Eve transcription process has started."
  (setq eve--transcribe-process-count (1+ eve--transcribe-process-count))
  (eve--update-transcribe-mode-line))

(defun eve--transcribe-finished-state ()
  "Record that an Eve transcription process has finished."
  (setq eve--transcribe-process-count (max 0 (1- eve--transcribe-process-count)))
  (eve--update-transcribe-mode-line))

(defun eve--default-output-file ()
  "Compute the default output mp4 path for the current TJM buffer."
  (when buffer-file-name
    (let ((parent (file-name-directory buffer-file-name))
	  (name (file-name-nondirectory
		 (directory-file-name
		  (file-name-directory buffer-file-name)))))
      (expand-file-name (concat name ".mp4") parent))))

(defun eve--text-edit-command (input output &optional quality)
  "Build the `eve text-edit` command for INPUT and OUTPUT.
QUALITY overrides `eve-compile-quality' when non-nil.
Only non-default settings are passed; the CLI resolves
codec, cache, and parallelism from the quality profile."
  (let* ((program (or (eve--resolve-cli)
                      (user-error "Cannot find eve CLI executable: %s"
                                  eve-cli-program)))
         (effective-quality (or quality eve-compile-quality "draft"))
         (parts (list
                 (shell-quote-argument program)
                 "text-edit"
                 (shell-quote-argument input)
                 "--output" (shell-quote-argument output)
                 "--subtitles"
                 "--preserve-short-gaps" (number-to-string eve-preserve-gaps-max)
                 "--quality" (shell-quote-argument effective-quality))))
    (when (/= eve-compile-jobs 0)
      (setq parts (append parts (list "--jobs" (number-to-string eve-compile-jobs)))))
    (when (and (< eve-compile-scale 1.0) (> eve-compile-scale 0.0))
      (setq parts (append parts (list "--scale" (number-to-string eve-compile-scale)))))
    (unless eve-compile-cache
      (setq parts (append parts (list "--no-cache"))))
    (string-join parts " ")))

(defun eve--compile-command (&optional quality)
  "Build the `eve text-edit` command for the current buffer.
QUALITY overrides `eve-compile-quality' when non-nil."
  (let* ((file buffer-file-name)
	 (output (eve--default-output-file)))
    (unless file
      (user-error "TJM buffer is not visiting a file"))
    (unless output
      (user-error "Unable to determine output filename"))

    (eve--text-edit-command file output quality)))

(defun eve--dry-run-analysis (manifest-file quality)
  "Run dry-run analysis on MANIFEST-FILE at QUALITY.
Return a plist with segment counts and estimated render time."
  (let* ((program (eve--resolve-cli))
         (raw (and program
                   (with-temp-buffer
                     (when (zerop
                            (call-process program nil t nil
                                          "text-edit" manifest-file
                                          "--output" "/dev/null"
                                          "--dry-run" "--json"
                                          "--quality" quality))
                       (buffer-string))))))
    (when raw
      (condition-case nil
          (json-parse-string raw :object-type 'plist)
        (error nil)))))

(defun eve--validate-manifest-sync (manifest-file)
  "Run `eve text-edit --validate --json' on MANIFEST-FILE synchronously.
Returns a plist with :valid, :errors, and :warnings, or nil on failure."
  (let* ((program (eve--resolve-cli))
         (output (and program
                      (with-temp-buffer
                        (when (zerop
                               (call-process program nil t nil
                                             "text-edit" manifest-file
                                             "--output" "/dev/null"
                                             "--validate" "--json"))
                          (buffer-string))))))
    (when output
      (condition-case nil
          (json-parse-string output :object-type 'plist)
        (error nil)))))

(defun eve-check-spelling ()
  "Spell-check the visible transcript text in the current eve buffer."
  (interactive)
  (let ((start (point-min))
        (end (point-max)))
    (ispell-region start end)))

(defun eve--slugify (title)
  "Return a filesystem-safe slug for TITLE."
  (let* ((down (downcase (or title "")))
	 (clean (replace-regexp-in-string "[^a-z0-9]+" "-" down))
	 (trim (replace-regexp-in-string "^-+\|+-+$" "" clean)))
    (if (string-empty-p trim)
	"section"
      trim)))

(defun eve--output-path (&optional slug)
  "Compute the default output path, optionally suffixed with SLUG."
  (when buffer-file-name
    (let* ((dir (file-name-directory buffer-file-name))
	   (parent (directory-file-name dir))
	   (base (file-name-nondirectory parent))
	   (name (if (and slug (not (string-empty-p slug)))
		     (format "%s-%s" base slug)
		   base)))
      (expand-file-name (concat name ".mp4") dir))))

(defun eve--filter-media-files (files)
  "Return FILES limited to supported media extensions."
  (seq-filter (lambda (file)
		(let ((extension (file-name-extension file)))
		  (and extension
		       (member (downcase extension) eve-media-extensions))))
	      files))

(defun eve--infer-manifest-path (files)
  "Infer a TJM manifest path for FILES."
  (when files
    (if (= (length files) 1)
	(concat (file-name-sans-extension (car files)) ".tjm.json")
      (let* ((dir (file-name-as-directory
		   (file-name-directory (car files))))
	     (name (file-name-nondirectory (directory-file-name dir))))
	(expand-file-name (concat name ".tjm.json") dir)))))

(defun eve--write-json-file (data path)
  "Write DATA as JSON to PATH."
  (let ((json-encoding-pretty-print t)
	(json-encoding-lisp-style-closings t))
    (with-temp-file path
      (insert (json-encode data))
      (unless (bolp) (insert "\n")))))

(defun eve--section-segments (marker)
  "Return MARKER and following segments until the next marker.
Including MARKER is harmless for plain structural markers (the renderer skips
non-renderable markers) and required for markers that carry intro/outro b-roll."
  (let* ((segments (eve--segments))
	 (start (cl-position marker segments :test #'eq)))
    (when start
      (let (result)
	(cl-loop for idx from start below (length segments)
		 for seg = (nth idx segments)
		 until (and (> idx start) (eve--marker-p seg))
		 do (push seg result))
	(nreverse result)))))

(defun eve--raw-buffer-name ()
  "Return the buffer name used for the raw JSON view."
  (format "*eve-raw:%s*"
	  (or (and buffer-file-name
		   (file-name-nondirectory buffer-file-name))
	      (buffer-name))))

(defun eve--reload-associated-buffers (file)
  "Reload any `eve-mode' buffers visiting FILE."
  (let ((target (expand-file-name file)))
    (dolist (buf (buffer-list))
      (when (buffer-live-p buf)
	(with-current-buffer buf
	  (when (and (derived-mode-p 'eve-mode)
		     buffer-file-name
		     (string= (expand-file-name buffer-file-name) target))
	    (eve-reload)))))))

(defun eve--raw-after-save ()
  "Refresh structured buffers when saving a raw TJM manifest."
  (when buffer-file-name
    (eve--reload-associated-buffers buffer-file-name)))

(defun eve--populate-raw-buffer (buf file)
  "Populate BUF with the literal contents of FILE."
  (with-current-buffer buf
    (setq-local buffer-file-name file)
    (setq-local buffer-file-truename
		(when (file-exists-p file)
		  (ignore-errors (file-truename file))))
    (setq-local default-directory (file-name-directory file))
    (let ((inhibit-read-only t))
      (erase-buffer)
      (insert-file-contents file nil nil nil t)
      (goto-char (point-min)))
    (set-buffer-modified-p nil)
    (setq-local buffer-read-only nil)
    (setq-local revert-buffer-function
		(lambda (&rest _)
		  (eve--populate-raw-buffer buf file)))
    (add-hook 'after-save-hook #'eve--raw-after-save nil t)
    (cond
     ((fboundp 'json-mode)
      (let* ((local-remap (copy-alist major-mode-remap-alist)))
	(setq local-remap (assq-delete-all 'json-mode local-remap))
	(setq local-remap (assq-delete-all 'jsonc-mode local-remap))
	(let ((major-mode-remap-alist local-remap))
	  (json-mode))))
     ((fboundp 'js-json-mode) (js-json-mode))
     ((fboundp 'js-mode) (js-mode))
     (t (fundamental-mode)))))

(defun eve--play-file (file)
  "Play FILE using the configured media player with IPC tracking."
  (let ((abs (expand-file-name file))
        (socket-path (concat (make-temp-name "/tmp/eve-mpv-") ".sock")))
    (unless (file-readable-p abs)
      (user-error "Output file not found: %s" abs))
    (eve--play-with-mpv abs nil nil socket-path)
    (eve-playback-mode 1)
    (add-hook 'post-command-hook #'eve--playback-seek-hook nil t)
    (message "Playing %s" (file-name-nondirectory abs))))

(defun eve--elapsed-label (start-time)
  "Return elapsed time since START-TIME as a compact label."
  (eve--format-time (max 0.0 (- (float-time) start-time))))

(defun eve--progress-message (prefix detail start-time)
  "Show PREFIX progress DETAIL with elapsed time from START-TIME."
  (message "%s: %s (%s elapsed)" prefix detail (eve--elapsed-label start-time)))

(defun eve--transcribe-progress-detail (event)
  "Return human-readable progress detail for transcribe EVENT."
  (let ((phase (alist-get 'phase event nil nil #'equal))
        (current (alist-get 'current event nil nil #'equal))
        (total (alist-get 'total event nil nil #'equal))
        (file (alist-get 'file event nil nil #'equal))
        (backend (alist-get 'backend event nil nil #'equal))
        (model (alist-get 'model event nil nil #'equal))
        (output (alist-get 'output event nil nil #'equal))
        (segments (alist-get 'segments event nil nil #'equal)))
    (pcase phase
      ("start" (format "queued %s file(s) with %s/%s" total backend model))
      ("load-model" (format "loading model %s via %s" model backend))
      ("extract" (format "extracting audio %s/%s — %s" current total (file-name-nondirectory (eve--stringify file))))
      ("decode" (format "decoding %s/%s — %s" current total (file-name-nondirectory (eve--stringify file))))
      ("resegment" "resegmenting transcript")
      ("tag-fillers" "tagging fillers")
      ("write" (format "writing %s" (file-name-nondirectory (eve--stringify output))))
      ("done" (format "done, wrote %s segment(s)" segments))
      (_ (eve--stringify phase)))))

(defun eve--transcribe-process-filter (process chunk)
  "Handle transcribe PROCESS output CHUNK, parsing JSON progress lines."
  (when-let* ((buffer (process-buffer process)))
    (with-current-buffer buffer
      (let ((inhibit-read-only t))
        (goto-char (point-max))
        (insert chunk))))
  (let ((pending (concat (or (process-get process :eve-progress-pending) "") chunk))
        lines)
    (setq lines (split-string pending "\n"))
    (process-put process :eve-progress-pending (car (last lines)))
    (dolist (line (butlast lines))
      (when (string-prefix-p "{" line)
        (condition-case nil
            (let ((event (json-parse-string line :object-type 'alist)))
              (when (equal (alist-get 'event event nil nil #'equal) "progress")
                (eve--progress-message
                 "Transcribe"
                 (eve--transcribe-progress-detail event)
                 (or (process-get process :eve-start-time) (float-time)))))
          (error nil))))))

(defun eve--suspicious-long-segments ()
  "Return non-marker segments longer than the suspicious-duration threshold."
  (when eve-validation-suspicious-segment-duration
    (seq-filter
     (lambda (segment)
       (and (not (eve--marker-p segment))
            (let ((start (eve--coerce-number (alist-get 'start segment)))
                  (end (eve--coerce-number (alist-get 'end segment))))
              (and start end
                   (> (- end start)
                      eve-validation-suspicious-segment-duration)))))
     (eve--segments))))

(defun eve--suspicious-transcript-intervals ()
  "Return index intervals needing post-transcription cleanup."
  (let ((segments (eve--segments))
        intervals
        previous
        previous-index)
    (cl-loop for segment in segments
             for index from 0
             unless (eve--marker-p segment)
             do (let ((start (eve--coerce-number (alist-get 'start segment)))
                      (end (eve--coerce-number (alist-get 'end segment))))
                  (when (and start end eve-validation-suspicious-segment-duration
                             (> (- end start)
                                eve-validation-suspicious-segment-duration))
                    (push (list :start index :end index :kind 'long) intervals))
                  (when (and previous previous-index start
                             eve-transcribe-suspicious-gap-duration
                             (equal (alist-get 'source previous)
                                    (alist-get 'source segment)))
                    (let ((previous-end
                           (eve--coerce-number (alist-get 'end previous))))
                      (when (and previous-end
                                 (> (- start previous-end)
                                    eve-transcribe-suspicious-gap-duration))
                        (push (list :start previous-index :end index :kind 'gap)
                              intervals))))
                  (setq previous segment
                        previous-index index)))
    (eve--merge-suspicious-intervals (nreverse intervals))))

(defun eve--merge-suspicious-intervals (intervals)
  "Merge overlapping suspicious transcript INTERVALS."
  (let ((sorted (sort (copy-sequence intervals)
                      (lambda (a b)
                        (< (plist-get a :start) (plist-get b :start)))))
        result)
    (dolist (interval sorted (nreverse result))
      (let ((last (car result)))
        (if (and last (<= (plist-get interval :start) (1+ (plist-get last :end))))
            (progn
              (setf (plist-get last :end)
                    (max (plist-get last :end) (plist-get interval :end)))
              (setf (plist-get last :kind) 'mixed))
          (push (copy-sequence interval) result))))))

(defun eve--reprocess-suspicious-intervals (intervals backend model)
  "Retranscribe suspicious index INTERVALS with BACKEND and MODEL."
  (let ((start-time (float-time))
        (count 0))
    (eve--record-state)
    (dolist (interval (reverse intervals))
      (let* ((segments (eve--segments))
             (start-index (plist-get interval :start))
             (end-index (plist-get interval :end))
             (start-segment (nth start-index segments))
             (end-segment (nth end-index segments)))
        (when (and start-segment end-segment)
          (eve--progress-message
           "Transcribe"
           (format "reprocessing %s through %s"
                   (eve--stringify (alist-get 'id start-segment))
                   (eve--stringify (alist-get 'id end-segment)))
           start-time)
          (let ((eve-segment-panel-source-buffer (current-buffer)))
            (eve--regenerate-segment-range
             start-segment end-segment backend model start-time))
          (setq count (1+ count)))))
    (when (> count 0)
      (eve--mark-dirty)
      (eve--render t))
    count))

(defun eve--maybe-offer-reprocess-suspicious-transcripts (backend model)
  "Offer retranscription for suspicious long/gapped regions using BACKEND and MODEL."
  (when eve-transcribe-offer-reprocess-suspicious
    (let ((intervals (eve--suspicious-transcript-intervals)))
      (when (and intervals
                 (y-or-n-p
                  (format "%d suspicious transcript region(s) found. Reprocess now? "
                          (length intervals))))
        (let ((count (eve--reprocess-suspicious-intervals intervals backend model)))
          (message "Reprocessed %d suspicious region(s)" count))))))

(defun eve--transcribe-finished (process event output-path)
  "Handle completion of transcribe PROCESS EVENT for OUTPUT-PATH."
  (eve--transcribe-finished-state)
  (let ((buffer (process-buffer process)))
    (if (and (eq (process-status process) 'exit)
             (= (process-exit-status process) 0))
        (progn
          (message "eve transcribe finished: %s" output-path)
          (find-file output-path)
          (when (derived-mode-p 'eve-mode)
            (eve--maybe-offer-reprocess-suspicious-transcripts
             (or (process-get process :eve-backend) eve-transcribe-backend)
             (or (process-get process :eve-model) eve-transcribe-model))))
      (when (buffer-live-p buffer)
        (pop-to-buffer buffer))
      (message "eve transcribe failed: %s" (string-trim event)))))

(defun eve--transcribe-async (files output-path)
  "Launch `eve transcribe` for FILES, writing OUTPUT-PATH asynchronously."
  (let* ((program (or (eve--resolve-cli)
                      (user-error "Cannot find eve CLI executable: %s"
                                  eve-cli-program)))
          (output-file (expand-file-name output-path))
          (buffer (get-buffer-create eve--transcribe-buffer-name))
          (command (append (list program "transcribe")
                           files
                           (list "--output" output-file
                                 "--backend" eve-transcribe-backend
                                 "--model" eve-transcribe-model)
                           (when eve-transcribe-verbatim
                             '("--verbatim"))
                           (when eve-transcribe-hotwords
                             (list "--hotwords" eve-transcribe-hotwords))
                           (when eve-transcribe-tag-fillers
                             '("--tag-fillers"))
                           '("--progress-json"))))
    (with-current-buffer buffer
      (let ((inhibit-read-only t))
        (erase-buffer)))
    (let* ((start-time (float-time))
           (process (make-process :name "eve-transcribe"
                                  :buffer buffer
                                  :command command
                                  :noquery t
                                  :filter #'eve--transcribe-process-filter
                                  :sentinel (lambda (process event)
                                              (eve--transcribe-finished
                                               process event output-file)))))
      (when (processp process)
        (process-put process :eve-start-time start-time)
        (process-put process :eve-backend eve-transcribe-backend)
        (process-put process :eve-model eve-transcribe-model))
      (eve--transcribe-started)
      (eve--progress-message "Transcribe"
                             (format "queued %d file(s)" (length files))
                             start-time))))

(defun eve--compilation-finished (buffer status)
  "Handle completion of an `eve text-edit` BUFFER with STATUS."
  (when (buffer-live-p buffer)
    (with-current-buffer buffer
	      (let ((output eve--pending-output)
	    (temp eve--pending-temp)
	    (origin eve--pending-origin)
	    (play-socket eve--pending-play-socket))
	(when (buffer-live-p origin)
	  (with-current-buffer origin
	    (setq eve--render-status nil
		  eve--render-progress nil
		  eve--render-eta nil
		  eve--compile-start-time nil)
	    (force-mode-line-update)))
	(when (and temp (file-exists-p temp))
	  (ignore-errors (delete-file temp)))
	(when (and output
		   (string-match-p "^finished" (string-trim status))
		   (buffer-live-p origin))
	  (with-current-buffer origin
	    (condition-case err
		(if play-socket
		    (progn
		      (setq-local eve--pending-play-socket nil)
		      (eve--play-with-mpv (expand-file-name output)
					 0.0 nil play-socket)
		      ;; Set state AFTER eve--play-with-mpv (teardown clears these)
		      (setq eve--playback-mode 'rendered)
		      (setq eve--playback-time-map
			    (eve--rendered-cumulative-times
			     (eve--segments) eve-hide-deleted-mode)))
		  (when eve-play-after-compile
		    (eve--play-file output)))
	      (error (message "%s" (or (cadr err) err))))))
	(setq eve--pending-output nil
	      eve--pending-temp nil
	      eve--pending-origin nil
	      eve--pending-play-socket nil)))))

(defvar compilation-filter-start)

(defun eve--compilation-filter ()
  "Parse rendering progress from compilation output, update mode-line."
  (let ((origin (and (boundp 'eve--pending-origin) eve--pending-origin)))
    (when (and origin (buffer-live-p origin))
      (save-excursion
        (goto-char compilation-filter-start)
        (while (re-search-forward
                "\\[eve text-edit\\] Rendering \\([0-9]+\\)/\\([0-9]+\\)" nil t)
          (let ((n (string-to-number (match-string 1)))
                (m (string-to-number (match-string 2))))
            (with-current-buffer origin
              (setq eve--render-progress (cons n m))
              (when eve--compile-start-time
                (let* ((elapsed (- (float-time) eve--compile-start-time))
                       (per-seg (if (> n 0) (/ elapsed n) 0))
                       (remaining (* per-seg (- m n))))
                  (setq eve--render-eta remaining)))
              (force-mode-line-update)))))
      (with-silent-modifications
        (ansi-color-apply-on-region compilation-filter-start (point))))))

(defun eve--run-compile (command output &optional temp)
  "Run COMMAND via `compilation-start`, capturing OUTPUT (and TEMP manifest)."
  (setq-local compile-command command)
  (setq-local eve--render-status 'compiling)
  (setq-local eve--render-progress nil)
  (setq-local eve--render-eta nil)
  (setq-local eve--compile-start-time (float-time))
  (force-mode-line-update)
  (let* ((origin-buffer (current-buffer))
         (play-socket eve--pending-play-socket)
	 (origin-window (selected-window))
	 (display-buffer-overriding-action
	  '((display-buffer-reuse-mode-window display-buffer-in-side-window)
	    (side . bottom)
	    (slot . 0)
	    (window-height . 12)
	    (inhibit-same-window . t)
	    (select . nil)
	    (window-parameters . ((no-other-window . t)))))
	 (compilation-buffer-name-function (lambda (_mode) "*eve text-edit*")))
    (let ((buffer (compilation-start command nil)))
      (when buffer
	(with-current-buffer buffer
	  (setq-local eve--pending-output (and output (expand-file-name output)))
	  (setq-local eve--pending-temp temp)
	  (setq-local eve--pending-origin origin-buffer)
          (setq-local eve--pending-play-socket play-socket)
	  (add-hook 'compilation-filter-hook #'eve--compilation-filter nil t)
	  (add-hook 'compilation-finish-functions #'eve--compilation-finished nil t))))
    (when (window-live-p origin-window)
      (when (and (buffer-live-p origin-buffer)
		 (not (eq (window-buffer origin-window) origin-buffer)))
	(set-window-buffer origin-window origin-buffer))
      (select-window origin-window))))

(defun eve-toggle-broll-continue (segment)
  "Inherit b-roll from the previous segment and mark it as a continuation."
  (interactive (list (or (eve--segment-at-point)
			 (user-error "No segment at point"))))
  (when (eve--marker-p segment)
    (user-error "Markers do not carry b-roll"))
  (let* ((segments (eve--segments))
	 (idx (cl-position segment segments :test #'eq)))
    (unless idx
      (error "Unable to determine segment index"))
    (let ((previous nil)
	  (scan (1- idx)))
      (while (and (null previous) (>= scan 0))
	(let ((candidate (nth scan segments)))
	  (when (and (not (eve--marker-p candidate))
		     (eve--segment-broll candidate))
	    (setq previous candidate))
	  (setq scan (1- scan))))
      (unless previous
	(user-error "No previous segment with b-roll metadata found"))
      (let* ((prev-broll (copy-alist (eve--segment-broll previous)))
	     (curr-broll (copy-alist prev-broll))
	     (prev-offset (alist-get 'start_offset prev-broll))
	     (prev-duration (alist-get 'duration prev-broll))
	     (start-prev (eve--coerce-number (alist-get 'start previous)))
	     (end-prev (eve--coerce-number (alist-get 'end previous)))
	     (duration-prev (and start-prev end-prev (- end-prev start-prev)))
	     (offset-seconds (eve--time->seconds prev-offset))
	     (continuation-offset (+ (or offset-seconds 0.0)
				     (or duration-prev 0.0)))
	     (remaining (when prev-duration
			  (max 0.0 (- (eve--time->seconds prev-duration)
				      (or duration-prev 0.0))))))
	(eve--record-state)
	(when curr-broll
	  (setf (alist-get 'continue curr-broll) t)
	  (setf (alist-get 'start_offset curr-broll)
		(eve--seconds->time-like prev-offset continuation-offset))
	  (when prev-duration
	    (setf (alist-get 'duration curr-broll)
		  (if (> remaining 0.0)
		      (eve--seconds->time-like prev-duration remaining)
		    nil))))
	(eve--set-segment-broll segment curr-broll)
	(eve--mark-dirty)
	(eve--render t)
	(eve--goto-segment (alist-get 'id segment))
	(eve--update-focus-overlay)
	(eve--echo-segment-info)
	(message "Copied b-roll from previous segment and set continue")))))

(cl-defun eve--pre-flight-validate (quality)
  "Validate manifest and check ETA before compile at QUALITY.
Return t to proceed, nil to abort."
  (when (and buffer-file-name (file-exists-p buffer-file-name))
    (let ((result (eve--validate-manifest-sync buffer-file-name)))
      (when result
        (let ((valid (eq t (plist-get result :valid)))
              (errors (plist-get result :errors))
              (warnings (plist-get result :warnings)))
          (dolist (w (if (arrayp warnings) (append warnings nil) warnings))
            (message "[eve] WARNING: %s" (plist-get w :message)))
          (unless valid
            (dolist (e (if (arrayp errors) (append errors nil) errors))
              (message "[eve] ERROR: %s" (plist-get e :message)))
            (unless (y-or-n-p "Manifest has validation errors.  Compile anyway? ")
              (cl-return-from eve--pre-flight-validate nil)))))
      (let ((analysis (eve--dry-run-analysis buffer-file-name quality)))
        (when analysis
          (let* ((raw-changed (plist-get analysis :changed_segments))
                 (raw-total (plist-get analysis :total_segments))
                 (changed (if (numberp raw-changed) raw-changed 0))
                 (total (if (numberp raw-total) raw-total 0))
                 (eta (plist-get analysis :estimated_seconds))
                 (threshold eve-compile-confirm-threshold))
            (when (and (numberp eta) (> eta threshold))
              (unless (y-or-n-p
                       (format "Render %d/%d segments (~%ds).  Proceed? "
                               changed total (round eta)))
                (cl-return-from eve--pre-flight-validate nil)))
            (when (and (zerop changed) (> total 0))
              (message "[eve] All %d segments cached — render will be instant." total)))))))
  t)

(defun eve-compile (&optional toggle-quality)
  "Compile the current TJM (or section) using `eve text-edit'.
With a prefix argument (\\[universal-argument]), use the opposite quality to
`eve-compile-quality': draft becomes final, final becomes draft."
  (interactive "P")
  (let* ((quality (if toggle-quality
                      (if (equal eve-compile-quality "draft") "final" "draft")
                    eve-compile-quality))
         (segment (eve--segment-at-point))
	 (marker? (and segment (eve--marker-p segment))))
    (when toggle-quality
      (message "Compiling at %s quality" quality))
    (when (and buffer-file-name (buffer-modified-p))
      (save-buffer))
    (unless (eve--pre-flight-validate quality)
      (user-error "Compilation aborted due to manifest errors"))
    (if (not marker?)
	(eve--run-compile (eve--compile-command quality)
			(eve--default-output-file))
      (let* ((title (eve--stringify (alist-get 'title segment)))
	     (slug (eve--slugify title))
	     (subset (eve--section-segments segment)))
	(unless subset
	  (user-error "Marker has no following segments to compile"))
	(let* ((temp (make-temp-file "eve-section" nil ".json"))
	       (data (copy-tree eve--data t))
	       (segments-copy (mapcar (lambda (seg) (copy-tree seg t)) subset))
	       (output (eve--output-path slug))
	       (cmd (eve--text-edit-command temp output quality)))
	  (setf (alist-get 'segments data) segments-copy)
	  (eve--write-json-file data temp)
	  (eve--run-compile cmd output temp))))))

(defvar-local eve--auto-render-process nil
  "Background speculative render process for the current buffer.")

(defvar-local eve--auto-render-timer nil
  "Idle timer for debouncing speculative renders in the current buffer.")

(defcustom eve-auto-render-default-enabled t
  "Whether `eve-auto-render-mode' is enabled in new Eve buffers.
When enabled, saving a TJM manifest triggers a background
`eve text-edit --segments <changed-ids>' to warm the cache.  The next
`eve-compile' (\\`C-c C-c') then only needs the final concat."
  :type 'boolean
  :group 'eve)

(defcustom eve-auto-render-idle-seconds 2.0
  "Idle time in seconds before a background speculative render fires after save."
  :type 'float
  :group 'eve)

(defcustom eve-auto-render-all-segments nil
  "Whether speculative renders should warm every segment cache entry.
When nil, only segments reported as changed by the dry run are warmed.  When
non-nil, every segment id is requested with `eve text-edit --segments', relying
on the CLI cache to skip entries that are already warm."
  :type 'boolean
  :group 'eve)

(defcustom eve-auto-render-low-priority t
  "Whether speculative render processes should run at low CPU priority.
When non-nil and the `nice' executable is available, background cache warming is
launched as `nice -n 19 eve text-edit ...'."
  :type 'boolean
  :group 'eve)

(defcustom eve-context-thumbnails-enabled t
  "Whether Eve context-panel timestamp rows show video thumbnails."
  :type 'boolean
  :group 'eve)

(defcustom eve-context-thumbnail-width 96
  "Width in pixels for context-panel timestamp thumbnails."
  :type 'integer
  :group 'eve)

(defcustom eve-context-thumbnail-height 54
  "Height in pixels for context-panel timestamp thumbnails."
  :type 'integer
  :group 'eve)

(defcustom eve-context-thumbnail-source 'best
  "Preferred source for context-panel timestamp thumbnails.
`best' uses rendered output when it already exists and is fresh, falling back to
source media.  `source' uses source media only.  `rendered' uses rendered output
only and shows text until it is available."
  :type '(choice (const :tag "Rendered when available, else source" best)
                 (const :tag "Source media only" source)
                 (const :tag "Rendered output only" rendered))
  :group 'eve)

(defcustom eve-context-thumbnail-jobs 1
  "Maximum concurrent ffmpeg thumbnail extraction jobs per Eve buffer."
  :type 'integer
  :group 'eve)

(defcustom eve-segment-panel-height 12
  "Height of the Eve current-segment bottom panel."
  :type 'natnum
  :group 'eve)

(defvar-local eve-auto-render-mode nil
  "Non-nil when automatic Eve render-cache warming is enabled.")

(defun eve--auto-render-segment-ids ()
  "Return segment IDs in the current manifest."
  (delq nil
        (mapcar (lambda (segment)
                  (let ((id (alist-get 'id segment)))
                    (and id (eve--stringify id))))
                (eve--segments))))

(defun eve--auto-render-changed-segment-ids ()
  "Return a list of segment IDs that differ from the cache, or nil."
  (when (and buffer-file-name (file-exists-p buffer-file-name))
    (let ((analysis (eve--dry-run-analysis buffer-file-name eve-compile-quality)))
      (when analysis
        (let ((ids (plist-get analysis :changed_ids)))
          (when (and (arrayp ids) (> (length ids) 0))
            (append ids nil)))))))

(defun eve--auto-render-command (command)
  "Return low-priority wrapper for COMMAND when configured."
  (if (and eve-auto-render-low-priority (executable-find "nice"))
      (append (list (executable-find "nice") "-n" "19") command)
    command))

(defun eve--auto-render-fire (&optional all-segments)
  "Launch a speculative background render for the current buffer.
When ALL-SEGMENTS is non-nil, ask the CLI to warm every segment id, relying on
the segment cache to skip already-warm entries."
  (when (and (derived-mode-p 'eve-mode)
             buffer-file-name
             (file-exists-p buffer-file-name))
    (when (and eve--auto-render-process
               (process-live-p eve--auto-render-process))
      (delete-process eve--auto-render-process)
      (setq eve--auto-render-process nil))
    (let ((seg-ids (if (or all-segments eve-auto-render-all-segments)
                       (eve--auto-render-segment-ids)
                     (eve--auto-render-changed-segment-ids)))
          (buf (current-buffer)))
      (if (null seg-ids)
          (progn
            (setq eve--render-status 'cached)
            (force-mode-line-update))
        (setq eve--render-status 'speculative)
        (force-mode-line-update)
        (let* ((output (eve--default-output-file))
               (base-cmd (split-string
                          (eve--text-edit-command buffer-file-name output)
                          " " t))
               (seg-args (apply #'append
                                (mapcar (lambda (id) (list "--segments" id))
                                        seg-ids)))
               (full-cmd (eve--auto-render-command
                          (append base-cmd seg-args))))
          (setq eve--auto-render-process
                (make-process :name "eve-auto-render"
                              :buffer nil
                              :command full-cmd
                              :noquery t
                              :sentinel
                              (lambda (proc _event)
                                (when (eq (process-status proc) 'exit)
                                  (when (buffer-live-p buf)
                                    (with-current-buffer buf
                                      (setq eve--auto-render-process nil
                                            eve--render-status 'cached)
                                      (force-mode-line-update))))))))))))

(defun eve--auto-render-after-save ()
  "Schedule a speculative render after the buffer is saved."
  (when eve-auto-render-mode
    (when eve--auto-render-timer
      (cancel-timer eve--auto-render-timer))
    (setq eve--auto-render-timer
          (run-with-idle-timer eve-auto-render-idle-seconds nil
                               #'eve--auto-render-fire))))

;;;###autoload
(define-minor-mode eve-auto-render-mode
  "Automatically warm Eve's segment render cache after saves.
The mode is buffer-local.  Automatic renders run in the background and use low
CPU priority when `eve-auto-render-low-priority' is non-nil."
  :lighter " EveRender"
  (if eve-auto-render-mode
      (add-hook 'after-save-hook #'eve--auto-render-after-save nil t)
    (remove-hook 'after-save-hook #'eve--auto-render-after-save t)
    (when eve--auto-render-timer
      (cancel-timer eve--auto-render-timer)
      (setq eve--auto-render-timer nil))))

(defun eve-render-cache-changed ()
  "Warm render cache entries for changed segments now."
  (interactive)
  (when (buffer-modified-p)
    (save-buffer))
  (eve--auto-render-fire nil))

(defun eve-render-cache-all ()
  "Warm render cache entries for all segments now at low priority."
  (interactive)
  (when (buffer-modified-p)
    (save-buffer))
  (eve--auto-render-fire t))

(defun eve--cache-dir ()
  "Return the render cache directory for the current Eve buffer."
  (unless buffer-file-name
    (user-error "Eve buffer is not visiting a file"))
  (expand-file-name ".eve-cache" (file-name-directory buffer-file-name)))

;;;###autoload
(defun eve-wipe-cache ()
  "Delete the `.eve-cache' directory next to the current TJM manifest."
  (interactive)
  (let ((cache-dir (eve--cache-dir)))
    (unless (file-directory-p cache-dir)
      (user-error "No Eve cache directory: %s" cache-dir))
    (when (and eve--auto-render-process
               (process-live-p eve--auto-render-process))
      (delete-process eve--auto-render-process)
      (setq eve--auto-render-process nil))
    (when (yes-or-no-p (format "Delete Eve cache %s? " cache-dir))
      (delete-directory cache-dir t)
      (setq eve--render-status nil
            eve--render-progress nil
            eve--render-eta nil)
      (force-mode-line-update)
      (message "Deleted Eve cache: %s" cache-dir))))

(defun eve--auto-render-teardown ()
  "Clean up speculative render state for the current buffer."
  (when eve--auto-render-timer
    (cancel-timer eve--auto-render-timer)
    (setq eve--auto-render-timer nil))
  (when (and eve--auto-render-process
             (process-live-p eve--auto-render-process))
    (delete-process eve--auto-render-process)
    (setq eve--auto-render-process nil))
  (when eve--context-thumbnail-refresh-timer
    (cancel-timer eve--context-thumbnail-refresh-timer)
    (setq eve--context-thumbnail-refresh-timer nil)))

(defun eve--play-marker-broll (segment)
  "Play marker SEGMENT's b-roll directly when it references media."
  (let* ((broll (eve--segment-broll segment))
         (file (and broll (eve--stringify (alist-get 'file broll))))
         (abs-file (and file (not (string-empty-p file))
                        (not (string-match-p "\\.json\\'" file))
                        (eve--resolve-relative-path file))))
    (when (and abs-file (file-exists-p abs-file))
      (let* ((start (or (eve--time->seconds (alist-get 'start_offset broll)) 0.0))
             (duration (eve--time->seconds (alist-get 'duration broll)))
             (end (and duration (+ start duration)))
             (socket-path (concat (make-temp-name "/tmp/eve-mpv-") ".sock")))
        (eve--play-with-mpv abs-file start end socket-path)
        (setq eve--playback-source-segments (list segment))
        (setq eve--playback-mode 'source)
        (eve-playback-mode 1)
        (add-hook 'post-command-hook #'eve--playback-seek-hook nil t)
        (message "Playing marker b-roll %s" file)
        t))))

(defun eve--play-marker (segment)
  "Play marker SEGMENT b-roll or its compiled section."
  (let* ((title (eve--stringify (alist-get 'title segment)))
	 (slug (eve--slugify title))
	 (output (eve--output-path slug)))
    (or (eve--play-marker-broll segment)
        (if (and output (file-exists-p output))
	    (eve--play-file output)
          (progn
	    (message "No compiled output for '%s'; compiling section..." (or title slug))
	    (eve-compile))))))

(when (featurep 'hydra)
  (defhydra eve-hydra (:hint nil :color teal)
    "
Navigation        Edit              Fillers           Playback
──────────────   ──────────────   ──────────────   ──────────────────────
_j_/_k_  next/prev  _d_ del word      _f_ filler        _SPC_/_RET_ play
_n_/_p_  next/prev  _D_ del segment   _F_ del fillers   ]f next filler
_J_/_K_  move up/dn _s_/|  split      _A_ non-filler _S-<left>_/_S-<right>_ ±30s/10s
                  _m_/_)_ merge                       During playback:
Metadata          _u_ undo          Structure           _SPC_ pause/resume
──────────────   _C-r_ redo         ___  separator       _q_ stop
_t_ toggle tag    _e_ edit (DWIM)   _C-RET_ separator
_b_ edit b-roll                    _o_ insert marker
_B_ b-roll cont   Global C-c C-x
_i_ speaker       ──────────────
_r_ timestamps    _C-c C-c_ compile    _C-c C-w_ word timings
                  _C-c C-v_ validate   _C-c C-l_ ruler mode
_g_ reload        _C-c C-r_ play rendered  _C-c C-h_ hide deleted
_q_ quit          _C-c C-o_ raw JSON   _C-c C-t_ transcribe
_?_ this help     _C-c C-p_ panels     _C-c C-s_ play source
                  _C-c C-b_ b-roll placeholders
                  _C-c C-n_ edit notes
"
    ("j" eve-next-segment)
    ("k" eve-previous-segment)
    ("n" eve-next-segment)
    ("p" eve-previous-segment)
    ("J" eve-move-segment-down)
    ("K" eve-move-segment-up)
    ("d" eve-delete-word)
    ("D" eve-delete-segment)
    ("s" eve-split-segment)
    ("|" eve-split-segment)
    ("m" eve-merge-with-next)
    (")" eve-merge-with-next)
    ("u" eve-undo)
    ("C-r" eve-redo)
    ("e" eve-dwim-edit)
    ("o" eve-insert-marker)
    ("t" eve-toggle-tag)
    ("b" eve-edit-broll)
    ("B" eve-toggle-broll-continue)
    ("i" eve-edit-speaker)
    ("r" eve-edit-start-end)
    ("f" eve-dwim-filler)
    ("F" eve-delete-fillers)
    ("A" eve-accept-filler)
    ("] f" eve-next-filler)
    ("[ f" eve-previous-filler)
    ("SPC" eve-play-segment)
    ("RET" eve-play-segment)
    ("<left>" eve-seek-short-backward)
    ("<right>" eve-seek-short-forward)
    ("S-<left>" eve-seek-long-backward)
    ("S-<right>" eve-seek-long-forward)
    ("_" eve-toggle-separator)
    ("C-RET" eve-toggle-separator)
    ("g" eve-reload)
    ("C-c C-c" eve-compile)
    ("C-c C-v" eve-validate)
    ("C-c C-r" eve-play-rendered)
    ("C-c C-p" eve-context-panels-open)
    ("C-c C-o" eve-open-raw-json)
    ("C-c C-h" eve-hide-deleted-mode)
    ("C-c C-w" eve-toggle-words)
    ("C-c C-l" eve-ruler-mode)
    ("C-c C-t" eve-transcribe)
    ("C-c C-s" eve-play-source)
    ("C-c C-b" eve-edit-broll-placeholders)
    ("C-c C-n" eve-edit-notes)
    ("?" nil "close")
    ("q" nil "close")))

(defun eve--show-help ()
  "Show the eve keybinding cheatsheet.
Uses the hydra popup when hydra is available; falls back to a brief
summary in the echo area."
  (interactive)
  (if (and (featurep 'hydra) (fboundp 'eve-hydra/body))
      (eve-hydra/body)
    (message
     (concat "eve bindings: j/k nav  d del-word  D del-seg  s split  m merge  "
             "u undo  C-r redo  f filler  F del-fillers  A non-filler  ]f/[f nav  "
             "SPC play  C-c C-c compile  ? (install hydra for full cheatsheet)"))))

(defun eve--snapshot ()
  "Create a deep copy of `eve--data'."
  (json-parse-string (json-encode eve--data)
		     :object-type 'alist :array-type 'list
		     :null-object nil :false-object nil))

(defun eve--record-state ()
  "Push current state onto the undo stack prior to mutation."
  (push (eve--snapshot) eve--undo-stack)
  (setq eve--redo-stack nil))

(defun eve--stringify (value)
  "Return VALUE rendered as a string."
  (cond
   ((null value) "")
   ((stringp value) value)
   (t (format "%s" value))))

(defun eve--word-strings (word)
  "Return non-empty candidate text strings for WORD.
Both `spoken' and legacy `token' values are included when present."
  (delete-dups
   (delq nil
         (mapcar (lambda (value)
                   (let ((text (string-trim (eve--stringify value))))
                     (unless (string-empty-p text)
                       text)))
                 (list (alist-get 'spoken word)
                       (alist-get 'token word))))))

(defun eve--word-matches-filler-p (word)
  "Return non-nil when WORD matches `eve-filler-regex'."
  (let ((strings (eve--word-strings word)))
    (and strings
         (seq-some (lambda (text)
                     (seq-some (lambda (regexp)
                                 (string-match-p regexp text))
                               eve-filler-regex))
                   strings))))

(defun eve--normalize-word-tokens (text)
  "Return TEXT as lowercase alnum tokens.
Downcases, removes non-alphanumeric/non-space characters, collapses
whitespace, and splits on spaces. Returns nil for empty input."
  (let* ((lower (downcase (or (eve--stringify text) "")))
         (stripped (replace-regexp-in-string "[^[:alnum:][:space:]]" " " lower))
         (collapsed (replace-regexp-in-string "\\s-+" " " stripped))
         (trimmed (string-trim collapsed)))
    (unless (string-empty-p trimmed)
      (split-string trimmed " " t))))

(defun eve--normalize-filler-text (text)
  "Backward-compatible alias for `eve--normalize-word-tokens'."
  (eve--normalize-word-tokens text))

(defun eve--phrase-matches-at-p (normalized-words phrase-tokens start-idx)
  "Return non-nil when PHRASE-TOKENS match NORMALIZED-WORDS at START-IDX."
  (let ((plen (length phrase-tokens))
        (wlen (length normalized-words)))
    (and (> plen 0)
         (<= (+ start-idx plen) wlen)
         (cl-loop for j from 0 below plen
                  always (string= (nth j phrase-tokens)
                                  (nth (+ start-idx j) normalized-words))))))

(defun eve--legacy-filler-regexp-literal (regexp)
  "Extract a literal filler phrase from anchored REGEXP.
Returns nil when REGEXP is not a simple anchored literal like `\\`um\\'' or
contains regex metacharacters."
  (when (and (stringp regexp)
             (string-match "\\`\\\\`\\(.+\\)\\\\'\\'" regexp))
    (let ((candidate (match-string 1 regexp)))
      (when (string-match-p "\\`[[:alnum:][:space:]-]+\\'" candidate)
        candidate))))

(defun eve--filler-phrase-list ()
  "Return normalized phrase-token lists for configured filler settings.
Combines `eve-filler-phrases' with literal-compatible entries from
`eve-filler-regex'. Returned phrases are deduplicated and sorted longest-first."
  (let (result)
    (dolist (phrase eve-filler-phrases)
      (let ((tokens (eve--normalize-word-tokens phrase)))
        (when tokens
          (cl-pushnew tokens result :test #'equal))))
    (when (null result)
      (dolist (regexp eve-filler-regex)
        (let* ((literal (eve--legacy-filler-regexp-literal regexp))
               (tokens (and literal (eve--normalize-word-tokens literal))))
          (when tokens
            (cl-pushnew tokens result :test #'equal)))))
    (sort result (lambda (a b) (> (length a) (length b))))))

(defun eve--filler-exception-phrase-list ()
  "Return normalized exception phrase-token lists, longest first."
  (let (result)
    (dolist (phrase eve-filler-exception-phrases)
      (let ((tokens (eve--normalize-word-tokens phrase)))
        (when tokens
          (cl-pushnew tokens result :test #'equal))))
    (sort result (lambda (a b) (> (length a) (length b))))))

(defun eve--apply-filler-tags ()
  "Apply filler tags to words in all segments.
Returns the count of words newly tagged. Does not render, message, mark dirty,
or record undo state."
  (let ((phrases (eve--filler-phrase-list))
        (exceptions (eve--filler-exception-phrase-list))
        (tagged 0))
    (dolist (segment (eve--segments))
      (let ((words (alist-get 'words segment)))
        (when (listp words)
          (let* ((normalized-words
                  (mapcar (lambda (word)
                            (let* ((text (or (and (alist-get 'spoken word)
                                                  (eve--stringify (alist-get 'spoken word)))
                                             (and (alist-get 'token word)
                                                  (eve--stringify (alist-get 'token word)))
                                             ""))
                                   (tokens (eve--normalize-word-tokens text)))
                              (or (car tokens) "")))
                          words))
                 (len (length words))
                 (i 0))
            (while (< i len)
              (let ((exception-len
                     (cl-loop for phrase in exceptions
                              when (eve--phrase-matches-at-p normalized-words phrase i)
                              return (length phrase)
                              finally return nil)))
                (if exception-len
                    (setq i (+ i exception-len))
                  (let ((match-len
                         (cl-loop for phrase in phrases
                                  when (eve--phrase-matches-at-p normalized-words phrase i)
                                  return (length phrase)
                                  finally return nil)))
                    (if match-len
                        (progn
                          (cl-loop for j from 0 below match-len
                                   do (let ((word (nth (+ i j) words)))
                                        (unless (or (eve--word-filler-p word)
                                                    (eve--word-no-filler-p word))
                                          (eve--mark-word-filler word)
                                          (setq tagged (1+ tagged)))))
                          (setq i (+ i match-len)))
                      (setq i (1+ i)))))))))))
    tagged))

(defun eve--alist-set (alist key value)
  "Return ALIST with KEY set to VALUE, mutating in place when possible."
  (let ((cell (assq key alist)))
    (if cell
        (setcdr cell value)
      (setq alist (if alist
                      (nconc alist (list (cons key value)))
                    (list (cons key value)))))
    alist))

(defun eve--set-word-edit-kind (word kind)
  "Set WORD's nested edit KIND while preserving other edit members."
  (eve--set-edit-field word 'kind kind))

(defun eve--word-filler-state (word)
  "Return WORD filler state: `filler', `no-filler', or nil.
The explicit nested `filler' edit field wins over legacy `kind = filler'."
  (let ((edit (eve--edit-metadata word)))
    (cond
     ((eve--edit-key-present-p 'filler edit)
      (let ((value (eve--aget 'filler edit)))
        (cond
         ((or (eq value t) (equal value "t") (equal value "filler")) 'filler)
         ((or (eq value :json-false) (null value) (equal value "no-filler"))
          'no-filler)
         (t nil))))
     ((equal (eve--edit-kind word) "filler") 'filler)
     (t nil))))

(defun eve--word-filler-p (word)
  "Return non-nil when WORD is marked as a filler."
  (eq (eve--word-filler-state word) 'filler))

(defun eve--word-no-filler-p (word)
  "Return non-nil when WORD is explicitly marked as not filler."
  (eq (eve--word-filler-state word) 'no-filler))

(defun eve--set-word-filler (word value)
  "Set WORD filler edit field to VALUE and remove legacy filler kind."
  (eve--remove-word-edit-kind word)
  (eve--set-edit-field word 'filler value))

(defun eve--mark-word-filler (word)
  "Mark WORD as a filler."
  (eve--set-word-filler word t))

(defun eve--mark-word-non-filler (word)
  "Mark WORD as explicitly not a filler."
  (eve--set-word-filler word "no-filler"))

(defun eve--remove-edit-field (item key)
  "Remove ITEM nested edit KEY while preserving other edit members."
  (let ((edit (copy-tree (eve--edit-metadata item) t)))
    (when (listp edit)
      (setq edit (assq-delete-all key edit))
      (if edit
          (eve--alist-set item 'edit edit)
        (eve--alist-set item 'edit nil)))))

(defun eve--remove-word-edit-kind (word)
  "Remove WORD's nested edit kind while preserving other edit members."
  (eve--remove-edit-field word 'kind))

(defun eve--set-edit-deleted (item deleted)
  "Set ITEM's nested edit deleted flag while preserving other edit members."
  (eve--set-edit-field item 'deleted deleted))

(defun eve--toggle-edit-deleted (item)
  "Toggle ITEM's nested edit deleted flag, returning the new state."
  (let ((deleted (not (eve--edit-deleted-p item))))
    (eve--set-edit-deleted item deleted)
    deleted))

(defun eve--aget (key alist)
  "Fetch KEY from ALIST supporting both symbol and string lookups."
  (cond
   ((null alist) nil)
   ((symbolp key)
    (or (alist-get key alist)
	(alist-get (symbol-name key) alist nil nil #'equal)))
    ((stringp key)
     (or (alist-get key alist nil nil #'equal)
	(let ((sym (ignore-errors (intern key))))
	  (and (symbolp sym) (alist-get sym alist)))))
    (t (alist-get key alist nil nil #'equal))))

(defun eve--edit-key-present-p (key alist)
  "Return non-nil when ALIST contains KEY as a symbol or string entry."
  (cond
   ((null alist) nil)
   ((symbolp key)
    (or (assq key alist)
        (assoc (symbol-name key) alist)))
   ((stringp key)
    (or (assoc key alist)
        (let ((sym (ignore-errors (intern key))))
          (and (symbolp sym) (assq sym alist)))))
   (t (assoc key alist))))

(defun eve--edit-field (item key &optional legacy-key)
  "Return ITEM edit KEY, falling back to LEGACY-KEY on ITEM itself."
  (let ((edit (eve--edit-metadata item))
        (fallback (or legacy-key key)))
    (cond
     ((eve--edit-key-present-p key edit) (eve--aget key edit))
     ((eve--edit-key-present-p fallback item) (eve--aget fallback item))
     (t nil))))

(defun eve--set-edit-field (item key value)
  "Set ITEM nested edit KEY to VALUE while preserving other edit members."
  (let ((edit (eve--edit-metadata item)))
    (unless (listp edit)
      (setq edit nil))
    (setq edit (copy-tree edit t))
    (setq edit (eve--alist-set edit key value))
    (eve--alist-set item 'edit edit)))

(defun eve--segment-tags (segment)
  "Return SEGMENT tags, preferring nested edit metadata."
  (eve--edit-field segment 'tags))

(defun eve--segment-notes (segment)
  "Return SEGMENT notes, preferring nested edit metadata."
  (eve--edit-field segment 'notes))

(defun eve--segment-broll (segment)
  "Return SEGMENT b-roll metadata, preferring nested edit metadata."
  (eve--edit-field segment 'broll))

(defun eve--set-segment-tags (segment tags)
  "Set SEGMENT tags inside nested edit metadata."
  (eve--set-edit-field segment 'tags tags))

(defun eve--set-segment-notes (segment notes)
  "Set SEGMENT notes inside nested edit metadata."
  (eve--set-edit-field segment 'notes notes))

(defun eve--set-segment-broll (segment broll)
  "Set SEGMENT b-roll metadata inside nested edit metadata."
  (eve--set-edit-field segment 'broll (and broll (copy-tree broll t))))

(defun eve--segment-kind (segment)
  (let ((kind (alist-get 'kind segment)))
    (when kind
      (downcase (eve--stringify kind)))))

(defun eve--marker-p (segment)
  (string= (eve--segment-kind segment) "marker"))

(defun eve--display-time (value)
  (cond
   ((null value) nil)
   ((numberp value) (eve--format-time value))
   ((and (stringp value) (string-match-p ":" value)) value)
   ((stringp value)
    (let ((num (eve--coerce-number value)))
      (if num (eve--format-time num) value)))
   (t (eve--format-time value))))

(defun eve--normalize-time-input (input)
  "Normalise user-entered time INPUT to either a float, string, or nil."
  (let ((trim (string-trim (or input ""))))
    (cond
     ((string-empty-p trim) nil)
     ((string-match-p "\\`[+-]?[0-9]*\\.?[0-9]+\\'" trim)
      (string-to-number trim))
     ((string-match-p "\\`[0-9]+:\\(?:[0-9]+:\\)?[0-9]+\\(?:\\.[0-9]+\\)?\\'" trim)
      trim)
     (t (user-error "Invalid time format: %s" input)))))

(defun eve--time->seconds (value)
  "Convert VALUE (string or number) to seconds as a float."
  (cond
   ((null value) nil)
   ((numberp value) (float value))
   ((stringp value)
    (let ((trim (string-trim value)))
      (cond
       ((string-match-p "\\`[0-9]*\\.?[0-9]+\\'" trim)
	(string-to-number trim))
       ((string-match-p "\\`[0-9]+:[0-9]+\\(?:\\.[0-9]+\\)?\\'" trim)
	(pcase-let* ((`(,minutes ,seconds)
		      (mapcar #'string-to-number (split-string trim ":")))
		     (total (+ (* minutes 60.0) seconds)))
	  total))
       ((string-match-p "\\`[0-9]+:[0-9]+:[0-9]+\\(?:\\.[0-9]+\\)?\\'" trim)
	(pcase-let* ((parts (mapcar #'string-to-number (split-string trim ":")))
		     (`(,hours ,minutes ,seconds) parts)
		     (total (+ (* hours 3600.0) (* minutes 60.0) seconds)))
	  total))
       (t nil))))
   (t nil)))

(defun eve--resolve-relative-path (path)
  "Return PATH expanded relative to the current buffer."
  (when (and path (not (string-empty-p path)))
    (let ((base (file-name-directory (or buffer-file-name default-directory))))
      (expand-file-name path base))))

(defun eve--read-json-file (file)
  "Read FILE into an alist, returning nil when FILE is unreadable."
  (when (and file (file-readable-p file))
    (condition-case err
	(with-temp-buffer
	  (insert-file-contents file)
	  (let ((json-source (buffer-substring-no-properties (point-min) (point-max))))
	    (unless (string-empty-p (string-trim json-source))
	      (json-parse-string json-source
				 :object-type 'alist
				 :array-type 'list
				 :null-object nil
				 :false-object nil))))
      (error
       (message "[eve] Failed to read JSON template %s: %s" file (error-message-string err))
       nil))))

(defun eve--broll-template-data (broll)
  "Return template metadata referenced by BROLL, if any."
  (let* ((file (eve--stringify (alist-get 'file broll)))
	 (resolved (and file
			(string-match-p "\\.json\\'" file)
			(eve--resolve-relative-path file))))
    (when resolved
      (eve--read-json-file resolved))))

(defun eve--placeholder-get (key placeholders)
  "Lookup KEY inside PLACEHOLDERS (string-keyed alist)."
  (alist-get (eve--stringify key) placeholders nil nil #'equal))

(defun eve--placeholder-set (key value placeholders)
  "Associate KEY with VALUE inside PLACEHOLDERS, removing when VALUE is nil.
Returns the updated PLACEHOLDERS alist."
  (let ((key (eve--stringify key)))
    (if value
	(setf (alist-get key placeholders nil nil #'equal) value)
      (setf (alist-get key placeholders nil 'remove #'equal) nil))
    placeholders))

(defun eve--merge-placeholder-maps (&rest maps)
  "Return the merged associative list from MAPS,
later maps overriding earlier ones."
  (let ((result nil))
    (dolist (map maps)
      (dolist (entry map)
	(let ((key (eve--stringify (car entry)))
	      (value (cdr entry)))
	  (when key
	    (setf (alist-get key result nil nil #'equal) value)))))
    result))

(defun eve--placeholder-keys (&rest maps)
  "Return all unique placeholder keys mentioned in MAPS."
  (let ((keys nil))
    (dolist (map maps)
      (dolist (entry map)
	(let ((key (eve--stringify (car entry))))
	  (when (and key (not (string-empty-p key)))
	    (cl-pushnew key keys :test #'string=)))))
    (sort keys #'string<)))

(defun eve--quote-value (value)
  "Return VALUE formatted for human readable output."
  (cond
   ((stringp value) (format "%S" value))
   ((numberp value) (format "%.3f" value))
   (t (format "%S" value))))

(defun eve--summarize-placeholders (defaults overrides)
  "Return a human-friendly summary of placeholder DEFAULTS and OVERRIDES."
  (let* ((keys (eve--placeholder-keys defaults overrides))
	 (parts
	  (mapcar
	   (lambda (key)
	     (let* ((default (eve--placeholder-get key defaults))
		    (override (eve--placeholder-get key overrides))
		    (value (or override default))
		    (role (cond
			   (override (if default "override" "custom"))
			   (default "template")
			   (t "unset"))))
	       (format "%s=%s (%s)"
		       key
		       (eve--quote-value value)
		       role)))
	   keys)))
    (when parts
      (string-join parts ", "))))

(defun eve--seconds->time-like (original seconds)
  "Convert SECONDS to match ORIGINAL's format."
  (when seconds
    (if (stringp original)
	(eve--format-time seconds)
      seconds)))

(defun eve--auto-correct ()
  "Attempt to auto-correct common validation issues. Return change summaries."
  (let ((tolerance eve-validation-time-tolerance)
	(changes nil)
	(recorded nil))
    (dolist (segment (eve--segments))
      (unless (eve--marker-p segment)
	(let* ((id (eve--stringify (alist-get 'id segment)))
	       (start (eve--coerce-number (alist-get 'start segment)))
	       (end (eve--coerce-number (alist-get 'end segment)))
	       (words (alist-get 'words segment))
	       (changed nil))
	  (when words
	    (let* ((word-starts (delq nil (mapcar (lambda (word)
						    (eve--coerce-number (alist-get 'start word)))
						  words)))
		   (word-ends (delq nil (mapcar (lambda (word)
						  (eve--coerce-number (alist-get 'end word)))
						words))))
	      (when (and word-starts word-ends)
		(let ((first (apply #'min word-starts))
		      (last (apply #'max word-ends)))
		  (when (or (null start) (and start (> (- start first) tolerance)))
		    (unless recorded
		      (eve--record-state)
		      (setq recorded t))
		    (setf (alist-get 'start segment) first)
		    (setq start first
			  changed t)
		    (push (format "%s: aligned start to %s" id (eve--format-time first))
			  changes))
		  (when (or (null end) (and end (> (- last end) tolerance)))
		    (unless recorded
		      (eve--record-state)
		      (setq recorded t))
		    (setf (alist-get 'end segment) last)
		    (setq end last
			  changed t)
		    (push (format "%s: aligned end to %s" id (eve--format-time last))
			  changes))))))
	  (when (and start end (<= (- end start) tolerance))
	    (unless recorded
	      (eve--record-state)
	      (setq recorded t))
	    (let ((new-end (+ start (max tolerance 0.001))))
	      (setf (alist-get 'end segment) new-end)
	      (setq end new-end
		    changed t)
	      (push (format "%s: extended duration to %s" id (eve--format-time new-end))
		    changes)))
	  (when changed
	    (setf (alist-get 'start segment) start)
	    (setf (alist-get 'end segment) end)))))
    (when changes
      (eve--mark-dirty)
      (eve--render t))
    (nreverse changes)))

(defun eve--generate-marker-id ()
  "Generate a unique marker segment id."
  (let* ((ids (eve--collect-segment-ids))
	 (n 1)
	 (candidate (format "marker-%03d" n)))
    (while (member candidate ids)
      (setq n (1+ n)
	    candidate (format "marker-%03d" n)))
    candidate))

(defun eve-mode--ensure-json-library ()
  (unless (fboundp 'json-parse-buffer)
    (user-error "This mode requires native JSON parsing (Emacs 27+)")))

(define-minor-mode eve-hide-deleted-mode
  "Hide deleted words and segments when rendering.
This is a buffer-local mode for `eve-mode' buffers."
  :init-value nil
  :lighter nil
  (when (and (derived-mode-p 'eve-mode) eve--data)
    (eve--render t)))

(define-minor-mode eve-ruler-mode
  "Show a right-margin timestamp ruler in `eve-mode' buffers.
When active, a `[hh:mm:ss]' marker is placed in the right margin next
to the segment containing each time milestone in the rendered timeline.
The milestone interval is controlled by `eve-ruler-interval'."
  :init-value nil
  :lighter nil
  (if eve-ruler-mode
      (progn
        (when (and (derived-mode-p 'eve-mode) eve--data)
          (eve--update-ruler)))
    (eve--ruler-clear-overlays)
    (setq-local right-margin-width 0)
    (eve--apply-visual-wrap)))

(defvar eve-segment-panel-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map "w" #'eve-segment-panel-next-word)
    (define-key map "b" #'eve-segment-panel-previous-word)
    (define-key map "$" #'eve-segment-panel-last-word)
    (define-key map "v" #'eve-segment-panel-toggle-selection)
    (define-key map "j" #'eve-segment-panel-next-segment)
    (define-key map "n" #'eve-segment-panel-next-segment)
    (define-key map "k" #'eve-segment-panel-previous-segment)
    (define-key map "p" #'eve-segment-panel-paste-after)
    (define-key map "i" #'eve-segment-panel-edit-words)
    (define-key map "E" #'eve-segment-panel-edit-segment)
    (define-key map "r" #'eve-segment-panel-regenerate-transcript)
    (define-key map "U" #'eve-segment-panel-regenerate-until)
    (define-key map "~" #'eve-segment-panel-capitalize-words)
    (define-key map "." #'eve-segment-panel-add-dot)
    (define-key map "," #'eve-segment-panel-add-colon)
    (define-key map "d" #'eve-segment-panel-delete-words)
    (define-key map "D" #'eve-segment-panel-delete-segment)
    (define-key map "f" #'eve-segment-panel-mark-filler)
    (define-key map "A" #'eve-segment-panel-accept-filler)
    (define-key map "F" #'eve-segment-panel-delete-fillers)
    (define-key map "s" #'eve-segment-panel-split)
    (define-key map "|" #'eve-segment-panel-split)
    (define-key map "B" #'eve-segment-panel-edit-broll)
    (define-key map "C" #'eve-segment-panel-toggle-broll-continue)
    (define-key map "y" #'eve-segment-panel-copy)
    (define-key map "P" #'eve-segment-panel-paste-before)
    (define-key map "N" #'eve-segment-panel-edit-notes)
    (define-key map "T" #'eve-segment-panel-toggle-tag)
    (define-key map "I" #'eve-segment-panel-edit-speaker)
    (define-key map "x" #'eve-segment-panel-edit-range)
    (define-key map (kbd "g g") #'eve-segment-panel-first-segment)
    (define-key map "G" #'eve-segment-panel-last-segment)
    (define-key map "{" #'eve-segment-panel-previous-marker)
    (define-key map "}" #'eve-segment-panel-next-marker)
    (define-key map (kbd "TAB") #'eve-segment-panel-toggle-marker-fold)
    (define-key map (kbd "<backtab>") #'eve-segment-panel-toggle-all-marker-folds)
    (define-key map (kbd "S-TAB") #'eve-segment-panel-toggle-all-marker-folds)
    (define-key map "O" #'eve-segment-panel-insert-marker)
    (define-key map (kbd "SPC") #'eve-segment-panel-play-source)
    (define-key map "R" #'eve-segment-panel-play-rendered)
    (define-key map "c" #'eve-segment-panel-cache-current)
    (define-key map "m" #'eve-segment-panel-merge-next)
    (define-key map "M" #'eve-segment-panel-merge-previous)
    (define-key map "u" #'eve-segment-panel-undo)
    (define-key map (kbd "C-r") #'eve-segment-panel-redo)
    (define-key map (kbd "C-x C-s") #'eve-segment-panel-save)
    (define-key map (kbd "C-c C-c") #'eve-segment-panel-compile)
    (define-key map (kbd "C-c C-r") #'eve-segment-panel-play-rendered)
    (define-key map (kbd "C-c C-s") #'eve-segment-panel-play-source-continuous)
    (define-key map (kbd "C-c C-v") #'eve-segment-panel-validate)
    (define-key map "q" #'eve-segment-panel-focus-source)
    (define-key map "Q" #'eve-segment-panel-close)
    map)
  "Keymap for `eve-segment-panel-mode'.")

(defvar-local eve-segment-panel-source-buffer nil
  "Eve source buffer associated with the current segment panel.")

(defvar-local eve-segment-panel-segment-id nil
  "Segment id currently displayed by the segment panel.")

(defvar-local eve-segment-panel-word-index 0
  "Focused canonical word index in the current segment panel.")

(defvar-local eve-segment-panel-selection-anchor nil
  "Selection anchor word index in the current segment panel, or nil.")

(define-derived-mode eve-segment-panel-mode special-mode "Eve-Segment"
  "Mode for Eve's current-segment bottom panel."
  (setq-local truncate-lines nil)
  (setq-local word-wrap t))

(defun eve--context-panels-available-p ()
  "Return non-nil when optional context-panel support can be used."
  (setq eve--context-panels-available
        (or eve--context-panels-available
            (require 'context-panels nil t)))
  (and eve--context-panels-available
       (fboundp 'context-panels-register-provider)
       (fboundp 'context-panels-mode)
       (fboundp 'context-panels-open)))

(defun eve--segment-position (segment-id)
  "Return buffer position for SEGMENT-ID, or nil."
  (save-excursion
    (eve--goto-segment segment-id)))

(defun eve--segment-end-position (segment-id)
  "Return end buffer position for SEGMENT-ID, or nil."
  (cdr (eve--segment-bounds segment-id)))

(defun eve--segment-by-id (segment-id &optional segments)
  "Return segment SEGMENT-ID from SEGMENTS or current manifest."
  (seq-find (lambda (segment)
              (equal (eve--stringify (alist-get 'id segment))
                     (eve--stringify segment-id)))
            (or segments (eve--segments))))

(defun eve--segment-panel-source-buffer ()
  "Return the Eve source buffer associated with the current segment panel."
  (or eve-segment-panel-source-buffer
      (and (boundp 'context-panels-source-buffer)
           (buffer-live-p context-panels-source-buffer)
           context-panels-source-buffer)
      (user-error "No Eve source buffer for segment panel")))

(defun eve--segment-panel-key-label (label key)
  "Return LABEL with KEY highlighted when context-panels provides a helper."
  (if (fboundp 'context-panels-propertize-key)
      (context-panels-propertize-key label key)
    label))

(defun eve--segment-panel-action (label key)
  "Return action LABEL with KEY highlighted and action face applied."
  (eve--segment-panel-key-label
   (propertize label 'face 'eve-segment-panel-action-face)
   key))

(defun eve--segment-panel-action-with-key (label key)
  "Return action LABEL followed by parenthesized KEY.
The label uses `eve-segment-panel-action-face', parentheses use the muted label
face, and KEY uses the normal context-panel key-hint face."
  (concat (propertize label 'face 'eve-segment-panel-action-face)
          (propertize "(" 'face 'eve-segment-panel-label-face)
          (eve--segment-panel-key-label key key)
          (propertize ")" 'face 'eve-segment-panel-label-face)))

(defun eve--segment-panel-header-label (label)
  "Return muted header LABEL."
  (propertize label 'face 'eve-segment-panel-label-face))

(defun eve--segment-panel-header-value (value)
  "Return header VALUE using the segment-panel value face."
  (propertize (eve--stringify value) 'face 'eve-segment-panel-value-face))

(defun eve--segment-panel-segment-id (source-buffer)
  "Return segment id the panel should display for SOURCE-BUFFER."
  (if (eq (window-buffer (selected-window)) source-buffer)
      (with-current-buffer source-buffer
        (or (eve--segment-id-at-point)
            (alist-get 'id (car (eve--segments)))))
    (or eve-segment-panel-segment-id
        (with-current-buffer source-buffer
          (or (eve--segment-id-at-point)
              (alist-get 'id (car (eve--segments))))))))

(defun eve--segment-panel-visible-words (segment source-buffer)
  "Return visible words for SEGMENT as (INDEX WORD TEXT) using SOURCE-BUFFER state."
  (let ((hide-deleted (buffer-local-value 'eve-hide-deleted-mode source-buffer)))
    (cl-loop for word in (or (alist-get 'words segment) '())
             for index from 0
             unless (and hide-deleted (eve--edit-deleted-p word))
             collect (list index word (string-trim (eve--word-render-text word))))))

(defun eve--segment-panel-clamp-word-index (visible-words)
  "Clamp `eve-segment-panel-word-index' to VISIBLE-WORDS."
  (let ((indices (mapcar #'car visible-words)))
    (setq eve-segment-panel-word-index
          (cond
           ((null indices) 0)
           ((member eve-segment-panel-word-index indices)
            eve-segment-panel-word-index)
           ((seq-find (lambda (idx) (> idx eve-segment-panel-word-index)) indices))
           (t (car (last indices)))))))

(defun eve--segment-panel-selection-range ()
  "Return selected word range as (START . END), or nil."
  (when eve-segment-panel-selection-anchor
    (cons (min eve-segment-panel-selection-anchor eve-segment-panel-word-index)
          (max eve-segment-panel-selection-anchor eve-segment-panel-word-index))))

(defun eve--segment-panel-word-face (word index)
  "Return face for WORD at INDEX in the segment panel."
  (eve--merge-faces
   (when-let* ((range (eve--segment-panel-selection-range)))
     (when (and (<= (car range) index) (<= index (cdr range)))
       'eve-segment-panel-selection-face))
   (when (= index eve-segment-panel-word-index)
     'eve-segment-panel-current-word-face)
   (when (eve--word-filler-p word)
     'eve-filler-face)
   (when (eve--edit-deleted-p word)
     'eve-deleted-face)))

(defun eve--segment-panel-insert-words (visible-words)
  "Insert VISIBLE-WORDS into the current segment panel."
  (let ((first t))
    (dolist (entry visible-words)
      (pcase-let ((`(,index ,word ,text) entry))
        (unless first (insert " "))
        (setq first nil)
        (let ((start (point)))
          (insert text)
          (add-text-properties
           start (point)
           (list 'eve-segment-panel-word-index index
                 'face (eve--segment-panel-word-face word index))))))))

(defun eve--segment-panel-broll-summary (segment)
  "Return bottom-panel b-roll summary for SEGMENT."
  (if-let* ((broll (eve--segment-broll segment)))
      (eve--context-panel-broll-label broll)
    "none"))

(defun eve--segment-panel-cache-summary (source-buffer)
  "Return cheap cache/dirty summary for SOURCE-BUFFER."
  (with-current-buffer source-buffer
    (if (buffer-modified-p)
        "Modified"
      "Saved")))

(defun eve--segment-panel-render (source-buffer _view)
  "Render Eve current-segment bottom panel for SOURCE-BUFFER."
  (setq-local eve-segment-panel-source-buffer source-buffer)
  (let* ((segment-id (eve--segment-panel-segment-id source-buffer))
         (segment (with-current-buffer source-buffer
                    (eve--segment-by-id segment-id)))
         (inhibit-read-only t))
    (unless segment
      (setq segment (with-current-buffer source-buffer (car (eve--segments)))
            segment-id (alist-get 'id segment)))
    (setq-local eve-segment-panel-segment-id (eve--stringify segment-id))
    (let* ((visible-words (and segment
                               (eve--segment-panel-visible-words segment source-buffer)))
           (start (alist-get 'start segment))
           (end (alist-get 'end segment))
           (duration (max 0.0 (- (or end 0.0) (or start 0.0))))
           (speaker (or (eve--aget 'speaker segment) "—"))
           (tags (or (eve--segment-tags segment) '())))
      (eve--segment-panel-clamp-word-index visible-words)
      (insert (propertize eve-segment-panel-segment-id 'face 'eve-heading-face)
              "  " (eve--segment-panel-header-value (format "%.1fs" duration))
              "  " (eve--segment-panel-header-label "Time: ")
              (eve--segment-panel-header-value
               (format "%s–%s" (eve--format-time start) (eve--format-time end)))
              "  " (eve--segment-panel-header-label "Speaker: ")
              (eve--segment-panel-header-value speaker)
              "  " (eve--segment-panel-header-label "Tags: ")
              (eve--segment-panel-header-value
               (if tags (string-join (mapcar #'eve--stringify tags) ",") "—"))
              "  " (eve--segment-panel-header-value
                     (eve--segment-panel-cache-summary source-buffer))
              "\n\n")
      (insert (eve--segment-panel-key-label "Transcript" "T") "  ")
      (eve--segment-panel-insert-words visible-words)
      (insert "\n\n  "
              (string-join
               (list (eve--segment-panel-action-with-key "edit" "i")
                     (eve--segment-panel-action "sEgment" "E")
                     (eve--segment-panel-action-with-key "regen" "r")
                     (eve--segment-panel-action-with-key "until" "U")
                     (eve--segment-panel-action-with-key "capitalize" "~")
                     (eve--segment-panel-action-with-key "dot" ".")
                     (eve--segment-panel-action-with-key "colon" ",")
                     (eve--segment-panel-action "delete" "d")
                     (eve--segment-panel-action-with-key "segment" "D")
                     (eve--segment-panel-action "filler" "f")
                     (eve--segment-panel-action-with-key "non-filler" "A")
                     (eve--segment-panel-action "Fillers" "F")
                     (eve--segment-panel-action-with-key "split" "|"))
               "  ")
              "\n\n")
      (insert (eve--segment-panel-key-label "B-roll" "B")
              ": " (propertize (eve--segment-panel-broll-summary segment)
                                'face 'eve-segment-panel-broll-value-face)
              "\n  "
              (string-join
               (list (eve--segment-panel-action-with-key "edit" "B")
                     (eve--segment-panel-action-with-key "continue" "C")
                     (eve--segment-panel-action-with-key "preview" "P"))
               "  ")
              "\n\n")
      (insert (eve--segment-panel-header-label "Other")
              "  "
              (string-join
               (list (eve--segment-panel-action "Notes" "N")
                     (eve--segment-panel-action "Tags" "T")
                     (eve--segment-panel-action-with-key "speaker" "I")
                     (eve--segment-panel-action-with-key "range" "x")
                     (eve--segment-panel-action-with-key "first" "gg")
                     (eve--segment-panel-action-with-key "last" "G")
                     (eve--segment-panel-action-with-key "marker" "O")
                     (eve--segment-panel-action-with-key "Source" "SPC")
                     (eve--segment-panel-action "Rendered" "R")
                     (eve--segment-panel-action "merge" "m")
                     (eve--segment-panel-action-with-key "prev" "M")
                     (eve--segment-panel-action "undo" "u")
                     (eve--segment-panel-action-with-key "save" "C-x C-s")
                     (eve--segment-panel-action "cache" "c"))
               "  ")
              "\n")
      (when-let* ((pos (text-property-any (point-min) (point-max)
                                          'eve-segment-panel-word-index
                                          eve-segment-panel-word-index)))
        (goto-char pos)))))

(defun eve--segment-panel-refresh ()
  "Refresh the current Eve segment panel without replacing panel-local state."
  (let ((source (eve--segment-panel-source-buffer))
        (inhibit-read-only t))
    (erase-buffer)
    (eve--segment-panel-render source nil)))

(defun eve--segment-panel-move-word (delta)
  "Move focused panel word by DELTA visible words."
  (let* ((source (eve--segment-panel-source-buffer))
         (segment-id eve-segment-panel-segment-id)
         (segment (with-current-buffer source
                    (eve--segment-by-id segment-id)))
         (visible (eve--segment-panel-visible-words segment source))
         (indices (mapcar #'car visible))
         (pos (or (cl-position eve-segment-panel-word-index indices) 0))
         (target (nth (min (max 0 (+ pos delta)) (max 0 (1- (length indices))))
                      indices)))
    (when target
      (setq eve-segment-panel-word-index target)
      (eve--segment-panel-refresh))))

(defun eve-segment-panel-next-word ()
  "Move to the next visible word in the segment panel."
  (interactive)
  (eve--segment-panel-move-word 1))

(defun eve-segment-panel-previous-word ()
  "Move to the previous visible word in the segment panel."
  (interactive)
  (eve--segment-panel-move-word -1))

(defun eve-segment-panel-last-word ()
  "Move focus to the last visible word in the segment panel."
  (interactive)
  (let* ((source (eve--segment-panel-source-buffer))
         (segment-id eve-segment-panel-segment-id)
         (segment (with-current-buffer source
                    (eve--segment-by-id segment-id)))
         (visible (eve--segment-panel-visible-words segment source)))
    (if-let* ((last-index (caar (last visible))))
        (progn
          (setq eve-segment-panel-word-index last-index)
          (setq eve-segment-panel-selection-anchor nil)
          (eve--segment-panel-refresh)
          (message "Focused last word"))
      (user-error "No visible words in segment"))))

(defun eve-segment-panel-toggle-selection ()
  "Toggle the segment panel word-selection anchor."
  (interactive)
  (setq eve-segment-panel-selection-anchor
        (unless eve-segment-panel-selection-anchor
          eve-segment-panel-word-index))
  (eve--segment-panel-refresh))

(defun eve--segment-panel-goto-segment-id (target-id)
  "Move the segment panel and source buffer to TARGET-ID."
  (let ((panel (current-buffer))
        (source (eve--segment-panel-source-buffer)))
    (when target-id
      (with-current-buffer source
        (eve--goto-segment target-id)
        (eve--update-focus-overlay)
        (when-let* ((window (get-buffer-window source t)))
          (set-window-point window (point))))
      (with-current-buffer panel
        (setq eve-segment-panel-segment-id (eve--stringify target-id))
        (setq eve-segment-panel-word-index 0)
        (setq eve-segment-panel-selection-anchor nil)
        (eve--segment-panel-refresh)))))

(defun eve--segment-panel-move-segment (delta)
  "Move the segment panel by DELTA visible segments, keeping panel focus.
Navigation is based on manifest order instead of rendered source positions so it
still works when the current segment has no visible words."
  (let* ((source (eve--segment-panel-source-buffer))
         (segment-id eve-segment-panel-segment-id)
         (target-id
          (with-current-buffer source
            (eve--segment-navigation-target-id
             (eve--segments) segment-id delta eve-hide-deleted-mode))))
    (if target-id
        (eve--segment-panel-goto-segment-id target-id)
      (message (if (> delta 0)
                   "Already at last segment"
                 "Already at first segment")))))

(defun eve--segment-panel-move-marker (delta)
  "Move the segment panel by DELTA marker segments, keeping panel focus."
  (let* ((source (eve--segment-panel-source-buffer))
         (segment-id eve-segment-panel-segment-id)
         (target-id
          (with-current-buffer source
            (eve--marker-navigation-target-id
             (eve--segments) segment-id delta eve-hide-deleted-mode))))
    (if target-id
        (eve--segment-panel-goto-segment-id target-id)
      (message (if (> delta 0)
                   "Already at last marker"
                 "Already at first marker")))))

(defun eve-segment-panel-next-marker ()
  "Move the segment panel to the next marker."
  (interactive)
  (eve--segment-panel-move-marker 1))

(defun eve-segment-panel-previous-marker ()
  "Move the segment panel to the previous marker."
  (interactive)
  (eve--segment-panel-move-marker -1))

(defun eve-segment-panel-toggle-marker-fold ()
  "Toggle source-buffer folding for the current panel marker."
  (interactive)
  (eve--segment-panel-run-source-command #'eve-toggle-marker-fold))

(defun eve-segment-panel-toggle-all-marker-folds ()
  "Toggle source-buffer folding for all markers from the segment panel."
  (interactive)
  (let ((source (eve--segment-panel-source-buffer)))
    (with-current-buffer source
      (call-interactively #'eve-toggle-all-marker-folds))))

(defun eve-segment-panel-next-segment ()
  "Move the segment panel to the next segment."
  (interactive)
  (eve--segment-panel-move-segment 1))

(defun eve-segment-panel-previous-segment ()
  "Move the segment panel to the previous segment."
  (interactive)
  (eve--segment-panel-move-segment -1))

(defun eve-segment-panel-first-segment ()
  "Move the segment panel to the first visible segment or marker."
  (interactive)
  (let ((source (eve--segment-panel-source-buffer)))
    (eve--segment-panel-goto-segment-id
     (with-current-buffer source
       (alist-get 'id
                  (seq-find (lambda (segment)
                              (eve--navigable-segment-p segment eve-hide-deleted-mode))
                            (eve--segments)))))))

(defun eve-segment-panel-last-segment ()
  "Move the segment panel to the last visible segment or marker."
  (interactive)
  (let ((source (eve--segment-panel-source-buffer)))
    (eve--segment-panel-goto-segment-id
     (with-current-buffer source
       (alist-get 'id
                  (seq-find (lambda (segment)
                              (eve--navigable-segment-p segment eve-hide-deleted-mode))
                            (reverse (eve--segments))))))))

(defun eve-segment-panel-focus-source ()
  "Return focus to the Eve source buffer."
  (interactive)
  (pop-to-buffer (eve--segment-panel-source-buffer)))

(defun eve-segment-panel-close ()
  "Close the Eve segment panel."
  (interactive)
  (if (fboundp 'context-panels-close-bottom-view)
      (context-panels-close-bottom-view)
    (quit-window t)))

(defun eve--segment-panel-segment ()
  "Return the current panel segment."
  (let ((source (eve--segment-panel-source-buffer))
        (segment-id eve-segment-panel-segment-id))
    (or (with-current-buffer source
          (eve--segment-by-id segment-id))
        (user-error "No current segment"))))

(defun eve--segment-panel-word-index-at-point ()
  "Return the panel word index under point, or nil."
  (or (get-text-property (point) 'eve-segment-panel-word-index)
      (when (> (point) (point-min))
        (get-text-property (1- (point)) 'eve-segment-panel-word-index))))

(defun eve--segment-panel-word-range ()
  "Return active word range as (START . END).
When point is on a rendered word, make that word the panel focus before
computing the range so action keys operate on the visibly focused word."
  (when-let* ((index (eve--segment-panel-word-index-at-point)))
    (setq eve-segment-panel-word-index index))
  (or (eve--segment-panel-selection-range)
      (cons eve-segment-panel-word-index eve-segment-panel-word-index)))

(defun eve--segment-panel-visible-range-after-change (segment preferred)
  "Return nearest visible word index in SEGMENT around PREFERRED."
  (let* ((source (eve--segment-panel-source-buffer))
         (visible (eve--segment-panel-visible-words segment source))
         (indices (mapcar #'car visible)))
    (cond
     ((null indices) 0)
     ((member preferred indices) preferred)
     ((seq-find (lambda (idx) (> idx preferred)) indices))
     (t (car (last indices))))))

(defun eve--segment-panel-record-state ()
  "Record undo state in the source buffer for a segment-panel edit."
  (with-current-buffer (eve--segment-panel-source-buffer)
    (eve--record-state)))

(defun eve--segment-panel-after-edit (&optional preferred-index)
  "Mark source dirty, render it, and refresh the segment panel."
  (let* ((source (eve--segment-panel-source-buffer))
         (segment-id eve-segment-panel-segment-id)
         (preferred (or preferred-index eve-segment-panel-word-index)))
    (with-current-buffer source
      (eve--mark-dirty)
      (eve--render t)
      (eve--goto-segment segment-id))
    (let ((segment (eve--segment-panel-segment)))
      (setq eve-segment-panel-word-index
            (eve--segment-panel-visible-range-after-change segment preferred)))
    (setq eve-segment-panel-selection-anchor nil)
    (eve--segment-panel-refresh)))

(defun eve--segment-panel-retokenize (text)
  "Return non-empty whitespace tokens from TEXT."
  (mapcar #'substring-no-properties
          (split-string (string-trim text) "[[:space:]]+" t)))

(defun eve--segment-panel-copy-word-with-token (word token)
  "Return copy of WORD with visible TOKEN updated."
  (let ((copy (copy-tree word t)))
    (if (alist-get 'spoken copy)
        (setf (alist-get 'spoken copy) token)
      (setf (alist-get 'token copy) token))
    copy))

(defun eve--segment-panel-word-start (word)
  "Return WORD start time."
  (or (eve--coerce-number (alist-get 'start word))
      (eve--coerce-number (alist-get 'start_tick word))
      0.0))

(defun eve--segment-panel-word-end (word)
  "Return WORD end time."
  (or (eve--coerce-number (alist-get 'end word))
      (eve--coerce-number (alist-get 'end_tick word))
      (eve--segment-panel-word-start word)))

(defun eve--segment-panel-replacement-word (token start end)
  "Return a new replacement word TOKEN from START to END."
  (list (cons 'start start)
        (cons 'end end)
        (cons 'token token)))

(defun eve--segment-panel-rephrase-range (segment start-index end-index replacement)
  "Replace SEGMENT words from START-INDEX to END-INDEX with REPLACEMENT text."
  (let* ((tokens (eve--segment-panel-retokenize replacement))
         (words (copy-sequence (alist-get 'words segment)))
         (range-len (1+ (- end-index start-index))))
    (when (null tokens)
      (user-error "Replacement cannot be empty; use delete instead"))
    (unless (and (<= 0 start-index) (< end-index (length words)))
      (user-error "Invalid word range"))
    (let* ((before (cl-subseq words 0 start-index))
           (after (nthcdr (1+ end-index) words))
           (old-range (cl-subseq words start-index (1+ end-index)))
           (new-range
            (if (= (length tokens) range-len)
                (cl-mapcar #'eve--segment-panel-copy-word-with-token old-range tokens)
              (let* ((span-start (eve--segment-panel-word-start (car old-range)))
                     (span-end (eve--segment-panel-word-end (car (last old-range))))
                     (step (/ (max 0.0 (- span-end span-start))
                              (float (length tokens))))
                     (idx 0)
                     result)
                (dolist (token tokens (nreverse result))
                  (let ((start (+ span-start (* idx step)))
                        (end (+ span-start (* (1+ idx) step))))
                    (push (eve--segment-panel-replacement-word token start end)
                          result)
                    (setq idx (1+ idx)))))))
           (merged (append before new-range after)))
      (setf (alist-get 'words segment) merged)
      (setf (alist-get 'text segment)
            (string-join (mapcar #'eve--word-render-text merged) " "))
      (setf (alist-get 'start segment)
            (eve--segment-panel-word-start (car merged)))
      (setf (alist-get 'end segment)
            (eve--segment-panel-word-end (car (last merged))))
      start-index)))

(defun eve-segment-panel-edit-words ()
  "Rephrase the focused word or selected range in the panel."
  (interactive)
  (let* ((segment (eve--segment-panel-segment))
         (range (eve--segment-panel-word-range))
         (words (alist-get 'words segment))
         (current (string-join
                   (mapcar #'eve--word-render-text
                           (cl-subseq words (car range) (1+ (cdr range))))
                   " "))
         (replacement (read-string "Rephrase words: " current)))
    (eve--segment-panel-record-state)
    (eve--segment-panel-rephrase-range segment (car range) (cdr range) replacement)
    (eve--segment-panel-after-edit (car range))
    (message "Rephrased '%s' → '%s'" current replacement)))

(defun eve-segment-panel-edit-segment ()
  "Rephrase the whole current segment in the panel."
  (interactive)
  (let* ((segment (eve--segment-panel-segment))
         (words (alist-get 'words segment))
         (current (string-join (mapcar #'eve--word-render-text words) " "))
         (replacement (read-string "Rephrase segment: " current)))
    (eve--segment-panel-record-state)
    (eve--segment-panel-rephrase-range segment 0 (1- (length words)) replacement)
    (eve--segment-panel-after-edit 0)
    (message "Rephrased segment '%s' → '%s'" current replacement)))

(defun eve--segment-source-file (segment)
  "Return absolute source media file for SEGMENT."
  (let* ((source-id (alist-get 'source segment))
         (source (and source-id (eve--source-by-id source-id)))
         (file (and source (alist-get 'file source))))
    (unless source
      (user-error "Unknown source id: %s" source-id))
    (let ((abs-file (expand-file-name file (if buffer-file-name
                                               (file-name-directory buffer-file-name)
                                             default-directory))))
      (unless (file-exists-p abs-file)
        (user-error "Source file not found: %s" abs-file))
      abs-file)))

(defun eve--segment-panel-transcribe-command (media output backend model)
  "Return `eve transcribe' command for MEDIA to OUTPUT using BACKEND and MODEL."
  (let ((program (or (eve--resolve-cli)
                     (user-error "Cannot find eve CLI executable: %s"
                                 eve-cli-program))))
    (append (list program "transcribe" media "--output" output
                  "--backend" backend "--model" model)
            (when eve-transcribe-verbatim
              '("--verbatim"))
            (when eve-transcribe-hotwords
              (list "--hotwords" eve-transcribe-hotwords))
            (when eve-transcribe-tag-fillers
              '("--tag-fillers")))))

(defun eve--segment-panel-run-command (command buffer-name)
  "Run COMMAND synchronously, writing output to BUFFER-NAME."
  (let ((buffer (get-buffer-create buffer-name)))
    (with-current-buffer buffer
      (let ((inhibit-read-only t))
        (erase-buffer)))
    (let ((exit-code (apply #'call-process (car command) nil buffer nil (cdr command))))
      (unless (and (integerp exit-code) (= exit-code 0))
        (pop-to-buffer buffer)
        (user-error "Command failed: %s" (string-join command " "))))))

(defun eve--segment-panel-offset-word-times (word offset)
  "Return WORD copy with start/end offset by OFFSET seconds."
  (let ((copy (copy-tree word t)))
    (dolist (key '(start end))
      (when-let* ((value (eve--coerce-number (alist-get key copy))))
        (setf (alist-get key copy) (+ offset value))))
    copy))

(defun eve--segment-panel-transcribed-words (segment offset)
  "Return transcribed words from SEGMENT offset by OFFSET seconds."
  (mapcar (lambda (word)
            (eve--segment-panel-offset-word-times word offset))
          (alist-get 'words segment)))

(defun eve--segment-panel-generated-segment (original transcribed id offset)
  "Return replacement for ORIGINAL from TRANSCRIBED with ID offset by OFFSET."
  (let* ((copy (copy-tree original t))
         (words (eve--segment-panel-transcribed-words transcribed offset)))
    (unless words
      (user-error "Regenerated transcript contained a segment without word timings"))
    (setf (alist-get 'id copy) id)
    (setf (alist-get 'source copy) (alist-get 'source original))
    (setf (alist-get 'words copy) words)
    (setf (alist-get 'text copy)
          (or (let ((text (string-trim (eve--stringify (alist-get 'text transcribed)))))
                (unless (string-empty-p text) text))
              (string-join (mapcar #'eve--word-render-text words) " ")))
    (setf (alist-get 'start copy)
          (or (eve--coerce-number (alist-get 'start (car words)))
              (alist-get 'start original)))
    (setf (alist-get 'end copy)
          (or (eve--coerce-number (alist-get 'end (car (last words))))
              (alist-get 'end original)))
    (when-let* ((edit (alist-get 'edit copy)))
      (setf (alist-get 'deleted edit) nil))
    copy))

(defun eve--segment-panel-regenerated-segments (original manifest &optional removed-ids)
  "Return replacement segments for ORIGINAL from transcribed MANIFEST.
REMOVED-IDS are ignored when generating replacement ids because those segments
will be replaced by the regenerated transcript."
  (let* ((transcribed (seq-filter
                       (lambda (segment) (alist-get 'words segment))
                       (alist-get 'segments manifest)))
         (offset (or (eve--coerce-number (alist-get 'start original)) 0.0))
         (base-id (eve--stringify (alist-get 'id original)))
         (ignored-ids (cons base-id removed-ids))
         (existing-ids (seq-remove (lambda (id) (member id ignored-ids))
                                   (eve--segment-id-list)))
         result)
    (unless transcribed
      (user-error "Regenerated transcript contained no word timings"))
    (cl-loop for segment in transcribed
             for index from 0
             for requested-id = (if (= index 0)
                                    base-id
                                  (format "%s-regen-%d" base-id index))
             for id = (eve--unique-id requested-id existing-ids)
             do (push id existing-ids)
             collect (eve--segment-panel-generated-segment
                      original segment id offset))))

(defun eve--segment-panel-apply-regenerated-transcript (segment manifest)
  "Replace SEGMENT with one or more segments from transcribed MANIFEST."
  (eve--apply-regenerated-segment-range segment segment manifest))

(defun eve--apply-regenerated-segment-range (start-segment end-segment manifest)
  "Replace START-SEGMENT through END-SEGMENT with transcribed MANIFEST."
  (let ((source-buffer (eve--segment-panel-source-buffer)))
    (with-current-buffer source-buffer
      (let* ((segments (eve--segments))
             (start-index (cl-position start-segment segments :test #'eq))
             (end-index (cl-position end-segment segments :test #'eq)))
        (unless (and start-index end-index (<= start-index end-index))
          (user-error "Segment range no longer exists"))
        (let* ((removed (cl-subseq segments start-index (1+ end-index)))
               (removed-ids (mapcar (lambda (segment)
                                      (eve--stringify (alist-get 'id segment)))
                                    removed))
               (replacements
                (eve--segment-panel-regenerated-segments
                 start-segment manifest removed-ids)))
          (setf (alist-get 'segments eve--data)
                (append (cl-subseq segments 0 start-index)
                        replacements
                        (nthcdr (1+ end-index) segments))))))))

(defun eve--regenerate-segment-range (start-segment end-segment backend model start-time)
  "Regenerate START-SEGMENT through END-SEGMENT using BACKEND and MODEL.
START-TIME is used for minibuffer elapsed-time feedback."
  (let* ((source-buffer (eve--segment-panel-source-buffer))
         (source-file (with-current-buffer source-buffer
                        (eve--segment-source-file start-segment)))
         (start-source (alist-get 'source start-segment))
         (end-source (alist-get 'source end-segment))
         (_ (unless (equal start-source end-source)
              (user-error "Cannot regenerate across different source clips")))
         (start (or (eve--coerce-number (alist-get 'start start-segment))
                    (user-error "Start segment has no start time")))
         (end (or (eve--coerce-number (alist-get 'end end-segment))
                  (user-error "End segment has no end time")))
         (_ (when (<= end start)
              (user-error "Invalid regeneration range")))
         (start-id (eve--stringify (alist-get 'id start-segment)))
         (end-id (eve--stringify (alist-get 'id end-segment)))
         (range-label (if (equal start-id end-id)
                          start-id
                        (format "%s–%s" start-id end-id)))
         (wav-file (make-temp-file "eve-segment-" nil ".wav"))
         (json-file (make-temp-file "eve-segment-" nil ".tjm.json"))
         (ffmpeg (or (executable-find "ffmpeg")
                     (user-error "Cannot find ffmpeg executable"))))
    (unwind-protect
        (progn
          (eve--progress-message
           "Regenerate"
           (format "%s: extracting %.1fs segment audio"
                   range-label (max 0.0 (- end start)))
           start-time)
          (eve--segment-panel-run-command
           (list ffmpeg "-y" "-ss" (number-to-string start)
                 "-to" (number-to-string end) "-i" source-file
                 "-vn" "-ac" "1" "-ar" "16000" wav-file)
           "*eve-segment-regenerate*")
          (eve--progress-message
           "Regenerate"
           (format "%s: transcribing with %s/%s" range-label backend model)
           start-time)
          (eve--segment-panel-run-command
           (with-current-buffer source-buffer
             (eve--segment-panel-transcribe-command wav-file json-file backend model))
           "*eve-segment-regenerate*")
          (eve--progress-message
           "Regenerate" (format "%s: applying transcript" range-label) start-time)
          (eve--apply-regenerated-segment-range
           start-segment end-segment
           (json-parse-string
            (with-temp-buffer
              (insert-file-contents json-file)
              (buffer-string))
            :object-type 'alist :array-type 'list
            :null-object nil :false-object nil)))
      (ignore-errors (delete-file wav-file))
      (ignore-errors (delete-file json-file)))))

(defun eve--segment-panel-regenerate-transcript (segment backend model start-time)
  "Regenerate SEGMENT transcript using BACKEND and MODEL.
START-TIME is used for minibuffer elapsed-time feedback."
  (eve--regenerate-segment-range segment segment backend model start-time))

(defun eve-segment-panel-regenerate-transcript (backend model)
  "Regenerate the current segment transcript with BACKEND and MODEL."
  (interactive
   (list (read-string "Backend: " eve-transcribe-backend)
         (read-string "Model: " eve-transcribe-model)))
  (let ((segment (eve--segment-panel-segment))
        (start-time (float-time)))
    (eve--segment-panel-record-state)
    (eve--segment-panel-regenerate-transcript segment backend model start-time)
    (eve--segment-panel-after-edit 0)
    (eve--progress-message
     "Regenerate"
     (format "%s: done with %s/%s"
             (eve--stringify (alist-get 'id segment)) backend model)
     start-time)))

(defun eve--segment-panel-next-segment-id ()
  "Return the next navigable non-marker segment id after the current panel segment."
  (let ((source (eve--segment-panel-source-buffer))
        (current-id eve-segment-panel-segment-id))
    (with-current-buffer source
      (let* ((segments (eve--segments))
             (count (length segments))
             (index (or (eve--segment-index current-id segments) -1))
             target-id)
        (while (and (null target-id)
                    (setq index (1+ index))
                    (< index count))
          (let ((segment (nth index segments)))
            (when (and (not (eve--marker-p segment))
                       (eve--navigable-segment-p segment eve-hide-deleted-mode))
              (setq target-id (alist-get 'id segment)))))
        target-id))))

(defun eve-segment-panel-regenerate-until (target-id backend model)
  "Regenerate from the current segment through TARGET-ID, inclusive."
  (interactive
   (let* ((default (eve--segment-panel-next-segment-id))
          (target (read-string "Regenerate until segment id: " default)))
     (list target
           (read-string "Backend: " eve-transcribe-backend)
           (read-string "Model: " eve-transcribe-model))))
  (let* ((source (eve--segment-panel-source-buffer))
         (start-segment (eve--segment-panel-segment))
         (end-segment (with-current-buffer source
                        (or (eve--segment-by-id target-id)
                            (user-error "Unknown target segment: %s" target-id))))
         (start-time (float-time)))
    (when (eve--marker-p end-segment)
      (user-error "Cannot regenerate until marker segment"))
    (eve--segment-panel-record-state)
    (eve--regenerate-segment-range start-segment end-segment backend model start-time)
    (eve--segment-panel-after-edit 0)
    (eve--progress-message
     "Regenerate"
     (format "%s–%s: done with %s/%s"
             (eve--stringify (alist-get 'id start-segment))
             (eve--stringify target-id)
             backend model)
     start-time)))

(defun eve--segment-panel-words-in-range (segment range)
  "Return words in SEGMENT RANGE."
  (cl-subseq (alist-get 'words segment) (car range) (1+ (cdr range))))

(defun eve--segment-panel-words-label (words)
  "Return a compact display label for WORDS."
  (string-join (mapcar #'eve--word-render-text words) " "))

(defun eve--segment-panel-set-word-token (word token)
  "Set WORD's visible TOKEN in place."
  (if (alist-get 'spoken word)
      (setf (alist-get 'spoken word) token)
    (setf (alist-get 'token word) token)))

(defun eve--capitalize-token (token)
  "Return TOKEN with its first word character capitalized."
  (if (string-match "[[:alpha:]]" token)
      (replace-match (upcase (match-string 0 token)) t t token)
    token))

(defun eve--segment-panel-refresh-segment-text (segment)
  "Refresh SEGMENT text from its rendered words."
  (setf (alist-get 'text segment)
        (string-join (mapcar #'eve--word-render-text (alist-get 'words segment)) " ")))

(defun eve-segment-panel-capitalize-words ()
  "Capitalize the focused word or selected range in the panel."
  (interactive)
  (let* ((segment (eve--segment-panel-segment))
         (range (eve--segment-panel-word-range))
         (words (eve--segment-panel-words-in-range segment range))
         (before (eve--segment-panel-words-label words)))
    (eve--segment-panel-record-state)
    (dolist (word words)
      (eve--segment-panel-set-word-token
       word (eve--capitalize-token (eve--word-render-text word))))
    (eve--segment-panel-refresh-segment-text segment)
    (eve--segment-panel-after-edit (car range))
    (message "Capitalized '%s'" before)))

(defun eve--segment-panel-add-suffix (suffix label)
  "Add SUFFIX to the focused word or selected range and report LABEL."
  (let* ((segment (eve--segment-panel-segment))
         (range (eve--segment-panel-word-range))
         (words (eve--segment-panel-words-in-range segment range))
         (before (eve--segment-panel-words-label words)))
    (eve--segment-panel-record-state)
    (dolist (word words)
      (eve--segment-panel-set-word-token
       word (concat (eve--word-render-text word) suffix)))
    (eve--segment-panel-refresh-segment-text segment)
    (eve--segment-panel-after-edit (car range))
    (message "Added %s to '%s'" label before)))

(defun eve-segment-panel-add-dot ()
  "Add a dot at the end of the focused word or selected range."
  (interactive)
  (eve--segment-panel-add-suffix "." "dot"))

(defun eve-segment-panel-add-colon ()
  "Add a colon at the end of the focused word or selected range."
  (interactive)
  (eve--segment-panel-add-suffix ":" "colon"))

(defun eve-segment-panel-add-comma ()
  "Add a comma at the end of the focused word or selected range."
  (interactive)
  (eve--segment-panel-add-suffix "," "comma"))

(defun eve--segment-all-words-deleted-p (segment)
  "Return non-nil when SEGMENT has words and every word is deleted."
  (let ((words (alist-get 'words segment)))
    (and words (seq-every-p #'eve--edit-deleted-p words))))

(defun eve-segment-panel-delete-words ()
  "Delete the focused word or selected range in the panel.
When this deletes every word in the segment, delete the whole segment instead
of leaving an empty visible-word state behind."
  (interactive)
  (let* ((segment (eve--segment-panel-segment))
         (range (eve--segment-panel-word-range))
         (words (eve--segment-panel-words-in-range segment range))
         (label (eve--segment-panel-words-label words)))
    (eve--segment-panel-record-state)
    (dolist (word words)
      (eve--set-edit-deleted word t))
    (let ((segment-deleted (eve--segment-all-words-deleted-p segment)))
      (when segment-deleted
        (eve--set-edit-deleted segment t))
      (eve--segment-panel-after-edit (car range))
      (if segment-deleted
          (message "Deleted segment")
        (message "Deleted '%s'" label)))))

(defun eve-segment-panel-delete-segment ()
  "Toggle deletion of the current segment in the panel."
  (interactive)
  (let* ((segment (eve--segment-panel-segment))
         (segment-id (eve--stringify (alist-get 'id segment))))
    (when (or (eve--edit-deleted-p segment)
              (yes-or-no-p (format "Delete segment %s? " segment-id)))
      (eve--segment-panel-record-state)
      (let ((deleted (eve--toggle-edit-deleted segment)))
        (eve--segment-panel-after-edit eve-segment-panel-word-index)
        (message "Segment %s" (if deleted "deleted" "restored"))))))

(defun eve--customize-save-variable-preserving-windows (symbol value)
  "Save SYMBOL as VALUE without letting Customize alter visible windows."
  (save-window-excursion
    (customize-save-variable symbol value)))

(defun eve--segment-panel-learn-filler-phrase (phrase)
  "Prompt to add PHRASE to `eve-filler-phrases'."
  (unless (member phrase eve-filler-phrases)
    (when (y-or-n-p (format "Add filler phrase '%s' for future sessions? " phrase))
      (setq eve-filler-phrases (append eve-filler-phrases (list phrase)))
      (eve--customize-save-variable-preserving-windows
       'eve-filler-phrases eve-filler-phrases))))

(defun eve-segment-panel-mark-filler ()
  "Mark focused word/range as filler and optionally learn the phrase."
  (interactive)
  (let* ((segment (eve--segment-panel-segment))
         (range (eve--segment-panel-word-range))
         (words (eve--segment-panel-words-in-range segment range))
         (phrase (eve--segment-panel-words-label words)))
    (eve--segment-panel-record-state)
    (dolist (word words)
      (eve--mark-word-filler word))
    (eve--segment-panel-learn-filler-phrase phrase)
    (eve--segment-panel-after-edit (car range))
    (message "Marked filler '%s'" phrase)))

(defun eve-segment-panel-accept-filler ()
  "Mark focused filler word/range as explicitly not filler."
  (interactive)
  (let* ((segment (eve--segment-panel-segment))
         (range (eve--segment-panel-word-range))
         (words (eve--segment-panel-words-in-range segment range))
         (label (eve--segment-panel-words-label words))
         (count 0))
    (eve--segment-panel-record-state)
    (dolist (word words)
      (when (eve--word-filler-p word)
        (setq count (1+ count))
        (eve--mark-word-non-filler word)))
    (eve--segment-panel-after-edit (car range))
    (if (> count 0)
        (message "Marked non-filler '%s'" label)
      (message "'%s' was not marked as filler" label))))

(defun eve-segment-panel-delete-fillers ()
  "Delete fillers in the current segment only."
  (interactive)
  (let ((segment (eve--segment-panel-segment))
        (count 0))
    (eve--segment-panel-record-state)
    (dolist (word (alist-get 'words segment))
      (when (eve--word-filler-p word)
        (setq count (1+ count))
        (eve--set-edit-deleted word t)))
    (eve--segment-panel-after-edit eve-segment-panel-word-index)
    (message "Deleted %d filler word%s in current segment"
             count (if (= count 1) "" "s"))))

(defun eve--segment-panel-run-source-command (function &rest args)
  "Run source-buffer FUNCTION with ARGS on the current panel segment, then refresh."
  (let* ((panel (current-buffer))
         (source (eve--segment-panel-source-buffer))
         (segment-id eve-segment-panel-segment-id))
    (with-current-buffer source
      (eve--goto-segment segment-id)
      (apply function args))
    (when (buffer-live-p panel)
      (with-current-buffer panel
        (eve--segment-panel-refresh)))))

(defun eve-segment-panel-split ()
  "Split current segment at focused word."
  (interactive)
  (let* ((source (eve--segment-panel-source-buffer))
         (segment-id eve-segment-panel-segment-id)
         (word-index eve-segment-panel-word-index)
         (panel (current-buffer)))
    (with-current-buffer source
      (eve--goto-word-by-index segment-id word-index)
      (call-interactively #'eve-split-segment))
    (with-current-buffer panel
      (setq eve-segment-panel-segment-id
            (with-current-buffer source (eve--segment-id-at-point)))
      (setq eve-segment-panel-word-index 0)
      (setq eve-segment-panel-selection-anchor nil)
      (eve--segment-panel-refresh))))

(defun eve-segment-panel-edit-broll ()
  "Edit b-roll for the current panel segment."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-edit-broll))

(defun eve-segment-panel-toggle-broll-continue ()
  "Toggle b-roll continue for the current panel segment."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-toggle-broll-continue))

(defun eve-segment-panel-preview ()
  "Preview current panel segment."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-play-segment))

(defun eve--segment-panel-run-source-returning-segment (function &rest args)
  "Run source-buffer FUNCTION with ARGS and focus the returned segment id."
  (let* ((panel (current-buffer))
         (source (eve--segment-panel-source-buffer))
         (segment-id eve-segment-panel-segment-id)
         pasted-id)
    (with-current-buffer source
      (eve--goto-segment segment-id)
      (setq pasted-id (apply function args)))
    (when (buffer-live-p panel)
      (with-current-buffer panel
        (when pasted-id
          (setq eve-segment-panel-segment-id pasted-id)
          (setq eve-segment-panel-word-index 0))
        (setq eve-segment-panel-selection-anchor nil)
        (eve--segment-panel-refresh)))
    pasted-id))

(defun eve-segment-panel-copy ()
  "Copy the current panel segment or marker."
  (interactive)
  (eve--segment-panel-run-source-command #'eve-copy-segment))

(defun eve-segment-panel-paste-after ()
  "Paste the copied segment or marker after the current panel segment."
  (interactive)
  (eve--segment-panel-run-source-returning-segment #'eve-paste-segment))

(defun eve-segment-panel-paste-before ()
  "Paste the copied segment or marker before the current panel segment."
  (interactive)
  (eve--segment-panel-run-source-returning-segment #'eve-paste-segment-before))

(defun eve-segment-panel-edit-notes ()
  "Edit notes for the current panel segment."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-edit-notes))

(defun eve-segment-panel-toggle-tag ()
  "Toggle a tag for the current panel segment."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-toggle-tag))

(defun eve-segment-panel-edit-speaker ()
  "Edit speaker for the current panel segment."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-edit-speaker))

(defun eve-segment-panel-edit-range ()
  "Edit time range for the current panel segment."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-edit-start-end))

(defun eve-segment-panel-insert-marker ()
  "Insert a marker before the current panel segment."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-insert-marker))

(defun eve-segment-panel-play-source ()
  "Play source for the current panel segment."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-play-segment))

(defun eve-segment-panel-play-rendered ()
  "Play rendered output for the current panel segment."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-play-rendered))

(defun eve-segment-panel-play-source-continuous ()
  "Play source media from the current panel segment onward."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-play-source))

(defun eve-segment-panel-compile ()
  "Compile the source TJM buffer from the segment panel."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-compile))

(defun eve-segment-panel-validate ()
  "Validate the source TJM buffer from the segment panel."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-validate))

(defun eve-segment-panel-cache-current ()
  "Warm render cache for the current panel segment."
  (interactive)
  (let ((segment-id eve-segment-panel-segment-id))
    (with-current-buffer (eve--segment-panel-source-buffer)
      (if (not buffer-file-name)
          (message "Cannot cache unsaved Eve buffer")
        (when (buffer-modified-p)
          (save-buffer))
        (let* ((output (eve--default-output-file))
               (base-cmd (split-string
                          (eve--text-edit-command buffer-file-name output)
                          " " t))
               (full-cmd (eve--auto-render-command
                          (append base-cmd (list "--segments" segment-id)))))
          (setq eve--render-status 'speculative)
          (force-mode-line-update)
          (make-process :name "eve-panel-cache"
                        :buffer nil
                        :command full-cmd
                        :noquery t))))))

(defun eve-segment-panel-undo ()
  "Undo Eve edit from the segment panel."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-undo))

(defun eve-segment-panel-redo ()
  "Redo Eve edit from the segment panel."
  (interactive)
  (eve--segment-panel-run-source-command #'call-interactively #'eve-redo))

(defun eve-segment-panel-save ()
  "Save the source TJM buffer from the segment panel."
  (interactive)
  (let ((source (eve--segment-panel-source-buffer)))
    (with-current-buffer source
      (save-buffer))
    (eve--segment-panel-refresh)))

(defun eve-segment-panel-merge-next (&optional force)
  "Merge current panel segment with the next segment."
  (interactive "P")
  (let ((panel (current-buffer)))
    (eve--segment-panel-run-source-command #'eve-merge-with-next force)
    (when (buffer-live-p panel)
      (with-current-buffer panel
        (setq eve-segment-panel-selection-anchor nil)
        (eve--segment-panel-refresh)))))

(defun eve-segment-panel-merge-previous (&optional force)
  "Merge previous segment with the current panel segment."
  (interactive "P")
  (let ((panel (current-buffer)))
    (eve--segment-panel-run-source-command #'eve-merge-with-previous force)
    (when (buffer-live-p panel)
      (with-current-buffer panel
        (setq eve-segment-panel-segment-id
              (with-current-buffer (eve--segment-panel-source-buffer)
                (eve--segment-id-at-point)))
        (setq eve-segment-panel-selection-anchor nil)
        (eve--segment-panel-refresh)))))

(defun eve--context-panel-compact-duration (duration)
  "Return compact text for b-roll DURATION."
  (when-let* ((seconds (eve--time->seconds duration)))
    (if (= seconds (floor seconds))
        (format "%ds" (floor seconds))
      (format "%.1fs" seconds))))

(defun eve--context-panel-broll-glyphs (broll)
  "Return compact configuration glyphs for BROLL."
  (let (glyphs)
    (push (pcase (downcase (eve--stringify (eve--aget 'mode broll)))
            ("replace" "▣")
            ("overlay" "⊕")
            ("pip" "◱")
            (_ nil))
          glyphs)
    (push (pcase (downcase (eve--stringify (eve--aget 'audio broll)))
            ("source" "🎙")
            ("broll" "♪")
            ("mute" "🔇")
            (_ nil))
          glyphs)
    (when (eve--aget 'continue broll) (push "→" glyphs))
    (when (eve--aget 'still broll) (push "▧" glyphs))
    (when (eve--aget 'placeholders broll) (push "{…}" glyphs))
    (when (eve--aget 'overlays broll) (push "▭" glyphs))
    (string-join (delq nil (nreverse glyphs)) " ")))

(defun eve--context-panel-broll-label (broll)
  "Return compact side-panel label for BROLL."
  (let* ((file (eve--stringify (eve--aget 'file broll)))
         (basename (if (string-empty-p file)
                       "<no file>"
                     (file-name-nondirectory file)))
         (duration (eve--context-panel-compact-duration (eve--aget 'duration broll)))
         (glyphs (eve--context-panel-broll-glyphs broll)))
    (string-join (delq nil (list "🎞"
                                 basename
                                 duration
                                 (unless (string-empty-p glyphs) glyphs)))
                 " ")))

(defun eve--context-thumbnail-cache-dir ()
  "Return the thumbnail cache directory for the current TJM buffer."
  (let* ((base (file-name-directory (or buffer-file-name default-directory)))
         (dir (expand-file-name ".eve-cache/thumbnails" base)))
    (make-directory dir t)
    dir))

(defun eve--context-thumbnail-time-from-label (label)
  "Return seconds represented by timestamp LABEL like [HH:MM:SS]."
  (when (and (stringp label)
             (string-match "\\`\\[\\([0-9]+\\):\\([0-9]+\\):\\([0-9]+\\)\\]\\'" label))
    (+ (* 3600.0 (string-to-number (match-string 1 label)))
       (* 60.0 (string-to-number (match-string 2 label)))
       (string-to-number (match-string 3 label)))))

(defun eve--context-thumbnail-rendered-output-fresh-p (output)
  "Return non-nil when rendered OUTPUT exists and is fresh for this TJM file."
  (and output buffer-file-name
       (file-exists-p output)
       (not (time-less-p (nth 5 (file-attributes output))
                         (nth 5 (file-attributes buffer-file-name))))))

(defun eve--context-thumbnail-segment-render-starts (cumulative-times)
  "Return alist of segment id to rendered start time from CUMULATIVE-TIMES."
  (let ((prev 0.0)
        starts)
    (dolist (entry cumulative-times)
      (push (cons (car entry) prev) starts)
      (setq prev (cdr entry)))
    (nreverse starts)))

(defun eve--context-thumbnail-source-candidate (segment rendered-time rendered-start)
  "Return source thumbnail candidate for SEGMENT at RENDERED-TIME.
RENDERED-START is SEGMENT's start time in the rendered timeline."
  (let ((delta (max 0.0 (- rendered-time rendered-start))))
    (if (eve--marker-p segment)
        (let* ((broll (eve--segment-broll segment))
               (file (and broll (eve--stringify (alist-get 'file broll)))))
          (when (and file (not (string-empty-p file))
                     (not (string-match-p "\\.json\\'" file)))
            (list :kind 'source
                  :file (eve--resolve-relative-path file)
                  :time (+ (or (eve--time->seconds
                                (alist-get 'start_offset broll))
                               0.0)
                           delta))))
      (when-let* ((source-id (alist-get 'source segment))
                  (source (eve--source-by-id source-id))
                  (file (alist-get 'file source)))
        (list :kind 'source
              :file (eve--resolve-relative-path file)
              :time (+ (or (alist-get 'start segment) 0.0) delta))))))

(defun eve--context-thumbnail-candidates (item)
  "Return thumbnail candidates for timestamp ITEM in preferred order."
  (let* ((rendered-time (plist-get item :rendered-time))
         (rendered-output (eve--default-output-file))
         (rendered-fresh (eve--context-thumbnail-rendered-output-fresh-p
                          rendered-output))
         (segments (eve--segments))
         (segment-id (plist-get item :segment-id))
         (segment (seq-find (lambda (candidate)
                              (equal (eve--stringify (alist-get 'id candidate))
                                     (eve--stringify segment-id)))
                            segments))
         (cumulative (eve--rendered-cumulative-times
                      segments eve-hide-deleted-mode))
         (starts (eve--context-thumbnail-segment-render-starts cumulative))
         (rendered-start (or (alist-get segment-id starts nil nil #'equal) 0.0))
         (source-candidate (and segment
                                rendered-time
                                (eve--context-thumbnail-source-candidate
                                 segment rendered-time rendered-start)))
         (rendered-candidate (and rendered-fresh rendered-time
                                  (list :kind 'rendered
                                        :file rendered-output
                                        :time rendered-time))))
    (pcase eve-context-thumbnail-source
      ('source (delq nil (list source-candidate)))
      ('rendered (delq nil (list rendered-candidate)))
      (_ (delq nil (list rendered-candidate source-candidate))))))

(defun eve--context-thumbnail-cache-path (candidate)
  "Return cache path for thumbnail CANDIDATE."
  (let* ((file (plist-get candidate :file))
         (time (plist-get candidate :time))
         (kind (plist-get candidate :kind))
         (signature (format "%s|%s|%.3f|%dx%d|%s"
                            kind file time
                            eve-context-thumbnail-width
                            eve-context-thumbnail-height
                            (nth 5 (file-attributes file))))
         (name (concat (secure-hash 'sha1 signature) ".jpg")))
    (expand-file-name name (eve--context-thumbnail-cache-dir))))

(defun eve--context-thumbnail-fresh-p (candidate path)
  "Return non-nil when thumbnail PATH is fresh for CANDIDATE."
  (and path (file-exists-p path)
       (not (time-less-p (nth 5 (file-attributes path))
                         (nth 5 (file-attributes
                                 (plist-get candidate :file)))))))

(defun eve--context-thumbnail-state (item)
  "Return (:path PATH) for ITEM, enqueueing generation when needed."
  (when (and eve-context-thumbnails-enabled
             (display-images-p)
             (executable-find "ffmpeg"))
    (catch 'found
      (dolist (candidate (eve--context-thumbnail-candidates item))
        (let* ((file (plist-get candidate :file))
               (path (and file (file-exists-p file)
                          (eve--context-thumbnail-cache-path candidate))))
          (when path
            (if (eve--context-thumbnail-fresh-p candidate path)
                (throw 'found (list :path path))
              (eve--context-thumbnail-enqueue candidate path))))))))

(defun eve--context-thumbnail-ensure-tables ()
  "Initialise thumbnail bookkeeping tables for the current buffer."
  (unless (hash-table-p eve--context-thumbnail-pending)
    (setq eve--context-thumbnail-pending (make-hash-table :test #'equal)))
  (unless (hash-table-p eve--context-thumbnail-failed)
    (setq eve--context-thumbnail-failed (make-hash-table :test #'equal))))

(defun eve--context-thumbnail-enqueue (candidate path)
  "Queue thumbnail generation for CANDIDATE into PATH."
  (eve--context-thumbnail-ensure-tables)
  (let ((key path))
    (unless (or (gethash key eve--context-thumbnail-pending)
                (gethash key eve--context-thumbnail-failed))
      (puthash key t eve--context-thumbnail-pending)
      (setq eve--context-thumbnail-queue
            (append eve--context-thumbnail-queue
                    (list (list :candidate candidate :path path))))
      (eve--context-thumbnail-pump))))

(defun eve--context-thumbnail-command (candidate path)
  "Return ffmpeg command to write thumbnail CANDIDATE to PATH."
  (let* ((cmd (list (executable-find "ffmpeg") "-y"
                    "-ss" (format "%.3f" (plist-get candidate :time))
                    "-i" (plist-get candidate :file)
                    "-frames:v" "1"
                    "-vf" (format "scale=%d:%d:force_original_aspect_ratio=decrease"
                                   eve-context-thumbnail-width
                                   eve-context-thumbnail-height)
                    path)))
    (if (and eve-auto-render-low-priority (executable-find "nice"))
        (append (list (executable-find "nice") "-n" "19") cmd)
      cmd)))

(defun eve--context-thumbnail-pump ()
  "Start queued thumbnail jobs up to `eve-context-thumbnail-jobs'."
  (eve--context-thumbnail-ensure-tables)
  (while (and eve--context-thumbnail-queue
              (< eve--context-thumbnail-active
                 (max 1 eve-context-thumbnail-jobs)))
    (let* ((job (pop eve--context-thumbnail-queue))
           (candidate (plist-get job :candidate))
           (path (plist-get job :path))
           (source-buffer (current-buffer)))
      (setq eve--context-thumbnail-active (1+ eve--context-thumbnail-active))
      (make-process
       :name "eve-thumbnail"
       :buffer nil
       :command (eve--context-thumbnail-command candidate path)
       :noquery t
       :sentinel
       (lambda (proc _event)
         (when (memq (process-status proc) '(exit signal))
           (when (buffer-live-p source-buffer)
             (with-current-buffer source-buffer
               (setq eve--context-thumbnail-active
                     (max 0 (1- eve--context-thumbnail-active)))
               (remhash path eve--context-thumbnail-pending)
               (unless (and (eq (process-status proc) 'exit)
                            (= (process-exit-status proc) 0)
                            (file-exists-p path))
                 (puthash path t eve--context-thumbnail-failed)
                 (message "eve thumbnail failed: %s @ %.3fs"
                          (file-name-nondirectory
                           (plist-get candidate :file))
                          (plist-get candidate :time)))
               (eve--context-thumbnail-schedule-refresh)
               (eve--context-thumbnail-pump)))))))))

(defun eve--context-thumbnail-schedule-refresh ()
  "Debounce context-panel refresh after a thumbnail finishes."
  (when eve--context-thumbnail-refresh-timer
    (cancel-timer eve--context-thumbnail-refresh-timer))
  (let ((source-buffer (current-buffer)))
    (setq eve--context-thumbnail-refresh-timer
          (run-with-idle-timer
           0.1 nil
           (lambda ()
             (when (buffer-live-p source-buffer)
               (with-current-buffer source-buffer
                 (setq eve--context-thumbnail-refresh-timer nil)
                 (when (and (eve--context-panels-available-p)
                            (buffer-live-p context-panels-side-panel-buffer)
                            (get-buffer-window context-panels-side-panel-buffer t))
                   (context-panels-refresh)))))))))

(defun eve--context-panel-timestamp-items (milestones)
  "Return context-panel timestamp items from MILESTONES."
  (delq nil
        (mapcar (lambda (milestone)
                  (let* ((segment-id (car milestone))
                         (label (cdr milestone))
                         (anchor (eve--segment-position segment-id))
                         (rendered-time
                          (eve--context-thumbnail-time-from-label label)))
                    (when anchor
                      (list :id (format "timestamp:%s:%s" segment-id label)
                            :kind 'timestamp
                            :label label
                            :rendered-time rendered-time
                            :segment-id segment-id
                            :anchor-pos anchor
                            :source-start anchor
                            :source-end (eve--segment-end-position segment-id)))))
                milestones)))

(defun eve--context-panel-broll-items (segments)
  "Return context-panel b-roll items for SEGMENTS."
  (delq nil
        (mapcar (lambda (segment)
                  (when-let* ((broll (eve--segment-broll segment))
                              (segment-id (eve--aget 'id segment))
                              (anchor (eve--segment-position segment-id)))
                    (list :id (format "broll:%s" segment-id)
                          :kind 'broll
                          :label (eve--context-panel-broll-label broll)
                          :segment-id segment-id
                          :anchor-pos anchor
                          :source-start anchor
                          :source-end (eve--segment-end-position segment-id))))
                segments)))

(defun eve--context-panel-side-items (&optional source-buffer)
  "Return Eve side-panel items for SOURCE-BUFFER."
  (with-current-buffer (or source-buffer (current-buffer))
    (let* ((segments (eve--segments))
           (cumulative-times (eve--rendered-cumulative-times
                              segments eve-hide-deleted-mode))
           (milestones (eve--ruler-milestones cumulative-times eve-ruler-interval)))
      (append (eve--context-panel-timestamp-items milestones)
              (eve--context-panel-broll-items segments)))))

(defun eve--context-panel-render-side-item (source-buffer item)
  "Render Eve side-panel ITEM."
  (let ((label (or (plist-get item :label) "")))
    (if (eq (plist-get item :kind) 'timestamp)
        (let ((thumb (with-current-buffer source-buffer
                       (eve--context-thumbnail-state item))))
          (when-let* ((path (plist-get thumb :path))
                      (image (create-image
                              path 'jpeg nil
                              :width eve-context-thumbnail-width
                              :height eve-context-thumbnail-height)))
            (insert-image image)
            (insert " "))
          (insert (propertize label 'face 'eve-ruler-face) "\n"))
      (insert label "\n"))))

(defun eve--context-panel-jump-side-item (source-buffer item)
  "Jump from an Eve side-panel ITEM to SOURCE-BUFFER."
  (pop-to-buffer source-buffer)
  (eve--goto-segment (plist-get item :segment-id)))

(defun eve--context-panel-buffer-name (_source-buffer)
  "Return the Eve context side-panel buffer name."
  "*Eve Context*")

(defun eve--context-panel-bottom-views (_source-buffer)
  "Return Eve bottom views for `context-panels'."
  (list (list :id 'segment-editor
              :label "Segment editor"
              :height eve-segment-panel-height
              :mode #'eve-segment-panel-mode
              :render #'eve--segment-panel-render
              :select t)))

(defun eve--context-panels-provider ()
  "Return the Eve provider descriptor for `context-panels'."
  (list :name 'eve
        :collect-side-items #'eve--context-panel-side-items
        :render-side-item #'eve--context-panel-render-side-item
        :jump-side-item #'eve--context-panel-jump-side-item
        :collect-bottom-views #'eve--context-panel-bottom-views
        :side-panel-buffer-name #'eve--context-panel-buffer-name
        :side-panel-width 42))

(defun eve--context-tick-clear-overlays ()
  "Remove source tick overlays used by the Eve context panel."
  (mapc #'delete-overlay eve--context-tick-overlays)
  (setq eve--context-tick-overlays nil))

(defun eve--context-tick-refresh-overlays ()
  "Render source tick overlays for context-panel timestamp targets."
  (eve--context-tick-clear-overlays)
  (let* ((segments (eve--segments))
         (cumulative-times (eve--rendered-cumulative-times
                            segments eve-hide-deleted-mode))
         (milestones (eve--ruler-milestones cumulative-times eve-ruler-interval))
         (seen nil))
    (dolist (milestone milestones)
      (let* ((segment-id (car milestone))
             (pos (and (not (member segment-id seen))
                       (eve--segment-position segment-id))))
        (when pos
          (push segment-id seen)
          (let ((overlay (make-overlay pos (min (point-max) (1+ pos)) nil t nil)))
            (overlay-put overlay 'before-string
                         (propertize "| "
                                     'face 'eve-ruler-face
                                     'display '(height 1.0)))
            (push overlay eve--context-tick-overlays)))))))

(defun eve--context-panels-after-open ()
  "Disable legacy ruler and show source ticks while the context panel is open."
  (when (derived-mode-p 'eve-mode)
    (when eve-ruler-mode
      (setq eve--context-panels-disabled-ruler t)
      (eve-ruler-mode -1))
    (eve--context-tick-refresh-overlays)))

(defun eve--context-panels-before-close ()
  "Clean up context ticks and restore the legacy ruler if Eve disabled it."
  (when (derived-mode-p 'eve-mode)
    (eve--context-tick-clear-overlays)
    (when eve--context-panels-disabled-ruler
      (setq eve--context-panels-disabled-ruler nil)
      (eve-ruler-mode 1))))

(defun eve--context-panels-setup ()
  "Register Eve context-panel support for the current buffer when available."
  (when (eve--context-panels-available-p)
    (context-panels-mode 1)
    (context-panels-register-provider (eve--context-panels-provider))
    (add-hook 'context-panels-after-side-panel-open-hook
              #'eve--context-panels-after-open nil t)
    (add-hook 'context-panels-before-side-panel-close-hook
              #'eve--context-panels-before-close nil t)))

(defun eve--context-panels-command-source-buffer ()
  "Return the Eve source buffer for a context-panel command."
  (cond
   ((derived-mode-p 'eve-mode) (current-buffer))
   ((and (boundp 'context-panels-source-buffer)
         (buffer-live-p context-panels-source-buffer)
         (with-current-buffer context-panels-source-buffer
           (derived-mode-p 'eve-mode)))
    context-panels-source-buffer)
   (t (user-error "No Eve source buffer for context panels"))))

(defun eve--context-panels-visible-p (source)
  "Return non-nil when SOURCE already has visible context panels."
  (or (when-let* ((side (buffer-local-value 'context-panels-side-panel-buffer
                                            source)))
        (get-buffer-window side t))
      (when-let* ((bottom (buffer-local-value 'context-panels-bottom-panel-buffer
                                              source)))
        (get-buffer-window bottom t))))

(defun eve--context-panels-close (source)
  "Close Eve context panels for SOURCE."
  (with-current-buffer source
    (context-panels-close)
    (when (fboundp 'context-panels-close-bottom-view)
      (context-panels-close-bottom-view)))
  (message "Closed Eve context panels"))

(defun eve-context-panels-open ()
  "Toggle Eve context panels for the current buffer.
Open the side and bottom panels when absent; close visible context panels when
already open.  This command requires the optional `context-panels' package."
  (interactive)
  (unless (eve--context-panels-available-p)
    (user-error "Optional context-panels package is unavailable"))
  (eve--context-panels-setup)
  (let ((source (eve--context-panels-command-source-buffer)))
    (if (eve--context-panels-visible-p source)
        (eve--context-panels-close source)
      (let ((side-buffer (context-panels-open source)))
        (when (fboundp 'context-panels-open-bottom-view)
          (context-panels-open-bottom-view 'segment-editor source t))
        (message "Opened Eve context panel")
        side-buffer))))

;;;###autoload
(define-derived-mode eve-mode special-mode "EVE"
  "Major mode for Textual Join Manifest files (.tjm.json)."
  (eve-mode--ensure-json-library)
  (setq-local indent-tabs-mode nil)
  (setq-local buffer-read-only t)
  (setq-local truncate-lines nil)
  (setq-local word-wrap t)
  (setq-local line-spacing eve-line-spacing)
  (setq-local revert-buffer-function #'eve--revert)
  (setq-local write-contents-functions (list #'eve--write-file))
  (setq-local eve--visual-separators nil)
  (setq-local eve--last-echo-id nil)
  (auto-save-mode -1)
  (setq-local buffer-auto-save-file-name nil)
  (setq-local auto-save-default nil)
  (when (boundp 'auto-save-visited-predicate)
    (setq-local auto-save-visited-predicate #'eve--auto-save-blocker))
  (add-hook 'post-command-hook #'eve--post-command nil t)
  ;; `window-size-change-functions' is buffer-local-aware and runs only for
  ;; windows showing this buffer when it is displayed or resized.  This keeps
  ;; Eve's visual wrapping out of users' global window-change hooks.
  (add-hook 'window-size-change-functions #'eve--apply-visual-wrap nil t)
  (add-hook 'kill-buffer-hook #'eve--remove-wrap nil t)
  (add-hook 'kill-buffer-hook #'eve--context-tick-clear-overlays nil t)
  (add-hook 'kill-buffer-hook #'eve--ipc-teardown nil t)
  (add-hook 'kill-buffer-hook #'eve--auto-render-teardown nil t)
  (when eve-auto-render-default-enabled
    (eve-auto-render-mode 1))
  (eve-hide-deleted-mode 1)
  (eve-ruler-mode 1)
  (setq-local mode-line-format
              (let ((fmt (copy-sequence (default-value 'mode-line-format)))
                    (status '(:eval (eve--mode-line-status))))
                (let ((tail (memq 'mode-line-buffer-identification fmt)))
                  (if tail
                      (setcdr tail (cons status (cdr tail)))
                    (setq fmt (append fmt (list status)))))
                fmt))
  (eve-reload)
  (eve--context-panels-setup)
  (when eve-context-panels-auto-open
    (eve-context-panels-open))
  (eve--apply-visual-wrap))

;;;###autoload
(add-to-list 'auto-mode-alist '("\\.tjm\\.json\\'" . eve-mode))

(defun eve-reload (&optional _ignore-auto _noconfirm)
  "Reload TJM data from disk and re-render the buffer."
  (interactive)
  (eve--load-data)
  (eve--apply-filler-tags)
  (eve--render t)
  (setq eve--dirty nil)
  (setq eve--undo-stack nil
	eve--redo-stack nil)
  (set-buffer-modified-p nil)
  (message "Reloaded TJM manifest"))

(defun eve--revert (&rest _)
  (eve-reload))

(defun eve--write-file ()
  "Custom saver used by `eve-mode'."
  (when eve--dirty
    (let* ((issues (when eve-validation-on-save
		     (eve-validate t)))
	   (auto-fixes (and issues (eve--auto-correct))))
      (when auto-fixes
	(setq issues (when eve-validation-on-save
		       (eve-validate t)))
	(message "Auto-corrected %d issue(s)" (length auto-fixes)))
      (when (and issues
		 (not (yes-or-no-p (format "%d validation issue(s) found; save anyway? "
					   (length issues)))))
	(user-error "Aborted save"))
      (eve--serialize-to-file buffer-file-name)
      (when buffer-file-name
	(set-visited-file-modtime))
      (setq eve--dirty nil)))
  (set-buffer-modified-p nil)
  t)

(defun eve--load-data ()
  "Parse buffer contents (or backing file) into `eve--data'."
  (let* ((file buffer-file-name)
	 (json-source
	  (cond
	   ((and file (file-readable-p file))
	    (with-temp-buffer
	      (insert-file-contents file)
	      (buffer-substring-no-properties (point-min) (point-max))))
	   (t
	    (buffer-substring-no-properties (point-min) (point-max)))))
	 (json-source (string-trim json-source)))
    (setq eve--data
	  (if (string-empty-p json-source)
	      '((version . 1) (sources . nil) (segments . nil))
	    (json-parse-string json-source :object-type 'alist :array-type 'list
			       :null-object nil :false-object nil)))))

(defun eve--segments ()
  (or (alist-get 'segments eve--data)
      (let ((segments '()))
	(setf (alist-get 'segments eve--data) segments))))

(defun eve--sources ()
  "Return the manifest source list, initializing it when absent."
  (or (alist-get 'sources eve--data)
      (let ((sources '()))
        (setf (alist-get 'sources eve--data) sources))))

(defun eve--source-by-id (source-id &optional sources)
  "Return the source descriptor with SOURCE-ID from SOURCES or current data."
  (seq-find (lambda (item)
	      (string= (eve--stringify (eve--aget 'id item))
                       (eve--stringify source-id)))
	    (or sources (eve--sources))))

(defun eve--format-time (seconds)
  (let* ((tot (or (eve--coerce-number seconds) 0.0))
	 (mins (floor (/ tot 60)))
	 (secs (- tot (* mins 60))))
    (format "%02d:%06.3f" mins secs)))

(defun eve--coerce-number (value)
  "Return VALUE as a float when representable, otherwise nil."
  (cond
   ((numberp value) (float value))
   ((stringp value)
    (let ((parsed (string-to-number value)))
      (if (and (= parsed 0.0)
	       (not (string-match-p "\\`[+-]?[0-9]*\\.?[0-9]+\\'" value)))
	  nil
	parsed)))
   (t nil)))

(defun eve--edit-metadata (item)
  "Return ITEM's nested edit metadata alist, if any."
  (let ((edit (alist-get 'edit item)))
    (when (listp edit)
      edit)))

(defun eve--ensure-edit-metadata-cell (item)
  "Ensure ITEM has a nested `edit' cell for in-place mutation."
  (when (and (listp item)
             (not (assq 'edit item)))
    (nconc item (list (cons 'edit nil))))
  item)

(defun eve--edit-kind (item)
  "Return ITEM edit kind from nested edit metadata only."
  (eve--aget 'kind (eve--edit-metadata item)))

(defun eve--edit-deleted-p (item)
  "Return non-nil when ITEM is marked deleted via nested edit metadata."
  (alist-get 'deleted (eve--edit-metadata item)))

(defun eve--merge-faces (&rest faces)
  "Merge FACES into a single face value."
  (let ((merged nil))
    (dolist (face faces)
      (cond
       ((null face))
       ((listp face)
        (dolist (item face)
          (unless (memq item merged)
            (setq merged (append merged (list item))))))
       ((memq face merged))
       (t
        (setq merged (append merged (list face))))))
    (pcase merged
      (`() nil)
      (`(,only) only)
      (_ merged))))

(defun eve--normalize-segment-text (text)
  "Normalize segment TEXT for rendering."
  (string-trim (replace-regexp-in-string "\\s-+" " " (eve--stringify text))))

(defun eve--word-render-text (word)
  "Return the preferred visible text for WORD."
  (or (car (eve--word-strings word)) ""))

(defun eve--segment-render-data (segment)
  "Return SEGMENT render data as (:text TEXT :word-spans SPANS).
Each entry in SPANS is (START END WORD) over the returned TEXT."
  (let* ((words (alist-get 'words segment))
         (text (eve--normalize-segment-text (alist-get 'text segment)))
         (source-spans (and words (eve--segment-word-spans text)))
         (pieces nil)
         (word-spans nil)
         (cursor 0))
    (if (and words (= (length words) (length source-spans)))
        (cl-loop for word in words
                 for span in source-spans
                 do
                 (unless (and eve-hide-deleted-mode (eve--edit-deleted-p word))
                   (let ((word-text (nth 2 span)))
                     (unless (string-empty-p word-text)
                       (when pieces
                         (setq cursor (1+ cursor)))
                       (push word-text pieces)
                       (push (list cursor (+ cursor (length word-text)) word) word-spans)
                       (setq cursor (+ cursor (length word-text)))))))
      (if words
          (dolist (word words)
            (unless (and eve-hide-deleted-mode (eve--edit-deleted-p word))
              (let ((word-text (string-trim (eve--word-render-text word))))
                (unless (string-empty-p word-text)
                  (when pieces
                    (setq cursor (1+ cursor)))
                  (push word-text pieces)
                  (push (list cursor (+ cursor (length word-text)) word) word-spans)
                  (setq cursor (+ cursor (length word-text)))))))
        (setq pieces (unless (string-empty-p text) (list text)))))
    (list :text (if pieces
                    (string-join (nreverse pieces) " ")
                  "")
          :word-spans (nreverse word-spans))))

(defun eve--rendered-marker-duration (segment)
  "Return rendered duration in seconds for marker SEGMENT, or 0.0."
  (let* ((broll (eve--segment-broll segment))
         (broll-duration (and broll (eve--aget 'duration broll)))
         (duration (or (eve--time->seconds broll-duration)
                       (eve--time->seconds (alist-get 'duration segment))
                       0.0)))
    (if (and broll (eve--aget 'file broll) (> duration 0.0))
        duration
      0.0)))

(defun eve--segment-has-deleted-words-p (segment)
  "Return non-nil if SEGMENT has any deleted words."
  (seq-some #'eve--edit-deleted-p (or (alist-get 'words segment) '())))

(defun eve--rendered-word-ranges (segment hide-deleted)
  "Return rendered word ranges for SEGMENT."
  (let ((words (or (alist-get 'words segment) '())))
    (delq nil
          (mapcar (lambda (word)
                    (unless (and hide-deleted (eve--edit-deleted-p word))
                      (let ((start (alist-get 'start word))
                            (end (alist-get 'end word)))
                        (when (and (numberp start) (numberp end) (> end start))
                          (cons start end)))))
                  words))))

(defun eve--rendered-segment-gap-bounds (segment hide-deleted)
  "Return rendered source-media bounds for gap calculations."
  (if (and hide-deleted (eve--segment-has-deleted-words-p segment))
      (when-let* ((ranges (eve--rendered-word-ranges segment hide-deleted)))
        (cons (caar ranges) (cdar (last ranges))))
    (let ((start (alist-get 'start segment))
          (end (alist-get 'end segment)))
      (when (and (numberp start) (numberp end) (> end start))
        (cons start end)))))

(defun eve--rendered-preserved-gap-duration (previous current hide-deleted)
  "Return rendered preserved-gap duration between PREVIOUS and CURRENT."
  (let ((previous-source (alist-get 'source previous))
        (current-source (alist-get 'source current)))
    (if (not (and previous-source current-source
                  (equal previous-source current-source)))
        0.0
      (let ((previous-bounds (eve--rendered-segment-gap-bounds previous hide-deleted))
            (current-bounds (eve--rendered-segment-gap-bounds current hide-deleted)))
        (if (not (and previous-bounds current-bounds))
            0.0
          (let* ((gap (- (car current-bounds) (cdr previous-bounds)))
                 (threshold (or eve-preserve-gaps-max 0.0)))
            (if (and (> gap 0.0) (<= gap threshold)) gap 0.0)))))))

(defun eve--rendered-segment-duration (segment hide-deleted)
  "Return rendered duration in seconds for SEGMENT."
  (cond
   ((and hide-deleted (eve--edit-deleted-p segment)) 0.0)
   ((eve--marker-p segment) (eve--rendered-marker-duration segment))
   ((and hide-deleted (eve--segment-has-deleted-words-p segment))
    (cl-reduce #'+ (mapcar (lambda (range) (- (cdr range) (car range)))
                           (eve--rendered-word-ranges segment hide-deleted))
               :initial-value 0.0))
   (t
    (let ((start (alist-get 'start segment))
          (end (alist-get 'end segment)))
      (if (and (numberp start) (numberp end) (> end start))
          (- end start)
        0.0)))))

(defun eve--rendered-total-duration (segments hide-deleted)
  "Return total rendered duration in seconds for SEGMENTS."
  (or (cdr (car (last (eve--rendered-cumulative-times segments hide-deleted))))
      0.0))

(defun eve--rendered-cumulative-times (segments hide-deleted)
  "Return alist of (ID . cumulative-end-time) for each segment."
  (let ((cumulative 0.0)
        (previous-source-segment nil)
        result)
    (dolist (segment segments)
      (unless (eve--marker-p segment)
        (setq cumulative (+ cumulative
                            (if previous-source-segment
                                (eve--rendered-preserved-gap-duration
                                 previous-source-segment segment hide-deleted)
                              0.0))))
      (let ((id (alist-get 'id segment))
            (duration (eve--rendered-segment-duration segment hide-deleted)))
        (setq cumulative (+ cumulative duration))
        (push (cons id cumulative) result)
        (when (and (not (eve--marker-p segment)) (> duration 0.0))
          (setq previous-source-segment segment))))
    (nreverse result)))

(defun eve--format-ruler-time (seconds)
  "Format SECONDS as a ruler timestamp string [hh:mm:ss]."
  (let* ((total (max 0 (floor seconds)))
         (hh (/ total 3600))
         (mm (/ (mod total 3600) 60))
         (ss (mod total 60)))
    (format "[%02d:%02d:%02d]" hh mm ss)))

(defun eve--ruler-milestones (cumulative-times interval)
  "Return milestones as ((segment-id . formatted-time) ...) list.
CUMULATIVE-TIMES is an alist of (id . end-time) from
`eve--rendered-cumulative-times'. INTERVAL is the spacing in seconds.
Always includes time 0, mapped to the first segment."
  (when cumulative-times
    (let* ((safe-interval (max 1.0 (or interval 30.0)))
           (total (cdr (car (last cumulative-times))))
           (last-seg (caar (last cumulative-times)))
           (milestones nil)
           (t-val 0.0))
      (while (<= t-val (+ total 0.001))
        (let ((seg-id
               (cond
                ((<= t-val 0.0) (caar cumulative-times))
                (t
                 (or (catch 'segment
                       (let ((prev 0.0))
                         (cl-loop for (id . end) in cumulative-times do
                                  (when (and (> t-val prev)
                                             (<= t-val end))
                                    (throw 'segment id))
                                  (setq prev end))))
                     last-seg)))))
          (push (cons seg-id (eve--format-ruler-time t-val)) milestones))
        (setq t-val (+ t-val safe-interval)))
      (nreverse milestones))))

(defun eve--playback-source-segment-at-time (time segments)
  "Return the segment in SEGMENTS whose start <= TIME < end, or nil.
SEGMENTS is an ordered list of segment alists with \\='start and \\='end keys.
Returns nil if TIME is before all segments or falls in a gap."
  (cl-loop for seg in segments
           for seg-start = (or (alist-get 'start seg) 0.0)
           for seg-end   = (or (alist-get 'end   seg) 0.0)
           when (and (>= time seg-start) (< time seg-end))
           return seg
           finally return nil))

(defun eve--playback-rendered-segment-at-time (time cumulative-times)
  "Return the segment-id in CUMULATIVE-TIMES that contains rendered TIME.
CUMULATIVE-TIMES is an alist of (id . cumulative-end) as produced by
`eve--rendered-cumulative-times'.  Returns the id of the segment where
prev-cumulative-end < TIME <= cumulative-end.  At TIME=0 returns the
first segment id.  Returns nil when CUMULATIVE-TIMES is empty."
  (when cumulative-times
    (if (<= time 0.0)
        (caar cumulative-times)
      (let ((prev 0.0)
            found)
        (cl-loop for (id . end) in cumulative-times
                 do (when (and (> time prev) (<= time end))
                      (setq found id)
                      (cl-return))
                 do (setq prev end))
        (or found (caar (last cumulative-times)))))))

(defun eve--render (&optional preserve-point)
  "Render `eve--data' into the current buffer."
  (let* ((segments (eve--segments))
	 (current-id (and preserve-point (eve--segment-id-at-point)))
	 (inhibit-read-only t))
    (setq eve--last-echo-id nil)
    (setq eve--visual-separators
	  (cl-remove-if-not (lambda (id)
			      (seq-some (lambda (segment)
				  (equal id (alist-get 'id segment)))
				segments))
			    eve--visual-separators))
    (eve--clear-fold-overlays)
    (erase-buffer)
    (let ((previous-id nil)
	  (previous-marker nil)
	  (previous-meta-inserted nil)
	  (rendered-any nil))
	  (dolist (segment segments)
	    (let* ((id (or (alist-get 'id segment) ""))
		   (_segment-edit (eve--ensure-edit-metadata-cell segment))
		   (_word-edits (mapc #'eve--ensure-edit-metadata-cell
				      (alist-get 'words segment)))
		   (segment-deleted (eve--edit-deleted-p segment))
		   (tags (seq-filter (lambda (tag)
				       (let ((s (eve--stringify tag)))
					 (and s (not (string-empty-p s)))))
				     (eve--segment-tags segment)))
		   (notes (let ((s (eve--stringify (eve--segment-notes segment))))
			    (unless (string-empty-p s) s)))
		   (broll (eve--segment-broll segment))
		   (markerp (eve--marker-p segment))
		   (render-data (unless markerp (eve--segment-render-data segment)))
		   (text (and render-data (plist-get render-data :text)))
		   (word-spans (and render-data (plist-get render-data :word-spans)))
		   (base-face (if broll 'eve-broll-face 'eve-text-face))
		   (segment-face (eve--merge-faces (when segment-deleted 'eve-deleted-face)
					      (if markerp 'eve-marker-face base-face)))
		   (segment-visible
		    (and (not (and eve-hide-deleted-mode segment-deleted))
			 (or markerp
			     (not (string-empty-p text))
			     (and eve--words-visible (alist-get 'words segment))
			     broll))))
	      (when segment-visible
		(when rendered-any
		  (cond
		   ((or previous-marker
			(member previous-id eve--visual-separators))
		    (insert "\n\n"))
		   (previous-meta-inserted
		    (insert "\n"))
		   (t
		    (insert " "))))
		(when (and markerp
			   (> (point) (point-min)))
		  (let* ((start (max (point-min) (- (point) 2)))
			 (need-extra
			  (not (and (> (point) start)
				    (string= "\n\n"
					     (buffer-substring-no-properties start (point)))))))
		    (when need-extra
		      (unless (eq (char-before) ?\n)
			(insert "\n"))
		      (insert "\n"))))
		(if markerp
		    (let* ((title (string-trim (eve--stringify (alist-get 'title segment))))
			   (display (format "# %s"
					    (if (string-empty-p title)
						"(Untitled marker)"
					      title)))
			   (start-pos (point)))
		      (insert display)
		      (add-text-properties start-pos (point)
					   (list 'face segment-face
						 'font-lock-face segment-face
						 'eve-segment segment
						 'eve-segment-id id
						 'eve-tags tags
						 'eve-notes notes
						 'eve-broll broll
						 'eve-marker t)))
		  (let ((start-pos (point)))
		    (insert text)
		    (when (> (point) start-pos)
		      (add-text-properties start-pos (point)
					   (list 'face segment-face
						 'font-lock-face segment-face
						 'eve-segment segment
						 'eve-segment-id id
						 'eve-tags tags
						 'eve-notes notes
						 'eve-broll broll))
		      (dolist (span word-spans)
			(let* ((word (nth 2 span))
			       (word-face
				(eve--merge-faces
				 (when (eve--word-filler-p word)
				   'eve-filler-face)
				 (when (eve--edit-deleted-p word)
				   'eve-deleted-face)
				 segment-face)))
			  (when (and word-face
				     (> (nth 1 span) (nth 0 span)))
			    (add-text-properties (+ start-pos (nth 0 span))
						 (+ start-pos (nth 1 span))
						 (list 'face word-face
						       'font-lock-face word-face))))))
		    (when (and eve--words-visible (alist-get 'words segment))
		      (insert (propertize (format "\n[words] %s"
						  (eve--format-words (alist-get 'words segment)))
					  'face 'eve-meta-face)))))
		(let ((meta-inserted (eve--render-broll-summary segment)))
		  (setq rendered-any t
			previous-id id
			previous-marker markerp
			previous-meta-inserted meta-inserted)))))
    (goto-char (point-min))
    (when current-id
      (eve--goto-segment current-id))
    (eve--update-focus-overlay)
    (when eve-ruler-mode
      (eve--update-ruler))
    (when eve--context-tick-overlays
      (eve--context-tick-refresh-overlays))
    (eve--apply-fold-overlays)
    (eve--apply-visual-wrap))))

(defun eve--format-words (words)
  (mapconcat (lambda (word)
	       (let ((token (alist-get 'token word))
		     (start (eve--format-time (alist-get 'start word)))
		     (end (eve--format-time (alist-get 'end word))))
		 (format "%s[%s-%s]" token start end)))
	     (if eve-hide-deleted-mode
		 (seq-remove #'eve--edit-deleted-p words)
	       words)
	     " "))

(defun eve--summarize-broll (broll)
  (let* ((file (eve--stringify (alist-get 'file broll)))
	 (mode (eve--aget 'mode broll))
	 (audio (eve--aget 'audio broll))
	 (offset (eve--aget 'start_offset broll))
	 (duration (eve--aget 'duration broll))
	 (still (eve--aget 'still broll))
	 (continue (eve--aget 'continue broll))
	 (overlays (eve--aget 'overlays broll))
	 (placeholders (eve--aget 'placeholders broll))
	 (template (eve--broll-template-data broll))
	 (template-media (and template (eve--stringify (eve--aget 'template template))))
	 (template-placeholders (and template (eve--aget 'placeholders template)))
	 (template-overlays (and template (eve--aget 'overlays template)))
	 (placeholder-info (eve--summarize-placeholders template-placeholders placeholders))
	 (overlay-part
	  (cond
	   ((and overlays (listp overlays)) (format "overlays=%d" (length overlays)))
	   ((and template-overlays (listp template-overlays))
	    (format "overlays=%d (template)" (length template-overlays)))))
	 (parts
	  (cl-remove-if
	   #'null
	   (list
	    (when file (format "file=%s" file))
	    (when template-media (format "template=%s" template-media))
	    (when mode (format "mode=%s" (eve--stringify mode)))
	    (when audio (format "audio=%s" (eve--stringify audio)))
	    (when offset
	      (format "offset=%s" (or (eve--display-time offset) (eve--stringify offset))))
	    (when duration
	      (format "duration=%s" (or (eve--display-time duration) (eve--stringify duration))))
	    overlay-part
	    (when placeholder-info (format "placeholders: %s" placeholder-info))
	    (when continue "continue")
	    (when still "still")))))
    (string-join parts ", ")))

(defun eve--render-broll-summary (segment)
  "Insert a descriptive summary line for SEGMENT's b-roll metadata.
Returns non-nil when a summary was inserted."
  (let ((broll (eve--segment-broll segment)))
    (when broll
      (let* ((summary (or (eve--summarize-broll broll) ""))
	     (line (if (string-empty-p (string-trim summary))
		       "[b-roll] attached"
		     (format "[b-roll] %s" summary))))
	(insert (propertize (concat "\n" line)
			    'face 'eve-meta-face
			    'eve-segment segment
			    'eve-segment-id (alist-get 'id segment)
			    'eve-broll broll))
	t))))

(defun eve--segment-at-point ()
  (or (get-text-property (point) 'eve-segment)
      (get-text-property (max (point-min) (1- (point))) 'eve-segment)))

(defun eve--segment-id-at-point ()
  (or (get-text-property (point) 'eve-segment-id)
      (get-text-property (max (point-min) (1- (point))) 'eve-segment-id)))

(defun eve--goto-segment (segment-id)
  (let ((pos (point-min))
	(found nil))
    (while (and (< pos (point-max)) (not found))
      (if (equal (get-text-property pos 'eve-segment-id) segment-id)
	  (setq found pos)
	(setq pos (or (next-single-property-change pos 'eve-segment-id nil (point-max))
		      (point-max)))))
    (when found (goto-char found))
    found))

(defun eve--segment-word-spans (text)
  "Return a list of (START END WORD) spans parsed from TEXT."
  (let ((len (length text))
	(idx 0)
	(spans nil))
    (while (< idx len)
      (while (and (< idx len)
		  (memq (aref text idx) '(?	 ?\n ?\r ? )))
	(setq idx (1+ idx)))
      (when (< idx len)
	(let ((start idx))
	  (while (and (< idx len)
		      (not (memq (aref text idx) '(?	 ?\n ?\r ? ))))
	    (setq idx (1+ idx)))
	  (push (list start idx (substring text start idx)) spans))))
    (nreverse spans)))

(defun eve--spans->text (spans)
  "Reconstruct segment text from SPANS produced by `eve--segment-word-spans'."
  (string-trim (mapconcat (lambda (span) (nth 2 span)) spans " ")))

(defun eve--word-bounds-in-segment (pos seg-start seg-end)
  "Return the bounds of the word around POS limited to SEG-START..SEG-END."
  (let ((pos (min (max pos seg-start)
		  (if (> seg-end seg-start)
		      (max (1- seg-end) seg-start)
		    seg-start))))
    (save-excursion
      (goto-char pos)
      (when (and (< (point) seg-end)
		 (memq (char-after) '(?	 ?\n ?\r ? )))
	(skip-chars-forward "\s-" seg-end)
	(when (>= (point) seg-end)
	  (goto-char pos)
	  (skip-chars-backward "\s-" seg-start)))
      (let ((mid (point)))
	(let ((word-start (progn
			    (skip-chars-backward "^\s-" seg-start)
			    (point))))
	  (goto-char mid)
	  (let ((word-end (progn
			    (skip-chars-forward "^\s-" seg-end)
			    (point))))
	    (when (< word-start word-end)
	      (cons word-start word-end))))))))

(defun eve--word-info-at-point ()
  "Return a plist describing the word at point in the current segment."
  (let* ((segment (eve--segment-at-point)))
    (unless segment
      (user-error "No segment at point"))
    (when (eve--marker-p segment)
      (user-error "Markers do not contain words"))
    (let* ((segment-id (alist-get 'id segment))
	   (bounds (eve--segment-bounds segment-id)))
      (unless bounds
	(error "Failed to determine segment bounds"))
      (let* ((seg-start (car bounds))
	     (seg-end (cdr bounds))
	     (word-bounds (eve--word-bounds-in-segment (point) seg-start seg-end)))
	(unless word-bounds
	  (user-error "No word at point"))
	(let* ((segment-text (buffer-substring-no-properties seg-start seg-end))
	       (relative-start (- (car word-bounds) seg-start))
	       (relative-end (- (cdr word-bounds) seg-start))
	       (spans (eve--segment-word-spans segment-text))
	       (word-text (substring segment-text relative-start relative-end))
	       (word-index
		(or (cl-loop for span in spans
			     for idx from 0
			     when (and (>= relative-start (nth 0 span))
				       (< relative-start (nth 1 span)))
			     return idx)
		    (cl-position word-text spans
				 :test #'string=
				 :key (lambda (span) (nth 2 span))))))
	  (unless (numberp word-index)
	    (user-error "Unable to locate word index"))
	  (list :segment segment
		:segment-id segment-id
		:segment-text segment-text
		:segment-bounds bounds
		:word-bounds word-bounds
		:word-text word-text
		:word-index word-index
		:word-spans spans))))))

(defun eve--collect-segment-ids ()
  "Return a list of all segment ids in the current buffer."
  (mapcar (lambda (segment)
	    (eve--stringify (alist-get 'id segment)))
	  (eve--segments)))

(defun eve--generate-segment-id (base)
  "Generate a unique segment id derived from BASE."
  (let* ((base (or (and (stringp base) (not (string-empty-p base)) base)
		   "segment"))
	 (ids (eve--collect-segment-ids))
	 (n 1)
	 (candidate (format "%s-split-%d" base n)))
    (while (member candidate ids)
      (setq n (1+ n)
	    candidate (format "%s-split-%d" base n)))
    candidate))

(defun eve--remove-nth (n list)
  "Return LIST without the element at index N (0-based)."
  (if (or (null list) (< n 0))
      list
    (let ((result nil)
	  (idx 0))
      (dolist (item list (nreverse result))
	(unless (= idx n)
	  (push item result))
	(setq idx (1+ idx))))))

(defun eve--find-word-token-index (words token fallback-index)
  "Find TOKEN within WORDS, preferring FALLBACK-INDEX when it matches."
  (when words
    (let ((len (length words)))
      (cond
       ((and (numberp fallback-index)
	     (<= 0 fallback-index)
	     (< fallback-index len)
	     (string=
	      token
	      (eve--stringify (alist-get 'token (nth fallback-index words)))))
	fallback-index)
       (t
	(cl-position token words
		     :test #'string=
		     :key (lambda (word)
			    (eve--stringify (alist-get 'token word)))))))))

(defun eve--goto-word-by-index (segment-id index)
  "Move point to the start of word INDEX within SEGMENT-ID."
  (let ((bounds (eve--segment-bounds segment-id)))
    (when bounds
      (let* ((seg-start (car bounds))
	     (seg-end (cdr bounds))
	     (segment-text (buffer-substring-no-properties seg-start seg-end))
	     (spans (eve--segment-word-spans segment-text)))
	(when (and spans (<= 0 index) (< index (length spans)))
	  (goto-char (+ seg-start (nth 0 (nth index spans))))
	  t)))))

(defun eve--window-total-width (window)
  (let* ((margins (window-margins window))
	 (left (or (car margins) 0))
	 (right (or (cdr margins) 0)))
    (+ (window-width window) left right)))

(defun eve--apply-visual-wrap (&optional changed-window)
  "Apply Eve wrapping to visible windows showing the current buffer.
When CHANGED-WINDOW is non-nil, update only that window.  This matches the
calling convention of `window-size-change-functions'."
  (dolist (window (if (window-live-p changed-window)
                      (list changed-window)
                    (get-buffer-window-list (current-buffer) nil t)))
    (when (window-live-p window)
      (with-current-buffer (window-buffer window)
        (when (derived-mode-p 'eve-mode)
          (let ((fill (or fill-column (default-value 'fill-column)))
                (minimum-right-margin (or right-margin-width 0)))
            (let* ((margins (window-margins window))
                   (left (or (car margins) 0))
                   (right (or (cdr margins) 0))
                   (total (eve--window-total-width window))
                   (wrap-margin (if (> total fill) (- total fill) 0))
                   (desired (max minimum-right-margin wrap-margin))
                   (current-right right))
              (unless (= desired current-right)
                (unless (window-parameter window 'eve--saved-margins)
                  (set-window-parameter window 'eve--saved-margins
                                        (cons left right)))
                (set-window-margins window left desired)
                (set-window-parameter window 'eve--wrapped t)))
            (eve--apply-fringe-width window 0 0)))))))

(defun eve--apply-fringe-width (window left right)
  "Adjust WINDOW fringes, saving the original values when first changed."
  (let ((current (window-fringes window)))
    (unless (window-parameter window 'eve--saved-fringes)
      (set-window-parameter window 'eve--saved-fringes current))
    (set-window-fringes window left right (nth 2 current))
    (set-window-parameter window 'eve--wrapped t)))

(defun eve--remove-wrap ()
  (dolist (window (get-buffer-window-list (current-buffer) nil t))
    (eve--restore-wrap window)))

(defun eve--restore-wrap (window)
  (when-let* ((saved (window-parameter window 'eve--saved-margins)))
    (set-window-margins window (car saved) (cdr saved)))
  (when-let* ((saved-fringes (window-parameter window 'eve--saved-fringes)))
    (apply #'set-window-fringes (append (list window) saved-fringes)))
  (set-window-parameter window 'eve--saved-margins nil)
  (set-window-parameter window 'eve--saved-fringes nil)
  (set-window-parameter window 'eve--wrapped nil))

(defun eve--segment-index (segment-id segments)
  "Return the index of SEGMENT-ID within SEGMENTS."
  (when segment-id
    (cl-position segment-id segments
		 :test #'equal
		 :key (lambda (segment)
			(alist-get 'id segment)))))

(defun eve--segment-id-list (&optional segments)
  "Return all segment ids in SEGMENTS or current data."
  (delq nil (mapcar (lambda (segment)
                      (eve--aget 'id segment))
                    (or segments (eve--segments)))))

(defun eve--navigable-segment-p (segment hide-deleted)
  "Return non-nil when SEGMENT should be reachable by segment navigation.
When HIDE-DELETED is non-nil, deleted segments and markers are skipped because
they are not rendered in the source buffer or bottom panel."
  (not (and hide-deleted (eve--edit-deleted-p segment))))

(defun eve--segment-navigation-target-index (segments current-id delta hide-deleted)
  "Return index DELTA navigable positions from CURRENT-ID in SEGMENTS."
  (let* ((count (length segments))
         (step (if (> delta 0) 1 -1))
         (remaining (abs delta))
         (index (or (eve--segment-index current-id segments)
                    (if (> delta 0) -1 count)))
         target-index)
    (while (and (null target-index)
                (setq index (+ index step))
                (<= 0 index)
                (< index count))
      (let ((segment (nth index segments)))
        (when (eve--navigable-segment-p segment hide-deleted)
          (setq remaining (1- remaining))
          (when (zerop remaining)
            (setq target-index index)))))
    target-index))

(defun eve--segment-navigation-target-id (segments current-id delta hide-deleted)
  "Return segment id DELTA navigable positions from CURRENT-ID in SEGMENTS."
  (when-let* ((index (eve--segment-navigation-target-index
                      segments current-id delta hide-deleted)))
    (alist-get 'id (nth index segments))))

(defun eve--marker-navigation-target-id (segments current-id delta hide-deleted)
  "Return marker id DELTA marker positions from CURRENT-ID in SEGMENTS."
  (let* ((count (length segments))
         (step (if (> delta 0) 1 -1))
         (remaining (abs delta))
         (index (or (eve--segment-index current-id segments)
                    (if (> delta 0) -1 count)))
         target-id)
    (while (and (null target-id)
                (setq index (+ index step))
                (<= 0 index)
                (< index count))
      (let ((segment (nth index segments)))
        (when (and (eve--marker-p segment)
                   (eve--navigable-segment-p segment hide-deleted))
          (setq remaining (1- remaining))
          (when (zerop remaining)
            (setq target-id (alist-get 'id segment))))))
    target-id))

(defun eve--unique-id (base existing-ids)
  "Return a unique id based on BASE not present in EXISTING-IDS."
  (let* ((root (if (and base (not (string-empty-p (eve--stringify base))))
                   (eve--stringify base)
                 "segment"))
         (candidate root)
         (n 1))
    (while (member candidate existing-ids)
      (setq candidate (format "%s-copy-%d" root n))
      (setq n (1+ n)))
    candidate))

(defun eve--source-equal-p (a b)
  "Return non-nil when source descriptors A and B point at the same source."
  (and a b
       (equal (eve--stringify (eve--aget 'file a))
              (eve--stringify (eve--aget 'file b)))))

(defun eve--import-source-for-segment (segment source)
  "Import SOURCE for SEGMENT if needed, rewriting SEGMENT's source id on conflict."
  (when source
    (let* ((sources (eve--sources))
           (source-id (eve--aget 'id source))
           (existing (eve--source-by-id source-id sources)))
      (cond
       ((null existing)
        (setf (alist-get 'sources eve--data)
              (append sources (list (copy-tree source t)))))
       ((eve--source-equal-p existing source) nil)
       (t
        (let* ((new-source (copy-tree source t))
               (new-id (eve--unique-id source-id
                                       (delq nil
                                             (mapcar (lambda (item)
                                                       (eve--aget 'id item))
                                                     sources)))))
          (setf (alist-get 'id new-source) new-id)
          (setf (alist-get 'source segment) new-id)
          (setf (alist-get 'sources eve--data)
                (append sources (list new-source)))))))))

(defun eve-copy-segment ()
  "Copy the segment or marker at point for later `eve-paste-segment'.
The copied data includes edit metadata such as b-roll.  When pasted into another
TJM buffer, the referenced source descriptor is copied when needed."
  (interactive)
  (let* ((segment (or (eve--segment-at-point)
                      (user-error "No segment at point")))
         (source-id (eve--aget 'source segment))
         (source (and source-id (eve--source-by-id source-id))))
    (setq eve--segment-clipboard
          (list :segment (copy-tree segment t)
                :source (and source (copy-tree source t))))
    (message "Copied %s" (or (eve--aget 'id segment) "segment"))))

(defun eve--paste-segment-at-point (&optional before)
  "Paste the copied Eve segment near point.
When BEFORE is non-nil, paste before the segment at point; otherwise paste
after it.  Return the pasted segment id."
  (unless eve--segment-clipboard
    (user-error "No copied Eve segment"))
  (let* ((segment (copy-tree (plist-get eve--segment-clipboard :segment) t))
         (source (copy-tree (plist-get eve--segment-clipboard :source) t))
         (segments (eve--segments))
         (current (eve--segment-at-point))
         (current-index (and current (cl-position current segments :test #'eq)))
         (insert-index (cond
                        ((and before current-index) current-index)
                        (current-index (1+ current-index))
                        (t (length segments))))
         (new-id (eve--unique-id (eve--aget 'id segment) (eve--segment-id-list segments))))
    (eve--record-state)
    (setf (alist-get 'id segment) new-id)
    (eve--import-source-for-segment segment source)
    (setf (alist-get 'segments eve--data)
          (append (cl-subseq segments 0 insert-index)
                  (list segment)
                  (nthcdr insert-index segments)))
    (eve--mark-dirty)
    (eve--render t)
    (eve--goto-segment new-id)
    (eve--update-focus-overlay)
    (message "Pasted %s" new-id)
    new-id))

(defun eve-paste-segment ()
  "Paste the last copied Eve segment or marker after the segment at point.
The pasted segment receives a fresh id when needed.  Source descriptors are
copied across TJM buffers when required, and b-roll metadata is preserved."
  (interactive)
  (eve--paste-segment-at-point))

(defun eve-paste-segment-before ()
  "Paste the last copied Eve segment or marker before the segment at point.
The pasted segment receives a fresh id when needed.  Source descriptors are
copied across TJM buffers when required, and b-roll metadata is preserved."
  (interactive)
  (eve--paste-segment-at-point t))

(defun eve--move-by-segments (delta)
  "Move point DELTA visible segments forward (or backward when negative)."
  (let ((segments (eve--segments)))
    (unless segments
      (user-error "No segments available"))
    (if-let* ((target-id (eve--segment-navigation-target-id
                          segments (eve--segment-id-at-point) delta
                          eve-hide-deleted-mode)))
        (progn
          (eve--goto-segment target-id)
          (eve--update-focus-overlay)
          (eve--echo-segment-info)
          t)
      (message (if (> delta 0)
                   "Already at last segment"
                 "Already at first segment")))))

(defun eve--move-by-markers (delta)
  "Move point DELTA markers forward or backward."
  (let ((segments (eve--segments)))
    (unless segments
      (user-error "No segments available"))
    (if-let* ((target-id (eve--marker-navigation-target-id
                          segments (eve--segment-id-at-point) delta
                          eve-hide-deleted-mode)))
        (progn
          (eve--goto-segment target-id)
          (eve--update-focus-overlay)
          (eve--echo-segment-info)
          t)
      (message (if (> delta 0)
                   "Already at last marker"
                 "Already at first marker")))))

(defun eve-next-marker (&optional count)
  "Move point to the next marker segment.
With prefix COUNT, move that many markers."
  (interactive "p")
  (eve--move-by-markers (or count 1)))

(defun eve-previous-marker (&optional count)
  "Move point to the previous marker segment.
With prefix COUNT, move that many markers."
  (interactive "p")
  (eve--move-by-markers (- (or count 1))))

(defun eve--clear-fold-overlays ()
  "Remove all active marker fold overlays in the current buffer."
  (mapc #'delete-overlay eve--fold-overlays)
  (setq eve--fold-overlays nil))

(defun eve--marker-render-ranges ()
  "Return rendered marker ranges as (ID START END)."
  (let ((pos (point-min))
        (limit (point-max))
        ranges)
    (while (< pos limit)
      (let ((next (next-single-property-change pos 'eve-marker nil limit)))
        (when (get-text-property pos 'eve-marker)
          (let ((id (get-text-property pos 'eve-segment-id)))
            (push (list (eve--stringify id) pos next) ranges)))
        (setq pos next)))
    (nreverse ranges)))

(defun eve--marker-ids-rendered ()
  "Return marker ids currently rendered in the buffer."
  (mapcar #'car (eve--marker-render-ranges)))

(defun eve--apply-fold-overlays ()
  "Apply fold overlays for ids in `eve--folded-marker-ids'."
  (eve--clear-fold-overlays)
  (add-to-invisibility-spec '(eve-fold . t))
  (let ((ranges (eve--marker-render-ranges)))
    (setq eve--folded-marker-ids
          (seq-filter (lambda (id)
                        (member id (mapcar #'car ranges)))
                      eve--folded-marker-ids))
    (cl-loop for (id _start marker-end) in ranges
             for rest on ranges
             for next-start = (cadr (cadr rest))
             for fold-end = (or next-start (point-max))
             when (and (member id eve--folded-marker-ids)
                       (< marker-end fold-end))
             do (let ((overlay (make-overlay marker-end fold-end nil t nil)))
                  (overlay-put overlay 'invisible 'eve-fold)
                  (overlay-put overlay 'isearch-open-invisible #'delete-overlay)
                  (overlay-put overlay 'before-string
                               (propertize " …" 'face 'eve-meta-face))
                  (push overlay eve--fold-overlays)))))

(defun eve--refold ()
  "Reapply marker folds after changing `eve--folded-marker-ids'."
  (eve--apply-fold-overlays)
  (eve--update-focus-overlay)
  (when eve-ruler-mode
    (eve--update-ruler))
  (when eve--context-tick-overlays
    (eve--context-tick-refresh-overlays)))

(defun eve-toggle-marker-fold ()
  "Toggle folding for the marker section at point."
  (interactive)
  (let* ((segment (or (eve--segment-at-point)
                      (user-error "No segment at point")))
         (id (eve--stringify (alist-get 'id segment))))
    (unless (eve--marker-p segment)
      (user-error "Not on a marker"))
    (if (member id eve--folded-marker-ids)
        (progn
          (setq eve--folded-marker-ids
                (delete id eve--folded-marker-ids))
          (eve--refold)
          (message "Expanded %s" id))
      (push id eve--folded-marker-ids)
      (eve--refold)
      (message "Folded %s" id))))

(defun eve-toggle-all-marker-folds ()
  "Toggle folding for all rendered marker sections."
  (interactive)
  (let ((marker-ids (eve--marker-ids-rendered)))
    (if (and marker-ids
             (seq-every-p (lambda (id) (member id eve--folded-marker-ids))
                          marker-ids))
        (progn
          (setq eve--folded-marker-ids nil)
          (eve--refold)
          (message "Expanded all markers"))
      (setq eve--folded-marker-ids marker-ids)
      (eve--refold)
      (message "Folded all markers"))))

(defun eve-next-segment (&optional count)
  "Move point to the next TJM segment.
Optional COUNT moves forward multiple segments."
  (interactive "p")
  (eve--move-by-segments (or count 1)))

(defun eve-previous-segment (&optional count)
  "Move point to the previous TJM segment.
Optional COUNT moves backward multiple segments."
  (interactive "p")
  (eve--move-by-segments (- (or count 1))))

(defun eve--mark-dirty ()
  (setq eve--dirty t)
  (set-buffer-modified-p t))

(defun eve-undo ()
  "Restore the previous TJM state from the undo stack, preserving point."
  (interactive)
  (if (null eve--undo-stack)
      (message "Nothing to undo")
    (push (eve--snapshot) eve--redo-stack)
    (let ((target-id (eve--segment-id-at-point)))
      (setq eve--data (pop eve--undo-stack))
      (eve--mark-dirty)
      (eve--render t)
      (when target-id
	(eve--goto-segment target-id))
      (message "TJM undo"))))

(defun eve-redo ()
  "Reapply a state from the redo stack."
  (interactive)
  (if (null eve--redo-stack)
      (message "Nothing to redo")
    (push (eve--snapshot) eve--undo-stack)
    (setq eve--data (pop eve--redo-stack))
    (eve--mark-dirty)
    (eve--render t)
    (message "TJM redo")))

(defun eve-delete-segment ()
  "Toggle the deleted flag on the segment at point."
  (interactive)
  (let* ((segment (eve--segment-at-point))
	 (segments (eve--segments))
	 (segment-id (and segment (alist-get 'id segment)))
	 (segment-index (and segment (cl-position segment segments :test #'eq)))
	 (next-id (and segment-index
		       (< (1+ segment-index) (length segments))
		       (alist-get 'id (nth (1+ segment-index) segments))))
	 (previous-id (and segment-index
			 (> segment-index 0)
			 (alist-get 'id (nth (1- segment-index) segments)))))
    (unless segment
      (user-error "No segment at point"))
    (when (or (eve--edit-deleted-p segment)
	      (yes-or-no-p (format "Delete segment %s? " segment-id)))
      (eve--record-state)
	      (let ((deleted (eve--toggle-edit-deleted segment)))
	(eve--mark-dirty)
	(eve--render t)
	(or (eve--goto-segment segment-id)
	    (and next-id (eve--goto-segment next-id))
	    (and previous-id (eve--goto-segment previous-id)))
	(eve--update-focus-overlay)
	(eve--echo-segment-info)
	(message "Segment %s" (if deleted "deleted" "restored"))))))

(defun eve--word-data-at-point ()
  "Return plist containing mutable word data for the word at point."
  (let* ((info (eve--word-info-at-point))
         (segment (plist-get info :segment))
         (word-index (plist-get info :word-index))
         (word-text (plist-get info :word-text))
         (words (alist-get 'words segment))
         (token-index (eve--find-word-token-index words word-text word-index))
         (fallback-token-index (and words (numberp word-index)
                                    (<= 0 word-index)
                                    (< word-index (length words))
                                    word-index))
         (effective-token-index (or token-index fallback-token-index))
         (word (and words
                    (numberp effective-token-index)
                    (<= 0 effective-token-index)
                    (< effective-token-index (length words))
                    (nth effective-token-index words))))
    (unless word
      (user-error "Segment has no word timings"))
    (plist-put info :word word)))

(defun eve-delete-word ()
  "Toggle the deleted flag on the word at point."
  (interactive)
  (let* ((info (eve--word-info-at-point))
	 (segment (plist-get info :segment))
	 (segment-id (plist-get info :segment-id))
	 (word-index (plist-get info :word-index))
	 (word-text (plist-get info :word-text))
	 (words (alist-get 'words segment))
	 (token-index (eve--find-word-token-index words word-text word-index))
	 (fallback-token-index (and words (numberp word-index)
				    (<= 0 word-index)
				    (< word-index (length words))
				    word-index))
	 (effective-token-index (or token-index fallback-token-index))
	 (word (and words
		    (numberp effective-token-index)
		    (<= 0 effective-token-index)
		    (< effective-token-index (length words))
		    (nth effective-token-index words))))
    (unless word
      (user-error "Segment has no word timings to delete"))
    (eve--record-state)
    (let ((deleted (eve--toggle-edit-deleted word)))
      (eve--mark-dirty)
      (eve--render t)
      (or (eve--goto-word-by-index segment-id word-index)
	  (and (> word-index 0)
	       (eve--goto-word-by-index segment-id (1- word-index)))
	  (eve--goto-segment segment-id))
      (eve--update-focus-overlay)
      (eve--echo-segment-info)
      (message "Word '%s' %s" word-text (if deleted "deleted" "restored")))))

(defun eve--filler-word-positions ()
  "Return visible filler word positions as (POSITION SEGMENT-ID WORD-INDEX WORD)."
  (let (positions)
    (dolist (segment (eve--segments) (nreverse positions))
      (unless (eve--marker-p segment)
        (let* ((id (alist-get 'id segment))
               (bounds (and id (eve--segment-bounds id)))
               (render-data (and bounds (eve--segment-render-data segment)))
               (spans (plist-get render-data :word-spans))
               (words (alist-get 'words segment)))
          (when bounds
            (cl-loop for span in spans
                     for word = (nth 2 span)
                     for index = (cl-position word words :test #'eq)
                     when (and index (eve--word-filler-p word))
                     do (push (list (+ (car bounds) (nth 0 span)) id index word)
                              positions))))))))

(defun eve--goto-filler (direction)
  "Move to the next filler in DIRECTION, either `next' or `previous'."
  (let* ((here (point))
         (positions (eve--filler-word-positions))
         (candidate
          (pcase direction
            ('next
             (car (seq-sort-by #'car #'<
                               (seq-filter (lambda (item) (> (car item) here))
                                           positions))))
            ('previous
             (car (seq-sort-by #'car #'>
                               (seq-filter (lambda (item) (< (car item) here))
                                           positions)))))))
    (unless candidate
      (user-error "No %s filler word" (symbol-name direction)))
    (goto-char (car candidate))
    (eve--update-focus-overlay)
    (eve--echo-segment-info)
    candidate))

(defun eve-next-filler ()
  "Move point to the next visible word tagged as filler."
  (interactive)
  (eve--goto-filler 'next))

(defun eve-previous-filler ()
  "Move point to the previous visible word tagged as filler."
  (interactive)
  (eve--goto-filler 'previous))

(defun eve-accept-filler ()
  "Mark the filler word at point as explicitly not filler.
This keeps the word in the transcript and prevents future auto-tagging from
marking it as filler again in this TJM file."
  (interactive)
  (let* ((info (eve--word-data-at-point))
         (segment-id (plist-get info :segment-id))
         (word-index (plist-get info :word-index))
         (word-text (plist-get info :word-text))
         (word (plist-get info :word)))
    (unless (eve--word-filler-p word)
      (user-error "Word is not marked as filler"))
    (eve--record-state)
    (eve--mark-word-non-filler word)
    (eve--mark-dirty)
    (eve--render t)
    (or (eve--goto-word-by-index segment-id word-index)
        (eve--goto-segment segment-id))
    (eve--update-focus-overlay)
    (eve--echo-segment-info)
    (message "Marked non-filler word '%s'" word-text)))

(defun eve-split-segment ()
  "Split the current segment at point, creating a new segment
that starts at the word under point."
  (interactive)
  (let* ((info (eve--word-info-at-point))
	 (segment (plist-get info :segment))
	 (broll-orig (let ((b (eve--segment-broll segment)))
		       (and b (copy-alist b))))
	 (segment-id (plist-get info :segment-id))
	 (word-index (plist-get info :word-index))
	 (word-spans (plist-get info :word-spans))
	 (words (alist-get 'words segment)))
    (unless words
      (user-error "Segment has no word timings to split"))
    (let ((total (length words)))
      (when (or (<= word-index 0)
		(>= word-index total))
	(user-error "Move to a word away from the segment boundary to split")))
    (eve--record-state)
    (let* ((segments (eve--segments))
	   (segment-index (cl-position segment segments :test #'eq)))
      (unless segment-index
	(error "Unable to determine segment index"))
      (let* ((before-words (cl-subseq words 0 word-index))
	     (after-words (cl-subseq words word-index))
	     (before-spans (cl-subseq word-spans 0 word-index))
	     (after-spans (cl-subseq word-spans word-index))
	     (segment-before (copy-tree segment t))
	     (segment-after (copy-tree segment t))
	     (new-id (eve--generate-segment-id segment-id)))
	;; Update text and timings for the first half
	(setf (alist-get 'text segment-before) (eve--spans->text before-spans))
	(setf (alist-get 'words segment-before) before-words)
	(eve--set-segment-broll segment-before (and broll-orig (copy-alist broll-orig)))
	(let ((first before-words)
	      (last (last before-words)))
	  (when (and first last)
	    (setf (alist-get 'start segment-before)
		  (eve--coerce-number (alist-get 'start (car first))))
	    (setf (alist-get 'end segment-before)
		  (eve--coerce-number (alist-get 'end (car last))))))
	;; Update text, id, and timings for the second half
	(setf (alist-get 'id segment-after) new-id)
	(setf (alist-get 'text segment-after) (eve--spans->text after-spans))
	(setf (alist-get 'words segment-after) after-words)
	(eve--set-segment-broll segment-after (and broll-orig (copy-alist broll-orig)))
	(let ((first after-words)
	      (last (last after-words)))
	  (when (and first last)
	    (setf (alist-get 'start segment-after)
		  (eve--coerce-number (alist-get 'start (car first))))
	    (setf (alist-get 'end segment-after)
		  (eve--coerce-number (alist-get 'end (car last))))))
	;; Splice the new segments into the manifest
	(let* ((head (cl-subseq segments 0 segment-index))
	       (tail (cl-subseq segments (1+ segment-index)))
	       (combined (append head (list segment-before segment-after) tail)))
	  (setf (alist-get 'segments eve--data) combined))
	;; Maintain visual separators, moving the flag to the new tail if necessary
	(when (member segment-id eve--visual-separators)
	  (setq eve--visual-separators
		(cons new-id (cl-remove segment-id eve--visual-separators
					:test #'equal))))
	(eve--mark-dirty)
	(eve--render t)
	(when broll-orig
	  (let* ((broll-before (eve--segment-broll segment-before))
		 (broll-after (eve--segment-broll segment-after))
		 (start-before (eve--coerce-number (alist-get 'start segment-before)))
		 (end-before (eve--coerce-number (alist-get 'end segment-before)))
		 (duration-before (and start-before end-before (- end-before start-before)))
		 (orig-offset (alist-get 'start_offset broll-orig))
		 (orig-duration (alist-get 'duration broll-orig))
		 (offset-seconds (eve--time->seconds orig-offset))
		 (new-offset (+ (or offset-seconds 0.0)
				(or duration-before 0.0)))
		 (remaining (when orig-duration
			      (max 0.0 (- (eve--time->seconds orig-duration)
					  (or duration-before 0.0))))))
	    (when broll-before
	      (setf (alist-get 'continue broll-before) nil)
	      (when orig-duration
		(setf (alist-get 'duration broll-before)
		      (eve--seconds->time-like orig-duration (or duration-before 0.0)))))
	    (when broll-after
	      (setf (alist-get 'continue broll-after) t)
	      (setf (alist-get 'start_offset broll-after)
		    (eve--seconds->time-like orig-offset new-offset))
	      (when orig-duration
		(setf (alist-get 'duration broll-after)
		      (if (> (or remaining 0.0) 0)
			  (eve--seconds->time-like orig-duration remaining)
			nil))))
	    (message "Segment split; marked b-roll to continue into the next segment.")))
	(eve--goto-segment new-id)
	(eve--goto-word-by-index new-id 0)
	(eve--update-focus-overlay)
	(eve--echo-segment-info)
	(message "Split segment %s" segment-id)))))

(defun eve--merge-conflicts (segment other)
  "Return strict merge conflicts between SEGMENT and OTHER."
  (let (conflicts)
    (let ((source-a (eve--stringify (alist-get 'source segment)))
          (source-b (eve--stringify (alist-get 'source other))))
      (when (and (not (string-empty-p source-a))
                 (not (string-empty-p source-b))
                 (not (string= source-a source-b)))
        (push "source" conflicts)))
    (let ((speaker-a (eve--stringify (alist-get 'speaker segment)))
          (speaker-b (eve--stringify (alist-get 'speaker other))))
      (when (and (not (string-empty-p speaker-a))
                 (not (string-empty-p speaker-b))
                 (not (string= speaker-a speaker-b)))
        (push "speaker" conflicts)))
    (let ((broll-a (eve--segment-broll segment))
          (broll-b (eve--segment-broll other)))
      (when (and broll-a broll-b (not (equal broll-a broll-b)))
        (push "b-roll" conflicts)))
    (nreverse conflicts)))

(defun eve--merge-segments-at-index (idx &optional force next-idx)
  "Merge segment at IDX with NEXT-IDX, optionally FORCE conflicts.
When NEXT-IDX is nil, merge with the immediately following segment."
  (let* ((segments (eve--segments))
         (segment (nth idx segments))
         (next-index (or next-idx (1+ idx)))
         (next (nth next-index segments)))
    (unless segment
      (user-error "No segment at point"))
    (when (eve--marker-p segment)
      (user-error "Cannot merge marker segments"))
    (unless next
      (user-error "Already at last segment"))
    (when (<= next-index idx)
      (user-error "Merge target must follow current segment"))
    (when (eve--marker-p next)
      (user-error "Cannot merge across marker segments"))
    (let ((conflicts (eve--merge-conflicts segment next)))
      (when (and conflicts
                 (not force)
                 (not (y-or-n-p (format "Merge conflicts (%s). Force merge? "
                                        (string-join conflicts ", ")))))
        (user-error "Merge conflicts: %s" (string-join conflicts ", "))))
    (eve--record-state)
    (let* ((text-a (eve--stringify (alist-get 'text segment)))
           (text-b (eve--stringify (alist-get 'text next)))
           (combined-text
            (string-trim
             (string-join
              (seq-filter (lambda (chunk)
                            (and chunk (not (string-empty-p chunk))))
                          (list text-a text-b))
              " ")))
           (words-a (copy-sequence (alist-get 'words segment)))
           (words-b (copy-sequence (alist-get 'words next)))
           (merged-words (append words-a words-b))
           (note-a (eve--stringify (eve--segment-notes segment)))
           (note-b (eve--stringify (eve--segment-notes next)))
           (tag-a (copy-sequence (eve--segment-tags segment)))
           (tag-b (copy-sequence (eve--segment-tags next)))
           (next-id (alist-get 'id next))
           (merged-id (alist-get 'id segment)))
      (when (and (null (alist-get 'source segment)) (alist-get 'source next))
        (setf (alist-get 'source segment) (alist-get 'source next)))
      (when (and (string-empty-p (eve--stringify (alist-get 'speaker segment)))
                 (not (string-empty-p (eve--stringify (alist-get 'speaker next)))))
        (setf (alist-get 'speaker segment) (alist-get 'speaker next)))
      (when (and (null (eve--segment-broll segment)) (eve--segment-broll next))
        (eve--set-segment-broll segment (copy-tree (eve--segment-broll next) t)))
      (setf (alist-get 'text segment)
            (and (not (string-empty-p combined-text)) combined-text))
      (setf (alist-get 'words segment) (and merged-words merged-words))
      (let ((notes (seq-filter (lambda (chunk)
                                 (and chunk (not (string-empty-p chunk))))
                               (list note-a note-b))))
        (eve--set-segment-notes
         segment
         (cond
          ((null notes) nil)
          ((= (length notes) 1) (car notes))
          (t (mapconcat #'identity notes "\n\n")))))
      (let* ((combined-tags (append tag-a tag-b))
             (clean-tags
              (seq-filter (lambda (tag)
                            (let ((as-string (eve--stringify tag)))
                              (and as-string (not (string-empty-p as-string)))))
                          combined-tags)))
        (eve--set-segment-tags
         segment
         (when clean-tags
           (cl-delete-duplicates clean-tags
                                 :test #'string=
                                 :key #'eve--stringify))))
      (if merged-words
          (let ((first-word (car merged-words))
                (last-word (car (last merged-words))))
            (setf (alist-get 'start segment)
                  (eve--coerce-number (alist-get 'start first-word)))
            (setf (alist-get 'end segment)
                  (eve--coerce-number (alist-get 'end last-word))))
        (setf (alist-get 'end segment) (alist-get 'end next)))
      (setq eve--visual-separators
            (cl-remove (eve--stringify next-id) eve--visual-separators
                       :test #'string= :count 1))
      (setf (alist-get 'segments eve--data)
            (cl-remove next segments :test #'eq :count 1))
      (eve--mark-dirty)
      (eve--render t)
      (when merged-id
        (eve--goto-segment merged-id)
        (eve--update-focus-overlay)
        (eve--echo-segment-info))
      (message "Merged segment %s with %s" merged-id (eve--stringify next-id)))))

(defun eve-merge-with-next (&optional force)
  "Merge the segment at point with the next visible segment.
With prefix FORCE, merge despite metadata conflicts."
  (interactive "P")
  (let* ((segment (or (eve--segment-at-point)
                      (user-error "No segment at point")))
         (segments (eve--segments))
         (idx (cl-position segment segments :test #'eq)))
    (unless idx
      (error "Unable to determine segment index"))
    (if-let* ((next-idx (eve--segment-navigation-target-index
                         segments (alist-get 'id segment) 1
                         eve-hide-deleted-mode)))
        (eve--merge-segments-at-index idx force next-idx)
      (user-error "Already at last segment"))))

(defun eve-merge-with-previous (&optional force)
  "Merge the previous visible segment with the segment at point.
With prefix FORCE, merge despite metadata conflicts."
  (interactive "P")
  (let* ((segment (or (eve--segment-at-point)
                      (user-error "No segment at point")))
         (segments (eve--segments))
         (idx (cl-position segment segments :test #'eq)))
    (unless idx
      (error "Unable to determine segment index"))
    (if-let* ((previous-idx (eve--segment-navigation-target-index
                             segments (alist-get 'id segment) -1
                             eve-hide-deleted-mode)))
        (eve--merge-segments-at-index previous-idx force idx)
      (user-error "Already at first segment"))))

(defun eve-move-segment-up ()
  "Move the current segment up by one position."
  (interactive)
  (let* ((segment (eve--segment-at-point))
	 (segments (eve--segments))
	 (idx (cl-position segment segments :test #'eq)))
    (unless segment
      (user-error "No segment at point"))
    (if (or (null idx) (<= idx 0))
	(message "Already at first segment")
      (eve--record-state)
      (cl-rotatef (nth (1- idx) segments) (nth idx segments))
      (eve--mark-dirty)
      (eve--render t))))

(defun eve-move-segment-down ()
  "Move the current segment down by one position."
  (interactive)
  (let* ((segment (eve--segment-at-point))
	 (segments (eve--segments))
	 (idx (cl-position segment segments :test #'eq)))
    (unless segment
      (user-error "No segment at point"))
    (if (or (null idx) (>= idx (1- (length segments))))
	(message "Already at last segment")
      (eve--record-state)
      (cl-rotatef (nth idx segments) (nth (1+ idx) segments))
      (eve--mark-dirty)
      (eve--render t))))

(defun eve-edit-text (segment)
  "Edit the text of SEGMENT (defaults to segment at point)."
  (interactive (list (or (eve--segment-at-point)
			 (user-error "No segment at point"))))
  (eve--record-state)
  (if (eve--marker-p segment)
      (let* ((current (alist-get 'title segment))
	     (new (read-string "Marker title: " (eve--stringify current))))
	(setf (alist-get 'title segment) (unless (string-empty-p new) new))
	(eve--mark-dirty)
	(eve--render t)
	(message "Updated marker title"))
    (let* ((current (alist-get 'text segment))
	   (new (read-string "Segment text: " current)))
      (setf (alist-get 'text segment) new)
      (eve--mark-dirty)
      (eve--render t))))

(defun eve-dwim-edit ()
  "Edit b-roll metadata or segment text depending on context at point."
  (interactive)
  (if (and (get-text-property (point) 'eve-broll)
	   (eq (face-at-point) 'eve-meta-face))
      (call-interactively #'eve-edit-broll)
    (call-interactively #'eve-edit-text)))

(defun eve-edit-speaker (segment)
  "Edit the speaker field of SEGMENT."
  (interactive (list (or (eve--segment-at-point)
			 (user-error "No segment at point"))))
  (when (eve--marker-p segment)
    (user-error "Markers do not have speakers"))
  (eve--record-state)
  (let* ((current (alist-get 'speaker segment))
	 (new (read-string "Speaker (blank to clear): " current)))
    (setf (alist-get 'speaker segment) (unless (string-empty-p new) new))
    (eve--mark-dirty)
    (eve--render t)))

(defun eve-edit-notes (segment)
  "Edit the notes field of SEGMENT."
  (interactive (list (or (eve--segment-at-point)
			 (user-error "No segment at point"))))
  (eve--record-state)
  (let* ((current (eve--segment-notes segment))
	 (new (read-string "Notes (blank to clear): " (or current ""))))
    (eve--set-segment-notes segment (unless (string-empty-p new) new))
    (eve--mark-dirty)
    (eve--render t)))

(defun eve-edit-start-end (segment)
  "Edit start and end timestamps of SEGMENT."
  (interactive (list (or (eve--segment-at-point)
			 (user-error "No segment at point"))))
  (eve--record-state)
  (if (eve--marker-p segment)
      (let* ((current-source (eve--stringify (alist-get 'source segment)))
	     (source-input (read-string "Marker source (blank to clear): " current-source))
	     (current-start (alist-get 'start segment))
	     (start-default (or (eve--display-time current-start) ""))
	     (start-input (read-string "Marker start (MM:SS or seconds, blank to clear): "
				       start-default))
	     (start (eve--normalize-time-input start-input)))
	(setf (alist-get 'source segment)
	      (unless (string-empty-p source-input) source-input))
	(if start
	    (setf (alist-get 'start segment) start)
	  (setf (alist-get 'start segment) nil))
	(eve--mark-dirty)
	(eve--render t)
	(message "Updated marker timing"))
    (let* ((start (alist-get 'start segment))
	   (end (alist-get 'end segment))
	   (new-start (read-number (format "Start (%.3f): " start) start))
	   (new-end (read-number (format "End (%.3f): " end) end)))
      (when (>= new-start new-end)
	(user-error "Start must be strictly less than end"))
      (setf (alist-get 'start segment) new-start
	    (alist-get 'end segment) new-end)
      (eve--mark-dirty)
      (eve--render t))))

(defun eve-toggle-tag (segment tag)
  "Toggle TAG on SEGMENT. Prompts for both if interactive."
  (interactive
   (let ((seg (or (eve--segment-at-point)
		  (user-error "No segment at point")))
	 (tag (read-string "Tag: ")))
     (list seg tag)))
  (eve--record-state)
  (let* ((tags (copy-sequence (eve--segment-tags segment)))
	 (existing (member tag tags)))
    (if existing
	(setq tags (delete tag tags))
      (setq tags (append tags (list tag))))
    (eve--set-segment-tags segment tags)
    (eve--mark-dirty)
    (eve--render t)))

(defun eve--read-choice (prompt choices default)
  "Read one of CHOICES with PROMPT, using DEFAULT as the initial choice."
  (let* ((default-string (eve--stringify default))
         (choice (completing-read prompt choices nil t nil nil default-string)))
    (if (string-empty-p choice) default-string choice)))

(defun eve--read-yes-no-choice (prompt default)
  "Read a y/n answer with PROMPT and boolean DEFAULT."
  (let* ((default-string (if default "y" "n"))
         (choice (completing-read
                  (format "%s [%s]: " prompt default-string)
                  '("y" "n" "yes" "no") nil t nil nil default-string)))
    (cond
     ((member choice '("y" "yes")) t)
     ((member choice '("n" "no")) nil)
     ((string-empty-p choice) default)
     (t (user-error "Enter y or n")))))

(defun eve-edit-broll (segment)
  "Edit b-roll metadata for SEGMENT."
  (interactive (list (or (eve--segment-at-point)
			 (user-error "No segment at point"))))
  (eve--record-state)
  (let* ((broll (copy-tree (eve--segment-broll segment) t))
	 (file (read-string "B-roll file (blank to clear): "
			    (eve--stringify (alist-get 'file broll))))
	 (mode (eve--read-choice "Mode: "
				 '("overlay" "replace" "pip")
				 (or (alist-get 'mode broll) "overlay")))
	 (audio-default (or (eve--stringify (alist-get 'audio broll))
			    "source"))
	 (audio (eve--read-choice "Audio: "
				  '("source" "mute" "broll")
				  audio-default))
	 (offset-existing (alist-get 'start_offset broll))
	 (offset-default (cond
			  ((stringp offset-existing) offset-existing)
			  ((numberp offset-existing) (eve--format-time offset-existing))
			  (t "")))
	 (offset-input (read-string "Start offset (MM:SS or seconds, blank to clear): "
				    offset-default))
	 (offset (eve--normalize-time-input offset-input))
	 (duration-existing (alist-get 'duration broll))
	 (duration-default (cond
			    ((stringp duration-existing) duration-existing)
			    ((numberp duration-existing) (eve--format-time duration-existing))
			    (t "")))
	 (duration-input (read-string "Duration (MM:SS or seconds, blank to clear): "
				      duration-default))
	 (duration (eve--normalize-time-input duration-input))
	 (still-existing (alist-get 'still broll))
	 (still-default (cond
			 ((booleanp still-existing) still-existing)
			 ((and file (not (string-empty-p file))
			       (image-file-name-p file)))
			 (t nil)))
	 (still (and (not (string-empty-p file))
		     (eve--read-yes-no-choice "Treat as still image?" still-default)))
	 (continue-existing (alist-get 'continue broll))
	 (continue-default (and continue-existing t))
	 (continue (and (not (string-empty-p file))
			(eve--read-yes-no-choice
			 "Continue from previous segment?" continue-default)))
	 (template (unless (string-empty-p file)
		     (eve--broll-template-data (list (cons 'file file))))))
    (when (and still (string= (downcase audio) "broll"))
      (user-error "Still images cannot use b-roll audio"))
    (if (string-empty-p file)
	(eve--set-segment-broll segment nil)
      (progn
	(setf (alist-get 'file broll nil t) file)
	(setf (alist-get 'mode broll nil t) (unless (string-empty-p mode) mode))
	(setf (alist-get 'audio broll nil t) (unless (string-empty-p audio) audio))
	(setf (alist-get 'start_offset broll nil t) offset)
	(setf (alist-get 'duration broll nil t) duration)
	(setf (alist-get 'still broll nil t) (and still t))
	(setf (alist-get 'continue broll nil t) (and continue t))
	(eve--set-segment-broll segment broll)
	(when (and template
		   (null (eve--aget 'placeholders broll))
		   (eve--aget 'placeholders template))
	  (message "Template provides placeholders; use C-c C-b to edit them."))))
    (eve--mark-dirty)
    (eve--render t)))

(defun eve-edit-broll-placeholders (segment)
  "Edit placeholder overrides for SEGMENT's b-roll metadata."
  (interactive (list (or (eve--segment-at-point)
			 (user-error "No segment at point"))))
  (let ((broll (eve--segment-broll segment)))
    (unless broll
      (user-error "Segment has no b-roll metadata")))
  (eve--record-state)
  (let* ((broll (copy-alist (eve--segment-broll segment)))
	 (existing (copy-tree (eve--aget 'placeholders broll)))
	 (template (eve--broll-template-data broll))
	 (template-defaults (copy-tree (eve--aget 'placeholders template)))
	 (overrides existing)
	 (keys (eve--placeholder-keys template-defaults existing)))
    (dolist (key keys)
      (let* ((default (eve--placeholder-get key template-defaults))
	     (current (eve--placeholder-get key overrides))
	     (initial (eve--stringify (or current default "")))
	     (prompt (if default
			 (format "Placeholder %s (default %s): "
				 key (eve--quote-value default))
		       (format "Placeholder %s: " key)))
	     (input (string-trim (read-string prompt initial))))
	(if (string-empty-p input)
	    (setq overrides (eve--placeholder-set key nil overrides))
	  (setq overrides (eve--placeholder-set key input overrides)))))
    (let ((continue t))
      (while continue
	(let ((new-key (string-trim (read-string "New placeholder key (blank to finish): "))))
	  (if (string-empty-p new-key)
	      (setq continue nil)
	    (let* ((existing (eve--placeholder-get new-key overrides))
		   (value (string-trim (read-string (format "Value for %s: " new-key)
						    (eve--stringify existing)))))
	      (if (string-empty-p value)
		  (setq overrides (eve--placeholder-set new-key nil overrides))
		(setq overrides (eve--placeholder-set new-key value overrides))))))))
    (if overrides
	(setf (alist-get 'placeholders broll nil t #'equal) overrides)
      (setf (alist-get 'placeholders broll nil t #'equal) nil))
    (eve--set-segment-broll segment broll)
    (eve--mark-dirty)
    (eve--render t)
    (message "Updated b-roll placeholders")))

(defun eve-toggle-words ()
  "Toggle display of per-word timings."
  (interactive)
  (setq eve--words-visible (not eve--words-visible))
  (eve--render t)
  (message "Word timings %s" (if eve--words-visible "enabled" "hidden")))

(defun eve-tag-fillers ()
  "Tag filler words/phrases in `eve--data'."
  (interactive)
  (eve--record-state)
  (let ((tagged (eve--apply-filler-tags)))
    (when (> tagged 0)
      (eve--mark-dirty))
    (eve--render t)
    (message (if (> tagged 0)
                 "Tagged %d filler word%s"
               "No filler words matched")
             tagged
             (if (= tagged 1) "" "s"))))

(defun eve-add-filler-at-point ()
  "Add the word at point to `eve-filler-phrases', then re-tag and render."
  (interactive)
  (let* ((info (eve--word-info-at-point))
         (word-text (plist-get info :word-text))
         (tokens (eve--normalize-word-tokens word-text))
         (phrase (and tokens (string-join tokens " "))))
    (when (or (null phrase) (string-empty-p phrase))
      (user-error "No word at point"))
    (unless (member phrase eve-filler-phrases)
      (setq eve-filler-phrases (append eve-filler-phrases (list phrase)))
      (condition-case nil
          (eve--customize-save-variable-preserving-windows
           'eve-filler-phrases eve-filler-phrases)
        (error nil)))
    (eve--record-state)
    (let ((tagged (eve--apply-filler-tags)))
      (when (> tagged 0)
        (eve--mark-dirty)))
    (eve--render t)
    (message "Added filler phrase: %s" phrase)))

(defun eve--save-filler-exception-phrase (phrase)
  "Persist PHRASE in `eve-filler-exception-phrases'."
  (unless (member phrase eve-filler-exception-phrases)
    (setq eve-filler-exception-phrases
          (append eve-filler-exception-phrases (list phrase)))
    (condition-case nil
        (eve--customize-save-variable-preserving-windows
         'eve-filler-exception-phrases eve-filler-exception-phrases)
      (error nil))))

(defun eve-add-filler-exception-region (start end)
  "Add region text START..END as a future filler exception phrase."
  (interactive "r")
  (let* ((raw (buffer-substring-no-properties start end))
         (tokens (eve--normalize-word-tokens raw))
         (phrase (and tokens (string-join tokens " "))))
    (when (or (null phrase) (string-empty-p phrase))
      (user-error "Region normalizes to empty string"))
    (eve--save-filler-exception-phrase phrase)
    (eve--record-state)
    (let ((start-pos (min start end))
          (end-pos (max start end)))
      (dolist (segment (eve--segments))
        (unless (eve--marker-p segment)
          (let* ((bounds (eve--segment-bounds (alist-get 'id segment)))
                 (render-data (and bounds (eve--segment-render-data segment)))
                 (spans (plist-get render-data :word-spans)))
            (when bounds
              (cl-loop for span in spans
                       for word = (nth 2 span)
                       for abs-start = (+ (car bounds) (nth 0 span))
                       for abs-end = (+ (car bounds) (nth 1 span))
                       when (and (< abs-start end-pos) (> abs-end start-pos))
                       do (eve--mark-word-non-filler word)))))))
    (eve--mark-dirty)
    (eve--render t)
    (message "Added filler exception phrase: %s" phrase)))

(defun eve-add-filler-exception-at-point ()
  "Add the word at point as a future filler exception phrase."
  (interactive)
  (let* ((info (eve--word-info-at-point))
         (word (plist-get info :word))
         (word-text (plist-get info :word-text))
         (tokens (eve--normalize-word-tokens word-text))
         (phrase (and tokens (string-join tokens " "))))
    (when (or (null phrase) (string-empty-p phrase))
      (user-error "No word at point"))
    (eve--save-filler-exception-phrase phrase)
    (eve--record-state)
    (eve--mark-word-non-filler word)
    (eve--mark-dirty)
    (eve--render t)
    (message "Added filler exception phrase: %s" phrase)))

(defun eve-add-filler-exception ()
  "Add region or word at point as a future filler exception phrase."
  (interactive)
  (if (use-region-p)
      (call-interactively #'eve-add-filler-exception-region)
    (call-interactively #'eve-add-filler-exception-at-point)))

(defun eve-add-filler-region (start end)
  "Add region text START..END to `eve-filler-phrases', then re-tag and render."
  (interactive "r")
  (let* ((raw (buffer-substring-no-properties start end))
         (tokens (eve--normalize-word-tokens raw))
         (phrase (and tokens (string-join tokens " "))))
    (when (or (null phrase) (string-empty-p phrase))
      (user-error "Region normalizes to empty string"))
    (unless (member phrase eve-filler-phrases)
      (setq eve-filler-phrases (append eve-filler-phrases (list phrase)))
      (condition-case nil
          (eve--customize-save-variable-preserving-windows
           'eve-filler-phrases eve-filler-phrases)
        (error nil)))
    (eve--record-state)
    (let ((tagged (eve--apply-filler-tags)))
      (when (> tagged 0)
        (eve--mark-dirty)))
    (eve--render t)
    (message "Added filler phrase: %s" phrase)))

(defun eve-dwim-filler ()
  "Add filler from region or word at point."
  (interactive)
  (if (use-region-p)
      (call-interactively #'eve-add-filler-region)
    (call-interactively #'eve-add-filler-at-point)))

(defun eve-delete-fillers ()
  "Mark every word tagged as filler as deleted."
  (interactive)
  (eve--record-state)
  (let ((count 0))
    (dolist (segment (eve--segments))
      (let ((words (alist-get 'words segment)))
        (when (listp words)
          (dolist (word words)
            (when (and (listp word)
                       (eve--word-filler-p word)
                       (not (eve--edit-deleted-p word)))
              (setq count (1+ count))
              (eve--set-edit-deleted word t))))))
    (when (> count 0)
      (eve--mark-dirty))
    (eve--render t)
    (message (if (> count 0)
                 (format "Deleted %d filler word%s" count
                         (if (= count 1) "" "s"))
               "No filler words to delete"))))

(defun eve-toggle-separator ()
  "Toggle a visual separator after the current segment."
  (interactive)
  (let ((id (eve--segment-id-at-point)))
    (unless id (user-error "No segment at point"))
    (eve--record-state)
    (if (member id eve--visual-separators)
	(setq eve--visual-separators (delete id eve--visual-separators))
      (cl-pushnew id eve--visual-separators :test #'equal))
    (eve--render t)))

(defun eve-insert-marker (&optional title)
  "Insert a marker segment before the current segment, prompting for TITLE only."
  (interactive)
  (let* ((current (eve--segment-at-point))
	 (segments (eve--segments))
	 (idx (if current
		  (cl-position current segments :test #'eq)
		(length segments)))
	 (default-title (or title ""))
	 (title (read-string "Marker title: " default-title))
	 (marker `((id . ,(eve--generate-marker-id))
		   (kind . "marker"))))
    (eve--record-state)
    (unless (string-empty-p title)
      (setf (alist-get 'title marker) title))
    (when current
      (let ((src (alist-get 'source current))
	    (st (alist-get 'start current)))
	(when src (setf (alist-get 'source marker) src))
	(when st (setf (alist-get 'start marker) st))))
    (let* ((head (cl-subseq segments 0 idx))
	   (tail (cl-subseq segments idx))
	   (new-segments (append head (list marker) tail)))
      (setf (alist-get 'segments eve--data) new-segments))
    (eve--mark-dirty)
    (eve--render t)
    (eve--goto-segment (alist-get 'id marker))
    (eve--update-focus-overlay)
    (eve--echo-segment-info)
    (message "Inserted marker")))

(defun eve-open-raw-json ()
  "View the underlying TJM JSON in the current window."
  (interactive)
  (unless buffer-file-name
    (user-error "Current buffer is not visiting a file"))
  (let* ((raw-name (eve--raw-buffer-name))
	 (buf (get-buffer-create raw-name)))
    (eve--populate-raw-buffer buf buffer-file-name)
    (switch-to-buffer buf)))

(defun eve-transcribe (directory)
  "Transcribe supported media files from DIRECTORY."
  (interactive "DDirectory to transcribe: ")
  (let* ((expanded-directory (file-name-as-directory
				      (expand-file-name directory)))
	 (files (seq-filter #'file-regular-p
			    (directory-files expanded-directory t
					     directory-files-no-dot-files-regexp)))
	 (media-files (eve--filter-media-files files)))
    (unless media-files
      (user-error "No media files found in %s" expanded-directory))
    (eve--transcribe-async media-files
			  (eve--infer-manifest-path media-files))))

(defun eve-dired-transcribe ()
  "Transcribe supported marked media files from the current Dired buffer."
  (interactive)
  (unless (derived-mode-p 'dired-mode)
    (user-error "Not in a Dired buffer"))
  (let ((media-files (eve--filter-media-files (dired-get-marked-files t))))
    (unless media-files
      (user-error "No media files selected"))
    (eve--transcribe-async media-files
                          (eve--infer-manifest-path media-files))))

(defun eve-play-segment (&optional segment)
  "Play SEGMENT using `eve-play-program', or the rendered section when on a marker."
  (interactive)
  (let ((seg (or segment (eve--segment-at-point)
                 (user-error "No segment at point"))))
    (if (eve--marker-p seg)
        (eve--play-marker seg)
      (let* ((source (alist-get 'source seg))
             (start (alist-get 'start seg))
             (end (alist-get 'end seg))
             (source-entry (eve--source-by-id source)))
        (unless source-entry
          (user-error "Unknown source id: %s" source))
        (let* ((file (alist-get 'file source-entry))
               (abs-file (expand-file-name file (file-name-directory buffer-file-name))))
          (unless (file-exists-p abs-file)
            (user-error "Source file not found: %s" abs-file))
          (let ((socket-path (concat (make-temp-name "/tmp/eve-mpv-") ".sock")))
            (eve--play-with-mpv abs-file start end socket-path)
            ;; Set state AFTER eve--play-with-mpv (teardown clears these)
            (setq eve--playback-source-segments (list seg))
            (setq eve--playback-mode 'source)
            (eve-playback-mode 1)
            (add-hook 'post-command-hook #'eve--playback-seek-hook nil t)
            (message "Playing %s [%s-%s]" file start end)))))))

(defun eve-play (&optional rendered)
  "Play source media, or with prefix arg play the rendered output.
\\[eve-play] plays the source; \\[universal-argument] \\[eve-play] plays rendered."
  (interactive "P")
  (if rendered
      (eve-play-rendered)
    (eve-play-source)))

(defun eve-play-source ()
  "Play source media from the current segment to end-of-file, tracking progress.
Collects all non-deleted segments from the same source, stores them in
`eve--playback-source-segments', and plays from the current segment's start."
  (interactive)
  (let* ((seg (or (eve--segment-at-point)
                  (user-error "No segment at point")))
         (_ (when (eve--marker-p seg)
              (user-error "Cannot play source from a marker segment")))
         (source-id (alist-get 'source seg))
         (source-entry (or (eve--source-by-id source-id)
                           (user-error "Unknown source: %s" source-id)))
         (file (alist-get 'file source-entry))
         (abs-file (expand-file-name file (file-name-directory buffer-file-name))))
    (unless (file-exists-p abs-file)
      (user-error "Source file not found: %s" abs-file))
    ;; Collect all non-deleted segments from the same source, sorted by start
    (let* ((all-segs (eve--segments))
           (source-segs
            (sort
             (seq-filter (lambda (s)
                           (and (equal (alist-get 'source s) source-id)
                                (not (eve--marker-p s))
                                (not (and eve-hide-deleted-mode
                                          (eve--edit-deleted-p s)))))
                         all-segs)
             (lambda (a b)
               (< (or (alist-get 'start a) 0.0)
                  (or (alist-get 'start b) 0.0))))))
      (let ((socket-path (concat (make-temp-name "/tmp/eve-mpv-") ".sock"))
            (start-time (or (alist-get 'start seg) 0.0)))
        (eve--play-with-mpv abs-file start-time nil socket-path)
        ;; Set state AFTER eve--play-with-mpv: that function calls
        ;; eve-stop-playback → eve--ipc-teardown which clears these vars.
        (setq eve--playback-source-segments source-segs)
        (setq eve--playback-mode 'source)
        ;; Enable playback minor mode: gives SPC a higher-priority binding
        (eve-playback-mode 1)
        ;; Add seek hook
        (add-hook 'post-command-hook #'eve--playback-seek-hook nil t)))))

(defun eve-play-rendered ()
  "Play the compiled rendered video, auto-compiling if stale.
After compilation (or immediately if up-to-date), starts tracked playback
using the rendered timeline from `eve--rendered-cumulative-times'."
  (interactive)
  (let* ((output (or (eve--default-output-file)
                     (user-error "Cannot determine output file path")))
         (tjm-file buffer-file-name)
         (_ (unless tjm-file (user-error "Buffer is not visiting a file")))
         (output-exists (file-exists-p output))
         (output-fresh (and output-exists
                            (not (time-less-p
                                  (nth 5 (file-attributes output))
                                  (nth 5 (file-attributes tjm-file)))))))
    (if output-fresh
        ;; Up-to-date: play directly
        (let ((socket-path (concat (make-temp-name "/tmp/eve-mpv-") ".sock")))
          (eve--play-with-mpv output 0.0 nil socket-path)
          ;; Set state AFTER eve--play-with-mpv (teardown clears these)
          (setq eve--playback-mode 'rendered)
          (setq eve--playback-time-map
                (eve--rendered-cumulative-times (eve--segments) eve-hide-deleted-mode))
          ;; Enable playback minor mode: gives SPC a higher-priority binding
          (eve-playback-mode 1)
          ;; Add seek hook
          (add-hook 'post-command-hook #'eve--playback-seek-hook nil t))
      ;; Stale or missing: save and compile first, play after
      (when (buffer-modified-p)
        (save-buffer))
      ;; Override compilation-finished to start IPC playback
      (let ((socket-path (concat (make-temp-name "/tmp/eve-mpv-") ".sock")))
        (setq-local eve--pending-play-socket socket-path)
        (eve--run-compile (eve--compile-command) output)
        ;; Wire controls (playback will start after compile finishes)
        (eve-playback-mode 1)
        (add-hook 'post-command-hook #'eve--playback-seek-hook nil t)))))

(defun eve-playback-pause-resume ()
  "Pause or resume mpv playback via IPC."
  (interactive)
  (if (process-live-p eve--mpv-process)
      (progn
        (eve--ipc-send '("cycle" "pause"))
        ;; Query the new state and report it
        (run-with-timer 0.05 nil
                        (lambda ()
                          (let ((paused (eve--ipc-get-property "pause")))
                            (message (if paused "Paused" "Resumed"))))))
    (message "No playback active")))

(defun eve-seek (seconds)
  "Seek mpv by SECONDS."
  (if (process-live-p eve--mpv-process)
      (eve--ipc-send (list "seek" (number-to-string seconds) "relative"))
    (message "No playback active")))

(defun eve-seek-short-backward ()
  (interactive)
  (eve-seek (- eve-seek-short-seconds)))

(defun eve-seek-short-forward ()
  (interactive)
  (eve-seek eve-seek-short-seconds))

(defun eve-seek-long-backward ()
  (interactive)
  (eve-seek (- eve-seek-long-backward-seconds)))

(defun eve-seek-long-forward ()
  (interactive)
  (eve-seek eve-seek-long-forward-seconds))

(defun eve--playback-seek-hook ()
  "Post-command hook: seek mpv to the segment at point during playback."
  (when (and (process-live-p eve--mpv-process)
             (timerp eve--playback-timer)
             (not (eq this-command 'eve-playback-pause-resume)))
    (let* ((seg (eve--segment-at-point))
           (seg-id (and seg (alist-get 'id seg)))
           ;; ID of segment currently highlighted by playback overlay
           (current-id (and (overlayp eve--playback-overlay)
                            (overlay-buffer eve--playback-overlay)
                            (get-text-property
                             (overlay-start eve--playback-overlay)
                             'eve-segment-id))))
      (when (and seg-id (not (equal seg-id current-id)))
        (let ((seek-time
               (pcase eve--playback-mode
                 ('source
                  (or (alist-get 'start seg) 0.0))
                 ('rendered
                  ;; Find the cumulative START time for this segment
                  (let ((prev 0.0))
                    (cl-loop for (id . end) in eve--playback-time-map
                             until (equal id seg-id)
                             do (setq prev end))
                    prev))
                 (_ nil))))
          (when seek-time
            (eve--ipc-send (list "seek"
                                 (number-to-string seek-time)
                                 "absolute"))))))))

(defun eve--ipc-connect (socket-path)
  "Connect to the mpv JSON IPC socket at SOCKET-PATH.
Stores the network process in `eve--ipc-process' and sets up a filter
to accumulate incoming data in the process buffer.  Returns the process."
  (let* ((buf (generate-new-buffer " *eve-ipc*"))
         (proc (condition-case err
                   (make-network-process
                    :name "eve-ipc"
                    :buffer buf
                    :family 'local
                    :service socket-path
                    :coding 'utf-8-unix
                    :noquery t)
                 (error
                  (kill-buffer buf)
                  (signal (car err) (cdr err))))))
    (setq eve--ipc-process proc)
    proc))

(defun eve--ipc-send (command)
  "Send COMMAND (a list of strings) as a JSON IPC message to mpv.
COMMAND is e.g. \='(\"get_property\" \"playback-time\").
Silently does nothing if `eve--ipc-process' is not live."
  (condition-case nil
      (when (and eve--ipc-process (process-live-p eve--ipc-process))
        (process-send-string
         eve--ipc-process
         (concat (json-encode `(("command" . ,command))) "\n")))
    (error nil)))

(defun eve--ipc-get-property (property)
  "Query mpv for PROPERTY via IPC and return the value, or nil on error.
Sends a get_property request and waits up to 0.1s for the response."
  (condition-case nil
      (when (and eve--ipc-process (process-live-p eve--ipc-process))
        (let ((buf (process-buffer eve--ipc-process)))
          ;; Clear existing output
          (with-current-buffer buf (erase-buffer))
          (eve--ipc-send (list "get_property" property))
          (accept-process-output eve--ipc-process 0.1)
          (with-current-buffer buf
            (condition-case nil
                (let* ((json-str (buffer-string))
                       ;; Take last complete JSON object (mpv may send multiple)
                       (last-obj (and (not (string-empty-p json-str))
                                      (json-parse-string
                                       (car (last (split-string
                                                   (string-trim json-str) "\n" t)))))))
                  (when (hash-table-p last-obj)
                    (gethash "data" last-obj)))
              (error nil)))))
    (error nil)))

(defun eve--ipc-teardown ()
  "Tear down mpv IPC: kill process, cancel timer, remove overlay, delete socket.
This is the single cleanup entry point called from all exit paths."
  ;; Cancel poll timer
  (when (timerp eve--playback-timer)
    (cancel-timer eve--playback-timer)
    (setq eve--playback-timer nil))
  ;; Remove playback overlay
  (when (overlayp eve--playback-overlay)
    (delete-overlay eve--playback-overlay)
    (setq eve--playback-overlay nil))
  ;; Kill IPC process and its buffer
  (when (process-live-p eve--ipc-process)
    (condition-case nil (kill-process eve--ipc-process) (error nil)))
  (when (and eve--ipc-process (process-buffer eve--ipc-process))
    (condition-case nil
        (kill-buffer (process-buffer eve--ipc-process))
      (error nil)))
  (setq eve--ipc-process nil)
  ;; Delete socket file (Unix sockets persist after process death)
  (when (and eve--ipc-socket-path (file-exists-p eve--ipc-socket-path))
    (condition-case nil (delete-file eve--ipc-socket-path) (error nil)))
  (setq eve--ipc-socket-path nil)
  ;; Clear remaining state
  (setq eve--playback-mode nil
        eve--playback-time-map nil
        eve--playback-source-segments nil)
  ;; Disable playback minor mode, restoring normal SPC behavior
  (eve-playback-mode -1)
  ;; Remove seek hook
  (remove-hook 'post-command-hook #'eve--playback-seek-hook t)
  ;; Restore Emacs frame geometry if video layout was active
  (eve--restore-frame-geometry))

(defun eve--playback-update-overlay (segment-id)
  "Highlight SEGMENT-ID with `eve-playback-face' as the currently-playing segment.
If SEGMENT-ID is nil, just remove any existing playback overlay."
  (when (overlayp eve--playback-overlay)
    (delete-overlay eve--playback-overlay)
    (setq eve--playback-overlay nil))
  (when segment-id
    (let ((bounds (eve--segment-bounds segment-id)))
      (when bounds
        (setq eve--playback-overlay
              (make-overlay (car bounds) (cdr bounds)))
        (overlay-put eve--playback-overlay 'face 'eve-playback-face)
        (overlay-put eve--playback-overlay 'priority 200)
        (overlay-put eve--playback-overlay 'evaporate t)))))

(defun eve--playback-start-timer ()
  "Start a 0.25-second repeating timer to poll mpv playback position."
  (when (timerp eve--playback-timer)
    (cancel-timer eve--playback-timer))
  (setq eve--playback-timer
        (run-with-timer 0.25 0.25 #'eve--playback-poll (current-buffer))))

(defun eve--playback-poll (buf)
  "Timer callback: query mpv position and update the playback overlay.
BUF is the eve-mode buffer to update."
  (when (buffer-live-p buf)
    (with-current-buffer buf
      ;; If mpv is dead, tear everything down
      (unless (process-live-p eve--mpv-process)
        (eve--ipc-teardown)
        (cl-return-from eve--playback-poll nil))
      ;; Query current playback time
      (let ((time (condition-case nil
                      (eve--ipc-get-property "playback-time")
                    (error nil))))
        (when (numberp time)
          ;; Map time to segment
          (let* ((seg-or-id
                  (pcase eve--playback-mode
                    ('source
                     (eve--playback-source-segment-at-time
                      time eve--playback-source-segments))
                    ('rendered
                     (eve--playback-rendered-segment-at-time
                      time eve--playback-time-map))
                    (_ nil)))
                 (segment-id
                  (pcase eve--playback-mode
                    ('source (and seg-or-id (alist-get 'id seg-or-id)))
                    ('rendered seg-or-id)
                    (_ nil))))
            ;; Only update if segment changed
            (let ((current-id (and (overlayp eve--playback-overlay)
                                   (overlay-buffer eve--playback-overlay)
                                   (get-text-property
                                    (overlay-start eve--playback-overlay)
                                    'eve-segment-id))))
              (unless (equal segment-id current-id)
                (eve--playback-update-overlay segment-id)
                (when segment-id
                  (save-excursion
                    (eve--goto-segment segment-id)))))))))))

(defun eve-stop-playback ()
  "Stop current playback process and clean up IPC state."
  (interactive)
  (let ((was-playing (process-live-p eve--mpv-process)))
    (eve--ipc-teardown)
    (when (process-live-p eve--mpv-process)
      (condition-case nil (kill-process eve--mpv-process) (error nil))
      (setq eve--mpv-process nil))
    (when was-playing
      (message "Playback stopped"))))

(defun eve-dwim-quit ()
  "Stop playback if active, then quit the window."
  (interactive)
  (when (process-live-p eve--mpv-process)
    (eve-stop-playback))
  (quit-window))

(defun eve--deferred-ipc-connect (buf &optional attempt)
  "Connect to mpv IPC socket in BUF, then start the playback poll timer.
Retries up to 5 times at 0.4-second intervals while mpv is alive and the
socket file has not appeared yet."
  (let ((attempt (or attempt 1)))
    (when (buffer-live-p buf)
      (with-current-buffer buf
        (cond
         ;; mpv died before we could connect — give up silently
         ((not (process-live-p eve--mpv-process)) nil)
         ;; Socket exists — connect and start timer
         ((file-exists-p (or eve--ipc-socket-path ""))
          (condition-case err
              (progn
                (eve--ipc-connect eve--ipc-socket-path)
                (eve--playback-start-timer))
            (error
             (message "eve: IPC connect failed: %s" (cadr err)))))
         ;; Socket not yet there — retry up to 5 times
         ((< attempt 5)
          (run-with-timer 0.4 nil #'eve--deferred-ipc-connect buf (1+ attempt)))
         ;; Gave up
          (t
           (message "eve: mpv IPC socket did not appear after %.1fs"
                    (* attempt 0.4))))))))

(defun eve--save-frame-geometry ()
  "Save the current Emacs frame geometry to `eve--saved-frame-geometry'.
Idempotent: does nothing if geometry is already saved."
  (unless eve--saved-frame-geometry
    (setq eve--saved-frame-geometry
          (list :left       (frame-parameter nil 'left)
                :top        (frame-parameter nil 'top)
                :width      (frame-pixel-width)
                :height     (frame-pixel-height)
                :fullscreen (frame-parameter nil 'fullscreen)))))

(defun eve--restore-frame-geometry ()
  "Restore the Emacs frame geometry saved by `eve--save-frame-geometry'.
No-op when nothing is saved."
  (when eve--saved-frame-geometry
    (let ((left  (plist-get eve--saved-frame-geometry :left))
          (top   (plist-get eve--saved-frame-geometry :top))
          (w     (plist-get eve--saved-frame-geometry :width))
          (h     (plist-get eve--saved-frame-geometry :height))
          (fs    (plist-get eve--saved-frame-geometry :fullscreen)))
      (when fs
        (set-frame-parameter nil 'fullscreen fs))
      (set-frame-position (selected-frame) (or left 0) (or top 0))
      (set-frame-size (selected-frame) (or w 800) (or h 600) t))
    (setq eve--saved-frame-geometry nil)))

(defun eve--compute-video-layout (workarea ratio)
  "Compute screen layout from WORKAREA and video RATIO.
WORKAREA is a list (X Y W H) as returned by `frame-monitor-workarea'.
RATIO is the fraction of height reserved for the video (e.g. 0.6).
Returns a plist:
  :mpv-geometry  percentage-based mpv --geometry string (Retina-safe)
  :emacs-x       Emacs frame left pixel
  :emacs-y       Emacs frame top pixel (outer frame — macOS title bar included)
  :emacs-w       Emacs frame pixel width
  :emacs-h       Emacs frame pixel height (text area)"
  (let* ((sx (nth 0 workarea))
         (sy (nth 1 workarea))
         (sw (nth 2 workarea))
         (sh (nth 3 workarea))
         (video-h  (floor (* sh ratio)))
         ;; mpv-geometry uses percentages: safe across Retina and non-Retina.
         ;; --macos-geometry-calculation=visible anchors to the usable area
         ;; (below menu bar), so +0+0 is top-left of the visible screen.
         (mpv-pct  (round (* 100 ratio)))
         (mpv-geo  (format "100%%x%d%%+0+0" mpv-pct))
         ;; For Emacs we use absolute pixels from frame-monitor-workarea.
         ;; emacs-y is where the Emacs outer frame should start.  We subtract
         ;; a hard-coded approximation of the macOS title-bar height (22 pt)
         ;; so that the TEXT AREA begins flush with mpv's bottom edge.
         ;; If the user has hidden the title bar, the overlap is harmless.
         (title-bar-approx 22)
         (mpv-bottom (+ sy video-h))
         (emacs-outer-y (max sy (- mpv-bottom title-bar-approx)))
         (emacs-h  (- (+ sy sh) mpv-bottom)))
    (list :mpv-geometry   mpv-geo
          :emacs-x        sx
          :emacs-y        emacs-outer-y
          :emacs-w        sw
          :emacs-h        emacs-h)))

(defun eve--apply-video-layout ()
  "Resize the Emacs frame to make room for the video window above it.
Saves the original geometry, detects fullscreen (skips with a message),
resizes Emacs to the bottom portion of the screen, and returns the mpv
geometry string for the video area.  Returns nil when skipped."
  (when eve-video-layout
    ;; Skip in native fullscreen — macOS blocks frame resize
    (let ((fs (frame-parameter nil 'fullscreen)))
      (if (memq fs '(fullscreen fullboth))
          (progn
            (message "eve-video-layout: skipped (Emacs is in native fullscreen)")
            nil)
        (eve--save-frame-geometry)
        (let* ((workarea (frame-monitor-workarea))
               (layout   (eve--compute-video-layout workarea eve-video-layout-ratio))
               (ex  (plist-get layout :emacs-x))
               (ey  (plist-get layout :emacs-y))
               (ew  (plist-get layout :emacs-w))
               (eh  (plist-get layout :emacs-h))
               (mpv-geo (plist-get layout :mpv-geometry)))
          (message "eve-layout: workarea=%S  mpv→%s  emacs→%dx%d at outer-y=%d"
                   workarea mpv-geo ew eh ey)
          (setq frame-resize-pixelwise t)
          (set-frame-position (selected-frame) ex ey)
          (set-frame-size (selected-frame) ew eh t)
          mpv-geo)))))

(defun eve--mpv-geometry-args (geometry-string)
  "Return a list of mpv args for GEOMETRY-STRING, or nil."
  (when geometry-string
    (list (format "--geometry=%s" geometry-string)
          ;; Use visible-area coordinates so +0+0 means top-left of the
          ;; usable screen (below menu bar) regardless of Retina scaling.
          "--macos-geometry-calculation=visible"
          "--no-border"
          "--ontop"
          "--force-window-position")))

(defun eve--play-with-mpv (file start end &optional ipc-socket)
  (unless (executable-find eve-play-program)
    (user-error "Executable '%s' not found" eve-play-program))
  (eve-stop-playback)
  (let* ((start-arg (format "--start=%f" (or start 0.0)))
         (end-arg (and end (format "--end=%f" end)))
         (ipc-arg (and ipc-socket
                       (format "--input-ipc-server=%s" ipc-socket)))
         (layout-args (eve--mpv-geometry-args (eve--apply-video-layout)))
         (args (append eve-play-args
                       layout-args
                       (list start-arg)
                       (when end-arg (list end-arg))
                       (when ipc-arg (list ipc-arg))
                       (list file)))
         (sentinel-buf (current-buffer)))
    (when ipc-socket
      (setq eve--ipc-socket-path ipc-socket))
    (setq eve--mpv-process
          (apply #'start-process "eve-mpv" "*eve-mpv*"
                 eve-play-program args))
    (set-process-sentinel eve--mpv-process
                          (lambda (_proc _event)
                            (when (buffer-live-p sentinel-buf)
                              (with-current-buffer sentinel-buf
                                (eve--ipc-teardown)
                                (setq eve--mpv-process nil)))))
    (when ipc-socket
      (run-with-timer 0.5 nil #'eve--deferred-ipc-connect sentinel-buf))
    ;; Reclaim OS-level focus for Emacs after mpv opens its window.
    ;; select-frame-set-input-focus alone is insufficient on macOS — mpv
    ;; grabs system focus when its NSWindow appears (~0.8–1 s after launch).
    ;; We fire at 1.5 s so mpv is fully open, then use osascript to activate
    ;; Emacs at the OS level, followed by select-frame-set-input-focus for the
    ;; correct buffer.
    (when eve-video-layout
      (run-with-timer 1.5 nil
                      (lambda ()
                        (condition-case nil
                            (start-process "eve-focus" nil "osascript"
                                           "-e" "tell application \"Emacs\" to activate")
                          (error nil))
                        (when (buffer-live-p sentinel-buf)
                          (with-current-buffer sentinel-buf
                            (select-frame-set-input-focus (selected-frame))))))))
  (message "Playing %s" (file-name-nondirectory file)))

(defun eve-validate (&optional silent)
  "Validate manifest contents, returning a list of issue strings.
If SILENT is non-nil, only produce messages when failures occur."
  (interactive)
  (let ((tolerance eve-validation-time-tolerance)
	issues)
    (dolist (segment (eve--segments))
      (unless (eve--marker-p segment)
	(let ((start (eve--coerce-number (alist-get 'start segment)))
	      (end (eve--coerce-number (alist-get 'end segment)))
	      (words (alist-get 'words segment))
	      (id (alist-get 'id segment)))
	  (when (or (null start) (null end))
	    (push (format "%s: missing start/end" (or id "<no-id>")) issues))
	  (when (and start end (<= (- end start) (- tolerance)))
	    (push (format "%s: start >= end (Δ%.2fs)" (or id "<no-id>") (- end start)) issues))
          (when (and start end eve-validation-suspicious-segment-duration
                     (> (- end start) eve-validation-suspicious-segment-duration))
            (push (format "%s: suspiciously long segment (%.1fs > %.1fs)"
                          (or id "<no-id>")
                          (- end start)
                          eve-validation-suspicious-segment-duration)
                  issues))
	  (dolist (word words)
	    (let ((w-start (eve--coerce-number (alist-get 'start word)))
		  (w-end (eve--coerce-number (alist-get 'end word)))
		  (token (alist-get 'token word)))
	      (when (or (null w-start) (null w-end))
		(push (format "%s: word '%s' missing timing"
			      (or id "<no-id>")
			      (or token ""))
		      issues))
	      (when (and w-start w-end (< (- w-end w-start) (- tolerance)))
		(push (format "%s: word '%s' start >= end (Δ%.2fs)"
			      (or id "<no-id>")
			      (or token "")
			      (- w-end w-start))
		      issues))
	      (when (and start w-start (> (- start w-start) tolerance))
		(push (format "%s: word '%s' before segment (Δ%.2fs)"
			      (or id "<no-id>")
			      (or token "")
			      (- start w-start))
		      issues))
	      (when (and end w-end (> (- w-end end) tolerance))
		(push (format "%s: word '%s' after segment (Δ%.2fs)"
			      (or id "<no-id>")
			      (or token "")
			      (- w-end end))
		      issues)))))))
    (setq issues (cl-remove-duplicates issues :test #'equal))
    (if issues
	(progn
	  (eve--display-validation issues)
	  (unless silent
	    (message "TJM validation reported %d issue(s)" (length issues))))
      (unless silent
	(message "TJM validation passed")))
    issues))

(defun eve--serialize-to-file (file)
  (let ((json-encoding-pretty-print t)
	(json-encoding-lisp-style-closings t))
    (let ((payload (json-encode eve--data)))
      (when (and file (file-exists-p file))
	(let ((backup (concat file "~")))
	  (condition-case _err
	      (copy-file file backup t t t)
	    (error nil))))
      (with-temp-buffer
	(insert payload)
	(unless (bolp) (insert "\n"))
	(write-region (point-min) (point-max) file nil 'silent)))))

(defun eve--segment-bounds (segment-id)
  (when segment-id
    (save-excursion
      (let ((start (eve--goto-segment segment-id)))
	(when start
	  (let ((end (or (next-single-property-change start 'eve-segment-id nil (point-max))
			 (point-max))))
	    (cons start end)))))))

(defun eve--update-focus-overlay ()
  (let* ((id (eve--segment-id-at-point))
	 (bounds (eve--segment-bounds id)))
    (when (overlayp eve--focus-overlay)
      (delete-overlay eve--focus-overlay)
      (setq eve--focus-overlay nil))
    (when bounds
      (setq eve--focus-overlay (make-overlay (car bounds) (cdr bounds)))
      (overlay-put eve--focus-overlay 'face
                   (if (display-graphic-p)
                       '((:background "#1a2e1a" :extend t
                          :box (:line-width (-3 . 0) :color "#5a9e5a"))
                         eve-current-segment-face)
                     'eve-current-segment-face))
      (overlay-put eve--focus-overlay 'priority -50)
      (overlay-put eve--focus-overlay 'evaporate t))))

(defun eve--ruler-clear-overlays ()
  "Delete all ruler overlays and clear the tracking list."
  (mapc #'delete-overlay eve--ruler-overlays)
  (setq eve--ruler-overlays nil))

(defun eve--ruler-create-overlays (milestones)
  "Create right-margin ruler overlays for MILESTONES.
MILESTONES is a list of (segment-id . formatted-time-string) from
`eve--ruler-milestones'.  Multiple milestones for the same segment are
joined into a single annotation."
  (when milestones
    ;; Group milestones by segment-id preserving first-occurrence order
    (let ((grouped nil)
          (seen nil))
      (dolist (m milestones)
        (let ((id (car m))
              (ts (cdr m)))
          (if (member id seen)
              (let ((entry (assoc id grouped)))
                (setcdr entry (concat (cdr entry) " " ts)))
            (push id seen)
            (push (cons id ts) grouped))))
      (setq grouped (nreverse grouped))
      ;; Create one overlay per group
      (dolist (group grouped)
        (let* ((id (car group))
               (label (cdr group))
               (bounds (eve--segment-bounds id)))
          (when bounds
            (let* ((o (make-overlay (car bounds) (1+ (car bounds)) nil t))
                   (text (propertize label 'face 'eve-ruler-face))
                   (margin-str (propertize " " 'display
                                           (list (list 'margin 'right-margin) text))))
              (overlay-put o 'before-string margin-str)
              (overlay-put o 'evaporate t)
              (overlay-put o 'priority -100)
              (push o eve--ruler-overlays))))))))

(defun eve--update-ruler ()
  "Recompute and redraw the right-margin timestamp ruler."
  (eve--ruler-clear-overlays)
  (let* ((segments (eve--segments))
         (hide-deleted eve-hide-deleted-mode)
         (cumulative (eve--rendered-cumulative-times segments hide-deleted))
         (milestones (eve--ruler-milestones cumulative eve-ruler-interval)))
    (setq eve--ruler-total-duration
          (eve--rendered-total-duration segments hide-deleted))
    (force-mode-line-update)
    (when milestones
      (setq-local right-margin-width 12)
      (eve--apply-visual-wrap)
      (eve--ruler-create-overlays milestones))))

(defun eve--ruler-mode-line-string ()
  "Return mode-line segment showing total rendered duration, or empty string."
  (if (> eve--ruler-total-duration 0.0)
      (concat " " (eve--format-ruler-time eve--ruler-total-duration))
    ""))

(defun eve--mode-line-status ()
  "Return the combined eve status string for the mode-line.
Shows duration and render state right after the buffer name."
  (let ((dur (eve--ruler-mode-line-string))
        (render
         (pcase eve--render-status
           ('speculative
            (propertize " \u27f3" 'face 'warning
                        'help-echo "Speculative pre-render in progress"))
           ('compiling
            (let* ((prog eve--render-progress)
                   (n (car-safe prog))
                   (m (cdr-safe prog))
                   (eta eve--render-eta)
                   (parts (list (propertize " \u25b6" 'face 'success)))
                   (nums (when (and n m)
                           (format "%d/%d" n m)))
                   (time (when (and eta (> eta 0))
                           (format "~%ds" (round eta)))))
              (when nums (push nums parts))
              (when time (push time parts))
              (mapconcat #'identity (nreverse parts) "")))
           ('cached
            (propertize " \u2713" 'face 'success
                        'help-echo "All segments cached"))
           (_ nil))))
    (concat dur (or render ""))))

(defun eve--segment-summary (segment)
  (when segment
    (if (eve--marker-p segment)
	(let* ((id (eve--stringify (alist-get 'id segment)))
	       (title (string-trim (eve--stringify (alist-get 'title segment))))
	       (display (format "# %s"
				(if (string-empty-p title) "(Untitled marker)" title))))
	  (format "[%s] %s" id display))
      (let* ((id (eve--stringify (alist-get 'id segment)))
	     (speaker (eve--stringify (alist-get 'speaker segment)))
	     (source (eve--stringify (alist-get 'source segment)))
	     (start (or (eve--display-time (alist-get 'start segment)) ""))
	     (end (or (eve--display-time (alist-get 'end segment)) ""))
	     (tags (eve--segment-tags segment))
	     (notes (eve--stringify (eve--segment-notes segment)))
	     (broll (eve--segment-broll segment))
	     (parts (delq nil (list (format "[%s]" id)
				    speaker
				    (format "(%s %s-%s)" source start end)
				    (when tags (format "tags:%s"
						       (mapconcat #'eve--stringify tags ",")))
				    (when (and broll (alist-get 'file broll))
				      (format "broll:%s" (alist-get 'file broll)))
				    notes))))
	(string-join (cl-remove-if #'string-empty-p (mapcar #'string-trim parts)) " ")))))

(defun eve--echo-segment-info ()
  (let* ((segment (eve--segment-at-point))
	 (id (and segment (alist-get 'id segment))))
    (when (and id (not (equal id eve--last-echo-id)))
      (setq eve--last-echo-id id)
      (let (message-log-max)
        (message "%s" (eve--segment-summary segment))))))

(defun eve--context-panels-sync-bottom-panel ()
  "Keep the segment bottom panel aligned with source-buffer point."
  (when (and (derived-mode-p 'eve-mode)
             (boundp 'context-panels-bottom-panel-buffer)
             (buffer-live-p context-panels-bottom-panel-buffer)
             (get-buffer-window context-panels-bottom-panel-buffer t))
    (when-let* ((segment-id (eve--segment-id-at-point)))
      (let ((source (current-buffer))
            (bottom context-panels-bottom-panel-buffer))
        (with-current-buffer bottom
          (when (and (derived-mode-p 'eve-segment-panel-mode)
                     (not (equal eve-segment-panel-segment-id segment-id)))
            (setq eve-segment-panel-source-buffer source)
            (setq eve-segment-panel-segment-id (eve--stringify segment-id))
            (setq eve-segment-panel-word-index 0)
            (setq eve-segment-panel-selection-anchor nil)
            (eve--segment-panel-refresh)))))))

(defun eve--post-command ()
  (eve--update-focus-overlay)
  (eve--echo-segment-info)
  (eve--context-panels-sync-bottom-panel))

(defun eve--display-validation (issues)
  (let ((buf (get-buffer-create "*TJM Validation*")))
    (with-current-buffer buf
      (setq buffer-read-only nil)
      (erase-buffer)
      (insert "Validation issues:\n\n")
      (dolist (issue issues)
	(insert " - " issue "\n"))
      (goto-char (point-min))
      (setq buffer-read-only t))
    (display-buffer buf))
  issues)

(provide 'eve)

;;; eve.el ends here
