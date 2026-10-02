"""Build Lattice's self-hosted web fonts (design-direction §4.1).

Inputs: three pinned Fontsource packages (SIL OFL 1.1), verified against their registry
integrity hashes. Outputs, all committed:

- public/fonts/<family>-<subset>[-<weight>].<hash>.woff2 -- variable axes limited to the
  ranges the type scale uses; Latin and Latin Extended as separate files.
- public/fonts/<family>.LICENSE.txt -- the OFL text travels with the fonts it covers.
- src/styles/fonts.css -- one @font-face family per voice. The Latin Extended face carries
  a unicode-range, so it downloads only for a page that contains such characters. Each
  voice also gets a metric-matched fallback face (Georgia / Arial / Courier New with
  size-adjust and ascent/descent overrides), so text doesn't shift when the web font
  arrives.
- src/lib/fonts.gen.ts -- the Latin files the root layout preloads.

File names carry a content hash, so /fonts/* is served with an immutable cache policy.
The build itself never touches the network: these files are the only font source.

Run from apps/web on macOS (it reads the fallback metrics from the system fonts):
    uvx --with brotli --from fonttools python scripts/build-fonts.py
"""

from __future__ import annotations

import base64
import hashlib
import io
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

WEB = Path(__file__).resolve().parent.parent
PUBLIC = WEB / "public" / "fonts"
CSS = WEB / "src" / "styles" / "fonts.css"
PRELOADS = WEB / "src" / "lib" / "fonts.gen.ts"
SYSTEM = Path("/System/Library/Fonts/Supplemental")

PACKAGES = {
    "newsreader": (
        "https://registry.npmjs.org/@fontsource-variable/newsreader/-/newsreader-5.3.0.tgz",
        "sha512-rrzYi43qMpbzwuFtf9OkWH8sxAPVPcQQQEwXpPtwaKYeJ8yVg5aLs5kawmo1f2Q1t1M38TLmEKCkGVDsYwgdFw==",
    ),
    "schibsted-grotesk": (
        "https://registry.npmjs.org/@fontsource-variable/schibsted-grotesk/-/schibsted-grotesk-5.3.0.tgz",
        "sha512-ndS6H/KICWANchlHF3q4UdZD+xrZ3Ji0rUobIuXlgvDUzDfqZDsVuXPXZb636nJZMHE9zZ9Fmwgqazm8hMB50Q==",
    ),
    "ibm-plex-mono": (
        "https://registry.npmjs.org/@fontsource/ibm-plex-mono/-/ibm-plex-mono-5.3.0.tgz",
        "sha512-eTgnZjZEGk1QtD3ZstF+Vclo2HLAni8YMy34/DxllwZvyz1lR/1RF/xTiAquOBO7MvqBx8D2Ig2WCPMVfdZu7Q==",
    ),
}

# Fontsource's own subset ranges (from the packages' CSS).
RANGES = {
    "latin": "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, "
    "U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD",
    "latin-ext": "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, "
    "U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF",
}
SUBSETS = ("latin", "latin-ext")


@dataclass(frozen=True)
class Face:
    family: str  # CSS family name
    package: str
    source: str  # file inside package/files, with {subset}
    out: str  # output stem, with {subset}
    weight: str  # CSS font-weight (a range for variable faces)
    limits: dict[str, tuple[float, float]] | None
    preload: bool


FACES = [
    Face("Lattice Record", "newsreader", "newsreader-{subset}-opsz-normal.woff2", "newsreader-{subset}", "400 600", {"wght": (400, 600), "opsz": (12, 72)}, True),
    Face("Lattice Interface", "schibsted-grotesk", "schibsted-grotesk-{subset}-wght-normal.woff2", "schibsted-grotesk-{subset}", "400 700", {"wght": (400, 700)}, True),
    Face("Lattice Ledger", "ibm-plex-mono", "ibm-plex-mono-{subset}-400-normal.woff2", "ibm-plex-mono-{subset}-400", "400", None, True),
    Face("Lattice Ledger", "ibm-plex-mono", "ibm-plex-mono-{subset}-500-normal.woff2", "ibm-plex-mono-{subset}-500", "500", None, False),
]

# Each voice's fallback: the local font whose metrics are matched, by family.
FALLBACKS = {
    "Lattice Record": ("Georgia", SYSTEM / "Georgia.ttf"),
    "Lattice Interface": ("Arial", SYSTEM / "Arial.ttf"),
    "Lattice Ledger": ("Courier New", SYSTEM / "Courier New.ttf"),
}

