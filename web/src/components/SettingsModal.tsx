import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getSettings,
  updateSettings,
  getOllamaModels,
  getTools,
  TIER_ALIASES,
  SettingsSnapshot,
  SettingsUpdate,
  IntentLabel,
  RoutingRule,
} from "../api/client";

const FALLBACK_ROUTER_TOOLS = [
  "web_search",
  "bash",
  "pdf_gen",
  "file_ops",
  "vision",
] as const;

interface Props {
  onClose: () => void;
  onSaved?: (snapshot: SettingsSnapshot) => void;
}

type TabId =
  | "general"
  | "models"
  | "router"
  | "embedding"
  | "search"
  | "infrastructure"
  | "logging";

const TABS: { id: TabId; label: string }[] = [
  { id: "general", label: "General" },
  { id: "models", label: "Models" },
  { id: "router", label: "Router" },
  { id: "embedding", label: "Embedding" },
  { id: "search", label: "Search" },
  { id: "infrastructure", label: "Infrastructure" },
  { id: "logging", label: "Logging / Debug" },
];

const INTENT_LABELS: { key: IntentLabel; label: string }[] = [
  { key: "general_chat", label: "General chat" },
  { key: "web_search", label: "Web search" },
  { key: "deep_research", label: "Deep research" },
  { key: "coding_basic", label: "Coding (basic)" },
  { key: "coding_advanced", label: "Coding (advanced)" },
  { key: "bash", label: "Bash" },
  { key: "pdf_gen", label: "PDF generation" },
  { key: "file_ops", label: "File operations" },
  { key: "vision", label: "Vision" },
  { key: "reasoning_medium", label: "Reasoning (medium)" },
  { key: "reasoning_heavy", label: "Reasoning (heavy)" },
];

const TOOL_INTENTS: IntentLabel[] = ["bash", "pdf_gen", "file_ops", "web_search"];

const NON_TOOL_INTENT_LABELS = INTENT_LABELS.filter(
  ({ key }) => !TOOL_INTENTS.includes(key),
);

const TIER_LABELS: { key: (typeof TIER_ALIASES)[number]; label: string }[] = [
  { key: "local/coding-light", label: "Programming - Light" },
  { key: "local/coding-medium", label: "Programming - Medium" },
  { key: "local/coding-heavy", label: "Programming - Heavy" },
  { key: "local/reasoning-medium", label: "Reasoning - Medium" },
  { key: "local/reasoning-heavy", label: "Reasoning - Heavy" },
  { key: "local/vision-light", label: "Vision - Light" },
  { key: "local/vision-medium", label: "Vision - Medium" },
  { key: "local/vision-heavy", label: "Vision - Heavy" },
  { key: "local/tool-calling-medium", label: "Tool calling - Medium" },
];

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"] as const;

const SEARCH_PROVIDERS = ["duckduckgo", "tavily"] as const;

function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** Classifier is stored as ollama/<tag>; UI lists bare Ollama tags. */
function stripOllamaPrefix(model: string): string {
  return model.startsWith("ollama/") ? model.slice("ollama/".length) : model;
}

function toOllamaClassifier(tag: string): string {
  const trimmed = tag.trim();
  if (!trimmed) return "";
  return trimmed.startsWith("ollama/") ? trimmed : `ollama/${trimmed}`;
}

function normalizePatterns(patterns: string[]): string[] {
  return patterns.map((p) => p.trim()).filter(Boolean);
}

function validateForm(form: SettingsSnapshot): string | null {
  if (form.embedding.max_tokens < 1) return "Embedding max tokens must be at least 1";
  if (form.rag.chunk_size < 1) return "RAG chunk size must be at least 1";
  if (form.rag.top_k < 1) return "RAG top-k must be at least 1";
  if (form.rag.chunk_overlap_ratio < 0 || form.rag.chunk_overlap_ratio > 1) {
    return "RAG chunk overlap ratio must be between 0 and 1";
  }
  if (form.health.classifier_timeout_s <= 0) {
    return "Classifier timeout must be greater than 0";
  }
  for (const rule of form.router.rules) {
    if (!normalizePatterns(rule.patterns).length) {
      return "Each routing rule needs at least one pattern";
    }
    if (!rule.intent.trim()) return "Each routing rule needs an intent";
  }
  return null;
}

