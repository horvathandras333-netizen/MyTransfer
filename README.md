# MyTransfer

MyTransfer is a small file-sharing app that runs on your computer. It automatically creates a temporary public HTTPS address, so you can upload a file, choose an expiry, and share a working download link. Revoking a share immediately removes both the link and the stored copy.

## Start it

1. Open PowerShell in this folder.
2. Create an isolated environment and install the one dependency:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

3. Run the desktop app:

   ```powershell
   python desktop.py
   ```

4. Use the MyTransfer window to choose files and create links.

Alternatively, after downloading the release build, run `dist\MyTransfer.exe` directly. No Python installation is needed for that executable.

## Sharing outside your home network

The desktop app includes Cloudflare's `cloudflared` component. On startup it creates a free, random `trycloudflare.com` address automatically; no account or router configuration is required. Wait until the **Public address** field is populated, then create and send a link. Keep MyTransfer open and your computer online while the file is being downloaded. The temporary address changes whenever MyTransfer restarts.

Cloudflare describes these Quick Tunnels as intended for testing and development, with no uptime guarantee and a 200 concurrent-request limit. For a long-term, stable address, a named Cloudflare Tunnel and your own domain would be the appropriate next step.

## Configuration

- `PORT`: server port (default `8080`)
- `MYTRANSFER_PUBLIC_URL`: the public tunnel URL used in generated links
- `MYTRANSFER_MAX_FILE_SIZE`: maximum upload size in bytes (default 5 GB)
- `MYTRANSFER_SECRET_KEY`: optional persistent secret key

When using the executable, shared files and metadata are stored under `%LOCALAPPDATA%\MyTransfer`. The source version stores them under `data/`, which is ignored by Git.
