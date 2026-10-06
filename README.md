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
- `featured`: your own comparison presets, `[{"a": "<setting id>", "b": "<setting id>", "title": "Does 10k matter?"}]`. When this is empty the page lists every pair that differs by exactly one knob.

Existing entries in `settings.json` are never overwritten. Delete the file to regenerate it.

## The feature table and the appendix

Both live in the `docs` folder and are never overwritten by a rebuild.

- `features.csv`: one row per recording, one column per setting in the table. Put `1` for a check, `0` for an x, and leave a cell blank to show a `?`. For either/or columns, `1` means the first option in the heading (volume 8, 100pf, split cathode, 0.022uf, 67-spec, 0.1uf, 48+32+32+32+32, 2 x 8k2, high bias). The first build fills in only what the file names state, and leaves the rest blank. Open it in Excel or any editor, save as CSV, then refresh the page (no rebuild needed).
- `appendix.md`: the write-up shown below the table. `##` makes a section heading, `###` a smaller one, `- ` a bullet. Plain text, `**bold**`, `*italics*` and `[links](https://...)` work. Each column heading in the table links to the section with the matching `{#ap-...}` tag, so keep those tags. Text inside `<!-- -->` is not shown.

## Hidden parts

Blind and ABX modes and the one-knob-different list are built but switched off. At the top of the script in `index.html`: `ENABLED_MODES = ['compare']` (add `'blind'` and `'abx'` to bring the tabs back) and `SHOW_PAIRS = false`.

## Notes

- Levels are matched per riff to the same loudness (LUFS), capped so nothing peaks above -1.5 dBFS. The page has a Match levels toggle.
- Timing between files in a riff is checked to about 1 ms. Offsets under 2 ms are treated as aligned, larger ones are corrected in the manifest.
- Audio is copied as is (mp3 stays mp3). WAV or AIFF sources are converted to FLAC. If you still have the Cubase project, export WAV and point the script at that.
- ABX and blind results are stored in each visitor's own browser. There is no server, so nothing is collected centrally.