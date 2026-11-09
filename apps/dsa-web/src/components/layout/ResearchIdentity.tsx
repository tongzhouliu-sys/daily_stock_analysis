export function ResearchMark() {
  return (
    <svg viewBox="0 0 36 36" className="research-mark" aria-hidden="true">
      <rect width="36" height="36" fill="currentColor" />
      <path d="M8 26h20M10 22v-5h4v5m2 0V13h4v9m2 0V9h4v13" fill="none" stroke="var(--research-mark-ink)" strokeWidth="1.5" />
    </svg>
  );
}

export function ResearchPattern() {
  return (
    <svg className="research-pattern" viewBox="0 0 380 220" preserveAspectRatio="xMaxYMid meet" fill="none" aria-hidden="true">
      <path className="research-pattern-grid" d="M0 35h380M0 78h380M0 121h380M0 164h380M0 207h380M30 0v220M90 0v220M150 0v220M210 0v220M270 0v220M330 0v220" />
      <path className="research-pattern-outline" d="M36 174h60v-34h60v-34h60V72h60V38h60M36 189h75v-34h60v-34h60V87h60V53h60M36 204h90v-34h60v-34h60v-34h60V68h60" />
      <path className="research-pattern-accent" d="M36 159h45v-34h60V91h60V57h60V23h75" />
      <path className="research-pattern-pins" d="M76 120h10v10H76zM196 52h10v10h-10zM331 18h10v10h-10z" />
    </svg>
  );
}
