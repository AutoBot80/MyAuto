interface StatusMessageProps {
  message: string;
  className?: string;
  role?: "status" | "alert";
}

function parseStatusMessage(message: string): { lead: string; bullets: string[] } {
  const text = String(message || "").trim();
  if (!text) return { lead: "", bullets: [] };

  const missingMatch = text.match(/^(.+?)\s+Missing:\s+(.+?)\.?$/i);
  if (missingMatch) {
    const lead = missingMatch[1].trim();
    const bullets = missingMatch[2]
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    return { lead, bullets };
  }

  const parts = text
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length > 1) {
    return { lead: parts[0], bullets: parts.slice(1) };
  }

  return { lead: text, bullets: [] };
}

export function StatusMessage({ message, className, role = "status" }: StatusMessageProps) {
  const parsed = parseStatusMessage(message);
  return (
    <div className={className} role={role}>
      {parsed.lead}
      {parsed.bullets.length > 0 && (
        <ul style={{ marginTop: 6, marginBottom: 0, paddingLeft: 18 }}>
          {parsed.bullets.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
