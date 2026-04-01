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

(ert-deftest eve-delete-word-updates-segment ()
  "Deleting a word rewrites text, words array, and timings."
  (eve-test-with-buffer
   (should (eve--goto-segment "seg-1"))
   (should (eve--goto-word-by-index "seg-1" 1))
   (forward-char 1)
   (call-interactively #'eve-delete-word)
   (let* ((segment (car (eve--segments)))
	  (words (alist-get 'words segment)))
     (should (= 3 (length words)))
     (should (equal (eve-test--segment-tokens segment)
		    '("alpha" "gamma" "delta")))
     (should (= (alist-get 'start segment) 1.0))
     (should (= (alist-get 'end segment) 4.0))
     (should (equal (alist-get 'text segment) "alpha gamma delta"))
     (should (looking-at "gamma")))))

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
	    (broll (alist-get 'broll segment)))
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
	     (setf (alist-get 'broll segment)
		   (list (cons 'file template-file))))
	   (let ((segment (car (eve--segments))))
	     (should (alist-get 'broll segment)))
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
		  (broll (alist-get 'broll segment))
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

(ert-deftest eve-transcribe-async-builds-command ()
  "Launcher clears the transcribe buffer and builds argv for `make-process`."
  (let ((eve-cli-program "eve-custom")
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
                           "--output" "/tmp/session.tjm.json")))
          (should (plist-get captured-plist :noquery))
          (should (functionp (plist-get captured-plist :sentinel)))
          (should (equal (with-current-buffer buffer (buffer-string)) ""))
          (should (equal last-message
                         "Started eve transcribe -> /tmp/session.tjm.json")))
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

(provide 'eve-test)

;;; eve-test.el ends here
