import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createAdminDealer,
  createAdminDealerDiscount,
  getAdminDealerDetail,
  getAdminDealerDiscounts,
  getAdminDealerLogins,
  getAdminDealerNames,
  getAdminLoginCatalog,
  getAdminPortalInsurers,
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
  subdealer_type: string;
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
    subdealer_type: "",
    model: "",
    discount: "",
    create_date: "",
    valid_flag: "Y",
  };
}

type LoginDraftRow = {
  tempId: string;
  login_id: string;
  name: string;
  password: string;
  phone: string;
  email: string;
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
    name: "",
    password: "",
    phone: "",
    email: "",
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

function normalizeCpiReqd(raw: unknown): "Y" | "N" {
  return raw === "Y" ? "Y" : "N";
}

function normalizeInsurancePay(raw: unknown): "CC" | "APD" {
  return raw === "CC" ? "CC" : "APD";
}

function normalizeDmsSiebelPortal(raw: unknown): "HMCL" | "ASC" {
  return raw === "ASC" ? "ASC" : "HMCL";
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
  const [portalInsurers, setPortalInsurers] = useState<string[]>([]);
  const [portalInsurersError, setPortalInsurersError] = useState<string | null>(null);
  const [heroCpiEdit, setHeroCpiEdit] = useState<"Y" | "N">("N");
  const [cpiReqdEdit, setCpiReqdEdit] = useState<"Y" | "N">("N");
  const [insurancePayEdit, setInsurancePayEdit] = useState<"CC" | "APD">("APD");
  const [dmsSiebelPortalEdit, setDmsSiebelPortalEdit] = useState<"HMCL" | "ASC">("HMCL");
  const [saveDetailError, setSaveDetailError] = useState<string | null>(null);
  const [savingDetail, setSavingDetail] = useState(false);
  const [rolesCatalog, setRolesCatalog] = useState<{ role_id: number; role_name: string }[]>([]);
  const [loginCatalog, setLoginCatalog] = useState<{ login_id: string; display_name: string | null }[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [loginDrafts, setLoginDrafts] = useState<LoginDraftRow[]>([]);
  const [editRoleByLrrId, setEditRoleByLrrId] = useState<Record<number, number>>({});
  const [activeByLoginId, setActiveByLoginId] = useState<Record<string, "Y" | "N">>({});
  const [phoneByLoginId, setPhoneByLoginId] = useState<Record<string, string>>({});
  const [emailByLoginId, setEmailByLoginId] = useState<Record<string, string>>({});
  const [passwordByLoginId, setPasswordByLoginId] = useState<Record<string, string>>({});
  const [deletedLrrIds, setDeletedLrrIds] = useState<Set<number>>(() => new Set());
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
    setPortalInsurersError(null);
    getAdminPortalInsurers()
      .then((res) => {
        const rows = Array.isArray(res.insurers) ? res.insurers.map((x) => String(x).trim()).filter(Boolean) : [];
        setPortalInsurers(rows);
      })
      .catch((e) => {
        setPortalInsurers([]);
        setPortalInsurersError(e instanceof Error ? e.message : "Could not load portal insurers.");
      });
  }, []);

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
    setPhoneByLoginId({});
    setEmailByLoginId({});
    setPasswordByLoginId({});
    setDeletedLrrIds(new Set());
  }, [selectedId]);

  useEffect(() => {
    const activeM: Record<string, "Y" | "N"> = {};
    const phoneM: Record<string, string> = {};
    const emailM: Record<string, string> = {};
    for (const r of logins) {
      if (!(r.login_id in activeM)) {
        activeM[r.login_id] = normalizeLoginActive(r.login_active_flag);
        phoneM[r.login_id] = r.login_phone != null ? String(r.login_phone) : "";
        emailM[r.login_id] = r.login_email != null ? String(r.login_email) : "";
      }
    }
    setActiveByLoginId(activeM);
    setPhoneByLoginId(phoneM);
    setEmailByLoginId(emailM);
    setPasswordByLoginId({});
    setEditRoleByLrrId({});
    setDeletedLrrIds(new Set());
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
      setCpiReqdEdit("N");
      setInsurancePayEdit("APD");
      setDmsSiebelPortalEdit("HMCL");
      return;
    }
    const pi = detail.prefer_insurer;
    setPreferInsurerEdit(pi != null && pi !== "" ? String(pi) : "");
    setHeroCpiEdit(normalizeHeroCpi(detail.hero_cpi));
    setCpiReqdEdit(normalizeCpiReqd(detail.cpi_reqd));
    setInsurancePayEdit(normalizeInsurancePay(detail.insurance_pay));
    setDmsSiebelPortalEdit(normalizeDmsSiebelPortal(detail.dms_siebel_portal));
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
      { key: "hero_cpi", label: "Hero CPI" },
      { key: "insurance_pay", label: "Insurance Pay" },
      { key: "cpi_reqd", label: "CPA Reqd" },
      { key: "dms_siebel_portal", label: "Siebel URL type" }
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
    const savedCpiReqd = normalizeCpiReqd(detail.cpi_reqd);
    const savedInsurancePay = normalizeInsurancePay(detail.insurance_pay);
    const savedDmsSiebelPortal = normalizeDmsSiebelPortal(detail.dms_siebel_portal);
    return (
      preferInsurerEdit !== savedPi ||
      heroCpiEdit !== savedHero ||
      cpiReqdEdit !== savedCpiReqd ||
      insurancePayEdit !== savedInsurancePay ||
      dmsSiebelPortalEdit !== savedDmsSiebelPortal
    );
  }, [detail, selectedId, preferInsurerEdit, heroCpiEdit, cpiReqdEdit, insurancePayEdit, dmsSiebelPortalEdit]);

  function addLoginDraftRow() {
    setLoginDrafts((prev) => [...prev, newLoginDraft()]);
    setLoginSaveError(null);
    setLoginDirty(true);
  }

  function removeLoginDraft(tempId: string) {
    setLoginDrafts((prev) => prev.filter((r) => r.tempId !== tempId));
    setLoginSaveError(null);
    setLoginDirty(true);
  }

  function updateLoginDraft(tempId: string, patch: Partial<LoginDraftRow>) {
    setLoginDrafts((prev) => prev.map((r) => (r.tempId === tempId ? { ...r, ...patch } : r)));
    setLoginDirty(true);
  }

  function markLoginRowDeleted(loginRolesRefId: number) {
    setDeletedLrrIds((prev) => new Set(prev).add(loginRolesRefId));
    setLoginSaveError(null);
    setLoginDirty(true);
  }

  function loginExistsGlobally(loginId: string): boolean {
    const lid = loginId.trim();
    if (!lid) return false;
    return loginCatalog.some((l) => l.login_id === lid);
  }

  const visibleLogins = useMemo(
    () => logins.filter((r) => !deletedLrrIds.has(r.login_roles_ref_id)),
    [logins, deletedLrrIds]
  );

  /** First role row per login id — only that row edits shared ``login_ref`` contact/password fields. */
  const firstLrrIdByLoginId = useMemo(() => {
    const m: Record<string, number> = {};
    for (const r of visibleLogins) {
      if (!(r.login_id in m)) {
        m[r.login_id] = r.login_roles_ref_id;
      }
    }
    return m;
  }, [visibleLogins]);

  async function handleSaveLoginAssignments() {
    if (selectedId == null || !loginDirty) return;

    const pendingLoginIds = new Set<string>();
    for (const d of loginDrafts) {
      const lid = d.login_id.trim();
      if (!lid || d.role_id === "" || Number.isNaN(Number(d.role_id))) {
        setLoginSaveError("Each new row must have a login id and a role.");
        return;
      }
      if (pendingLoginIds.has(lid)) {
        setLoginSaveError(`Duplicate login id in pending rows: ${lid}`);
        return;
      }
      pendingLoginIds.add(lid);
      const isNewLogin = !loginExistsGlobally(lid);
      if (isNewLogin && !d.name.trim()) {
        setLoginSaveError(`Name is required for new login: ${lid}`);
        return;
      }
      if (isNewLogin && !d.password.trim()) {
        setLoginSaveError(`Password is required for new login: ${lid}`);
        return;
      }
    }

    setLoginSaveError(null);
    setSavingLogins(true);
    try {
      const existingRows = visibleLogins.map((r) => {
        const isPrimaryContactRow = firstLrrIdByLoginId[r.login_id] === r.login_roles_ref_id;
        const base = {
          login_roles_ref_id: r.login_roles_ref_id,
          login_id: r.login_id,
          role_id: editRoleByLrrId[r.login_roles_ref_id] ?? r.role_id,
          active_flag: activeByLoginId[r.login_id] ?? normalizeLoginActive(r.login_active_flag),
        };
        if (!isPrimaryContactRow) {
          return base;
        }
        const pwd = passwordByLoginId[r.login_id]?.trim();
        return {
          ...base,
          phone: phoneByLoginId[r.login_id] ?? (r.login_phone != null ? String(r.login_phone) : ""),
          email: emailByLoginId[r.login_id] ?? (r.login_email != null ? String(r.login_email) : ""),
          ...(pwd ? { password: pwd } : {}),
        };
      });

      const draftRows = loginDrafts.map((d) => {
        const lid = d.login_id.trim();
        const isNewLogin = !loginExistsGlobally(lid);
        const pwd = d.password.trim();
        return {
          login_roles_ref_id: null as number | null,
          login_id: lid,
          role_id: Number(d.role_id),
          active_flag: d.active_flag,
          phone: d.phone.trim() || null,
          email: d.email.trim() || null,
          ...(isNewLogin ? { name: d.name.trim(), password: pwd } : pwd ? { password: pwd } : {}),
        };
      });

      const updated = await upsertLoginAssignments(selectedId, {
        rows: [...existingRows, ...draftRows],
        delete_login_roles_ref_ids: [...deletedLrrIds],
      });
      setLogins(updated);
      setLoginDrafts([]);
      setDeletedLrrIds(new Set());
      setLoginDirty(false);
      const catalog = await getAdminLoginCatalog();
      setLoginCatalog(catalog);
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
    loginDrafts.every((d) => {
      if (d.login_id.trim() === "" || d.role_id === "" || Number.isNaN(Number(d.role_id))) return false;
      const isNewLogin = !loginExistsGlobally(d.login_id.trim());
      if (isNewLogin && (!d.name.trim() || !d.password.trim())) return false;
      return true;
    });

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
        const st = d.subdealer_type.trim();
        await createAdminDealerDiscount(selectedId, {
          subdealer_type: st || undefined,
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
        cpi_reqd: cpiReqdEdit,
        insurance_pay: insurancePayEdit,
        dms_siebel_portal: dmsSiebelPortalEdit,
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
          {portalInsurersError ? <p className="view-vehicles-error">{portalInsurersError}</p> : null}
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
                        portalInsurers.length > 0 ? (
                          <select
                            className="view-vehicles-kv-select"
                            value={
                              portalInsurers.includes(preferInsurerEdit) || preferInsurerEdit === ""
                                ? preferInsurerEdit
                                : ""
                            }
                            onChange={(e) => setPreferInsurerEdit(e.target.value)}
                            disabled={busy || savingDetail}
                            aria-label="Preferred Insurance"
                          >
                            <option value="">— None —</option>
                            {preferInsurerEdit &&
                            preferInsurerEdit !== "" &&
                            !portalInsurers.includes(preferInsurerEdit) ? (
                              <option value="" disabled>
                                {preferInsurerEdit} (not in portal list — pick a value below)
                              </option>
                            ) : null}
                            {portalInsurers.map((name) => (
                              <option key={name} value={name}>
                                {name}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            type="text"
                            className="view-vehicles-kv-input"
                            value={preferInsurerEdit}
                            onChange={(e) => setPreferInsurerEdit(e.target.value)}
                            disabled={busy || savingDetail}
                            autoComplete="off"
                            aria-label="Preferred Insurance"
                            placeholder="Portal insurers not loaded"
                          />
                        )
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
                      ) : key === "insurance_pay" ? (
                        <select
                          className="view-vehicles-kv-select"
                          value={insurancePayEdit}
                          onChange={(e) =>
                            setInsurancePayEdit(e.target.value === "CC" ? "CC" : "APD")
                          }
                          disabled={busy || savingDetail}
                          aria-label="Insurance Pay"
                        >
                          <option value="APD">APD</option>
                          <option value="CC">CC</option>
                        </select>
                      ) : key === "cpi_reqd" ? (
                        <select
                          className="view-vehicles-kv-select"
                          value={cpiReqdEdit}
                          onChange={(e) => setCpiReqdEdit(e.target.value === "Y" ? "Y" : "N")}
                          disabled={busy || savingDetail}
                          aria-label="CPA Required"
                        >
                          <option value="N">N</option>
                          <option value="Y">Y</option>
                        </select>
                      ) : key === "dms_siebel_portal" ? (
                        <select
                          className="view-vehicles-kv-select"
                          value={dmsSiebelPortalEdit}
                          onChange={(e) =>
                            setDmsSiebelPortalEdit(e.target.value === "ASC" ? "ASC" : "HMCL")
                          }
                          disabled={busy || savingDetail}
                          aria-label="Siebel URL type"
                        >
                          <option value="HMCL">HMCL</option>
                          <option value="ASC">ASC</option>
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
            logins or role assignments, then <strong>Save changes</strong>. Password shows as **** for existing logins; leave blank to
            keep the current password. Phone, email, and password apply to the login account and are shared across all role rows for
            that login id (editable on the first row only).
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
                  <th>Password</th>
                  <th>Name</th>
                  <th>Phone</th>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Active</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {visibleLogins.length === 0 && loginDrafts.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="view-vehicles-table-empty">
                      No login rows for this dealer.
                    </td>
                  </tr>
                ) : null}
                {visibleLogins.map((row) => {
                  const roleVal = editRoleByLrrId[row.login_roles_ref_id] ?? row.role_id;
                  const activeVal =
                    activeByLoginId[row.login_id] ?? normalizeLoginActive(row.login_active_flag);
                  const phoneVal =
                    phoneByLoginId[row.login_id] ?? (row.login_phone != null ? String(row.login_phone) : "");
                  const emailVal =
                    emailByLoginId[row.login_id] ?? (row.login_email != null ? String(row.login_email) : "");
                  const isPrimaryContactRow =
                    firstLrrIdByLoginId[row.login_id] === row.login_roles_ref_id;
                  return (
                    <tr key={row.login_roles_ref_id}>
                      <td>{row.login_id}</td>
                      <td>
                        {isPrimaryContactRow ? (
                          <input
                            type="password"
                            className="view-vehicles-kv-input admin-dealers-discount-cell-input"
                            value={passwordByLoginId[row.login_id] ?? ""}
                            onChange={(e) => {
                              setPasswordByLoginId((prev) => ({ ...prev, [row.login_id]: e.target.value }));
                              setLoginDirty(true);
                            }}
                            placeholder="****"
                            disabled={busy || savingLogins}
                            autoComplete="new-password"
                            aria-label={`Password for ${row.login_id}`}
                          />
                        ) : (
                          <span className="view-vehicles-table-muted" aria-label={`Password for ${row.login_id}`}>
                            ****
                          </span>
                        )}
                      </td>
                      <td>{formatCell(row.login_display_name)}</td>
                      <td>
                        {isPrimaryContactRow ? (
                          <input
                            type="text"
                            className="view-vehicles-kv-input admin-dealers-discount-cell-input"
                            value={phoneVal}
                            onChange={(e) => {
                              setPhoneByLoginId((prev) => ({ ...prev, [row.login_id]: e.target.value }));
                              setLoginDirty(true);
                            }}
                            disabled={busy || savingLogins}
                            autoComplete="off"
                            aria-label={`Phone for ${row.login_id}`}
                          />
                        ) : (
                          <span className="view-vehicles-table-muted" title="Same as first row for this login">
                            {phoneVal.trim() !== "" ? phoneVal : "—"}
                          </span>
                        )}
                      </td>
                      <td>
                        {isPrimaryContactRow ? (
                          <input
                            type="text"
                            className="view-vehicles-kv-input admin-dealers-discount-cell-input"
                            value={emailVal}
                            onChange={(e) => {
                              setEmailByLoginId((prev) => ({ ...prev, [row.login_id]: e.target.value }));
                              setLoginDirty(true);
                            }}
                            disabled={busy || savingLogins}
                            autoComplete="off"
                            aria-label={`Email for ${row.login_id}`}
                          />
                        ) : (
                          <span className="view-vehicles-table-muted" title="Same as first row for this login">
                            {emailVal.trim() !== "" ? emailVal : "—"}
                          </span>
                        )}
                      </td>
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
                      <td className="admin-dealers-actions-cell">
                        <button
                          type="button"
                          className="subdealer-challan-row-delete"
                          aria-label={`Remove assignment for ${row.login_id}`}
                          onClick={() => markLoginRowDeleted(row.login_roles_ref_id)}
                          disabled={busy || savingLogins}
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {loginDrafts.map((dr) => {
                  const isNewLogin = dr.login_id.trim() !== "" && !loginExistsGlobally(dr.login_id);
                  return (
                  <tr key={dr.tempId} className="admin-dealers-discount-draft-row">
                    <td>
                      <input
                        type="text"
                        className="view-vehicles-kv-input admin-dealers-discount-cell-input"
                        value={dr.login_id}
                        onChange={(e) => updateLoginDraft(dr.tempId, { login_id: e.target.value })}
                        disabled={savingLogins}
                        autoComplete="off"
                        aria-label="Login id"
                        placeholder="Login id"
                      />
                    </td>
                    <td>
                      <input
                        type="password"
                        className="view-vehicles-kv-input admin-dealers-discount-cell-input"
                        value={dr.password}
                        onChange={(e) => updateLoginDraft(dr.tempId, { password: e.target.value })}
                        disabled={savingLogins}
                        autoComplete="new-password"
                        aria-label="Password"
                        placeholder={isNewLogin ? "Required" : "Optional"}
                      />
                    </td>
                    <td>
                      <input
                        type="text"
                        className="view-vehicles-kv-input admin-dealers-discount-cell-input"
                        value={dr.name}
                        onChange={(e) => updateLoginDraft(dr.tempId, { name: e.target.value })}
                        disabled={savingLogins}
                        autoComplete="off"
                        aria-label="Name"
                        placeholder={isNewLogin ? "Required" : "Existing login"}
                      />
                    </td>
                    <td>
                      <input
                        type="text"
                        className="view-vehicles-kv-input admin-dealers-discount-cell-input"
                        value={dr.phone}
                        onChange={(e) => updateLoginDraft(dr.tempId, { phone: e.target.value })}
                        disabled={savingLogins}
                        autoComplete="off"
                        aria-label="Phone"
                      />
                    </td>
                    <td>
                      <input
                        type="text"
                        className="view-vehicles-kv-input admin-dealers-discount-cell-input"
                        value={dr.email}
                        onChange={(e) => updateLoginDraft(dr.tempId, { email: e.target.value })}
                        disabled={savingLogins}
                        autoComplete="off"
                        aria-label="Email"
                      />
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
                    <td className="admin-dealers-actions-cell">
                      <button
                        type="button"
                        className="subdealer-challan-row-delete"
                        aria-label="Remove draft row"
                        onClick={() => removeLoginDraft(dr.tempId)}
                        disabled={savingLogins}
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        <section className="view-vehicles-section" aria-label="Subdealer discounts for dealer">
          <p className="view-vehicles-hint">
            Per-dealer subdealer discounts (<code>subdealer_discount_master_ref</code>). Subdealer type can match model if
            you only need one line per model. Use <strong>Add</strong> for new rows, then{" "}
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
                  <th>Subdealer type</th>
                  <th>Model</th>
                  <th>Discount</th>
                  <th>Create date</th>
                  <th>Valid</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {discounts.map((row) => (
                  <tr
                    key={`${row.dealer_id}-${row.subdealer_type}-${row.valid_flag}-${row.model}`}
                  >
                    <td>{row.subdealer_type}</td>
                    <td>{row.model}</td>
                    <td>{row.discount != null ? String(row.discount) : "—"}</td>
                    <td>{formatCell(row.create_date)}</td>
                    <td>{row.valid_flag}</td>
                    <td />
                  </tr>
                ))}
                {discountDrafts.map((dr) => (
                  <tr key={dr.tempId} className="admin-dealers-discount-draft-row">
                    <td>
                      <input
                        type="text"
                        className="view-vehicles-kv-input admin-dealers-discount-cell-input"
                        value={dr.subdealer_type}
                        onChange={(e) =>
                          updateDiscountDraft(dr.tempId, { subdealer_type: e.target.value })
                        }
                        disabled={savingDiscounts}
                        placeholder="Defaults to model"
                        maxLength={64}
                        aria-label="Subdealer type"
                      />
                    </td>
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
