# Checkpoint: Enabled Add Vehicle Looping

This milestone is registered in **`Documentation/checkpoints.md`** as **Serial No. 14**.

- **Tag:** `checkpoint/enabled-add-vehicle-looping`
- **Commit:** `3df79152f68720501aec83643572f9ee7778763a`
- **Created (IST):** `2026-04-07T20:16:46+05:30`

## Scope (this snapshot)

Multi-line order attach in **`hero_dms_playwright_invoice`**: **`_attach_vehicle_to_bkg`** loops **New** → row **VIN** → optional **Discount** per **`order_line_vehicles`** / **`attach_vehicles`**, then **Price All** / **Allocate All** once; post-allocate **`order_line_ex_showroom`** scrape; BRD/HLD/LLD updates (**LLD** **6.278**, **BRD** **3.158**, **HLD** **1.159**).

## TODOs (mirrored on tag / registry)

1. Create subdealer challan wrapper  
2. Test single sale  
3. Test Challan creation  