export default function SettingsModal({ onClose, onSaved }: Props) {
  const [snap, setSnap] = useState<SettingsSnapshot | null>(null);
  const [form, setForm] = useState<SettingsSnapshot | null>(null);
  const [tab, setTab] = useState<TabId>("general");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [draftTavilyKey, setDraftTavilyKey] = useState("");
  const [draftOpencodeKey, setDraftOpencodeKey] = useState("");
  const [tiersExpanded, setTiersExpanded] = useState(true);
  const [assistantPromptExpanded, setAssistantPromptExpanded] = useState(false);
  const [classifierPromptExpanded, setClassifierPromptExpanded] = useState(false);
  const [ollamaModels, setOllamaModels] = useState<{ reachable: boolean; models: string[] }>({
    reachable: false,
    models: [],
  });
  const [ollamaLoading, setOllamaLoading] = useState(false);
  const [availableTools, setAvailableTools] = useState<string[]>([...FALLBACK_ROUTER_TOOLS]);
  const [toolsLoading, setToolsLoading] = useState(false);

  const ollamaTagOptions = useMemo(() => {
    const tags = new Set(ollamaModels.models);
    return Array.from(tags).sort();
  }, [ollamaModels.models]);

  const refreshOllamaModels = useCallback(() => {
    setOllamaLoading(true);
    getOllamaModels()
      .then((res) => setOllamaModels(res))
      .catch(() => setOllamaModels({ reachable: false, models: [] }))
      .finally(() => setOllamaLoading(false));
  }, []);

  const refreshTools = useCallback(() => {
    setToolsLoading(true);
    getTools()
      .then((res) => {
        const tools = res.tools?.length ? res.tools : [...FALLBACK_ROUTER_TOOLS];
        setAvailableTools(tools);
      })
      .catch(() => setAvailableTools([...FALLBACK_ROUTER_TOOLS]))
      .finally(() => setToolsLoading(false));
  }, []);

  useEffect(() => {
    getSettings()
      .then((s) => {
        setSnap(s);
        setForm(deepClone(s));
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load settings");
      });
    refreshOllamaModels();
    refreshTools();
  }, [refreshOllamaModels, refreshTools]);

  const modelAliases = useMemo(() => {
    if (!form) return [];
    const aliases = new Set(Object.values(form.models));
    Object.keys(form.ollama_model_names).forEach((a) => aliases.add(a));
    return Array.from(aliases).sort();
  }, [form]);

  const patch = useCallback(
    (updater: (current: SettingsSnapshot) => SettingsSnapshot) => {
      setForm((f) => (f ? updater(f) : f));
      setFieldError(null);
    },
    [],
  );

  const buildUpdate = (source: SettingsSnapshot): SettingsUpdate => {
    if (!snap) return {};
    const updates: SettingsUpdate = {};
    const sections = [
      "models",
      "ollama_model_names",
      "assistant",
      "vision",
      "router",
      "embedding",
      "search",
      "ollama",
      "opencode_go",
      "qdrant",
      "rag",
      "health",
      "logging",
      "debug",
    ] as const;
    for (const key of sections) {
      const next = source[key];
      const prev = snap[key];
      if (next === undefined) continue;
      if (JSON.stringify(prev) !== JSON.stringify(next)) {
        (updates as Record<string, unknown>)[key] = next;
      }
    }
    if (draftTavilyKey.trim()) {
      updates.search = {
        ...(updates.search ?? {}),
        tavily_api_key: draftTavilyKey.trim(),
      };
    }
    if (draftOpencodeKey.trim()) {
      updates.opencode_go = {
        ...(updates.opencode_go ?? {}),
        api_key: draftOpencodeKey.trim(),
      };
    }
    return updates;
  };

  const handleSave = async () => {
    if (!form) return;
    // Drop blank pattern lines before validate/save (kept during edit so Enter works).
    const cleanedForm: SettingsSnapshot = {
      ...form,
      router: {
        ...form.router,
        rules: form.router.rules.map((rule) => ({
          ...rule,
          patterns: normalizePatterns(rule.patterns),
        })),
      },
    };
    setForm(cleanedForm);
    const validationError = validateForm(cleanedForm);
    if (validationError) {
      setFieldError(validationError);
      return;
    }
    setSaving(true);
    setError(null);
    setFieldError(null);
    try {
      const updated = await updateSettings(buildUpdate(cleanedForm));
      setSnap(updated);
      setForm(deepClone(updated));
      setDraftTavilyKey("");
      setDraftOpencodeKey("");
      setSaved(true);
      onSaved?.(updated);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const updateRule = (index: number, rule: RoutingRule) => {
    patch((f) => {
      const rules = [...f.router.rules];
      rules[index] = rule;
      return { ...f, router: { ...f.router, rules } };
    });
  };

  const addRule = () => {
    patch((f) => ({
      ...f,
      router: {
        ...f.router,
        rules: [
          ...f.router.rules,
          { patterns: [""], intent: "general_chat", tools: [] },
        ],
      },
    }));
  };

  const removeRule = (index: number) => {
    patch((f) => ({
      ...f,
      router: {
        ...f.router,
        rules: f.router.rules.filter((_, i) => i !== index),
      },
    }));
  };

  if (!form) {
    return (
      <div style={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
        <div style={styles.modal}>
          <div style={styles.modalHeader}>
            <span style={styles.modalTitle}>Settings</span>
            <button style={styles.closeBtn} onClick={onClose} type="button">×</button>
          </div>
          <div style={{ padding: 24, color: "var(--text-muted)", fontSize: 13 }}>
            {error ?? "Loading settings…"}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div style={{ ...styles.modal, width: 640 }}>
        <div style={styles.modalHeader}>
          <span style={styles.modalTitle}>Settings</span>
          <button style={styles.closeBtn} onClick={onClose} type="button">×</button>
        </div>
        {(error || fieldError) && (
          <div style={styles.errorBanner}>{fieldError ?? error}</div>
        )}
        <div style={styles.tabBar}>
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              style={{
                ...styles.tabBtn,
                ...(tab === t.id ? styles.tabBtnActive : {}),
              }}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div style={styles.body}>
          {tab === "general" && (
            <section>
              <button
                type="button"
                style={styles.disclosureTitle}
                onClick={() => setAssistantPromptExpanded((v) => !v)}
                aria-expanded={assistantPromptExpanded}
              >
                <span aria-hidden="true">{assistantPromptExpanded ? "▾" : "▸"}</span>
                Default assistant prompt
              </button>
              {assistantPromptExpanded && (
                <>
                  <p style={styles.hint}>
                    Applied to every chat. Project instructions are appended below this.
                    Use {"{project_name}"} for the project name.
                  </p>
                  <textarea
                    style={styles.textarea}
                    value={form.assistant?.system_prompt ?? ""}
                    onChange={(e) =>
                      patch((f) => ({
                        ...f,
                        assistant: {
                          system_prompt: e.target.value,
                        },
                      }))
                    }
                    rows={10}
                  />
                </>
              )}
            </section>
          )}

          {tab === "models" && (
            <section>
              <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
                <button
                  type="button"
                  style={styles.secondaryBtn}
                  onClick={refreshOllamaModels}
                  disabled={ollamaLoading}
                >
                  {ollamaLoading ? "Refreshing…" : "Refresh from Ollama"}
                </button>
              </div>
              <button
                type="button"
                style={styles.disclosureTitle}
                onClick={() => setTiersExpanded((v) => !v)}
                aria-expanded={tiersExpanded}
              >
                <span aria-hidden="true">{tiersExpanded ? "▾" : "▸"}</span>
                Model tiers
              </button>
              {tiersExpanded && (
                <>
                  <p style={styles.hint}>
                    Assign installed Ollama tags to each tier alias. Then pick that alias
                    (e.g. local/coding-medium) under Intent → model below.
                  </p>
                  {!ollamaModels.reachable && (
                    <p style={styles.hint}>
                      Ollama unreachable — showing previously configured tags only.
                    </p>
                  )}
                  {TIER_LABELS.map(({ key, label }) => {
                    const current = form.ollama_model_names[key] ?? "";
                    const options = new Set(ollamaModels.models);
                    if (current) options.add(current);
                    return (
                      <Field key={key} label={label}>
                        <SearchableSelect
                          value={current}
                          options={Array.from(options).sort()}
                          allowEmpty
                          emptyLabel="Unassigned"
                          placeholder="Search Ollama tags…"
                          onChange={(next) =>
                            patch((f) => ({
                              ...f,
                              ollama_model_names: {
                                ...f.ollama_model_names,
                                [key]: next,
                              },
                            }))
                          }
                        />
                      </Field>
                    );
                  })}
                </>
              )}
              <div style={styles.sectionTitle}>Intent → model alias</div>
              {NON_TOOL_INTENT_LABELS.map(({ key, label }) => (
                <Field key={key} label={label}>
                  <SearchableSelect
                    value={form.models[key]}
                    options={modelAliases}
                    placeholder="Search model aliases…"
                    onChange={(next) =>
                      patch((f) => ({
                        ...f,
                        models: { ...f.models, [key]: next },
                      }))
                    }
                  />
                </Field>
              ))}
              <Field label="Tools model (bash / PDF generation / file operations / web search)">
                <SearchableSelect
                  value={form.models.bash}
                  options={modelAliases}
                  placeholder="Search model aliases…"
                  onChange={(next) =>
                    patch((f) => ({
                      ...f,
                      models: {
                        ...f.models,
                        bash: next,
                        pdf_gen: next,
                        file_ops: next,
                        web_search: next,
                      },
                    }))
                  }
                />
              </Field>
              <div style={styles.sectionTitle}>Vision models</div>
              <Field label="Local vision model">
                <input
                  style={styles.input}
                  value={form.vision.local_model}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      vision: { ...f.vision, local_model: e.target.value },
                    }))
                  }
                />
              </Field>
              <Field label="Remote vision model (optional)">
                <input
                  style={styles.input}
                  value={form.vision.remote_model}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      vision: { ...f.vision, remote_model: e.target.value },
                    }))
                  }
                  placeholder="Empty = local only"
                />
              </Field>
            </section>
          )}

          {tab === "router" && (
            <section>
              <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
                <button
                  type="button"
                  style={styles.secondaryBtn}
                  onClick={refreshOllamaModels}
                  disabled={ollamaLoading}
                >
                  {ollamaLoading ? "Refreshing…" : "Refresh from Ollama"}
                </button>
              </div>
              <Field label="Classifier model">
                <SearchableSelect
                  value={stripOllamaPrefix(form.router.classifier)}
                  options={(() => {
                    const current = stripOllamaPrefix(form.router.classifier);
                    const opts = new Set(ollamaTagOptions);
                    if (current) opts.add(current);
                    return Array.from(opts).sort();
                  })()}
                  placeholder="Search Ollama tags…"
                  onChange={(tag) =>
                    patch((f) => ({
                      ...f,
                      router: {
                        ...f.router,
                        classifier: tag ? toOllamaClassifier(tag) : tag,
                      },
                    }))
                  }
                />
                {!ollamaModels.reachable && (
                  <div style={styles.hint}>
                    Ollama unreachable — showing previously configured tag only.
                  </div>
                )}
              </Field>
              <button
                type="button"
                style={{ ...styles.disclosureTitle, marginTop: 8 }}
                onClick={() => setClassifierPromptExpanded((v) => !v)}
                aria-expanded={classifierPromptExpanded}
              >
                <span aria-hidden="true">{classifierPromptExpanded ? "▾" : "▸"}</span>
                Classifier prompt
              </button>
              {classifierPromptExpanded && (
                <textarea
                  style={styles.textarea}
                  value={form.router.classifier_prompt}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      router: { ...f.router, classifier_prompt: e.target.value },
                    }))
                  }
                  rows={8}
                />
              )}
              <Field label="Keyword rules">
                <label style={styles.checkLabel}>
                  <input
                    type="checkbox"
                    checked={form.router.rules_enabled}
                    onChange={(e) =>
                      patch((f) => ({
                        ...f,
                        router: { ...f.router, rules_enabled: e.target.checked },
                      }))
                    }
                  />
                  <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    Enable keyword routing rules
                  </span>
                </label>
              </Field>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  Available tools: {availableTools.join(", ") || "(none)"}
                </span>
                <button
                  type="button"
                  style={styles.secondaryBtn}
                  onClick={refreshTools}
                  disabled={toolsLoading}
                >
                  {toolsLoading ? "Refreshing…" : "Refresh tools"}
                </button>
              </div>
              {form.router.rules.map((rule, index) => (
                <div key={index} style={styles.ruleCard}>
                  <div style={styles.ruleHeader}>
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                      Rule {index + 1}
                    </span>
                    <button type="button" style={styles.linkBtn} onClick={() => removeRule(index)}>
                      Remove
                    </button>
                  </div>
                  <Field label="Patterns (one per line)">
                    <textarea
                      style={{ ...styles.textarea, minHeight: 72 }}
                      value={rule.patterns.join("\n")}
                      onChange={(e) =>
                        updateRule(index, {
                          ...rule,
                          // Keep empty lines while typing so Enter works; blanks stripped on Save.
                          patterns: e.target.value.split("\n"),
                        })
                      }
                      onBlur={(e) =>
                        updateRule(index, {
                          ...rule,
                          patterns: normalizePatterns(e.target.value.split("\n")),
                        })
                      }
                      rows={3}
                    />
                  </Field>
                  <Field label="Intent">
                    <select
                      style={styles.select}
                      value={rule.intent}
                      onChange={(e) =>
                        updateRule(index, {
                          ...rule,
                          intent: e.target.value as IntentLabel,
                        })
                      }
                    >
                      {INTENT_LABELS.map(({ key, label }) => (
                        <option key={key} value={key}>{label}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Tools">
                    <div style={styles.toolCheckList}>
                      {availableTools.map((tool) => {
                        const checked = rule.tools.includes(tool);
                        return (
                          <label key={tool} style={styles.checkLabel}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => {
                                const tools = checked
                                  ? rule.tools.filter((t) => t !== tool)
                                  : [...rule.tools, tool];
                                updateRule(index, { ...rule, tools });
                              }}
                            />
                            <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                              {tool}
                            </span>
                          </label>
                        );
                      })}
                      {rule.tools
                        .filter((t) => !availableTools.includes(t))
                        .map((orphan) => (
                          <label key={orphan} style={styles.checkLabel}>
                            <input
                              type="checkbox"
                              checked
                              onChange={() =>
                                updateRule(index, {
                                  ...rule,
                                  tools: rule.tools.filter((t) => t !== orphan),
                                })
                              }
                            />
                            <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                              {orphan} (unknown)
                            </span>
                          </label>
                        ))}
                    </div>
                  </Field>
                </div>
              ))}
              <button type="button" style={styles.secondaryBtn} onClick={addRule}>
                Add rule
              </button>
            </section>
          )}

          {tab === "embedding" && (
            <section>
              <Field label="Embedding model">
                <input
                  style={styles.input}
                  value={form.embedding.model}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      embedding: { ...f.embedding, model: e.target.value },
                    }))
                  }
                />
              </Field>
              <Field label="Max tokens">
                <input
                  style={{ ...styles.input, width: 100 }}
                  type="number"
                  min={1}
                  value={form.embedding.max_tokens}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      embedding: {
                        ...f.embedding,
                        max_tokens: Number(e.target.value),
                      },
                    }))
                  }
                />
              </Field>
              <div style={styles.sectionTitle}>RAG chunking</div>
              <Field label="Chunk size (tokens)">
                <input
                  style={{ ...styles.input, width: 100 }}
                  type="number"
                  min={1}
                  value={form.rag.chunk_size}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      rag: { ...f.rag, chunk_size: Number(e.target.value) },
                    }))
                  }
                />
              </Field>
              <Field label="Overlap ratio">
                <input
                  style={{ ...styles.input, width: 100 }}
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={form.rag.chunk_overlap_ratio}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      rag: {
                        ...f.rag,
                        chunk_overlap_ratio: Number(e.target.value),
                      },
                    }))
                  }
                />
              </Field>
              <Field label="Top-K">
                <input
                  style={{ ...styles.input, width: 80 }}
                  type="number"
                  min={1}
                  value={form.rag.top_k}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      rag: { ...f.rag, top_k: Number(e.target.value) },
                    }))
                  }
                />
              </Field>
            </section>
          )}

          {tab === "search" && (
            <section>
              <Field label="Web search provider">
                <select
                  style={styles.select}
                  value={form.search.providers.web_search}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      search: {
                        ...f.search,
                        providers: {
                          ...f.search.providers,
                          web_search: e.target.value,
                        },
                      },
                    }))
                  }
                >
                  {SEARCH_PROVIDERS.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </Field>
              <Field label="Deep research provider">
                <select
                  style={styles.select}
                  value={form.search.providers.deep_research}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      search: {
                        ...f.search,
                        providers: {
                          ...f.search.providers,
                          deep_research: e.target.value,
                        },
                      },
                    }))
                  }
                >
                  {SEARCH_PROVIDERS.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </Field>
              <Field label="Tavily API key">
                <input
                  style={styles.input}
                  type="password"
                  value={draftTavilyKey}
                  onChange={(e) => setDraftTavilyKey(e.target.value)}
                  placeholder={
                    form.search.tavily_api_key_set
                      ? "Configured — enter a new key to replace"
                      : "Enter Tavily API key"
                  }
                  autoComplete="off"
                />
                <div style={styles.hint}>
                  Saved to .env (TAVILY_API_KEY), not settings.json.
                </div>
              </Field>
            </section>
          )}

          {tab === "infrastructure" && (
            <section>
              <div style={styles.sectionTitle}>Ollama</div>
              <Field label="Base URL">
                <input
                  style={styles.input}
                  value={form.ollama.base_url}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      ollama: { ...f.ollama, base_url: e.target.value },
                    }))
                  }
                />
              </Field>
              <Field label="Keep alive (seconds; 300 = 5m idle unload, -1 = forever)">
                <input
                  style={{ ...styles.input, width: 100 }}
                  type="number"
                  value={form.ollama.keep_alive}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      ollama: {
                        ...f.ollama,
                        keep_alive: Number(e.target.value),
                      },
                    }))
                  }
                />
              </Field>
              <Field label="Model scheduler">
                <label style={styles.checkLabel}>
                  <input
                    type="checkbox"
                    checked={form.ollama.scheduler_enabled}
                    onChange={(e) =>
                      patch((f) => ({
                        ...f,
                        ollama: {
                          ...f.ollama,
                          scheduler_enabled: e.target.checked,
                        },
                      }))
                    }
                  />
                  <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    Serialize local model swaps
                  </span>
                </label>
              </Field>
              <div style={styles.sectionTitle}>Qdrant</div>
              <Field label="URL">
                <input
                  style={styles.input}
                  value={form.qdrant.url}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      qdrant: { ...f.qdrant, url: e.target.value },
                    }))
                  }
                />
              </Field>
              <div style={styles.sectionTitle}>OpenCode Go</div>
              <Field label="Base URL">
                <input
                  style={styles.input}
                  value={form.opencode_go.base_url}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      opencode_go: {
                        ...f.opencode_go,
                        base_url: e.target.value,
                      },
                    }))
                  }
                />
              </Field>
              <Field label="Enabled">
                <label style={styles.checkLabel}>
                  <input
                    type="checkbox"
                    checked={form.opencode_go.enabled}
                    onChange={(e) =>
                      patch((f) => ({
                        ...f,
                        opencode_go: {
                          ...f.opencode_go,
                          enabled: e.target.checked,
                        },
                      }))
                    }
                  />
                  <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    Use remote models via OpenCode Go
                  </span>
                </label>
              </Field>
              <Field label="API key">
                <input
                  style={styles.input}
                  type="password"
                  value={draftOpencodeKey}
                  onChange={(e) => setDraftOpencodeKey(e.target.value)}
                  placeholder={
                    form.opencode_go.api_key_set
                      ? "Configured — enter a new key to replace"
                      : "Enter OpenCode Go API key"
                  }
                  autoComplete="off"
                />
                <div style={styles.hint}>
                  Saved to .env (OPENCODE_API_KEY), not settings.json.
                </div>
              </Field>
              <div style={styles.sectionTitle}>Health</div>
              <Field label="Classifier timeout (seconds)">
                <input
                  style={{ ...styles.input, width: 80 }}
                  type="number"
                  min={0.1}
                  step={0.5}
                  value={form.health.classifier_timeout_s}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      health: {
                        ...f.health,
                        classifier_timeout_s: Number(e.target.value),
                      },
                    }))
                  }
                />
              </Field>
              <Field label="Ollama fallback">
                <label style={styles.checkLabel}>
                  <input
                    type="checkbox"
                    checked={form.health.ollama_fallback_to_remote}
                    onChange={(e) =>
                      patch((f) => ({
                        ...f,
                        health: {
                          ...f.health,
                          ollama_fallback_to_remote: e.target.checked,
                        },
                      }))
                    }
                  />
                  <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    Fall back to remote when Ollama is unavailable
                  </span>
                </label>
              </Field>
            </section>
          )}

          {tab === "logging" && (
            <section>
              <Field label="Log level">
                <select
                  style={styles.select}
                  value={form.logging.level}
                  onChange={(e) =>
                    patch((f) => ({
                      ...f,
                      logging: { ...f.logging, level: e.target.value },
                    }))
                  }
                >
                  {LOG_LEVELS.map((level) => (
                    <option key={level} value={level}>{level}</option>
                  ))}
                </select>
              </Field>
              <Field label="File logging">
                <label style={styles.checkLabel}>
                  <input
                    type="checkbox"
                    checked={form.logging.file_enabled}
                    onChange={(e) =>
                      patch((f) => ({
                        ...f,
                        logging: {
                          ...f.logging,
                          file_enabled: e.target.checked,
                        },
                      }))
                    }
                  />
                  <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    Write logs to file
                  </span>
                </label>
              </Field>
              <div style={styles.sectionTitle}>Subsystems</div>
              {(Object.keys(form.logging.subsystems) as (keyof typeof form.logging.subsystems)[]).map(
                (key) => (
                  <Field key={key} label={key}>
                    <label style={styles.checkLabel}>
                      <input
                        type="checkbox"
                        checked={form.logging.subsystems[key]}
                        onChange={(e) =>
                          patch((f) => ({
                            ...f,
                            logging: {
                              ...f.logging,
                              subsystems: {
                                ...f.logging.subsystems,
                                [key]: e.target.checked,
                              },
                            },
                          }))
                        }
                      />
                      <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                        Enable logging
                      </span>
                    </label>
                  </Field>
                ),
              )}
              <div style={styles.sectionTitle}>Debug</div>
              <Field label="Router decisions">
                <label style={styles.checkLabel}>
                  <input
                    type="checkbox"
                    checked={form.debug.router_decisions}
                    onChange={(e) =>
                      patch((f) => ({
                        ...f,
                        debug: {
                          ...f.debug,
                          router_decisions: e.target.checked,
                        },
                      }))
                    }
                  />
                  <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    Log routing decisions to stderr
                  </span>
                </label>
              </Field>
              <Field label="Debug trace">
                <label style={styles.checkLabel}>
                  <input
                    type="checkbox"
                    checked={form.debug.sse_trace ?? false}
                    onChange={(e) =>
                      patch((f) => ({
                        ...f,
                        debug: {
                          ...f.debug,
                          sse_trace: e.target.checked,
                        },
                      }))
                    }
                  />
                  <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    Show debug trace in chat UI
                  </span>
                </label>
              </Field>
            </section>
          )}
        </div>
        <div style={styles.footer}>
          <button style={styles.cancelBtn} onClick={onClose} type="button">Close</button>
          <button
            type="button"
            style={{ ...styles.saveBtn, ...(saved ? styles.savedBtn : {}) }}
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? "Saving…" : saved ? "Saved" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>{label}</div>
      {children}
    </div>
  );
}

