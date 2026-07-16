import { memo, useMemo, useState } from "react";
import type { SourceChunk, TodoItem } from "../api/client";
import ArtifactRenderer from "./ArtifactRenderer";
import AskUserPrompt from "./AskUserPrompt";
import SourceCitations from "./SourceCitations";
import TodoChecklist from "./TodoChecklist";

export interface ToolEvent {
  name: string;
  kind: "call" | "result";
  input?: Record<string, unknown>;
  output?: string;
}

interface AskUserState {
  question: string;
  options: string[];
  answered: boolean;
}

interface Props {
  role: "user" | "assistant" | "system";
  content: string;
  createdAt?: string;
  model?: string;
  isStreaming?: boolean;
  tools?: ToolEvent[];
  sources?: SourceChunk[];
  todos?: TodoItem[];
  askUser?: AskUserState;
  onAskUserAnswer?: (text: string) => void;
  error?: string;
}

const TOOL_STEP_LABELS: Record<string, string> = {
  grep: "Searched files",
  glob: "Found files",
  web_fetch: "Fetched a page",
};

function formatToolOutput(output: string): string {
  try {
    const parsed = JSON.parse(output);
    return JSON.stringify(parsed, null, 2);
  } catch {
    return output;
  }
}

const DSML_PIPES = /[|｜]+/; // matches ASCII | and fullwidth ｜ (U+FF5C)
const DSML_BLOCK_RE = new RegExp(
  `<${DSML_PIPES.source}DSML${DSML_PIPES.source}tool_calls>[\\s\\S]*</${DSML_PIPES.source}DSML${DSML_PIPES.source}tool_calls>`,
  "gi"
);

function stripToolMarkup(content: string): string {
  return content.replace(DSML_BLOCK_RE, "").trim();
}

function ToolSection({ tools }: { tools: ToolEvent[] }) {
  const [open, setOpen] = useState(true);
  if (tools.length === 0) return null;

  return (
    <div style={styles.toolsWrap}>
      <button type="button" style={styles.toolsToggle} onClick={() => setOpen((v) => !v)}>
        {open ? "▾" : "▸"} Tools ({tools.length})
      </button>
      {open && (
        <div style={styles.toolsList}>
          {tools.map((t, i) => (
            <div key={i} style={styles.toolItem}>
              <div style={styles.toolHeader}>
                <span style={styles.toolKind}>{t.kind === "call" ? "Call" : "Result"}</span>
                <span style={styles.toolName}>{TOOL_STEP_LABELS[t.name] ?? t.name}</span>
              </div>
              {t.kind === "call" && t.input && (
                <pre style={styles.toolBody}>
                  {JSON.stringify(t.input, null, 2)}
                </pre>
              )}
              {t.kind === "result" && t.output !== undefined && (
                <pre style={styles.toolBody}>{formatToolOutput(t.output)}</pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function areEqual(prev: Props, next: Props): boolean {
  return (
    prev.role === next.role &&
    prev.content === next.content &&
    prev.createdAt === next.createdAt &&
    prev.model === next.model &&
    prev.isStreaming === next.isStreaming &&
    prev.error === next.error &&
    prev.tools === next.tools &&
    prev.sources === next.sources &&
    prev.todos === next.todos &&
    prev.askUser === next.askUser &&
    prev.onAskUserAnswer === next.onAskUserAnswer
  );
}

function MessageBubble({
  role,
  content,
  createdAt,
  model,
  isStreaming,
  tools,
  sources,
  todos,
  askUser,
  onAskUserAnswer,
  error,
}: Props) {
  const displayContent = useMemo(
    () => (role === "assistant" ? stripToolMarkup(content) : content),
    [role, content],
  );

  if (role === "system") return null;

  const time = createdAt
    ? new Date(createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;

  if (role === "user") {
    return (
      <div style={styles.userRow}>
        <div style={styles.userBubble}>
          <div style={styles.userContent}>{content}</div>
          {time && <div style={styles.timeUser}>{time}</div>}
        </div>
      </div>
    );
  }

  return (
    <div style={styles.assistantRow}>
      <div style={styles.assistantDot}>•</div>
      <div style={styles.assistantBody}>
        {tools && tools.length > 0 && <ToolSection tools={tools} />}
        {sources && sources.length > 0 && <SourceCitations sources={sources} />}
        {todos && todos.length > 0 && <TodoChecklist todos={todos} />}
        {askUser && onAskUserAnswer && (
          <AskUserPrompt
            question={askUser.question}
            options={askUser.options}
            onAnswer={onAskUserAnswer}
            disabled={!!isStreaming || askUser.answered}
          />
        )}
        {(content || isStreaming) && (
          <ArtifactRenderer content={displayContent} isStreaming={isStreaming} />
        )}
        {error && <div style={styles.inlineError}>{error}</div>}
        {(model || (time && !isStreaming)) && (
          <div style={styles.metaRow}>
            {model && <span style={styles.modelLabel}>{model}</span>}
            {time && !isStreaming && (
              <span style={styles.timeAssistant}>{time}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(MessageBubble, areEqual);

const styles: Record<string, React.CSSProperties> = {
  userRow: {
    display: "flex",
    justifyContent: "flex-end",
    marginBottom: 12,
    paddingLeft: "20%",
  },
  userBubble: {
    background: "var(--user-bubble)",
    border: "1px solid var(--user-bubble-border)",
    borderRadius: "var(--radius-md)",
    padding: "10px 14px",
    maxWidth: "100%",
  },
  userContent: {
    fontSize: 14,
    lineHeight: 1.55,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
  timeUser: {
    marginTop: 4,
    fontSize: 10,
    color: "rgba(232,232,232,0.4)",
    textAlign: "right",
  },
  assistantRow: {
    display: "flex",
    alignItems: "flex-start",
    gap: 10,
    marginBottom: 16,
    paddingRight: "10%",
  },
  assistantDot: {
    flexShrink: 0,
    color: "var(--accent)",
    fontSize: 18,
    lineHeight: "1.55",
    marginTop: 2,
  },
  assistantBody: {
    flex: 1,
    minWidth: 0,
  },
  metaRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 4,
    gap: 8,
  },
  modelLabel: {
    fontSize: 10,
    color: "var(--text-dim)",
    fontFamily: "var(--font-mono)",
  },
  timeAssistant: {
    fontSize: 10,
    color: "var(--text-dim)",
    marginLeft: "auto",
  },
  inlineError: {
    marginTop: 8,
    padding: "6px 10px",
    borderRadius: "var(--radius-sm)",
    background: "var(--danger-dim)",
    color: "var(--danger)",
    fontSize: 12,
  },
  toolsWrap: {
    marginBottom: 8,
  },
  toolsToggle: {
    fontSize: 11,
    color: "var(--text-muted)",
    padding: "2px 0",
    background: "transparent",
  },
  toolsList: {
    marginTop: 4,
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  toolItem: {
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-sm)",
    overflow: "hidden",
    background: "var(--bg-sidebar)",
  },
  toolHeader: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "4px 8px",
    borderBottom: "1px solid var(--border)",
    fontSize: 11,
  },
  toolKind: {
    color: "var(--text-dim)",
    textTransform: "uppercase",
    fontSize: 10,
    letterSpacing: "0.04em",
  },
  toolName: {
    color: "var(--accent)",
    fontFamily: "var(--font-mono)",
  },
  toolBody: {
    margin: 0,
    padding: "8px 10px",
    fontSize: 11,
    fontFamily: "var(--font-mono)",
    lineHeight: 1.45,
    overflowX: "auto",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    color: "var(--text-muted)",
  },
};
