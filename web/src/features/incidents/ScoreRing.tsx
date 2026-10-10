import { ru } from "../../i18n/ru";

const RADIUS = 34;
const LENGTH = 2 * Math.PI * RADIUS;

export function ScoreRing({ score }: { score: number }) {
  const clamped = Math.max(0, Math.min(100, score));
  return (
    <svg width="88" height="88" viewBox="0 0 88 88" role="img" aria-label={ru.incidents.detail.scoreRing(clamped)}>
      <circle cx="44" cy="44" r={RADIUS} fill="none" stroke="var(--surface-2)" strokeWidth="7" />
      <circle
        cx="44" cy="44" r={RADIUS} fill="none" stroke="var(--accent)" strokeWidth="7" strokeLinecap="round"
        strokeDasharray={LENGTH} strokeDashoffset={LENGTH * (1 - clamped / 100)}
        transform="rotate(-90 44 44)" style={{ transition: "stroke-dashoffset var(--dur-slow) var(--ease-out)" }}
      />
      <text x="44" y="50" textAnchor="middle" fontSize="22" fontWeight="600" fill="var(--text)">
        {clamped}
      </text>
    </svg>
  );
}
