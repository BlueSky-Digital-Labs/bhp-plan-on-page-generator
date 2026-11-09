# Name, header text and look

## The names the page shows (no rebuild)

The app name, the header text and the footer text come from configuration, not from the built
frontend. The page asks `GET /api/branding` (public: the sign-in page needs it, and it holds nothing
secret) before it draws, so a new name needs a **restart, not a rebuild**.

| Variable | Shown in | Default |
| --- | --- | --- |
| `APP_NAME` | the main heading on the sign-in page, the browser tab, the appendix title, the email subject and body | `SCP AI Ideation assistant` |
| `HEADER_TEXT` | the header, next to the BHP logo | `SCP AI Ideation assistant` |
| `FOOTER_TEXT` | the footer | `BHP` |

For the Integrated Operations name, for example:

```bash
export APP_NAME="Integrated Operations Plan on a Page"
export HEADER_TEXT="Integrated Operations"
```

Each is 1 to 80 plain characters (control characters are refused and the app will not start with a bad
value; the error names the setting, not its value). The page shows them as **plain text**, never as
markup, and falls back to the defaults if the names cannot be read. The one-page canvas template
(its title, the "BHP" mark and "AI LAB" footer) is not configurable and is unchanged.

## Colours and logo

The palette comes from the BHP logo orange, sampled from the logo file (`#ED5400`):

| Token | Value | Used for |
| --- | --- | --- |
| `--brand` | `#ED5400` | The logo, the header and card accent bars, the assistant avatar ring |
| `--accent` | `#C44400` | Buttons, links and orange text (white on it is 5.0:1) |
| `--accent-hover` | `#A33800` | Hover and pressed |
| `--brand-tint` | `#FFF1EA` | Soft backgrounds, hover on secondary buttons, notices |
| `--fg` / `--muted` / `--border` / `--surface` | `#1F1D1B` / `#5C5650` / `#E3DDD7` / `#FAF8F6` | Warm neutrals for text and surfaces |

The raw logo orange is for marks and accents only: white text on it is 3.6:1, below the 4.5:1 for
normal text, so buttons use the darker `--accent`. Focus rings are a 3 px dark outline, controls are at
least 44 px high, and motion respects `prefers-reduced-motion`. The one-page canvas keeps its own
colours and typography (`.one-pager`, `.op-*`); the new page-wide rules exclude it.

The logo is `frontend/public/bhp-logo.png` (the supplied logo, scaled to 480 px wide).
