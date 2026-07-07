# Third-Party Notices

This project optionally integrates with the third-party open-source software listed below.
These components are **not** required for the Cloud Run runtime (`requirements.txt`); they are
used only by the optional developer / visualization tooling (`requirements-dev.txt`).

## Rerun

- Used by: `tools/export_to_rerun.py` (optional "Rerun mode" visualization export)
- Package: `rerun-sdk`
- Repository: https://github.com/rerun-io/rerun
- License: MIT OR Apache-2.0
- Copyright: Rerun Technologies AB and the Rerun contributors

Rerun is dual-licensed under the MIT license and the Apache License, Version 2.0. See
https://github.com/rerun-io/rerun/blob/main/LICENSE-MIT and
https://github.com/rerun-io/rerun/blob/main/LICENSE-APACHE for the full license texts.

This project uses Rerun as an unmodified pip dependency and does not redistribute Rerun's source
code. The `.rrd` recordings produced by `tools/export_to_rerun.py` contain only this project's own
synthetic data.
