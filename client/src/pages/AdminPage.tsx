import { useState } from "react";
import { resetAllData } from "../api/admin";
import { AdminStagingToolsPanel } from "./AdminStagingToolsPanel";
import "./AdminPage.css";

interface AdminPageProps {
  /** Disabled when backend ``ENVIRONMENT`` is prod/production, or while settings are loading. */
  deleteAllDataDisabled: boolean;
}

export function AdminPage({ deleteAllDataDisabled }: AdminPageProps) {
  const [isDeleting, setIsDeleting] = useState(false);

  async function handleDeleteAllData() {
    const confirmed = window.confirm(
      "This will delete all database data except tables whose names end in \"ref\", plus oem_service_schedule and subdealer_discount_master. Do you want to continue?"
    );
    if (!confirmed) return;

    setIsDeleting(true);
    try {
      const res = await resetAllData();
      window.alert(res.message);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to delete data.";
      window.alert(message);
    } finally {
      setIsDeleting(false);
    }
  }

  const buttonDisabled = isDeleting || deleteAllDataDisabled;

  return (
    <div className="admin-page">
      <button
        type="button"
        className="app-button admin-danger-button admin-danger-button--top-right"
        onClick={handleDeleteAllData}
        disabled={buttonDisabled}
        title={
          deleteAllDataDisabled
            ? "Disabled in production (ENVIRONMENT=prod or production in backend/.env)."
            : undefined
        }
      >
        {isDeleting ? "Deleting..." : "Delete All Data"}
      </button>
      {deleteAllDataDisabled ? (
        <p className="admin-page-prod-hint" role="status">
          Delete All Data is disabled when ENVIRONMENT is prod or production.
        </p>
      ) : null}

      <AdminStagingToolsPanel />
    </div>
  );
}
