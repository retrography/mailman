# Mailman

Sorts the mail arriving in a Gmail mailbox by rules you can read and change. A classifier (TypeSafe Jev)
answers plain questions about each email; rules written in YAML combine those answers with observations
about the email (sender lists, addresses, writing system, your own mailbox history) and decide: keep, label,
file out of the inbox, trash, block. A web interface shows every decision with its reasons and tests each
change on real emails before it is saved.

It runs as a [Home Assistant](https://www.home-assistant.io) app, or as a plain container or a local process.

## Install as a Home Assistant app

1. Settings → Apps → App store → ⋮ → Repositories, add `https://github.com/retrography/mailman`.
2. Install **Mailman** and follow its Documentation tab ([mailman/DOCS.md](mailman/DOCS.md)).

You need a TypeSafe API key and your own Google OAuth client (type "Desktop app", published "In production").

## Run it yourself

```bash
uv sync
cp -r starter config            # your configuration; edit config/profile.yaml
uv run mailman auth --client-secrets credentials.json --out data/token.json
MAILMAN_TOKEN_FILE=data/token.json TYPESAFE_API_KEY=… uv run mailman daemon
MAILMAN_TOKEN_FILE=data/token.json TYPESAFE_API_KEY=… uv run mailman web     # http://127.0.0.1:8377
```

As a container: build the `Dockerfile` and mount a folder at `/config`; set `TYPESAFE_API_KEY`,
`GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET`.

## Layout

- `src/mailman/engine/` — the rule engine: flags, questions, stages, outcomes, jobs, the daemon.
- `src/mailman/web/` — the interface (FastAPI and one plain JavaScript page).
- `starter/` — a neutral configuration with an invented person; the app starts from it.
- `mailman/` — the Home Assistant app manifest and documentation.

Your own configuration (`config/`) and data (`data/`) hold personal details and mail, and are not part of
this repository.

## A word of caution

This was built for one person's mailbox and moves mail to Trash on its own. Start by watching Logs, and
loosen or tighten the rules from there.
