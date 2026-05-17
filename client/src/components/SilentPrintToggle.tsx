import { useCallback, useState } from "react";
import { isElectron } from "../electron";
import { getSilentPrintEnabled, setSilentPrintEnabled } from "../settings/printPreferences";

/** Home page: silent PDF print (no system dialog). Browser-only builds hide this control. */
export function SilentPrintToggle() {
  const [on, setOn] = useState(() => getSilentPrintEnabled());

  const toggle = useCallback(() => {
    setOn((prev) => {
      const next = !prev;
      setSilentPrintEnabled(next);
      return next;
    });
  }, []);

  if (!isElectron()) return null;

  return (
    <label className="home-silent-print-toggle" title="When on, Sale Certificate / Insurance / Gate Pass print without a dialog">
      <span className="home-silent-print-toggle__label">Silent print</span>
      <input
        type="checkbox"
        checked={on}
        onChange={toggle}
        aria-label="Silent print"
      />
    </label>
  );
}
