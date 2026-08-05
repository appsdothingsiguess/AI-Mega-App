/** marked → DOMPurify → highlight.js. All user/model HTML is sanitized. */

// @ts-expect-error vendored ESM, no types
import { marked } from "../vendor/marked.esm.js";
// @ts-expect-error vendored ESM, no types
import DOMPurify from "../vendor/purify.es.mjs";
// @ts-expect-error vendored ESM, no types
import hljs from "../vendor/highlight.min.js";

marked.setOptions({
  gfm: true,
  breaks: true,
});

const ALLOWED_TAGS = [
  "a",
  "b",
  "blockquote",
  "br",
  "code",
  "del",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "i",
  "li",
  "ol",
  "p",
  "pre",
  "span",
  "strong",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "ul",
];

const ALLOWED_ATTR = ["href", "title", "class", "target", "rel"];

function highlightCode(html: string): string {
  const tmp = document.createElement("div");
  tmp.innerHTML = html;
  tmp.querySelectorAll("pre code").forEach((block) => {
    const el = block as HTMLElement;
    const cls = el.className || "";
    const match = /language-([\w+-]+)/.exec(cls);
    const lang = match?.[1];
    const text = el.textContent ?? "";
    try {
      if (lang && hljs.getLanguage(lang)) {
        el.innerHTML = hljs.highlight(text, { language: lang }).value;
      } else {
        el.innerHTML = hljs.highlightAuto(text).value;
      }
      el.classList.add("hljs");
    } catch {
      /* leave plain text */
    }
  });
  return tmp.innerHTML;
}

/** Render markdown to sanitized HTML safe for innerHTML. */
export function renderMarkdown(src: string): string {
  const raw = marked.parse(src ?? "", { async: false }) as string;
  const clean = DOMPurify.sanitize(raw, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
  });
  const highlighted = highlightCode(clean);
  return DOMPurify.sanitize(highlighted, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
  });
}

/** Add a "Copy" button (top-right) to every <pre> block inside a rendered container. */
export function addCopyButtons(container: HTMLElement): void {
  container.querySelectorAll("pre").forEach((pre) => {
    const el = pre as HTMLElement;
    if (el.querySelector(".copy-code-btn")) return;
    el.style.position = "relative";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy-code-btn";
    btn.textContent = "Copy";
    btn.addEventListener("click", () => {
      const code = el.querySelector("code");
      const text = code?.textContent ?? el.textContent ?? "";
      navigator.clipboard.writeText(text).then(
        () => {
          btn.textContent = "Copied!";
          setTimeout(() => (btn.textContent = "Copy"), 1500);
        },
        () => {
          btn.textContent = "Failed";
          setTimeout(() => (btn.textContent = "Copy"), 1500);
        },
      );
    });
    el.appendChild(btn);
  });
}

/** Escape plain text for safe textContent-alternative HTML. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
