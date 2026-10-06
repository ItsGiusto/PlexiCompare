# Plexi A/B site

Put `build_site.py` and `index.html` in the same folder, then:

    pip install numpy scipy
    python build_site.py "C:\Users\justin\Dropbox\CubaseFiles\plexitest9-26\Mixdown" site --serve

ffmpeg must be on PATH (`winget install Gyan.FFmpeg`). The `site` folder is the finished page. Upload it to any static host (GitHub Pages, Netlify, Cloudflare Pages, your own site). It will not play from `file://`.

## Editing names and presets

The first run writes `site/settings.json`. Edit it, then run the same command again (it takes about 30 seconds):

- `title`, `intro`: the page heading and blurb.
- `settings`: the `label` and `detail` shown for each setup. Add a `note` for a tooltip.
- `riffs`: the title shown for each riff. Old names are tidied for display ("bridgt" becomes bridge, "neck bridge shuffle" becomes bridge shuffle, "shuffle single notes" becomes single notes), so renaming your files is optional.
- `featured`: your own comparison presets, `[{"a": "<setting id>", "b": "<setting id>", "title": "Does 10k matter?"}]`. When this is empty the page lists every pair that differs by exactly one knob.

Existing entries in `settings.json` are never overwritten. Delete the file to regenerate it.

## Notes

- Levels are matched per riff to the same loudness (LUFS), capped so nothing peaks above -1.5 dBFS. The page has a Match levels toggle.
- Timing between files in a riff is checked to about 1 ms. Offsets under 2 ms are treated as aligned, larger ones are corrected in the manifest.
- Audio is copied as is (mp3 stays mp3). WAV or AIFF sources are converted to FLAC. If you still have the Cubase project, export WAV and point the script at that.
- ABX and blind results are stored in each visitor's own browser. There is no server, so nothing is collected centrally.
