#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

mkdir -p "$TEST_DIR/config/omarchy" "$TEST_DIR/state"

wl-paste() {
  if [[ ${1:-} == --list-types ]]; then
    printf 'text/plain\n'
  fi
}

hyprctl() {
  jq -cn --arg class "${MOCK_SOURCE_CLASS:-}" --arg title "Test window" \
    '{class:$class,title:$title}'
}

export -f wl-paste hyprctl
export XDG_CONFIG_HOME="$TEST_DIR/config"
export XDG_STATE_HOME="$TEST_DIR/state"

capture_text() {
  printf 'test value' | "$ROOT/capture.sh" text
}

included=$(MOCK_SOURCE_CLASS=firefox capture_text)
jq -e '.text == "test value" and .sourceApp == "Firefox"' <<<"$included" >/dev/null

cp "$ROOT/tests/fixtures/excluded-applications.json" "$XDG_CONFIG_HOME/omarchy/clipboard.json"

excluded_by_name=$(MOCK_SOURCE_CLASS=brave-browser capture_text)
[[ -z $excluded_by_name ]]

excluded_by_class=$(MOCK_SOURCE_CLASS=org.keepassxc.KeePassXC capture_text)
[[ -z $excluded_by_class ]]

still_included=$(MOCK_SOURCE_CLASS=firefox capture_text)
jq -e '.sourceApp == "Firefox"' <<<"$still_included" >/dev/null

cp "$ROOT/tests/fixtures/malformed-config.json" "$XDG_CONFIG_HOME/omarchy/clipboard.json"
malformed_falls_back=$(MOCK_SOURCE_CLASS=firefox capture_text)
jq -e '.sourceApp == "Firefox"' <<<"$malformed_falls_back" >/dev/null

printf 'capture tests: OK\n'
