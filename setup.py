from setuptools import setup, find_packages

setup(
    version="0.2",
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
        'stable-ts'
    ],
    description="Automatically generate and/or embed, translate subtitles into your videos",
    entry_points={
        'console_scripts': ['auto_subtitle_plus=auto_subtitle_plus.cli:main'],
    },
    include_package_data=True,
)
