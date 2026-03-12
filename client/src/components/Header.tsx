import type { ReactNode } from "react";

interface HeaderProps {
  title: string;
  subtitle?: string | null;
  leftSlot?: ReactNode;
  rightSlot: ReactNode;
}

export function Header({ title, subtitle, leftSlot, rightSlot }: HeaderProps) {
  return (
    <header className="app-topbar">
      {leftSlot ? <div className="app-topbar-left">{leftSlot}</div> : null}
      <div className="app-topbar-spacer" />
      <div className="app-topbar-title-block">
        <h1 className="app-topbar-title">{title}</h1>
        {subtitle ? <span className="app-topbar-subtitle">{subtitle}</span> : null}
      </div>
      <div className="app-topbar-spacer" />
      <div className="app-topbar-date">{rightSlot}</div>
    </header>
  );
}
