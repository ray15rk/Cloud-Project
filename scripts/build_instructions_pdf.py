#!/usr/bin/env python3
"""Build instructions.pdf from readme.md using Headless Chrome."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
import markdown

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "readme.md"
PDF_OUT = ROOT / "instructions.pdf"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

if not CHROME.exists():
    print(f"Error: Chrome executable not found at {CHROME}")
    sys.exit(1)

with open(README, "r", encoding="utf-8") as f:
    md_content = f.read()

html_body = markdown.markdown(
    md_content,
    extensions=["tables", "fenced_code", "codehilite", "toc"]
)

styled_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Relay - Instructions & Documentation</title>
<style>
  @page {{
    margin: 18mm 16mm 18mm 16mm;
    size: A4 portrait;
    @bottom-right {{
      content: counter(page);
    }}
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.55;
    color: #1f2937;
    margin: 0;
    padding: 0;
  }}
  h1 {{
    font-size: 20pt;
    font-weight: 700;
    color: #111827;
    border-bottom: 2px solid #e5e7eb;
    padding-bottom: 8px;
    margin-top: 0;
  }}
  h2 {{
    font-size: 14pt;
    font-weight: 600;
    color: #1f2937;
    border-bottom: 1px solid #e5e7eb;
    padding-bottom: 5px;
    margin-top: 22px;
    page-break-after: avoid;
  }}
  h3 {{
    font-size: 12pt;
    font-weight: 600;
    color: #374151;
    margin-top: 16px;
    page-break-after: avoid;
  }}
  h4 {{
    font-size: 11pt;
    font-weight: 600;
    color: #4b5563;
    margin-top: 12px;
    page-break-after: avoid;
  }}
  p, li {{
    color: #374151;
  }}
  code {{
    font-family: Consolas, "Liberation Mono", Menlo, Courier, monospace;
    font-size: 9pt;
    background: #f3f4f6;
    padding: 1px 4px;
    border-radius: 3px;
    color: #0f172a;
  }}
  pre {{
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 10px 14px;
    overflow-x: auto;
    page-break-inside: avoid;
  }}
  pre code {{
    background: transparent;
    padding: 0;
    font-size: 8.5pt;
    line-height: 1.45;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 14px 0;
    font-size: 9.5pt;
    page-break-inside: avoid;
  }}
  th, td {{
    border: 1px solid #d1d5db;
    padding: 6px 10px;
    text-align: left;
    vertical-align: top;
  }}
  th {{
    background: #f9fafb;
    font-weight: 600;
    color: #111827;
  }}
  tr:nth-child(even) {{
    background: #f9fafb;
  }}
  blockquote {{
    border-left: 4px solid #3b82f6;
    margin: 12px 0;
    padding: 6px 14px;
    background: #eff6ff;
    color: #1e40af;
    border-radius: 0 4px 4px 0;
    page-break-inside: avoid;
  }}
  blockquote p {{
    margin: 0;
    color: #1e40af;
  }}
  hr {{
    border: 0;
    border-top: 1px solid #e5e7eb;
    margin: 20px 0;
  }}
</style>
</head>
<body>
{html_body}
</body>
</html>
"""

with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as temp_file:
    temp_file.write(styled_html)
    temp_path = temp_file.name

try:
    print(f"Generating PDF via Chrome: {PDF_OUT}")
    cmd = [
        str(CHROME),
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-software-rasterizer",
        f"--print-to-pdf={str(PDF_OUT)}",
        f"file:///{temp_path.replace(os.sep, '/')}"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print("Chrome error:", result.stderr)
        sys.exit(result.returncode)
    size = os.path.getsize(PDF_OUT)
    print(f"Successfully generated {PDF_OUT} ({size:,} bytes)")
finally:
    if os.path.exists(temp_path):
        os.remove(temp_path)
