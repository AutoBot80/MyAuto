import type { ReactNode } from "react";

interface HeaderProps {
  title: string;
  rightSlot: ReactNode;
}

export function Header({ title, rightSlot }: HeaderProps) {
  return (
    <header className="app-topbar">
      <div className="app-topbar-spacer" />
      <h1 className="app-topbar-title">{title}</h1>
      <div className="app-topbar-date">{rightSlot}</div>
    </header>
  );
}
