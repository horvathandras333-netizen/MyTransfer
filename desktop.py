"""Basic Windows desktop interface for MyTransfer with automatic public links."""

from __future__ import annotations

import re
import secrets
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from werkzeug.serving import make_server

import app as transfer


TUNNEL_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)
TUNNEL_BINARY = transfer.RESOURCE_DIR / "vendor" / "cloudflared.exe"


class TransferServer(threading.Thread):
    def __init__(self, port: int) -> None:
        super().__init__(daemon=True)
        self.server = make_server("0.0.0.0", port, transfer.app)

    def run(self) -> None:
        self.server.serve_forever()

    def stop(self) -> None:
        self.server.shutdown()


class QuickTunnel:
    """Runs Cloudflare's temporary tunnel and captures its generated URL."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.process: subprocess.Popen[str] | None = None
        self.url: str | None = None
        self.lines: deque[str] = deque(maxlen=12)
        self.ready = threading.Event()

    @staticmethod
    def extract_url(line: str) -> str | None:
        match = TUNNEL_URL_PATTERN.search(line)
        return match.group(0) if match else None

    def start(self) -> None:
        if not TUNNEL_BINARY.is_file():
            raise FileNotFoundError("The bundled Cloudflare Tunnel component is missing.")
        startup = subprocess.STARTUPINFO() if sys.platform == "win32" else None
        if startup:
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        self.process = subprocess.Popen(
            [str(TUNNEL_BINARY), "tunnel", "--url", f"http://127.0.0.1:{self.port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startup,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        threading.Thread(target=self._read_output, daemon=True).start()

    def _read_output(self) -> None:
        if not self.process or not self.process.stdout:
            return
        for line in self.process.stdout:
            self.lines.append(line.strip())
            url = self.extract_url(line)
            if url:
                self.url = url
                self.ready.set()

    def wait_for_url(self, timeout: float = 35) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.url:
                return self.url
            if self.process and self.process.poll() is not None:
                break
            self.ready.wait(timeout=0.2)
        details = "\n".join(self.lines) or "Cloudflare Tunnel did not return a URL."
        raise RuntimeError(details)

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()


class MyTransferGUI:
    def __init__(self, root: tk.Tk, server: TransferServer, tunnel: QuickTunnel) -> None:
        self.root = root
        self.server = server
        self.tunnel = tunnel
        self.file_path = tk.StringVar()
        self.expiry = tk.StringVar(value="7d")
        self.public_url = tk.StringVar()
        self.status = tk.StringVar(value="Creating your public sharing address…")

        root.title("MyTransfer")
        root.minsize(760, 500)
        root.configure(bg="#f3f2f2")
        root.protocol("WM_DELETE_WINDOW", self.close)

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f2f2")
        style.configure("App.TFrame", background="#f3f2f2")
        style.configure("TLabel", background="#f3f2f2", foreground="#201e1d", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#f3f2f2", foreground="#201e1d", font=("Segoe UI", 20, "bold"))
        style.configure("Muted.TLabel", background="#f3f2f2", foreground="#706c6b")
        style.configure("TLabelframe", background="#f3f2f2", foreground="#201e1d", bordercolor="#8a8583")
        style.configure("TLabelframe.Label", background="#f3f2f2", foreground="#ec3013", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", padding=(11, 7), background="#f3f2f2", foreground="#201e1d", bordercolor="#8a8583", font=("Segoe UI", 9, "bold"))
        style.map("TButton", background=[("active", "#eae9e9")])
        style.configure("Primary.TButton", padding=(13, 7), background="#ec3013", foreground="#ffffff", bordercolor="#ec3013")
        style.map("Primary.TButton", background=[("active", "#ae1800")])
        style.configure("Treeview", background="#eae9e9", fieldbackground="#eae9e9", foreground="#201e1d", rowheight=31, borderwidth=0)
        style.configure("Treeview.Heading", background="#f3f2f2", foreground="#706c6b", font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", "#fff2ef")], foreground=[("selected", "#ae1800")])

        container = ttk.Frame(root, padding=18)
        container.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(6, weight=1)

        ttk.Label(container, text="MyTransfer", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(container, text="Choose a file and create a public download link.", style="Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 14))

        ttk.Label(container, text="File").grid(row=2, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(container, textvariable=self.file_path, state="readonly").grid(row=2, column=1, sticky="ew")
        ttk.Button(container, text="Browse…", command=self.choose_file).grid(row=2, column=2, sticky="e", padx=(8, 0))

        expiry_row = ttk.Frame(container)
        expiry_row.grid(row=3, column=1, sticky="w", pady=12)
        ttk.Label(container, text="Expires").grid(row=3, column=0, sticky="w")
        ttk.Radiobutton(expiry_row, text="After 3 days", variable=self.expiry, value="3d").grid(row=0, column=0, padx=(0, 15))
        ttk.Radiobutton(expiry_row, text="After 7 days", variable=self.expiry, value="7d").grid(row=0, column=1)
        ttk.Button(container, text="Create link", command=self.create_share, style="Primary.TButton").grid(row=3, column=2, sticky="e")

        ttk.Label(container, text="Public address").grid(row=4, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(container, textvariable=self.public_url, state="readonly").grid(row=4, column=1, columnspan=2, sticky="ew")
        ttk.Label(container, text="This temporary address stays active while MyTransfer is open.", style="Muted.TLabel").grid(row=5, column=0, columnspan=3, sticky="w")

        shares_frame = ttk.LabelFrame(container, text="Active shares", padding=10)
        shares_frame.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(14, 0))
        shares_frame.columnconfigure(0, weight=1)
        shares_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(shares_frame, columns=("file", "expires", "downloads", "size"), show="headings", selectmode="browse")
        for column, label, width in (("file", "File", 290), ("expires", "Expires", 175), ("downloads", "Downloads", 80), ("size", "Size", 80)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, anchor="w" if column == "file" else "center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(shares_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(shares_frame)
        actions.grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(actions, text="Refresh", command=self.refresh).grid(row=0, column=0, padx=4)
        ttk.Button(actions, text="Copy selected link", command=self.copy_selected).grid(row=0, column=1, padx=4)
        ttk.Button(actions, text="Revoke selected", command=self.revoke_selected).grid(row=0, column=2, padx=(4, 0))

        ttk.Label(container, textvariable=self.status, foreground="#245b3c", wraplength=700).grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self.refresh()
        threading.Thread(target=self.await_public_url, daemon=True).start()

    def await_public_url(self) -> None:
        try:
            url = self.tunnel.wait_for_url()
        except RuntimeError as error:
            self.root.after(0, lambda: self.tunnel_failed(str(error)))
            return
        self.root.after(0, lambda: self.tunnel_ready(url))

    def tunnel_ready(self, url: str) -> None:
        self.public_url.set(url)
        self.status.set("Public address ready. New links will work for anyone you send them to (allow a few seconds for first-time DNS setup).")

    def tunnel_failed(self, details: str) -> None:
        self.status.set("Could not create a public address. Check your internet connection and restart MyTransfer.")
        messagebox.showerror("Public address unavailable", f"MyTransfer could not create a Cloudflare Tunnel.\n\n{details}")

    def choose_file(self) -> None:
        chosen = filedialog.askopenfilename(title="Choose a file to share")
        if chosen:
            self.file_path.set(chosen)

    def link_for(self, share: dict) -> str:
        base = self.public_url.get().rstrip("/")
        if not base:
            raise ValueError("Your public address is still being created. Please wait a moment.")
        return f"{base}/download/{share['token']}"

    def create_share(self) -> None:
        chosen = Path(self.file_path.get())
        if not chosen.is_file():
            messagebox.showerror("Choose a file", "Choose a file before creating a link.")
            return
        if not self.public_url.get():
            messagebox.showerror("Public address not ready", "Your public address is still being created. Please wait a moment.")
            return
        try:
            share = transfer.create_share_from_path(chosen, self.expiry.get())
            link = self.link_for(share)
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not create link", str(error))
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(link)
        self.status.set(f"Public link created and copied: {link}")
        self.file_path.set("")
        self.refresh()

    def selected_share(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Select a share", "Select a share from the list first.")
            return None
        return next((share for share in transfer.load_shares() if share["id"] == selection[0]), None)

    def copy_selected(self) -> None:
        share = self.selected_share()
        if not share:
            return
        if transfer.is_expired(share):
            messagebox.showinfo("Link expired", "This share has expired. Revoke it to remove the stored copy.")
            return
        try:
            link = self.link_for(share)
        except ValueError as error:
            messagebox.showerror("Public address not ready", str(error))
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(link)
        self.status.set(f"Copied public link: {link}")

    def revoke_selected(self) -> None:
        share = self.selected_share()
        if not share:
            return
        if not messagebox.askyesno("Revoke share", f"Revoke the link and delete the stored copy of '{share['original_name']}'?"):
            return
        path = transfer.UPLOAD_DIR / share["stored_name"]
        try:
            path.unlink(missing_ok=True)
            transfer.save_shares([item for item in transfer.load_shares() if item["id"] != share["id"]])
        except OSError as error:
            messagebox.showerror("Could not revoke share", str(error))
            return
        self.status.set("Share revoked and stored file deleted.")
        self.refresh()

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        shares = sorted(transfer.load_shares(), key=lambda item: item["created_at"], reverse=True)
        for share in shares:
            expires = "Expired" if transfer.is_expired(share) else transfer.format_datetime(share["expires_at"])
            self.tree.insert("", "end", iid=share["id"], values=(share["original_name"], expires, share["downloads"], transfer.format_size(share["size"])))

    def close(self) -> None:
        self.tunnel.stop()
        self.server.stop()
        self.root.destroy()


def main() -> None:
    # The GUI is the only management interface. A random password blocks the
    # optional browser dashboard while leaving token-based downloads public.
    transfer.ADMIN_PASSWORD = secrets.token_urlsafe(32)
    transfer.initialise_storage()
    port = 8080
    try:
        server = TransferServer(port)
        tunnel = QuickTunnel(port)
        tunnel.start()
    except (OSError, FileNotFoundError) as error:
        error_root = tk.Tk()
        error_root.withdraw()
        messagebox.showerror("MyTransfer could not start", str(error))
        return
    server.start()
    root = tk.Tk()
    MyTransferGUI(root, server, tunnel)
    root.mainloop()


if __name__ == "__main__":
    main()
