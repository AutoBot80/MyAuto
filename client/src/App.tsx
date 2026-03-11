import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import "./App.css";

type Dealer = {
  id: number;
  name: string;
  city: string | null;
};

function todayFormatted(): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function App() {
  const [dealers, setDealers] = useState<Dealer[]>([]);
  const [name, setName] = useState("");
  const [city, setCity] = useState("");
  const [today] = useState(todayFormatted);

  async function loadDealers() {
    const res = await fetch("http://127.0.0.1:8000/dealers");
    const data = await res.json();
    setDealers(data);
  }
  useEffect(() => {
    loadDealers();
  }, []);
  async function handleAdd(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    await fetch("http://127.0.0.1:8000/dealers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, city: city || null }),
    });
    setName("");
    setCity("");
    await loadDealers();
  }
  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-spacer" />
        <h1 className="app-header-title">Arya Agencies</h1>
        <div className="app-header-date">{today}</div>
      </header>
      <main className="app-main">
      <h2>Dealers</h2>
      <form onSubmit={handleAdd} style={{ marginBottom: "1.5rem" }}>
        <div style={{ marginBottom: "0.5rem" }}>
          <label>
            Name{" "}
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </label>
        </div>
        <div style={{ marginBottom: "0.5rem" }}>
          <label>
            City{" "}
            <input
              value={city}
              onChange={(e) => setCity(e.target.value)}
            />
          </label>
        </div>
        <button type="submit">Add Dealer</button>
      </form>
      <ul>
        {dealers.map((d) => (
          <li key={d.id}>
            {d.name} {d.city ? `(${d.city})` : ""}
          </li>
        ))}
      </ul>
      </main>
    </div>
  );
}
export default App;