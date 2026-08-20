# Local development (Flyerz Artwork Intelligence)

## Start the stack (required)

From the project root in PowerShell:

```powershell
.\start_dev.ps1
```

This script:

1. Stops anything listening on ports **3000**, **5000**, and **5173**.
2. Force-stops **node**, **python** / **pythonw**, and Ghostscript CLI (**gswin64c** / **gswin32c**) so headless workers do not linger.
3. Clears **`server/temp/`**, **`server/output/`**, **`dist/`**, and **`.next/`** (creates empty temp/output folders if missing).
4. Runs **`npm run dev`**, which starts the **single** dev process used by this repo: Express (API) plus Vite in middleware mode (UI). There is no separate `python main.py` server for the web app; root `main.py` is a stub.

After it boots, open **http://localhost:5000/** unless `PORT` in `.env` overrides the port.

### Auto-start when opening this folder in Cursor

Opening this workspace runs the **Start Flyerz (LAN)** task (`.vscode/tasks.json`) with `-SkipGlobalKill`, so the app boots without killing Cursor’s Node processes. Allow automatic tasks if Cursor prompts once.

Colleagues can use **http://192.168.0.100:5000/** while this PC is on the office network and the task is running. If DHCP changes your IP, share the new TEAM LINK from the task terminal (or ask IT for a reservation on `.100`).

### Options

- **Cleanup only** (no server): `.\start_dev.ps1 -NoBoot`
- **Do not global-kill all Node/Python/GS** (only free the ports above): `.\start_dev.ps1 -SkipGlobalKill`

If your editor relies on Node (e.g. Cursor/VS Code extensions), run `start_dev.ps1` from an **external** PowerShell window so killing every `node.exe` does not reset the IDE.

## Office LAN sharing (intermittent)

With `LAN_ONLY_MODE=true` and `HOST=0.0.0.0` in `.env`, the app accepts connections from the company network while this machine is online and the server is running. On boot, the console prints **TEAM LINK (IP)** and **TEAM LINK (name)** — share those with colleagues.

- Works only while this PC is on the company network, awake, and the app is running.
- Prefer the **IP** link if the PC name does not resolve for other machines.
- First time (Admin PowerShell), allow the port:

```powershell
New-NetFirewallRule -DisplayName "Flyerz Artwork Intelligence" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow
```

To go back to laptop-only access, set `LAN_ONLY_MODE=false` and `HOST=127.0.0.1` (or remove `HOST`), then restart.

## Legacy one-liner

`npm run dev` alone still works, but day-to-day restarts should use **`.\start_dev.ps1`** so ports, processes, and caches stay clean.
