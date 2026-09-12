"""Видео Переводчик PRO — compatibility launcher.

Рабочая реализация разнесена по пакету ``videotranslator``.
Старый импорт ``import video_translator`` сохранён.
"""
from videotranslator.public_api import *  # noqa: F401,F403
from videotranslator.bootstrap import main

if __name__ == "__main__":
    main()
