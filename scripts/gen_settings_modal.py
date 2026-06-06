"""Write SettingsModal.tsx (logic head + JSX tail)."""
from pathlib import Path

from settings_modal_tail import tail

p = Path(__file__).resolve().parent.parent / "web" / "src" / "components" / "SettingsModal.tsx"
head = p.read_text(encoding="utf-8").split("  return (")[0]
p.write_text(head + tail(), encoding="utf-8")
print("Wrote", p, "bytes", p.stat().st_size)
