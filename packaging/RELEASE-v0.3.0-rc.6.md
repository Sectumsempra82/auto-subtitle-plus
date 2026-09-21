# Auto Subtitle Plus v0.3.0-rc.6

This release pins the Windows x64 build to a reproducible GitHub Actions pipeline.

## Windows

- Windows x64 CLI and GUI builds run on every push and pull request after the regression suite passes.
- Versioned tags publish the CLI and GUI ZIPs plus SHA256 checksums as release assets.
- The `continuous` prerelease tracks the latest successful `main` build.
- The launcher explains that Python is an isolated app-local runtime and does not modify system Python, PATH, or the registry.
- `--diagnose` reports the effective Python runtime, backend, model, device, application data, and cache paths.
- Model and cache behavior is documented in the project wiki, including offline use and Linux CLI guidance.

## Validation

- Windows regression suite runs in GitHub Actions on Python 3.13.
- Benchmark and faster-backend test extras are installed explicitly in CI.
- CI artifacts include the regression log when tests fail.
- Windows installer/update behavior remains covered by the existing release validation process.

## Scope

This is a Windows automation and CLI diagnostics milestone. The macOS Apple Silicon build remains a locally compiled release until its native build and signing process is standardized.
