from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db import dumps


def _num(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def _frame_rate(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        den = _num(denominator)
        if not den:
            return None
        num = _num(numerator)
        return round(num / den, 3) if num is not None else None
    return _num(value)


def _resolution_bucket(width: int | None, height: int | None) -> str | None:
    if not width or not height:
        return None
    if height >= 2160 or width >= 3840:
        return "4K"
    if height >= 1440:
        return "1440p"
    if height >= 1080:
        return "1080p"
    if height >= 720:
        return "720p"
    if height >= 480:
        return "480p"
    return "SD"


def _is_hdr(stream: dict[str, Any]) -> tuple[int, str | None]:
    transfer = (stream.get("color_transfer") or "").lower()
    primaries = (stream.get("color_primaries") or "").lower()
    side_data = dumps(stream.get("side_data_list", []))
    if "smpte2084" in transfer or "arib-std-b67" in transfer:
        return 1, transfer
    if "bt2020" in primaries and ("Mastering display" in side_data or "Content light" in side_data):
        return 1, "bt2020"
    return 0, None


def normalize_probe(path: Path, stat_result: Any, raw: dict[str, Any]) -> dict[str, Any]:
    streams = raw.get("streams") or []
    format_info = raw.get("format") or {}
    video_streams = [item for item in streams if item.get("codec_type") == "video"]
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    subtitle_streams = [item for item in streams if item.get("codec_type") == "subtitle"]
    primary_video = video_streams[0] if video_streams else {}
    primary_audio = audio_streams[0] if audio_streams else {}

    duration_seconds = _num(format_info.get("duration")) or _num(primary_video.get("duration"))
    overall_bitrate = _int(format_info.get("bit_rate"))
    size_bytes = stat_result.st_size
    size_per_hour_gb = None
    if duration_seconds and duration_seconds > 0:
        size_per_hour_gb = round((size_bytes / (1024**3)) / (duration_seconds / 3600), 3)
    bitrate_mbps = round(overall_bitrate / 1_000_000, 3) if overall_bitrate else None
    width = _int(primary_video.get("width"))
    height = _int(primary_video.get("height"))
    is_hdr, hdr_format = _is_hdr(primary_video)

    subtitle_codecs = sorted({item.get("codec_name") for item in subtitle_streams if item.get("codec_name")})
    subtitle_languages = sorted(
        {
            (item.get("tags") or {}).get("language")
            for item in subtitle_streams
            if (item.get("tags") or {}).get("language")
        }
    )
    image_subs = {"hdmv_pgs_subtitle", "dvd_subtitle", "xsub"}
    audio_summary = "; ".join(_audio_label(item) for item in audio_streams if _audio_label(item))

    return {
        "path": str(path),
        "directory": str(path.parent),
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": size_bytes,
        "modified_time_ns": stat_result.st_mtime_ns,
        "created_time_ns": getattr(stat_result, "st_birthtime", None)
        and int(stat_result.st_birthtime * 1_000_000_000),
        "format_name": format_info.get("format_name"),
        "format_long_name": format_info.get("format_long_name"),
        "container": _container(format_info, path),
        "duration_seconds": duration_seconds,
        "overall_bitrate": overall_bitrate,
        "primary_video_codec": primary_video.get("codec_name"),
        "primary_video_codec_long": primary_video.get("codec_long_name"),
        "primary_video_profile": primary_video.get("profile"),
        "width": width,
        "height": height,
        "resolution_bucket": _resolution_bucket(width, height),
        "frame_rate": _frame_rate(primary_video.get("avg_frame_rate") or primary_video.get("r_frame_rate")),
        "video_bitrate": _int(primary_video.get("bit_rate")),
        "pixel_format": primary_video.get("pix_fmt"),
        "bit_depth": _int(primary_video.get("bits_per_raw_sample")),
        "color_space": primary_video.get("color_space"),
        "color_transfer": primary_video.get("color_transfer"),
        "color_primaries": primary_video.get("color_primaries"),
        "hdr_format": hdr_format,
        "is_hdr": is_hdr,
        "is_interlaced": 1 if (primary_video.get("field_order") or "").lower() not in {"", "progressive"} else 0,
        "primary_audio_codec": primary_audio.get("codec_name"),
        "primary_audio_codec_long": primary_audio.get("codec_long_name"),
        "primary_audio_channels": _int(primary_audio.get("channels")),
        "primary_audio_channel_layout": primary_audio.get("channel_layout"),
        "primary_audio_language": (primary_audio.get("tags") or {}).get("language"),
        "audio_stream_count": len(audio_streams),
        "subtitle_stream_count": len(subtitle_streams),
        "video_stream_count": len(video_streams),
        "subtitle_codecs": ", ".join(subtitle_codecs),
        "subtitle_languages": ", ".join(subtitle_languages),
        "has_forced_subtitles": 1
        if any((item.get("disposition") or {}).get("forced") for item in subtitle_streams)
        else 0,
        "has_image_subtitles": 1
        if any((item.get("codec_name") or "").lower() in image_subs for item in subtitle_streams)
        else 0,
        "audio_summary": audio_summary,
        "size_per_hour_gb": size_per_hour_gb,
        "bitrate_mbps": bitrate_mbps,
        "raw_probe_json": dumps(raw),
        "streams": [_normalize_stream(item) for item in streams],
        "chapters": [_normalize_chapter(index, item) for index, item in enumerate(raw.get("chapters") or [])],
    }


def _container(format_info: dict[str, Any], path: Path) -> str:
    names = str(format_info.get("format_name") or "").split(",")
    if names and names[0]:
        return names[0].lower()
    return path.suffix.lower().lstrip(".")


def _audio_label(stream: dict[str, Any]) -> str:
    codec = stream.get("codec_name") or "unknown"
    channels = stream.get("channels")
    layout = stream.get("channel_layout")
    language = (stream.get("tags") or {}).get("language")
    title = (stream.get("tags") or {}).get("title")
    parts = [codec.upper()]
    if layout:
        parts.append(str(layout))
    elif channels:
        parts.append(f"{channels}ch")
    if language:
        parts.append(language)
    if title:
        parts.append(title)
    return " ".join(parts)


def _normalize_stream(stream: dict[str, Any]) -> dict[str, Any]:
    tags = stream.get("tags") or {}
    disposition = stream.get("disposition") or {}
    return {
        "stream_index": stream.get("index"),
        "stream_type": stream.get("codec_type"),
        "codec_name": stream.get("codec_name"),
        "codec_long_name": stream.get("codec_long_name"),
        "profile": stream.get("profile"),
        "language": tags.get("language"),
        "title": tags.get("title"),
        "disposition_default": 1 if disposition.get("default") else 0,
        "disposition_forced": 1 if disposition.get("forced") else 0,
        "width": _int(stream.get("width")),
        "height": _int(stream.get("height")),
        "frame_rate": _frame_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
        "channels": _int(stream.get("channels")),
        "channel_layout": stream.get("channel_layout"),
        "sample_rate": _int(stream.get("sample_rate")),
        "bit_rate": _int(stream.get("bit_rate")),
        "bits_per_raw_sample": _int(stream.get("bits_per_raw_sample")),
        "pixel_format": stream.get("pix_fmt"),
        "color_space": stream.get("color_space"),
        "color_transfer": stream.get("color_transfer"),
        "color_primaries": stream.get("color_primaries"),
        "duration_seconds": _num(stream.get("duration")),
        "raw_stream_json": dumps(stream),
    }


def _normalize_chapter(index: int, chapter: dict[str, Any]) -> dict[str, Any]:
    tags = chapter.get("tags") or {}
    return {
        "chapter_index": index,
        "start_seconds": _num(chapter.get("start_time")),
        "end_seconds": _num(chapter.get("end_time")),
        "title": tags.get("title"),
        "raw_chapter_json": dumps(chapter),
    }
