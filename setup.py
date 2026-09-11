from setuptools import setup, find_packages

setup(
    version="0.3.0rc2",
    name="auto_subtitle_plus",
    packages=find_packages(),
    py_modules=["auto_subtitle_plus"],
    author="sthokelek - based on the work of Miguel Piedrafita, RapDoodle and Sectumsempra82",
    install_requires=[
        'youtube-dl',
        'psutil',
        'openai-whisper',
        'ffmpeg-python',
        'deep-translator',
        'stable-ts',
        'torch>=2.6,<3',
        'ctranslate2==4.8.2',
        'transformers==4.57.6',
        'huggingface-hub==0.36.2',
        'sentencepiece==0.2.1',
        'sacremoses==0.1.1',
        'filelock>=3.18,<4',
    ],
    extras_require={
        'gui': [
            'PySide6-Essentials==6.11.2',
        ],
        'benchmark': [
            'jiwer',
            'srt',
            'sacrebleu==2.6.0',
        ],
        'faster': [
            'faster-whisper>=1.2.1,<2',
        ],
        'faster-cuda': [
            'faster-whisper>=1.2.1,<2',
            'nvidia-cublas-cu12>=12,<13; sys_platform == "win32"',
            'nvidia-cudnn-cu12>=9,<10; sys_platform == "win32"',
        ],
    },
    description="Automatically generate and/or embed, translate subtitles into your videos",
    entry_points={
        'console_scripts': [
            'auto_subtitle_plus=auto_subtitle_plus.cli:main',
            'auto_subtitle_benchmark=auto_subtitle_plus.benchmark:main',
            'auto_subtitle_translation_benchmark=auto_subtitle_plus.translation_benchmark:main',
        ],
        'gui_scripts': [
            'auto_subtitle_plus_gui=auto_subtitle_plus.desktop:main',
        ],
    },
    include_package_data=True,
    package_data={'auto_subtitle_plus': ['translation_catalog.json']},
)
