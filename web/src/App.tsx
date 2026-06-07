import { useCallback, useEffect, useState } from "react";
import ProjectSidebar from "./components/ProjectSidebar";
import ProjectGrid from "./components/ProjectGrid";
import SourcesPanel from "./components/SourcesPanel";
import ChatView from "./components/ChatView";
import InstructionsPanel from "./components/InstructionsPanel";
import SettingsModal from "./components/SettingsModal";
import StatusBar, { ModelLoadingState } from "./components/StatusBar";
import {
  DEFAULT_TOOL_TOGGLES,
  ToolTogglesState,
} from "./components/ToolToggles";
import { getSettings, listProjects, createProject } from "./api/client";

export type AppView = "home-chat" | "projects" | "project-workspace";

const HOME_PROJECT_NAME = "__home__";

export default function App() {
  const [view, setView] = useState<AppView>("home-chat");
  const [homeProjectId, setHomeProjectId] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [sourcesVersion, setSourcesVersion] = useState(0);
  const [threadsVersion, setThreadsVersion] = useState(0);

  const [modelOverride, setModelOverride] = useState<string | null>(null);
  const [toolToggles, setToolToggles] = useState<ToolTogglesState>(DEFAULT_TOOL_TOGGLES);
  const [modelLoading, setModelLoading] = useState<ModelLoadingState | null>(null);
  const [ollamaNameToAlias, setOllamaNameToAlias] = useState<Record<string, string>>({});
  const [debugTraceOpen, setDebugTraceOpen] = useState(false);
  const [sseTraceEnabled, setSseTraceEnabled] = useState(false);

  const notifySourcesChanged = useCallback(
    () => setSourcesVersion((v) => v + 1),
    [],
  );
  const notifyThreadsChanged = useCallback(
    () => setThreadsVersion((v) => v + 1),
    [],
  );

  useEffect(() => {
    getSettings()
      .then((s) => {
        const reverse: Record<string, string> = {};
        for (const [alias, ollamaName] of Object.entries(s.ollama_model_names)) {
          reverse[ollamaName] = alias;
        }
        setOllamaNameToAlias(reverse);
        setSseTraceEnabled(Boolean(s.debug?.sse_trace));
      })
      .catch(() => {
        // mapping is optional for display
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await listProjects();
        let home = list.find((p) => p.name === HOME_PROJECT_NAME);
        if (!home) {
          home = await createProject(HOME_PROJECT_NAME);
        }
        if (!cancelled) {
          setHomeProjectId(home.id);
        }
      } catch {
        // home project bootstrap is best-effort
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const resetConversationState = useCallback(() => {
    setModelOverride(null);
    setToolToggles(DEFAULT_TOOL_TOGGLES);
    setModelLoading(null);
  }, []);

  const activeProjectId =
    view === "home-chat"
      ? homeProjectId
      : view === "project-workspace"
        ? selectedProject
        : null;

  const handleNavChange = (next: AppView) => {
    setView(next);
    if (next === "projects") {
      setThreadId(null);
      resetConversationState();
    }
    if (next === "home-chat") {
      resetConversationState();
    }
  };

  const handleSelectProject = (id: string) => {
    setSelectedProject(id);
    setView("project-workspace");
    setThreadId(null);
    resetConversationState();
  };

  const handleThreadSelect = (id: string | null) => {
    setThreadId(id);
    resetConversationState();
  };

  const handleModelLoading = useCallback(
    (payload: { model: string; estimated_seconds: number }) => {
      const display =
        ollamaNameToAlias[payload.model] ?? payload.model;
      setModelLoading({
        model: display,
        estimated_seconds: payload.estimated_seconds,
      });
    },
    [ollamaNameToAlias],
  );

  const handleClearModelLoading = useCallback(() => {
    setModelLoading(null);
  }, []);

  const settingsButton = (
    <div style={styles.settingsFooter}>
      <button
        style={styles.settingsBtn}
        onClick={() => setShowSettings(true)}
        title="Settings"
      >
        ⚙ Settings
      </button>
    </div>
  );

  return (
    <div style={styles.app}>
      <div style={styles.main}>
        {view === "projects" ? (
          <ProjectGrid
            onSelectProject={handleSelectProject}
            threadsVersion={threadsVersion}
          />
        ) : (
          <>
            <div style={styles.left}>
              <ProjectSidebar
                view={view}
                projectId={activeProjectId}
                onNavChange={handleNavChange}
                threadId={threadId}
                onThreadSelect={handleThreadSelect}
                onThreadsChange={notifyThreadsChanged}
                threadsVersion={threadsVersion}
              />
              {view === "project-workspace" && (
                <SourcesPanel
                  projectId={selectedProject}
                  onSourcesChange={notifySourcesChanged}
                />
              )}
              {view === "home-chat" && settingsButton}
            </div>

            <div
              style={{
                ...styles.center,
                ...(view === "home-chat" ? styles.centerNoRight : {}),
              }}
            >
              <ChatView
                projectId={activeProjectId}
                threadId={threadId}
                sourcesVersion={sourcesVersion}
                threadsVersion={threadsVersion}
                onThreadsChange={notifyThreadsChanged}
                modelOverride={modelOverride}
                onModelOverrideChange={setModelOverride}
                toolToggles={toolToggles}
                onToolTogglesChange={setToolToggles}
                onModelLoading={handleModelLoading}
                onClearModelLoading={handleClearModelLoading}
                debugTraceOpen={debugTraceOpen}
                sseTraceEnabled={sseTraceEnabled}
              />
            </div>

            {view === "project-workspace" && (
              <div style={styles.right}>
                <InstructionsPanel projectId={selectedProject} />
                {settingsButton}
              </div>
            )}
          </>
        )}
      </div>

      <StatusBar
        modelLoading={modelLoading}
        {...(sseTraceEnabled
          ? {
              debugTraceOpen,
              onToggleDebugTrace: () => setDebugTraceOpen((v) => !v),
            }
          : {})}
      />

      {showSettings && (
        <SettingsModal
          onClose={() => setShowSettings(false)}
          onSaved={(s) => {
            const enabled = Boolean(s.debug?.sse_trace);
            setSseTraceEnabled(enabled);
            if (!enabled) setDebugTraceOpen(false);
          }}
        />
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  app: {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    background: "var(--bg-app)",
    overflow: "hidden",
  },
  main: {
    display: "flex",
    flex: 1,
    overflow: "hidden",
  },
  left: {
    width: 280,
    flexShrink: 0,
    minWidth: 240,
    background: "var(--bg-sidebar)",
    borderRight: "1px solid var(--border)",
    display: "flex",
    flexDirection: "column",
    minHeight: 0,
    overflow: "hidden",
  },
  center: {
    flex: 1,
    background: "var(--bg-panel)",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    borderRight: "1px solid var(--border)",
  },
  centerNoRight: {
    borderRight: "none",
  },
  right: {
    width: 300,
    flexShrink: 0,
    background: "var(--bg-panel)",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  settingsFooter: {
    flexShrink: 0,
    padding: "10px 12px",
    borderTop: "1px solid var(--border)",
  },
  settingsBtn: {
    width: "100%",
    padding: "8px 0",
    borderRadius: "var(--radius-sm)",
    background: "var(--bg-hover)",
    color: "var(--text-muted)",
    fontSize: 12,
    fontWeight: 500,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  },
};
