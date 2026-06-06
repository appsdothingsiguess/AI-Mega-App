"""Fix double-brace JSX in SettingsModal.tsx."""
import re
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "web" / "src" / "components" / "SettingsModal.tsx"
text = p.read_text(encoding="utf-8")

replacements = [
    ("{{error &&", "{error &&"),
    ("{{loadMessage &&", "{loadMessage &&"),
    ("{{serverNote &&", "{serverNote &&"),
    ("{{lanUrls.length", "{lanUrls.length"),
    ("{{modelsError &&", "{modelsError &&"),
    ("{{llmModels.length", "{llmModels.length"),
    ("{{llmModels.map", "{llmModels.map"),
    ("{{llmModels\n                  .filter", "{llmModels\n                  .filter"),
    ("{{selectedModel &&", "{selectedModel &&"),
    ("{{loadingModelKey ===", "{loadingModelKey ==="),
    ("onClick={{onClose}}", "onClick={onClose}"),
    ("onClick={{refreshModels}}", "onClick={refreshModels}"),
    ("onClick={{handleLoadModel}}", "onClick={handleLoadModel}"),
    ("onClick={{handleSave}}", "onClick={handleSave}"),
    ("onChange={{(e)", "onChange={(e)"),
    ("{{modelsLoading ? ", "{modelsLoading ? "),
    ("{{saving ? ", "{saving ? "),
]
for a, b in replacements:
    text = text.replace(a, b)

text = re.sub(r"value=\{\{(form|selectedKey)", r"value={\1", text)
text = re.sub(r"checked=\{\{(server|form)", r"checked={\1", text)
text = re.sub(r"disabled=\{\{(!selectedKey|modelsLoading)", r"disabled={\1", text)
text = re.sub(r">\{\{(\w+)\}\}<", r">{\\1}<", text)
text = re.sub(r"</(\w+)>\}\}\)", r"</\1>)}", text)
text = re.sub(r"</(\w+)>\}\}", r"</\1>}", text)
text = text.replace("))}}", "))}")
text = text.replace(")}}", ")}")
text = text.replace("{{ ...styles", "{ ...styles")
text = text.replace("{{ ...styles.saveBtn", "{ ...styles.saveBtn")

p.write_text(text, encoding="utf-8")
print("fixed", p)
