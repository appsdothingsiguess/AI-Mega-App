/** Tiny pub/sub store (~150 lines). */

export type Listener<T> = (state: T) => void;
export type Unsubscribe = () => void;

export interface AppState {
  sidebarCollapsed: boolean;
  chats: import("./types.js").ChatSummary[];
  activeChatId: string | null;
  modelOverride: string | null;
  healthModels: import("./types.js").HealthModel[];
  lastTraceId: string | null;
}

const initial: AppState = {
  sidebarCollapsed: false,
  chats: [],
  activeChatId: null,
  modelOverride: null,
  healthModels: [],
  lastTraceId: null,
};

let state: AppState = { ...initial };
const listeners = new Set<Listener<AppState>>();

export function get(): AppState {
  return state;
}

export function set(patch: Partial<AppState>): AppState {
  state = { ...state, ...patch };
  for (const fn of listeners) fn(state);
  return state;
}

export function subscribe(fn: Listener<AppState>): Unsubscribe {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function resetStore(): void {
  state = { ...initial };
  for (const fn of listeners) fn(state);
}
