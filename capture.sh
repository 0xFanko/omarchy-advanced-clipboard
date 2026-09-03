#!/bin/bash

# Captures the current clipboard as a JSON entry on stdout. In watch mode,
# wl-paste invokes this with the payload on stdin and the mime as $1. Without
# arguments, it snapshots the current selection itself.

set -o pipefail

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy"
IMAGE_DIR="$STATE_DIR/clipboard-images"
mkdir -p "$IMAGE_DIR"

types=$(wl-paste --list-types 2>/dev/null || true)

if [[ ${CLIPBOARD_STATE:-} == "sensitive" ]] || grep -qx 'x-kde-passwordManagerHint' <<<"$types"; then
  exit 0
fi

SOURCE_APP=""
SOURCE_ICON=""
SOURCE_TITLE=""

capture_source() {
  local active_window source_id normalized_id

  active_window=$(hyprctl activewindow -j 2>/dev/null || printf '{}')
  source_id=$(jq -r '(.class // .initialClass // "") | tostring' <<<"$active_window" 2>/dev/null)
  SOURCE_TITLE=$(jq -r '(.title // .initialTitle // "") | tostring' <<<"$active_window" 2>/dev/null)
  [[ $source_id == null ]] && source_id=""
  [[ $SOURCE_TITLE == null ]] && SOURCE_TITLE=""

  SOURCE_ICON="$source_id"
  normalized_id=${source_id,,}
  case "$normalized_id" in
  brave-browser*) SOURCE_APP="Brave Browser"; SOURCE_ICON="brave-browser" ;;
  google-chrome*) SOURCE_APP="Google Chrome"; SOURCE_ICON="google-chrome" ;;
  chromium*) SOURCE_APP="Chromium"; SOURCE_ICON="chromium" ;;
  firefox*|org.mozilla.firefox*) SOURCE_APP="Firefox"; SOURCE_ICON="firefox" ;;
  code|code-oss) SOURCE_APP="Visual Studio Code"; SOURCE_ICON="visual-studio-code" ;;
  codium|vscodium) SOURCE_APP="VSCodium"; SOURCE_ICON="vscodium" ;;
  org.gnome.nautilus|nautilus) SOURCE_APP="Files" ;;
  org.kde.dolphin|dolphin) SOURCE_APP="Dolphin" ;;
  "") ;;
  *) SOURCE_APP=$(sed -E 's/[._-]+/ /g; s/(^| )([a-z])/\1\U\2/g' <<<"$source_id") ;;
  esac
}

enrich_entry() {
  jq -c \
    --arg source_app "$SOURCE_APP" \
    --arg source_icon "$SOURCE_ICON" \
    --arg source_title "$SOURCE_TITLE" \
    'if ($source_app | length) == 0 and ($source_title | length) == 0 then . else
      . + {sourceApp:$source_app, sourceIcon:$source_icon, sourceTitle:$source_title}
    end'
}

capture_source

emit_image() {
  local mime="$1"
  local ext tmp hash file

  ext=${mime#image/}
  [[ $ext == jpeg ]] && ext=jpg

  tmp=$(mktemp --tmpdir="$IMAGE_DIR" clipboard.XXXXXX) || return 0
  cat >"$tmp"
  if [[ ! -s $tmp ]]; then
    rm -f "$tmp"
    return 0
  fi

  hash=$(sha256sum "$tmp" | awk '{print $1}')
  file="$IMAGE_DIR/$hash.$ext"
  if [[ -e $file ]]; then
    rm -f "$tmp"
  else
    mv "$tmp" "$file"
  fi

  jq -cn --arg mime "$mime" --arg path "$file" --arg captured_at "$(date +'%A %H:%M')" \
    '{type:"image", mime:$mime, path:$path, capturedAt:$captured_at}' | enrich_entry
}

emit_text() {
  perl -MEncode=decode,FB_CROAK,LEAVE_SRC -MJSON::PP=encode_json -0777 -e '
    my $raw = <STDIN>;
    exit unless length $raw;

    my $encoding;
    my $heuristic_encoding = 0;
    if ($raw =~ /^(?:\xFF\xFE|\xFE\xFF)/) {
      $encoding = "UTF-16";
    } elsif (length($raw) % 2 == 0 && index($raw, "\0") >= 0) {
      my $units = length($raw) / 2;
      my $nuls = $raw =~ tr/\0/\0/;

      # Neither byte lane can reach the padding threshold when the entire
      # payload contains fewer NULs than that, so avoid two full string passes.
      if ($nuls * 4 >= $units * 3) {
        my $even_bytes = $raw;
        $even_bytes =~ s/(.)./$1/sg;
        my $even_nuls = $even_bytes =~ tr/\0/\0/;
        undef $even_bytes;

        my $odd_bytes = $raw;
        $odd_bytes =~ s/.(.)/$1/sg;
        my $odd_nuls = $odd_bytes =~ tr/\0/\0/;

        # BOM-less UTF-16 is indistinguishable from NUL-separated bytes. Decode
        # only when at least three quarters of the code units have consistent
        # padding and fewer than one quarter have NULs in the opposite byte.
        if ($odd_nuls * 4 >= $units * 3 && $even_nuls * 4 < $units) {
          $encoding = "UTF-16LE";
          $heuristic_encoding = 1;
        } elsif ($even_nuls * 4 >= $units * 3 && $odd_nuls * 4 < $units) {
          $encoding = "UTF-16BE";
          $heuristic_encoding = 1;
        }
      }
    }

    my $text = $encoding ? eval { decode($encoding, $raw, FB_CROAK | LEAVE_SRC) } : undef;
    if ($heuristic_encoding && defined($text) && $text =~ /[\x00-\x08\x0E-\x1A\x1C-\x1F]/) {
      $text = undef;
    }
    $text = decode("UTF-8", $raw) unless defined $text;
    print "{\"type\":\"text\",\"text\":", encode_json($text), "}\n";
  '
}

case "${1:-}" in
text) emit_text | enrich_entry; exit 0 ;;
image/*) emit_image "$1"; exit 0 ;;
esac

for mime in image/png image/jpeg image/webp image/gif image/bmp image/tiff; do
  if grep -qx "$mime" <<<"$types"; then
    timeout 2s wl-paste --type "$mime" 2>/dev/null | emit_image "$mime"
    exit 0
  fi
done

if grep -q '^text/' <<<"$types" || grep -qx 'UTF8_STRING' <<<"$types" || grep -qx 'STRING' <<<"$types"; then
  wl-paste --type text --no-newline 2>/dev/null | emit_text | enrich_entry
fi
