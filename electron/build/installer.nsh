; Default install root: D:\Saathi when D: is present; otherwise C:\Saathi.
; electron-builder reads InstallLocation from the registry AFTER customInit,
; overwriting $INSTDIR. preInit runs before that registry read, so we write
; the desired path into the registry first (only when no previous install exists).
; Also creates scanner workspace folders under the install directory.

!macro preInit
  ; Check if there is already a saved install location (upgrade scenario).
  SetRegView 64
  ReadRegStr $0 HKCU "${INSTALL_REGISTRY_KEY}" "InstallLocation"
  StrCmp $0 "" 0 _saathi_skip

  ; First install: pick D:\Saathi or C:\Saathi.
  IfFileExists "D:\*.*" 0 _saathi_useC
    StrCpy $0 "D:\Saathi"
    Goto _saathi_write
  _saathi_useC:
    StrCpy $0 "C:\Saathi"

  _saathi_write:
  WriteRegExpandStr HKLM "${INSTALL_REGISTRY_KEY}" "InstallLocation" "$0"
  WriteRegExpandStr HKCU "${INSTALL_REGISTRY_KEY}" "InstallLocation" "$0"
  SetRegView 32
  WriteRegExpandStr HKLM "${INSTALL_REGISTRY_KEY}" "InstallLocation" "$0"
  WriteRegExpandStr HKCU "${INSTALL_REGISTRY_KEY}" "InstallLocation" "$0"

  _saathi_skip:
!macroend

!macro customInstall
  CreateDirectory "$INSTDIR\scanner\landing"
  CreateDirectory "$INSTDIR\scanner\processed"
  CreateDirectory "$INSTDIR\logs"
  ; Seed .env with minimal Playwright config (skip if file already exists)
  IfFileExists "$INSTDIR\.env" +4 0
    FileOpen $0 "$INSTDIR\.env" w
    FileWrite $0 "DMS_MODE=real$\r$\nDMS_PLAYWRIGHT_HEADED=1$\r$\n"
    FileClose $0
!macroend
