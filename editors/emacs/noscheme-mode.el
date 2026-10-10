;;; noscheme-mode.el --- Edit NoScheme circuit designs -*- lexical-binding: t; -*-

;; Copyright (C) 2026 NoScheme contributors
;; SPDX-License-Identifier: GPL-3.0-or-later
;; Package-Requires: ((emacs "29.1"))

;;; Commentary:
;; A major mode for .nosch files.  Load noscheme-eglot for language-server support.

;;; Code:

(require 'newcomment)

(defgroup noscheme nil
  "Edit NoScheme circuit designs."
  :group 'languages)

(defvar noscheme-mode-syntax-table
  (let ((table (make-syntax-table)))
    (modify-syntax-entry ?_ "w" table)
    (modify-syntax-entry ?# "w" table)
    (modify-syntax-entry ?/ ". 124b" table)
    (modify-syntax-entry ?* ". 23" table)
    (modify-syntax-entry ?\n "> b" table)
    table)
  "Syntax table for NoScheme, including non-nesting C-style comments.")

(defconst noscheme-font-lock-keywords
  `((,(concat "\\_<" (regexp-opt '("component" "block" "board" "bundle"))
              "\\_>[ \t]+\\([[:alnum:]_#]+\\)")
     (0 font-lock-keyword-face) (1 font-lock-type-face))
    (,(regexp-opt '("from" "import" "as" "extend" "global") 'symbols)
     . font-lock-keyword-face)
    (,(regexp-opt '("true" "false" "no_connect") 'symbols)
     . font-lock-constant-face)
    (,(regexp-opt '("number" "string" "resistance" "capacitance" "inductance"
                    "voltage" "current" "frequency" "power" "time") 'symbols)
     . font-lock-type-face)
    ("\\_<\\(?:role\\|net\\|bias\\|unused\\|num\\)\\.[[:alnum:]_#]+"
     . font-lock-builtin-face)
    ("^[ \t]*\\([[:alpha:]_#][[:alnum:]_#]*\\)[ \t]*:"
     (1 font-lock-variable-name-face)))
  "Highlighting shared by all NoScheme buffers.")

(defun noscheme-indent-line ()
  "Indent from the preceding code line, using two spaces for a new body.
Use `noscheme-dedent-line' to leave a body; full formatting comes from Eglot."
  (interactive)
  (let ((offset (- (point) (line-beginning-position)))
        (indent
         (save-excursion
           (back-to-indentation)
           (let ((opener (nth 1 (syntax-ppss))))
             (if opener
                 (let ((closing (looking-at-p "[])]")))
                   (goto-char opener)
                   (+ (current-indentation) (if closing 0 2)))
               (let ((found nil))
                 (while (and (not found) (= (forward-line -1) 0))
                   (back-to-indentation)
                   (unless (or (eolp) (nth 4 (syntax-ppss))
                               (looking-at-p "//\\|/\\*"))
                     (setq found t)))
                 (if (not found) 0
                   (let ((base (current-indentation)))
                     (end-of-line)
                     (when (nth 4 (syntax-ppss))
                       (goto-char (nth 8 (syntax-ppss))))
                     (skip-chars-backward " \t")
                     (+ base (if (and (eq (char-before) ?:)
                                      (not (nth 3 (syntax-ppss))))
                                 2 0))))))))))
    (indent-line-to indent)
    (when (> offset indent)
      (move-to-column offset))))

(defun noscheme-dedent-line ()
  "Remove one two-space indentation level from the current line."
  (interactive)
  (indent-line-to (max 0 (- (current-indentation) 2))))

(defvar noscheme-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "<backtab>") #'noscheme-dedent-line)
    map)
  "Keymap for `noscheme-mode'.")

;;;###autoload
(define-derived-mode noscheme-mode prog-mode "NoScheme"
  "Major mode for NoScheme circuit designs."
  :syntax-table noscheme-mode-syntax-table
  (setq-local font-lock-defaults '(noscheme-font-lock-keywords))
  (setq-local indent-tabs-mode nil)
  (setq-local tab-width 2)
  (setq-local indent-line-function #'noscheme-indent-line)
  (setq-local comment-start "// ")
  (setq-local comment-end "")
  (setq-local comment-start-skip "\\(?://+\\|/\\*+\\)[ \t]*"))

;;;###autoload
(add-to-list 'auto-mode-alist '("\\.nosch\\'" . noscheme-mode))

(provide 'noscheme-mode)
;;; noscheme-mode.el ends here
