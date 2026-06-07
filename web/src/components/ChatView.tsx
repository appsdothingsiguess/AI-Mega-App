import {
  useEffect,
  useRef,
  useState,
} from "react";
import {
  listThreads,
  getMessages,
  streamChat,
  getSources,
  MessageRecord,
  SourcesState,
  SourceChunk,
  StreamChatError,
} from "../api/client";
import MessageBubble, { ToolEvent } from "./MessageBubble";
import ModelSelector from "./ModelSelector";
import ToolToggles, { ToolTogglesState } from "./ToolToggles";
import DebugTracePanel, { TraceEntry } from "./DebugTracePanel";

// Same verb list as app/main.py _THINKING_VERBS
const THINKING_VERBS = [
  "Accomplishing", "Actioning", "Actualizing", "Architecting", "Baking",
  "Beaming", "Befuddling", "Billowing", "Blanching", "Bloviating",
  "Boogieing", "Boondoggling", "Bootstrapping", "Brewing", "Burrowing",
  "Calculating", "Canoodling", "Caramelizing", "Cascading", "Catapulting",
  "Cerebrating", "Channeling", "Choreographing", "Churning", "Clauding",
  "Coalescing", "Cogitating", "Combobulating", "Composing", "Computing",
  "Concocting", "Considering", "Contemplating", "Cooking", "Crafting",
  "Creating", "Crunching", "Crystallizing", "Cultivating", "Deciphering",
  "Deliberating", "Determining", "Doodling", "Drizzling", "Ebbing",
  "Elucidating", "Embellishing", "Enchanting", "Envisioning", "Fermenting",
  "Finagling", "Flambéing", "Flowing", "Fluttering", "Forging",
  "Forming", "Frolicking", "Frosting", "Gallivanting", "Galloping",
  "Garnishing", "Generating", "Germinating", "Grooving", "Harmonizing",
  "Hashing", "Hatching", "Herding", "Ideating", "Imagining",
  "Improvising", "Incubating", "Inferring", "Infusing", "Julienning",
  "Kneading", "Leavening", "Levitating", "Lollygagging", "Manifesting",
  "Marinating", "Meandering", "Metamorphosing", "Moonwalking", "Moseying",
  "Mulling", "Mustering", "Musing", "Nesting", "Noodling",
  "Nucleating", "Orbiting", "Orchestrating", "Perambulating", "Percolating",
  "Perusing", "Philosophising", "Pondering", "Pontificating", "Prestidigitating",
  "Processing", "Proofing", "Propagating", "Puttering", "Puzzling",
  "Roosting", "Ruminating", "Sautéing", "Scampering", "Schlepping",
  "Seasoning", "Shenaniganing", "Shimmying", "Simmering", "Sketching",
  "Smooshing", "Spelunking", "Spinning", "Sprouting", "Stewing",
  "Sublimating", "Swirling", "Swooping", "Synthesizing", "Tempering",
  "Thinking", "Tinkering", "Transmuting", "Twisting", "Undulating",
  "Unfurling", "Unravelling", "Vibing", "Waddling", "Wandering",
  "Warping", "Whisking", "Wibbling", "Working", "Wrangling",
  "Zesting", "Zigzagging",
];

function pickVerb() {
  return THINKING_VERBS[Math.floor(Math.random() * THINKING_VERBS.length)];
}

export function countEnabled(s: SourcesState): number {
  const enabled = s.files.filter((f) => f.enabled);
  return s.files.length === 0 ? 0 : enabled.length;
}

export interface StreamingMessage {
  role: "user" | "assistant";
  content: string;
  created_at: string;
  model?: string;
  isStreaming?: boolean;
  tools?: ToolEvent[];
  sources?: SourceChunk[];
  error?: string;
}

interface Props {
  projectId: string | null;
  threadId: string | null;
  sourcesVersion: number;
  threadsVersion?: number;
  onThreadsChange?: () => void;
  modelOverride: string | null;
  onModelOverrideChange: (alias: string | null) => void;
  toolToggles: ToolTogglesState;
  onToolTogglesChange: (toggles: ToolTogglesState) => void;
  onModelLoading: (payload: { model: string; estimated_seconds: number }) => void;
  onClearModelLoading: () => void;
  debugTraceOpen: boolean;
  sseTraceEnabled: boolean;
}

