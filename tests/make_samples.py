"""Make sample PDFs for end-to-end testing: a normal text PDF and a 'scanned' copy (images only).

Run inside the app container:  python tests/make_samples.py /data/samples
"""
import sys
from pathlib import Path

import pypdfium2
from PIL import Image
from weasyprint import HTML

HTML_DOC = """<!doctype html><html><head><style>
@page { size: A4; margin: 2cm;
  @top-right { content: "Urban Water Report 2026"; font-size: 8pt; color: #777; }
  @bottom-center { content: "Page " counter(page); font-size: 8pt; color: #777; } }
body { font-family: "DejaVu Serif", serif; font-size: 11pt; line-height: 1.45; }
.cols { columns: 2; column-gap: 1cm; }
table { border-collapse: collapse; margin: 8pt 0; } td, th { border: 1px solid #444; padding: 3pt 6pt; }
</style></head><body>
<h1>Managing Urban Floods in Bangkok</h1>
<h2>1. Abstract</h2>
<p>Bangkok has faced recurring floods for decades, but the problem has grown more complex as the city
has expanded. Many canals that once carried rainwater to the Chao Phraya River have been filled in to make
room for roads and housing, while land subsidence in some districts has lowered the ground by more than a
metre since the 1970s.</p>
<h2>2. Approaches</h2>
<p>City planners are experimenting with several approaches:</p>
<ul>
<li>Underground retention tanks that store runoff during storms.</li>
<li>Parks redesigned to act as sponges, with lawns below street level.</li>
<li>Upgraded pumping stations and cleared drains before each rainy season.</li>
</ul>
<p>Table 1 lists the estimated cost of each measure.</p>
<table>
<tr><th>Measure</th><th>Capacity</th><th>Cost (million THB)</th></tr>
<tr><td>Retention tank</td><td>50,000 m³</td><td>1,200</td></tr>
<tr><td>Sponge park</td><td>12,000 m³</td><td>340</td></tr>
<tr><td>Pumping station upgrade</td><td>25 m³/s</td><td>860</td></tr>
</table>
<h2 style="break-before: page">3. Discussion</h2>
<div class="cols">
<p>Experts warn that infrastructure alone will not solve the problem. Rising sea levels in the Gulf of
Thailand could make it harder for water to drain out of the city, and rapid development in surrounding
provinces continues to reduce the amount of open land available to absorb rainfall.</p>
<p>Some researchers argue that the city needs stricter rules on groundwater extraction, better coordination
between national and local agencies, and early-warning systems that give residents time to protect their
homes and businesses.</p>
<p>For residents, the most immediate concern is often practical. Shop owners in low-lying areas raise their
goods onto shelves when storms are forecast, and many families keep sandbags ready at the start of the
rainy season.</p>
</div>
</body></html>"""


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    text_pdf = out / "sample-text.pdf"
    HTML(string=HTML_DOC).write_pdf(text_pdf)

    # "Scan": render each page to a grey image with a slight skew, then save the images as a PDF.
    pages = []
    for page in pypdfium2.PdfDocument(str(text_pdf)):
        img = page.render(scale=200 / 72).to_pil().convert("L")
        pages.append(img.rotate(0.6, expand=False, fillcolor=255, resample=Image.BICUBIC))
    pages[0].save(out / "sample-scanned.pdf", save_all=True, append_images=pages[1:], resolution=200)
    print("wrote", text_pdf, "and", out / "sample-scanned.pdf")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples")