# Letter frequencies of English text (the same idea as next/font and capsize): the average
# advance of running text, not of the whole alphabet.
SAMPLE = "aaabcdeeeefghiijklmnnoopqrrssttuvwxyz      "


def fetch(url: str, integrity: str) -> tarfile.TarFile:
    data = urllib.request.urlopen(url, timeout=60).read()  # noqa: S310 - fixed https URLs
    algo, expected = integrity.split("-", 1)
    if base64.b64encode(hashlib.new(algo, data).digest()).decode() != expected:
        raise SystemExit(f"integrity mismatch for {url}")
    return tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")


def average_width(font: TTFont) -> float:
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    widths = [hmtx[cmap[ord(c)]][0] for c in SAMPLE if ord(c) in cmap]
    return sum(widths) / len(widths) / font["head"].unitsPerEm


def fallback_face(family: str, web: TTFont) -> str:
    local_name, path = FALLBACKS[family]
    local = TTFont(path)
    size_adjust = average_width(web) / average_width(local)
    upm = web["head"].unitsPerEm
    hhea = web["hhea"]
    ascent = hhea.ascent / (upm * size_adjust)
    descent = abs(hhea.descent) / (upm * size_adjust)
    line_gap = hhea.lineGap / (upm * size_adjust)
    return (
        "@font-face {\n"
        f'  font-family: "{family} Fallback";\n'
        f'  src: local("{local_name}");\n'
        f"  size-adjust: {size_adjust * 100:.2f}%;\n"
        f"  ascent-override: {ascent * 100:.2f}%;\n"
        f"  descent-override: {descent * 100:.2f}%;\n"
        f"  line-gap-override: {line_gap * 100:.2f}%;\n"
        "}\n"
    )


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    for old in PUBLIC.glob("*.woff2"):
        old.unlink()
    archives = {name: fetch(url, integrity) for name, (url, integrity) in PACKAGES.items()}
    for name, archive in archives.items():
        licence = archive.extractfile("package/LICENSE")
        assert licence is not None
        (PUBLIC / f"{name}.LICENSE.txt").write_bytes(licence.read())

    faces_css: list[str] = []
    fallbacks: dict[str, str] = {}
    preloads: list[str] = []
    total = {subset: 0 for subset in SUBSETS}
    for face in FACES:
        for subset in SUBSETS:
            member = archives[face.package].extractfile(f"package/files/{face.source.format(subset=subset)}")
            assert member is not None
            font = TTFont(io.BytesIO(member.read()))
            if face.limits:
                font = instancer.instantiateVariableFont(font, face.limits)
            font.flavor = "woff2"
            buffer = io.BytesIO()
            font.save(buffer)
            data = buffer.getvalue()
            name = f"{face.out.format(subset=subset)}.{hashlib.sha256(data).hexdigest()[:10]}.woff2"
            (PUBLIC / name).write_bytes(data)
            total[subset] += len(data)
            print(f"{len(data):>7}  {name}")
            faces_css.append(
                f"/* {face.package} {subset} */\n"
                "@font-face {\n"
                f'  font-family: "{face.family}";\n'
                f'  src: url("/fonts/{name}") format("woff2");\n'
                f"  font-weight: {face.weight};\n"
                "  font-style: normal;\n"
                "  font-display: swap;\n"
                f"  unicode-range: {RANGES[subset]};\n"
                "}\n"
            )
            if subset == "latin":
                if face.preload:
                    preloads.append(f"/fonts/{name}")
                if face.family not in fallbacks:
                    fallbacks[face.family] = fallback_face(face.family, font)

    CSS.write_text(
        "/*\n * Generated by scripts/build-fonts.py -- do not edit. Lattice's three voices\n"
        " * (design-direction §4.1), self-hosted; fallback faces are metric-matched.\n */\n\n"
        + "\n".join(faces_css)
        + "\n"
        + "\n".join(fallbacks.values())
    )
    PRELOADS.write_text(
        "// Generated by scripts/build-fonts.py -- do not edit.\n"
        "/** The Latin font files every page needs first; the root layout preloads them. */\n"
        "export const FONT_PRELOADS: readonly string[] = [\n"
        + "".join(f'  "{p}",\n' for p in preloads)
        + "];\n"
    )
    for subset, size in total.items():
        print(f"{size:>7}  total {subset}")


if __name__ == "__main__":
    main()
