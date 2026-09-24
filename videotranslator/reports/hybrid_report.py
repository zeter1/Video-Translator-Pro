"""Generate simple hybrid translation report."""
from html import escape
from pathlib import Path
from videotranslator.core.io import atomic_write_text

def create_report(path, stats, title="Translation report"):
    rows="".join(f"<tr><td>{escape(str(k))}</td><td>{escape(str(v))}</td></tr>" for k,v in stats.items())
    html=f"<html><body><h1>{escape(title)}</h1><table>{rows}</table></body></html>"
    atomic_write_text(Path(path), html)
    return path
