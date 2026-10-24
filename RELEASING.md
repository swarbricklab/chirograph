# Releasing chirograph

How to cut a release and register it on **PyPI** and **conda-forge**. Steps needing an
account or web console — what a maintainer must do by hand — are marked **(manual)**.

## Versioning

The version is **static**, in two places that a test keeps in sync:

- `version` in [`pyproject.toml`](pyproject.toml)
- `_FALLBACK` in [`src/chirograph/version.py`](src/chirograph/version.py) (used only when
  running from a source tree)

`tests/test_version.py` fails if they drift, so bump both together. Tag `vX.Y.Z` to match.

## 1. Pre-flight (every release)

```bash
pytest && ruff check . && mypy
python -m build
python -m twine check --strict dist/*
```

`chiro test-vectors --check` (part of the suite) guards the frozen canonicalisation, and
`twine check --strict` catches metadata/README problems before PyPI sees them.

## 2. First PyPI release — set up Trusted Publishing (manual, once)

A [`publish.yml`](.github/workflows/publish.yml) workflow is already present, dormant.
To go live:

1. **(manual)** Create the project on PyPI — a first manual `twine upload`, or a
   **pending publisher** at <https://pypi.org/manage/account/publishing/> for an OIDC
   first upload.
2. **(manual)** On PyPI → `chirograph` → *Publishing* → add a trusted publisher:
   - Owner: `Swarbricklab` · Repository: `chirograph`
   - Workflow: `publish.yml` · Environment: `pypi`
3. **(manual)** Create a GitHub environment named `pypi`.
4. Uncomment the `release:` trigger in `publish.yml`.

## 3. Subsequent releases

1. Bump `version` + `_FALLBACK`; move `[Unreleased]` in `CHANGELOG.md` to the new version.
2. `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. **(manual)** Publish a GitHub Release → `publish.yml` builds, `twine check`s, and
   uploads via Trusted Publishing.

## 4. conda-forge (after the PyPI release)

conda-forge builds from the **PyPI sdist**.

1. Get the sdist sha256 (`pip download chirograph==X.Y.Z --no-deps --no-binary :all:`
   then `sha256sum`, or the PyPI *Download files* page, or `grayskull pypi chirograph`).
2. Fill `version` and `sha256` in [`conda-recipe/meta.yaml`](conda-recipe/meta.yaml).
3. **(manual)** Fork <https://github.com/conda-forge/staged-recipes>, copy the recipe to
   `recipes/chirograph/meta.yaml`, open a PR; the bots build it and a maintainer reviews.
4. Once merged you get a `chirograph-feedstock`; later releases are auto-PR'd by its bot.

The core is zero-dependency, so the recipe's `run` is just `python`. The `dvc` / `schema`
extras (`pyyaml`, `jsonschema`) are installed alongside by users who need them.
