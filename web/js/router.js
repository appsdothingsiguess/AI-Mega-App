/** Hash router: #/ | #/chat/:id → chat; #/debug | #/debug?trace= → debug. */
const views = new Map();
let current = null;
let host = null;
let onRoute = null;
export function registerView(name, factory) {
    views.set(name, factory);
}
export function parseHash(hash = location.hash) {
    const raw = hash.replace(/^#/, "") || "/";
    const qIdx = raw.indexOf("?");
    const path = qIdx >= 0 ? raw.slice(0, qIdx) : raw;
    const query = qIdx >= 0 ? raw.slice(qIdx + 1) : "";
    const params = new URLSearchParams(query);
    const parts = path.split("/").filter(Boolean);
    if (parts[0] === "debug") {
        return { name: "debug", chatId: null, traceId: params.get("trace") };
    }
    if (parts[0] === "chat" && parts[1]) {
        return { name: "chat", chatId: parts[1], traceId: null };
    }
    return { name: "chat", chatId: null, traceId: null };
}
export function navigate(hash) {
    const next = hash.startsWith("#") ? hash : `#${hash}`;
    if (location.hash === next) {
        void applyRoute(parseHash(next));
        return;
    }
    location.hash = next;
}
/** Update the hash URL without remounting (no hashchange). Use when the
 *  active view must keep running — e.g. create-chat mid-send before SSE. */
export function replaceHash(hash) {
    const next = hash.startsWith("#") ? hash : `#${hash}`;
    if (location.hash === next)
        return;
    history.replaceState(null, "", next);
}
async function applyRoute(route) {
    if (!host)
        return;
    if (current) {
        current.unmount();
        current = null;
    }
    host.replaceChildren();
    const factory = views.get(route.name);
    if (!factory) {
        host.textContent = `Unknown view: ${route.name}`;
        return;
    }
    current = factory();
    await current.mount(host, route);
    onRoute?.(route);
}
export function startRouter(el, opts) {
    host = el;
    onRoute = opts?.onRoute ?? null;
    window.addEventListener("hashchange", () => {
        void applyRoute(parseHash());
    });
    void applyRoute(parseHash());
}
