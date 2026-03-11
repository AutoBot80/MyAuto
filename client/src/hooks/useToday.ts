import { useState } from "react";

export function useToday(): string {
  const [today] = useState(() =>
    new Date().toLocaleDateString("en-US", {
      weekday: "short",
      year: "numeric",
      month: "short",
      day: "numeric",
    })
  );
  return today;
}
