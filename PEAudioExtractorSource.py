from __future__ import annotations
import math, os, struct, threading, wave, webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_NAME = "EIDOS Audio Extractor"
STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5360, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767
]
INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8]


def decode_ima_style(payload: bytes):
    if len(payload) < 4:
        return []

    predictor = struct.unpack_from("<h", payload, 0)[0]
    index = payload[2] & 0x7F
    if index > 88:
        index = 88

    samples = [predictor]
    pos = 4
    length = len(payload)

    while pos < length:
        byte = payload[pos]
        pos += 1

        for nibble in (byte & 0x0F, (byte >> 4) & 0x0F):
            step = STEP_TABLE[index]

            diff = step >> 3
            if nibble & 1:
                diff += step >> 2
            if nibble & 2:
                diff += step >> 1
            if nibble & 4:
                diff += step

            if nibble & 8:
                predictor -= diff
            else:
                predictor += diff

            if predictor > 32767:
                predictor = 32767
            elif predictor < -32768:
                predictor = -32768

            index += INDEX_TABLE[nibble & 7]
            if index < 0:
                index = 0
            elif index > 88:
                index = 88

            samples.append(predictor)

    return samples


def find_embedded_waves(data: bytes):
    found, pos = [], 0
    while True:
        pos = data.find(b"RIFF", pos)
        if pos < 0:
            break
        if pos + 44 <= len(data) and data[pos + 8:pos + 12] == b"WAVE":
            riff_size = struct.unpack_from("<I", data, pos + 4)[0]
            end = min(len(data), pos + 8 + riff_size)
            fmt_pos = data.find(b"fmt ", pos + 12, min(end, pos + 128))
            data_pos = data.find(b"data", pos + 12, min(end, pos + 256))
            if fmt_pos >= 0 and data_pos >= 0 and fmt_pos + 24 <= end and data_pos + 8 <= end:
                fmt_size = struct.unpack_from("<I", data, fmt_pos + 4)[0]
                if fmt_size >= 16:
                    tag, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", data, fmt_pos + 8)
                    size = struct.unpack_from("<I", data, data_pos + 4)[0]
                    start = data_pos + 8
                    stop = min(end, start + size)
                    if channels == 1 and tag in (2, 0x11) and stop > start:
                        found.append({"rate": rate or 22050, "tag": tag, "payload": data[start:stop]})
        pos += 4
    return found


def peak_dbfs(samples):
    peak = max((abs(int(x)) for x in samples), default=0)
    return 20 * math.log10(peak / 32768) if peak else float("-inf")


def process_samples(samples, gain_db=0.0, normalize=False):
    if not samples:
        return samples, 0.0, 0

    peak = max(abs(int(x)) for x in samples)
    gain = 10 ** (gain_db / 20.0)

    if normalize and peak:
        target = 32768 * (10 ** (-1.0 / 20.0))
        gain = target / peak

    clipped = 0
    out = []
    for x in samples:
        v = int(round(x * gain))
        if v > 32767:
            v = 32767
            clipped += 1
        elif v < -32768:
            v = -32768
            clipped += 1
        out.append(v)

    applied = 20 * math.log10(gain) if gain > 0 else float("-inf")
    return out, applied, clipped


