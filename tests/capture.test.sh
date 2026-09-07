#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

mkdir -p "$TEST_DIR/plugin" "$TEST_DIR/state"
cp "$ROOT/capture.sh" "$TEST_DIR/plugin/capture.sh"
chmod +x "$TEST_DIR/plugin/capture.sh"
CAPTURE_SCRIPT="$TEST_DIR/plugin/capture.sh"

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
export XDG_STATE_HOME="$TEST_DIR/state"

capture_text() {
  printf 'test value' | "$CAPTURE_SCRIPT" text
}

included=$(MOCK_SOURCE_CLASS=firefox capture_text)
jq -e '.text == "test value" and .sourceApp == "Firefox"' <<<"$included" >/dev/null

cp "$ROOT/tests/fixtures/excluded-applications.json" "$TEST_DIR/plugin/clipboard.json"

excluded_by_name=$(MOCK_SOURCE_CLASS=brave-browser capture_text)
[[ -z $excluded_by_name ]]

excluded_by_class=$(MOCK_SOURCE_CLASS=org.keepassxc.KeePassXC capture_text)
[[ -z $excluded_by_class ]]

still_included=$(MOCK_SOURCE_CLASS=firefox capture_text)
jq -e '.sourceApp == "Firefox"' <<<"$still_included" >/dev/null

jq -n '{excludedApplications: [42]}' >"$TEST_DIR/plugin/clipboard.json"
numeric_value_is_ignored=$(MOCK_SOURCE_CLASS=42 capture_text)
jq -e '.sourceApp == "42"' <<<"$numeric_value_is_ignored" >/dev/null

jq -n '{excludedApplications: ([range(100) | "app\(.)"] + ["firefox"])}' >"$TEST_DIR/plugin/clipboard.json"
beyond_limit_is_included=$(MOCK_SOURCE_CLASS=firefox capture_text)
jq -e '.sourceApp == "Firefox"' <<<"$beyond_limit_is_included" >/dev/null

cp "$ROOT/tests/fixtures/malformed-config.json" "$TEST_DIR/plugin/clipboard.json"
malformed_falls_back=$(MOCK_SOURCE_CLASS=firefox capture_text)
jq -e '.sourceApp == "Firefox"' <<<"$malformed_falls_back" >/dev/null

printf 'capture tests: OK\n'
