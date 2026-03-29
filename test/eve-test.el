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

(provide 'eve-test)

;;; eve-test.el ends here