def write_wav(path, samples, rate):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<" + "h" * len(samples), *samples))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME + " v1.0")
        self.geometry("780x640")
        self.minsize(700, 560)
        self.configure(bg="#BEBEBE")

        self.eds = tk.StringVar()
        self.output = tk.StringVar(value=str(Path.home() / "Desktop" / "EIDOSExtracted"))
        self.gain = tk.DoubleVar(value=-6.0)
        self.normalize = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Select an EDS file to begin.")
        self.files = []
        self._last_extracted_folder = None

        self._ui()

    def _ui(self):
        s = ttk.Style(self)
        try:
            s.theme_use("winnative")
        except tk.TclError:
            try:
                s.theme_use("xpnative")
            except tk.TclError:
                s.theme_use("clam")

        bg = "#ADADAD"
        box = "#E0E0E0"   # lighter grey for text boxes / list

        s.configure("TFrame", background=bg)
        s.configure("TLabel", background=bg, foreground="#000000", font=("Tahoma", 8))
        s.configure("Title.TLabel", font=("Tahoma", 12, "bold"), foreground="#000000", background=bg)
        s.configure("Muted.TLabel", foreground="#555555", background=bg, font=("Tahoma", 8))
        s.configure("TButton", font=("Tahoma", 8), padding=3)
        s.configure("TLabelframe", background=bg, font=("Tahoma", 8))
        s.configure("TLabelframe.Label", background=bg, font=("Tahoma", 8, "bold"))
        s.configure("TCheckbutton", background=bg, font=("Tahoma", 8))
        s.configure("Horizontal.TScale", background=bg)
        s.configure("TProgressbar", background="#000080")
        s.configure("TEntry", fieldbackground=box, foreground="#000000")

        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="EIDOS Audio Extractor", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="v1.0", style="Muted.TLabel").pack(anchor="w", pady=(0, 10))

        ttk.Label(root, text="EDS file:").pack(anchor="w")
        r = ttk.Frame(root)
        r.pack(fill="x", pady=(2, 8))
        ttk.Entry(r, textvariable=self.eds, font=("Tahoma", 8)).pack(side="left", fill="x", expand=True, ipady=2)
        ttk.Button(r, text="Browse...", command=self.pick_eds).pack(side="left", padx=(6, 0))

        ttk.Label(root, text="Save decoded WAV files to:").pack(anchor="w")
        r = ttk.Frame(root)
        r.pack(fill="x", pady=(2, 8))
        ttk.Entry(r, textvariable=self.output, font=("Tahoma", 8)).pack(side="left", fill="x", expand=True, ipady=2)
        ttk.Button(r, text="Choose folder...", command=self.pick_output).pack(side="left", padx=(6, 0))

        settings = ttk.LabelFrame(root, text="Audio output", padding=8)
        settings.pack(fill="x", pady=(0, 8))

        r = ttk.Frame(settings)
        r.pack(fill="x")
        ttk.Label(r, text="Master gain:").pack(side="left")
        self.gain_label = ttk.Label(r, text="-6.0 dB")
        self.gain_label.pack(side="right")

        scale = ttk.Scale(settings, from_=-24, to=6, variable=self.gain, command=self.update_gain)
        scale.pack(fill="x", pady=(4, 6))

        ttk.Checkbutton(
            settings,
            text="Peak-normalize each clip to -1 dBFS (only changes level; does not repair decoding distortion)",
            variable=self.normalize
        ).pack(anchor="w")

        ttk.Label(
            settings,
            text="Default is -6 dB headroom to reduce harsh playback level. You can change it any time.",
            style="Muted.TLabel"
        ).pack(anchor="w", pady=(4, 0))

        self.decode = ttk.Button(root, text="Decode Audio", command=self.start)
        self.decode.pack(fill="x", pady=(4, 0), ipady=4)

        self.progress = ttk.Progressbar(root, maximum=100)
        self.progress.pack(fill="x", pady=(8, 4))
        ttk.Label(root, textvariable=self.status).pack(anchor="w")

        lower = ttk.Frame(root)
        lower.pack(fill="both", expand=True, pady=(10, 0))

        left = ttk.Frame(lower)
        left.pack(side="left", fill="both", expand=True)

        ttk.Label(left, text="Extracted clips:").pack(anchor="w")
        self.listbox = tk.Listbox(
            left,
            bg=box,
            fg="black",
            selectbackground="#316AC5",
            selectforeground="white",
            relief="sunken",
            borderwidth=2,
            font=("Tahoma", 8),
            height=11
        )
        self.listbox.pack(fill="both", expand=True, pady=(3, 0))

        controls = ttk.Frame(left)
        controls.pack(fill="x", pady=5)
        ttk.Button(controls, text="Preview selected", command=self.preview).pack(side="left")
        ttk.Button(controls, text="Stop", command=self.stop_preview).pack(side="left", padx=6)

        right = ttk.Frame(lower)
        right.pack(side="left", fill="y", padx=(10, 0))

        ttk.Label(right, text="Diagnostics:").pack(anchor="w")
        self.report = tk.Text(
            right,
            width=32,
            height=13,
            bg=box,
            fg="black",
            relief="sunken",
            borderwidth=2,
            font=("Courier New", 8),
            padx=6,
            pady=6
        )
        self.report.pack(fill="both", expand=True, pady=(3, 0))

        ttk.Button(root, text="Open output folder", command=self.open_folder).pack(anchor="w", pady=(8, 0))

    def update_gain(self, _=None):
        self.gain_label.configure(text=f"{self.gain.get():.1f} dB")

    def log(self, text):
        self.report.insert("end", text + "\n")
        self.report.see("end")

    def pick_eds(self):
        p = filedialog.askopenfilename(filetypes=[("EDS files", "*.eds *.EDS"), ("All files", "*.*")])
        if p:
            self.eds.set(p)

    def pick_output(self):
        p = filedialog.askdirectory()
        if p:
            self.output.set(p)
            self._last_extracted_folder = None

    def open_folder(self):
        p = Path(self.output.get()).expanduser()
        if p.exists():
            os.startfile(str(p)) if os.name == "nt" else webbrowser.open(p.as_uri())
        else:
            messagebox.showinfo(APP_NAME, "Output folder has not been created yet.")

    def _folder_has_extracted_files(self, folder: Path) -> bool:
        if not folder.is_dir():
            return False
        for f in folder.glob("eden_*.wav"):
            return True
        return False

    def start(self):
        src = Path(self.eds.get()).expanduser()
        dest = Path(self.output.get()).expanduser()

        if not src.is_file():
            messagebox.showerror(APP_NAME, "Select a valid EDS file.")
            return

        already_used = (
            self._last_extracted_folder is not None
            and Path(self._last_extracted_folder).resolve() == dest.resolve()
        ) or self._folder_has_extracted_files(dest)

        if already_used:
            messagebox.showwarning(
                APP_NAME,
                "This output folder already contains extracted WAV files.\n\n"
                "Please choose or create a different folder so the previous\n"
                "files are not overwritten."
            )
            new_folder = filedialog.askdirectory(
                title="Choose a different output folder",
                initialdir=str(dest.parent if dest.exists() else Path.home() / "Desktop")
            )
            if not new_folder:
                self.status.set("Decode cancelled – choose a new folder first.")
                return
            self.output.set(new_folder)
            dest = Path(new_folder)
            self._last_extracted_folder = None

        self.decode.configure(state="disabled")
        self.progress["value"] = 0
        self.listbox.delete(0, "end")
        self.files = []
        self.report.delete("1.0", "end")
        threading.Thread(
            target=self.worker,
            args=(src, dest, self.gain.get(), self.normalize.get()),
            daemon=True
        ).start()

    def worker(self, src, dest, gain_db, normalize):
        try:
            raw = src.read_bytes()
            entries = find_embedded_waves(raw)
            if not entries:
                raise RuntimeError("No supported embedded audio entries found.")

            dest.mkdir(parents=True, exist_ok=True)
            made = []
            clipped_samples = 0
            clips_with_clip = 0
            max_before = -999.0

            total = len(entries)
            for i, e in enumerate(entries, 1):
                samples = decode_ima_style(e["payload"])
                if not samples:
                    continue

                before = peak_dbfs(samples)
                if before > max_before:
                    max_before = before

                processed, applied_gain, clip_count = process_samples(samples, gain_db, normalize)
                clipped_samples += clip_count
                if clip_count:
                    clips_with_clip += 1

                out = dest / f"eden_{i:03d}.wav"
                write_wav(out, processed, e["rate"])
                made.append(out)

                if i == 1 or i % 10 == 0 or i == total:
                    self.after(0, lambda n=i, t=total: (
                        self.progress.configure(value=n * 100 / t),
                        self.status.set(f"Decoding {n} of {t}...")
                    ))

            self.after(0, lambda: self.done(
                made, entries, clipped_samples, clips_with_clip,
                max_before, gain_db, normalize, dest
            ))
        except Exception as ex:
            self.after(0, lambda e=str(ex): self.failed(e))

    def done(self, made, entries, clipped, clips_clips, max_peak, gain_db, normalize, dest):
        self.decode.configure(state="normal")
        self.progress["value"] = 100
        self.files = made
        self._last_extracted_folder = str(dest)

        for p in made:
            self.listbox.insert("end", p.name)

        self.status.set(f"Finished: {len(made)} WAV clips saved to {dest}")
        self.log(f"Source entries found: {len(entries)}")
        self.log(f"WAV files exported: {len(made)}")
        self.log(f"Master gain: {gain_db:+.1f} dB")
        self.log(f"Peak normalization: {'ON' if normalize else 'OFF'}")
        self.log(f"Clips with near-full-scale source samples: {clips_clips}")
        self.log(f"Near-full-scale samples detected: {clipped:,}")
        self.log(f"Highest decoded peak: {max_peak:.1f} dBFS")
        self.log("")
        self.log("NOTE: Gain reduces playback level but cannot")
        self.log("restore waveform detail lost to decoder clipping.")
        self.log("This build uses an experimental decoder.")
        messagebox.showinfo(APP_NAME, f"Exported {len(made)} WAV files.\n\nOutput:\n{dest}")

    def failed(self, msg):
        self.decode.configure(state="normal")
        self.status.set("Decode failed.")
        messagebox.showerror(APP_NAME, msg)

    def preview(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Select a clip first.")
            return
        p = self.files[sel[0]]
        try:
            import winsound
            winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            try:
                os.startfile(str(p))
            except Exception as e:
                messagebox.showerror(APP_NAME, f"Could not preview this file: {e}")

    def stop_preview(self):
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass


if __name__ == "__main__":
    App().mainloop()