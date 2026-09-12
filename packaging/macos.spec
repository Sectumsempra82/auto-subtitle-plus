"""Apple Silicon desktop bundle. Build on macOS with tools/package_macos.py."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / "auto_subtitle_plus/translation_catalog.json"), "auto_subtitle_plus"),
         (str(root / ".build/macos-notices"), "licenses")]
for package in ("whisper", "stable_whisper", "faster_whisper", "tiktoken", "sacremoses"):
    datas += collect_data_files(package)
for distribution in ("auto-subtitle-plus", "openai-whisper", "stable-ts", "faster-whisper",
                     "ctranslate2", "transformers", "sentencepiece", "sacremoses", "tiktoken"):
    datas += copy_metadata(distribution)

hidden = ["gui_smoke", "auto_subtitle_plus.api", "auto_subtitle_plus.processing",
          "auto_subtitle_plus.transcription_worker", "auto_subtitle_plus.translation_worker",
          "tiktoken_ext.openai_public"]
for package in ("whisper", "stable_whisper", "faster_whisper", "ctranslate2"):
    hidden += collect_submodules(package)
model_packages = {"auto", "m2m_100", "nllb", "nllb_moe", "marian", "t5", "mt5"}
def translation_modules(name):
    return not name.startswith("transformers.models.") or name.split(".")[2] in model_packages
hidden += collect_submodules("transformers", filter=translation_modules)

a = Analysis([str(root / "packaging/macos_entry.py")],
             pathex=[str(root), str(root / "packaging")], datas=datas, binaries=[],
             hiddenimports=hidden, excludes=["tensorflow", "jax", "jaxlib", "triton", "IPython",
             "notebook", "pytest", "playwright", "matplotlib", "pandas", "scipy", "sklearn",
             "tensorboard", "_tkinter", "PyQt5", "PyQt6"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AutoSubtitlePlus",
          console=False, debug=False, strip=False, upx=False, target_arch="arm64")
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AutoSubtitlePlus")
app = BUNDLE(collection, name="Auto Subtitle Plus.app", version="0.3.0",
             bundle_identifier="local.autosubtitleplus.desktop", info_plist={
                 "CFBundleDisplayName": "Auto Subtitle Plus", "CFBundleVersion": "3",
                 "LSMinimumSystemVersion": "26.0", "NSHighResolutionCapable": True,
                 "NSPrincipalClass": "NSApplication", "CFBundleDocumentTypes": [{
                     "CFBundleTypeName": "Audio and Video", "CFBundleTypeRole": "Viewer",
                     "LSHandlerRank": "Alternate", "LSItemContentTypes": ["public.audio", "public.movie"],
                 }],
             })
