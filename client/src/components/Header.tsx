import type { ReactNode } from "react";

interface HeaderProps {
  title: string;
  leftSlot?: ReactNode;
  rightSlot: ReactNode;
}

export function Header({ title, leftSlot, rightSlot }: HeaderProps) {
  return (
    <header className="app-topbar">
      {leftSlot ? <div className="app-topbar-left">{leftSlot}</div> : null}
      <div className="app-topbar-spacer" />
      <h1 className="app-topbar-title">{title}</h1>
      <div className="app-topbar-spacer" />
      <div className="app-topbar-date">{rightSlot}</div>
    </header>
  );
}
