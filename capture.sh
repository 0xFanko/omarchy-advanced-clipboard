#!/bin/bash
# Compatibility entry point. The Python helper enforces the byte/deadline
# boundary before decoding or writing clipboard data.
set -eu
SCRIPT_DIR=$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")
if (($#)); then
  exec /usr/bin/python3 "$SCRIPT_DIR/clipboard_helper.py" capture "$1"
fi
exec /usr/bin/python3 "$SCRIPT_DIR/clipboard_helper.py" snapshot
