; Seeds data under the Saathi root (e.g. D:\Saathi) when the app is installed
; in ...\Saathi\Dealer Saathi, so app updates that replace the inner folder
; do not clobber scanner folders or .env. See getSaathiBaseDir in
; electron/src/main/paths.ts.
; preInit: default D:\Saathi (or C:\Saathi) when no prior InstallLocation.
; customInit: re-applies that path to $INSTDIR so the "Choose install location" page
; shows it (initMultiUser can still leave the default as %LocalAppData%\Programs\<app>).
; Duplicated in electron/build/installer.nsh for local reference; electron-builder uses this file (buildResources).
!include "StdUtils.nsh"

!macro preInit
  SetRegView 64
  ReadRegStr $0 HKCU "${INSTALL_REGISTRY_KEY}" "InstallLocation"
  StrCmp $0 "" 0 _s_skip
  ReadRegStr $0 HKLM "${INSTALL_REGISTRY_KEY}" "InstallLocation"
  StrCmp $0 "" 0 _s_skip
  SetRegView 32
  ReadRegStr $0 HKCU "${INSTALL_REGISTRY_KEY}" "InstallLocation"
  StrCmp $0 "" 0 _s_skip_32
  ReadRegStr $0 HKLM "${INSTALL_REGISTRY_KEY}" "InstallLocation"
  StrCmp $0 "" 0 _s_skip_32
  SetRegView 64

  IfFileExists "D:\*.*" 0 _s_c
    StrCpy $0 "D:\Saathi"
    Goto _s_w
  _s_c:
    StrCpy $0 "C:\Saathi"
  _s_w:
  WriteRegExpandStr HKLM "${INSTALL_REGISTRY_KEY}" "InstallLocation" "$0"
  WriteRegExpandStr HKCU "${INSTALL_REGISTRY_KEY}" "InstallLocation" "$0"
  SetRegView 32
  WriteRegExpandStr HKLM "${INSTALL_REGISTRY_KEY}" "InstallLocation" "$0"
  WriteRegExpandStr HKCU "${INSTALL_REGISTRY_KEY}" "InstallLocation" "$0"
  Goto _s_skip
  _s_skip_32:
  SetRegView 64
  _s_skip:
!macroend

!macro customInit
  ${StdUtils.GetParameter} $0 "D" ""
  StrCmp $0 "" 0 sdr_ci_end

  SetRegView 64
  StrCmp $installMode "all" sdr_ci_64_m sdr_ci_64_u
sdr_ci_64_m:
  ReadRegStr $0 HKLM "${INSTALL_REGISTRY_KEY}" "InstallLocation"
  Goto sdr_ci_64_x
sdr_ci_64_u:
  ReadRegStr $0 HKCU "${INSTALL_REGISTRY_KEY}" "InstallLocation"
sdr_ci_64_x:
  SetRegView 32
  StrCmp $0 "" 0 sdr_ci_apply
  StrCmp $installMode "all" sdr_ci_32_m sdr_ci_32_u
sdr_ci_32_m:
  ReadRegStr $0 HKLM "${INSTALL_REGISTRY_KEY}" "InstallLocation"
  Goto sdr_ci_32_x
sdr_ci_32_u:
  ReadRegStr $0 HKCU "${INSTALL_REGISTRY_KEY}" "InstallLocation"
sdr_ci_32_x:
  StrCmp $0 "" 0 sdr_ci_apply
  IfFileExists "D:\*.*" 0 sdr_ci_c
  StrCpy $INSTDIR "D:\Saathi"
  Goto sdr_ci_end
sdr_ci_c:
  StrCpy $INSTDIR "C:\Saathi"
  Goto sdr_ci_end
sdr_ci_apply:
  StrCpy $INSTDIR $0
sdr_ci_end:
!macroend

; $R0 = string in, $R1 = last backslash index, $4/$3 = scratch, $0/$2 = scratch
; Returns last path segment in $2 (or empty if $R1 < 0);
; string length in $0 for basename; offset of basename in $4
Function _SaathiPathLastSeg
  StrCpy $R1 -1
  StrCpy $3 0
sdr_pl:
  StrCpy $0 $R0 1 $3
  StrCmp $0 "" sdr_pld
  StrCmp $0 "\" 0 sdr_pln
  StrCpy $R1 $3
sdr_pln:
  IntOp $3 $3 + 1
  Goto sdr_pl
sdr_pld:
  IntCmp $R1 -1 sdr_plb 0 0
  IntOp $4 $R1 + 1
  StrLen $0 $R0
  IntOp $0 $0 - $4
  IntCmp $0 0 sdr_plb 0 sdr_plex
sdr_plex:
  StrCpy $2 $R0 $0 $4
  Return
sdr_plb:
  StrCpy $2 ""
  Return
FunctionEnd

; $R5 := stable data root: parent of "Dealer Saathi" only when that parent ends with "Saathi"
Function _SaathiDataRoot
  StrCpy $R5 $INSTDIR
  StrCpy $R0 $INSTDIR
  Call _SaathiPathLastSeg
  StrLen $0 $2
  IntCmp $0 0 sdr_exit sdr_exit sdr_1a
sdr_1a:
  StrCmp $2 "Dealer Saathi" 0 sdr_exit
  IntCmp $R1 0 sdr_exit sdr_exit sdr_do1
sdr_do1:
  StrCpy $R6 $R0 $R1 0
  StrCpy $R0 $R6
  Call _SaathiPathLastSeg
  StrLen $0 $2
  IntCmp $0 0 sdr_exit sdr_exit sdr_1b
sdr_1b:
  StrCmp $2 "Saathi" 0 sdr_exit
  StrCpy $R5 $R6
sdr_exit:
  Return
FunctionEnd

!macro customInstall
  Call _SaathiDataRoot
  CreateDirectory "$R5\scanner\landing"
  CreateDirectory "$R5\scanner\processed"
  CreateDirectory "$R5\logs"
  IfFileExists "$R5\.env" sdr_envok
  FileOpen $9 "$R5\.env" w
  FileWrite $9 "# Dealer Saathi — created by installer (local Playwright / IPC fallback).$\r$\n"
  FileWrite $9 "# API-first site URLs use the cloud; edit here only if needed.$\r$\n"
  FileWrite $9 "DMS_MODE=real$\r$\n"
  FileWrite $9 "DMS_BASE_URL=https://connect.heromotocorp.biz/edealerHMCL_enu?SWECmd=Start$\r$\n"
  FileWrite $9 "INSURANCE_BASE_URL=https://heroinsurance.com/misp-partner-login$\r$\n"
  FileWrite $9 "VAHAN_BASE_URL=https://vahan.parivahan.gov.in/vahan/vahan/ui/login/login.xhtml$\r$\n"
  FileClose $9
sdr_envok:
!macroend
