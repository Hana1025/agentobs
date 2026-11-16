import { NavLink, Outlet } from "react-router-dom";
import { useMe } from "@/api/hooks";
import { cn, GRAFANA_URL } from "@/lib/utils";

const links = [
  { to: "/", label: "总览", end: true },
  { to: "/traces", label: "Traces" },
  { to: "/review", label: "复核队列" },
  { to: "/eval", label: "评测" },
];

export default function Layout() {
  const me = useMe();
  return (
    <div className="min-h-screen">
      <header className="flex items-center gap-6 px-4 h-12 border-b" style={{ borderColor: "var(--border)", background: "var(--card)" }}>
        <span className="font-semibold">agentobs</span>
        <nav className="flex gap-4">
          {links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) => cn("muted hover:underline", isActive && "font-semibold")}
              style={({ isActive }) => (isActive ? { color: "var(--fg)" } : undefined)}
            >
              {l.label}
            </NavLink>
          ))}
          <a className="muted hover:underline" href={GRAFANA_URL} target="_blank" rel="noreferrer">
            Grafana ↗
          </a>
        </nav>
        <span className="ml-auto muted text-xs" data-testid="current-user">
          {me.data?.user ?? "…"}
        </span>
      </header>
      <main className="p-4 max-w-[1400px] mx-auto">
        <Outlet />
      </main>
    </div>
  );
}
