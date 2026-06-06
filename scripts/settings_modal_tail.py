# Tail JSX for SettingsModal (concatenated by gen_settings_modal.py)
D = "d" + "iv"

def tail():
    o = f"<{D}"
    c = f"</{D}>"
    return f"""
  return (
    {o} style={{styles.overlay}} onClick={{(e) => e.target === e.currentTarget && onClose()}}>
      {o} style={{styles.modal}}>
        {o} style={{styles.modalHeader}}>
          <span style={{styles.modalTitle}}>Settings</span>
          <button style={{styles.closeBtn}} onClick={{onClose}}>×</button>
        {c}
        {{error && {o} style={{styles.errorBanner}}>{{error}}{c}}}
        {{loadMessage && {o} style={{styles.successBanner}}>{{loadMessage}}{c}}}
        {{serverNote && {o} style={{styles.warnBanner}}>{{serverNote}}{c}}}
        {o} style={{styles.body}}>
          <section>
            {o} style={{styles.sectionTitle}}>LM Studio connection{c}
            <Field label="Server URL">
              <input style={{styles.input}} value={{form.lmstudio_base_url ?? ""}} onChange={{(e) => set("lmstudio_base_url", e.target.value)}} placeholder="http://localhost:1234" />
              {{lanUrls.length > 0 && (
                {o} style={{styles.hint}}>
                  LAN URLs (for other devices):
                  {o} style={{{{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}}}>
                    {{lanUrls.map((url) => (
                      <button key={{url}} type="button" style={{styles.urlChip}} onClick={{() => set("lmstudio_base_url", url)}}>{{url}}</button>
                    ))}}
                  {c}
                {c}
              )}}
            </Field>
            <Field label="Serve on local network">
              <label style={{styles.checkLabel}}>
                <input type="checkbox" checked={{server?.serve_on_local_network ?? false}} disabled={{networkSaving}} onChange={{(e) => handleNetworkToggle(e.target.checked)}} style={{{{ accentColor: "var(--accent)" }}}} />
                <span style={{{{ color: "var(--text-muted)", fontSize: 12 }}}}>Allow LAN access to LM Studio</span>
              </label>
              {o} style={{styles.hint}}>Restart LM Studio server after toggling.{c}
            </Field>
            <Field label="API mode">
              <select style={{styles.select}} value={{form.lmstudio_mode ?? "llm"}} onChange={{(e) => set("lmstudio_mode", e.target.value)}}>
                <option value="llm">llm (native v1 — supports load)</option>
                <option value="rest">rest (OpenAI-compatible)</option>
              </select>
            </Field>
          </section>
          <section>
            {o} style={{styles.sectionTitleRow}}>
              <span style={{styles.sectionTitleInline}}>Model</span>
              <button type="button" style={{styles.refreshBtn}} onClick={{refreshModels}} disabled={{modelsLoading}}>{{modelsLoading ? "…" : "↻"}}</button>
            {c}
            {{modelsError && {o} style={{styles.inlineError}}>{{modelsError}}{c}}}
            <Field label="Chat model">
              <select style={{styles.select}} value={{selectedKey}} onChange={{(e) => set("lmstudio_model", e.target.value)}} disabled={{modelsLoading || llmModels.length === 0}}>
                {{llmModels.length === 0 && <option value={{selectedKey}}>{{selectedKey || "(no models)"}}</option>}}
                {{llmModels.map((m) => (
                  <option key={{m.key}} value={{m.key}}>{{m.loaded ? "● " : "○ "}}{{m.display_name}}</option>
                ))}}
              </select>
              {o} style={{styles.modelActions}}>
                <button type="button" style={{styles.loadBtn}} disabled={{!selectedKey || loadingModelKey !== null}} onClick={{handleLoadModel}}>
                  {{loadingModelKey === selectedKey ? "Loading…" : selectedModel?.loaded ? "Reload model" : "Load model"}}
                </button>
                {{selectedModel && <span style={{{{ fontSize: 11, color: selectedModel.loaded ? "var(--success)" : "var(--warning)" }}}}>{{selectedModel.loaded ? "Loaded" : "Not loaded"}}{c}</span>}}
              {c}
            </Field>
            <Field label="Vision model">
              <select style={{styles.select}} value={{form.lmstudio_vision_model ?? ""}} onChange={{(e) => set("lmstudio_vision_model", e.target.value || undefined)}}>
                <option value="">(same as chat model)</option>
                {{llmModels.filter((m) => m.vision).map((m) => <option key={{m.key}} value={{m.key}}>{{m.display_name}}</option>)}}
              </select>
            </Field>
            <Field label="Vision support">
              <select style={{styles.select}} value={{form.lmstudio_supports_vision ?? "auto"}} onChange={{(e) => set("lmstudio_supports_vision", e.target.value)}}>
                <option value="auto">auto</option><option value="true">on</option><option value="false">off</option>
              </select>
            </Field>
          </section>
          <section>
            {o} style={{styles.sectionTitle}}>RAG{c}
            <Field label="Top-K"><input style={{{{ ...styles.input, width: 80 }}}} type="number" value={{form.rag_top_k ?? 5}} onChange={{(e) => set("rag_top_k", Number(e.target.value))}} /></Field>
            <Field label="Chunk size"><input style={{{{ ...styles.input, width: 100 }}}} type="number" value={{form.chunk_size ?? 800}} onChange={{(e) => set("chunk_size", Number(e.target.value))}} /></Field>
            <Field label="Overlap"><input style={{{{ ...styles.input, width: 100 }}}} type="number" value={{form.chunk_overlap ?? 120}} onChange={{(e) => set("chunk_overlap", Number(e.target.value))}} /></Field>
          </section>
          <section>
            {o} style={{styles.sectionTitle}}>Advanced{c}
            <Field label="Debug"><label style={{styles.checkLabel}}><input type="checkbox" checked={{form.debug_prompts ?? false}} onChange={{(e) => set("debug_prompts", e.target.checked)}} /> Debug prompts</label></Field>
          </section>
        {c}
        {o} style={{styles.footer}}>
          <button style={{styles.cancelBtn}} onClick={{onClose}}>Close</button>
          <button style={{{{ ...styles.saveBtn, ...(saved ? styles.savedBtn : {{}}) }}}} onClick={{handleSave}} disabled={{saving}}>{{saving ? "Saving…" : saved ? "Saved" : "Save"}}</button>
        {c}
      {c}
    {c}
  );
}}

function Field({{ label, children }}: {{ label: string; children: React.ReactNode }}) {{
  return (
    {o} style={{{{ marginBottom: 12 }}}}>
      {o} style={{{{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}}}>{{label}}{c}
      {{children}}
    {c}
  );
}}

const styles: Record<string, React.CSSProperties> = {{
  overlay: {{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }},
  modal: {{ background: "var(--bg-panel)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", width: 520, maxWidth: "95vw", maxHeight: "90vh", display: "flex", flexDirection: "column", overflow: "hidden" }},
  modalHeader: {{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 16px", borderBottom: "1px solid var(--border)" }},
  modalTitle: {{ fontWeight: 600, fontSize: 14 }},
  closeBtn: {{ width: 24, height: 24, fontSize: 18, color: "var(--text-muted)" }},
  errorBanner: {{ margin: "8px 16px 0", padding: "6px 10px", background: "rgba(224,85,85,0.12)", color: "var(--danger)", fontSize: 12 }},
  successBanner: {{ margin: "8px 16px 0", padding: "6px 10px", background: "rgba(34,197,94,0.1)", color: "var(--success)", fontSize: 12 }},
  warnBanner: {{ margin: "8px 16px 0", padding: "6px 10px", background: "rgba(245,158,11,0.12)", color: "var(--warning)", fontSize: 12 }},
  body: {{ flex: 1, overflowY: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 24 }},
  sectionTitle: {{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "var(--accent)", marginBottom: 12, borderBottom: "1px solid var(--border)", paddingBottom: 4 }},
  sectionTitleRow: {{ display: "flex", justifyContent: "space-between", marginBottom: 12, borderBottom: "1px solid var(--border)", paddingBottom: 4 }},
  sectionTitleInline: {{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "var(--accent)" }},
  refreshBtn: {{ width: 28, height: 28, background: "var(--bg-hover)", borderRadius: 4 }},
  inlineError: {{ fontSize: 12, color: "var(--danger)", marginBottom: 8 }},
  input: {{ width: "100%", padding: "7px 10px", fontSize: 13 }},
  select: {{ width: "100%", padding: "7px 10px", fontSize: 13 }},
  checkLabel: {{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }},
  hint: {{ marginTop: 4, fontSize: 11, color: "var(--text-dim)" }},
  urlChip: {{ padding: "4px 8px", fontSize: 11, background: "var(--bg-hover)", color: "var(--accent)", fontFamily: "var(--font-mono)" }},
  modelActions: {{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, flexWrap: "wrap" }},
  loadBtn: {{ padding: "6px 14px", background: "var(--accent)", color: "#000", fontWeight: 600, fontSize: 12, borderRadius: 4 }},
  footer: {{ display: "flex", justifyContent: "flex-end", gap: 8, padding: 12, borderTop: "1px solid var(--border)" }},
  cancelBtn: {{ padding: "7px 16px", background: "var(--bg-hover)" }},
  saveBtn: {{ padding: "7px 20px", background: "var(--accent)", color: "#000", fontWeight: 600 }},
  savedBtn: {{ background: "var(--success)" }},
}};
"""
