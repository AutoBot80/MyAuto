; Seeds dealer PC defaults under the Saathi data directory (see electron/src/main/paths.ts).
; Only writes .env if the file does not exist yet — upgrades keep a custom .env.
; Scanner folders + .env are created under $INSTDIR (the directory the user chose in the installer).

!macro customInstall
    CreateDirectory "$INSTDIR"
    CreateDirectory "$INSTDIR\scanner\landing"
    CreateDirectory "$INSTDIR\scanner\processed"

    IfFileExists "$INSTDIR\.env" saathi_env_done

    FileOpen $9 "$INSTDIR\.env" w
    FileWrite $9 "# Dealer Saathi — created by installer (local Playwright / IPC fallback).$\r$\n"
    FileWrite $9 "# API-first site URLs use the cloud; edit here only if needed.$\r$\n"
    FileWrite $9 "DMS_MODE=real$\r$\n"
    FileWrite $9 "DMS_BASE_URL=https://connect.heromotocorp.biz/edealerHMCL_enu?SWECmd=Start$\r$\n"
    FileWrite $9 "INSURANCE_BASE_URL=https://heroinsurance.com/misp-partner-login$\r$\n"
    FileWrite $9 "VAHAN_BASE_URL=https://vahan.parivahan.gov.in/vahan/vahan/ui/login/login.xhtml$\r$\n"
    FileClose $9

  saathi_env_done:
!macroend
