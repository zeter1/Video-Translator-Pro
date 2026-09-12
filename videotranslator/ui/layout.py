"""Method owner for App: _build_ui."""

from __future__ import annotations

from tkinter import scrolledtext
import tkinter as tk
from tkinter import ttk
from videotranslator.config import DEFAULT_TARGET_LANGUAGE, MODELS_MAP, SPEECH_SPEED_LIMITS, TOTAL_MAX_SPEECH_SPEED, get_language_labels, get_voice_options


class UILayoutMixin:
    """Behavior-preserving methods extracted from the legacy monolith."""

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Start.TButton", font=("Arial", 11, "bold"), foreground="white", background="#1b5e20")
        style.map("Start.TButton", background=[("disabled", "#888"), ("active", "#2e7d32")])
        style.configure("Cancel.TButton", foreground="white", background="#b71c1c")
        style.map("Cancel.TButton", background=[("disabled", "#888"), ("active", "#c62828")])

        pad = dict(padx=10, pady=4)

        files_frame = ttk.LabelFrame(self.root, text="1. Видеофайлы", padding=8)
        files_frame.pack(fill="both", expand=False, **pad)

        buttons_frame = ttk.Frame(files_frame)
        buttons_frame.pack(fill="x", pady=(0, 4))
        self.btn_add = ttk.Button(buttons_frame, text="➕ Добавить", command=self.add_files)
        self.btn_add.pack(side="left", padx=2)
        self.btn_remove = ttk.Button(buttons_frame, text="➖ Удалить", command=self.remove_selected)
        self.btn_remove.pack(side="left", padx=2)
        self.btn_clear = ttk.Button(buttons_frame, text="🗑️ Очистить", command=self.clear_all)
        self.btn_clear.pack(side="left", padx=2)
        self.lbl_count = ttk.Label(buttons_frame, text="Файлов: 0", foreground="#666")
        self.lbl_count.pack(side="right")

        list_frame = ttk.Frame(files_frame)
        list_frame.pack(fill="both")
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(
            list_frame,
            height=5,
            yscrollcommand=scrollbar.set,
            selectmode=tk.EXTENDED,
            font=("Consolas", 9),
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)

        recog_frame = ttk.LabelFrame(self.root, text="2. Язык, распознавание и голос", padding=8)
        recog_frame.pack(fill="x", **pad)
        recog_frame.columnconfigure(1, weight=1)

        ttk.Label(recog_frame, text="Перевести на:").grid(row=0, column=0, sticky="w", pady=2)
        self.combo_language = ttk.Combobox(recog_frame, values=get_language_labels(), state="readonly")
        self.combo_language.set(DEFAULT_TARGET_LANGUAGE)
        self.combo_language.grid(row=0, column=1, sticky="ew", padx=8, pady=2)
        self.combo_language.bind("<<ComboboxSelected>>", self._on_language_changed)

        ttk.Label(recog_frame, text="Диктор:").grid(row=1, column=0, sticky="w", pady=2)
        self.combo_voice = ttk.Combobox(recog_frame, values=list(get_voice_options(DEFAULT_TARGET_LANGUAGE).keys()), state="readonly")
        self.combo_voice.current(0)
        self.combo_voice.grid(row=1, column=1, sticky="ew", padx=8, pady=2)
        self.combo_voice.bind("<<ComboboxSelected>>", lambda _event: self.save_settings())

        ttk.Label(recog_frame, text="Точность:").grid(row=2, column=0, sticky="w", pady=2)
        self.combo_model = ttk.Combobox(recog_frame, values=list(MODELS_MAP.keys()), state="readonly")
        self.combo_model.current(1)
        self.combo_model.grid(row=2, column=1, sticky="ew", padx=8, pady=2)
        self.combo_model.bind("<<ComboboxSelected>>", lambda _event: self.save_settings())

        self.var_review = tk.BooleanVar(value=True)
        self.chk_review = ttk.Checkbutton(
            recog_frame,
            text="Перед озвучкой подкорректировать перевод",
            variable=self.var_review,
            command=self.save_settings,
        )
        self.chk_review.grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))

        audio_frame = ttk.LabelFrame(self.root, text="3. Настройки аудио", padding=8)
        audio_frame.pack(fill="x", **pad)
        self.var_keep = tk.BooleanVar(value=False)
        self.chk_keep = ttk.Checkbutton(
            audio_frame,
            text="Оставить оригинальный звук фоном",
            variable=self.var_keep,
            command=lambda: self._apply_keep_state(save=True),
        )
        self.chk_keep.pack(anchor="w")

        volume_frame = ttk.Frame(audio_frame)
        volume_frame.pack(fill="x", pady=(4, 0))
        self.lbl_vol = ttk.Label(volume_frame, text="Громкость оригинала: 15%", width=26)
        self.lbl_vol.pack(side="left")
        self.var_volume = tk.IntVar(value=15)
        self.scale_vol = ttk.Scale(
            volume_frame,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.var_volume,
            command=self._on_vol_changed,
        )
        self.scale_vol.pack(side="left", fill="x", expand=True, padx=6)

        voice_volume_frame = ttk.Frame(audio_frame)
        voice_volume_frame.pack(fill="x", pady=(6, 0))
        self.lbl_voice_vol = ttk.Label(voice_volume_frame, text="Громкость новой озвучки: 100%", width=30)
        self.lbl_voice_vol.pack(side="left")
        self.var_voice_volume = tk.IntVar(value=100)
        self.scale_voice_volume = ttk.Scale(
            voice_volume_frame,
            from_=50,
            to=150,
            orient="horizontal",
            variable=self.var_voice_volume,
            command=self._on_voice_volume_changed,
        )
        self.scale_voice_volume.pack(side="left", fill="x", expand=True, padx=6)

        highpass_frame = ttk.Frame(audio_frame)
        highpass_frame.pack(fill="x", pady=(6, 0))
        self.lbl_highpass = ttk.Label(highpass_frame, text="Убрать гул ниже: 55 Гц", width=30)
        self.lbl_highpass.pack(side="left")
        self.var_highpass = tk.IntVar(value=55)
        self.scale_highpass = ttk.Scale(
            highpass_frame,
            from_=0,
            to=220,
            orient="horizontal",
            variable=self.var_highpass,
            command=self._on_highpass_changed,
        )
        self.scale_highpass.pack(side="left", fill="x", expand=True, padx=6)

        lowpass_frame = ttk.Frame(audio_frame)
        lowpass_frame.pack(fill="x", pady=(6, 0))
        self.lbl_lowpass = ttk.Label(lowpass_frame, text="Смягчить верх: выкл", width=30)
        self.lbl_lowpass.pack(side="left")
        self.var_lowpass = tk.IntVar(value=0)
        self.scale_lowpass = ttk.Scale(
            lowpass_frame,
            from_=0,
            to=16000,
            orient="horizontal",
            variable=self.var_lowpass,
            command=self._on_lowpass_changed,
        )
        self.scale_lowpass.pack(side="left", fill="x", expand=True, padx=6)

        loudness_frame = ttk.Frame(audio_frame)
        loudness_frame.pack(fill="x", pady=(6, 0))
        self.lbl_loudness = ttk.Label(loudness_frame, text="Итоговая громкость: -16 LUFS", width=30)
        self.lbl_loudness.pack(side="left")
        self.var_loudness = tk.IntVar(value=-16)
        self.scale_loudness = ttk.Scale(
            loudness_frame,
            from_=-24,
            to=-12,
            orient="horizontal",
            variable=self.var_loudness,
            command=self._on_loudness_changed,
        )
        self.scale_loudness.pack(side="left", fill="x", expand=True, padx=6)

        self.var_denoise = tk.BooleanVar(value=False)
        self.chk_denoise = ttk.Checkbutton(
            audio_frame,
            text="Лёгкое шумоподавление перед озвучкой",
            variable=self.var_denoise,
            command=self.save_settings,
        )
        self.chk_denoise.pack(anchor="w", pady=(6, 0))

        speed_frame = ttk.Frame(audio_frame)
        speed_frame.pack(fill="x", pady=(6, 0))
        ttk.Label(speed_frame, text="Максимальное ускорение речи:", width=30).pack(side="left")
        self.combo_speed = ttk.Combobox(
            speed_frame,
            values=[f"{value:.2f}" for value in SPEECH_SPEED_LIMITS],
            state="readonly",
            width=8,
        )
        self.combo_speed.set(f"{TOTAL_MAX_SPEECH_SPEED:.2f}")
        self.combo_speed.pack(side="left", padx=6)
        self.combo_speed.bind("<<ComboboxSelected>>", lambda _event: self.save_settings())
        ttk.Label(speed_frame, text="x (выше — меньше стоп-кадров)", foreground="#666").pack(side="left")

        ttk.Label(
            self.root,
            text="ℹ️ Фразы переводятся, озвучиваются и вставляются по таймкодам оригинала",
            foreground="#1565c0",
            font=("Arial", 8),
        ).pack(padx=10, anchor="w")

        progress_frame = ttk.LabelFrame(self.root, text="Прогресс", padding=6)
        progress_frame.pack(fill="x", **pad)
        self.var_prog = tk.IntVar(value=0)
        ttk.Progressbar(progress_frame, variable=self.var_prog, maximum=100).pack(fill="x", pady=(0, 3))
        self.lbl_status = ttk.Label(progress_frame, text="Ожидание...", foreground="#555", font=("Arial", 9))
        self.lbl_status.pack(anchor="w")

        log_frame = ttk.LabelFrame(self.root, text="Журнал", padding=6)
        log_frame.pack(fill="both", expand=True, **pad)
        self.txt_log = scrolledtext.ScrolledText(
            log_frame,
            height=11,
            state="disabled",
            font=("Consolas", 8),
            wrap="word",
        )
        self.txt_log.pack(fill="both", expand=True)
        self.btn_clear_log = ttk.Button(log_frame, text="Очистить журнал", command=self._clear_log)
        self.btn_clear_log.pack(anchor="e", pady=(2, 0))

        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(fill="x", padx=10, pady=8)
        self.btn_start = ttk.Button(
            bottom_frame,
            text="▶  НАЧАТЬ ОБРАБОТКУ",
            style="Start.TButton",
            command=self.start,
        )
        self.btn_start.pack(side="left", fill="x", expand=True, ipady=10, padx=(0, 5))
        self.btn_cancel = ttk.Button(
            bottom_frame,
            text="⏹ Отмена",
            style="Cancel.TButton",
            command=self.cancel,
            state="disabled",
        )
        self.btn_cancel.pack(side="left", ipady=10, ipadx=14)