function SearchableSelect({
  value,
  options,
  onChange,
  allowEmpty = false,
  emptyLabel = "Unassigned",
  placeholder = "Search…",
}: {
  value: string;
  options: string[];
  onChange: (value: string) => void;
  allowEmpty?: boolean;
  emptyLabel?: string;
  placeholder?: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? options.filter((o) => o.toLowerCase().includes(q)) : options;
    return list;
  }, [options, query]);

  const display = open ? query : value || (allowEmpty ? emptyLabel : "");

  return (
    <div ref={rootRef} style={styles.searchSelectRoot}>
      <input
        style={styles.input}
        value={display}
        placeholder={placeholder}
        onFocus={() => {
          setOpen(true);
          setQuery(value);
        }}
        onChange={(e) => {
          setOpen(true);
          setQuery(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            setOpen(false);
            setQuery("");
          }
        }}
        autoComplete="off"
        spellCheck={false}
      />
      {open && (
        <div style={styles.searchSelectMenu}>
          {allowEmpty && (
            <button
              type="button"
              style={{
                ...styles.searchSelectOption,
                ...(value === "" ? styles.searchSelectOptionActive : {}),
              }}
              onClick={() => {
                onChange("");
                setOpen(false);
                setQuery("");
              }}
            >
              {emptyLabel}
            </button>
          )}
          {filtered.length === 0 ? (
            <div style={styles.searchSelectEmpty}>No matches</div>
          ) : (
            filtered.map((opt) => (
              <button
                key={opt}
                type="button"
                style={{
                  ...styles.searchSelectOption,
                  ...(opt === value ? styles.searchSelectOptionActive : {}),
                }}
                onClick={() => {
                  onChange(opt);
                  setOpen(false);
                  setQuery("");
                }}
              >
                {opt}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.7)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  },
  modal: {
    background: "var(--bg-panel)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-lg)",
    width: 520,
    maxWidth: "95vw",
    maxHeight: "90vh",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  modalHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "14px 16px",
    borderBottom: "1px solid var(--border)",
  },
  modalTitle: { fontWeight: 600, fontSize: 14 },
  closeBtn: { width: 24, height: 24, fontSize: 18, color: "var(--text-muted)" },
  errorBanner: {
    margin: "8px 16px 0",
    padding: "6px 10px",
    background: "rgba(224,85,85,0.12)",
    color: "var(--danger)",
    fontSize: 12,
  },
  tabBar: {
    display: "flex",
    flexWrap: "wrap",
    gap: 4,
    padding: "8px 12px",
    borderBottom: "1px solid var(--border)",
  },
  tabBtn: {
    padding: "5px 10px",
    fontSize: 11,
    background: "transparent",
    color: "var(--text-muted)",
    borderRadius: "var(--radius-sm)",
  },
  tabBtnActive: {
    background: "var(--bg-hover)",
    color: "var(--accent)",
    fontWeight: 600,
  },
  body: {
    flex: 1,
    overflowY: "auto",
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: 700,
    textTransform: "uppercase",
    color: "var(--accent)",
    marginBottom: 12,
    borderBottom: "1px solid var(--border)",
    paddingBottom: 4,
  },
  disclosureTitle: {
    fontSize: 11,
    fontWeight: 700,
    textTransform: "uppercase",
    color: "var(--accent)",
    marginBottom: 12,
    display: "flex",
    alignItems: "center",
    gap: 6,
    width: "100%",
    background: "transparent",
    border: "none",
    borderBottom: "1px solid var(--border)",
    padding: "0 0 4px",
    cursor: "pointer",
    textAlign: "left",
  },
  input: { width: "100%", padding: "7px 10px", fontSize: 13 },
  select: { width: "100%", padding: "7px 10px", fontSize: 13 },
  searchSelectRoot: { position: "relative", width: "100%" },
  searchSelectMenu: {
    position: "absolute",
    zIndex: 20,
    left: 0,
    right: 0,
    top: "100%",
    marginTop: 2,
    maxHeight: 220,
    overflowY: "auto",
    background: "var(--bg-panel)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-sm)",
    boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
  },
  searchSelectOption: {
    display: "block",
    width: "100%",
    textAlign: "left",
    padding: "7px 10px",
    fontSize: 12,
    fontFamily: "var(--font-mono)",
    background: "transparent",
    color: "var(--text)",
    border: "none",
    cursor: "pointer",
  },
  searchSelectOptionActive: {
    background: "var(--bg-hover)",
    color: "var(--accent)",
  },
  searchSelectEmpty: {
    padding: "8px 10px",
    fontSize: 12,
    color: "var(--text-muted)",
  },
  textarea: {
    width: "100%",
    padding: "7px 10px",
    fontSize: 12,
    fontFamily: "var(--font-mono)",
    minHeight: 120,
    resize: "vertical",
  },
  checkLabel: { display: "flex", alignItems: "center", gap: 8 },
  toolCheckList: {
    display: "flex",
    flexDirection: "column",
    gap: 6,
    padding: "6px 0",
  },
  hint: { marginTop: 4, fontSize: 11, color: "var(--text-dim)" },
  ruleCard: {
    marginBottom: 12,
    padding: 10,
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-input)",
  },
  ruleHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 8,
  },
  linkBtn: {
    fontSize: 11,
    color: "var(--danger)",
    background: "transparent",
    padding: 0,
  },
  secondaryBtn: {
    padding: "6px 12px",
    fontSize: 12,
    background: "var(--bg-hover)",
    color: "var(--accent)",
  },
  footer: {
    display: "flex",
    justifyContent: "flex-end",
    gap: 8,
    padding: 12,
    borderTop: "1px solid var(--border)",
  },
  cancelBtn: { padding: "7px 16px", background: "var(--bg-hover)" },
  saveBtn: { padding: "7px 20px", background: "var(--accent)", color: "#000", fontWeight: 600 },
  savedBtn: { background: "var(--success)" },
};
