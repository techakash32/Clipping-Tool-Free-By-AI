# Web UI

A minimal black-and-white dashboard for Podcast Shorts Factory. It wraps the
existing `run.py` CLI — it doesn't replace or duplicate any pipeline logic.

## Run it

```bash
pip install -r requirements.txt   # now includes fastapi/uvicorn
python webapp/server.py
```

Open **http://127.0.0.1:8787**

## What it does

- **Setup tab** — pick your AI provider and paste its API key (saved to
  `.env`), upload your YouTube `client_secret.json`, and click **Connect
  YouTube**. That runs `python run.py auth-youtube` for you and opens the
  Google sign-in window on this machine (it's a local OAuth flow — normal,
  expected, and only needs doing once).
- **Dashboard tab** — paste a podcast URL and click **Run**. This runs
  `python run.py auto <url> [--yes]` in the background and streams its
  live output into the log panel, with a simple stage tracker (Find → Review
  → Edit → Finishing → Upload → Stats).
- **History tab** — lists clips already recorded in `factory.db`.

## Notes

- This is a single-user, local tool — same trust model as the CLI. Don't
  expose port 8787 to the internet as-is; there's no login.
- Nothing here changes how the factory itself works — it just gives you
  buttons instead of typing `python run.py ...` commands.
