# QAlprit Repository Rules

These project rules govern git workflow, versioning, PyPI publishing, website/Vercel deployments, report generation, and safety in this repository.

## 1. Git, Commits & PR Workflow

- **Never commit directly to `main`**: Create a feature/fix branch off `main` (e.g., `feat/...`, `fix/...`, `docs/...`, `release/...`).
- **Always proceed through PR & merge**: Open a Pull Request targeting `main`, wait for all GitHub Actions CI checks and Vercel checks to pass, squash-merge the PR into `main`, and fast-forward local `main` (`git checkout main && git pull --ff-only origin main`).
- **Stage explicit file paths**: Never run `git add .` blindly. Preserve unrelated uncommitted work (such as local edits in `docs/architecture.drawio`) and respect `.gitignore`.
- **Pre-commit verification**:
  - Run `.venv/bin/pytest -v` after Python or template changes (all tests must pass).
  - Run `node --check web/app.js` after editing frontend JS.
  - Validate sample suites when specs, flows, steps, or locators change (`qalprit validate --suite mda --env mda` and `qalprit validate --suite regression --env qa`, using `.env` or `config/env.example` without committing `.env`).

## 2. Versioning & Releases

- **Bump both version files together**: Every release must update the exact same `X.Y.Z` version in both:
  - `pyproject.toml` (`version = "X.Y.Z"`)
  - `src/qalprit/__init__.py` (`__version__ = "X.Y.Z"`)
- **Tagging**: Tag merged commits on `main` as `vX.Y.Z` before creating a GitHub Release.
- **Immutable PyPI versions**: Never re-upload to an already-published PyPI version number — bump at least the patch version (`X.Y.Z+1`) for any new PyPI release or `README.md` update on PyPI.

## 3. PyPI Publishing (`qalprit`)

- **Primary release flow**: Publishing a GitHub Release (`vX.Y.Z`) triggers `.github/workflows/publish.yml` (`pypa/gh-action-pypi-publish`) via Trusted Publishing.
- **Manual fallback upload**:
  1. Build fresh packages: `.venv/bin/python -m build` (or `python -m build`) and validate with `twine check dist/*`.
  2. Authenticate with `TWINE_USERNAME=__token__` and `TWINE_PASSWORD` read safely from local `.env` (`PYPI_TOKEN`) without ever printing, logging, or committing `.env` or token values.
  3. Upload **only the target version's artifacts** (`dist/qalprit-<X.Y.Z>*`) so older wheels/sdists in `dist/` are not re-uploaded.
- **PyPI README rendering**: All image and file links in `README.md` must use absolute GitHub URLs (`https://raw.githubusercontent.com/vattitude-me/QAlprit/main/...` for images and `https://github.com/vattitude-me/QAlprit/blob/main/...` for files), because PyPI rejects/404s relative paths.
- **Post-publish check**: Verify `https://pypi.org/pypi/qalprit/<X.Y.Z>/json` and `https://pypi.org/project/qalprit/` reflect the new version.

## 4. Website (`web/`), Vercel & Report Consistency

- **Vercel static config (`vercel.json`)**: Preserve `"framework": null`, `"installCommand": ""`, `"buildCommand": null`, `"outputDirectory": "web"`, and `"cleanUrls": true` so Vercel serves `web/` statically instead of misdetecting `pyproject.toml` as a serverless Python app.
- **Domain & Attribution**: Production domain is `https://qalprit.vattitude.ca` (sample report at `/sample-report`). Maintain the `Managed by vattitude.ca` (`https://vattitude.ca`) footer across `web/index.html`, `src/qalprit/report/templates/report.html.j2`, `src/qalprit/ui/index.html`, and `README.md`.
- **Sample Data & Theme Sync**:
  - Keep `web/index.html`, `web/app.js`, and `web/previews/*` synchronized with `examples/mda-sample-run/results.json` and `web/sample-report.html`.
  - Keep the adaptive light/dark teal-slate theme (`#0f766e` / `#2dd4bf` accent; `#f3f6f9` / `#090e1a` backgrounds) aligned between `web/styles.css` and `src/qalprit/report/templates/report.html.j2`.
- **Report Regeneration**:
  - Regenerate `examples/mda-sample-run/report.html` with `home_url=None` (standalone CLI sample artifact).
  - Regenerate `web/sample-report.html` with `home_url="/"` so website visitors have the sticky top nav bar, `← Back to Home` header button, and footer home link back to `/`.
  - Never inject `data:image/` URIs into `report.html.j2` when `external_assets=True`.

## 5. Documentation, Privacy & Safety

- **Concise Docs & Architecture Flow**: Keep `README.md` clear and accessible (prerequisites, install, run, output, and simple animated architecture flow). Keep `docs/architecture.svg` and `web/architecture.svg` synchronized when updating the diagram.
- **Secrets & Ignored Artifacts**: Never commit `.env`, tokens/credentials, `.apk`/`.ipa` binaries (`apps/*`), raw test runs (`results/*`), `.agents/`, `PLAN.md`, `docs/PLAN.md`, `flows/hark/`, `specs/smoke/`, or unredacted screenshots.
- **Destructive Actions**: Always ask the user for explicit confirmation before deleting files or directories. Use `tempfile.TemporaryDirectory()` for temporary build/report-generation links instead of creating files that require deletion.
