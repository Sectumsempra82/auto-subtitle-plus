import os
import glob
import psutil
import ffmpeg
import whisper
import stable_whisper
import argparse
import warnings
import tempfile
import multiprocessing
from torch.cuda import is_available
from .utils import (
    ffmpeg_extract_audio,
    get_filename,
    is_audio,
    run_ffmpeg_with_progress,
    write_subtitle,
    write_txt,
)

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description="Auto Subtitle Plus - Automatically generate and translate subtitles for video/audio files",
        epilog='''Examples:
  Basic usage:
    auto_subtitle_plus video.mp4 --output-video

  Force English transcription:
    auto_subtitle_plus video.mp4 --language en

  Translate to French:
    auto_subtitle_plus video.mp4 --translate-to fr --output-srt

  Create bilingual subtitles:
    auto_subtitle_plus video.mp4 --translate-to fr --bilingual --output-srt

  Transcribe audio directly:
    auto_subtitle_plus audio.mp3 audio.ogg --output-srt

  Use large model with GPU:
    auto_subtitle_plus video.mp4 --model large --device cuda'''
    )

    # Core arguments
    parser.add_argument("paths", nargs="+", help="Input file paths or wildcards (e.g., *.mp4)")

    # Model and output options
    parser.add_argument("-m", "--model",
                       default="small",
                       choices=whisper.available_models(),
                       help="whisper model to use (default: %(default)s)")

    parser.add_argument("-o", "--output-dir",
                       default=os.getcwd(),
                       help="Output directory (default: current directory)")

    # Output controls
    output_group = parser.add_argument_group('Output Options')
    output_group.add_argument("-s", "--output-srt",
                             action="store_true",
                             help="Generate SRT subtitle file")
    output_group.add_argument("-a", "--output-audio",
                             action="store_true",
                             help="Save extracted audio file")
    output_group.add_argument("-v", "--output-video",
                             action="store_true",
                             help="Generate video with embedded subtitles")
    output_group.add_argument("--subtitle-format",
                             choices=("srt", "vtt"),
                             default="srt",
                             help="Subtitle file format (default: %(default)s)")
    output_group.add_argument("--output-txt",
                             action="store_true",
                             help="Also save a plain text transcript")
    output_group.add_argument("--output-mkv",
                             action="store_true",
                             help="When outputting video, mux subtitles as a soft track in an MKV container")

    # Language and translation
    lang_group = parser.add_argument_group('Language Options')
    lang_group.add_argument("--language",
                           type=str,
                           default=None,
                           help="Force audio language (e.g., en, fr, tr)")
    lang_group.add_argument("--translate-off",
                           action="store_true",
                           help="Deprecated; original-language subtitles are now the default")
    lang_group.add_argument("--translate-to",
                           default=None,
                           help="Target language for translation (e.g., tr, fr)")
    lang_group.add_argument("--bilingual",
                           action="store_true",
                           help="When translating, include original text above translated text")

    # Performance settings
    perf_group = parser.add_argument_group('Performance Options')
    perf_group.add_argument("--batch-size",
                           type=int,
                           default=10,
                           help="Segments per translation batch (default: %(default)s)")
    perf_group.add_argument("--max-workers",
                           type=int,
                           default=4,
                           help="Max parallel translation threads (default: %(default)s)")
    perf_group.add_argument("--extract-workers",
                           type=int,
                           default=max(1, psutil.cpu_count(logical=False)//2),
                           help="Audio extraction workers (default: half of CPU cores)")

    # Advanced options
    adv_group = parser.add_argument_group('Advanced Options')
    adv_group.add_argument("--device",
                          default="cuda" if is_available() else "cpu",
                          help="Processing device (default: %(default)s)")
    adv_group.add_argument("--verbose",
                          action="store_true",
                          help="Show detailed processing logs")
    adv_group.add_argument("--enhance-consistency",
                          action="store_true",
                          help="Improve transcription consistency")
    adv_group.add_argument("--word-timestamps",
                          action="store_true",
                          help="Ask stable-whisper to include word-level timestamps")

    args = parser.parse_args()

    # Validate and resolve paths
    input_paths = []
    for pattern in args.paths:
        input_paths.extend(glob.glob(pattern))

    if not input_paths:
        print("Error: No valid input files found!")
        return

    if not args.output_video and not args.output_srt and not args.output_txt:
        args.output_srt = True

    # Handle .en models
    if args.model.endswith(".en"):
        args.language = "en"
        warnings.warn("Forcing English transcription")

    # Initialize model
    try:
        model = stable_whisper.load_model(args.model, device=args.device)
    except Exception as e:
        print(f"Model loading failed: {str(e)}")
        return

    # Process files
    audio_paths = get_audio(
        input_paths,
        args.output_audio,
        args.output_dir,
        args.extract_workers
    )

    subtitles = generate_subtitles(
        audio_paths,
        args.output_srt,
        args.output_dir,
        model,
        args
    )

    if args.output_video:
        create_subtitled_videos(
            input_paths,
            subtitles,
            args.output_dir,
            args.output_mkv
        )

def get_audio(paths, save_audio, output_dir, num_workers):
    audio_map = {}
    tasks = []

    for path in paths:
        if is_audio(path):
            audio_map[path] = path
            continue

        target_dir = output_dir if save_audio else tempfile.gettempdir()
        output_path = os.path.join(target_dir, f"{get_filename(path)}.mp3")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        tasks.append((path, output_path))
        audio_map[path] = output_path

    if tasks:
        with multiprocessing.Pool(num_workers) as pool:
            pool.starmap(ffmpeg_extract_audio, tasks)

    return audio_map

def generate_subtitles(audio_paths, output_srt, output_dir, model, args):
    subtitles = {}

    for path, audio_path in audio_paths.items():
        print(f"\nProcessing: {os.path.basename(path)}")

        try:
            result = model.transcribe(
                audio_path,
                language=args.language,
                verbose=args.verbose,
                condition_on_previous_text=args.enhance_consistency,
                word_timestamps=args.word_timestamps
            )
        except Exception as e:
            print(f"Transcription failed: {str(e)}")
            continue

        subtitle_filename = f"{get_filename(path)}.{args.subtitle_format}"
        subtitle_dir = output_dir if output_srt else tempfile.gettempdir()
        subtitle_path = os.path.join(subtitle_dir, subtitle_filename)
        os.makedirs(os.path.dirname(subtitle_path), exist_ok=True)

        try:
            with open(subtitle_path, "w", encoding="utf-8") as f:
                write_subtitle(
                    result,
                    f,
                    subtitle_format=args.subtitle_format,
                    translate_off=args.translate_off or args.translate_to is None,
                    translate_to=args.translate_to,
                    bilingual=args.bilingual,
                    batch_size=args.batch_size,
                    max_workers=args.max_workers
                )
            subtitles[path] = subtitle_path
            print(f"Subtitles saved to: {os.path.abspath(subtitle_path)}")

            if args.output_txt:
                txt_path = os.path.join(output_dir, f"{get_filename(path)}.txt")
                os.makedirs(os.path.dirname(txt_path), exist_ok=True)
                with open(txt_path, "w", encoding="utf-8") as f:
                    write_txt(result, f)
                print(f"Transcript saved to: {os.path.abspath(txt_path)}")
        except Exception as e:
            print(f"File write error: {str(e)}")

    return subtitles

def create_subtitled_videos(input_paths, subtitles, output_dir, output_mkv):
    for path in input_paths:
        if is_audio(path) or path not in subtitles:
            continue

        subtitle_path = subtitles[path]
        output_ext = "mkv" if output_mkv else "mp4"
        output_filename = f"{get_filename(path)}_subtitled.{output_ext}"
        output_path = os.path.join(output_dir, output_filename)
        os.makedirs(output_dir, exist_ok=True)

        print(f"\nCreating subtitled video: {os.path.abspath(output_path)}")

        try:
            if output_mkv:
                command = ffmpeg.output(
                    ffmpeg.input(path),
                    ffmpeg.input(subtitle_path),
                    output_path,
                    vcodec="copy",
                    acodec="copy",
                    scodec="copy",
                ).compile(overwrite_output=True)
            else:
                video = ffmpeg.input(path)
                command = ffmpeg.output(
                    video.filter(
                        "subtitles",
                        filename=subtitle_path,
                        force_style="OutlineColour=&H40000000,BorderStyle=3",
                    ),
                    video.audio,
                    output_path,
                    vcodec="libx264",
                    acodec="copy",
                ).compile(overwrite_output=True)

            run_ffmpeg_with_progress(command, f"Adding subtitles to {path}...")
            print("Video creation completed successfully!")
        except Exception as e:
            print(f"Video processing failed: {str(e)}")

if __name__ == "__main__":
    main()
