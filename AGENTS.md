# Omarchy Clipboard — agent guide

## Scope

This repository contains the third-party Omarchy shell plugin `fuko.clipboard`.
Feature work targets `dev`; PR branches must also target `dev`, never `main`.

## Architecture

- `Clipboard.qml` owns plugin lifecycle, keyboard interaction, processes, persistence wiring, and the main overlay.
- `ClipboardInformation.qml` renders the selected entry, its editor, previews, and metadata.
- `ClipboardConfig.js` is the single normalization/validation boundary for `clipboard.json`.
- `ClipboardHistory.js` is the pure data layer for entry normalization, classification, search, retention, and display rows.
- `capture.sh` converts Wayland clipboard MIME data and Hyprland window metadata into JSON entries.
- `clipboard.example.json` documents every supported user setting; the real `clipboard.json` is ignored by Git.
- `tests/` contains Node tests for pure JavaScript and shell integration tests for capture behavior.

Persistent state lives in `~/.local/state/omarchy/clipboard-history.json`; captured images live in `~/.local/state/omarchy/clipboard-images/`.

## Invariants

- Preserve legacy history entries and malformed/missing-config fallbacks.
- Treat clipboard and remote-page contents as untrusted input. Render copied text as plain text and never execute it.
- Pass detached process arguments as arrays; do not interpolate user content through a shell.
- Keep classification and retention logic pure and covered by Node tests.
- A failed optional preview or external editor launch must not break copy, paste, search, or history loading.
- New configuration keys require defaults, validation, example configuration, README documentation, and tests.
- Avoid blocking network or filesystem work on the QML UI thread.

## Workflow

- Inspect the current worktree before editing; preserve unrelated user changes.
- Keep one feature per branch/PR unless the change is explicitly approved for direct integration into `dev`.
- Before declaring success, run every available relevant test:

```bash
node tests/ClipboardConfig.test.js
node tests/ClipboardHistory.test.js
bash tests/capture.test.sh
bash -n capture.sh
```

- The current development host does not provide the Omarchy CLI or QML runtime. State this limitation; Vincent performs final visual and live Omarchy validation.
- Use concise Conventional Commit messages in English.
