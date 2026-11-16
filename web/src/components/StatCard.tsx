export default function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card p-4">
      <div className="muted text-xs">{label}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
      {hint && <div className="muted text-xs mt-1">{hint}</div>}
    </div>
  );
}
