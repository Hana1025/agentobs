import { useState } from "react";
import { SHOW_CONTENT } from "@/lib/utils";

const CONTENT_PREFIX = "agentobs.content.";

function Node({ k, v, depth }: { k: string; v: unknown; depth: number }) {
  const isContent = k.startsWith(CONTENT_PREFIX);
  const [open, setOpen] = useState(!isContent && depth < 1);
  const isObj = v !== null && typeof v === "object";

  if (isContent) {
    return (
      <div className="pl-2">
        <span className="muted">{k}</span>:{" "}
        {!SHOW_CONTENT ? (
          <span className="muted italic">（正文显示已在构建时关闭）</span>
        ) : open ? (
          <pre className="whitespace-pre-wrap break-all text-xs mt-1 p-2 card">{String(v)}</pre>
        ) : (
          <button className="btn text-xs" onClick={() => setOpen(true)}>
            显示正文
          </button>
        )}
      </div>
    );
  }
  if (!isObj) {
    return (
      <div className="pl-2">
        <span className="muted">{k}</span>: <span className="break-all">{JSON.stringify(v)}</span>
      </div>
    );
  }
  const entries = Array.isArray(v) ? v.map((x, i) => [String(i), x] as const) : Object.entries(v as object);
  return (
    <div className="pl-2">
      <button className="muted" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} {k} <span className="text-xs">({entries.length})</span>
      </button>
      {open && entries.map(([ck, cv]) => <Node key={ck} k={ck} v={cv} depth={depth + 1} />)}
    </div>
  );
}

export default function JsonTree({ value }: { value: Record<string, unknown> | undefined | null }) {
  if (!value) return <span className="muted">–</span>;
  return (
    <div className="font-mono text-xs leading-5">
      {Object.entries(value)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => (
          <Node key={k} k={k} v={v} depth={0} />
        ))}
    </div>
  );
}
