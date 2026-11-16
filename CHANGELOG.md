# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/),
versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-11-16

First version.

- Place PDF labels (e.g. 4×6") on the four quarters of an A4 sticker sheet
- Live preview with the printer's non-printable margin, warnings when a label does not fit
- Auto-fit based on the measured content of the label; manual scale, offset and 90° rotation per sticker
- Tracking of used stickers between runs; "New sheet" button
- Test mode with sticker outlines for plain-paper alignment checks
- 600 dpi rendering, optional sharp black & white output, save as PDF, printing via CUPS (`lp`)
- English and Polish interface (from the locale or `--lang`), `--version`
- Known: print alignment on real hardware is not yet verified; use the plain-paper test mode first
- The saved state moved to `~/.config/a4-label-sheets/`; a state file from the earlier working name
  (`~/.config/etykiety-a4/`) is still read once if the new one does not exist
