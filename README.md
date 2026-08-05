# tl-dashboards

Static, self-contained Backlog dashboards — built on a schedule, encrypted at rest, decrypted in the browser with a passphrase.

## How it works

- `pull.py` fetches issue metadata from a Backlog space (API key via env).
- `build.py` renders one self-contained HTML page per configured project (plus an aggregate page), encrypts the payload with AES-GCM (key derived from a passphrase via PBKDF2), and writes a static site tree.
- `history.py` upserts a small per-day aggregate (counts only) into the encrypted `history.enc`, which CI commits back — the accumulating data behind trend views.
- GitHub Actions runs this daily (and on manual dispatch) and deploys to GitHub Pages.
- Viewers open the page, enter the shared passphrase, and everything — decryption, filtering, language, charts — runs client-side. No backend, no plaintext at rest.

## Configuration

The engine is fully config-driven. All instance specifics (Backlog space, project keys, status mappings, scopes, thresholds, labels) live in a JSON config injected at build time — in CI, from the `DASH_CONFIG_B64` secret. Nothing instance-specific is committed to this repository.

Secrets required (Actions → Secrets):

| Secret | Content |
|---|---|
| `BACKLOG_API_KEY` | Backlog API key (read scope) |
| `DASH_PASSPHRASE` | shared passphrase encrypting every page |
| `DASH_CONFIG_B64` | base64 of the instance config JSON |

## Local build

```
export BACKLOG_API_KEY=...
python pull.py  --config config.json --out cache/data.json
python build.py --config config.json --data cache/data.json --outdir dist --enc "passphrase"
```

Never publish a plaintext build (`build.py` without `--enc*`).
