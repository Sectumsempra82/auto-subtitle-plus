import os
import re
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from deep_translator import GoogleTranslator
from typing import Any, Iterator, TextIO

def str2bool(string):
    string = string.lower()
    str2val = {"true": True, "false": False}
    if string in str2val:
        return str2val[string]
    else:
        raise ValueError(f"Expected one of {set(str2val.keys())}, got {string}")

def format_timestamp(
    seconds: float,
    always_include_hours: bool = False,
    decimal_marker: str = ".",
):
    assert seconds >= 0, "non-negative timestamp expected"
    milliseconds = round(seconds * 1000.0)
    hours = milliseconds // 3_600_000
    milliseconds -= hours * 3_600_000
    minutes = milliseconds // 60_000
    milliseconds -= minutes * 60_000
    seconds = milliseconds // 1_000
    milliseconds -= seconds * 1_000
    hours_marker = f"{hours}:" if always_include_hours or hours > 0 else ""
    return f"{hours_marker}{minutes:02d}:{seconds:02d}{decimal_marker}{milliseconds:03d}"

def get_segment_value(segment: Any, key: str):
    if isinstance(segment, dict):
        return segment[key]
    return getattr(segment, key)

def normalize_segments(transcript: Iterator[dict]):
    if isinstance(transcript, dict):
        return list(transcript["segments"])
    if hasattr(transcript, "segments"):
        return list(transcript.segments)
    return list(transcript)

def write_subtitle(
    transcript: Iterator[dict],
    file: TextIO,
    subtitle_format: str = "srt",
    batch_size: int = 10,
    max_workers: int = 4,
    translate_to: str = "tr",
    translate_off: bool = False,
    bilingual: bool = False
):
    start_time = time.time()

    segments = normalize_segments(transcript)
    texts = [
        str(get_segment_value(segment, "text")).strip().replace("-->", "->")
        for segment in segments
    ]
    decimal_marker = "," if subtitle_format == "srt" else "."

    if subtitle_format == "vtt":
        print("WEBVTT\n", file=file)

    if translate_off:
        for i, segment in enumerate(segments, start=1):
            text = str(get_segment_value(segment, "text")).strip().replace("-->", "->")
            print(
                f"{i}\n"
                f"{format_timestamp(get_segment_value(segment, 'start'), always_include_hours=True, decimal_marker=decimal_marker)} --> "
                f"{format_timestamp(get_segment_value(segment, 'end'), always_include_hours=True, decimal_marker=decimal_marker)}\n"
                f"{text}\n",
                file=file,
                flush=True,
            )
    else:
        translations = [None] * len(texts)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(
                    GoogleTranslator(source="auto", target=translate_to).translate_batch,
                    texts[i:i + batch_size]
                ): (i, i + batch_size)
                for i in range(0, len(texts), batch_size)
            }

            for future in as_completed(future_to_index):
                start_idx, end_idx = future_to_index[future]
                try:
                    translations[start_idx:end_idx] = future.result()
                except Exception as e:
                    print(f"Translation error: {e}", file=file)

        for i, (segment, translation) in enumerate(zip(segments, translations), start=1):
            original_text = str(get_segment_value(segment, "text")).strip().replace("-->", "->")
            translated_text = translation or original_text
            subtitle_text = (
                f"{original_text}\n{translated_text}"
                if bilingual
                else translated_text
            )
            print(
                f"{i}\n"
                f"{format_timestamp(get_segment_value(segment, 'start'), always_include_hours=True, decimal_marker=decimal_marker)} --> "
                f"{format_timestamp(get_segment_value(segment, 'end'), always_include_hours=True, decimal_marker=decimal_marker)}\n"
                f"{subtitle_text}\n",
                file=file,
                flush=True,
            )

    elapsed_time = time.time() - start_time
    print(f"Process completed in {int(elapsed_time // 60)}m{int(elapsed_time % 60)}s.")

def write_srt(transcript: Iterator[dict], file: TextIO, **kwargs):
    write_subtitle(transcript, file, subtitle_format="srt", **kwargs)

def write_txt(transcript: Iterator[dict], file: TextIO):
    for segment in normalize_segments(transcript):
        print(str(get_segment_value(segment, "text")).strip(), file=file)

def get_filename(path):
    return os.path.splitext(os.path.basename(path))[0]

def is_audio(path):
    return path.lower().endswith(('.mp3', '.ogg', '.wav', '.flac', '.m4a', '.wma', '.aac'))

def run_ffmpeg_with_progress(cmd_args, description):
    print(description)
    process = subprocess.Popen(
        cmd_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1,
    )

    duration = None
    stderr_output = []

    if process.stderr:
        for line in process.stderr:
            line = line.strip()
            stderr_output.append(line)

            if "Duration:" in line:
                duration_match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", line)
                if duration_match:
                    hours, minutes, seconds = duration_match.groups()
                    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

            elif "time=" in line and duration:
                time_match = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
                if time_match:
                    hours, minutes, seconds = time_match.groups()
                    current_time = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
                    progress = min(current_time / duration * 100, 100)
                    print(f"\rProgress: {progress:.1f}%", end="", flush=True)

    process.wait()
    if process.returncode != 0:
        print(f"\nFFmpeg error (exit code {process.returncode}):")
        for line in stderr_output[-10:]:
            print(line)
        raise subprocess.CalledProcessError(
            process.returncode,
            cmd_args,
            "\n".join(stderr_output),
        )

    print("\rProgress: 100.0% - Complete!                ")

def ffmpeg_extract_audio(input_path, output_path):
    cmd_args = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-ac",
        "1",
        "-async",
        "1",
        output_path,
    ]
    run_ffmpeg_with_progress(cmd_args, f"Extracting audio from {input_path}...")
