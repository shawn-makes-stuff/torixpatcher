@echo off
rem Builds TrustAuraHal_x86.dll and TrustAuraHal_x64.dll into the folder above this one (needs the MSVC build tools).
setlocal
set VS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools
cd /d %~dp0
for %%A in (x86 x64) do (
  call "%VS%\VC\Auxiliary\Build\vcvarsall.bat" %%A >nul || exit /b 1
  cl /nologo /O2 /EHsc /MT /std:c++17 /LD TrustAuraHal.cpp /link /DEF:TrustAuraHal.def /OUT:..\TrustAuraHal_%%A.dll oleaut32.lib || exit /b 1
)
del *.obj *.exp *.lib 2>nul
