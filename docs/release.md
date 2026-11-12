# Releasing ccc (ccc_TUI)

Read this before you tag, build, sign or publish anything. The Android app has its own repository
and releases (`ccc_android`, its own `docs/release.md`).

## Language

**All text written for a release is in English**: the release title, the release notes, the tag message,
the commit messages of the release, and any text shown to users in connection with it. (The rest of the
project, such as PR descriptions, may be in Czech.)

## File names carry the version

Every file a user downloads is named with the version of the tag (no `v`):

- `ccc_X.Y.Z.tar.gz` — ccc (TUI, daemon, API) as a Python sdist, for example `ccc_1.4.0.tar.gz`;
- `ccc_X.Y.Z_all.deb` — the system package.

Otherwise the user cannot tell which version they have downloaded. `SHA256SUMS` must list those names
(`sha256sum -c` has to pass); `SHA256SUMS` and `SHA256SUMS.asc` keep their names.

**Current state:** releases still attach `ccc.tar.gz` and `ccc_all.deb`. The rename is not done yet.
It cannot be done in the workflow alone: ccc finds new versions by the exact asset name (`ASSET` in
`src/ccc/releases.py`, and `release.yml`), and shares its rules with the Android app through
`tests/fixtures/release-rules.json` (the app keeps a copy). So:

1. Teach the updater to accept `ccc_<version>.tar.gz` — the same rule in ccc and in the app, tests in both.
2. For a transition period attach **both** the new name and the old `ccc.tar.gz`; otherwise installed
   older versions do not find the new release and never update themselves.
3. Update the docs that name the old files (`help/*/android-api.md`, README) and
   `ccc-deb-sign-upload.sh` in `~/Documents/CC/bin`.

## Steps

1. Everything for the release is merged to `main`; tests pass.
2. Tag `X.Y.Z` (no `v`) on `main`.
3. The `Release` workflow (`.github/workflows/release.yml`) builds and tests, attaches the tarball and keeps
   the system package as the artifact `deb`. An artifact already published is never replaced.
4. The maintainer signs the package on their own computer: `ccc-deb-sign-upload.sh X.Y.Z`
   (`~/Documents/CC/bin`). It downloads the `deb` artifact, signs `SHA256SUMS` locally and attaches the
   package, `SHA256SUMS` and `SHA256SUMS.asc` together. The release key never reaches GitHub. The shared
   APT repository (`Icenux/icenux`) takes the package from there.
5. Check the release: the files are named with the version, the signature verifies, the release is "Latest".

## Release text

- Title: `ccc X.Y.Z`.
- Notes: what changed for the user, in English, one line per change, with the issue number when there is one.
