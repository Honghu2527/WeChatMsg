"""Main tkinter window for the Windows GUI MVP."""

from __future__ import annotations

import os
import tkinter as tk
from datetime import date
from tkinter import filedialog, messagebox, ttk

from gui import services
from gui.workers import BackgroundTasks


class MainWindow(ttk.Frame):
    def __init__(self, root):
        super().__init__(root, padding=12)
        self.root = root
        self.tasks = BackgroundTasks(root)
        self.accounts = []
        self.contacts = []
        self.visible_contacts = []
        self.database = None
        self.output_path = ""
        self.detection_diagnostics = []
        self._build()

    def _build(self):
        self.grid(sticky="nsew")
        self.root.title("WeChatMsg — Local Chat Export")
        self.root.geometry("920x760")
        self.root.minsize(760, 640)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        account = ttk.LabelFrame(self, text="1. WeChat 4.x database", padding=10)
        account.grid(row=0, column=0, sticky="ew")
        account.columnconfigure(1, weight=1)
        ttk.Button(account, text="Detect WeChat", command=self.detect).grid(row=0, column=0, padx=(0, 8))
        self.account_box = ttk.Combobox(account, state="readonly")
        self.account_box.grid(row=0, column=1, sticky="ew")
        self.account_box.bind("<<ComboboxSelected>>", lambda _event: self.update_account_details())
        ttk.Button(account, text="Prepare database", command=self.prepare).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(account, text="Use prepared database…", command=self.choose_prepared).grid(row=1, column=0, pady=(8, 0), sticky="w")
        ttk.Label(account, text="Working directory:").grid(row=1, column=1, pady=(8, 0), sticky="e")
        self.workspace = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "WeChatMsg", "decrypted"))
        ttk.Entry(account, textvariable=self.workspace).grid(row=1, column=2, pady=(8, 0), sticky="ew")

        ttk.Button(account, text="Detection details…", command=self.show_detection_details).grid(
            row=2, column=0, pady=(8, 0), sticky="w"
        )
        self.account_details = tk.StringVar(value="No account detected yet.")
        ttk.Label(
            account,
            textvariable=self.account_details,
            wraplength=720,
            justify="left",
        ).grid(row=2, column=1, columnspan=2, padx=(8, 0), pady=(8, 0), sticky="w")

        search_frame = ttk.Frame(self)
        search_frame.grid(row=1, column=0, sticky="ew", pady=(12, 6))
        search_frame.columnconfigure(1, weight=1)
        ttk.Label(search_frame, text="2. Search contacts:").grid(row=0, column=0, padx=(0, 8))
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self.apply_filter())
        ttk.Entry(search_frame, textvariable=self.search).grid(row=0, column=1, sticky="ew")

        columns = ("remark", "nickname", "wxid")
        self.contact_list = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse")
        for key, title, width in (("remark", "Remark", 190), ("nickname", "Nickname", 190), ("wxid", "Internal wxid", 300)):
            self.contact_list.heading(key, text=title)
            self.contact_list.column(key, width=width)
        self.contact_list.grid(row=2, column=0, sticky="nsew")

        options = ttk.LabelFrame(self, text="3. Export options", padding=10)
        options.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(options, text="Start date (YYYY-MM-DD)").grid(row=0, column=0, sticky="w")
        ttk.Label(options, text="End date (YYYY-MM-DD)").grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.start_date = tk.StringVar(value="2000-01-01")
        self.end_date = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(options, textvariable=self.start_date, width=18).grid(row=1, column=0, sticky="w")
        ttk.Entry(options, textvariable=self.end_date, width=18).grid(row=1, column=1, sticky="w", padx=(12, 0))
        self.html = tk.BooleanVar(value=True)
        self.docx = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="HTML", variable=self.html).grid(row=1, column=2, padx=(20, 4))
        ttk.Checkbutton(options, text="DOCX", variable=self.docx).grid(row=1, column=3, padx=4)
        self.output = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "WeChatMsg", "exports"))
        ttk.Entry(options, textvariable=self.output).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Button(options, text="Choose output…", command=self.choose_output).grid(row=2, column=3, padx=(8, 0), pady=(10, 0))
        options.columnconfigure(2, weight=1)

        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(actions, maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.export_button = ttk.Button(actions, text="Export", command=self.export)
        self.export_button.grid(row=0, column=1)
        self.open_button = ttk.Button(actions, text="Open output folder", command=self.open_output, state="disabled")
        self.open_button.grid(row=0, column=2, padx=(8, 0))
        self.status = tk.StringVar(value="All processing stays on this computer. Original WeChat data is read-only.")
        ttk.Label(self, textvariable=self.status, wraplength=860).grid(row=5, column=0, sticky="w", pady=(8, 0))

    def busy(self, text):
        self.status.set(text)
        self.export_button.configure(state="disabled")

    def idle(self):
        self.export_button.configure(state="normal")

    def fail(self, error):
        self.idle()
        self.status.set(str(error))
        messagebox.showerror("WeChatMsg", str(error))

    def progress_update(self, value, text):
        self.progress["value"] = value * 100
        self.status.set(text)

    def detect(self):
        self.busy("Looking for running WeChat 4.x processes…")
        self.tasks.submit(services.detect_accounts_detailed, self._detected, self.fail)

    def _detected(self, result):
        self.idle()
        accounts, diagnostics = result
        self.accounts = accounts
        self.detection_diagnostics = diagnostics
        self.account_box["values"] = [item.display_name for item in accounts]
        self.account_box.current(0)
        self.update_account_details()
        selected = accounts[0]
        if selected.key_found:
            self.status.set(f"Detected {len(accounts)} WeChat account(s). Choose Prepare database.")
        else:
            self.status.set(
                f"Detected {len(accounts)} account(s), but the selected account has no database key. "
                "Open Detection details for version/PID/path diagnostics."
            )

    def update_account_details(self):
        index = self.account_box.current()
        if index < 0 or index >= len(self.accounts):
            self.account_details.set("No account selected.")
            return
        account = self.accounts[index]
        key_text = "FOUND" if account.key_found else "NOT FOUND"
        self.account_details.set(
            f"Version: {account.version or 'unknown'} | PID: {account.pid or 'unknown'} | "
            f"Database key: {key_text}\nData folder: {account.source_dir or 'not detected'}"
        )

    def show_detection_details(self):
        index = self.account_box.current()
        selected_text = "No account selected."
        if 0 <= index < len(self.accounts):
            selected_text = self.accounts[index].diagnostic_summary
        process_text = "\n".join(self.detection_diagnostics) if self.detection_diagnostics else "No scan has run yet."

        window = tk.Toplevel(self)
        window.title("WeChat detection details")
        window.geometry("760x480")
        text = tk.Text(window, wrap="word", padx=10, pady=10)
        text.pack(fill="both", expand=True)
        text.insert(
            "1.0",
            "Selected account\n================\n"
            + selected_text
            + "\n\nProcess scan\n============\n"
            + process_text
            + "\n\nNote: database key material is never displayed or copied into these diagnostics.",
        )
        text.configure(state="disabled")

    def prepare(self):
        index = self.account_box.current()
        if index < 0:
            self.fail(services.GuiServiceError("No WeChat 4.x account is selected. Run detection first."))
            return
        account = self.accounts[index]
        if not account.key_found:
            self.fail(
                services.GuiServiceError(
                    "The account/data folder was detected, but the database key was not found. "
                    "Open Detection details and keep that information for troubleshooting."
                )
            )
            return
        self.busy("Preparing the database…")
        callback = lambda value, text: self.tasks.post(self.progress_update, value, text)
        self.tasks.submit(lambda: services.prepare_database(account, self.workspace.get(), callback), self._prepared, self.fail)

    def choose_prepared(self):
        selected = filedialog.askdirectory(title="Select prepared db_storage directory")
        if not selected:
            return
        self.busy("Opening the prepared database…")
        self.tasks.submit(lambda: services.use_prepared_database(selected), self._prepared, self.fail)

    def _prepared(self, prepared):
        self.status.set("Opening database and loading contacts…")
        self.tasks.submit(lambda: self._open_and_load(prepared), self._contacts_loaded, self.fail)

    @staticmethod
    def _open_and_load(prepared):
        database = services.open_database(prepared)
        return database, services.load_contacts(database)

    def _contacts_loaded(self, result):
        self.idle()
        self.database, self.contacts = result
        self.apply_filter()
        self.progress["value"] = 0
        self.status.set(f"Loaded {len(self.contacts)} contacts. Select one to export.")

    def apply_filter(self):
        self.visible_contacts = services.filter_contacts(self.contacts, self.search.get())
        self.contact_list.delete(*self.contact_list.get_children())
        for index, contact in enumerate(self.visible_contacts):
            self.contact_list.insert("", "end", iid=str(index), values=(
                getattr(contact, "remark", ""), getattr(contact, "nickname", ""), getattr(contact, "wxid", "")
            ))

    def choose_output(self):
        selected = filedialog.askdirectory(title="Choose export output directory")
        if selected:
            self.output.set(selected)

    def export(self):
        selection = self.contact_list.selection()
        if not selection:
            self.fail(services.GuiServiceError("No contact is selected."))
            return
        contact = self.visible_contacts[int(selection[0])]
        formats = [name for name, enabled in (("HTML", self.html.get()), ("DOCX", self.docx.get())) if enabled]
        self.busy("Exporting selected chat…")
        callback = lambda value, text: self.tasks.post(self.progress_update, value, text)
        self.tasks.submit(
            lambda: services.export_contact(self.database, contact, formats, self.output.get(), self.start_date.get(), self.end_date.get(), callback),
            self._exported,
            self.fail,
        )

    def _exported(self, output_path):
        self.idle()
        self.output_path = output_path
        self.open_button.configure(state="normal")
        self.status.set("Export completed successfully.")
        messagebox.showinfo("WeChatMsg", "Export completed successfully.")

    def open_output(self):
        try:
            services.open_folder(self.output_path or self.output.get())
        except services.GuiServiceError as exc:
            self.fail(exc)

    def close(self):
        self.tasks.shutdown()
        self.root.destroy()
