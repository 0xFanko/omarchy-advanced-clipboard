<p align="center">
  <img src="assets/clipboard-logo.png" alt="Advanced Clipboard logo" width="112">
</p>

<h1 align="center">Advanced Clipboard for Omarchy</h1>

<p align="center">
  A keyboard-first clipboard history plugin with search, previews, editing, retention controls, and application exclusions.
</p>

## Features

- Search and navigate clipboard history from the keyboard.
- Preview copied text, files, and images.
- Display native Open Graph and Twitter Card previews for copied links.
- Show the source application and capture details.
- Edit text entries directly from the clipboard history.
- Copy, paste, open, or delete individual entries.
- Clear the complete history.
- Configure every internal keyboard shortcut.
- Exclude selected applications from clipboard capture.
- Automatically remove entries older than a configurable retention period.

## Requirements

- Omarchy with Quickshell
- `python3`
- `curl`
- ImageMagick with JPEG, PNG, GIF, and WebP support
- `wl-clipboard`, `jq`, and `hyprctl`

These dependencies are included with a standard Omarchy installation.

## Installation

Before installing Advanced Clipboard, disable Omarchy's built-in clipboard plugin to prevent both plugins from running at the same time:

```bash
omarchy plugin disable omarchy.clipboard
```

## Configuration

The plugin reads its user configuration from:

```text
<plugin-directory>/clipboard.json
```

Copy the example file from the plugin directory before changing any settings:

```bash
cp clipboard.example.json clipboard.json
```

The personal `clipboard.json` file is ignored by Git so local settings do not interfere with plugin updates.

```json
{
  "shortcuts": {
    "close": "Escape",
    "previousEntry": "Up",
    "nextEntry": "Down",
    "previousPage": "PgUp",
    "nextPage": "PgDown",
    "firstEntry": "Home",
    "lastEntry": "End",
    "pasteEntry": "Return",
    "copyEntry": "Shift+Return",
    "openEntry": "Alt+Return",
    "editEntry": "Ctrl+E",
    "saveEdit": "Ctrl+S",
    "deleteEntry": "Ctrl+X",
    "clearHistory": "Ctrl+Shift+X"
  },
  "historyRetentionDays": 30,
  "excludedApplications": [
    "Brave Browser",
    "org.keepassxc.KeePassXC"
  ]
}
```

### Keyboard shortcuts

| Setting | Action |
| --- | --- |
| `close` | Clear the search field, then close the overlay |
| `previousEntry` / `nextEntry` | Select the previous or next entry |
| `previousPage` / `nextPage` | Move backward or forward by six entries |
| `firstEntry` / `lastEntry` | Select the first or last entry |
| `pasteEntry` | Paste the selected entry |
| `copyEntry` | Copy the selected entry without pasting it |
| `openEntry` | Open the selected entry |
| `editEntry` | Edit the selected text entry |
| `saveEdit` | Save the current edit |
| `deleteEntry` | Delete the selected entry |
| `clearHistory` | Clear the complete history |

Shortcuts use Qt syntax such as `Ctrl+X` or `Shift+Delete` and must be unique. Configuration changes are reloaded automatically. The global `Super+Ctrl+V` shortcut is managed separately by Hyprland.

Avoid assigning a single letter: while the clipboard overlay is open, that letter could no longer be entered in the search field.

Only entries shown as `Text` can be edited. `editEntry` opens the editor, `saveEdit` saves the new content, and `close` cancels the edit.

### History retention

`historyRetentionDays` defines the maximum entry age in days:

- `0` keeps history indefinitely while the 500-entry limit still applies.
- Valid values are integers from `0` to `36500`.
- Negative, fractional, or string values are invalid and fall back to `0`.
- Values above `36500` are capped at `36500`.
- Legacy entries without a timestamp are preserved because their age cannot be determined reliably.

Captured image files are removed when their corresponding entries expire. External files referenced through `file://` URLs are never deleted.

### Excluded applications

Applications are matched by their display name or Hyprland class, case-insensitively. Empty values and duplicates are ignored, and the list is limited to the first 100 unique applications.

List the classes of currently open applications with:

```bash
hyprctl clients -j | jq -r '.[].class' | sort -u
```

New clipboard content from excluded applications is not recorded. Existing history entries are not removed.

## Link previews and security

HTTP and HTTPS entries can display a native preview card built from Open Graph or Twitter Card metadata: image, title, description, and site name. Fetching runs outside the interface process through `link_preview.py`.

The preview implementation treats every remote page as untrusted:

- Every DNS resolution and redirect, including image redirects, must resolve to a public address.
- Private, loopback, link-local, multicast, and metadata-service addresses are blocked.
- The validated address is pinned for each connection to reduce DNS-rebinding risk.
- Proxy environment variables are ignored to preserve network pinning.
- HTML responses are limited to 2 MiB and images to 5 MiB.
- JPEG, PNG, GIF, and WebP inputs are decoded in an isolated ImageMagick subprocess with resource and dimension limits, then re-encoded as Qt-compatible PNG files.
- Preview images are cached for 24 hours, up to 100 files and 100 MiB; older files are pruned automatically.

Preview loading starts 400 ms after selection to avoid unnecessary requests during rapid navigation. If an image is absent, invalid, or blocked, available title and description metadata remain visible. A preview failure never blocks copy, paste, open, edit, search, or history loading.

## Data storage

- History: `~/.local/state/omarchy/clipboard-history.json`
- Captured images: `~/.local/state/omarchy/clipboard-images/`
- Link-preview cache: the user cache directory under `omarchy-clipboard/link-previews/`

## Remove

Disable Advanced Clipboard, then remove it:

```bash
omarchy plugin disable 0xfanko.advanced-clipboard
omarchy plugin remove 0xfanko.advanced-clipboard
```

## Development

Feature development takes place on the `dev` branch. The `main` branch contains the minimal distributable plugin.
