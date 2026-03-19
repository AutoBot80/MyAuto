(() => {
  const STORAGE_KEY = "vaahan_applications";

  function getApplications() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (error) {
      return {};
    }
  }

  function saveApplications(apps) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(apps));
  }

  function saveApplication(app) {
    const apps = getApplications();
    apps[app.application_id] = app;
    saveApplications(apps);
  }

  function getApplication(applicationId) {
    if (!applicationId) return null;
    const apps = getApplications();
    return apps[applicationId] || null;
  }

  function generateApplicationId() {
    return String(Math.floor(10000000 + Math.random() * 90000000));
  }

  function generateTcNumber() {
    return "TC" + Date.now() + "-" + Math.random().toString(36).slice(2, 8).toUpperCase();
  }

  function computeRtoFees(vehiclePrice) {
    const total = Number(vehiclePrice || 0);
    return Math.round(total * 0.01 + 200);
  }

  function getQueryParam(name) {
    const params = new URLSearchParams(window.location.search);
    return params.get(name);
  }

  function currency(value) {
    const num = Number(value || 0);
    return "₹" + num.toLocaleString("en-IN");
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function defaultApplication(partial) {
    const vehiclePrice = Number(partial.vehicle_price || partial.total_cost || 72000);
    const applicationId = partial.application_id || generateApplicationId();
    const regPart1 = partial.reg_part1 || "RJ14A";
    const regPart2 = partial.reg_part2 || applicationId.slice(-5).padStart(5, "0");
    return {
      application_id: applicationId,
      rto_dealer_id: partial.rto_dealer_id || "RTO100001",
      registration_type: partial.registration_type || "New Registration",
      chassis_no: partial.chassis_no || "",
      engine_last5: partial.engine_last5 || "",
      customer_name: partial.customer_name || "Customer",
      purchase_delivery_date: partial.purchase_delivery_date || "18-MAR-2026",
      owner_name: partial.owner_name || partial.customer_name || "Customer",
      owner_type: partial.owner_type || "Individual",
      relation_name: partial.relation_name || "Rajesh Kumar",
      ownership_serial: partial.ownership_serial || "1",
      choice_number_type: partial.choice_number_type || "SELECT",
      aadhaar_mode: partial.aadhaar_mode || "Aadhaar OTP",
      category: partial.category || "General",
      mobile_no: partial.mobile_no || "9876543210",
      pan_card: partial.pan_card || "ABCDE1234F",
      voter_id: partial.voter_id || "RJ/123/456/789",
      aadhaar_no: partial.aadhaar_no || "1234 5678 9012",
      permanent_address: partial.permanent_address || "Permanent Address",
      house_street: partial.house_street || "45, Sector 14",
      city: partial.city || "VILL RAGLA MUKARIV",
      vehicle_model: partial.vehicle_model || "SPLENDOR PLUS",
      vehicle_colour: partial.vehicle_colour || "Black",
      fuel_type: partial.fuel_type || "Petrol",
      year_of_mfg: partial.year_of_mfg || "2026",
      vehicle_price: vehiclePrice,
      rto_fees: Number(partial.rto_fees || computeRtoFees(vehiclePrice)),
      insurance_type: partial.insurance_type || "Comprehensive",
      insurer_name: partial.insurer_name || "UNIVERSAL SOMPO GENERAL INSURANCE",
      policy_no: partial.policy_no || "3005XXXXXX",
      insurance_from: partial.insurance_from || "18-MAR-2026",
      insurance_upto: partial.insurance_upto || "17-MAR-2031",
      insured_declared_value: partial.insured_declared_value || "72000",
      series_type: partial.series_type || "State Series",
      bank_name: partial.bank_name || "HDFC BANK LTD",
      assigned_office: partial.assigned_office || "Assigned Office & Action",
      reg_part1: regPart1,
      reg_part2: regPart2,
      registration_no: partial.registration_no || `${regPart1}${regPart2}`,
      status: partial.status || "Pending Applications",
      files_uploaded: Boolean(partial.files_uploaded),
      files_uploaded_at: partial.files_uploaded_at || "",
      pay_txn_id: partial.pay_txn_id || "",
      created_at: partial.created_at || new Date().toISOString(),
    };
  }

  function createSyntheticAppFromParams() {
    const applicationId = getQueryParam("application_id");
    if (!applicationId || getApplication(applicationId)) {
      return null;
    }
    const vehiclePrice = Number(getQueryParam("vehicle_price") || getQueryParam("total_cost") || 72000);
    return defaultApplication({
      application_id: applicationId,
      rto_dealer_id: getQueryParam("rto_dealer_id") || "RTO100001",
      customer_name: getQueryParam("customer_name") || "Customer",
      owner_name: getQueryParam("customer_name") || "Customer",
      chassis_no: getQueryParam("chassis_no") || "",
      mobile_no: getQueryParam("mobile_no") || "9876543210",
      vehicle_model: getQueryParam("vehicle_model") || "SPLENDOR PLUS",
      vehicle_colour: getQueryParam("vehicle_colour") || "Black",
      fuel_type: getQueryParam("fuel_type") || "Petrol",
      year_of_mfg: getQueryParam("year_of_mfg") || "2026",
      vehicle_price: vehiclePrice,
      rto_fees: Number(getQueryParam("rto_fees") || computeRtoFees(vehiclePrice)),
      status: getQueryParam("paid") === "1" ? "Paid" : "Pending Applications",
      files_uploaded: getQueryParam("uploaded") === "1",
      files_uploaded_at: getQueryParam("uploaded") === "1" ? new Date().toISOString() : "",
    });
  }

  function ensureQueryBackedApplication() {
    const synthetic = createSyntheticAppFromParams();
    if (synthetic) {
      saveApplication(synthetic);
      return synthetic;
    }
    return getApplication(getQueryParam("application_id"));
  }

  window.VahanDummy = {
    STORAGE_KEY,
    getApplications,
    saveApplication,
    getApplication,
    generateApplicationId,
    generateTcNumber,
    computeRtoFees,
    getQueryParam,
    currency,
    escapeHtml,
    defaultApplication,
    ensureQueryBackedApplication,
  };
})();
