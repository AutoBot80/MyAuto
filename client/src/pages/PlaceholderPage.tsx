interface PlaceholderPageProps {
  title?: string;
  message?: string;
}

export function PlaceholderPage({
  message = "Coming soon.",
}: PlaceholderPageProps) {
  return (
    <div className="app-placeholder">
      <p>{message}</p>
    </div>
  );
}
