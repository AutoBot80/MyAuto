import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createAdminDealer,
  createAdminDealerDiscount,
  getAdminDealerDetail,
  getAdminDealerDiscounts,
  getAdminDealerLogins,
  getAdminDealerNames,
  getAdminLoginCatalog,
  getAdminRoles,
  patchAdminDealerInsurerCpi,
  upsertLoginAssignments,
  type DealerLoginAssignmentRow,
  type JsonRecord,
  type SubdealerDiscountRow,
} from "../api/adminDealers";
import "./AdminDealersPage.css";

type SubTab = "logins" | "discounts";

type DiscountDraftRow = {
  tempId: string;
  model: string;
  discount: string;
  create_date: string;
  valid_flag: "Y" | "N";
};

function newDiscountDraft(): DiscountDraftRow {
  const tempId =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `draft-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
  return {
    tempId,
    model: "",
    discount: "",
    create_date: "",
    valid_flag: "Y",
  };
}

type LoginDraftRow = {
  tempId: string;
  login_id: string;
  role_id: number | "";
  active_flag: "Y" | "N";
};

function newLoginDraft(): LoginDraftRow {
  const tempId =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `ld-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
  return {
    tempId,
    login_id: "",
    role_id: "",
    active_flag: "Y",
  };
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** Sub-dealer when ``parent_id`` is set on ``dealer_ref`` (not shown as its own row). */
function isSubDealerDetail(detail: JsonRecord): boolean {
  const p = detail.parent_id;
  return p != null && p !== "" && Number(p) > 0;
}

function normalizeHeroCpi(raw: unknown): "Y" | "N" {
  return raw === "Y" ? "Y" : "N";
}

function normalizeLoginActive(raw: unknown): "Y" | "N" {
  return raw === "Y" ? "Y" : "N";
}

/** POS role in ``roles_ref`` is shown as the Sales Window tile label. */
function displayRoleName(roleName: string | null | undefined): string {
  if (roleName == null || String(roleName).trim() === "") return "—";
  const t = String(roleName).trim();
  if (t.toUpperCase() === "POS") return "Sales Window";
  return t;
}

export function AdminDealersPage() {
  const [dealerRows, setDealerRows] = useState<{ dealer_id: number; dealer_name: string }[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<JsonRecord | null>(null);
  const [logins, setLogins] = useState<DealerLoginAssignmentRow[]>([]);
  const [discounts, setDiscounts] = useState<SubdealerDiscountRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [subTab, setSubTab] = useState<SubTab>("logins");
  const [addOpen, setAddOpen] = useState(false);
  const [addName, setAddName] = useState("");
  const [addOemId, setAddOemId] = useState("");
  const [addParentId, setAddParentId] = useState("");
  const [preferInsurerEdit, setPreferInsurerEdit] = useState("");
  const [heroCpiEdit, setHeroCpiEdit] = useState<"Y" | "N">("N");
  const [saveDetailError, setSaveDetailError] = useState<string | null>(null);
  const [savingDetail, setSavingDetail] = useState(false);
  const [rolesCatalog, setRolesCatalog] = useState<{ role_id: number; role_name: string }[]>([]);
  const [loginCatalog, setLoginCatalog] = useState<{ login_id: string; display_name: string | null }[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [loginDrafts, setLoginDrafts] = useState<LoginDraftRow[]>([]);
  const [editRoleByLrrId, setEditRoleByLrrId] = useState<Record<number, number>>({});
  const [activeByLoginId, setActiveByLoginId] = useState<Record<string, "Y" | "N">>({});
  const [loginDirty, setLoginDirty] = useState(false);
  const [loginSaveError, setLoginSaveError] = useState<string | null>(null);
  const [savingLogins, setSavingLogins] = useState(false);
  const [discountDrafts, setDiscountDrafts] = useState<DiscountDraftRow[]>([]);
  const [discountSaveError, setDiscountSaveError] = useState<string | null>(null);
  const [savingDiscounts, setSavingDiscounts] = useState(false);

  const refreshNames = useCallback(() => {
    setLoadError(null);
    return getAdminDealerNames()
      .then((rows) => {
        setDealerRows(rows);
        setSelectedId((prev) => {
          if (prev != null && rows.some((r) => r.dealer_id === prev)) return prev;
          return rows.length ? rows[0].dealer_id : null;
        });
      })
      .catch((e) => {
        setLoadError(e instanceof Error ? e.message : "Could not load dealers.");
      });
  }, []);

  useEffect(() => {
    void refreshNames();
  }, [refreshNames]);

  useEffect(() => {
    if (selectedId == null) {
      setDetail(null);
      setLogins([]);
      setDiscounts([]);
      return;
    }
    setBusy(true);
    setLoadError(null);
    Promise.all([
      getAdminDealerDetail(selectedId),
      getAdminDealerLogins(selectedId),
      getAdminDealerDiscounts(selectedId),
    ])
      .then(([d, lg, disc]) => {
        setDetail(d);
        setLogins(lg);
        setDiscounts(disc);
      })
      .catch((e) => {
        setLoadError(e instanceof Error ? e.message : "Could not load dealer data.");
        setDetail(null);
        setLogins([]);
        setDiscounts([]);
      })
      .finally(() => setBusy(false));
  }, [selectedId]);

  const refreshDiscounts = useCallback(async () => {
    if (selectedId == null) return;
    const rows = await getAdminDealerDiscounts(selectedId);
    setDiscounts(rows);
  }, [selectedId]);

  useEffect(() => {
    setDiscountDrafts([]);
    setDiscountSaveError(null);
    setLoginDrafts([]);
    setLoginSaveError(null);
    setLoginDirty(false);
    setEditRoleByLrrId({});
    setActiveByLoginId({});
  }, [selectedId]);

  useEffect(() => {
    const m: Record<string, "Y" | "N"> = {};
    for (const r of logins) {
      if (!(r.login_id in m)) {
        m[r.login_id] = normalizeLoginActive(r.login_active_flag);
      }
    }
    setActiveByLoginId(m);
    setEditRoleByLrrId({});
  }, [logins]);

  useEffect(() => {
    if (subTab !== "logins") return;
    let cancelled = false;
    setCatalogError(null);
    Promise.all([getAdminRoles(), getAdminLoginCatalog()])
      .then(([roles, logins]) => {
        if (!cancelled) {
          setRolesCatalog(roles);
          setLoginCatalog(logins);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setCatalogError(e instanceof Error ? e.message : "Could not load roles or logins list.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [subTab]);

  useEffect(() => {
    if (!detail) {
      setPreferInsurerEdit("");
      setHeroCpiEdit("N");
      return;
    }
    const pi = detail.prefer_insurer;
    setPreferInsurerEdit(pi != null && pi !== "" ? String(pi) : "");
    setHeroCpiEdit(normalizeHeroCpi(detail.hero_cpi));
  }, [detail]);

  const detailRows = useMemo(() => {
    if (!detail) return [];
    const sub = isSubDealerDetail(detail);
    const rows: { key: string; label: string }[] = [
      { key: "dealer_name", label: sub ? "Sub-Dealer Name" : "Dealer Name" },
      { key: "address", label: "Address" },
      { key: "pin", label: "PIN" },
      { key: "phone", label: "Phone" },
      { key: "rto_name", label: "RTO" },
      { key: "oem_name", label: "OEM Name" },
    ];
    if (sub) {
      rows.push({ key: "parent_name", label: "Dealer Name" });
    }
    rows.push(
      { key: "prefer_insurer", label: "Prefered Insurance" },
      { key: "hero_cpi", label: "Hero CPI" }
    );
    return rows.map(({ key, label }) => ({
      key,
      label,
      value: detail[key],
    }));
  }, [detail]);

  const detailDirty = useMemo(() => {
    if (!detail || selectedId == null) return false;
    const savedPi = detail.prefer_insurer != null && detail.prefer_insurer !== "" ? String(detail.prefer_insurer) : "";
    const savedHero = normalizeHeroCpi(detail.hero_cpi);
    return preferInsurerEdit !== savedPi || heroCpiEdit !== savedHero;
  }, [detail, selectedId, preferInsurerEdit, heroCpiEdit]);

  function addLoginDraftRow() {
    setLoginDrafts((prev) => [...prev, newLoginDraft()]);
    setLoginSaveError(null);
    setLoginDirty(true);
  }

  function removeLoginDraft(tempId: string) {
    setLoginDrafts((prev) => prev.filter((r) => r.tempId !== tempId));
    setLoginSaveError(null);
  }

  function updateLoginDraft(tempId: string, patch: Partial<LoginDraftRow>) {
    setLoginDrafts((prev) => prev.map((r) => (r.tempId === tempId ? { ...r, ...patch } : r)));
    setLoginDirty(true);
  }

  async function handleSaveLoginAssignments() {
    if (selectedId == null || !loginDirty) return;
    for (const d of loginDrafts) {
      if (!d.login_id.trim() || d.role_id === "" || Number.isNaN(Number(d.role_id))) {
        setLoginSaveError("Each new row must have a login and a role.");
        return;
      }
    }
    setLoginSaveError(null);
    setSavingLogins(true);
    try {
      const rows = [
        ...logins.map((r) => ({
          login_roles_ref_id: r.login_roles_ref_id,
          login_id: r.login_id,
          role_id: editRoleByLrrId[r.login_roles_ref_id] ?? r.role_id,
          active_flag: activeByLoginId[r.login_id] ?? normalizeLoginActive(r.login_active_flag),
        })),
        ...loginDrafts.map((d) => ({
          login_roles_ref_id: null as number | null,
          login_id: d.login_id.trim(),
          role_id: Number(d.role_id),
          active_flag: d.active_flag,
        })),
      ];
      const updated = await upsertLoginAssignments(selectedId, { rows });
      setLogins(updated);
      setLoginDrafts([]);
      setLoginDirty(false);
    } catch (e) {
      setLoginSaveError(e instanceof Error ? e.message : "Could not save login assignments.");
    } finally {
      setSavingLogins(false);
    }
  }

  const canSaveLogins =
    loginDirty &&
    selectedId != null &&
    !savingLogins &&
    !busy &&
    loginDrafts.every(
      (d) => d.login_id.trim() !== "" && d.role_id !== "" && !Number.isNaN(Number(d.role_id))
    );

  function addDiscountDraftRow() {
    setDiscountDrafts((prev) => [...prev, newDiscountDraft()]);
    setDiscountSaveError(null);
  }

  function removeDiscountDraft(tempId: string) {
    setDiscountDrafts((prev) => prev.filter((r) => r.tempId !== tempId));
    setDiscountSaveError(null);
  }

  function updateDiscountDraft(tempId: string, patch: Partial<DiscountDraftRow>) {
    setDiscountDrafts((prev) => prev.map((r) => (r.tempId === tempId ? { ...r, ...patch } : r)));
  }

  async function handleSaveDiscountDrafts() {
    if (selectedId == null || discountDrafts.length === 0) return;
    for (const d of discountDrafts) {
      if (!d.model.trim()) {
        setDiscountSaveError("Each new row must have a model.");
        return;
      }
      const ds = d.discount.trim();
      if (ds !== "") {
        const n = Number(ds);
        if (Number.isNaN(n) || n < 0) {
          setDiscountSaveError("Discount must be a non-negative number.");
          return;
        }
      }
    }
    setDiscountSaveError(null);
    setSavingDiscounts(true);
    try {
      for (const d of discountDrafts) {
        const ds = d.discount.trim();
        const discountNum = ds === "" ? null : Number(ds);
        await createAdminDealerDiscount(selectedId, {
          model: d.model.trim(),
          discount: discountNum,
          create_date: d.create_date.trim() || null,
          valid_flag: d.valid_flag,
        });
      }
      setDiscountDrafts([]);
      await refreshDiscounts();
    } catch (e) {
      setDiscountSaveError(e instanceof Error ? e.message : "Could not save discounts.");
    } finally {
      setSavingDiscounts(false);
    }
  }

  const canSaveDiscountDrafts =
    discountDrafts.length > 0 &&
    discountDrafts.every((d) => d.model.trim() !== "") &&
    !savingDiscounts &&
    selectedId != null &&
    !busy;

  async function handleSaveDealerDetail() {
    if (selectedId == null || !detail) return;
    setSaveDetailError(null);
    setSavingDetail(true);
    try {
      const updated = await patchAdminDealerInsurerCpi(selectedId, {
        prefer_insurer: preferInsurerEdit.trim() || null,
        hero_cpi: heroCpiEdit,
      });
      setDetail(updated);
    } catch (e) {
      setSaveDetailError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSavingDetail(false);
    }
  }

  async function handleAddDealer() {
    const name = addName.trim();
    if (!name) {
      window.alert("Enter a dealer name.");
      return;
    }
    const oemRaw = addOemId.trim();
    const parentRaw = addParentId.trim();
    const oem_id = oemRaw === "" ? null : Number(oemRaw);
    const parent_id = parentRaw === "" ? null : Number(parentRaw);
    if (oem_id != null && Number.isNaN(oem_id)) {
      window.alert("OEM id must be a number or empty.");
      return;
    }
    if (parent_id != null && Number.isNaN(parent_id)) {
      window.alert("Parent id must be a number or empty.");
      return;
    }
    setBusy(true);
    try {
      const createdRow = await createAdminDealer({ dealer_name: name, oem_id, parent_id });
      setAddOpen(false);
      setAddName("");
      setAddOemId("");
      setAddParentId("");
      await refreshNames();
      const newId = typeof createdRow.dealer_id === "number" ? createdRow.dealer_id : null;
      if (newId != null) setSelectedId(newId);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Could not create dealer.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="view-vehicles-page admin-dealers-page">
      <section className="view-vehicles-search" aria-label="Choose dealer">
        <div className="view-vehicles-search-field">
          <label htmlFor="admin-dealers-pick">Dealer</label>
          <select
            id="admin-dealers-pick"
            value={selectedId ?? ""}
            onChange={(e) => setSelectedId(e.target.value ? Number(e.target.value) : null)}
            disabled={!dealerRows.length || busy}
            aria-label="Select dealer by name"
          >
            {dealerRows.length === 0 ? <option value="">No dealers</option> : null}
            {dealerRows.map((r) => (
              <option key={r.dealer_id} value={r.dealer_id}>
                {r.dealer_name}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          className="app-button app-button--primary view-vehicles-search-btn admin-dealers-add-btn"
          onClick={() => setAddOpen(true)}
          disabled={busy}
          title="Add dealer"
          aria-label="Add new dealer"
        >
          +
        </button>
      </section>

      {loadError ? <p className="view-vehicles-error">{loadError}</p> : null}
      {busy && selectedId != null ? <p className="view-vehicles-message">Loading…</p> : null}

      <article className="view-vehicles-match admin-dealers-card" aria-labelledby="admin-dealers-ref-heading">
        <section className="view-vehicles-section">
          <h4 id="admin-dealers-ref-heading" className="view-vehicles-section-title">
            Dealer reference
          </h4>
          {saveDetailError ? <p className="view-vehicles-error">{saveDetailError}</p> : null}
          {detail ? (
            <>
              <div className="view-vehicles-kv-grid">
                {detailRows.map(({ key, value, label }) => (
                  <div key={key} className="view-vehicles-kv-item">
                    <span className="view-vehicles-kv-label" id={`admin-dealers-k-${key}-l`}>
                      {label}
                    </span>
                    <div className="view-vehicles-kv-value" aria-labelledby={`admin-dealers-k-${key}-l`}>
                      {key === "prefer_insurer" ? (
                        <input
                          type="text"
                          className="view-vehicles-kv-input"
                          value={preferInsurerEdit}
                          onChange={(e) => setPreferInsurerEdit(e.target.value)}
                          disabled={busy || savingDetail}
                          autoComplete="off"
                          aria-label="Prefered Insurance"
                        />
                      ) : key === "hero_cpi" ? (
                        <select
                          className="view-vehicles-kv-select"
                          value={heroCpiEdit}
                          onChange={(e) => setHeroCpiEdit(e.target.value === "Y" ? "Y" : "N")}
                          disabled={busy || savingDetail}
                          aria-label="Hero CPI"
                        >
                          <option value="N">N</option>
                          <option value="Y">Y</option>
                        </select>
                      ) : (
                        formatCell(value)
                      )}
                    </div>
                  </div>
                ))}
              </div>
              <div className="admin-dealers-save-row">
                <button
                  type="button"
                  className="app-button app-button--primary"
                  disabled={busy || savingDetail || !detailDirty}
                  onClick={() => void handleSaveDealerDetail()}
                >
                  {savingDetail ? "Saving…" : "Save changes"}
                </button>
                {detailDirty ? <span className="admin-dealers-unsaved">Unsaved changes</span> : null}
              </div>
            </>
          ) : (
            <p className="view-vehicles-empty">{selectedId == null ? "Select or add a dealer." : "No row loaded."}</p>
          )}
        </section>
      </article>

      <article className="view-vehicles-match admin-dealers-card" aria-label="Dealer assignments and discounts">
      <div className="admin-dealers-subtabs" role="tablist" aria-label="Dealer details">
        <button
          type="button"
          role="tab"
          aria-selected={subTab === "logins"}
          className={`admin-dealers-subtab ${subTab === "logins" ? "active" : ""}`}
          onClick={() => setSubTab("logins")}
        >
          Logins
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={subTab === "discounts"}
          className={`admin-dealers-subtab ${subTab === "discounts" ? "active" : ""}`}
          onClick={() => setSubTab("discounts")}
        >
          Discounts
        </button>
      </div>

      {subTab === "logins" ? (
        <section className="view-vehicles-section" aria-label="Login and role assignments for dealer">
          <p className="view-vehicles-hint">
            Assignments in <code>login_roles_ref</code> for this dealer, with <code>login_ref</code> contact fields.
            Active flag is stored on <code>login_ref</code> (applies to all rows for that login). Use <strong>Add</strong> for new
            rows, then <strong>Save changes</strong> to store them.
          </p>
          {catalogError ? <p className="view-vehicles-error">{catalogError}</p> : null}
          {loginSaveError ? <p className="view-vehicles-error">{loginSaveError}</p> : null}
          <div className="view-vehicles-search admin-dealers-discount-toolbar">
            <button
              type="button"
              className="app-button app-button--primary view-vehicles-search-btn"
              onClick={addLoginDraftRow}
              disabled={busy || savingLogins || selectedId == null}
            >
              Add
            </button>
            <button
              type="button"
              className="app-button app-button--primary view-vehicles-search-btn"
              onClick={() => void handleSaveLoginAssignments()}
              disabled={!canSaveLogins}
            >
              {savingLogins ? "Saving…" : "Save changes"}
            </button>
            {loginDirty ? <span className="admin-dealers-unsaved">Unsaved changes</span> : null}
          </div>
          <div className="app-table-wrap">
            <table className="view-vehicles-table admin-dealers-wide-table">
              <thead>
                <tr>
                  <th>Login id</th>
                  <th>Name</th>
                  <th>Phone</th>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Active</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {logins.length === 0 && loginDrafts.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="view-vehicles-table-empty">
                      No login rows for this dealer.
                    </td>
                  </tr>
                ) : null}
                {logins.map((row) => {
                  const roleVal = editRoleByLrrId[row.login_roles_ref_id] ?? row.role_id;
                  const activeVal =
                    activeByLoginId[row.login_id] ?? normalizeLoginActive(row.login_active_flag);
                  return (
                    <tr key={row.login_roles_ref_id}>
                      <td>{row.login_id}</td>
                      <td>{formatCell(row.login_display_name)}</td>
                      <td>{formatCell(row.login_phone)}</td>
                      <td>{formatCell(row.login_email)}</td>
                      <td>
                        <select
                          className="view-vehicles-kv-select admin-dealers-active-select"
                          value={String(roleVal)}
                          onChange={(e) => {
                            const v = Number(e.target.value);
                            if (!Number.isNaN(v)) {
                              setEditRoleByLrrId((prev) => ({ ...prev, [row.login_roles_ref_id]: v }));
                              setLoginDirty(true);
                            }
                          }}
                          disabled={busy || savingLogins}
                          aria-label={`Role for ${row.login_id}`}
                        >
                          {rolesCatalog.map((r) => (
                            <option key={r.role_id} value={String(r.role_id)}>
                              {displayRoleName(r.role_name)}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <select
                          className="view-vehicles-kv-select admin-dealers-active-select"
                          value={activeVal}
                          onChange={(e) => {
                            const v = e.target.value === "Y" ? "Y" : "N";
                            setActiveByLoginId((prev) => ({ ...prev, [row.login_id]: v }));
                            setLoginDirty(true);
                          }}
                          disabled={busy || savingLogins}
                          aria-label={`Active for ${row.login_id}`}
                        >
                          <option value="Y">Y</option>
                          <option value="N">N</option>
                        </select>
                      </td>
                      <td />
                    </tr>
                  );
                })}
                {loginDrafts.map((dr) => (
                  <tr key={dr.tempId} className="admin-dealers-discount-draft-row">
                    <td>
                      <select
                        className="view-vehicles-kv-select admin-dealers-active-select"
                        value={dr.login_id}
                        onChange={(e) => updateLoginDraft(dr.tempId, { login_id: e.target.value })}
                        disabled={savingLogins}
                        aria-label="Login"
                      >
                        <option value="">Select login…</option>
                        {loginCatalog.map((l) => (
                          <option key={l.login_id} value={l.login_id}>
                            {l.login_id}
                            {l.display_name != null && String(l.display_name).trim() !== "" ? ` — ${l.display_name}` : ""}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td colSpan={3} className="view-vehicles-table-muted">
                      New assignment
                    </td>
                    <td>
                      <select
                        className="view-vehicles-kv-select admin-dealers-active-select"
                        value={dr.role_id === "" ? "" : String(dr.role_id)}
                        onChange={(e) => {
                          const raw = e.target.value;
                          if (raw === "") updateLoginDraft(dr.tempId, { role_id: "" });
                          else {
                            const n = Number(raw);
                            if (!Number.isNaN(n)) updateLoginDraft(dr.tempId, { role_id: n });
                          }
                        }}
                        disabled={savingLogins}
                        aria-label="Role"
                      >
                        <option value="">Select role…</option>
                        {rolesCatalog.map((r) => (
                          <option key={r.role_id} value={String(r.role_id)}>
                            {displayRoleName(r.role_name)}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        className="view-vehicles-kv-select admin-dealers-active-select"
                        value={dr.active_flag}
                        onChange={(e) =>
                          updateLoginDraft(dr.tempId, {
                            active_flag: e.target.value === "Y" ? "Y" : "N",
                          })
                        }
                        disabled={savingLogins}
                        aria-label="Active"
                      >
                        <option value="Y">Y</option>
                        <option value="N">N</option>
                      </select>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="app-button app-button--ghost"
                        onClick={() => removeLoginDraft(dr.tempId)}
                        disabled={savingLogins}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        <section className="view-vehicles-section" aria-label="Subdealer discounts for dealer">
          <p className="view-vehicles-hint">
            Per-dealer model discounts (<code>subdealer_discount_master</code>). Use <strong>Add</strong> for new rows, then{" "}
            <strong>Save changes</strong> to store them.
          </p>
          {discountSaveError ? <p className="view-vehicles-error">{discountSaveError}</p> : null}
          <div className="view-vehicles-search admin-dealers-discount-toolbar">
            <button
              type="button"
              className="app-button app-button--primary view-vehicles-search-btn"
              onClick={addDiscountDraftRow}
              disabled={busy || savingDiscounts || selectedId == null}
            >
              Add
            </button>
            <button
              type="button"
              className="app-button app-button--primary view-vehicles-search-btn"
              onClick={() => void handleSaveDiscountDrafts()}
              disabled={!canSaveDiscountDrafts}
            >
              {savingDiscounts ? "Saving…" : "Save changes"}
            </button>
          </div>
          <div className="app-table-wrap">
            <table className="view-vehicles-table admin-dealers-discount-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Model</th>
                  <th>Discount</th>
                  <th>Create date</th>
                  <th>Valid</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {discounts.map((row) => (
                  <tr key={row.subdealer_discount_id}>
                    <td>{row.subdealer_discount_id}</td>
                    <td>{row.model}</td>
                    <td>{row.discount != null ? String(row.discount) : "—"}</td>
                    <td>{formatCell(row.create_date)}</td>
                    <td>{row.valid_flag}</td>
                    <td />
                  </tr>
                ))}
                {discountDrafts.map((dr) => (
                  <tr key={dr.tempId} className="admin-dealers-discount-draft-row">
                    <td>—</td>
                    <td>
                      <input
                        type="text"
                        className="view-vehicles-kv-input admin-dealers-discount-cell-input"
                        value={dr.model}
                        onChange={(e) => updateDiscountDraft(dr.tempId, { model: e.target.value })}
                        disabled={savingDiscounts}
                        placeholder="Model"
                        maxLength={64}
                        aria-label="Model"
                      />
                    </td>
                    <td>
                      <input
                        type="text"
                        inputMode="decimal"
                        className="view-vehicles-kv-input admin-dealers-discount-cell-input"
                        value={dr.discount}
                        onChange={(e) => updateDiscountDraft(dr.tempId, { discount: e.target.value })}
                        disabled={savingDiscounts}
                        placeholder="Optional"
                        aria-label="Discount"
                      />
                    </td>
                    <td>
                      <input
                        type="text"
                        className="view-vehicles-kv-input admin-dealers-discount-cell-input"
                        value={dr.create_date}
                        onChange={(e) => updateDiscountDraft(dr.tempId, { create_date: e.target.value })}
                        disabled={savingDiscounts}
                        placeholder="dd/mm/yyyy"
                        aria-label="Create date"
                      />
                    </td>
                    <td>
                      <select
                        className="view-vehicles-kv-select admin-dealers-discount-cell-input"
                        value={dr.valid_flag}
                        onChange={(e) =>
                          updateDiscountDraft(dr.tempId, {
                            valid_flag: e.target.value === "Y" ? "Y" : "N",
                          })
                        }
                        disabled={savingDiscounts}
                        aria-label="Valid"
                      >
                        <option value="Y">Y</option>
                        <option value="N">N</option>
                      </select>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="app-button"
                        onClick={() => removeDiscountDraft(dr.tempId)}
                        disabled={savingDiscounts}
                        aria-label="Remove draft row"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
                {discounts.length === 0 && discountDrafts.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="view-vehicles-table-empty">
                      No discount rows yet. Click Add to create one.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>
      )}
      </article>

      {addOpen ? (
        <div className="admin-dealers-modal-backdrop" role="presentation" onClick={() => !busy && setAddOpen(false)}>
          <div
            className="admin-dealers-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="admin-dealers-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="admin-dealers-modal-title">New dealer</h3>
            <label className="admin-dealers-modal-field">
              Dealer name <span className="admin-dealers-req">*</span>
              <input
                type="text"
                value={addName}
                onChange={(e) => setAddName(e.target.value)}
                autoComplete="off"
                disabled={busy}
              />
            </label>
            <label className="admin-dealers-modal-field">
              OEM id <span className="admin-dealers-optional">(optional)</span>
              <input
                type="text"
                inputMode="numeric"
                value={addOemId}
                onChange={(e) => setAddOemId(e.target.value)}
                placeholder="e.g. 1"
                disabled={busy}
              />
            </label>
            <label className="admin-dealers-modal-field">
              Parent dealer id <span className="admin-dealers-optional">(optional)</span>
              <input
                type="text"
                inputMode="numeric"
                value={addParentId}
                onChange={(e) => setAddParentId(e.target.value)}
                placeholder="e.g. 100001"
                disabled={busy}
              />
            </label>
            <div className="admin-dealers-modal-actions">
              <button type="button" className="app-button" onClick={() => setAddOpen(false)} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="app-button app-button--primary" onClick={() => void handleAddDealer()} disabled={busy}>
                {busy ? "Saving…" : "Create"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
