;;; eve-test.el --- Tests for video TJM mode -*- lexical-binding: t; -*-

;;; Commentary:
;; Exercises editing primitives for the text-driven video major mode.

;;; Code:

(require 'ert)
(require 'cl-lib)

(defconst eve-test--root
  (file-name-directory
   (directory-file-name
    (file-name-directory (or load-file-name buffer-file-name))))
  "Package root for TJM tests.")

;; Provide minimal stubs when the optional dependencies are absent during tests.
(unless (fboundp 'defhydra)
  (defmacro defhydra (&rest _r)
    nil))
(unless (featurep 'hydra)
  (provide 'hydra))

(add-to-list 'load-path eve-test--root)
(require 'eve)

(defconst eve-test--sample-data
  '((version . 1)
    (sources . (((id . "clip") (file . "clip.mp4"))))
    (segments .
	      (((id . "seg-1")
		(source . "clip")
		(start . 1.0)
		(end . 4.0)
		(text . "alpha beta gamma delta")
		(broll . nil)
		(words .
		       (((start . 1.0) (end . 1.5) (token . "alpha"))
			((start . 1.5) (end . 2.0) (token . "beta"))
			((start . 2.0) (end . 3.0) (token . "gamma"))
			((start . 3.0) (end . 4.0) (token . "delta"))))))))
  "In-memory manifest used across tests.")

(defmacro eve-test-with-buffer (&rest body)
  "Execute BODY inside a fresh TJM buffer backed by the sample manifest."
  (declare (indent 0))
  `(with-temp-buffer
     (let ((buffer-file-name nil))
       (insert "{}")
       (goto-char (point-min))
       (eve-mode)
       (setq-local eve--data (copy-tree eve-test--sample-data t))
       (eve--render)
       ,@body)))

(defun eve-test--segment-tokens (segment)
  "Return the token list for SEGMENT."
  (mapcar (lambda (word)
	    (alist-get 'token word))
	  (alist-get 'words segment)))

(defun eve-test--face-contains-p (face-prop face)
  "Return non-nil when FACE-PROP includes FACE."
  (cond
   ((null face-prop) nil)
   ((eq face-prop face) t)
   ((consp face-prop)
    (or (eve-test--face-contains-p (car face-prop) face)
        (eve-test--face-contains-p (cdr face-prop) face)))
   (t nil)))

(defun eve-test--span-has-face-p (start end face)
  "Return non-nil when every character in START..END includes FACE."
  (cl-loop for pos from start below end
           always (eve-test--face-contains-p
                   (get-text-property pos 'face)
                   face)))

(defun eve-test--match-range (text)
  "Return the buffer range for the first match of TEXT."
  (save-excursion
    (goto-char (point-min))
    (when (search-forward text nil t)
      (cons (- (point) (length text)) (point)))))

(ert-deftest eve-visual-features-render-filler-word-uses-filler-face ()
  "Nested filler metadata highlights the visible word span."
  (eve-test-with-buffer
   (let* ((segment (car (eve--segments)))
          (word (nth 1 (alist-get 'words segment)))
          range)
     (setf (alist-get 'edit word) '((kind . "filler")))
     (eve--render)
     (setq range (eve-test--match-range "beta"))
     (should range)
     (should (eve-test--span-has-face-p (car range) (cdr range) 'eve-filler-face)))))

(ert-deftest eve-visual-features-render-legacy-word-kind-does-not-highlight-filler ()
  "Top-level word kind no longer drives filler highlighting."
  (eve-test-with-buffer
   (let* ((segment (car (eve--segments)))
          (words (alist-get 'words segment))
          (word (nth 1 words))
          range)
     (setf (nth 1 words) (eve--alist-set word 'kind "filler"))
     (eve--render)
     (setq range (eve-test--match-range "beta"))
     (should range)
      (should-not (eve-test--span-has-face-p (car range) (cdr range) 'eve-filler-face)))))

(ert-deftest eve-visual-features-prefers-segment-edit-broll-over-legacy-root ()
  "Segment rendering prefers nested edit b-roll metadata over legacy root data."
  (eve-test-with-buffer
   (let ((segment (car (eve--segments))))
     (setf (alist-get 'broll segment) '((file . "legacy.mp4")))
     (setf (alist-get 'edit segment) '((broll . ((file . "nested.mp4")))))
     (eve--render)
     (should (string-match-p "\\[b-roll\\] file=nested\\.mp4"
                             (buffer-substring-no-properties (point-min) (point-max)))))))

(ert-deftest eve-visual-features-render-deleted-segment-respects-hide-mode ()
  "Deleted segments hide or strike through based on hide mode."
  (eve-test-with-buffer
   (let* ((segment (car (eve--segments)))
          range)
     (setf (alist-get 'edit segment) '((deleted . t)))
     (setq-local eve-hide-deleted-mode t)
     (eve--render)
     (should (equal (buffer-string) ""))
     (setq-local eve-hide-deleted-mode nil)
     (eve--render)
     (setq range (eve-test--match-range "alpha beta gamma delta"))
     (should range)
     (should (eve-test--span-has-face-p (car range) (cdr range) 'eve-deleted-face)))))

(ert-deftest eve-visual-features-render-deleted-word-respects-hide-mode ()
  "Deleted words hide or strike through based on hide mode."
  (eve-test-with-buffer
   (let* ((segment (car (eve--segments)))
          (word (nth 1 (alist-get 'words segment)))
          range)
     (setf (alist-get 'edit word) '((deleted . t)))
     (setq-local eve-hide-deleted-mode t)
     (eve--render)
     (should (equal (buffer-string) "alpha gamma delta"))
     (setq-local eve-hide-deleted-mode nil)
     (eve--render)
     (setq range (eve-test--match-range "beta"))
     (should range)
     (should (eve-test--span-has-face-p (car range) (cdr range) 'eve-deleted-face)))))

(ert-deftest eve-visual-features-delete-word-toggles-nested-deleted-flag ()
  "Deleting a word toggles nested edit metadata without rewriting the segment."
  (eve-test-with-buffer
   (setq-local eve-hide-deleted-mode nil)
   (should (eve--goto-segment "seg-1"))
   (should (eve--goto-word-by-index "seg-1" 1))
   (forward-char 1)
   (call-interactively #'eve-delete-word)
   (let* ((segments (eve--segments))
          (segment (car segments))
          (word (nth 1 (alist-get 'words segment))))
     (should (= 1 (length segments)))
     (should (equal (alist-get 'id segment) "seg-1"))
     (should (equal (eve-test--segment-tokens segment)
                    '("alpha" "beta" "gamma" "delta")))
     (should (equal (alist-get 'text segment) "alpha beta gamma delta"))
     (should (= (alist-get 'start segment) 1.0))
     (should (= (alist-get 'end segment) 4.0))
     (should (eq (alist-get 'deleted (alist-get 'edit word)) t)))))

(ert-deftest eve-visual-features-delete-word-second-invocation-restores-word ()
  "Deleting the same word twice clears the nested deleted flag."
  (eve-test-with-buffer
   (setq-local eve-hide-deleted-mode nil)
   (should (eve--goto-segment "seg-1"))
   (should (eve--goto-word-by-index "seg-1" 1))
   (forward-char 1)
   (call-interactively #'eve-delete-word)
   (should (eve--goto-segment "seg-1"))
   (should (eve--goto-word-by-index "seg-1" 1))
   (forward-char 1)
   (call-interactively #'eve-delete-word)
   (let* ((segment (car (eve--segments)))
          (word (nth 1 (alist-get 'words segment))))
     (should (= 1 (length (eve--segments))))
     (should (equal (eve-test--segment-tokens segment)
                    '("alpha" "beta" "gamma" "delta")))
     (should (equal (alist-get 'text segment) "alpha beta gamma delta"))
     (should-not (alist-get 'deleted (alist-get 'edit word))))))

(ert-deftest eve-visual-features-delete-segment-toggles-nested-deleted-flag ()
  "Deleting a segment toggles nested edit metadata without removing it."
  (eve-test-with-buffer
   (setq-local eve-hide-deleted-mode nil)
   (cl-letf (((symbol-function 'yes-or-no-p)
              (lambda (_prompt) t)))
     (should (eve--goto-segment "seg-1"))
     (call-interactively #'eve-delete-segment))
   (let* ((segments (eve--segments))
          (segment (car segments)))
     (should (= 1 (length segments)))
     (should (equal (alist-get 'id segment) "seg-1"))
     (should (eq (alist-get 'deleted (alist-get 'edit segment)) t)))))

(ert-deftest eve-visual-features-delete-segment-second-invocation-restores-segment ()
  "Deleting the same segment twice clears the nested deleted flag."
  (eve-test-with-buffer
   (setq-local eve-hide-deleted-mode nil)
   (cl-letf (((symbol-function 'yes-or-no-p)
              (lambda (_prompt) t)))
     (should (eve--goto-segment "seg-1"))
     (call-interactively #'eve-delete-segment)
     (should (eve--goto-segment "seg-1"))
     (call-interactively #'eve-delete-segment))
   (let* ((segments (eve--segments))
          (segment (car segments)))
     (should (= 1 (length segments)))
     (should (equal (alist-get 'id segment) "seg-1"))
     (should-not (alist-get 'deleted (alist-get 'edit segment))))))

(ert-deftest eve-visual-features-tag-fillers-tags-matching-words-and-rerenders ()
  "Tagging fillers stores nested edit metadata and rerenders the buffer."
  (eve-test-with-buffer
   (let* ((segment (car (eve--segments)))
          (word (nth 1 (alist-get 'words segment)))
          (render-count 0)
          (original-render (symbol-function 'eve--render)))
     (setf (alist-get 'token word) "um")
     (setf (alist-get 'text segment) "alpha um gamma delta")
     (funcall original-render)
     (setq-local eve-filler-regex '("\\`um\\'"))
      (cl-letf (((symbol-function 'eve--render)
                 (lambda (&optional preserve-point)
                   (setq render-count (1+ render-count))
                   (funcall original-render preserve-point))))
        (call-interactively #'eve-tag-fillers))
      (should (equal (eve--edit-kind word) "filler"))
      (should (> render-count 0)))))

(ert-deftest eve-split-segment-creates-new-entry ()
  "Splitting a segment duplicates metadata and assigns a fresh id."
  (eve-test-with-buffer
   (setq eve--visual-separators (list "seg-1"))
   (should (eve--goto-segment "seg-1"))
   (should (eve--goto-word-by-index "seg-1" 2))
   (forward-char 1)
   (call-interactively #'eve-split-segment)
   (let* ((segments (eve--segments))
	  (first (nth 0 segments))
	  (second (nth 1 segments)))
     (should (= 2 (length segments)))
     (should (equal (alist-get 'id first) "seg-1"))
     (should (equal (eve-test--segment-tokens first) '("alpha" "beta")))
     (should (= (alist-get 'start first) 1.0))
     (should (= (alist-get 'end first) 2.0))
     (should (string-prefix-p "seg-1-split-" (alist-get 'id second)))
     (should (equal (eve-test--segment-tokens second) '("gamma" "delta")))
     (should (= (alist-get 'start second) 2.0))
     (should (= (alist-get 'end second) 4.0))
     (should (equal eve--visual-separators (list (alist-get 'id second)))))
   (should (looking-at "gamma"))))

(ert-deftest eve-insert-marker-before-current ()
  "Inserting a marker yields a dedicated marker segment ahead of point."
  (eve-test-with-buffer
   (should (eve--goto-segment "seg-1"))
   (let ((responses '("Launch Plan")))
     (cl-letf (((symbol-function 'read-string)
		(lambda (_prompt &optional _default)
		  (or (pop responses) "")))
	       ((symbol-function 'eve--generate-marker-id)
		(lambda () "marker-001")))
       (call-interactively #'eve-insert-marker)))
   (let ((segments (eve--segments)))
     (should (= 2 (length segments)))
     (let ((marker (car segments)))
       (should (eve--marker-p marker))
       (should (equal (alist-get 'id marker) "marker-001"))
       (should (equal (alist-get 'title marker) "Launch Plan"))
       (should (= (alist-get 'start marker) 1.0))
       (should (equal (alist-get 'source marker) "clip"))))))

(ert-deftest eve-validation-ignores-markers ()
  "Markers without timings do not raise validation issues."
  (eve-test-with-buffer
   (let* ((segments (eve--segments))
	  (marker '((id . "marker-1") (kind . "marker") (title . "Intro"))))
     (setf (alist-get 'segments eve--data) (append (list marker) segments))
     (eve--render)
     (should (null (eve-validate t))))))

(ert-deftest eve-broll-prevents-still-with-broll-audio ()
  "Still-image overlays cannot request b-roll audio."
  (eve-test-with-buffer
   (let ((segment (car (eve--segments)))
	 (responses '("broll/banner.png" "overlay" "broll" "" "" "y")))
     (cl-letf (((symbol-function 'read-string)
		(lambda (_prompt &optional _default)
		  (or (pop responses) "")))
	       ((symbol-function 'image-file-name-p) (lambda (_file) t)))
       (should-error (eve-edit-broll segment) :type 'user-error)))))

(ert-deftest eve-marker-supports-broll ()
  "Markers accept b-roll metadata and render a summary line."
  (eve-test-with-buffer
   (let* ((segments (eve--segments))
	  (marker (list (cons 'id "marker-001")
			(cons 'kind "marker")
			(cons 'title "Launch Intro")
			(cons 'broll nil))))
     (setf (alist-get 'segments eve--data) (cons marker segments))
     (eve--render)
     (should (eve--goto-segment "marker-001"))
    (let ((responses '("intro.mp4" "overlay" "mute" "" "" "n" "n")))
      (cl-letf (((symbol-function 'read-string)
		  (lambda (_prompt &optional default)
		    (or (prog1 (car responses)
			  (setq responses (cdr responses)))
			default))))
	 (call-interactively #'eve-edit-broll)))
     (eve--render)
     (should (eve--goto-segment "marker-001"))
     (let* ((segment (eve--segment-at-point))
	    (broll (eve--segment-broll segment)))
       (should broll)
       (should (string= (alist-get 'file broll) "intro.mp4")))
     (should (string-match-p "\\[b-roll\\] file=intro\\.mp4"
			     (buffer-substring-no-properties (point-min) (point-max)))))))

(ert-deftest eve-edit-placeholders-merges-template ()
  "Placeholder editor preloads template defaults and stores overrides."
  (eve-test-with-buffer
   (let* ((template-file (make-temp-file "tjm-template" nil ".json"
					 "{\"template\":\"card.mp4\",\"placeholders\":{\"cta\":\"Subscribe\",\"title\":\"Weekly Update\"}}"))
	  (cleanup (lambda () (when (file-exists-p template-file) (delete-file template-file)))))
     (unwind-protect
	 (progn
	   (let* ((segment (car (eve--segments))))
	     (eve--set-segment-broll segment
				      (list (cons 'file template-file))))
	   (let ((segment (car (eve--segments))))
	     (should (eve--segment-broll segment)))
	   (eve--render)
	   (should (eve--goto-segment "seg-1"))
	   (let ((responses '("Join us" "" "speaker" "Ari" "")))
	     (cl-letf (((symbol-function 'read-string)
			(lambda (_prompt &optional default)
			  (or (prog1 (car responses)
				(setq responses (cdr responses)))
			      default))))
	       (call-interactively #'eve-edit-broll-placeholders)))
	   (let* ((segment (car (eve--segments)))
		  (broll (eve--segment-broll segment))
		  (placeholders (eve--aget 'placeholders broll)))
	     (should placeholders)
	     (should (equal (alist-get "cta" placeholders nil nil #'equal) "Join us"))
	     (should (equal (alist-get "speaker" placeholders nil nil #'equal) "Ari"))
	     (should-not (alist-get "title" placeholders nil nil #'equal))))
       (funcall cleanup)))))

(ert-deftest eve-filter-media-files-drops-non-media-inputs ()
  "Filtering keeps only configured media files."
  (let ((files '("/tmp/clip.mp4"
		"/tmp/notes.txt"
		"/tmp/audio.wav"
		"/tmp/manifest.tjm.json")))
    (should (equal (eve--filter-media-files files)
		   '("/tmp/clip.mp4" "/tmp/audio.wav")))))

(ert-deftest eve-filter-media-files-is-case-insensitive ()
  "Filtering matches supported extensions regardless of case."
  (let ((files '("/tmp/INTRO.MP4"
		"/tmp/voice.M4A"
		"/tmp/still.PNG")))
    (should (equal (eve--filter-media-files files)
		   '("/tmp/INTRO.MP4" "/tmp/voice.M4A")))))

(ert-deftest eve-ruler-mode-enable-creates-overlays ()
  "Enabling eve-ruler-mode creates overlays."
  (eve-test-with-buffer
   (let ((eve-ruler-interval 1.0))
     (eve-ruler-mode -1)
     (eve--ruler-clear-overlays)
     (eve-ruler-mode 1)
     (eve--update-ruler)
     (should (> (length eve--ruler-overlays) 0)))))

(ert-deftest eve-ruler-mode-disable-removes-overlays ()
  "Disabling eve-ruler-mode removes overlays."
  (eve-test-with-buffer
   (let ((eve-ruler-interval 1.0))
     (eve-ruler-mode 1)
     (eve--update-ruler)
     (eve-ruler-mode -1)
     (should (null eve--ruler-overlays))
     (should (= 0 (or right-margin-width 0))))))

(ert-deftest eve-ruler-mode-render-updates-ruler ()
  "Re-rendering when ruler is active recreates ruler overlays."
  (eve-test-with-buffer
   (let ((eve-ruler-interval 1.0))
     (eve-ruler-mode 1)
     (eve--render)
     (should (> (length eve--ruler-overlays) 0)))))

(ert-deftest eve-infer-manifest-path-for-single-file ()
  "A single media input maps to a sibling `.tjm.json' file."
  (let ((file (expand-file-name "fixtures/session/clip.mp4" eve-test--root)))
    (should (equal (eve--infer-manifest-path (list file))
		   (expand-file-name "fixtures/session/clip.tjm.json" eve-test--root)))))

(ert-deftest eve-infer-manifest-path-for-multiple-files ()
  "Multiple media inputs map to a directory-named `.tjm.json' file."
  (let* ((dir (expand-file-name "fixtures/session/" eve-test--root))
	 (files (list (expand-file-name "clip.mp4" dir)
		      (expand-file-name "clip-2.mov" dir))))
    (should (equal (eve--infer-manifest-path files)
		   (expand-file-name "session.tjm.json" dir)))))

(ert-deftest eve-compile-command-uses-preserve-gaps-threshold ()
  "Compile commands source the gap threshold from `eve-preserve-gaps-max'."
  (with-temp-buffer
    (let* ((buffer-file-name "/tmp/session dir/session.tjm.json")
           (eve-cli-program "eve-custom")
           (eve-preserve-gaps-max 2.25)
           (program "/opt/eve tools/eve custom")
           (output "/tmp/session dir/session dir.mp4"))
      (cl-letf (((symbol-function 'executable-find)
                 (lambda (candidate)
                   (should (equal candidate eve-cli-program))
                   program)))
        (should (equal (eve--compile-command)
                       (format "%s text-edit %s --output %s --subtitles --preserve-short-gaps %s"
                                (shell-quote-argument program)
                                (shell-quote-argument buffer-file-name)
                                (shell-quote-argument output)
                                eve-preserve-gaps-max)))))))

(ert-deftest eve-compile-command-errors-when-cli-program-is-missing ()
  "Compile commands fail early when the configured CLI is unavailable."
  (with-temp-buffer
    (let ((buffer-file-name "/tmp/session dir/session.tjm.json")
          (eve-cli-program "missing-eve")
          err)
      (cl-letf (((symbol-function 'executable-find)
                 (lambda (candidate)
                   (should (equal candidate eve-cli-program))
                   nil)))
        (setq err (should-error (eve--compile-command)
                                :type 'user-error))
        (should (equal (cadr err)
                       "Cannot find eve CLI executable: missing-eve"))))))

(ert-deftest eve-compile-marker-uses-preserve-gaps-threshold ()
  "Marker compilation sources the gap threshold from `eve-preserve-gaps-max'."
  (eve-test-with-buffer
   (setq-local buffer-file-name "/tmp/session dir/session.tjm.json")
    (let* ((segment (copy-tree (car (eve--segments)) t))
           (marker (list (cons 'id "marker-001")
                         (cons 'kind "marker")
                         (cons 'title "Launch Plan")))
           (eve-cli-program "eve-custom")
           (eve-preserve-gaps-max 2.25)
           (program "/opt/eve tools/eve custom")
           (temp "/tmp/eve section.json")
           (output "/tmp/session dir/session dir-launch-plan.mp4")
          captured-command
          captured-output
          captured-temp
          captured-data
          captured-data-path)
     (setf (alist-get 'segments eve--data) (list marker segment))
     (eve--render)
     (should (eve--goto-segment "marker-001"))
     (cl-letf (((symbol-function 'executable-find)
                (lambda (candidate)
                  (should (equal candidate eve-cli-program))
                  program))
               ((symbol-function 'make-temp-file)
                (lambda (&rest _args)
                  temp))
               ((symbol-function 'eve--write-json-file)
                (lambda (data path)
                  (setq captured-data data
                        captured-data-path path)))
               ((symbol-function 'eve--run-compile)
                (lambda (command resolved-output &optional resolved-temp)
                  (setq captured-command command
                        captured-output resolved-output
                        captured-temp resolved-temp))))
       (eve-compile)
       (should (equal captured-command
                       (format "%s text-edit %s --output %s --subtitles --preserve-short-gaps %s"
                               (shell-quote-argument program)
                               (shell-quote-argument temp)
                               (shell-quote-argument output)
                               eve-preserve-gaps-max)))
        (should (equal captured-output output))
        (should (equal captured-temp temp))
        (should (equal captured-data-path temp))
       (should (= (length (alist-get 'segments captured-data)) 1))))))

(ert-deftest eve-transcribe-async-builds-command ()
  "Launcher clears the transcribe buffer and builds argv for `make-process`."
  (let ((eve-cli-program "eve-custom")
        (eve-transcribe-backend "mlx-whisper")
        (eve-transcribe-model "large-v3")
        (eve-transcribe-verbatim t)
        (eve-transcribe-tag-fillers t)
        (buffer (get-buffer-create eve--transcribe-buffer-name))
        captured-plist
        last-message)
    (unwind-protect
        (progn
          (with-current-buffer buffer
            (insert "stale output"))
          (cl-letf (((symbol-function 'executable-find)
                     (lambda (program)
                       (should (equal program eve-cli-program))
                       "/opt/eve/bin/eve-custom"))
                    ((symbol-function 'make-process)
                     (lambda (&rest args)
                       (setq captured-plist args)
                       'fake-process))
                    ((symbol-function 'message)
                     (lambda (format-string &rest args)
                       (setq last-message (apply #'format format-string args)))))
            (eve--transcribe-async '("/tmp/clip.mp4" "/tmp/voice.wav")
                                   "/tmp/session.tjm.json"))
          (should (equal (plist-get captured-plist :name) "eve-transcribe"))
          (should (eq (plist-get captured-plist :buffer) buffer))
          (should (equal (plist-get captured-plist :command)
                         '("/opt/eve/bin/eve-custom" "transcribe"
                            "/tmp/clip.mp4" "/tmp/voice.wav"
                            "--output" "/tmp/session.tjm.json"
                            "--backend" "mlx-whisper"
                            "--model" "large-v3"
                            "--verbatim"
                            "--tag-fillers")))
          (should (plist-get captured-plist :noquery))
          (should (functionp (plist-get captured-plist :sentinel)))
          (should (equal (with-current-buffer buffer (buffer-string)) ""))
          (should (equal last-message
                         "Started eve transcribe -> /tmp/session.tjm.json")))
      (when (buffer-live-p buffer)
        (kill-buffer buffer)))))

(ert-deftest eve-transcribe-async-omits-disabled-boolean-flags ()
  "Launcher keeps string flags and omits disabled boolean transcription flags."
  (let ((eve-cli-program "eve-custom")
        (eve-transcribe-backend "whisper.cpp")
        (eve-transcribe-model "small.en")
        (eve-transcribe-verbatim nil)
        (eve-transcribe-tag-fillers nil)
        (buffer (get-buffer-create eve--transcribe-buffer-name))
        captured-plist)
    (unwind-protect
        (progn
          (cl-letf (((symbol-function 'executable-find)
                     (lambda (program)
                       (should (equal program eve-cli-program))
                       "/opt/eve/bin/eve-custom"))
                    ((symbol-function 'make-process)
                     (lambda (&rest args)
                       (setq captured-plist args)
                       'fake-process)))
            (eve--transcribe-async '("/tmp/clip.mp4")
                                   "/tmp/session.tjm.json"))
          (should (equal (plist-get captured-plist :command)
                         '("/opt/eve/bin/eve-custom" "transcribe"
                           "/tmp/clip.mp4"
                           "--output" "/tmp/session.tjm.json"
                           "--backend" "whisper.cpp"
                           "--model" "small.en"))))
      (when (buffer-live-p buffer)
        (kill-buffer buffer)))))

(ert-deftest eve-transcribe-async-errors-when-cli-program-is-missing ()
  "Launcher reports a missing CLI executable before starting the process."
  (let ((eve-cli-program "missing-eve")
        err)
    (cl-letf (((symbol-function 'executable-find)
               (lambda (program)
                 (should (equal program eve-cli-program))
                 nil))
              ((symbol-function 'make-process)
               (lambda (&rest _args)
                 (ert-fail "should not start a process when the CLI is missing"))))
      (setq err (should-error (eve--transcribe-async '("/tmp/clip.mp4")
                                                      "/tmp/session.tjm.json")
                              :type 'user-error))
      (should (equal (cadr err)
                     "Cannot find eve CLI executable: missing-eve")))))

(ert-deftest eve-transcribe-errors-when-directory-has-no-media ()
  "Directory command rejects directories without supported media files."
  (let* ((directory (make-temp-file "eve-transcribe-empty" t))
         (expanded-directory (file-name-as-directory
                              (expand-file-name directory)))
         err)
    (unwind-protect
        (progn
          (with-temp-file (expand-file-name "notes.txt" expanded-directory)
            (insert "todo"))
          (setq err (should-error (eve-transcribe expanded-directory)
                                  :type 'user-error))
          (should (equal (cadr err)
                         (format "No media files found in %s"
                                 expanded-directory))))
      (delete-directory directory t))))

(ert-deftest eve-transcribe-delegates-directory-media-files ()
  "Directory command filters media files and delegates to the async launcher."
  (let* ((directory (make-temp-file "eve-transcribe-files" t))
         (expanded-directory (file-name-as-directory
                              (expand-file-name directory)))
         (clip (expand-file-name "clip.mp4" expanded-directory))
         (voice (expand-file-name "voice.WAV" expanded-directory))
         captured-files
         captured-output)
    (unwind-protect
        (progn
          (with-temp-file clip
            (insert "video"))
          (with-temp-file voice
            (insert "audio"))
          (make-directory (expand-file-name "nested" expanded-directory))
          (with-temp-file (expand-file-name "notes.txt" expanded-directory)
            (insert "ignore"))
          (cl-letf (((symbol-function 'eve--transcribe-async)
                     (lambda (files output-path)
                       (setq captured-files files
                             captured-output output-path))))
            (eve-transcribe expanded-directory))
          (should (equal captured-files (list clip voice)))
          (should (equal captured-output
                         (expand-file-name
                          (concat
                           (file-name-nondirectory
                            (directory-file-name expanded-directory))
                           ".tjm.json")
                          expanded-directory))))
      (delete-directory directory t))))

(ert-deftest eve-dired-transcribe-errors-outside-dired ()
  "Dired entry point rejects calls outside Dired buffers."
  (let (err)
    (cl-letf (((symbol-function 'derived-mode-p)
               (lambda (&rest _modes)
                 nil))
              ((symbol-function 'dired-get-marked-files)
               (lambda (&rest _args)
                 (ert-fail "should not request marked files outside Dired"))))
      (setq err (should-error (eve-dired-transcribe)
                              :type 'user-error))
      (should (equal (cadr err) "Not in a Dired buffer")))))

(ert-deftest eve-dired-transcribe-errors-when-no-media-selected ()
  "Dired entry point rejects marked selections without supported media."
  (let (err)
    (cl-letf (((symbol-function 'derived-mode-p)
               (lambda (&rest _modes)
                 t))
              ((symbol-function 'dired-get-marked-files)
               (lambda (&rest _args)
                 '("/tmp/session/notes.txt" "/tmp/session/outline.md"))))
      (setq err (should-error (eve-dired-transcribe)
                              :type 'user-error))
      (should (equal (cadr err) "No media files selected")))))

(ert-deftest eve-dired-transcribe-delegates-marked-media-files ()
  "Dired entry point filters marked files and preserves media order."
  (let (captured-files
        captured-output)
    (cl-letf (((symbol-function 'derived-mode-p)
               (lambda (&rest _modes)
                 t))
              ((symbol-function 'dired-get-marked-files)
               (lambda (&rest _args)
                 '("/tmp/session/voice.WAV"
                   "/tmp/session/notes.txt"
                   "/tmp/session/clip.mp4")))
              ((symbol-function 'eve--transcribe-async)
               (lambda (files output-path)
                 (setq captured-files files
                       captured-output output-path))))
      (eve-dired-transcribe)
      (should (equal captured-files
                     '("/tmp/session/voice.WAV"
                       "/tmp/session/clip.mp4")))
      (should (equal captured-output
                     "/tmp/session/session.tjm.json")))))

(ert-deftest eve-transcribe-async-opens-output-on-success ()
  "Successful completion visits the output manifest without surfacing the log."
  (let ((buffer (get-buffer-create eve--transcribe-buffer-name))
         captured-plist
         opened
        popped
        messages)
    (unwind-protect
        (progn
          (cl-letf (((symbol-function 'executable-find)
                     (lambda (_program)
                       "/opt/eve/bin/eve"))
                    ((symbol-function 'make-process)
                     (lambda (&rest args)
                       (setq captured-plist args)
                       'fake-process))
                     ((symbol-function 'process-buffer)
                      (lambda (_process)
                       buffer))
                    ((symbol-function 'process-status)
                     (lambda (_process)
                       'exit))
                    ((symbol-function 'process-exit-status)
                     (lambda (_process)
                       0))
                    ((symbol-function 'find-file)
                     (lambda (file)
                       (setq opened file)))
                    ((symbol-function 'pop-to-buffer)
                     (lambda (buf &rest _args)
                       (setq popped buf)))
                    ((symbol-function 'message)
                     (lambda (format-string &rest args)
                       (push (apply #'format format-string args) messages))))
            (eve--transcribe-async '("/tmp/clip.mp4") "/tmp/session.tjm.json")
            (funcall (plist-get captured-plist :sentinel) 'fake-process "finished\n"))
          (should (equal opened "/tmp/session.tjm.json"))
          (should-not popped)
          (should (member "eve transcribe finished: /tmp/session.tjm.json" messages)))
      (when (buffer-live-p buffer)
        (kill-buffer buffer)))))

(ert-deftest eve-transcribe-async-surfaces-buffer-on-failure ()
  "Non-zero completion shows the transcribe buffer and reports the failure."
  (let ((buffer (get-buffer-create eve--transcribe-buffer-name))
         captured-plist
         opened
        popped
        messages)
    (unwind-protect
        (progn
          (cl-letf (((symbol-function 'executable-find)
                     (lambda (_program)
                       "/opt/eve/bin/eve"))
                    ((symbol-function 'make-process)
                     (lambda (&rest args)
                       (setq captured-plist args)
                       'fake-process))
                     ((symbol-function 'process-buffer)
                      (lambda (_process)
                       buffer))
                    ((symbol-function 'process-status)
                     (lambda (_process)
                       'exit))
                    ((symbol-function 'process-exit-status)
                     (lambda (_process)
                       1))
                    ((symbol-function 'find-file)
                     (lambda (file)
                       (setq opened file)))
                    ((symbol-function 'pop-to-buffer)
                     (lambda (buf &rest _args)
                       (setq popped buf)))
                    ((symbol-function 'message)
                     (lambda (format-string &rest args)
                       (push (apply #'format format-string args) messages))))
            (eve--transcribe-async '("/tmp/clip.mp4") "/tmp/session.tjm.json")
            (funcall (plist-get captured-plist :sentinel)
                     'fake-process
                     "exited abnormally with code 1\n"))
          (should-not opened)
          (should (eq popped buffer))
          (should (member "eve transcribe failed: exited abnormally with code 1"
                          messages)))
      (when (buffer-live-p buffer)
        (kill-buffer buffer)))))

(ert-deftest eve-phrase-filler-tag-fillers-tags-multiword-phrase ()
  "Tagging uses `eve-filler-phrases` to mark full multi-word phrases."
  (eve-test-with-buffer
   (let* ((segment
           '((id . "seg-phrase")
             (source . "clip")
             (start . 5.0) (end . 8.2)
             (text . "to be honest with you today")
             (words .
                    (((start . 5.0) (end . 5.3) (spoken . "to") (token . "to"))
                     ((start . 5.3) (end . 5.5) (spoken . "be") (token . "be"))
                     ((start . 5.5) (end . 6.0) (spoken . "honest") (token . "honest"))
                     ((start . 6.0) (end . 6.2) (spoken . "with") (token . "with"))
                     ((start . 6.2) (end . 6.8) (spoken . "you") (token . "you"))
                     ((start . 6.8) (end . 7.2) (spoken . "today") (token . "today"))))))
           (words nil))
     (setf (alist-get 'segments eve--data) (list (copy-tree segment t)))
     (setq-local eve-filler-phrases '("to be honest with you"))
     (call-interactively #'eve-tag-fillers)
     (setq words (alist-get 'words (car (eve--segments))))
     (should (equal (eve--edit-kind (nth 0 words)) "filler"))
     (should (equal (eve--edit-kind (nth 1 words)) "filler"))
     (should (equal (eve--edit-kind (nth 2 words)) "filler"))
     (should (equal (eve--edit-kind (nth 3 words)) "filler"))
     (should (equal (eve--edit-kind (nth 4 words)) "filler"))
     (should-not (equal (eve--edit-kind (nth 5 words)) "filler")))))

(ert-deftest eve-phrase-filler-tag-fillers-normalizes-punctuation-and-case ()
  "Phrase tagging normalizes punctuation and case before matching."
  (eve-test-with-buffer
   (let* ((segment
           '((id . "seg-phrase")
             (source . "clip")
             (start . 5.0) (end . 8.0)
             (text . "To be honest, with you")
             (words .
                    (((start . 5.0) (end . 5.3) (spoken . "To") (token . "to"))
                     ((start . 5.3) (end . 5.5) (spoken . "be") (token . "be"))
                     ((start . 5.5) (end . 6.0) (spoken . "honest,") (token . "honest,"))
                     ((start . 6.0) (end . 6.2) (spoken . "with") (token . "with"))
                     ((start . 6.2) (end . 6.8) (spoken . "you") (token . "you"))))))
           (words nil))
     (setf (alist-get 'segments eve--data) (list (copy-tree segment t)))
     (setq-local eve-filler-phrases '("to be honest with you"))
     (call-interactively #'eve-tag-fillers)
     (setq words (alist-get 'words (car (eve--segments))))
     (should (equal (eve--edit-kind (nth 0 words)) "filler"))
     (should (equal (eve--edit-kind (nth 1 words)) "filler"))
     (should (equal (eve--edit-kind (nth 2 words)) "filler"))
     (should (equal (eve--edit-kind (nth 3 words)) "filler"))
     (should (equal (eve--edit-kind (nth 4 words)) "filler")))))

(ert-deftest eve-phrase-filler-tag-fillers-prefers-longest-match ()
  "When phrases overlap, the longest configured phrase wins."
  (eve-test-with-buffer
   (let* ((segment
           '((id . "seg-overlap")
             (source . "clip")
             (start . 9.0) (end . 10.5)
             (text . "um you know what")
             (words .
                    (((start . 9.0) (end . 9.1) (spoken . "um") (token . "um"))
                     ((start . 9.1) (end . 9.4) (spoken . "you") (token . "you"))
                     ((start . 9.4) (end . 9.8) (spoken . "know") (token . "know"))
                      ((start . 9.8) (end . 10.1) (spoken . "what") (token . "what"))))))
           (words nil))
     (setf (alist-get 'segments eve--data) (list (copy-tree segment t)))
     (setq-local eve-filler-phrases '("you know" "you know what"))
     (call-interactively #'eve-tag-fillers)
     (setq words (alist-get 'words (car (eve--segments))))
     (should-not (equal (eve--edit-kind (nth 0 words)) "filler"))
     (should (equal (eve--edit-kind (nth 1 words)) "filler"))
     (should (equal (eve--edit-kind (nth 2 words)) "filler"))
     (should (equal (eve--edit-kind (nth 3 words)) "filler")))))

(ert-deftest eve-phrase-filler-add-at-point-updates-config-and-rerenders ()
  "Adding a filler at point persists phrase config and re-tags the current word."
  (eve-test-with-buffer
   (let* ((segment
           '((id . "seg-point")
             (source . "clip")
             (start . 11.0) (end . 12.0)
             (text . "um alpha")
             (words .
                    (((start . 11.0) (end . 11.2) (spoken . "um") (token . "um"))
                     ((start . 11.2) (end . 11.7) (spoken . "alpha") (token . "alpha"))))))
           (saved-args nil)
          (word nil)
          (range nil))
     (setf (alist-get 'segments eve--data) (list (copy-tree segment t)))
     (setq-local eve-filler-phrases '())
     (eve--render)
     (should (eve--goto-segment "seg-point"))
     (search-forward "um" nil t)
     (backward-char 1)
     (cl-letf (((symbol-function 'customize-save-variable)
                (lambda (symbol value)
                  (setq saved-args (list symbol value)))))
       (call-interactively #'eve-add-filler-at-point))
     (setq word (car (alist-get 'words (car (eve--segments)))))
     (setq range (eve-test--match-range "um"))
     (should (member "um" eve-filler-phrases))
     (should (equal (eve--edit-kind word) "filler"))
     (should (equal saved-args (list 'eve-filler-phrases eve-filler-phrases)))
     (should range)
     (should (eve-test--span-has-face-p (car range) (cdr range) 'eve-filler-face)))))

(ert-deftest eve-phrase-filler-add-region-updates-config-and-rerenders ()
  "Adding a filler from region persists phrase config and re-tags words."
  (eve-test-with-buffer
   (let* ((segment
           '((id . "seg-region")
             (source . "clip")
             (start . 13.0) (end . 14.5)
             (text . "you know what")
             (words .
                    (((start . 13.0) (end . 13.3) (spoken . "you") (token . "you"))
                     ((start . 13.3) (end . 13.7) (spoken . "know") (token . "know"))
                      ((start . 13.7) (end . 14.1) (spoken . "what") (token . "what"))))))
           (saved-args nil)
          (words nil)
          (region nil))
     (setf (alist-get 'segments eve--data) (list (copy-tree segment t)))
     (setq-local eve-filler-phrases '())
     (eve--render)
     (setq region (eve-test--match-range "you know"))
     (goto-char (car region))
     (set-mark (car region))
     (goto-char (cdr region))
     (activate-mark)
     (cl-letf (((symbol-function 'customize-save-variable)
                (lambda (symbol value)
                  (setq saved-args (list symbol value)))))
       (call-interactively #'eve-add-filler-region))
     (setq words (alist-get 'words (car (eve--segments))))
     (should (member "you know" eve-filler-phrases))
     (should (equal (eve--edit-kind (nth 0 words)) "filler"))
     (should (equal (eve--edit-kind (nth 1 words)) "filler"))
     (should-not (equal (eve--edit-kind (nth 2 words)) "filler"))
     (should (equal saved-args (list 'eve-filler-phrases eve-filler-phrases))))))

(ert-deftest eve-phrase-filler-startup-autotags-configured-fillers ()
  "Reload/startup automatically applies filler tags from configured phrases."
  (eve-test-with-buffer
   (setq-local eve-filler-phrases '("um"))
   (cl-letf (((symbol-function 'eve--load-data)
              (lambda ()
                (setq-local eve--data
                            (list (cons 'version 1)
                                  (cons 'sources
                                        (list (list (cons 'id "clip")
                                                    (cons 'file "clip.mp4"))))
                                  (cons 'segments
                                        (list (list (cons 'id "seg-startup")
                                                    (cons 'source "clip")
                                                    (cons 'start 21.0)
                                                    (cons 'end 22.0)
                                                    (cons 'text "um hello")
                                                    (cons 'words
                                                          (list (list (cons 'start 21.0)
                                                                      (cons 'end 21.2)
                                                                      (cons 'spoken "um")
                                                                      (cons 'token "um"))
                                                                (list (cons 'start 21.2)
                                                                      (cons 'end 21.8)
                                                                      (cons 'spoken "hello")
                                                                      (cons 'token "hello"))))))))))))
     (eve-reload))
    (let* ((segment (car (eve--segments)))
           (word (car (alist-get 'words segment))))
      (should (equal (eve--edit-kind word) "filler")))))

;; Duration test helper data
(defconst eve-test--seg-timed
  '((id . "seg-t1")
    (source . "clip")
    (start . 0.0) (end . 5.0)
    (text . "hello world")
    (words .
           (((start . 0.5) (end . 1.5) (token . "hello"))
            ((start . 2.0) (end . 3.0) (token . "world")))))
  "Segment with timed words for duration tests.")

(ert-deftest eve-ruler-duration-single-segment-all-present ()
  "Rendered segment duration uses first/last visible word bounds."
  (let ((eve-preserve-gaps-max 2.0))
    (should (= 2.5 (eve--rendered-segment-duration (copy-tree eve-test--seg-timed t) t)))))

(ert-deftest eve-ruler-duration-deleted-word-excluded ()
  "Deleted words are excluded when hide-deleted is enabled."
  (let* ((eve-preserve-gaps-max 2.0)
         (segment (copy-tree eve-test--seg-timed t))
         (words (alist-get 'words segment)))
    (setf (alist-get 'edit (car words)) '((deleted . t)))
    (should (= 1.0 (eve--rendered-segment-duration segment t)))))

(ert-deftest eve-ruler-duration-deleted-word-included-when-show-deleted ()
  "Deleted words are included when hide-deleted is disabled."
  (let* ((eve-preserve-gaps-max 2.0)
         (segment (copy-tree eve-test--seg-timed t))
         (words (alist-get 'words segment)))
    (setf (alist-get 'edit (car words)) '((deleted . t)))
    (should (= 2.5 (eve--rendered-segment-duration segment nil)))))

(ert-deftest eve-ruler-duration-all-words-deleted ()
  "Segment duration is zero when all words are deleted and hidden."
  (let* ((segment (copy-tree eve-test--seg-timed t))
         (words (alist-get 'words segment)))
    (dotimes (idx (length words))
      (setf (alist-get 'edit (nth idx words)) '((deleted . t))))
    (should (= 0.0 (eve--rendered-segment-duration segment t)))))

(ert-deftest eve-ruler-duration-marker-segment ()
  "Marker segments always have rendered duration zero."
  (let ((segment '((id . "m1") (kind . "marker") (title . "Section"))))
    (should (= 0.0 (eve--rendered-segment-duration segment t)))))

(ert-deftest eve-ruler-duration-no-words-key ()
  "Segments without words render with zero duration."
  (let ((segment '((id . "seg-empty")
                   (source . "clip")
                   (start . 0.0) (end . 5.0)
                   (text . "hello world"))))
    (should (= 0.0 (eve--rendered-segment-duration segment t)))))

(ert-deftest eve-ruler-duration-cumulative-monotonic ()
  "Cumulative times increase with positive-duration segments."
  (let* ((seg1 (copy-tree eve-test--seg-timed t))
         (seg2 '((id . "seg-t2")
                 (source . "clip")
                 (start . 5.0) (end . 8.0)
                 (text . "again now")
                 (words . (((start . 5.5) (end . 6.0) (token . "again"))
                           ((start . 6.0) (end . 7.0) (token . "now"))))))
         (times (eve--rendered-cumulative-times (list seg1 seg2) t)))
    (should (= 2 (length times)))
    (should (< (cdr (nth 0 times)) (cdr (nth 1 times))))))

(ert-deftest eve-ruler-duration-leading-trailing-gap-trimmed ()
  "Excessive leading/trailing gaps are trimmed from rendered duration."
  (let* ((eve-preserve-gaps-max 2.0)
         (segment '((id . "seg-gap")
                    (source . "clip")
                    (start . 0.0) (end . 10.0)
                    (text . "hello")
                    (words . (((start . 4.0) (end . 5.0) (token . "hello")))))))
    (should (= 0.0 (eve--rendered-segment-duration segment t)))))

(ert-deftest eve-ruler-format-time-zero ()
  (should (equal "[00:00:00]" (eve--format-ruler-time 0))))

(ert-deftest eve-ruler-format-time-one-minute-one-second ()
  (should (equal "[00:01:01]" (eve--format-ruler-time 61))))

(ert-deftest eve-ruler-format-time-one-hour ()
  (should (equal "[01:01:01]" (eve--format-ruler-time 3661))))

(ert-deftest eve-ruler-milestones-standard-interval ()
  (let ((times '(("seg-a" . 45.0) ("seg-b" . 90.0)))
        (result nil))
    (setq result (eve--ruler-milestones times 30.0))
    (should (= 4 (length result)))
    (should (equal "seg-a" (car (nth 0 result))))
    (should (equal "[00:00:00]" (cdr (nth 0 result))))
    (should (equal "seg-a" (car (nth 1 result))))
    (should (equal "[00:00:30]" (cdr (nth 1 result))))
    (should (equal "seg-b" (car (nth 2 result))))
    (should (equal "[00:01:00]" (cdr (nth 2 result))))
    (should (equal "seg-b" (car (nth 3 result))))
    (should (equal "[00:01:30]" (cdr (nth 3 result))))))

(ert-deftest eve-ruler-milestones-interval-exceeds-duration ()
  (let ((times '(("seg-x" . 20.0))))
    (should (= 1 (length (eve--ruler-milestones times 60.0))))))

(ert-deftest eve-ruler-milestones-zero-interval-clamped ()
  (let ((times '(("seg-a" . 5.0))))
    (should (= 6 (length (eve--ruler-milestones times 0))))))

(ert-deftest eve-ruler-update-ruler-creates-overlays ()
  "After eve--update-ruler, ruler overlays are created."
  (eve-test-with-buffer
   (let ((eve-ruler-interval 1.0))
     (eve--update-ruler)
     (should (> (length eve--ruler-overlays) 0)))))

(ert-deftest eve-ruler-clear-overlays-removes-all ()
  "After eve--ruler-clear-overlays, the list is empty."
  (eve-test-with-buffer
   (let ((eve-ruler-interval 1.0))
     (eve--update-ruler)
     (should (> (length eve--ruler-overlays) 0))
     (eve--ruler-clear-overlays)
     (should (null eve--ruler-overlays)))))

(ert-deftest eve-ruler-overlay-has-margin-display ()
  "Ruler overlays have a before-string with right-margin display spec."
  (eve-test-with-buffer
   (let ((eve-ruler-interval 1.0))
     (eve--update-ruler)
     (when eve--ruler-overlays
       (let* ((o (car eve--ruler-overlays))
              (bs (overlay-get o 'before-string)))
         (should bs)
         (should (get-text-property 0 'display bs)))))))

(ert-deftest eve-ruler-overlays-survive-re-render ()
  "After a re-render that calls eve--update-ruler, overlays are recreated."
  (eve-test-with-buffer
   (let ((eve-ruler-interval 1.0))
     (eve--update-ruler)
     (let ((count-before (length eve--ruler-overlays)))
       (eve--update-ruler)
       (should (= (length eve--ruler-overlays) count-before))))))

(ert-deftest eve-ruler-mode-line-string-returns-duration ()
  "After eve--update-ruler, mode-line string includes duration."
  (eve-test-with-buffer
   (let ((eve-ruler-interval 1.0))
     (eve--update-ruler)
     (should (> eve--ruler-total-duration 0.0))
     (should (string-match-p "\\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\\]"
                             (eve--ruler-mode-line-string))))))

(ert-deftest eve-ruler-mode-line-string-empty-when-zero ()
  "When total duration is 0, mode-line string is empty."
  (eve-test-with-buffer
   (setq-local eve--ruler-total-duration 0.0)
   (should (string= "" (eve--ruler-mode-line-string)))))

(ert-deftest eve-resolve-cli-uses-executable-find ()
  "Resolver returns executable-find result when it succeeds."
  (cl-letf (((symbol-function 'executable-find)
             (lambda (_prog) "/usr/local/bin/eve")))
    (should (equal "/usr/local/bin/eve" (eve--resolve-cli)))))

(ert-deftest eve-resolve-cli-falls-back-to-run-cli ()
  "Resolver falls back to scripts/run-cli.sh when executable-find fails."
  (let ((eve--source-directory eve-test--root))
    (cl-letf (((symbol-function 'executable-find)
               (lambda (_prog) nil)))
      (let ((result (eve--resolve-cli)))
        (should (and result (string-match-p "scripts/run-cli\\.sh" result)))))))

(ert-deftest eve-resolve-cli-returns-nil-when-both-fail ()
  "Resolver returns nil when executable-find fails and no run-cli.sh exists."
  (let ((eve--source-directory "/nonexistent/"))
    (cl-letf (((symbol-function 'executable-find)
               (lambda (_prog) nil)))
      (should-not (eve--resolve-cli)))))

(ert-deftest eve-playback-face-is-defined ()
  "eve-playback-face is a valid Emacs face."
  (should (facep 'eve-playback-face)))

(ert-deftest eve-playback-source-segment-at-time-within ()
  "Returns segment when time falls within its start/end range."
  (let ((segs '(((id . "a") (start . 0.0) (end . 2.0))
                ((id . "b") (start . 2.0) (end . 5.0)))))
    (should (equal "a" (alist-get 'id (eve--playback-source-segment-at-time 1.0 segs))))
    (should (equal "b" (alist-get 'id (eve--playback-source-segment-at-time 3.0 segs))))))

(ert-deftest eve-playback-source-segment-at-time-gap ()
  "Returns nil when time falls in a gap between segments."
  (let ((segs '(((id . "a") (start . 0.0) (end . 1.0))
                ((id . "b") (start . 2.0) (end . 3.0)))))
    (should-not (eve--playback-source-segment-at-time 1.5 segs))))

(ert-deftest eve-playback-source-segment-at-time-before ()
  "Returns nil when time is before all segments."
  (let ((segs '(((id . "a") (start . 5.0) (end . 10.0)))))
    (should-not (eve--playback-source-segment-at-time 1.0 segs))))

(ert-deftest eve-playback-rendered-segment-at-time-mid ()
  "Returns correct segment-id for time within rendered timeline."
  (let ((ctimes '(("seg-a" . 30.0) ("seg-b" . 60.0) ("seg-c" . 90.0))))
    (should (equal "seg-c" (eve--playback-rendered-segment-at-time 72.0 ctimes)))
    (should (equal "seg-a" (eve--playback-rendered-segment-at-time 0.0  ctimes)))
    (should (equal "seg-a" (eve--playback-rendered-segment-at-time 15.0 ctimes)))))

(ert-deftest eve-ipc-teardown-is-idempotent ()
  "Calling eve--ipc-teardown when nothing is active leaves state nil."
  (eve-test-with-buffer
   ;; State already nil from fresh buffer — should not error
   (eve--ipc-teardown)
   (should-not eve--ipc-process)
   (should-not eve--playback-timer)
   (should-not eve--playback-overlay)
   ;; Second call also safe
   (eve--ipc-teardown)
   (should-not eve--ipc-process)))

(ert-deftest eve-ipc-send-nil-process-is-safe ()
  "eve--ipc-send does not error when eve--ipc-process is nil."
  (eve-test-with-buffer
   (setq-local eve--ipc-process nil)
   ;; Must not signal
   (should-not (eve--ipc-send '("get_property" "playback-time")))))

(ert-deftest eve-stop-playback-calls-teardown ()
  "eve-stop-playback invokes eve--ipc-teardown."
  (eve-test-with-buffer
   (let ((teardown-called nil))
     (cl-letf (((symbol-function 'eve--ipc-teardown)
                (lambda () (setq teardown-called t))))
       (eve-stop-playback)
       (should teardown-called)))))

(ert-deftest eve-playback-update-overlay-creates-overlay ()
  "eve--playback-update-overlay creates overlay with playback face and priority 200."
  (eve-test-with-buffer
   (let ((seg-id (alist-get 'id (car (eve--segments)))))
     (eve--playback-update-overlay seg-id)
     (should (overlayp eve--playback-overlay))
     (should (eq 'eve-playback-face (overlay-get eve--playback-overlay 'face)))
     (should (= 200 (overlay-get eve--playback-overlay 'priority))))))

(ert-deftest eve-playback-update-overlay-nil-removes ()
  "eve--playback-update-overlay nil removes any existing overlay."
  (eve-test-with-buffer
   (let ((seg-id (alist-get 'id (car (eve--segments)))))
     (eve--playback-update-overlay seg-id)
     (should (overlayp eve--playback-overlay))
     (eve--playback-update-overlay nil)
     (should-not eve--playback-overlay))))

(ert-deftest eve-playback-overlay-distinct-from-focus ()
  "Playback and focus overlays are different objects."
  (eve-test-with-buffer
   (let ((seg-id (alist-get 'id (car (eve--segments)))))
     (eve--update-focus-overlay)
     (eve--playback-update-overlay seg-id)
     (should (overlayp eve--playback-overlay))
     (should (overlayp eve--focus-overlay))
     (should-not (eq eve--playback-overlay eve--focus-overlay)))))

(ert-deftest eve-play-source-sets-playback-mode ()
  "eve-play-source sets eve--playback-mode to 'source."
  (eve-test-with-buffer
   (let ((buffer-file-name "/tmp/test.tjm.json"))
     (cl-letf (((symbol-function 'eve--play-with-mpv)
                (lambda (_f _s _e &optional _i) nil))
               ((symbol-function 'file-exists-p)
                (lambda (_f) t)))
       (eve-play-source)
       (should (eq 'source eve--playback-mode))))))

(ert-deftest eve-play-source-populates-source-segments ()
  "eve-play-source sets eve--playback-source-segments."
  (eve-test-with-buffer
   (let ((buffer-file-name "/tmp/test.tjm.json"))
     (cl-letf (((symbol-function 'eve--play-with-mpv)
                (lambda (_f _s _e &optional _i) nil))
               ((symbol-function 'file-exists-p)
                (lambda (_f) t)))
       (eve-play-source)
       (should eve--playback-source-segments)))))

(ert-deftest eve-play-rendered-sets-rendered-mode ()
  "eve-play-rendered sets eve--playback-mode to 'rendered."
  (eve-test-with-buffer
   (let ((buffer-file-name "/tmp/test.tjm.json")
         (fixed-time '(26864 1200 0 0)))
     (cl-letf (((symbol-function 'eve--play-with-mpv)
                (lambda (_f _s _e &optional _i) nil))
               ((symbol-function 'file-exists-p)
                (lambda (_f) t))
               ((symbol-function 'file-attributes)
                (lambda (_f) (list nil nil nil nil nil fixed-time))))
        (eve-play-rendered)
        (should (eq 'rendered eve--playback-mode))))))

(ert-deftest eve-play-rendered-compiles-when-stale ()
  "eve-play-rendered calls eve--run-compile when output file is stale."
  (eve-test-with-buffer
   (let ((buffer-file-name "/tmp/test.tjm.json")
         (compile-called nil))
     (cl-letf (((symbol-function 'eve--run-compile)
                (lambda (_cmd _out &optional _tmp) (setq compile-called t)))
               ((symbol-function 'eve--compile-command)
                (lambda () "fake-command"))
               ((symbol-function 'file-exists-p)
                (lambda (_f) nil)))
       (eve-play-rendered)
       (should compile-called)))))

(ert-deftest eve-playback-pause-resume-sends-cycle ()
  "eve-playback-pause-resume sends cycle/pause IPC command when mpv is live."
  (eve-test-with-buffer
   (let ((sent-commands nil))
     (cl-letf (((symbol-function 'eve--ipc-send)
                (lambda (cmd) (push cmd sent-commands)))
               ((symbol-function 'process-live-p)
                (lambda (_p) t)))
       (setq-local eve--mpv-process (make-process :name "fake" :command '("true")))
       (eve-playback-pause-resume)
       (should (member '("cycle" "pause") sent-commands))
       (ignore-errors (delete-process eve--mpv-process))))))

(ert-deftest eve-seek-short-forward-sends-ipc ()
  (eve-test-with-buffer
   (let ((sent-commands nil))
     (cl-letf (((symbol-function 'eve--ipc-send)
                (lambda (cmd) (push cmd sent-commands)))
               ((symbol-function 'process-live-p)
                (lambda (_p) t)))
       (setq-local eve--mpv-process (make-process :name "fake" :command '("true")))
       (eve-seek-short-forward)
       (should (member '("seek" "5" "relative") sent-commands))
       (ignore-errors (delete-process eve--mpv-process))))))

(ert-deftest eve-seek-short-backward-sends-ipc ()
  (eve-test-with-buffer
   (let ((sent-commands nil))
     (cl-letf (((symbol-function 'eve--ipc-send)
                (lambda (cmd) (push cmd sent-commands)))
               ((symbol-function 'process-live-p)
                (lambda (_p) t)))
       (setq-local eve--mpv-process (make-process :name "fake" :command '("true")))
       (eve-seek-short-backward)
       (should (member '("seek" "-5" "relative") sent-commands))
       (ignore-errors (delete-process eve--mpv-process))))))

(ert-deftest eve-seek-no-process-messages ()
  (eve-test-with-buffer
   (let ((messages nil))
     (cl-letf (((symbol-function 'eve--ipc-send)
                (lambda (_cmd) (error "should not send")))
               ((symbol-function 'process-live-p)
                (lambda (_p) nil))
               ((symbol-function 'message)
                (lambda (fmt &rest args)
                  (push (apply #'format fmt args) messages))))
       (eve-seek-short-forward)
       (should (member "No playback active" messages))))))

(ert-deftest eve-ipc-teardown-restores-spc ()
  "eve--ipc-teardown disables eve-playback-mode, restoring normal SPC."
  (eve-test-with-buffer
   ;; Activate playback mode as would happen during playback
   (eve-playback-mode 1)
   (should eve-playback-mode)
   ;; Teardown should disable it
   (eve--ipc-teardown)
   (should-not eve-playback-mode)))

(ert-deftest eve-video-layout-compute-basic ()
  "eve--compute-video-layout returns percentage mpv geometry and correct Emacs dims."
  (let ((layout (eve--compute-video-layout '(0 25 1440 875) 0.3)))
    ;; video-h = floor(875 * 0.3) = 262; pct = round(30) = 30
    (should (string= "100%x30%+0+0" (plist-get layout :mpv-geometry)))
    (should (= 0    (plist-get layout :emacs-x)))
    (should (= 1440 (plist-get layout :emacs-w)))
    ;; emacs-h = 875 - 262 = 613
    (should (= 613  (plist-get layout :emacs-h)))))

(ert-deftest eve-video-layout-compute-ratio-0-4 ()
  "eve--compute-video-layout respects ratio 0.4."
  (let ((layout (eve--compute-video-layout '(0 0 1920 1080) 0.4)))
    ;; video-h = floor(1080 * 0.4) = 432; emacs-h = 1080 - 432 = 648
    (should (= 648 (plist-get layout :emacs-h)))
    (should (string= "100%x40%+0+0" (plist-get layout :mpv-geometry)))))

(ert-deftest eve-video-layout-geometry-args-basic ()
  "eve--mpv-geometry-args returns expected mpv argument list."
  (let ((args (eve--mpv-geometry-args "100%x60%+0+0")))
    (should (= 5 (length args)))
    (should (member "--geometry=100%x60%+0+0" args))
    (should (member "--macos-geometry-calculation=visible" args))
    (should (member "--no-border" args))
    (should (member "--ontop" args))
    (should (member "--force-window-position" args))))

(ert-deftest eve-video-layout-geometry-args-nil ()
  "eve--mpv-geometry-args returns nil when given nil."
  (should-not (eve--mpv-geometry-args nil)))

(ert-deftest eve-video-layout-save-geometry-idempotent ()
  "eve--save-frame-geometry does not overwrite an existing saved geometry."
  (eve-test-with-buffer
   ;; Set a sentinel value
   (setq-local eve--saved-frame-geometry '(:left 999 :top 999 :width 999 :height 999))
   ;; Call save — should not overwrite
   (eve--save-frame-geometry)
   (should (= 999 (plist-get eve--saved-frame-geometry :left)))))

(ert-deftest eve-video-layout-off-no-geometry-args ()
  "When eve-video-layout is nil, eve--play-with-mpv does not include geometry args."
  (eve-test-with-buffer
   (let ((eve-video-layout nil)
         (captured-args nil)
         (fake-proc (make-process :name "fake" :command '("true") :noquery t)))
     (cl-letf (((symbol-function 'start-process)
                (lambda (_name _buf _prog &rest args)
                  (setq captured-args args)
                  fake-proc))
               ((symbol-function 'executable-find)
                (lambda (_prog) "/usr/bin/mpv"))
               ((symbol-function 'set-process-sentinel) #'ignore)
               ((symbol-function 'run-with-timer) #'ignore))
       (let ((buffer-file-name "/tmp/test.tjm.json"))
         (eve--play-with-mpv "/tmp/test.mp4" 0.0 nil "/tmp/test.sock")))
     (should-not (seq-find (lambda (a) (string-prefix-p "--geometry=" a))
                           captured-args)))))

(ert-deftest eve-ipc-teardown-restores-frame-geometry ()
  "eve--ipc-teardown calls eve--restore-frame-geometry."
  (eve-test-with-buffer
   (let ((restore-called nil))
     (cl-letf (((symbol-function 'eve--restore-frame-geometry)
                (lambda () (setq restore-called t))))
       (eve--ipc-teardown)
       (should restore-called)))))

(ert-deftest eve-mode-map-bindings ()
  "Verify new vim-inspired keybindings in eve-mode-map."
  :expected-result :failed
  (should (eq (lookup-key eve-mode-map "j") #'eve-next-segment))
  (should (eq (lookup-key eve-mode-map "k") #'eve-previous-segment))
  (should (eq (lookup-key eve-mode-map "n") #'eve-next-segment))
  (should (eq (lookup-key eve-mode-map "p") #'eve-previous-segment))
  (should (eq (lookup-key eve-mode-map "J") #'eve-move-segment-down))
  (should (eq (lookup-key eve-mode-map "K") #'eve-move-segment-up))
  (should (eq (lookup-key eve-mode-map "d") #'eve-delete-word))
  (should (eq (lookup-key eve-mode-map "D") #'eve-delete-segment))
  (should (eq (lookup-key eve-mode-map "s") #'eve-split-segment))
  (should (eq (lookup-key eve-mode-map "|") #'eve-split-segment))
  (should (eq (lookup-key eve-mode-map "m") #'eve-merge-with-next))
  (should (eq (lookup-key eve-mode-map ")") #'eve-merge-with-next))
  (should (eq (lookup-key eve-mode-map "u") #'eve-undo))
  (should (eq (lookup-key eve-mode-map (kbd "C-r")) #'eve-redo))
  (should (eq (lookup-key eve-mode-map "e") #'eve-dwim-edit))
  (should (eq (lookup-key eve-mode-map "o") #'eve-insert-marker))
  (should (eq (lookup-key eve-mode-map "t") #'eve-toggle-tag))
  (should (eq (lookup-key eve-mode-map "b") #'eve-edit-broll))
  (should (eq (lookup-key eve-mode-map "B") #'eve-toggle-broll-continue))
  (should (eq (lookup-key eve-mode-map "i") #'eve-edit-speaker))
  (should (eq (lookup-key eve-mode-map "r") #'eve-edit-start-end))
  (should (eq (lookup-key eve-mode-map "f") #'eve-dwim-filler))
  (should (eq (lookup-key eve-mode-map "F") #'eve-delete-fillers))
  (should (eq (lookup-key eve-mode-map (kbd "SPC")) #'eve-play-segment))
  (should (eq (lookup-key eve-mode-map (kbd "RET")) #'eve-play-segment))
  (should (eq (lookup-key eve-mode-map (kbd "<left>")) #'eve-seek-short-backward))
  (should (eq (lookup-key eve-mode-map (kbd "<right>")) #'eve-seek-short-forward))
  (should (eq (lookup-key eve-mode-map (kbd "S-<left>")) #'eve-seek-long-backward))
  (should (eq (lookup-key eve-mode-map (kbd "S-<right>")) #'eve-seek-long-forward))
  (should (eq (lookup-key eve-mode-map "_") #'eve-toggle-separator))
  (should (eq (lookup-key eve-mode-map (kbd "C-RET")) #'eve-toggle-separator))
  (should (eq (lookup-key eve-mode-map "g") #'eve-reload))
  (should (eq (lookup-key eve-mode-map "q") #'eve-dwim-quit))
  (should (eq (lookup-key eve-mode-map "?") #'eve-hydra/body)))

(ert-deftest eve-playback-mode-map-bindings ()
  "Verify playback keybindings in eve-playback-mode-map."
  :expected-result :failed
  (should (eq (lookup-key eve-playback-mode-map (kbd "SPC")) #'eve-playback-pause-resume))
  (should (eq (lookup-key eve-playback-mode-map (kbd "<left>")) #'eve-seek-short-backward))
  (should (eq (lookup-key eve-playback-mode-map (kbd "<right>")) #'eve-seek-short-forward))
  (should (eq (lookup-key eve-playback-mode-map (kbd "S-<left>")) #'eve-seek-long-backward))
  (should (eq (lookup-key eve-playback-mode-map (kbd "S-<right>")) #'eve-seek-long-forward))
  (should (eq (lookup-key eve-playback-mode-map "q") #'eve-stop-playback)))

(ert-deftest eve-cc-prefix-bindings ()
  "Verify C-c prefix bindings in eve-mode-map."
  :expected-result :failed
  (should (eq (lookup-key eve-mode-map (kbd "C-c C-c")) #'eve-compile))
  (should (eq (lookup-key eve-mode-map (kbd "C-c C-v")) #'eve-validate))
  (should (eq (lookup-key eve-mode-map (kbd "C-c C-r")) #'eve-reload))
  (should (eq (lookup-key eve-mode-map (kbd "C-c C-o")) #'eve-open-raw-json))
  (should (eq (lookup-key eve-mode-map (kbd "C-c C-h")) #'eve-hide-deleted-mode))
  (should (eq (lookup-key eve-mode-map (kbd "C-c C-w")) #'eve-toggle-words))
  (should (eq (lookup-key eve-mode-map (kbd "C-c C-l")) #'eve-ruler-mode))
  (should (eq (lookup-key eve-mode-map (kbd "C-c C-t")) #'eve-transcribe))
  (should (eq (lookup-key eve-mode-map (kbd "C-c C-p")) #'eve-play-rendered))
  (should (eq (lookup-key eve-mode-map (kbd "C-c C-s")) #'eve-play-source))
  (should (eq (lookup-key eve-mode-map (kbd "C-c C-b")) #'eve-edit-broll-placeholders))
  (should (eq (lookup-key eve-mode-map (kbd "C-c C-n")) #'eve-edit-notes)))

;;; DWIM dispatcher tests

(ert-deftest eve-dwim-edit-on-broll-summary-calls-edit-broll ()
  (eve-test-with-buffer
   (let ((called nil))
     (cl-letf (((symbol-function 'eve-edit-broll)
                (lambda (_seg) (interactive (list nil)) (setq called 'broll)))
               ((symbol-function 'get-text-property)
                (lambda (_pos prop)
                  (if (eq prop 'eve-broll) '((file . "clip.mp4")) nil)))
               ((symbol-function 'face-at-point)
                (lambda (&rest _) 'eve-meta-face)))
       (eve-dwim-edit)
       (should (eq called 'broll))))))

(ert-deftest eve-dwim-edit-on-text-calls-edit-text ()
  (eve-test-with-buffer
   (let ((called nil))
     (cl-letf (((symbol-function 'eve-edit-text)
                (lambda (_seg) (interactive (list nil)) (setq called 'text)))
               ((symbol-function 'get-text-property)
                (lambda (_pos prop)
                  (if (eq prop 'eve-broll) nil nil)))
               ((symbol-function 'face-at-point)
                (lambda (&rest _) 'eve-text-face)))
       (eve-dwim-edit)
       (should (eq called 'text))))))

(ert-deftest eve-dwim-filler-with-region-calls-add-filler-region ()
  (eve-test-with-buffer
   (let ((called nil))
     (push-mark (point-min) t t)
     (goto-char (point-max))
     (cl-letf (((symbol-function 'eve-add-filler-region)
                (lambda (_start _end) (interactive "r") (setq called 'region)))
               ((symbol-function 'use-region-p)
                (lambda () t)))
       (eve-dwim-filler)
       (should (eq called 'region))))))

(ert-deftest eve-dwim-filler-without-region-calls-add-filler-at-point ()
  (eve-test-with-buffer
   (let ((called nil))
     (cl-letf (((symbol-function 'eve-add-filler-at-point)
                (lambda () (interactive) (setq called 'at-point)))
               ((symbol-function 'use-region-p)
                (lambda () nil)))
       (eve-dwim-filler)
       (should (eq called 'at-point))))))

(ert-deftest eve-dwim-quit-with-live-process-stops-then-quits ()
  (eve-test-with-buffer
   (let ((stop-called nil)
         (quit-called nil))
     (setq-local eve--mpv-process 'fake-process)
     (cl-letf (((symbol-function 'process-live-p)
                (lambda (p) (eq p 'fake-process)))
               ((symbol-function 'eve-stop-playback)
                (lambda () (setq stop-called t)))
               ((symbol-function 'quit-window)
                (lambda (&rest _) (setq quit-called t))))
       (eve-dwim-quit)
       (should stop-called)
       (should quit-called)))))

(ert-deftest eve-dwim-quit-without-process-just-quits ()
  (eve-test-with-buffer
   (let ((stop-called nil)
         (quit-called nil))
     (setq-local eve--mpv-process nil)
     (cl-letf (((symbol-function 'eve-stop-playback)
                (lambda () (setq stop-called t)))
               ((symbol-function 'quit-window)
                (lambda (&rest _) (setq quit-called t))))
       (eve-dwim-quit)
       (should-not stop-called)
       (should quit-called)))))

(provide 'eve-test)

;;; eve-test.el ends here
