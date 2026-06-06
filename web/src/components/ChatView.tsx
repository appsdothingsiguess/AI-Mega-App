import {
  useEffect,
  useRef,
  useState,
} from "react";
import {
  listThreads,
  getMessages,
  sendMessage,
  getSources,
  MessageRecord,
  SourcesState,
} from "../api/client";
import MessageBubble from "./MessageBubble";

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

interface Props {
  projectId: string | null;
  threadId: string | null;
  sourcesVersion: number;
  threadsVersion?: number;
  onThreadsChange?: () => void;
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

export default function ChatView({
  projectId,
  threadId,
  sourcesVersion,
  threadsVersion = 0,
  onThreadsChange,
}: Props) {
  const [messages, setMessages] = useState<MessageRecord[]>([]);
  const [threadTitle, setThreadTitle] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [enabledCount, setEnabledCount] = useState<number | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const thinkingVerb = useVerbCycle(loading);

  const scrollBottom = () =>
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });

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
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !threadId) {
      setMessages([]);
      return;
    }
    getMessages(projectId, threadId)
      .then((msgs) => {
        setMessages(msgs as MessageRecord[]);
        setTimeout(scrollBottom, 80);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load messages");
      });
  }, [projectId, threadId, threadsVersion]);

  const handleSend = async () => {
    if (!projectId || !threadId || !draft.trim() || loading) return;
    const content = draft.trim();
    setDraft("");
    setError(null);

    const optimisticUser: MessageRecord = {
      role: "user",
      content,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticUser]);
    setTimeout(scrollBottom, 50);

    setLoading(true);
    try {
      const resp = await sendMessage(projectId, threadId, content);
      const assistantMsg: MessageRecord = {
        role: "assistant",
        content: resp.reply,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMsg]);
      getSources(projectId)
        .then((s) => setEnabledCount(countEnabled(s)))
        .catch(() => null);
      onThreadsChange?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Send failed");
      setMessages((prev) => prev.filter((m) => m !== optimisticUser));
    } finally {
      setLoading(false);
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

  const visibleMessages = messages.filter((m) => m.role !== "system");
  const headerTitle = threadTitle ?? threadId.slice(0, 12);

  return (
    <div style={styles.root}>
      <div style={styles.chatHeader}>
        <span style={styles.chatTitle}>{headerTitle}</span>
      </div>

      <div style={styles.messages}>
        {visibleMessages.length === 0 && !loading && (
          <div style={styles.noMessages}>
            No messages yet — send one below
          </div>
        )}
        {visibleMessages.map((m, i) => (
          <MessageBubble
            key={i}
            role={m.role as "user" | "assistant" | "system"}
            content={m.content}
            createdAt={m.created_at}
          />
        ))}
        {loading && (
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
            disabled={loading}
          />
          <button
            style={{
              ...styles.sendBtn,
              ...(loading || !draft.trim() ? styles.sendBtnDisabled : {}),
            }}
            onClick={handleSend}
            disabled={loading || !draft.trim()}
          >
            {loading ? "…" : "Send"}
          </button>
        </div>
      </div>
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
    alignItems: "center",
    padding: "0 16px",
    borderBottom: "1px solid var(--border)",
    height: 40,
    flexShrink: 0,
  },
  chatTitle: {
    fontSize: 14,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
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
