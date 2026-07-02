from __future__ import annotations

from typing import Any


LEGACY_CONTAINERS = {"avi", "wmv", "mpg", "mpeg", "flv", "ogm"}
LEGACY_CODECS = {"mpeg2video", "mpeg4", "msmpeg4", "wmv3", "vc1", "theora", "divx", "xvid"}
MODERN_CODECS = {"hevc", "h265", "av1", "h264"}
IMAGE_SUBS = {"hdmv_pgs_subtitle", "dvd_subtitle", "xsub"}
LOSSLESS_AUDIO = {"truehd", "dts", "dca", "flac", "mlp"}


def recommend(file_row: dict[str, Any]) -> dict[str, Any]:
    if file_row.get("is_missing"):
        return _result("Missing", "File is missing from its configured root.", ["File was not seen in the latest scan."])
    if file_row.get("probe_error"):
        return _result("Error", "The file could not be probed.", [file_row.get("probe_error") or "ffprobe failed."])

    container = (file_row.get("container") or file_row.get("extension") or "").lower().lstrip(".")
    video_codec = (file_row.get("primary_video_codec") or "").lower()
    audio_codec = (file_row.get("primary_audio_codec") or "").lower()
    height = file_row.get("height") or 0
    bitrate_mbps = file_row.get("bitrate_mbps") or 0
    reasons: list[str] = []
    warnings: list[str] = []

    if height >= 2160:
        reasons.append("4K media should be reviewed before transcoding.")
    if file_row.get("is_hdr"):
        reasons.append("HDR metadata is present.")
    if file_row.get("audio_stream_count", 0) > 1:
        reasons.append("Multiple audio tracks are present.")
    if file_row.get("subtitle_stream_count", 0) > 1:
        reasons.append("Multiple subtitle tracks are present.")
    if file_row.get("has_image_subtitles"):
        reasons.append("Image-based subtitles may not survive every target container.")
    if file_row.get("is_interlaced"):
        reasons.append("Interlaced video needs manual quality review.")
    if audio_codec in LOSSLESS_AUDIO:
        reasons.append("High-value or lossless audio is present.")
    if reasons:
        return _result("Review", "Complex media should be reviewed before conversion.", reasons, warnings)

    if container in LEGACY_CONTAINERS:
        reasons.append(f"Legacy container: {container.upper()}.")
    if video_codec in LEGACY_CODECS:
        reasons.append(f"Legacy video codec: {video_codec}.")
    if _above_threshold(height, bitrate_mbps):
        reasons.append(f"Bitrate {bitrate_mbps:.1f} Mbps is high for this resolution.")
    if reasons:
        return _result("Easy Win", "Likely conversion candidate.", reasons, warnings)

    if container in {"mov", "mpegts", "m2ts", "ts"} and video_codec in MODERN_CODECS:
        return _result(
            "Remux Only",
            "Video codec is acceptable; container cleanup may be enough.",
            [f"Container {container.upper()} can likely be remuxed without video re-encode."],
        )

    if video_codec in {"hevc", "h265", "av1"}:
        return _result("Already Modern", "Modern video codec detected.", [f"Video codec is {video_codec}."])
    if video_codec == "h264" and not _above_threshold(height, bitrate_mbps):
        return _result("Already Modern", "H.264 with reasonable bitrate.", ["Broadly compatible H.264 file."])
    return _result("Skip", "No obvious conversion benefit detected.", ["No legacy codec/container or high bitrate signal."])


def transcode_warnings(file_row: dict[str, Any]) -> list[str]:
    warnings = []
    if file_row.get("is_hdr"):
        warnings.append("HDR file: verify tone mapping and HDR preservation before replacing anything.")
    if (file_row.get("height") or 0) >= 2160:
        warnings.append("4K file: staged output should be visually reviewed.")
    if file_row.get("audio_stream_count", 0) > 1:
        warnings.append("Multiple audio tracks: confirm all intended tracks are present in output.")
    if file_row.get("subtitle_stream_count", 0) > 1:
        warnings.append("Multiple subtitle tracks: confirm all intended subtitles are present in output.")
    if file_row.get("has_image_subtitles"):
        warnings.append("Image subtitles may not be compatible with MP4 targets.")
    if file_row.get("is_interlaced"):
        warnings.append("Interlaced source: confirm deinterlacing expectations.")
    return warnings


def _above_threshold(height: int, bitrate_mbps: float) -> bool:
    if bitrate_mbps <= 0:
        return False
    if height >= 2160:
        return bitrate_mbps > 50
    if height >= 1080:
        return bitrate_mbps > 15
    if height >= 720:
        return bitrate_mbps > 8
    return bitrate_mbps > 4


def _result(
    category: str, summary: str, reasons: list[str], warnings: list[str] | None = None
) -> dict[str, Any]:
    return {
        "category": category,
        "summary": summary,
        "reasons": reasons,
        "warnings": warnings or [],
    }
