/** Tiny pub/sub store (~150 lines). */
const initial = {
    sidebarCollapsed: false,
    chats: [],
    activeChatId: null,
    modelOverride: null,
    healthModels: [],
    lastTraceId: null,
    activeChatStreaming: false,
};
let state = { ...initial };
const listeners = new Set();
export function get() {
    return state;
}
export function set(patch) {
    state = { ...state, ...patch };
    for (const fn of listeners)
        fn(state);
    return state;
}
export function subscribe(fn) {
    listeners.add(fn);
    return () => {
        listeners.delete(fn);
    };
}
export function resetStore() {
    state = { ...initial };
    for (const fn of listeners)
        fn(state);
}
