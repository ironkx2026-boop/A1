import concurrent.futures
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_DIR = Path(__file__).resolve().parent
LOCAL_MUTOOL = APP_DIR / "MuPDF" / "mutool.exe"
LOCAL_D2D_RENDERER = APP_DIR / "Direct2DRenderer" / "GpuPdfRenderer.exe"
LOCAL_TESSERACT = APP_DIR / "Tesseract-OCR" / "tesseract.exe"
LOCAL_TESSDATA = APP_DIR / "Tesseract-OCR" / "tessdata"
OCR_LANGUAGES = "eng+ell"


class PdfToPngApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF to PNG - GPU_001")
        self.geometry("780x520")
        self.minsize(720, 480)

        self.pdf_path = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.dpi = tk.IntVar(value=300)
        self.status = tk.StringVar(value="Select a PDF and an output folder.")
        self.gpu_status = tk.StringVar(value=self.detect_hardware_text())

        self.worker = None
        self.process = None
        self.child_processes = []
        self.process_lock = threading.Lock()
        self.cancel_requested = False
        self.events = queue.Queue()
        self.started_at = None

        self.create_widgets()
        self.after(100, self.poll_events)

    def create_widgets(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(9, weight=1)

        title = ttk.Label(root, text="Convert PDF to PNG and searchable OCR PDF", font=("Segoe UI", 16, "bold"))
        title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))

        ttk.Label(root, text="PDF file").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(root, textvariable=self.pdf_path).grid(row=1, column=1, sticky="ew", padx=8, pady=6)
        ttk.Button(root, text="Select PDF", command=self.select_pdf).grid(row=1, column=2, sticky="ew", pady=6)

        ttk.Label(root, text="Output folder").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(root, textvariable=self.output_dir).grid(row=2, column=1, sticky="ew", padx=8, pady=6)
        ttk.Button(root, text="Select Folder", command=self.select_output_dir).grid(row=2, column=2, sticky="ew", pady=6)

        ttk.Label(root, text="Resolution").grid(row=3, column=0, sticky="w", pady=6)
        dpi_frame = ttk.Frame(root)
        dpi_frame.grid(row=3, column=1, sticky="w", padx=8, pady=6)
        ttk.Spinbox(dpi_frame, from_=72, to=600, increment=25, textvariable=self.dpi, width=8).pack(side=tk.LEFT)
        ttk.Label(dpi_frame, text="DPI").pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(root, text="OCR").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Label(root, text=f"Tesseract languages: {OCR_LANGUAGES}").grid(row=4, column=1, columnspan=2, sticky="w", padx=8, pady=6)

        ttk.Label(root, text="Hardware").grid(row=5, column=0, sticky="w", pady=6)
        ttk.Label(root, textvariable=self.gpu_status).grid(row=5, column=1, columnspan=2, sticky="w", padx=8, pady=6)

        button_frame = ttk.Frame(root)
        button_frame.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 8))
        self.start_button = ttk.Button(button_frame, text="Start", command=self.start_conversion)
        self.start_button.pack(side=tk.LEFT)
        self.cancel_button = ttk.Button(button_frame, text="Cancel", command=self.cancel_conversion, state=tk.DISABLED)
        self.cancel_button.pack(side=tk.LEFT, padx=(8, 0))

        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        ttk.Label(root, textvariable=self.status).grid(row=8, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.log = tk.Text(root, height=12, wrap=tk.WORD, state=tk.DISABLED)
        self.log.grid(row=9, column=0, columnspan=3, sticky="nsew")

        scrollbar = ttk.Scrollbar(root, orient=tk.VERTICAL, command=self.log.yview)
        scrollbar.grid(row=9, column=3, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)

    def select_pdf(self):
        path = filedialog.askopenfilename(
            title="Select PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if path:
            self.pdf_path.set(path)
            if not self.output_dir.get():
                pdf = Path(path)
                self.output_dir.set(str(pdf.with_suffix("")))

    def select_output_dir(self):
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.output_dir.set(path)

    def detect_hardware_text(self):
        renderer = self.find_d2d_renderer()
        prefix = "Direct2D GPU renderer available. " if renderer else "Direct2D GPU renderer missing; MuPDF CPU fallback available. "
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if names:
                intel_names = [name for name in names if "intel" in name.lower()]
                if intel_names:
                    return prefix + "Intel GPU detected: " + ", ".join(intel_names)
                return prefix + "GPU detected: " + ", ".join(names)
        except Exception:
            pass
        return prefix + "GPU detection unavailable."

    def start_conversion(self):
        if self.worker and self.worker.is_alive():
            return

        pdf = Path(self.pdf_path.get().strip())
        output = Path(self.output_dir.get().strip())
        dpi = self.dpi.get()

        if not pdf.is_file():
            messagebox.showerror("Missing PDF", "Please select an existing PDF file.")
            return
        if pdf.suffix.lower() != ".pdf":
            messagebox.showerror("Invalid file", "Please select a PDF file.")
            return
        if dpi < 72 or dpi > 600:
            messagebox.showerror("Invalid DPI", "Choose a DPI between 72 and 600.")
            return

        renderer = self.find_d2d_renderer()
        mutool = self.find_mutool()
        tesseract = self.find_tesseract()
        if not renderer and not mutool:
            messagebox.showerror(
                "Renderer not found",
                "Could not find Direct2DRenderer\\GpuPdfRenderer.exe or MuPDF\\mutool.exe.",
            )
            return
        if not tesseract:
            messagebox.showerror(
                "Tesseract not found",
                "Could not find Tesseract-OCR\\tesseract.exe or tesseract.exe in PATH.",
            )
            return

        output.mkdir(parents=True, exist_ok=True)
        self.cancel_requested = False
        self.started_at = time.time()
        self.start_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL)
        self.progress.start(12)
        self.clear_log()

        self.worker = threading.Thread(
            target=self.convert_pdf,
            args=(renderer, mutool, tesseract, pdf, output, dpi),
            daemon=True,
        )
        self.worker.start()

    def cancel_conversion(self):
        self.cancel_requested = True
        self.status.set("Cancelling...")
        with self.process_lock:
            processes = [self.process] + list(self.child_processes)
        for process in processes:
            if process and process.poll() is None:
                try:
                    process.terminate()
                except Exception:
                    pass

    def find_mutool(self):
        if LOCAL_MUTOOL.is_file():
            return str(LOCAL_MUTOOL)
        return shutil.which("mutool")

    def find_d2d_renderer(self):
        if LOCAL_D2D_RENDERER.is_file():
            return str(LOCAL_D2D_RENDERER)
        return shutil.which("GpuPdfRenderer")

    def find_tesseract(self):
        if LOCAL_TESSERACT.is_file():
            return str(LOCAL_TESSERACT)
        return shutil.which("tesseract")

    def convert_pdf(self, renderer, mutool, tesseract, pdf, output, dpi):
        base_name = self.safe_stem(pdf.stem)
        if renderer:
            command = [
                renderer,
                "--input",
                str(pdf),
                "--output",
                str(output),
                "--dpi",
                str(dpi),
                "--prefix",
                base_name + "_page_",
            ]
            engine_text = "Windows Direct2D GPU renderer: " + renderer
            mode_text = "Rendering mode: Windows PDF API + Direct2D helper."
        else:
            pattern = output / f"{base_name}_page_%04d.png"
            command = [
                mutool,
                "draw",
                "-o",
                str(pattern),
                "-r",
                str(dpi),
                str(pdf),
            ]
            engine_text = "MuPDF CPU fallback: " + mutool
            mode_text = "Rendering mode: CPU via MuPDF; Direct2D helper was not found."

        self.events.put(("status", "Starting conversion..."))
        self.events.put(("log", "Input PDF: " + str(pdf)))
        self.events.put(("log", "Output folder: " + str(output)))
        self.events.put(("log", "DPI: " + str(dpi)))
        self.events.put(("log", "Engine: " + engine_text))
        self.events.put(("log", mode_text))
        self.events.put(("log", f"OCR: Tesseract {OCR_LANGUAGES}; final PDF will be searchable with Ctrl+F."))
        self.events.put(("log", self.gpu_status.get()))

        try:
            self.events.put(("status", "Rendering pages to PNG..."))
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            gpu_stop = threading.Event()
            gpu_stats = {"samples": 0, "nonzero": 0, "max": 0.0, "engine": ""}
            gpu_monitor = None
            if renderer:
                self.events.put(("log", f"GPU monitor: watching renderer PID {self.process.pid}."))
                gpu_monitor = threading.Thread(
                    target=self.monitor_renderer_gpu,
                    args=(self.process.pid, gpu_stop, gpu_stats),
                    daemon=True,
                )
                gpu_monitor.start()

            assert self.process.stdout is not None
            for line in self.process.stdout:
                clean = line.strip()
                if clean:
                    self.events.put(("log", clean))
                if self.cancel_requested:
                    break

            return_code = self.process.wait()
            if renderer:
                gpu_stop.set()
                if gpu_monitor:
                    gpu_monitor.join(timeout=2)
                self.events.put(
                    (
                        "log",
                        "GPU monitor: "
                        + f"{gpu_stats['nonzero']} active sample(s), "
                        + f"max {gpu_stats['max']:.2f}%"
                        + (f" on {gpu_stats['engine']}" if gpu_stats["engine"] else "")
                        + ".",
                    )
                )
            if self.cancel_requested:
                self.events.put(("done", False, "Cancelled."))
                return
            if return_code != 0:
                self.events.put(("done", False, f"Conversion failed with exit code {return_code}."))
                return

            png_files = sorted(output.glob(f"{base_name}_page_*.png"))
            png_count = len(png_files)
            if png_count == 0:
                self.events.put(("done", False, "No PNG pages were created, so OCR could not continue."))
                return

            final_pdf = output / f"{base_name}_OCR_eng_ell.pdf"
            if not self.create_searchable_pdf(tesseract, mutool, png_files, final_pdf):
                return

            elapsed = time.time() - self.started_at if self.started_at else 0
            self.events.put(
                (
                    "done",
                    True,
                    f"Done. Created {png_count} PNG file(s) and searchable PDF in {elapsed:.1f}s: {final_pdf}",
                )
            )
        except Exception as exc:
            self.events.put(("done", False, "Error: " + str(exc)))
        finally:
            self.process = None

    def create_searchable_pdf(self, tesseract, mutool, png_files, final_pdf):
        self.events.put(("status", "Running OCR and building searchable PDF..."))
        self.events.put(("log", f"OCR input pages: {len(png_files)}"))
        worker_count = max(1, min(os.cpu_count() or 1, len(png_files)))
        self.events.put(("log", f"OCR parallelism: {worker_count} page worker(s), one CPU thread each."))
        self.events.put(("log", "Final searchable PDF: " + str(final_pdf)))

        if not mutool:
            self.events.put(("done", False, "MuPDF mutool.exe is required to merge per-page OCR PDFs."))
            return False

        page_pdf_dir = final_pdf.parent / (final_pdf.stem + "_pages")
        try:
            if page_pdf_dir.exists():
                shutil.rmtree(page_pdf_dir)
            page_pdf_dir.mkdir(parents=True, exist_ok=True)
            if final_pdf.exists():
                final_pdf.unlink()

            page_pdfs = [None] * len(png_files)
            completed = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(self.ocr_one_page, tesseract, image_path, page_pdf_dir, index): index
                    for index, image_path in enumerate(png_files)
                }
                for future in concurrent.futures.as_completed(futures):
                    index = futures[future]
                    if self.cancel_requested:
                        break
                    ok, page_pdf, message = future.result()
                    if not ok:
                        self.events.put(("done", False, message))
                        return False
                    page_pdfs[index] = page_pdf
                    completed += 1
                    self.events.put(("log", f"OCR page {index + 1}/{len(png_files)} done."))
                    self.events.put(("status", f"OCR pages: {completed}/{len(png_files)}"))

                if self.cancel_requested:
                    self.events.put(("done", False, "Cancelled."))
                    return False

            missing = [index + 1 for index, path in enumerate(page_pdfs) if not path or not Path(path).is_file()]
            if missing:
                self.events.put(("done", False, "OCR failed; missing page PDF(s): " + ", ".join(map(str, missing))))
                return False

            self.events.put(("status", "Merging OCR pages into final PDF..."))
            merge_command = [mutool, "merge", "-o", str(final_pdf)]
            merge_command.extend(str(path) for path in page_pdfs)
            return_code, output = self.run_child_process(merge_command)
            for line in output:
                self.events.put(("log", "Merge: " + line))
            if return_code != 0:
                self.events.put(("done", False, f"PDF merge failed with exit code {return_code}."))
                return False
            if not final_pdf.is_file():
                self.events.put(("done", False, "Merge finished, but the searchable PDF was not created."))
                return False

            self.events.put(("log", "OCR searchable PDF created: " + str(final_pdf)))
            return True
        except Exception as exc:
            self.events.put(("done", False, "OCR error: " + str(exc)))
            return False
        finally:
            try:
                shutil.rmtree(page_pdf_dir, ignore_errors=True)
            except Exception:
                pass

    def ocr_one_page(self, tesseract, image_path, page_pdf_dir, index):
        output_base = page_pdf_dir / f"ocr_page_{index + 1:04d}"
        page_pdf = output_base.with_suffix(".pdf")
        command = [
            tesseract,
            str(image_path),
            str(output_base),
            "-l",
            OCR_LANGUAGES,
        ]
        if LOCAL_TESSDATA.is_dir():
            command.extend(["--tessdata-dir", str(LOCAL_TESSDATA)])
        command.append("pdf")

        env = os.environ.copy()
        env["OMP_THREAD_LIMIT"] = "1"

        return_code, output = self.run_child_process(command, env=env)
        if return_code != 0:
            detail = "; ".join(output[-3:]) if output else "no OCR output"
            return False, None, f"OCR page {index + 1} failed with exit code {return_code}: {detail}"
        if not page_pdf.is_file():
            return False, None, f"OCR page {index + 1} finished, but no page PDF was created."
        return True, page_pdf, ""

    def run_child_process(self, command, env=None):
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        with self.process_lock:
            self.child_processes.append(process)

        output = []
        try:
            assert process.stdout is not None
            for line in process.stdout:
                clean = line.strip()
                if clean:
                    output.append(clean)
                if self.cancel_requested and process.poll() is None:
                    try:
                        process.terminate()
                    except Exception:
                        pass
                    break
            return process.wait(), output
        finally:
            with self.process_lock:
                if process in self.child_processes:
                    self.child_processes.remove(process)

    def monitor_renderer_gpu(self, pid, stop_event, stats):
        pid_text = f"pid_{pid}_"
        command = (
            "$samples = (Get-Counter '\\GPU Engine(*)\\Utilization Percentage').CounterSamples "
            f"| Where-Object {{ $_.Path -like '*{pid_text}*' }} "
            "| ForEach-Object { "
            "[pscustomobject]@{ "
            "Engine=(($_.Path -replace '^.*engtype_','') -replace '\\\\utilization percentage$',''); "
            "Value=$_.CookedValue "
            "} }; "
            "$samples | ConvertTo-Json -Compress"
        )

        while not stop_event.is_set():
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", command],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                text = result.stdout.strip()
                if text:
                    parsed = json.loads(text)
                    rows = parsed if isinstance(parsed, list) else [parsed]
                    for row in rows:
                        value = float(row.get("Value", 0) or 0)
                        engine = str(row.get("Engine", "") or "").strip(")")
                        stats["samples"] += 1
                        if value > 0.01:
                            stats["nonzero"] += 1
                        if value > stats["max"]:
                            stats["max"] = value
                            stats["engine"] = engine
            except Exception:
                pass
            stop_event.wait(0.2)

    def poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "status":
                    self.status.set(event[1])
                elif kind == "log":
                    self.append_log(event[1])
                elif kind == "done":
                    success, message = event[1], event[2]
                    self.progress.stop()
                    self.start_button.configure(state=tk.NORMAL)
                    self.cancel_button.configure(state=tk.DISABLED)
                    self.status.set(message)
                    self.append_log(message)
                    if success:
                        messagebox.showinfo("PDF to PNG", message)
                    else:
                        messagebox.showwarning("PDF to PNG", message)
        except queue.Empty:
            pass
        self.after(100, self.poll_events)

    def append_log(self, text):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def clear_log(self):
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

    @staticmethod
    def safe_stem(stem):
        allowed = []
        for char in stem:
            if char.isalnum() or char in ("-", "_"):
                allowed.append(char)
            else:
                allowed.append("_")
        result = "".join(allowed).strip("_")
        return result or "page"


if __name__ == "__main__":
    app = PdfToPngApp()
    app.mainloop()
