import { useState } from "react";
import { resetAllData } from "../api/admin";
import "./AdminPage.css";

export function AdminPage() {
  const [isDeleting, setIsDeleting] = useState(false);

  async function handleDeleteAllData() {
    const confirmed = window.confirm(
      "This will delete all database data except oem_ref, dealer_ref, oem_service_schedule, and subdealer_discount_master. Do you want to continue?"
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

  return (
    <div className="admin-page">
      <button
        type="button"
        className="app-button admin-danger-button admin-danger-button--top-right"
        onClick={handleDeleteAllData}
        disabled={isDeleting}
      >
        {isDeleting ? "Deleting..." : "Delete All Data"}
      </button>
    </div>
  );
}

