; Default install root: D:\Saathi when D: is present; otherwise C:\Saathi.
; Also creates scanner workspace folders under the install directory.

!macro customInit
  IfFileExists "D:\*.*" 0 +3
    StrCpy $INSTDIR "D:\Saathi"
    Goto +2
  StrCpy $INSTDIR "C:\Saathi"
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
