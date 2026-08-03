#!/usr/bin/env bash
set -euo pipefail

smoke_dir="$(mktemp -d)"
source_mp4="${smoke_dir}/dual-audio.mp4"
source_mkv="${smoke_dir}/dual-audio.mkv"
ffmpeg_output="${smoke_dir}/ffmpeg-cleaned.mkv"
mkvmerge_output="${smoke_dir}/mkvmerge-cleaned.mkv"

ffmpeg -hide_banner -loglevel error \
  -f lavfi -i "color=size=64x64:rate=24:color=black" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000" \
  -f lavfi -i "sine=frequency=880:sample_rate=48000" \
  -t 1 \
  -map 0:v:0 -map 1:a:0 -map 2:a:0 \
  -c:v mpeg4 -c:a aac \
  -metadata:s:a:0 language=eng -metadata:s:a:1 language=spa \
  -disposition:a:0 default -disposition:a:1 0 \
  "$source_mp4"

ffmpeg -hide_banner -loglevel error -i "$source_mp4" \
  -map 0 -map -0:2 -map_metadata 0 -map_chapters 0 -c copy \
  "$ffmpeg_output"

ffmpeg_audio_count="$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$ffmpeg_output" | wc -l | tr -d ' ')"
test "$ffmpeg_audio_count" = "1"

mkvmerge -o "$source_mkv" "$source_mp4"
remove_track_id="$(mkvmerge -J "$source_mkv" | python -c 'import json,sys; tracks=[t["id"] for t in json.load(sys.stdin)["tracks"] if t["type"] == "audio"]; assert len(tracks) == 2; print(tracks[-1])')"
mkvmerge -o "$mkvmerge_output" --audio-tracks "!${remove_track_id}" "$source_mkv"

mkvmerge_audio_count="$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$mkvmerge_output" | wc -l | tr -d ' ')"
test "$mkvmerge_audio_count" = "1"

echo "Audio-cleanup smoke test passed for FFmpeg and MKVToolNix."