function useVerbCycle(active: boolean): string {
  const [verb, setVerb] = useState(pickVerb);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setVerb(pickVerb()), 2000);
    return () => clearInterval(id);
  }, [active]);
  return verb;
}

function toDisplayMessage(m: MessageRecord): StreamingMessage {
  return {
    role: m.role as "user" | "assistant",
    content: m.content,
    created_at: m.created_at,
    model: m.model,
  };
}

export default function ChatView({
  projectId,
  threadId,
  sourcesVersion,
  threadsVersion = 0,
  onThreadsChange,
  modelOverride,
  onModelOverrideChange,
  toolToggles,
  onToolTogglesChange,
  onModelLoading,
  onClearModelLoading,
  debugTraceOpen,
  sseTraceEnabled,
}: Props) {
  const [messages, setMessages] = useState<StreamingMessage[]>([]);
  const [traceEntries, setTraceEntries] = useState<TraceEntry[]>([]);
  const [threadTitle, setThreadTitle] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [awaitingFirstChunk, setAwaitingFirstChunk] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [enabledCount, setEnabledCount] = useState<number | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const thinkingVerb = useVerbCycle(awaitingFirstChunk);

  const scrollBottom = () =>
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!projectId) {
      setThreadTitle(null);
      return;
    }
    if (!threadId) {
      setThreadTitle(null);
      return;
    }
    listThreads(projectId)
      .then((threads) => {
        const t = threads.find((x) => x.id === threadId);
        setThreadTitle(
          t?.title?.trim() || t?.id.slice(0, 12) || threadId.slice(0, 12),
        );
      })
      .catch(() => setThreadTitle(threadId.slice(0, 12)));
  }, [projectId, threadId, threadsVersion]);

  useEffect(() => {
    if (!projectId) {
      setEnabledCount(null);
      return;
    }
    getSources(projectId)
      .then((s) => setEnabledCount(countEnabled(s)))
      .catch(() => setEnabledCount(null));
  }, [projectId, sourcesVersion]);

  useEffect(() => {
    setMessages([]);
    setError(null);
    setDraft("");
    abortRef.current?.abort();
    setStreaming(false);
    setAwaitingFirstChunk(false);
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !threadId) {
      setMessages([]);
      return;
    }
    abortRef.current?.abort();
    setStreaming(false);
    setAwaitingFirstChunk(false);

    getMessages(projectId, threadId)
      .then((msgs) => {
        setMessages(
          (msgs as MessageRecord[])
            .filter((m) => m.role !== "system")
            .map(toDisplayMessage),
        );
        setTimeout(scrollBottom, 80);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load messages");
      });
  }, [projectId, threadId, threadsVersion]);

  const updateStreamingMessage = (
    updater: (msg: StreamingMessage) => StreamingMessage,
  ) => {
    setMessages((prev) => {
      const idx = prev.findIndex((m) => m.isStreaming);
      if (idx === -1) return prev;
      const next = [...prev];
      next[idx] = updater(next[idx]);
      return next;
    });
  };

  const handleSend = async () => {
    if (!projectId || !threadId || !draft.trim() || streaming) return;
    const content = draft.trim();
    setDraft("");
    setError(null);
    setTraceEntries([]);

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const optimisticUser: StreamingMessage = {
      role: "user",
      content,
      created_at: new Date().toISOString(),
    };
    const streamingAssistant: StreamingMessage = {
      role: "assistant",
      content: "",
      created_at: new Date().toISOString(),
      isStreaming: true,
      tools: [],
      sources: [],
    };

    setMessages((prev) => [...prev, optimisticUser, streamingAssistant]);
    setStreaming(true);
    setAwaitingFirstChunk(true);
    setTimeout(scrollBottom, 50);

    const body: {
      content: string;
      model_override?: string;
      enabled_tools?: Record<string, boolean>;
    } = {
      content,
      enabled_tools: { ...toolToggles },
    };
    if (modelOverride) {
      body.model_override = modelOverride;
    }

    let gotFirstChunk = false;

    try {
      for await (const event of streamChat(
        projectId,
        threadId,
        body,
        controller.signal,
      )) {
        switch (event.type) {
          case "model_loading":
            onModelLoading({
              model: event.model,
              estimated_seconds: event.estimated_seconds,
            });
            break;

          case "routed":
            updateStreamingMessage((msg) => ({
              ...msg,
              model: event.model,
            }));
            break;

          case "sources":
            updateStreamingMessage((msg) => ({
              ...msg,
              sources: event.chunks,
            }));
            break;

          case "chunk":
            if (!gotFirstChunk) {
              gotFirstChunk = true;
              setAwaitingFirstChunk(false);
              onClearModelLoading();
            }
            updateStreamingMessage((msg) => ({
              ...msg,
              content: msg.content + event.content,
            }));
            setTimeout(scrollBottom, 30);
            break;

          case "tool_call":
            updateStreamingMessage((msg) => ({
              ...msg,
              tools: [
                ...(msg.tools ?? []),
                { name: event.name, kind: "call", input: event.input },
              ],
            }));
            break;

          case "tool_result":
            updateStreamingMessage((msg) => ({
              ...msg,
              tools: [
                ...(msg.tools ?? []),
                { name: event.name, kind: "result", output: event.output },
              ],
            }));
            break;

          case "error":
            updateStreamingMessage((msg) => ({
              ...msg,
              error: event.message,
            }));
            break;

          case "debug":
            setTraceEntries((prev) => [
              ...prev,
              {
                id: `${Date.now()}-${prev.length}`,
                timestamp: new Date().toISOString(),
                stage: event.stage,
                data: event.data,
              },
            ]);
            break;

          case "done":
            updateStreamingMessage((msg) => ({
              ...msg,
              isStreaming: false,
              model: msg.model ?? event.model,
            }));
            break;
        }
      }

      setMessages((prev) =>
        prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m)),
      );

      getSources(projectId)
        .then((s) => setEnabledCount(countEnabled(s)))
        .catch(() => null);
      onThreadsChange?.();
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === "AbortError") {
        setMessages((prev) =>
          prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m)),
        );
      } else {
        const msg =
          e instanceof StreamChatError || e instanceof Error
            ? e.message
            : "Send failed";
        setError(msg);
        setMessages((prev) => {
          const withoutStreaming = prev.filter((m) => !m.isStreaming);
          return withoutStreaming.filter((m) => m !== optimisticUser);
        });
      }
      onClearModelLoading();
    } finally {
      setStreaming(false);
      setAwaitingFirstChunk(false);
      abortRef.current = null;
      setTimeout(scrollBottom, 80);
      textareaRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (!projectId) {
    return (
      <div style={styles.empty}>
        <div style={styles.emptyIcon}>🧠</div>
        <div style={styles.emptyText}>
          Select or create a project to start chatting
        </div>
      </div>
    );
  }

  if (!threadId) {
    return (
      <div style={styles.empty}>
        <div style={styles.emptyIcon}>💬</div>
        <div style={styles.emptyText}>Select or create a chat to begin</div>
      </div>
    );
  }

  const headerTitle = threadTitle ?? threadId.slice(0, 12);

  return (
    <div style={styles.root}>
      <div style={styles.chatHeader}>
        <span style={styles.chatTitle}>{headerTitle}</span>
        <div style={styles.headerControls}>
          <ModelSelector
            modelOverride={modelOverride}
            onModelOverrideChange={onModelOverrideChange}
            disabled={streaming}
          />
          <ToolToggles
            toggles={toolToggles}
            onChange={onToolTogglesChange}
            disabled={streaming}
          />
        </div>
      </div>

      <div style={styles.messages}>
        {messages.length === 0 && !awaitingFirstChunk && (
          <div style={styles.noMessages}>
            No messages yet — send one below
          </div>
        )}
        {messages.map((m, i) => (
          <MessageBubble
            key={i}
            role={m.role}
            content={m.content}
            createdAt={m.created_at}
            model={m.model}
            isStreaming={m.isStreaming}
            tools={m.tools}
            sources={m.sources}
            error={m.error}
          />
        ))}
        {awaitingFirstChunk && (
          <div style={styles.loadingRow}>
            <span style={styles.loadingDot}>•</span>
            <span style={styles.loadingVerb}>{thinkingVerb}…</span>
            <LoadingDots />
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div style={styles.composerArea}>
        {error && (
          <div style={styles.errorBanner}>
            {error}
            <button
              style={styles.dismissBtn}
              onClick={() => setError(null)}
            >
              ×
            </button>
          </div>
        )}
        <div style={styles.sourceChipRow}>
          {enabledCount !== null && (
            <span style={styles.sourceChip}>
              {enabledCount === 0
                ? "No sources indexed"
                : `Using ${enabledCount} source${enabledCount !== 1 ? "s" : ""}`}
            </span>
          )}
        </div>
        <div style={styles.composerBox}>
          <textarea
            ref={textareaRef}
            style={styles.textarea}
            rows={3}
            value={draft}
            placeholder="Ask something… (Enter to send, Shift+Enter for newline)"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={streaming}
          />
          <button
            style={{
              ...styles.sendBtn,
              ...(streaming || !draft.trim() ? styles.sendBtnDisabled : {}),
            }}
            onClick={handleSend}
            disabled={streaming || !draft.trim()}
          >
            {streaming ? "…" : "Send"}
          </button>
        </div>
      </div>

      <DebugTracePanel
        entries={traceEntries}
        visible={debugTraceOpen}
        sseTraceEnabled={sseTraceEnabled}
        onClear={() => setTraceEntries([])}
      />
    </div>
  );
}

function LoadingDots() {
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setFrame((f) => (f + 1) % 4), 400);
    return () => clearInterval(id);
  }, []);
  return <span style={{ color: "var(--text-dim)" }}>{"...".slice(0, frame)}</span>;
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    overflow: "hidden",
  },
  empty: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    gap: 12,
  },
  emptyIcon: {
    fontSize: 48,
    opacity: 0.3,
  },
  emptyText: {
    color: "var(--text-dim)",
    fontSize: 14,
  },
  chatHeader: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
    padding: "8px 16px",
    borderBottom: "1px solid var(--border)",
    flexShrink: 0,
  },
  chatTitle: {
    fontSize: 14,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  headerControls: {
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  messages: {
    flex: 1,
    overflowY: "auto",
    padding: "16px 20px",
    display: "flex",
    flexDirection: "column",
  },
  noMessages: {
    color: "var(--text-dim)",
    fontSize: 12,
    textAlign: "center",
    marginTop: 40,
  },
  loadingRow: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    marginBottom: 12,
    color: "var(--text-muted)",
    fontSize: 13,
  },
  loadingDot: {
    color: "var(--accent)",
    fontSize: 18,
  },
  loadingVerb: {
    fontStyle: "italic",
  },
  composerArea: {
    flexShrink: 0,
    padding: "8px 12px 12px",
    borderTop: "1px solid var(--border)",
    background: "var(--bg-panel)",
  },
  errorBanner: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "6px 10px",
    marginBottom: 8,
    borderRadius: "var(--radius-sm)",
    background: "var(--danger-dim)",
    color: "var(--danger)",
    fontSize: 12,
  },
  dismissBtn: {
    fontSize: 16,
    color: "var(--danger)",
    opacity: 0.7,
    padding: "0 4px",
  },
  sourceChipRow: {
    marginBottom: 6,
    minHeight: 20,
  },
  sourceChip: {
    display: "inline-block",
    fontSize: 11,
    padding: "2px 8px",
    borderRadius: 10,
    background: "var(--accent-dim)",
    color: "var(--accent)",
  },
  composerBox: {
    display: "flex",
    gap: 8,
    alignItems: "flex-end",
  },
  textarea: {
    flex: 1,
    resize: "none",
    padding: "10px 12px",
    borderRadius: "var(--radius-md)",
    lineHeight: 1.5,
    fontSize: 14,
  },
  sendBtn: {
    padding: "0 20px",
    height: 72,
    borderRadius: "var(--radius-md)",
    background: "var(--accent)",
    color: "#000",
    fontWeight: 700,
    fontSize: 14,
    flexShrink: 0,
    transition: "background var(--transition)",
  },
  sendBtnDisabled: {
    background: "var(--bg-hover)",
    color: "var(--text-dim)",
  },
};
