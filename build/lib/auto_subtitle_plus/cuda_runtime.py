import os
import sys
from importlib.metadata import PackageNotFoundError, distribution


def configure_windows_cuda() -> None:
    if sys.platform != "win32":
        return

    packages = (
        ("nvidia-cublas-cu12", "nvidia/cublas/bin"),
        ("nvidia-cudnn-cu12", "nvidia/cudnn/bin"),
        ("nvidia-cuda-nvrtc-cu12", "nvidia/cuda_nvrtc/bin"),
    )
    current_path = os.environ.get("PATH", "")
    known_paths = {os.path.normcase(path) for path in current_path.split(os.pathsep)}
    additions = []
    for package, relative_path in packages:
        try:
            directory = distribution(package).locate_file(relative_path)
        except PackageNotFoundError:
            continue
        path = str(directory)
        if directory.is_dir() and os.path.normcase(path) not in known_paths:
            additions.append(path)
            known_paths.add(os.path.normcase(path))

    # CTranslate2 loads CUDA with LoadLibrary, which does not use add_dll_directory.
    if additions:
        os.environ["PATH"] = os.pathsep.join(additions + [current_path])
