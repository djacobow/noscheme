;;; noscheme-eglot.el --- NoScheme language-server setup -*- lexical-binding: t; -*-

;; Copyright (C) 2026 NoScheme contributors
;; SPDX-License-Identifier: GPL-3.0-or-later
;; Package-Requires: ((emacs "29.1"))

;;; Commentary:
;; Add this directory to load-path and (require 'noscheme-eglot).
;; NoScheme buffers then start Eglot automatically.  See docs/EDITOR_SUPPORT.md.

;;; Code:

(require 'cl-lib)
(require 'project)
(require 'eglot)
(require 'noscheme-mode)

(defconst noscheme-eglot--checkout
  (expand-file-name "../.." (file-name-directory (or load-file-name buffer-file-name)))
  "Checkout containing this integration.")

(defcustom noscheme-eglot-server-command nil
  "Server executable and arguments, or nil to discover the Python launcher.
Use this checkout's .venv/bin/noscheme-lsp, then noscheme-lsp on PATH.
An explicit command list can select the native implementation or --project."
  :type '(choice (const :tag "Discover launcher" nil) (repeat string))
  :group 'noscheme)

(defun noscheme-eglot--contact (&optional _interactive)
  "Return the configured language-server command for Eglot."
  (or noscheme-eglot-server-command
      (let ((local (expand-file-name ".venv/bin/noscheme-lsp" noscheme-eglot--checkout)))
        (cond ((file-executable-p local) (list local))
              ((executable-find "noscheme-lsp") (list (executable-find "noscheme-lsp")))
              (t (user-error "Run uv sync --locked in the NoScheme checkout, or set noscheme-eglot-server-command"))))))

(defun noscheme-eglot--project (directory)
  "Find a NoScheme project above DIRECTORY for a NoScheme buffer."
  (when (derived-mode-p 'noscheme-mode)
    (when-let* ((root (locate-dominating-file directory "noscheme.toml")))
      (cons 'noscheme (expand-file-name root)))))

(cl-defmethod project-root ((project (head noscheme)))
  "Return the root of a NoScheme PROJECT."
  (cdr project))

(add-hook 'project-find-functions #'noscheme-eglot--project)
(add-to-list 'eglot-server-programs '(noscheme-mode . noscheme-eglot--contact))
(add-hook 'noscheme-mode-hook #'eglot-ensure)

(provide 'noscheme-eglot)
;;; noscheme-eglot.el ends here
