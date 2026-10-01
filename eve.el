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

(declare-function image-file-name-p "image-mode" (filename))
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

(defcustom eve-ruler-interval 30.0
  "Interval in seconds between right-margin timestamp ruler markers."
  :type 'float
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
    (define-key map "p" #'eve-previous-segment)
    (define-key map "J" #'eve-move-segment-down)
    (define-key map "K" #'eve-move-segment-up)
    ;; Editing
    (define-key map "d" #'eve-delete-word)
    (define-key map "D" #'eve-delete-segment)
    (define-key map "s" #'eve-split-segment)
    (define-key map "|" #'eve-split-segment)
    (define-key map "m" #'eve-merge-with-next)
    (define-key map ")" #'eve-merge-with-next)
    (define-key map "u" #'eve-undo)
    (define-key map (kbd "C-r") #'eve-redo)
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
    (define-key map (kbd "C-c C-r") #'eve-reload)
    (define-key map (kbd "C-c C-o") #'eve-open-raw-json)
    (define-key map (kbd "C-c C-h") #'eve-hide-deleted-mode)
    (define-key map (kbd "C-c C-w") #'eve-toggle-words)
    (define-key map (kbd "C-c C-l") #'eve-ruler-mode)
    (define-key map (kbd "C-c C-t") #'eve-transcribe)
    (define-key map (kbd "C-c C-p") #'eve-play-rendered)
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
  "Return segments following MARKER until the next marker."
  (let* ((segments (eve--segments))
	 (start (cl-position marker segments :test #'eq)))
    (when start
      (let (result)
	(cl-loop for idx from (1+ start) below (length segments)
		 for seg = (nth idx segments)
		 until (eve--marker-p seg)
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

(defun eve--transcribe-finished (process event output-path)
  "Handle completion of transcribe PROCESS EVENT for OUTPUT-PATH."
  (eve--transcribe-finished-state)
  (let ((buffer (process-buffer process)))
    (if (and (eq (process-status process) 'exit)
             (= (process-exit-status process) 0))
        (progn
          (message "eve transcribe finished: %s" output-path)
          (find-file output-path))
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
                           (when eve-transcribe-tag-fillers
                             '("--tag-fillers")))))
    (with-current-buffer buffer
      (let ((inhibit-read-only t))
        (erase-buffer)))
    (make-process :name "eve-transcribe"
                  :buffer buffer
                  :command command
                  :noquery t
                  :sentinel (lambda (process event)
                              (eve--transcribe-finished
                               process event output-file)))
    (eve--transcribe-started)
    (message "Started eve transcribe -> %s" output-file)))

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

(defcustom eve-auto-render t
  "Whether to speculatively pre-render changed segments on save.
When non-nil (the default), saving a TJM manifest triggers a background
`eve text-edit --segments <changed-ids>' to warm the cache.  The next
`eve-compile' (\\`C-c C-c') then only needs the final concat."
  :type 'boolean
  :group 'eve)

(defcustom eve-auto-render-idle-seconds 2.0
  "Idle time in seconds before a background speculative render fires after save."
  :type 'float
  :group 'eve)

(defun eve--auto-render-changed-segment-ids ()
  "Return a list of segment IDs that differ from the cache, or nil."
  (when (and buffer-file-name (file-exists-p buffer-file-name))
    (let ((analysis (eve--dry-run-analysis buffer-file-name eve-compile-quality)))
      (when analysis
        (let ((ids (plist-get analysis :changed_ids)))
          (when (and (arrayp ids) (> (length ids) 0))
            (append ids nil)))))))

(defun eve--auto-render-fire ()
  "Launch the speculative background render for the current buffer."
  (when (and (derived-mode-p 'eve-mode)
             eve-auto-render
             buffer-file-name
             (file-exists-p buffer-file-name))
    (when (and eve--auto-render-process
               (process-live-p eve--auto-render-process))
      (delete-process eve--auto-render-process)
      (setq eve--auto-render-process nil))
    (let ((seg-ids (eve--auto-render-changed-segment-ids))
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
               (full-cmd (append base-cmd seg-args)))
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
  (when eve--auto-render-timer
    (cancel-timer eve--auto-render-timer))
  (setq eve--auto-render-timer
        (run-with-idle-timer eve-auto-render-idle-seconds nil
                             #'eve--auto-render-fire)))

(defun eve--auto-render-teardown ()
  "Clean up speculative render state for the current buffer."
  (when eve--auto-render-timer
    (cancel-timer eve--auto-render-timer)
    (setq eve--auto-render-timer nil))
  (when (and eve--auto-render-process
             (process-live-p eve--auto-render-process))
    (delete-process eve--auto-render-process)
    (setq eve--auto-render-process nil)))

(defun eve--play-marker (segment)
  "Play the compiled video associated with marker SEGMENT, compiling if needed."
  (let* ((title (eve--stringify (alist-get 'title segment)))
	 (slug (eve--slugify title))
	 (output (eve--output-path slug)))
    (if (and output (file-exists-p output))
	(eve--play-file output)
      (progn
	(message "No compiled output for '%s'; compiling section..." (or title slug))
	(eve-compile)))))

(when (featurep 'hydra)
  (defhydra eve-hydra (:hint nil :color teal)
    "
Navigation        Edit              Fillers           Playback
──────────────   ──────────────   ──────────────   ──────────────────────
_j_/_k_  next/prev  _d_ del word      _f_ filler        _SPC_/_RET_ play
_n_/_p_  next/prev  _D_ del segment   _F_ del fillers   _<left>_/_<right>_ seek ±5s
_J_/_K_  move up/dn _s_/|  split                        _S-<left>_/_S-<right>_ ±30s/10s
                  _m_/_)_ merge                       During playback:
Metadata          _u_ undo          Structure           _SPC_ pause/resume
──────────────   _C-r_ redo         ___  separator       _q_ stop
_t_ toggle tag    _e_ edit (DWIM)   _C-RET_ separator
_b_ edit b-roll                    _o_ insert marker
_B_ b-roll cont   Global C-c C-x
_i_ speaker       ──────────────
_r_ timestamps    _C-c C-c_ compile    _C-c C-w_ word timings
                  _C-c C-v_ validate   _C-c C-l_ ruler mode
_g_ reload        _C-c C-r_ reload     _C-c C-h_ hide deleted
_q_ quit          _C-c C-o_ raw JSON   _C-c C-t_ transcribe
_?_ this help     _C-c C-p_ play rendered  _C-c C-s_ play source
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
    ("C-c C-r" eve-reload)
    ("C-c C-o" eve-open-raw-json)
    ("C-c C-h" eve-hide-deleted-mode)
    ("C-c C-w" eve-toggle-words)
    ("C-c C-l" eve-ruler-mode)
    ("C-c C-t" eve-transcribe)
    ("C-c C-p" eve-play-rendered)
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
             "u undo  C-r redo  f filler  F del-fillers  "
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

(defun eve--apply-filler-tags ()
  "Apply filler tags to words in all segments.
Returns the count of words newly tagged. Does not render, message, mark dirty,
or record undo state."
  (let ((phrases (eve--filler-phrase-list))
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
              (let ((match-len
                     (cl-loop for phrase in phrases
                              when (eve--phrase-matches-at-p normalized-words phrase i)
                              return (length phrase)
                              finally return nil)))
                (if match-len
                    (progn
                      (cl-loop for j from 0 below match-len
                               do (let ((word (nth (+ i j) words)))
                                    (unless (equal (eve--edit-kind word) "filler")
                                      (eve--set-word-edit-kind word "filler")
                                      (setq tagged (1+ tagged)))))
                      (setq i (+ i match-len)))
                  (setq i (1+ i)))))))))
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
  (eve--set-edit-field segment 'broll broll))

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
     ((string-match-p "\\`[0-9]+:\(?:[0-9]+:\)?[0-9]+\\(?:\\.[0-9]+\\)?\\'" trim)
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

(defun eve--ruler-reapply-margins ()
  "Reapply right-margin width for the ruler when window configuration changes."
  (when eve-ruler-mode
    (set-window-margins (selected-window)
                        (car (window-margins (selected-window)))
                        right-margin-width)))

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
          (eve--update-ruler))
        (add-hook 'window-configuration-change-hook
                  #'eve--ruler-reapply-margins nil t))
    (eve--ruler-clear-overlays)
    (setq-local right-margin-width 0)
    (dolist (win (get-buffer-window-list nil nil t))
      (set-window-margins win (car (window-margins win)) 0))
    (remove-hook 'window-configuration-change-hook
                 #'eve--ruler-reapply-margins t)))

;;;###autoload
(define-derived-mode eve-mode special-mode "EVE"
  "Major mode for Textual Join Manifest files (.tjm.json)."
  (eve-mode--ensure-json-library)
  (setq-local indent-tabs-mode nil)
  (setq-local buffer-read-only t)
  (setq-local truncate-lines nil)
  (setq-local word-wrap t)
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
  (add-hook 'window-configuration-change-hook #'eve--apply-visual-wrap nil t)
  (add-hook 'kill-buffer-hook #'eve--remove-wrap nil t)
  (add-hook 'kill-buffer-hook #'eve--ipc-teardown nil t)
  (add-hook 'kill-buffer-hook #'eve--auto-render-teardown nil t)
  (add-hook 'after-save-hook #'eve--auto-render-after-save nil t)
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

(defun eve--source-by-id (source-id)
  (seq-find (lambda (item)
	      (string= (alist-get 'id item) source-id))
	    (alist-get 'sources eve--data)))

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

(defun eve--rendered-segment-duration (segment hide-deleted)
  "Return rendered duration in seconds for SEGMENT."
  (if (or (eve--marker-p segment)
          (and hide-deleted (eve--edit-deleted-p segment)))
      0.0
    (let* ((words (or (alist-get 'words segment) '()))
           (visible-words (if hide-deleted
                              (seq-remove #'eve--edit-deleted-p words)
                            words)))
      (if (null visible-words)
          0.0
        (let* ((first-word (car visible-words))
               (last-word (car (last visible-words)))
               (first-start (or (alist-get 'start first-word) 0.0))
               (last-end (or (alist-get 'end last-word) 0.0))
               (raw-duration (max 0.0 (- last-end first-start)))
               (max-gap (or eve-preserve-gaps-max 0.0))
               (seg-start (or (alist-get 'start segment) first-start))
               (leading-gap (- first-start seg-start))
               (seg-end (or (alist-get 'end segment) last-end))
               (trailing-gap (- seg-end last-end)))
          (when (> leading-gap max-gap)
            (setq raw-duration (- raw-duration (- leading-gap max-gap))))
          (when (> trailing-gap max-gap)
            (setq raw-duration (- raw-duration (- trailing-gap max-gap))))
          (max 0.0 raw-duration))))))

(defun eve--rendered-total-duration (segments hide-deleted)
  "Return total rendered duration in seconds for SEGMENTS."
  (cl-reduce #'+ (mapcar (lambda (seg)
                           (eve--rendered-segment-duration seg hide-deleted))
                         segments)
             :initial-value 0.0))

(defun eve--rendered-cumulative-times (segments hide-deleted)
  "Return alist of (ID . cumulative-end-time) for each segment."
  (let ((cumulative 0.0)
        result)
    (dolist (segment segments)
      (let ((id (alist-get 'id segment)))
        (setq cumulative (+ cumulative
                            (eve--rendered-segment-duration segment hide-deleted)))
        (push (cons id cumulative) result)))
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
				 (when (equal (eve--edit-kind word) "filler")
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

(defun eve--apply-visual-wrap (&rest _ignore)
  (when (derived-mode-p 'eve-mode)
    (let ((fill (or fill-column (default-value 'fill-column))))
      (dolist (window (get-buffer-window-list (current-buffer) nil t))
	(let* ((margins (window-margins window))
	       (left (or (car margins) 0))
	       (right (or (cdr margins) 0))
	       (total (eve--window-total-width window))
	       (desired (if (> total fill) (- total fill) 0))
	       (current-right right))
	  (unless (= desired current-right)
	    (unless (window-parameter window 'eve--saved-margins)
	      (set-window-parameter window 'eve--saved-margins
				    (cons left right)))
	    (set-window-margins window left desired)
	    (set-window-parameter window 'eve--wrapped t)))
	(eve--apply-fringe-width window 0 0)))))

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

(defun eve--cleanup-wrap ()
  (dolist (window (window-list))
    (unless (with-current-buffer (window-buffer window)
	      (derived-mode-p 'eve-mode))
      (when (or (window-parameter window 'eve--saved-margins)
		(window-parameter window 'eve--saved-fringes))
	(eve--restore-wrap window)))))

(add-hook 'window-configuration-change-hook #'eve--cleanup-wrap)

(defun eve--segment-index (segment-id segments)
  "Return the index of SEGMENT-ID within SEGMENTS."
  (when segment-id
    (cl-position segment-id segments
		 :test #'equal
		 :key (lambda (segment)
			(alist-get 'id segment)))))

(defun eve--move-by-segments (delta)
  "Move point DELTA segments forward (or backward when negative)."
  (let* ((segments (eve--segments))
	 (count (length segments)))
    (unless segments
      (user-error "No segments available"))
    (let* ((current-id (eve--segment-id-at-point))
	   (current-index (or (eve--segment-index current-id segments)
			      (if (> delta 0) -1 count)))
	   (target-index (+ current-index delta)))
      (if (or (< target-index 0) (>= target-index count))
	  (message (if (> delta 0)
		       "Already at last segment"
		     "Already at first segment"))
	(eve--goto-segment (alist-get 'id (nth target-index segments)))
	(eve--update-focus-overlay)
	(eve--echo-segment-info)
	t))))

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

(defun eve-merge-with-next ()
  "Merge the segment at point with the next segment in the manifest."
  (interactive)
  (let* ((segment (or (eve--segment-at-point)
		      (user-error "No segment at point")))
	 (segments (eve--segments))
	 (idx (cl-position segment segments :test #'eq)))
    (unless idx
      (error "Unable to determine segment index"))
    (when (eve--marker-p segment)
      (user-error "Cannot merge marker segments"))
    (let ((next (nth (1+ idx) segments)))
      (unless next
	(user-error "Already at last segment"))
      (when (eve--marker-p next)
	(user-error "Cannot merge into a marker segment"))
      (let* ((source (alist-get 'source segment))
	     (next-source (alist-get 'source next))
	     (source-str (eve--stringify source))
	     (next-source-str (eve--stringify next-source)))
	(when (and source-str next-source-str
		   (not (string= source-str next-source-str)))
	  (user-error "Segments use different sources (%s vs %s)" source-str next-source-str))
	(when (and (null source) next-source)
	  (setf (alist-get 'source segment) next-source)))
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
	     (start-a (eve--coerce-number (alist-get 'start segment)))
	     (end-a (eve--coerce-number (alist-get 'end segment)))
	     (start-b (eve--coerce-number (alist-get 'start next)))
	     (end-b (eve--coerce-number (alist-get 'end next)))
	     (note-a (eve--stringify (eve--segment-notes segment)))
	     (note-b (eve--stringify (eve--segment-notes next)))
	     (tag-a (copy-sequence (eve--segment-tags segment)))
	     (tag-b (copy-sequence (eve--segment-tags next)))
	     (next-id (alist-get 'id next))
	     (next-had-broll (eve--segment-broll next)))
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
	  (let ((starts (seq-filter #'numberp (list start-a start-b)))
		(ends (seq-filter #'numberp (list end-a end-b))))
	    (when starts
	      (setf (alist-get 'start segment) (apply #'min starts)))
	    (when ends
	      (setf (alist-get 'end segment) (apply #'max ends)))))
	(setq eve--visual-separators
	      (cl-remove (eve--stringify next-id) eve--visual-separators
			 :test #'string= :count 1))
	(setf (alist-get 'segments eve--data)
	      (cl-remove next segments :test #'eq :count 1))
	(eve--mark-dirty)
	(let ((merged-id (alist-get 'id segment)))
	  (eve--render t)
	  (when merged-id
	    (eve--goto-segment merged-id)
	    (eve--update-focus-overlay)
	    (eve--echo-segment-info))
	  (message "Merged segment %s with %s%s"
		   merged-id
		   (eve--stringify next-id)
		   (if next-had-broll
		       " (review b-roll metadata)"
		     "")))))))

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

(defun eve-edit-broll (segment)
  "Edit b-roll metadata for SEGMENT."
  (interactive (list (or (eve--segment-at-point)
			 (user-error "No segment at point"))))
  (eve--record-state)
  (let* ((broll (copy-alist (eve--segment-broll segment)))
	 (file (read-string "B-roll file (blank to clear): "
			    (eve--stringify (alist-get 'file broll))))
	 (mode (read-string "Mode (overlay/replace/pip): "
			    (alist-get 'mode broll)))
	 (audio-default (or (eve--stringify (alist-get 'audio broll))
			    "source"))
	 (audio (read-string "Audio (source/mute/broll): " audio-default))
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
	 (still-input (if (string-empty-p file)
			  nil
			(string-trim (downcase
				      (read-string
				       (format "Treat as still image? (y/n) [%s]: "
					       (if still-default "y" "n"))
				       (if still-default "y" "n"))))))
	 (still (cond
		 ((null still-input) still-default)
		 ((member still-input '("y" "yes")) t)
		 ((member still-input '("n" "no")) nil)
		 ((string-empty-p still-input) still-default)
		 (t (user-error "Enter y or n for still image"))))
	 (continue-existing (alist-get 'continue broll))
	 (continue-default (and continue-existing t))
	 (continue-input (if (string-empty-p file)
			     nil
			   (string-trim (downcase
					 (read-string
					  (format "Continue from previous segment? (y/n) [%s]: "
						  (if continue-default "y" "n"))
					  (if continue-default "y" "n"))))))
	 (continue (cond
		    ((null continue-input) continue-default)
		    ((member continue-input '("y" "yes")) t)
		    ((member continue-input '("n" "no")) nil)
		    ((string-empty-p continue-input) continue-default)
		    (t (user-error "Enter y or n for continue flag"))))
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
          (customize-save-variable 'eve-filler-phrases eve-filler-phrases)
        (error nil)))
    (eve--record-state)
    (let ((tagged (eve--apply-filler-tags)))
      (when (> tagged 0)
        (eve--mark-dirty)))
    (eve--render t)
    (message "Added filler phrase: %s" phrase)))

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
          (customize-save-variable 'eve-filler-phrases eve-filler-phrases)
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
                       (equal (eve--edit-kind word) "filler")
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
  (let* ((seg (or segment (eve--segment-at-point)
		  (user-error "No segment at point")))
	 (marker? (eve--marker-p seg)))
    (when marker?
      (eve--play-marker seg)
      (cl-return-from eve-play-segment nil))
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
	  (message "Playing %s [%s-%s]" file start end))))))

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
      (dolist (win (get-buffer-window-list nil nil t))
        (set-window-margins win (car (window-margins win)) 12))
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

(defun eve--post-command ()
  (eve--update-focus-overlay)
  (eve--echo-segment-info))

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
