"""Build both frontends from the same frozen processing library (Windows x64)."""
import os
import sys
from pathlib import Path
from importlib import metadata

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
assets = Path(os.environ["ASP_BUILD_ASSETS"])
def validate_binary_origins(analysis):
    allowed = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), assets.resolve(), Path(os.environ["SystemRoot"]).resolve()]
    for name, source, kind in analysis.binaries:
        path = Path(source).resolve()
        if not any(path.is_relative_to(directory) for directory in allowed):
            raise ValueError(f"Unexpected native build dependency {name}: {path}. Use an isolated build PATH.")
datas = [(str(root / "auto_subtitle_plus" / "translation_catalog.json"), "auto_subtitle_plus")]
datas += [(str(root / "packaging" / "gui_smoke.py"), "packaging")]
datas += [(str(assets / "bin"), "bin"), (str(assets / "runtimes"), "runtimes")]
datas += [(str(assets / "licenses"), "licenses")]
for package in ("whisper", "stable_whisper", "faster_whisper", "tiktoken", "sacremoses"):
    datas += collect_data_files(package)
for distribution in ("auto-subtitle-plus", "openai-whisper", "stable-ts", "faster-whisper",
                     "ctranslate2", "transformers", "nvidia-cublas-cu12", "nvidia-cudnn-cu12",
                     "nvidia-cuda-nvrtc-cu12", "sentencepiece", "sacremoses", "tiktoken"):
    datas += copy_metadata(distribution)
for distribution, relative in (("nvidia-cublas-cu12", "nvidia/cublas/bin"),
                               ("nvidia-cudnn-cu12", "nvidia/cudnn/bin"),
                               ("nvidia-cuda-nvrtc-cu12", "nvidia/cuda_nvrtc/bin")):
    directory = metadata.distribution(distribution).locate_file(relative)
    datas += [(str(directory), relative)]

hidden = ["gui_smoke", "auto_subtitle_plus.api", "auto_subtitle_plus.processing", "auto_subtitle_plus.transcription_worker",
          "auto_subtitle_plus.translation_worker", "tiktoken_ext.openai_public"]
for package in ("whisper", "stable_whisper", "faster_whisper", "ctranslate2"):
    hidden += collect_submodules(package)
model_packages = {"auto", "m2m_100", "nllb", "nllb_moe", "marian", "t5", "mt5"}
def translation_modules(name):
    if not name.startswith("transformers.models."):
        return True
    return name.split(".")[2] in model_packages
hidden += collect_submodules("transformers", filter=translation_modules)

excluded = ["tensorflow", "jax", "jaxlib", "triton", "IPython", "notebook", "pytest",
            "playwright", "matplotlib", "pandas", "scipy", "sklearn", "tensorboard", "_tkinter"]

cli = Analysis([str(root / "packaging" / "cli_entry.py")], pathex=[str(root)],
               binaries=[], datas=datas, hiddenimports=hidden,
               excludes=excluded + ["PySide6", "PyQt5", "PyQt6"], noarchive=False)
validate_binary_origins(cli)
cli_pyz = PYZ(cli.pure)
cli_exe = EXE(cli_pyz, cli.scripts, [], exclude_binaries=True, name="auto_subtitle_plus",
              console=True, debug=False, strip=False, upx=False)
COLLECT(cli_exe, cli.binaries, cli.datas, strip=False, upx=False, name="AutoSubtitlePlus-CLI")

gui = Analysis([str(root / "packaging" / "gui_entry.py")], pathex=[str(root), str(root / "packaging")],
               binaries=[], datas=datas, hiddenimports=hidden,
               excludes=excluded + ["PyQt5", "PyQt6"], noarchive=False)
validate_binary_origins(gui)
gui_pyz = PYZ(gui.pure)
gui_exe = EXE(gui_pyz, gui.scripts, [], exclude_binaries=True, name="auto_subtitle_plus_gui",
              console=False, debug=False, strip=False, upx=False)
COLLECT(gui_exe, gui.binaries, gui.datas, strip=False, upx=False, name="AutoSubtitlePlus-GUI")
