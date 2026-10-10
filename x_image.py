"""
x_image.py - makes a picture card for an X or LinkedIn post (1200 x 675, the
size X shows in full). Used only by telegram_bot_x.py.

The card shows your one-line opinion in large type, the question under it, and
your handle. It is drawn locally with Pillow: free, instant, no AI image service.

Needs:  python -m pip install pillow

Settings (.env, optional)
  X_HANDLE       shown at the bottom of the card, e.g. @Me_with_a_view
  CARD_LABEL     small label at the top, default OPINION
"""
import os
import textwrap
import time

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

HANDLE = os.environ.get("X_HANDLE", "").strip()
LABEL = os.environ.get("CARD_LABEL", "OPINION").strip().upper()
OUT_DIR = os.path.join(SCRIPT_DIR, "x_cards")
W, H = 1200, 675

# Colours: deep navy, a warm accent, white and soft grey text
BG_TOP, BG_BOTTOM = (12, 24, 48), (24, 48, 84)
ACCENT = (255, 153, 51)
WHITE, SOFT = (255, 255, 255), (190, 205, 225)

FONT_CANDIDATES = {
    True: [  # bold
        r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ],
    False: [
        r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ],
}


def _font(size, bold=False):
    for path in FONT_CANDIDATES[bold]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)   # Pillow 10.1+
    except TypeError:
        return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    """Wrap text to fit max_width pixels."""
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = (line + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def _fit(draw, text, max_width, max_height, start, minimum, bold, spacing=1.18):
    """Largest font size (start..minimum) at which text fits the box."""
    for size in range(start, minimum - 1, -4):
        f = _font(size, bold)
        lines = _wrap(draw, text, f, max_width)
        if len(lines) * size * spacing <= max_height:
            return f, lines, size
    f = _font(minimum, bold)
    lines = _wrap(draw, text, f, max_width)
    max_lines = max(1, int(max_height // (minimum * spacing)))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;: ") + "..."
    return f, lines, minimum


def make_card(take, question, path=None):
    """Draws the card and returns the PNG file path."""
    img = Image.new("RGB", (W, H), BG_TOP)
    d = ImageDraw.Draw(img)
    for y in range(H):   # vertical gradient
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))

    margin = 80
    width = W - 2 * margin
    d.rectangle([0, 0, 14, H], fill=ACCENT)          # accent bar on the left

    # Label
    y = 64
    if LABEL:
        lf = _font(26, bold=True)
        d.text((margin, y), LABEL, font=lf, fill=ACCENT)
        y += 56

    # The take, large
    take = (take or "").strip() or question
    tf, tlines, tsize = _fit(d, f"\u201c{take}\u201d", width, 300, 64, 36, bold=True)
    for line in tlines:
        d.text((margin, y), line, font=tf, fill=WHITE)
        y += int(tsize * 1.18)

    # The question, smaller
    if question and question.strip() != take:
        y += 28
        qf, qlines, qsize = _fit(d, question.strip(), width, H - y - 110, 34, 24, bold=False)
        for line in qlines:
            d.text((margin, y), line, font=qf, fill=SOFT)
            y += int(qsize * 1.25)

    # Footer
    ff = _font(26, bold=True)
    d.line([(margin, H - 84), (W - margin, H - 84)], fill=(70, 95, 130), width=2)
    if HANDLE:
        d.text((margin, H - 66), HANDLE, font=ff, fill=WHITE)
    tag = "What's your take?"
    d.text((W - margin - d.textlength(tag, font=ff), H - 66), tag, font=ff, fill=ACCENT)

    if not path:
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, time.strftime("card_%Y%m%d_%H%M%S.png"))
    img.save(path, "PNG")
    return path


if __name__ == "__main__":
    # Quick check:  python x_image.py   (writes x_cards/sample_card.png)
    os.makedirs(OUT_DIR, exist_ok=True)
    p = make_card("Borrowing to pay salaries is a slow-motion trap, not a policy.",
                  "If emoluments are opex, is borrowing to pay salaries ever sustainable?",
                  os.path.join(OUT_DIR, "sample_card.png"))
    print("Saved", p)
