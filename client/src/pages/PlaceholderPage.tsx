interface PlaceholderPageProps {
  title: string;
  message?: string;
}

export function PlaceholderPage({
  title,
  message = "Coming soon.",
}: PlaceholderPageProps) {
  return (
    <div className="app-placeholder">
      <h2>{title}</h2>
      <p>{message}</p>
    </div>
  );
}
