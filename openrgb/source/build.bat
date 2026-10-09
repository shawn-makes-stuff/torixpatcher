@echo off
rem Builds TorixOpenRGBPlugin.dll for OpenRGB 1.0 (Qt 6.8.3, plugin API 5). Needs: Qt 6.8.3 msvc2022_64 (QT), the OpenRGB 1.0 source (ORGB) for its plugin headers, VS Build Tools.
setlocal
if not defined QT echo Set QT to your Qt 6.8.3 msvc2022_64 folder, ORGB to the OpenRGB 1.0 source folder and VS to the VS Build Tools folder, then run again. & exit /b 1
cd /d %~dp0
call "%VS%\VC\Auxiliary\Build\vcvarsall.bat" x64 >nul || exit /b 1
set INC=/I"%ORGB%" /I"%ORGB%\RGBController" /I"%ORGB%\dependencies\json" /I"%QT%\include" /I"%QT%\include\QtCore" /I"%QT%\include\QtGui" /I"%QT%\include\QtWidgets"
"%QT%\bin\moc.exe" TorixPlugin.h -o moc_TorixPlugin.cpp %INC:/I=-I% || exit /b 1
cl /nologo /O2 /EHsc /MD /std:c++17 /permissive- /utf-8 /Zc:__cplusplus /DQT_NO_DEBUG /DNDEBUG /DWIN32_LEAN_AND_MEAN /DNOMINMAX %INC% /LD TorixPlugin.cpp moc_TorixPlugin.cpp /link /LIBPATH:"%QT%\lib" Qt6Core.lib Qt6Gui.lib Qt6Widgets.lib /OUT:TorixOpenRGBPlugin.dll || exit /b 1
del *.obj *.exp *.lib moc_TorixPlugin.cpp 2>nul
