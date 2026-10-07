# Plexi A/B site

Put `build_site.py` and `index.html` in the same folder, then:

    pip install numpy scipy
    python build_site.py "C:\Users\justin\Dropbox\CubaseFiles\plexitest9-26\Mixdown" --serve

ffmpeg must be on PATH (`winget install Gyan.FFmpeg`). The `docs` folder is the finished page. Upload it to any static host (GitHub Pages, Netlify, Cloudflare Pages, your own site). It will not play from `file://`.

## Editing names and presets

The first run writes `docs/settings.json`. Edit it, then run the same command again (it takes about 30 seconds):

- `title`, `intro`: the page heading and blurb.
- `settings`: the `label` and `detail` shown for each setup. Add a `note` for a tooltip.
- `riffs`: the title shown for each riff. Old names are tidied for display ("bridgt" becomes bridge, "neck bridge shuffle" becomes bridge shuffle, "shuffle single notes" becomes single notes), so renaming your files is optional.
- `group_order`: the order of the sections in the table, for example `["Original mic", "67 settings", "73 settings", "18 watt"]` (the default). Reorder the list and rebuild. A section left out of the list goes at the end. Within each section, the order of the rows follows the order of the rows in `features.csv` (see below).
- `featured`: your own comparison presets, `[{"a": "<setting id>", "b": "<setting id>", "title": "Does 10k matter?"}]`. When this is empty the page lists every pair that differs by exactly one knob.

Existing entries in `settings.json` are never overwritten. Delete the file to regenerate it.

## The feature table and the appendix

Both live in the `docs` folder and are never overwritten by a rebuild.

- `features.csv`: one row per recording, one column per setting in the table. Put `1` for a check, `0` for an x, and leave a cell blank to show a `?`. For either/or columns, `1` means the first option in the heading (volume 8, 100pf, split cathode, 0.022uf, 67-spec, 0.1uf, 48+32+32+32+32, 2 x 8k2, high bias). The first build fills in only what the file names state, and leaves the rest blank. Open it in Excel or any editor, save as CSV, then run the build command again (the page reads the table from `manifest.json`, which the build writes). In a column that shows text, a cell can also hold the text itself, such as `0.047uf`, instead of `1` or `0`.
- Row order: to reorder the recordings inside a section, move the rows up or down in `features.csv` and rebuild. Keep each row's `id` cell as it is, since that is how rows are matched. Moving a row to a different section isn't possible, because sections come from the file names. A recording that is missing from the file is added back at the end of its section.
- `appendix.md`: the write-up shown below the table. `##` makes a section heading, `###` a smaller one, `- ` a bullet. Plain text, `**bold**`, `*italics*` and `[links](https://...)` work. Each column heading in the table links to the section with the matching `{#ap-...}` tag, so keep those tags. Text inside `<!-- -->` is not shown.

### Check/x or text, per column

Each column of the table can show a check or x, or the setting itself as text. The `columns` section of `docs/settings.json` controls this, one entry per column:

- `mode`: `"check"` or `"text"`.
- `heading`: the column title in check mode (for example "Volume 8 (not 5)").
- `text_heading`: the column title in text mode (for example "Volume").
- `labels`: the two pieces of text, `[text for 1, text for 0]`, for example `["split", "shared"]`.

"V1 cathode" and "Post-PI coupling cap" start out in text mode. Every other either/or column already has its text and headings filled in, so switching it is one edit: change `"mode"` to `"text"` and rebuild. Your `features.csv` values don't change. Columns that are plain on/off (original recording, attenuation, lead tone stack, 18 watt, channels) stay check/x.

## Loop region

The loop has two edge handles on the waveform. Drag either one with the mouse, or press Tab to focus one and use the left and right arrow keys (0.05 s per press, Shift for 0.5 s; Home on the start handle and End on the end handle go to the clip edges). `[` and `]` still set the start and end at the playhead. The loop can't be shorter than 0.1 s. When you change the loop while it plays, the playhead jumps to the loop start if it was before it.

## Switches in index.html

At the top of the script in `index.html`:

- `ENABLED_MODES = ['compare']`: add `'blind'` and `'abx'` to bring those tabs back.
- `SHOW_PAIRS = false`: the list of one-knob-different comparisons.
- `FADE_ON_SWITCH = true`: a 6 ms fade when switching between settings. `false` makes it an instant cut, which can click on some material.
- `ROTATED_HEADERS = true`: table headings turned sideways. `false` shows normal horizontal text wrapped over several lines, and the table scrolls sideways with the first two columns pinned.
- `MATCH_LEVELS_DEFAULT = true`: whether level matching starts on.

## Hidden parts

Blind and ABX modes and the one-knob-different list are built but switched off (see the switches above).

## What the script does to your audio

Nothing. The audio files are never edited. mp3 and FLAC are copied byte for byte (WAV and AIFF are converted to lossless FLAC, or kept as they are with `--format copy`). The script only measures each file and writes numbers into `manifest.json`: loudness (LUFS and peak) and timing offset. The page uses the loudness numbers only while "Match levels" is on, and the timing offsets only if you built with `--apply-alignment`. Set `MATCH_LEVELS_DEFAULT = false` at the top of the script in `index.html` to start with matching off. While playing, the page adds a 6 ms fade when you switch (`FADE_ON_SWITCH`) and a 10 ms fade on start and stop, to avoid clicks.

## Notes

- Levels are matched per riff to the same loudness (LUFS), capped so nothing peaks above -1.5 dBFS. The page has a Match levels toggle.
- Timing between files in a riff is measured to about 1 ms and reported. Nothing is shifted unless you run with `--apply-alignment`.
- Audio is copied as is (mp3 stays mp3). WAV or AIFF sources are converted to FLAC. If you still have the Cubase project, export WAV and point the script at that.
- ABX and blind results are stored in each visitor's own browser. There is no server, so nothing is collected centrally.