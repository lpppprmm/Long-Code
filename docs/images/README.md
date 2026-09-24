# README images

[← Back to Long Code](../../README.md)

| Asset | Source |
| --- | --- |
| `hero.svg` | Editable vector artwork created for this repository. |
| `session-lifecycle.svg` | Editable diagram of the implemented session lifecycle. |
| `workspace.png` | The actual frontend rendered with deterministic, illustrative offline data. |
| `task-record.png` | The task-record dialog from the same offline capture. |

The screenshots are examples of the interface, not results from a live model run. The
capture script intercepts API requests and blocks external requests. It does not open
registered projects, read API keys, or change application data.

To recreate the screenshots after installing the frontend dependencies and Playwright's
Chromium browser:

```sh
node frontend/scripts/capture-readme.mjs
```

Run this from the repository root. Port 5180 must be available. A Chinese font must be
available to Chromium; if your system does not provide one, pass a local font explicitly:

```sh
README_FONT_PATH=/path/to/NotoSansCJKsc-Regular.otf node frontend/scripts/capture-readme.mjs
```

The font is used only during rendering and is not copied into this repository. The script
also writes SVG previews and a local Markdown layout preview under `/tmp/long-code-readme-*`.
